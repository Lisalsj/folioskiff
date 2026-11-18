from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path

import pytest

from paper_reader import __version__

ROOT = Path(__file__).parents[2]
LAYOUT = ROOT / "packaging/release-layout.json"


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    major, minor, patch = (int(value) for value in version.split("."))
    return major, minor, patch, 0


def _fake_pe(
    path: Path,
    *,
    machine: int = 0x8664,
    subsystem: int = 2,
    include_version: bool = False,
    version: str = __version__,
) -> None:
    payload = bytearray(4096)
    payload[0:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", payload, 0x84, machine)
    struct.pack_into("<H", payload, 0x94, 0xF0)
    struct.pack_into("<H", payload, 0x98, 0x20B)
    struct.pack_into("<H", payload, 0x98 + 68, subsystem)
    if include_version:
        major, minor, patch, build = _version_tuple(version)
        fixed = struct.pack(
            "<13I",
            0xFEEF04BD,
            0x00010000,
            (major << 16) | minor,
            (patch << 16) | build,
            (major << 16) | minor,
            (patch << 16) | build,
            0x3F,
            0,
            0x40004,
            1,
            0,
            0,
            0,
        )
        payload[0x200 : 0x200 + len(fixed)] = fixed
        fields = {
            "CompanyName": "FolioSkiff contributors",
            "FileDescription": "FolioSkiff local research workspace",
            "FileVersion": f"{version}.0",
            "LegalCopyright": "Licensed under Apache-2.0",
            "OriginalFilename": "paper-reader.exe",
            "ProductName": "页舟 · FolioSkiff",
            "ProductVersion": f"{version}.0",
        }
        encoded = "\x00".join(f"{key}\x00{value}" for key, value in fields.items()).encode(
            "utf-16le"
        )
        payload[0x300 : 0x300 + len(encoded)] = encoded
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _distribution(tmp_path: Path, *, name: str = "paper-reader") -> Path:
    from paper_reader.packaging.distribution import write_bundle_manifest

    root = tmp_path / name
    internal = root / "_internal"
    internal.mkdir(parents=True)
    (root / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
    (root / "THIRD_PARTY_NOTICES.md").write_bytes(
        (ROOT / "packaging/THIRD_PARTY_NOTICES.md").read_bytes()
    )
    _fake_pe(root / "paper-reader.exe", include_version=True)
    _fake_pe(internal / "runtime.dll")
    (internal / "paper_reader" / "static").mkdir(parents=True)
    (internal / "paper_reader" / "static" / "index.html").write_text(
        "<!doctype html>", encoding="utf-8"
    )
    write_bundle_manifest(root, version=__version__)
    return root


def _verify(root: Path, **kwargs: object):
    from paper_reader.packaging.distribution import verify_distribution

    return verify_distribution(
        root,
        layout_path=LAYOUT,
        project_root=ROOT,
        **kwargs,
    )


def test_distribution_scanner_accepts_closed_x64_gui_bundle(tmp_path: Path) -> None:
    root = _distribution(tmp_path / ("含 空格-" + "x" * 72))

    # Python modules may use old domain words; runtime data/cache forms remain banned.
    module_fixture = root / "_internal/paper_reader/backups/schema.json"
    module_fixture.parent.mkdir(parents=True)
    module_fixture.write_text("{}", encoding="utf-8")
    from paper_reader.packaging.distribution import write_bundle_manifest

    write_bundle_manifest(root, version=__version__)

    report = _verify(root)

    assert report.application == "paper-reader"
    assert report.version == __version__
    assert report.architecture == "x86_64"
    assert report.entrypoint == "paper-reader.exe"
    assert report.file_count == 6
    assert report.total_size_bytes > 8192
    assert len(report.manifest_sha256) == 64
    assert str(tmp_path) not in repr(report)


def test_bundle_manifest_is_root_independent_and_detects_tampering(tmp_path: Path) -> None:
    first = _distribution(tmp_path / "first")
    second = _distribution(tmp_path / "second")
    assert (first / "release-manifest.json").read_bytes() == (
        second / "release-manifest.json"
    ).read_bytes()

    target = first / "_internal/paper_reader/static/index.html"
    target.write_bytes(b"x" * target.stat().st_size)
    with pytest.raises(Exception, match="distribution_file_hash_mismatch") as captured:
        _verify(first)
    assert str(tmp_path) not in str(captured.value)


@pytest.mark.parametrize(
    ("relative", "content", "code"),
    [
        ("_internal/weights.onnx", b"model", "distribution_forbidden_suffix"),
        (
            "_internal/ch_PP-OCRv5_rec_mobile.onnx.txt",
            b"model",
            "distribution_model_asset_present",
        ),
        ("_internal/.huggingface/token", b"cache", "distribution_forbidden_name"),
        ("_internal/models--vendor--model/blob", b"cache", "distribution_model_cache"),
        ("_internal/.git/config", b"git", "distribution_forbidden_name"),
        ("_internal/__pycache__/module.pyc", b"bytecode", "distribution_build_cache"),
        ("_internal/python.exe", b"python", "distribution_forbidden_name"),
        ("_internal/node.exe", b"node", "distribution_forbidden_name"),
        ("_internal/paper-reader.sqlite3", b"db", "distribution_forbidden_name"),
    ],
)
def test_distribution_scan_rejects_models_caches_and_user_data(
    tmp_path: Path,
    relative: str,
    content: bytes,
    code: str,
) -> None:
    from paper_reader.packaging.distribution import DistributionError, write_bundle_manifest

    root = _distribution(tmp_path)
    target = root.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    write_bundle_manifest(root, version=__version__)

    with pytest.raises(DistributionError, match=code):
        _verify(root)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("console", "distribution_entrypoint_console"),
        ("x86", "distribution_pe_architecture"),
        ("second_exe", "distribution_entrypoint_count"),
        ("missing_license", "distribution_top_level"),
        ("wrong_license", "distribution_license_mismatch"),
        ("wrong_version", "distribution_version_mismatch"),
        ("extra_file", "distribution_manifest_members"),
    ],
)
def test_distribution_scan_rejects_ambiguous_or_modified_bundles(
    tmp_path: Path,
    mutation: str,
    code: str,
) -> None:
    from paper_reader.packaging.distribution import DistributionError, write_bundle_manifest

    root = _distribution(tmp_path)
    if mutation == "console":
        _fake_pe(root / "paper-reader.exe", subsystem=3, include_version=True)
        write_bundle_manifest(root, version=__version__)
    elif mutation == "x86":
        _fake_pe(root / "_internal/runtime.dll", machine=0x14C)
        write_bundle_manifest(root, version=__version__)
    elif mutation == "second_exe":
        _fake_pe(root / "_internal/helper.exe")
        write_bundle_manifest(root, version=__version__)
    elif mutation == "missing_license":
        (root / "LICENSE").unlink()
        write_bundle_manifest(root, version=__version__)
    elif mutation == "wrong_license":
        (root / "LICENSE").write_text("not the project license", encoding="utf-8")
        write_bundle_manifest(root, version=__version__)
    elif mutation == "wrong_version":
        _fake_pe(root / "paper-reader.exe", include_version=True, version="9.9.9")
        write_bundle_manifest(root, version=__version__)
    elif mutation == "extra_file":
        (root / "_internal/extra.txt").write_text("not declared", encoding="utf-8")
    else:  # pragma: no cover - parametrization invariant
        raise AssertionError(mutation)

    with pytest.raises(DistributionError, match=code):
        _verify(root)


