"""Build and verify deterministic SBOMs and closed paper-reader release metadata."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from secrets import token_hex
from typing import Any, cast

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paper_reader.migrations.versions import FROZEN_DATABASE_HEAD  # noqa: E402
from paper_reader.openapi import (  # noqa: E402
    API_CONTRACT_VERSION,
    OPENAPI_CONTRACT_SHA256,
    load_openapi_document,
    openapi_contract_sha256,
    validate_openapi_contract,
)
from paper_reader.packaging.resources import (  # noqa: E402
    ReleaseLayoutError,
    load_release_layout,
)
from paper_reader.plugins.contracts import PLUGIN_PROTOCOL_VERSION  # noqa: E402

RELEASE_SCHEMA_VERSION = "paper-reader-release-manifest-v1"
BUNDLE_SCHEMA_VERSION = "paper-reader-bundle-manifest-v1"
UPDATE_SCHEMA_VERSION = "paper-reader-update-v1"
API_VERSION = API_CONTRACT_VERSION
CYCLONEDX_SPEC_VERSION = "1.6"
CYCLONEDX_SCHEMA = "https://cyclonedx.org/schema/bom-1.6.schema.json"

CAPABILITY_MATRIX: dict[str, str] = {
    "agent_analysis": "supported_on_demand_codex_or_ollama",
    "assistant": "ask_explain_translate_related_explicit_context",
    "backups": "supported_local_and_optional_encryption",
    "discovery_sources": "five_official_sources_keys_may_be_required",
    "external_research": "related_resources_only_explicit_consent",
    "local_library": "supported",
    "macos": "not_supported",
    "onboarding": "four_step_skippable_except_storage",
    "pdf_reader_ocr": "supported_ocr_download_optional",
    "plugins": "supported_windows_sandbox_required",
    "portable": "supported_windows_x64",
    "recommendation": "supported",
    "telemetry": "none",
    "translation": "versioned_bilingual_english_to_simplified_chinese_default",
    "windows_installer": "supported_unsigned",
}

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_REVISION = re.compile(r"^[a-z0-9][a-z0-9_]{0,79}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHECKSUM_LINE = re.compile(
    r"^(?P<digest>[0-9a-f]{64})  (?P<name>[A-Za-z0-9][A-Za-z0-9._-]{0,199})$"
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_SBOM_BYTES = 64 * 1024 * 1024
_MAX_RELEASE_FILES = 32
_MAX_RELEASE_FILE_BYTES = 20 * 1024 * 1024 * 1024
_GENERATED_NAMES = {
    "CAPABILITY_MATRIX.md",
    "CHANGELOG.md",
    "RELEASE_NOTES.md",
    "SHA256SUMS.txt",
    "UNSIGNED.md",
    "release-manifest.json",
}
_REQUIRED_ARTIFACT_ROLES = {"installer", "portable", "python_sbom", "npm_sbom"}
_REQUIRED_RELEASE_ROLES = _REQUIRED_ARTIFACT_ROLES | {
    "capability_matrix",
    "changelog",
    "release_notes",
    "unsigned_notice",
}


class ReleaseMetadataError(RuntimeError):
    """Release metadata failed a closed, path-free validation boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReleaseMetadataReport:
    version: str
    git_commit: str
    signed: bool
    file_count: int
    manifest_sha256: str
    checksums_sha256: str


