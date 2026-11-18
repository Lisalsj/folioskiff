from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from paper_reader import __version__ as APPLICATION_VERSION
from paper_reader.migrations.versions import FROZEN_DATABASE_HEAD
from paper_reader.openapi import OPENAPI_CONTRACT_SHA256
from scripts.build_release_metadata import (
    CAPABILITY_MATRIX,
    ReleaseMetadataError,
    build_npm_sbom,
    build_python_sbom,
    build_release_metadata,
    normalize_cyclonedx_sbom,
    validate_cyclonedx_sbom,
    verify_release_directory,
)
from scripts.package_release import (
    ReleasePackageError,
    run_signing_hook,
    sign_command_from_environment,
)

ROOT = Path(__file__).parents[2]


def _canonical_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def _isolated_python_distributions() -> set[tuple[str, str]]:
    command = (
        "import importlib.metadata as metadata, json; "
        "print(json.dumps(sorted((item.metadata['Name'], item.version) "
        "for item in metadata.distributions() if item.metadata['Name'])))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", command],
        check=True,
        capture_output=True,
        encoding="utf-8",
        text=True,
        timeout=30,
    )
    payload = json.loads(result.stdout)
    return {(_canonical_distribution_name(name), version) for name, version in payload}


def _component(ecosystem: str, *, version: str = "0.8.0") -> tuple[str, str]:
    if ecosystem == "python":
        return (
            f"pkg:pypi/paper-reader@{version}",
            "pkg:pypi/example-dependency@1.2.3",
        )
    return (
        f"pkg:npm/paper-reader@{version}",
        "pkg:npm/example-dependency@1.2.3",
    )


def _raw_sbom(ecosystem: str, *, version: str = "0.8.0") -> dict[str, Any]:
    root, dependency = _component(ecosystem, version=version)
    old_root = f"paper-reader@{version}"
    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.5.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:7d9c7b58-4b27-4a87-8183-3f58dbbd6597",
        "version": 1,
        "metadata": {
            "timestamp": "2026-07-15T00:00:00Z",
            "tools": [{"vendor": "fixture", "name": "fixture", "version": "1"}],
            "component": {
                "bom-ref": old_root,
                "type": "library",
                "name": ".w" if ecosystem == "npm" else "paper-reader",
                "version": version,
                "purl": root,
            },
        },
        "components": [
            {
                "bom-ref": dependency,
                "type": "library",
                "name": "example-dependency",
                "version": "1.2.3",
                "purl": dependency,
            }
        ],
        "dependencies": [
            {"ref": dependency, "dependsOn": []},
            {"ref": old_root, "dependsOn": [dependency]},
        ],
    }


@pytest.mark.parametrize("ecosystem", ["python", "npm"])
def test_sbom_normalization_is_reproducible_rooted_and_license_explicit(
    ecosystem: str,
) -> None:
    source = _raw_sbom(ecosystem)

    first = normalize_cyclonedx_sbom(source, ecosystem=ecosystem, version="0.8.0")
    second = normalize_cyclonedx_sbom(copy.deepcopy(source), ecosystem=ecosystem, version="0.8.0")

    assert first == second
    assert first.endswith(b"\n")
    assert b"2026-07-15" not in first
    assert b"urn:uuid" not in first
    parsed = json.loads(first)
    root, dependency = _component(ecosystem)
    assert parsed["$schema"] == "https://cyclonedx.org/schema/bom-1.6.schema.json"
    assert parsed["specVersion"] == "1.6"
    assert parsed["metadata"]["component"] == {
        "bom-ref": root,
        "licenses": [{"license": {"id": "Apache-2.0"}}],
        "name": "paper-reader",
        "purl": root,
        "type": "application",
        "version": "0.8.0",
    }
    assert parsed["components"][0]["licenses"] == [{"license": {"name": "NOASSERTION"}}]
    assert parsed["dependencies"] == [
        {"dependsOn": [dependency], "ref": root},
        {"dependsOn": [], "ref": dependency},
    ]
    assert validate_cyclonedx_sbom(first, ecosystem=ecosystem, version="0.8.0") == parsed


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        (lambda value: value["metadata"]["component"].update(version="9.9.9"), "sbom_root"),
        (lambda value: value["metadata"]["component"].pop("licenses"), "sbom_license"),
        (
            lambda value: value["dependencies"][0]["dependsOn"].append("missing-ref"),
            "sbom_dependency_graph",
        ),
        (lambda value: value["components"][0].pop("licenses"), "sbom_license"),
    ],
)
def test_sbom_validator_rejects_identity_license_and_graph_drift(
    mutation: Any,
    error_code: str,
) -> None:
    normalized = json.loads(
        normalize_cyclonedx_sbom(_raw_sbom("python"), ecosystem="python", version="0.8.0")
    )
    mutation(normalized)
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ReleaseMetadataError, match=error_code):
        validate_cyclonedx_sbom(payload, ecosystem="python", version="0.8.0")


