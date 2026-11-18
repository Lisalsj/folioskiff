"""Deterministic manifests and closed audits for Windows onedir distributions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import unicodedata
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from secrets import token_hex
from typing import Any, cast

from paper_reader.packaging.resources import ReleaseLayout, load_release_layout

_MANIFEST_NAME = "release-manifest.json"
_MANIFEST_SCHEMA = "paper-reader-bundle-manifest-v1"
_DEFAULT_LARGE_FILE_THRESHOLD = 64 * 1024 * 1024
_MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_MAX_FILE_BYTES = 1024 * 1024 * 1024
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MODELISH_SUFFIXES = {".bin", ".ckpt", ".model", ".onnx", ".pt", ".pth", ".safetensors"}
_PE_SUFFIXES = {".dll", ".exe", ".pyd"}
_EXPECTED_VERSION_STRINGS = {
    "CompanyName": "FolioSkiff contributors",
    "FileDescription": "FolioSkiff local research workspace",
    "LegalCopyright": "Licensed under Apache-2.0",
    "OriginalFilename": "paper-reader.exe",
    "ProductName": "页舟 · FolioSkiff",
}


class DistributionError(ValueError):
    """A distribution does not satisfy the closed release contract."""

    def __init__(self, code: str, *, member: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.member = member if member is not None and _safe_relative_name(member) else None


@dataclass(frozen=True, slots=True)
class DistributionReport:
    application: str
    version: str
    architecture: str
    entrypoint: str
    file_count: int
    total_size_bytes: int
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class _FileRecord:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _BundleManifest:
    application: str
    version: str
    platform: str
    architecture: str
    bundle_mode: str
    entrypoint: str
    files: tuple[_FileRecord, ...]
    large_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PeInfo:
    machine: int
    subsystem: int
    fixed_version: tuple[int, int, int, int] | None
    payload: bytes


def write_bundle_manifest(
    root: Path,
    *,
    version: str,
    large_file_threshold: int = _DEFAULT_LARGE_FILE_THRESHOLD,
) -> Path:
    """Write a root-independent hash inventory, excluding the inventory itself."""

    _parse_version(version)
    _validate_large_file_threshold(large_file_threshold)
    distribution = _validated_root(root)
    files = _collect_files(distribution, omit_manifest=True)
    records = tuple(_record_file(distribution, path) for path in files)
    payload = {
        "application": "paper-reader",
        "architecture": "x86_64",
        "bundle_mode": "onedir",
        "entrypoint": "paper-reader.exe",
        "files": [
            {"path": item.path, "sha256": item.sha256, "size_bytes": item.size_bytes}
            for item in records
        ],
        "large_files": [item.path for item in records if item.size_bytes > large_file_threshold],
        "platform": "windows",
        "schema_version": _MANIFEST_SCHEMA,
        "version": version,
    }
    manifest_text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    encoded = (manifest_text + "\n").encode("utf-8")
    target = distribution / _MANIFEST_NAME
    temporary = distribution / f".{_MANIFEST_NAME}.{token_hex(8)}.tmp"
    try:
        _exclusive_write(temporary, encoded)
        os.replace(temporary, target)
    except OSError as error:
        raise DistributionError("distribution_manifest_write_failed") from error
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
    return target


def verify_distribution(
    root: Path,
    *,
    layout_path: Path,
    project_root: Path | None = None,
    forbidden_build_roots: Sequence[Path] = (),
    large_file_threshold: int = _DEFAULT_LARGE_FILE_THRESHOLD,
) -> DistributionReport:
    """Audit a complete onedir tree without executing any bundled code."""

    _validate_large_file_threshold(large_file_threshold)
    distribution = _validated_root(root)
    layout = load_release_layout(layout_path)
    _verify_top_level(distribution, layout)
    manifest_path = distribution / _MANIFEST_NAME
    manifest = _load_manifest(manifest_path, layout)
    files = _collect_files(distribution, omit_manifest=True)
    actual_by_name = {_relative_name(distribution, path): path for path in files}
    declared_by_name = {item.path: item for item in manifest.files}
    if set(actual_by_name) != set(declared_by_name):
        raise DistributionError("distribution_manifest_members")

    actual_large: list[str] = []
    total_size = 0
    for relative in sorted(actual_by_name, key=str.casefold):
        path = actual_by_name[relative]
        expected = declared_by_name[relative]
        size = path.stat().st_size
        total_size += size
        if size != expected.size_bytes:
            raise DistributionError("distribution_file_size_mismatch")
        if _sha256_file(path) != expected.sha256:
            raise DistributionError("distribution_file_hash_mismatch")
        if size > large_file_threshold:
            actual_large.append(relative)
    if tuple(actual_large) != manifest.large_files:
        raise DistributionError("distribution_large_file_undeclared")

    asset_descriptors = _load_model_asset_descriptors(project_root)
    for relative, path in actual_by_name.items():
        _verify_member_policy(relative, path, layout, asset_descriptors)
    _verify_no_build_path_leaks(
        distribution,
        (*files, manifest_path),
        forbidden_build_roots=forbidden_build_roots,
    )
    _verify_executable_contract(actual_by_name, manifest)
    if project_root is not None:
        _verify_legal_files(distribution, Path(project_root))

    return DistributionReport(
        application=manifest.application,
        version=manifest.version,
        architecture=manifest.architecture,
        entrypoint=manifest.entrypoint,
        file_count=len(files),
        total_size_bytes=total_size,
        manifest_sha256=_sha256_file(manifest_path),
    )


def _validated_root(root: Path) -> Path:
    candidate = Path(root).absolute()
    if not candidate.is_dir():
        raise DistributionError("distribution_root_missing")
    if _is_link_or_reparse(candidate):
        raise DistributionError("distribution_link_member")
    return candidate


def _collect_files(root: Path, *, omit_manifest: bool) -> tuple[Path, ...]:
    files: list[Path] = []
    seen: set[str] = set()
    try:
        for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            directory_names.sort(key=str.casefold)
            file_names.sort(key=str.casefold)
            for name in directory_names:
                directory = current_path / name
                if _is_link_or_reparse(directory):
                    raise DistributionError("distribution_link_member")
                _register_relative(root, directory, seen)
            for name in file_names:
                path = current_path / name
                if omit_manifest and path.parent == root and name == _MANIFEST_NAME:
                    continue
                if _is_link_or_reparse(path) or not path.is_file():
                    raise DistributionError("distribution_link_member")
                _register_relative(root, path, seen)
                files.append(path)
    except OSError as error:
        raise DistributionError("distribution_walk_failed") from error
    files.sort(key=lambda item: _relative_name(root, item).casefold())
    return tuple(files)


def _register_relative(root: Path, path: Path, seen: set[str]) -> None:
    relative = _relative_name(root, path)
    folded = relative.casefold()
    if folded in seen:
        raise DistributionError("distribution_member_collision")
    seen.add(folded)


def _relative_name(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as error:
        raise DistributionError("distribution_member_escape") from error
    if not _safe_relative_name(relative):
        raise DistributionError("distribution_member_name")
    return relative


def _safe_relative_name(value: str) -> bool:
    if not value or value != unicodedata.normalize("NFC", value) or "\\" in value:
        return False
    pure = PurePosixPath(value)
    return not pure.is_absolute() and all(part not in {"", ".", ".."} for part in pure.parts)


def _record_file(root: Path, path: Path) -> _FileRecord:
    size = path.stat().st_size
    if size > _MAX_FILE_BYTES:
        raise DistributionError("distribution_file_too_large")
    return _FileRecord(
        path=_relative_name(root, path),
        size_bytes=size,
        sha256=_sha256_file(path),
    )


def _verify_top_level(root: Path, layout: ReleaseLayout) -> None:
    try:
        children = tuple(root.iterdir())
    except OSError as error:
        raise DistributionError("distribution_walk_failed") from error
    names = {child.name for child in children}
    expected = {*layout.required_top_level_files, layout.internal_directory}
    if names != expected:
        raise DistributionError("distribution_top_level")
    if len({name.casefold() for name in names}) != len(names):
        raise DistributionError("distribution_member_collision")
    internal = root / layout.internal_directory
    if not internal.is_dir() or _is_link_or_reparse(internal):
        raise DistributionError("distribution_internal_missing")


def _load_manifest(path: Path, layout: ReleaseLayout) -> _BundleManifest:
    try:
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise DistributionError("distribution_manifest_too_large")
        raw = cast(Any, json.loads(path.read_text(encoding="utf-8")))
    except DistributionError:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise DistributionError("distribution_manifest_invalid") from error
    expected_keys = {
        "application",
        "architecture",
        "bundle_mode",
        "entrypoint",
        "files",
        "large_files",
        "platform",
        "schema_version",
        "version",
    }
    if not isinstance(raw, dict) or set(raw) != expected_keys:
        raise DistributionError("distribution_manifest_invalid")
    identity = {
        "application": layout.application,
        "architecture": layout.architecture,
        "bundle_mode": layout.bundle_mode,
        "entrypoint": layout.entrypoint,
        "platform": layout.platform,
        "schema_version": _MANIFEST_SCHEMA,
    }
    if any(raw.get(key) != value for key, value in identity.items()):
        raise DistributionError("distribution_manifest_identity")
    version = raw.get("version")
    if not isinstance(version, str):
        raise DistributionError("distribution_manifest_version")
    _parse_version(version)
    files = _parse_file_records(raw.get("files"))
    large_files = _parse_path_list(raw.get("large_files"))
    return _BundleManifest(
        application=layout.application,
        version=version,
        platform=layout.platform,
        architecture=layout.architecture,
        bundle_mode=layout.bundle_mode,
        entrypoint=layout.entrypoint,
        files=files,
        large_files=large_files,
    )


def _parse_file_records(value: object) -> tuple[_FileRecord, ...]:
    if not isinstance(value, list) or not value or len(value) > 100_000:
        raise DistributionError("distribution_manifest_files")
    records: list[_FileRecord] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {"path", "sha256", "size_bytes"}:
            raise DistributionError("distribution_manifest_files")
        path = raw.get("path")
        size = raw.get("size_bytes")
        digest = raw.get("sha256")
        if (
            not isinstance(path, str)
            or not _safe_relative_name(path)
            or path == _MANIFEST_NAME
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= _MAX_FILE_BYTES
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
        ):
            raise DistributionError("distribution_manifest_files")
        folded = path.casefold()
        if folded in seen:
            raise DistributionError("distribution_manifest_files")
        seen.add(folded)
        records.append(_FileRecord(path=path, size_bytes=size, sha256=digest))
    if records != sorted(records, key=lambda item: item.path.casefold()):
        raise DistributionError("distribution_manifest_order")
    return tuple(records)


def _parse_path_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 100_000:
        raise DistributionError("distribution_manifest_large_files")
    if any(not isinstance(item, str) or not _safe_relative_name(item) for item in value):
        raise DistributionError("distribution_manifest_large_files")
    items = cast(list[str], value)
    if items != sorted(items, key=str.casefold) or len({item.casefold() for item in items}) != len(
        items
    ):
        raise DistributionError("distribution_manifest_large_files")
    return tuple(items)


def _verify_member_policy(
    relative: str,
    path: Path,
    layout: ReleaseLayout,
    model_assets: tuple[tuple[str, int, str], ...],
) -> None:
    pure = PurePosixPath(relative)
    folded_parts = tuple(part.casefold() for part in pure.parts)
    if "__pycache__" in folded_parts or pure.suffix.casefold() in {".pyc", ".pyo"}:
        raise DistributionError("distribution_build_cache")
    if any(path.suffix.casefold() == suffix for suffix in layout.forbidden_suffixes):
        raise DistributionError("distribution_forbidden_suffix")
    forbidden_names = {name.casefold() for name in layout.forbidden_names}
    module_like_names = {"archive", "backups", "models"}
    always_forbidden = forbidden_names - module_like_names
    if any(part in always_forbidden for part in folded_parts):
        raise DistributionError("distribution_forbidden_name")
    if any(part.startswith("models--") for part in folded_parts) or any(
        part in {".modelscope", "modelscope-cache"} for part in folded_parts
    ):
        raise DistributionError("distribution_model_cache")
    filename = pure.name.casefold()
    for asset_name, expected_size, expected_sha256 in model_assets:
        modelish = PurePosixPath(asset_name).suffix.casefold() in _MODELISH_SUFFIXES
        if (modelish and asset_name.casefold() in filename) or (
            filename == PurePosixPath(asset_name).name.casefold()
            and path.stat().st_size == expected_size
            and _sha256_file(path) == expected_sha256
        ):
            raise DistributionError("distribution_model_asset_present")


def _load_model_asset_descriptors(project_root: Path | None) -> tuple[tuple[str, int, str], ...]:
    if project_root is None:
        return ()
    descriptors: list[tuple[str, int, str]] = []
    manifest_paths = (
        Path(project_root) / "src/paper_reader/data/e5-manifest.json",
        Path(project_root) / "src/paper_reader/data/rapidocr-manifest.json",
    )
    try:
        e5 = cast(dict[str, Any], json.loads(manifest_paths[0].read_text(encoding="utf-8")))
        for item in cast(list[dict[str, Any]], e5["assets"]):
            descriptors.append(
                (str(item["relative_path"]), int(item["size_bytes"]), str(item["sha256"]))
            )
        ocr = cast(dict[str, Any], json.loads(manifest_paths[1].read_text(encoding="utf-8")))
        for item in cast(list[dict[str, Any]], ocr["files"]):
            descriptors.append((str(item["filename"]), int(item["size"]), str(item["sha256"])))
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        raise DistributionError("distribution_model_manifest_invalid") from error
    return tuple(descriptors)


def _verify_no_build_path_leaks(
    root: Path,
    files: Iterable[Path],
    *,
    forbidden_build_roots: Sequence[Path],
) -> None:
    needles: set[bytes] = set()
    for forbidden_root in forbidden_build_roots:
        rendered = str(Path(forbidden_root).absolute())
        if len(rendered) < 4:
            continue
        for variant in {rendered, rendered.replace("\\", "/"), rendered.replace("/", "\\")}:
            needles.add(variant.encode("utf-8"))
            needles.add(variant.encode("utf-16le"))
    if not needles:
        return
    for path in files:
        for needle in needles:
            if _file_contains(path, needle):
                raise DistributionError(
                    "distribution_build_path_leak",
                    member=_relative_name(root, path),
                )


def _file_contains(path: Path, needle: bytes) -> bool:
    if not needle:
        return False
    overlap = max(0, len(needle) - 1)
    tail = b""
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                haystack = tail + chunk
                if needle in haystack:
                    return True
                tail = haystack[-overlap:] if overlap else b""
    except OSError as error:
        raise DistributionError("distribution_read_failed") from error
    return False


def _verify_executable_contract(
    actual_by_name: dict[str, Path],
    manifest: _BundleManifest,
) -> None:
    executables = [
        name for name in actual_by_name if PurePosixPath(name).suffix.casefold() == ".exe"
    ]
    if executables != [manifest.entrypoint]:
        raise DistributionError("distribution_entrypoint_count")
    for relative, path in actual_by_name.items():
        if PurePosixPath(relative).suffix.casefold() not in _PE_SUFFIXES:
            continue
        pe = _inspect_pe(path, require_version=relative == manifest.entrypoint)
        if pe.machine != 0x8664:
            raise DistributionError("distribution_pe_architecture")
        if relative == manifest.entrypoint:
            if pe.subsystem != 2:
                raise DistributionError("distribution_entrypoint_console")
            _verify_version_resource(pe, manifest.version)


def _inspect_pe(path: Path, *, require_version: bool) -> _PeInfo:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise DistributionError("distribution_pe_invalid") from error
    if len(payload) < 0x100 or payload[:2] != b"MZ":
        raise DistributionError("distribution_pe_invalid")
    pe_offset = struct.unpack_from("<I", payload, 0x3C)[0]
    if pe_offset + 24 + 70 > len(payload) or payload[pe_offset : pe_offset + 4] != b"PE\x00\x00":
        raise DistributionError("distribution_pe_invalid")
    machine = struct.unpack_from("<H", payload, pe_offset + 4)[0]
    optional_size = struct.unpack_from("<H", payload, pe_offset + 20)[0]
    optional = pe_offset + 24
    if optional_size < 70 or optional + optional_size > len(payload):
        raise DistributionError("distribution_pe_invalid")
    magic = struct.unpack_from("<H", payload, optional)[0]
    if magic not in {0x10B, 0x20B}:
        raise DistributionError("distribution_pe_invalid")
    subsystem = struct.unpack_from("<H", payload, optional + 68)[0]
    fixed = _find_fixed_version(payload) if require_version else None
    return _PeInfo(machine=machine, subsystem=subsystem, fixed_version=fixed, payload=payload)


def _find_fixed_version(payload: bytes) -> tuple[int, int, int, int] | None:
    signature = struct.pack("<I", 0xFEEF04BD)
    start = 0
    while True:
        offset = payload.find(signature, start)
        if offset < 0:
            return None
        if offset + 24 <= len(payload):
            values = struct.unpack_from("<6I", payload, offset)
            if values[1] == 0x00010000:
                return (
                    values[2] >> 16,
                    values[2] & 0xFFFF,
                    values[3] >> 16,
                    values[3] & 0xFFFF,
                )
        start = offset + 4


def _verify_version_resource(pe: _PeInfo, version: str) -> None:
    expected_tuple = (*_parse_version(version), 0)
    if pe.fixed_version != expected_tuple:
        raise DistributionError("distribution_version_mismatch")
    strings = {
        **_EXPECTED_VERSION_STRINGS,
        "FileVersion": f"{version}.0",
        "ProductVersion": f"{version}.0",
    }
    for key, value in strings.items():
        if key.encode("utf-16le") not in pe.payload or value.encode("utf-16le") not in pe.payload:
            raise DistributionError("distribution_version_mismatch")


def _verify_legal_files(root: Path, project_root: Path) -> None:
    expected = {
        "LICENSE": project_root / "LICENSE",
        "THIRD_PARTY_NOTICES.md": project_root / "packaging/THIRD_PARTY_NOTICES.md",
    }
    for name, source in expected.items():
        try:
            if (root / name).read_bytes() != source.read_bytes():
                raise DistributionError("distribution_license_mismatch")
        except OSError as error:
            raise DistributionError("distribution_license_mismatch") from error


def _parse_version(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise DistributionError("distribution_manifest_version")
    parts = tuple(int(item) for item in match.groups())
    if any(item > 65535 for item in parts):
        raise DistributionError("distribution_manifest_version")
    return cast(tuple[int, int, int], parts)


def _validate_large_file_threshold(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= _MAX_FILE_BYTES:
        raise DistributionError("distribution_large_file_threshold")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise DistributionError("distribution_read_failed") from error
    return digest.hexdigest()


def _exclusive_write(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        os.set_inheritable(descriptor, False)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        return bool(checker()) if checker is not None else False
    except OSError:
        return True