def normalize_cyclonedx_sbom(
    source: Mapping[str, Any],
    *,
    ecosystem: str,
    version: str,
) -> bytes:
    """Normalize trusted tool output to deterministic CycloneDX 1.6 JSON."""

    _validate_version(version)
    root_ref = _root_purl(ecosystem, version)
    try:
        payload = copy.deepcopy(dict(source))
    except (TypeError, ValueError) as error:
        raise ReleaseMetadataError("sbom_invalid") from error
    if payload.get("bomFormat") != "CycloneDX" or payload.get("version") != 1:
        raise ReleaseMetadataError("sbom_invalid")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ReleaseMetadataError("sbom_invalid")
    original_root = metadata.get("component")
    if not isinstance(original_root, dict):
        raise ReleaseMetadataError("sbom_root")
    if original_root.get("version") != version:
        raise ReleaseMetadataError("sbom_root")
    old_root_ref = original_root.get("bom-ref")
    if not isinstance(old_root_ref, str) or not old_root_ref:
        raise ReleaseMetadataError("sbom_root")

    normalized_metadata: dict[str, Any] = {
        "component": {
            "bom-ref": root_ref,
            "licenses": [{"license": {"id": "Apache-2.0"}}],
            "name": "paper-reader",
            "purl": root_ref,
            "type": "application",
            "version": version,
        },
        "lifecycles": [{"phase": "build"}],
    }
    tools = metadata.get("tools")
    if tools is not None:
        normalized_metadata["tools"] = _sort_nested_lists(copy.deepcopy(tools))

    raw_components = payload.get("components")
    if not isinstance(raw_components, list) or not raw_components:
        raise ReleaseMetadataError("sbom_components")
    components: list[dict[str, Any]] = []
    component_refs: set[str] = set()
    root_aliases = {old_root_ref}
    for raw_component in raw_components:
        if not isinstance(raw_component, dict):
            raise ReleaseMetadataError("sbom_components")
        component = cast(dict[str, Any], copy.deepcopy(raw_component))
        ref = component.get("bom-ref")
        name = component.get("name")
        component_version = component.get("version")
        if (
            not isinstance(ref, str)
            or not ref
            or not isinstance(name, str)
            or not name
            or not isinstance(component_version, str)
            or not component_version
        ):
            raise ReleaseMetadataError("sbom_components")
        is_paper_reader = name.casefold().replace("_", "-") == "paper-reader"
        if is_paper_reader and component_version != version:
            raise ReleaseMetadataError("sbom_environment_version")
        if ref == old_root_ref or (is_paper_reader and component_version == version):
            root_aliases.add(ref)
            continue
        if ref in component_refs or ref == root_ref:
            raise ReleaseMetadataError("sbom_components")
        component_refs.add(ref)
        if not component.get("licenses"):
            component["licenses"] = [{"license": {"name": "NOASSERTION"}}]
        component = cast(dict[str, Any], _sort_nested_lists(component))
        components.append(component)
    if not components:
        raise ReleaseMetadataError("sbom_components")
    components.sort(key=lambda item: cast(str, item["bom-ref"]).casefold())

    raw_dependencies = payload.get("dependencies")
    if not isinstance(raw_dependencies, list):
        raise ReleaseMetadataError("sbom_dependency_graph")
    dependencies: dict[str, set[str]] = {}
    for raw_dependency in raw_dependencies:
        if not isinstance(raw_dependency, dict) or not set(raw_dependency) <= {"ref", "dependsOn"}:
            raise ReleaseMetadataError("sbom_dependency_graph")
        ref = raw_dependency.get("ref")
        depends_on = raw_dependency.get("dependsOn", [])
        if not isinstance(ref, str) or not isinstance(depends_on, list):
            raise ReleaseMetadataError("sbom_dependency_graph")
        normalized_ref = root_ref if ref in root_aliases else ref
        values = dependencies.setdefault(normalized_ref, set())
        for dependency_ref in depends_on:
            if not isinstance(dependency_ref, str):
                raise ReleaseMetadataError("sbom_dependency_graph")
            normalized_dependency = root_ref if dependency_ref in root_aliases else dependency_ref
            if normalized_dependency != normalized_ref:
                values.add(normalized_dependency)
    dependencies.setdefault(root_ref, set())
    for ref in component_refs:
        dependencies.setdefault(ref, set())
    normalized_dependencies = [
        {"dependsOn": sorted(values, key=str.casefold), "ref": ref}
        for ref, values in dependencies.items()
    ]
    normalized_dependencies.sort(
        key=lambda item: (0 if item["ref"] == root_ref else 1, cast(str, item["ref"]).casefold())
    )

    normalized = {
        "$schema": CYCLONEDX_SCHEMA,
        "bomFormat": "CycloneDX",
        "specVersion": CYCLONEDX_SPEC_VERSION,
        "version": 1,
        "metadata": normalized_metadata,
        "components": components,
        "dependencies": normalized_dependencies,
    }
    result = _canonical_json_bytes(normalized)
    validate_cyclonedx_sbom(result, ecosystem=ecosystem, version=version)
    return result