def test_python_sbom_rejects_a_different_installed_paper_reader_version() -> None:
    source = _raw_sbom("python")
    source["components"].append(
        {
            "bom-ref": "paper-reader==0.7.9",
            "type": "library",
            "name": "paper-reader",
            "version": "0.7.9",
            "purl": "pkg:pypi/paper-reader@0.7.9",
        }
    )
    source["dependencies"].append({"ref": "paper-reader==0.7.9", "dependsOn": []})

    with pytest.raises(ReleaseMetadataError, match="sbom_environment_version"):
        normalize_cyclonedx_sbom(source, ecosystem="python", version="0.8.0")


@pytest.mark.skipif(
    importlib.util.find_spec("cyclonedx") is None,
    reason="install the locked packaging extra for official CycloneDX schema validation",
)
@pytest.mark.resource_serial
def test_real_python_and_npm_sboms_use_the_actual_locked_environments(tmp_path: Path) -> None:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    assert npm is not None
    version = APPLICATION_VERSION
    python_first = tmp_path / "python-first.cdx.json"
    python_second = tmp_path / "python-second.cdx.json"
    npm_first = tmp_path / "npm-first.cdx.json"
    npm_second = tmp_path / "npm-second.cdx.json"

    for destination in (python_first, python_second):
        build_python_sbom(
            python_executable=Path(sys.executable),
            pyproject=Path("pyproject.toml").absolute(),
            version=version,
            destination=destination,
        )
    for destination in (npm_first, npm_second):
        build_npm_sbom(
            npm_executable=Path(npm).absolute(),
            project_root=Path.cwd(),
            package_lock=Path("package-lock.json").absolute(),
            version=version,
            destination=destination,
        )

    assert python_first.read_bytes() == python_second.read_bytes()
    assert npm_first.read_bytes() == npm_second.read_bytes()
    python_payload = validate_cyclonedx_sbom(
        python_first.read_bytes(), ecosystem="python", version=version
    )
    npm_payload = validate_cyclonedx_sbom(npm_first.read_bytes(), ecosystem="npm", version=version)
    python_components = {
        (_canonical_distribution_name(component["name"]), component["version"])
        for component in python_payload["components"]
    }
    python_root = python_payload["metadata"]["component"]
    python_components.add(
        (_canonical_distribution_name(python_root["name"]), python_root["version"])
    )
    assert python_components == _isolated_python_distributions()

    package = json.loads(Path("package.json").read_text(encoding="utf-8"))
    package_lock = json.loads(Path("package-lock.json").read_text(encoding="utf-8"))
    locked_components = {
        (path.rsplit("node_modules/", maxsplit=1)[-1], details["version"])
        for path, details in package_lock["packages"].items()
        if path and details.get("version")
    }
    npm_components = {
        (component["name"], component["version"]) for component in npm_payload["components"]
    }
    direct_names = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))
    locked_direct = {
        (name, package_lock["packages"][f"node_modules/{name}"]["version"]) for name in direct_names
    }
    assert npm_components <= locked_components
    assert locked_direct <= npm_components
    assert len(python_payload["dependencies"]) == len(python_payload["components"]) + 1
    assert len(npm_payload["dependencies"]) == len(npm_payload["components"]) + 1


def _write_sbom(path: Path, ecosystem: str, *, version: str = "0.8.0") -> None:
    path.write_bytes(
        normalize_cyclonedx_sbom(
            _raw_sbom(ecosystem, version=version),
            ecosystem=ecosystem,
            version=version,
        )
    )


def _release_fixture(root: Path, *, version: str = "0.8.0") -> dict[str, str]:
    root.mkdir(parents=True)
    roles = {
        f"paper-reader-{version}-windows-x64-setup.exe": "installer",
        f"paper-reader-{version}-windows-x64-portable.zip": "portable",
        f"paper-reader-{version}-python.cdx.json": "python_sbom",
        f"paper-reader-{version}-npm.cdx.json": "npm_sbom",
    }
    (root / f"paper-reader-{version}-windows-x64-setup.exe").write_bytes(b"MZfixture-setup")
    (root / f"paper-reader-{version}-windows-x64-portable.zip").write_bytes(b"PKfixture-zip")
    _write_sbom(root / f"paper-reader-{version}-python.cdx.json", "python", version=version)
    _write_sbom(root / f"paper-reader-{version}-npm.cdx.json", "npm", version=version)
    return roles