def test_distribution_scan_rejects_build_paths_and_undeclared_large_files(
    tmp_path: Path,
) -> None:
    from paper_reader.packaging.distribution import DistributionError, write_bundle_manifest

    root = _distribution(tmp_path)
    leak = root / "_internal/leak.txt"
    build_root = tmp_path / "private-build-root"
    leak.write_text(str(build_root.resolve()), encoding="utf-8")
    write_bundle_manifest(root, version=__version__)
    with pytest.raises(DistributionError, match="distribution_build_path_leak"):
        _verify(root, forbidden_build_roots=(build_root,))

    leak.unlink()
    oversized = root / "_internal/oversized.dat"
    oversized.write_bytes(b"x" * 65)
    write_bundle_manifest(root, version=__version__, large_file_threshold=64)
    manifest = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
    manifest["large_files"] = []
    (root / "release-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(DistributionError, match="distribution_large_file_undeclared"):
        _verify(root, large_file_threshold=64)


def test_distribution_scan_rejects_linked_members_when_supported(tmp_path: Path) -> None:
    from paper_reader.packaging.distribution import DistributionError, write_bundle_manifest

    root = _distribution(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    link = root / "_internal/linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows profile")

    with pytest.raises(DistributionError, match="distribution_link_member"):
        write_bundle_manifest(root, version=__version__)


def test_manifest_hashes_cover_every_non_manifest_file(tmp_path: Path) -> None:
    root = _distribution(tmp_path)
    payload = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
    actual = {
        path.relative_to(root).as_posix(): {
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in root.rglob("*")
        if path.is_file() and path.name != "release-manifest.json"
    }
    declared = {
        item["path"]: {"size_bytes": item["size_bytes"], "sha256": item["sha256"]}
        for item in payload["files"]
    }

    assert payload["schema_version"] == "paper-reader-bundle-manifest-v1"
    assert payload["version"] == __version__
    assert declared == actual
    assert payload["large_files"] == []


@pytest.mark.skipif(
    os.environ.get("PAPER_READER_FROZEN_DIST") is None,
    reason="set PAPER_READER_FROZEN_DIST to run the real frozen smoke",
)
def test_real_frozen_distribution_passes_content_audit() -> None:
    root = Path(os.environ["PAPER_READER_FROZEN_DIST"])
    report = _verify(root, forbidden_build_roots=(ROOT,))
    assert report.entrypoint == "paper-reader.exe"


@pytest.mark.skipif(
    os.environ.get("PAPER_READER_FROZEN_DIST") is None,
    reason="set PAPER_READER_FROZEN_DIST to run the real frozen smoke",
)
def test_real_frozen_distribution_starts_without_toolchain(tmp_path: Path) -> None:
    from scripts.build_windows import smoke_distribution

    root = Path(os.environ["PAPER_READER_FROZEN_DIST"])
    smoke_distribution(root, scratch_root=tmp_path / "isolated smoke")