def validate_cyclonedx_sbom(
    content: bytes,
    *,
    ecosystem: str,
    version: str,
) -> dict[str, Any]:
    """Validate canonical identity, licenses, graph, and the official 1.6 schema."""

    _validate_version(version)
    if (
        not content
        or len(content) > _MAX_SBOM_BYTES
        or b"\r" in content
        or not content.endswith(b"\n")
    ):
        raise ReleaseMetadataError("sbom_encoding")
    payload = _load_json_bytes(content, code="sbom_invalid")
    if content != _canonical_json_bytes(payload):
        raise ReleaseMetadataError("sbom_not_canonical")
    root_ref = _root_purl(ecosystem, version)
    expected_root = {
        "bom-ref": root_ref,
        "licenses": [{"license": {"id": "Apache-2.0"}}],
        "name": "paper-reader",
        "purl": root_ref,
        "type": "application",
        "version": version,
    }
    metadata = payload.get("metadata")
    root_component = metadata.get("component") if isinstance(metadata, dict) else None
    if isinstance(root_component, dict) and not root_component.get("licenses"):
        raise ReleaseMetadataError("sbom_license")
    if (
        payload.get("$schema") != CYCLONEDX_SCHEMA
        or payload.get("bomFormat") != "CycloneDX"
        or payload.get("specVersion") != CYCLONEDX_SPEC_VERSION
        or payload.get("version") != 1
        or not isinstance(metadata, dict)
        or metadata.get("component") != expected_root
    ):
        raise ReleaseMetadataError("sbom_root")
    components = payload.get("components")
    dependencies = payload.get("dependencies")
    if not isinstance(components, list) or not components:
        raise ReleaseMetadataError("sbom_components")
    refs = {root_ref}
    for component in components:
        if not isinstance(component, dict):
            raise ReleaseMetadataError("sbom_components")
        ref = component.get("bom-ref")
        if not isinstance(ref, str) or not ref or ref in refs:
            raise ReleaseMetadataError("sbom_components")
        if not isinstance(component.get("licenses"), list) or not component["licenses"]:
            raise ReleaseMetadataError("sbom_license")
        refs.add(ref)
    if not isinstance(dependencies, list) or len(dependencies) != len(refs):
        raise ReleaseMetadataError("sbom_dependency_graph")
    dependency_refs: set[str] = set()
    for dependency in dependencies:
        if not isinstance(dependency, dict) or set(dependency) != {"dependsOn", "ref"}:
            raise ReleaseMetadataError("sbom_dependency_graph")
        ref = dependency.get("ref")
        depends_on = dependency.get("dependsOn")
        if (
            not isinstance(ref, str)
            or ref not in refs
            or ref in dependency_refs
            or not isinstance(depends_on, list)
            or len(depends_on) != len(set(depends_on))
            or any(not isinstance(item, str) or item not in refs for item in depends_on)
        ):
            raise ReleaseMetadataError("sbom_dependency_graph")
        dependency_refs.add(ref)
    if dependency_refs != refs:
        raise ReleaseMetadataError("sbom_dependency_graph")
    _validate_official_schema(content.decode("utf-8"))
    return payload


def build_python_sbom(
    *,
    python_executable: Path,
    pyproject: Path,
    version: str,
    destination: Path,
) -> Path:
    """Generate a Python SBOM from the exact interpreter used for packaging."""

    python = _validated_executable(python_executable, names={"python.exe", "python"})
    project_file = _validated_input_file(pyproject, expected_name="pyproject.toml")
    before = _file_signature(project_file)
    command = (
        str(python),
        "-m",
        "cyclonedx_py",
        "environment",
        str(python),
        "--pyproject",
        str(project_file),
        "--mc-type",
        "application",
        "--spec-version",
        CYCLONEDX_SPEC_VERSION,
        "--output-reproducible",
        "--output-format",
        "JSON",
        "--output-file",
        "-",
        "--validate",
    )
    raw = _run_tool(command, cwd=project_file.parent, environment=_python_environment(python))
    if _file_signature(project_file) != before:
        raise ReleaseMetadataError("python_sbom_input_changed")
    payload = _load_json_bytes(raw, code="python_sbom_invalid")
    normalized = normalize_cyclonedx_sbom(payload, ecosystem="python", version=version)
    _atomic_write(destination, normalized)
    return Path(destination).absolute()