def _build_fixture_release(root: Path, tmp_path: Path) -> Any:
    roles = _release_fixture(root)
    changelog = tmp_path / f"{root.name}-CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 0.8.0\n\nAuditable preview.\n", encoding="utf-8")
    openapi = json.loads((ROOT / "openapi/openapi.json").read_text(encoding="utf-8"))
    openapi["info"]["version"] = "0.8.0"
    openapi_path = tmp_path / f"{root.name}-openapi.json"
    openapi_path.write_text(json.dumps(openapi), encoding="utf-8")
    return build_release_metadata(
        release_dir=root,
        version="0.8.0",
        git_commit="a" * 40,
        database_revision=FROZEN_DATABASE_HEAD,
        artifact_roles=roles,
        changelog_path=changelog,
        changelog_summary="Windows packaged preview with auditable metadata.",
        migration_notes="Back up before upgrade; SQLite migrations run on first start.",
        signed=False,
        openapi_path=openapi_path,
    )


def test_release_manifest_hashes_and_update_notes_share_one_canonical_fact_set(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_report = _build_fixture_release(first, tmp_path)
    second_report = _build_fixture_release(second, tmp_path)

    assert first_report == second_report
    assert first_report.signed is False
    first_files = {item.name: item.read_bytes() for item in first.iterdir() if item.is_file()}
    second_files = {item.name: item.read_bytes() for item in second.iterdir() if item.is_file()}
    assert first_files == second_files
    assert set(first_files) == {
        "CAPABILITY_MATRIX.md",
        "CHANGELOG.md",
        "RELEASE_NOTES.md",
        "SHA256SUMS.txt",
        "UNSIGNED.md",
        "paper-reader-0.8.0-npm.cdx.json",
        "paper-reader-0.8.0-python.cdx.json",
        "paper-reader-0.8.0-windows-x64-portable.zip",
        "paper-reader-0.8.0-windows-x64-setup.exe",
        "release-manifest.json",
    }
    manifest = json.loads(first_files["release-manifest.json"])
    assert manifest["schema_version"] == "paper-reader-release-manifest-v1"
    assert manifest["version"] == "0.8.0"
    assert manifest["git_commit"] == "a" * 40
    assert manifest["api_version"] == "v1"
    assert manifest["openapi_version"] == "0.8.0"
    assert manifest["openapi_sha256"] == OPENAPI_CONTRACT_SHA256
    assert manifest["plugin_protocol_version"] == "1.0"
    assert manifest["database_schema_revision"] == FROZEN_DATABASE_HEAD
    assert manifest["bundle_schema_version"] == "paper-reader-bundle-manifest-v1"
    assert manifest["platform"] == {
        "architecture": "x86_64",
        "minimum_windows_build": 22000,
        "operating_system": "windows",
    }
    assert manifest["signed"] is False
    assert manifest["capability_matrix"] == CAPABILITY_MATRIX
    assert {asset["role"] for asset in manifest["assets"]} == {
        "capability_matrix",
        "changelog",
        "installer",
        "npm_sbom",
        "portable",
        "python_sbom",
        "release_notes",
        "unsigned_notice",
    }
    for asset in manifest["assets"]:
        content = first_files[asset["name"]]
        assert asset["size_bytes"] == len(content)
        assert asset["sha256"] == hashlib.sha256(content).hexdigest()

    release_notes = first_files["RELEASE_NOTES.md"].decode("utf-8")
    marker = "<!-- paper-reader-update-manifest:v1\n"
    summary = json.loads(release_notes.split(marker, 1)[1].split("\n-->", 1)[0])
    assert summary["schema_version"] == "paper-reader-update-v1"
    assert summary["version"] == manifest["version"]
    assert summary["capability_matrix"] == manifest["capability_matrix"]
    assert summary["signed"] == manifest["signed"]
    assert summary["required_assets"] == sorted(first_files)

    sums = first_files["SHA256SUMS.txt"]
    assert b"\r" not in sums
    lines = sums.decode("ascii").splitlines()
    assert lines == sorted(lines, key=lambda value: value.split("  ", 1)[1].casefold())
    assert all(line.count("  ") == 1 for line in lines)
    assert {line.split("  ", 1)[1] for line in lines} == set(first_files) - {"SHA256SUMS.txt"}
    assert verify_release_directory(first) == first_report


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "size", "hash", "crlf"])
def test_release_verifier_rejects_closed_set_and_checksum_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    release = tmp_path / mutation
    _build_fixture_release(release, tmp_path)
    installer = release / "paper-reader-0.8.0-windows-x64-setup.exe"
    sums = release / "SHA256SUMS.txt"
    if mutation == "missing":
        installer.unlink()
    elif mutation == "extra":
        (release / "unexpected.txt").write_text("extra", encoding="utf-8")
    elif mutation == "duplicate":
        first_line = sums.read_text(encoding="ascii").splitlines()[0]
        sums.write_bytes(sums.read_bytes() + f"{first_line}\n".encode("ascii"))
    elif mutation == "size":
        installer.write_bytes(installer.read_bytes()[:-1])
    elif mutation == "hash":
        installer.write_bytes(b"NZ" + installer.read_bytes()[2:])
    else:
        sums.write_bytes(sums.read_bytes().replace(b"\n", b"\r\n"))

    with pytest.raises(ReleaseMetadataError):
        verify_release_directory(release)


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("database_schema_revision", "9999_future_schema", "database_revision_contract"),
        ("openapi_sha256", "0" * 64, "release_manifest_contract"),
        ("openapi_version", "9.9.9", "release_manifest_contract"),
        ("plugin_protocol_version", "2.0", "release_manifest_contract"),
    ],
)
def test_release_verifier_rejects_frozen_contract_drift_before_trusting_checksums(
    tmp_path: Path,
    field: str,
    value: str,
    error_code: str,
) -> None:
    release = tmp_path / field
    _build_fixture_release(release, tmp_path)
    manifest_path = release / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_bytes(
        (
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    )

    with pytest.raises(ReleaseMetadataError) as failure:
        verify_release_directory(release)

    assert failure.value.code == error_code


def test_unsigned_notice_is_explicit_and_never_claims_platform_trust() -> None:
    notice = Path("packaging/UNSIGNED.md").read_text(encoding="utf-8")

    assert "not digitally signed" in notice
    assert "SmartScreen" in notice
    assert "SHA256SUMS.txt" in notice
    assert "Get-FileHash" in notice
    assert "release-manifest.json" in notice
    assert "does not establish publisher trust" in notice


def test_signing_is_explicit_json_argv_only_and_v08_stays_unsigned(tmp_path: Path) -> None:
    environment = {
        "PAPER_READER_SIGN_COMMAND": json.dumps(
            [sys.executable, "-c", "print('sign')", "{artifact}"]
        ),
        "PAPER_READER_CERTIFICATE": "must-not-be-probed",
    }
    assert sign_command_from_environment(enabled=False, environment=environment) is None
    command = sign_command_from_environment(enabled=True, environment=environment)
    assert command is not None
    assert command[0] == str(Path(sys.executable).absolute())
    assert command.count("{artifact}") == 1

    bad_values = (
        "signtool sign {artifact}",
        json.dumps(["relative.exe", "{artifact}"]),
        json.dumps([sys.executable, "missing-placeholder"]),
        json.dumps([sys.executable, "{artifact}", "{artifact}"]),
    )
    for value in bad_values:
        with pytest.raises(ReleasePackageError):
            sign_command_from_environment(
                enabled=True,
                environment={"PAPER_READER_SIGN_COMMAND": value},
            )

    artifact = tmp_path / "future-installer.exe"
    artifact.write_bytes(b"MZunsigned")
    scans: list[bytes] = []

    def scanner(path: Path) -> None:
        scans.append(path.read_bytes())

    signing_command = (
        str(Path(sys.executable).absolute()),
        "-c",
        "from pathlib import Path; import sys; p=Path(sys.argv[1]); "
        "p.write_bytes(p.read_bytes()+b'signed')",
        "{artifact}",
    )
    run_signing_hook(artifact=artifact, command=signing_command, scanner=scanner)
    assert scans == [b"MZunsigned", b"MZunsignedsigned"]

    with pytest.raises(ReleasePackageError, match="v08_must_be_unsigned"):
        run_signing_hook(
            artifact=artifact,
            command=signing_command,
            scanner=scanner,
            release_version="0.8.0",
        )


def test_signing_hook_uses_no_shell_and_a_closed_tool_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts import package_release

    artifact = tmp_path / "future.exe"
    artifact.write_bytes(b"MZbefore")
    executable = Path(sys.executable).absolute()
    calls: list[dict[str, Any]] = []

    def fake_run(command: Any, **kwargs: Any) -> Any:
        calls.append({"command": command, **kwargs})
        artifact.write_bytes(b"MZafter")
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr(package_release.subprocess, "run", fake_run)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-signer")
    scans: list[bytes] = []
    package_release.run_signing_hook(
        artifact=artifact,
        command=(str(executable), "--fixture", "{artifact}"),
        scanner=lambda path: scans.append(path.read_bytes()),
    )

    assert scans == [b"MZbefore", b"MZafter"]
    assert len(calls) == 1
    assert calls[0]["shell"] is False
    assert calls[0]["command"] == (str(executable), "--fixture", str(artifact))
    assert calls[0]["env"]["PATH"] == str(executable.parent)
    assert "OPENAI_API_KEY" not in calls[0]["env"]


def test_full_release_bundle_stages_sboms_and_metadata_before_one_promotion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts import package_release

    distribution = tmp_path / "distribution" / "paper-reader"
    distribution.mkdir(parents=True)
    output = tmp_path / "release"
    python = Path(sys.executable).absolute()
    npm = tmp_path / "tools" / "npm.cmd"
    npm.parent.mkdir()
    npm.write_bytes(b"@echo off\r\n")

    def fake_package_distribution(**kwargs: Any) -> Any:
        destination = Path(kwargs["output_dir"])
        destination.mkdir(parents=True, exist_ok=True)
        setup_name = f"paper-reader-{APPLICATION_VERSION}-windows-x64-setup.exe"
        portable_name = f"paper-reader-{APPLICATION_VERSION}-windows-x64-portable.zip"
        (destination / setup_name).write_bytes(b"MZsetup")
        (destination / portable_name).write_bytes(b"PKzip")
        return SimpleNamespace(
            version=APPLICATION_VERSION,
            portable_path=portable_name,
            installer_path=setup_name,
        )

    def fake_python_sbom(**kwargs: Any) -> Path:
        destination = Path(kwargs["destination"])
        _write_sbom(destination, "python", version=APPLICATION_VERSION)
        return destination

    def fake_npm_sbom(**kwargs: Any) -> Path:
        destination = Path(kwargs["destination"])
        _write_sbom(destination, "npm", version=APPLICATION_VERSION)
        return destination

    monkeypatch.setattr(package_release, "package_distribution", fake_package_distribution)
    monkeypatch.setattr(package_release, "build_python_sbom", fake_python_sbom, raising=False)
    monkeypatch.setattr(package_release, "build_npm_sbom", fake_npm_sbom, raising=False)

    report = package_release.package_release_bundle(
        distribution=distribution,
        output_dir=output,
        python_executable=python,
        npm_executable=npm,
        git_commit="b" * 40,
        database_revision=FROZEN_DATABASE_HEAD,
        iscc=None,
        sign_command=None,
    )

    assert report.version == APPLICATION_VERSION
    assert report.metadata == verify_release_directory(output)
    assert report.metadata.file_count == 10
    assert not tuple(output.glob(".*.candidate-*"))
    assert not tuple(output.parent.glob(".release-staging-*"))
    # GitHub serves the same generated notes and filenames to the in-app update checker.
    from paper_reader.updates.service import RELEASE_REPOSITORY, _parse_release

    tag = f"v{APPLICATION_VERSION}"
    base = f"https://github.com/{RELEASE_REPOSITORY}/releases"
    release, draft, prerelease = _parse_release(
        {
            "tag_name": tag,
            "draft": False,
            "prerelease": False,
            "published_at": "2026-11-18T00:00:00Z",
            "html_url": f"{base}/tag/{tag}",
            "body": (output / "RELEASE_NOTES.md").read_text(encoding="utf-8"),
            "assets": [
                {
                    "name": asset.name,
                    "size": asset.stat().st_size,
                    "browser_download_url": f"{base}/download/{tag}/{asset.name}",
                    "digest": f"sha256:{hashlib.sha256(asset.read_bytes()).hexdigest()}",
                }
                for asset in output.iterdir()
            ],
        }
    )
    assert not draft and not prerelease
    assert len(release.assets) == report.metadata.file_count
    source_notes = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release.changelog in source_notes and release.migration_notes in source_notes


def test_release_cli_requires_explicit_environment_and_source_identity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from scripts import package_release

    monkeypatch.setattr(
        package_release,
        "package_distribution",
        lambda **_kwargs: pytest.fail("packaging started before metadata inputs were validated"),
    )
    monkeypatch.setattr(sys, "argv", ["package_release.py", str(tmp_path / "paper-reader")])

    assert package_release.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": "release_metadata_inputs_required",
        "ok": False,
    }