def build_npm_sbom(
    *,
    npm_executable: Path,
    project_root: Path,
    package_lock: Path,
    version: str,
    destination: Path,
) -> Path:
    """Generate an npm SBOM only from the exact package-lock.json."""

    npm = _validated_executable(npm_executable, names={"npm", "npm.cmd", "npm.exe"})
    project = Path(project_root).absolute()
    lock = _validated_input_file(package_lock, expected_name="package-lock.json")
    package_json = _validated_input_file(project / "package.json", expected_name="package.json")
    if lock.parent != project:
        raise ReleaseMetadataError("npm_sbom_project_boundary")
    before = (_file_signature(lock), _file_signature(package_json))
    output_parent = Path(destination).absolute().parent
    output_parent.mkdir(parents=True, exist_ok=True)
    if _has_reparse_ancestor(output_parent):
        raise ReleaseMetadataError("release_output_unsafe")
    with tempfile.TemporaryDirectory(
        prefix="paper-reader-npm-config-", dir=output_parent
    ) as raw_temp:
        config_root = Path(raw_temp)
        user_config = config_root / "user.npmrc"
        global_config = config_root / "global.npmrc"
        user_config.write_bytes(b"")
        global_config.write_bytes(b"")
        command = (
            str(npm),
            "sbom",
            "--sbom-format=cyclonedx",
            "--package-lock-only",
            "--sbom-type=application",
            "--ignore-scripts",
            f"--userconfig={user_config}",
            f"--globalconfig={global_config}",
        )
        raw = _run_tool(command, cwd=project, environment=_npm_environment(npm))
    if (_file_signature(lock), _file_signature(package_json)) != before:
        raise ReleaseMetadataError("npm_sbom_input_changed")
    payload = _load_json_bytes(raw, code="npm_sbom_invalid")
    normalized = normalize_cyclonedx_sbom(payload, ecosystem="npm", version=version)
    _atomic_write(destination, normalized)
    return Path(destination).absolute()


def build_release_metadata(
    *,
    release_dir: Path,
    version: str,
    git_commit: str,
    database_revision: str,
    artifact_roles: Mapping[str, str],
    changelog_path: Path,
    changelog_summary: str,
    migration_notes: str,
    signed: bool,
    openapi_path: Path | None = None,
) -> ReleaseMetadataReport:
    """Finalize a staging directory into a deterministic, closed release set."""

    _validate_release_facts(
        version=version,
        git_commit=git_commit,
        database_revision=database_revision,
        changelog_summary=changelog_summary,
        migration_notes=migration_notes,
        signed=signed,
    )
    if signed:
        raise ReleaseMetadataError("signed_release_not_supported")
    _validate_release_layout_contract()
    openapi_version, openapi_sha256 = _openapi_release_facts(
        openapi_path if openapi_path is not None else ROOT / "openapi/openapi.json",
        version=version,
    )
    root = _validated_release_directory(release_dir)
    roles = _validate_artifact_roles(artifact_roles, version=version)
    existing = _release_file_names(root)
    if existing != set(roles):
        raise ReleaseMetadataError("release_initial_members")
    python_sbom = next(name for name, role in roles.items() if role == "python_sbom")
    npm_sbom = next(name for name, role in roles.items() if role == "npm_sbom")
    validate_cyclonedx_sbom((root / python_sbom).read_bytes(), ecosystem="python", version=version)
    validate_cyclonedx_sbom((root / npm_sbom).read_bytes(), ecosystem="npm", version=version)

    changelog_source = _validated_input_file(changelog_path, expected_name=None)
    changelog = changelog_source.read_bytes()
    if not changelog or len(changelog) > 2 * 1024 * 1024 or b"\x00" in changelog:
        raise ReleaseMetadataError("release_changelog_invalid")
    unsigned = _validated_input_file(
        ROOT / "packaging/UNSIGNED.md", expected_name="UNSIGNED.md"
    ).read_bytes()
    generated: dict[str, bytes] = {
        "CAPABILITY_MATRIX.md": _capability_matrix_markdown(),
        "CHANGELOG.md": changelog,
        "UNSIGNED.md": unsigned,
    }
    required_names = sorted(set(roles) | _GENERATED_NAMES)
    generated["RELEASE_NOTES.md"] = _release_notes(
        version=version,
        changelog_summary=changelog_summary,
        migration_notes=migration_notes,
        signed=signed,
        required_assets=required_names,
    )
    for name, content in generated.items():
        _atomic_write(root / name, content)

    complete_roles = dict(roles)
    complete_roles.update(
        {
            "CAPABILITY_MATRIX.md": "capability_matrix",
            "CHANGELOG.md": "changelog",
            "RELEASE_NOTES.md": "release_notes",
            "UNSIGNED.md": "unsigned_notice",
        }
    )
    assets = [
        _asset_record(root / name, role=role)
        for name, role in sorted(complete_roles.items(), key=lambda item: item[0].casefold())
    ]
    manifest = {
        "api_version": API_VERSION,
        "assets": assets,
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "capability_matrix": CAPABILITY_MATRIX,
        "changelog_summary": changelog_summary,
        "database_schema_revision": database_revision,
        "git_commit": git_commit,
        "migration_notes": migration_notes,
        "openapi_sha256": openapi_sha256,
        "openapi_version": openapi_version,
        "platform": {
            "architecture": "x86_64",
            "minimum_windows_build": 22000,
            "operating_system": "windows",
        },
        "plugin_protocol_version": PLUGIN_PROTOCOL_VERSION,
        "required_assets": required_names,
        "schema_version": RELEASE_SCHEMA_VERSION,
        "signed": signed,
        "version": version,
    }
    _atomic_write(root / "release-manifest.json", _canonical_json_bytes(manifest))
    checksum_names = sorted(required_names, key=str.casefold)
    checksum_names.remove("SHA256SUMS.txt")
    sums = "".join(f"{_sha256_file(root / name)}  {name}\n" for name in checksum_names).encode(
        "ascii"
    )
    _atomic_write(root / "SHA256SUMS.txt", sums)
    return verify_release_directory(root)


def verify_release_directory(release_dir: Path) -> ReleaseMetadataReport:
    """Verify a release without trusting its manifest or checksum inventory."""

    root = _validated_release_directory(release_dir)
    names = _release_file_names(root)
    if "release-manifest.json" not in names or "SHA256SUMS.txt" not in names:
        raise ReleaseMetadataError("release_members")
    manifest_bytes = (root / "release-manifest.json").read_bytes()
    manifest = _load_json_bytes(manifest_bytes, code="release_manifest_invalid")
    if manifest_bytes != _canonical_json_bytes(manifest):
        raise ReleaseMetadataError("release_manifest_not_canonical")
    expected_keys = {
        "api_version",
        "assets",
        "bundle_schema_version",
        "capability_matrix",
        "changelog_summary",
        "database_schema_revision",
        "git_commit",
        "migration_notes",
        "openapi_sha256",
        "openapi_version",
        "platform",
        "plugin_protocol_version",
        "required_assets",
        "schema_version",
        "signed",
        "version",
    }
    if set(manifest) != expected_keys:
        raise ReleaseMetadataError("release_manifest_fields")
    version = manifest.get("version")
    git_commit = manifest.get("git_commit")
    database_revision = manifest.get("database_schema_revision")
    changelog_summary = manifest.get("changelog_summary")
    migration_notes = manifest.get("migration_notes")
    openapi_sha256 = manifest.get("openapi_sha256")
    openapi_version = manifest.get("openapi_version")
    signed = manifest.get("signed")
    if (
        not isinstance(version, str)
        or not isinstance(git_commit, str)
        or not isinstance(database_revision, str)
        or not isinstance(changelog_summary, str)
        or not isinstance(migration_notes, str)
        or not isinstance(openapi_sha256, str)
        or not isinstance(openapi_version, str)
        or not isinstance(signed, bool)
    ):
        raise ReleaseMetadataError("release_manifest_types")
    _validate_release_facts(
        version=version,
        git_commit=git_commit,
        database_revision=database_revision,
        changelog_summary=changelog_summary,
        migration_notes=migration_notes,
        signed=signed,
    )
    _validate_release_layout_contract()
    if (
        manifest.get("schema_version") != RELEASE_SCHEMA_VERSION
        or manifest.get("api_version") != API_VERSION
        or openapi_version != version
        or openapi_sha256 != OPENAPI_CONTRACT_SHA256
        or manifest.get("plugin_protocol_version") != PLUGIN_PROTOCOL_VERSION
        or manifest.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION
        or manifest.get("capability_matrix") != CAPABILITY_MATRIX
        or manifest.get("platform")
        != {
            "architecture": "x86_64",
            "minimum_windows_build": 22000,
            "operating_system": "windows",
        }
    ):
        raise ReleaseMetadataError("release_manifest_contract")
    if signed:
        raise ReleaseMetadataError("signed_release_not_supported")

    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise ReleaseMetadataError("release_assets")
    asset_names: set[str] = set()
    roles: set[str] = set()
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict) or set(raw_asset) != {
            "name",
            "role",
            "sha256",
            "size_bytes",
        }:
            raise ReleaseMetadataError("release_assets")
        name = raw_asset.get("name")
        role = raw_asset.get("role")
        digest = raw_asset.get("sha256")
        size = raw_asset.get("size_bytes")
        if (
            not isinstance(name, str)
            or _SAFE_NAME.fullmatch(name) is None
            or name in asset_names
            or not isinstance(role, str)
            or role in roles
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= _MAX_RELEASE_FILE_BYTES
        ):
            raise ReleaseMetadataError("release_assets")
        file = root / name
        if not file.is_file() or _is_link_or_reparse(file):
            raise ReleaseMetadataError("release_assets")
        if file.stat().st_size != size or _sha256_file(file) != digest:
            raise ReleaseMetadataError("release_asset_drift")
        asset_names.add(name)
        roles.add(role)
    if roles != _REQUIRED_RELEASE_ROLES:
        raise ReleaseMetadataError("release_asset_roles")
    expected_names = asset_names | {"release-manifest.json", "SHA256SUMS.txt"}
    required_assets = manifest.get("required_assets")
    if (
        not isinstance(required_assets, list)
        or any(not isinstance(item, str) for item in required_assets)
        or required_assets != sorted(expected_names)
        or names != expected_names
    ):
        raise ReleaseMetadataError("release_members")

    role_to_name = {cast(str, asset["role"]): cast(str, asset["name"]) for asset in raw_assets}
    validate_cyclonedx_sbom(
        (root / role_to_name["python_sbom"]).read_bytes(),
        ecosystem="python",
        version=version,
    )
    validate_cyclonedx_sbom(
        (root / role_to_name["npm_sbom"]).read_bytes(),
        ecosystem="npm",
        version=version,
    )
    if (root / "CAPABILITY_MATRIX.md").read_bytes() != _capability_matrix_markdown():
        raise ReleaseMetadataError("release_capability_matrix")
    unsigned_source = ROOT / "packaging/UNSIGNED.md"
    if (
        not unsigned_source.is_file()
        or (root / "UNSIGNED.md").read_bytes() != unsigned_source.read_bytes()
    ):
        raise ReleaseMetadataError("release_unsigned_notice")
    expected_notes = _release_notes(
        version=version,
        changelog_summary=changelog_summary,
        migration_notes=migration_notes,
        signed=signed,
        required_assets=cast(list[str], required_assets),
    )
    if (root / "RELEASE_NOTES.md").read_bytes() != expected_notes:
        raise ReleaseMetadataError("release_update_summary")
    _verify_checksums(root, names=expected_names)
    return ReleaseMetadataReport(
        version=version,
        git_commit=git_commit,
        signed=signed,
        file_count=len(names),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        checksums_sha256=_sha256_file(root / "SHA256SUMS.txt"),
    )


def _validate_official_schema(content: str) -> None:
    try:
        from cyclonedx.schema import SchemaVersion
        from cyclonedx.validation.json import JsonStrictValidator
    except ImportError as error:
        raise ReleaseMetadataError("sbom_schema_validator_unavailable") from error
    try:
        validation_error = JsonStrictValidator(SchemaVersion.V1_6).validate_str(content)
    except Exception as error:
        raise ReleaseMetadataError("sbom_schema_invalid") from error
    if validation_error is not None:
        raise ReleaseMetadataError("sbom_schema_invalid")


def _root_purl(ecosystem: str, version: str) -> str:
    if ecosystem == "python":
        return f"pkg:pypi/paper-reader@{version}"
    if ecosystem == "npm":
        return f"pkg:npm/paper-reader@{version}"
    raise ReleaseMetadataError("sbom_ecosystem")


def _sort_nested_lists(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sort_nested_lists(item) for key, item in value.items()}
    if isinstance(value, list):
        normalized = [_sort_nested_lists(item) for item in value]
        try:
            return sorted(normalized, key=lambda item: _canonical_json_bytes(item))
        except (TypeError, ValueError):
            return normalized
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError) as error:
        raise ReleaseMetadataError("json_not_canonicalizable") from error


def _load_json_bytes(content: bytes, *, code: str) -> dict[str, Any]:
    if not content or content.startswith(b"\xef\xbb\xbf"):
        raise ReleaseMetadataError(code)

    def reject_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseMetadataError(code)
            result[key] = value
        return result

    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ReleaseMetadataError(code) from error
    if not isinstance(value, dict):
        raise ReleaseMetadataError(code)
    return cast(dict[str, Any], value)


def _run_tool(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
) -> bytes:
    try:
        completed = subprocess.run(
            tuple(command),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            shell=False,
            timeout=4 * 60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleaseMetadataError("sbom_tool_failed") from error
    if completed.returncode != 0 or not completed.stdout or len(completed.stdout) > _MAX_SBOM_BYTES:
        raise ReleaseMetadataError("sbom_tool_failed")
    return completed.stdout


def _base_tool_environment(executable: Path) -> dict[str, str]:
    environment: dict[str, str] = {}
    for name in (
        "COMSPEC",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment.update(
        {
            "NO_COLOR": "1",
            "PATH": str(executable.parent),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": "",
        }
    )
    return environment


def _python_environment(python: Path) -> dict[str, str]:
    environment = _base_tool_environment(python)
    environment["VIRTUAL_ENV"] = str(python.parent.parent)
    return environment


def _npm_environment(npm: Path) -> dict[str, str]:
    environment = _base_tool_environment(npm)
    environment.update(
        {
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "NO_PROXY": "*",
            "NPM_CONFIG_AUDIT": "false",
            "NPM_CONFIG_FUND": "false",
            "NPM_CONFIG_IGNORE_SCRIPTS": "true",
            "NPM_CONFIG_OFFLINE": "true",
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        }
    )
    return environment


def _validated_executable(path: Path, *, names: set[str]) -> Path:
    candidate = Path(path)
    if (
        not candidate.is_absolute()
        or candidate.name.casefold() not in names
        or not candidate.is_file()
        or _is_link_or_reparse(candidate)
    ):
        raise ReleaseMetadataError("sbom_tool_path")
    return candidate.absolute()


def _validated_input_file(path: Path, *, expected_name: str | None) -> Path:
    candidate = Path(path)
    if (
        not candidate.is_absolute()
        or (expected_name is not None and candidate.name != expected_name)
        or not candidate.is_file()
        or _is_link_or_reparse(candidate)
        or candidate.stat().st_size > _MAX_SBOM_BYTES
    ):
        raise ReleaseMetadataError("release_input_file")
    return candidate.absolute()


def _validated_release_directory(path: Path) -> Path:
    root = Path(path).absolute()
    if not root.is_dir() or root == Path(root.anchor) or _has_reparse_ancestor(root):
        raise ReleaseMetadataError("release_directory_unsafe")
    return root


def _release_file_names(root: Path) -> set[str]:
    children = tuple(root.iterdir())
    if not children or len(children) > _MAX_RELEASE_FILES:
        raise ReleaseMetadataError("release_members")
    names: set[str] = set()
    folded: set[str] = set()
    for child in children:
        if (
            not child.is_file()
            or _is_link_or_reparse(child)
            or _SAFE_NAME.fullmatch(child.name) is None
            or child.name.casefold() in folded
            or child.stat().st_size > _MAX_RELEASE_FILE_BYTES
        ):
            raise ReleaseMetadataError("release_members")
        names.add(child.name)
        folded.add(child.name.casefold())
    return names


def _validate_artifact_roles(roles: Mapping[str, str], *, version: str) -> dict[str, str]:
    if set(roles.values()) != _REQUIRED_ARTIFACT_ROLES or len(roles) != 4:
        raise ReleaseMetadataError("release_artifact_roles")
    expected = {
        f"paper-reader-{version}-windows-x64-setup.exe": "installer",
        f"paper-reader-{version}-windows-x64-portable.zip": "portable",
        f"paper-reader-{version}-python.cdx.json": "python_sbom",
        f"paper-reader-{version}-npm.cdx.json": "npm_sbom",
    }
    if dict(roles) != expected:
        raise ReleaseMetadataError("release_artifact_roles")
    return expected


def _validate_release_facts(
    *,
    version: str,
    git_commit: str,
    database_revision: str,
    changelog_summary: str,
    migration_notes: str,
    signed: bool,
) -> None:
    _validate_version(version)
    if _COMMIT.fullmatch(git_commit) is None or _REVISION.fullmatch(database_revision) is None:
        raise ReleaseMetadataError("release_identity")
    if database_revision != FROZEN_DATABASE_HEAD:
        raise ReleaseMetadataError("database_revision_contract")
    _validate_plain_text(changelog_summary, maximum=20_000)
    _validate_plain_text(migration_notes, maximum=10_000)
    if not isinstance(signed, bool):
        raise ReleaseMetadataError("release_signed_state")
    if version == "0.8.0" and signed:
        raise ReleaseMetadataError("v08_must_be_unsigned")


def _validate_release_layout_contract() -> None:
    try:
        layout = load_release_layout(ROOT / "packaging/release-layout.json")
    except ReleaseLayoutError as error:
        raise ReleaseMetadataError("release_layout_contract") from error
    if (
        layout.api_version != API_VERSION
        or layout.database_schema_revision != FROZEN_DATABASE_HEAD
        or layout.openapi_sha256 != OPENAPI_CONTRACT_SHA256
        or layout.plugin_protocol_version != PLUGIN_PROTOCOL_VERSION
    ):
        raise ReleaseMetadataError("release_layout_contract")


def _openapi_release_facts(path: Path, *, version: str) -> tuple[str, str]:
    try:
        document = load_openapi_document(path)
        validate_openapi_contract(document, application_version=version)
        digest = openapi_contract_sha256(document)
        info = cast(dict[str, Any], document["info"])
        openapi_version = cast(str, info["version"])
    except (KeyError, TypeError, ValueError) as error:
        raise ReleaseMetadataError("openapi_contract") from error
    return openapi_version, digest


def _validate_plain_text(value: str, *, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or _CONTROL.search(value)
        or "<" in value
        or ">" in value
    ):
        raise ReleaseMetadataError("release_text_invalid")


def _validate_version(version: str) -> None:
    if not isinstance(version, str) or _SEMVER.fullmatch(version) is None:
        raise ReleaseMetadataError("release_version")


def _asset_record(path: Path, *, role: str) -> dict[str, Any]:
    return {
        "name": path.name,
        "role": role,
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _capability_matrix_markdown() -> bytes:
    lines = [
        "# FolioSkiff capability matrix",
        "",
        "This file is generated from the same closed facts as `release-manifest.json`.",
        "",
        "| Capability | Status |",
        "|---|---|",
    ]
    lines.extend(f"| `{key}` | `{CAPABILITY_MATRIX[key]}` |" for key in sorted(CAPABILITY_MATRIX))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _release_notes(
    *,
    version: str,
    changelog_summary: str,
    migration_notes: str,
    signed: bool,
    required_assets: Sequence[str],
) -> bytes:
    summary = {
        "capability_matrix": CAPABILITY_MATRIX,
        "changelog": changelog_summary,
        "migration_notes": migration_notes,
        "required_assets": list(required_assets),
        "schema_version": UPDATE_SCHEMA_VERSION,
        "signed": signed,
        "version": version,
    }
    embedded = _canonical_json_bytes(summary).decode("utf-8").rstrip("\n")
    return (
        f"# 页舟 · FolioSkiff {version}\n\n"
        f"{changelog_summary}\n\n"
        f"Migration: {migration_notes}\n\n"
        "<!-- paper-reader-update-manifest:v1\n"
        f"{embedded}\n"
        "-->\n"
    ).encode()


def _verify_checksums(root: Path, *, names: set[str]) -> None:
    content = (root / "SHA256SUMS.txt").read_bytes()
    if not content or b"\r" in content or not content.endswith(b"\n"):
        raise ReleaseMetadataError("release_checksums_format")
    try:
        lines = content.decode("ascii").splitlines()
    except UnicodeError as error:
        raise ReleaseMetadataError("release_checksums_format") from error
    entries: dict[str, str] = {}
    for line in lines:
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None or match.group("name") in entries:
            raise ReleaseMetadataError("release_checksums_format")
        entries[match.group("name")] = match.group("digest")
    if lines != sorted(lines, key=lambda value: value.split("  ", 1)[1].casefold()):
        raise ReleaseMetadataError("release_checksums_format")
    expected_names = names - {"SHA256SUMS.txt"}
    if set(entries) != expected_names:
        raise ReleaseMetadataError("release_checksums_members")
    for name, digest in entries.items():
        if _sha256_file(root / name) != digest:
            raise ReleaseMetadataError("release_checksums_drift")


def _atomic_write(destination: Path, content: bytes) -> None:
    target = Path(destination).absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    if _has_reparse_ancestor(target.parent) or (target.exists() and _is_link_or_reparse(target)):
        raise ReleaseMetadataError("release_output_unsafe")
    candidate = target.parent / f".{target.name}.candidate-{token_hex(8)}"
    try:
        with candidate.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if _has_reparse_ancestor(target.parent):
            raise ReleaseMetadataError("release_output_unsafe")
        os.replace(candidate, target)
    except ReleaseMetadataError:
        raise
    except OSError as error:
        raise ReleaseMetadataError("release_write_failed") from error
    finally:
        with suppress(OSError):
            candidate.unlink(missing_ok=True)


def _file_signature(path: Path) -> tuple[int, str]:
    return path.stat().st_size, _sha256_file(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse)


def _has_reparse_ancestor(path: Path) -> bool:
    current = Path(path).absolute()
    while True:
        if current.exists() and _is_link_or_reparse(current):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("release_dir", type=Path)
    args = parser.parse_args()
    try:
        report = verify_release_directory(args.release_dir)
    except ReleaseMetadataError as error:
        print(json.dumps({"ok": False, "error": error.code}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "report": asdict(report)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
