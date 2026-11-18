from __future__ import annotations

import json
import os
import sqlite3
import stat
import struct
import warnings
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
NOW = datetime(2026, 7, 14, 13, 30, tzinfo=UTC)


def _paths(tmp_path: Path, name: str = "app"):  # type: ignore[no-untyped-def]
    from paper_reader.settings import AppPaths

    paths = AppPaths.from_overrides(
        state_root=tmp_path / name / "state",
        cache_root=tmp_path / name / "cache",
        library_root=tmp_path / name / "library",
    )
    paths.ensure()
    return paths


def _valid_backup(tmp_path: Path, *, with_object: bool = True):  # type: ignore[no-untyped-def]
    from paper_reader.backups.archive import BackupArchiveBuilder
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.migrations import upgrade_database
    from paper_reader.reader.imports import LibraryImportService, PdfImportInput
    from paper_reader.reader.storage import ContentStore

    source_paths = _paths(tmp_path, "source")
    upgrade_database(source_paths.database, snapshot_root=source_paths.migration_snapshots)
    imported = None
    if with_object:
        engine = create_sqlite_engine(source_paths.database)
        try:
            imported = LibraryImportService(
                sessions=create_session_factory(engine),
                store=ContentStore(source_paths.library_root),
                clock=lambda: NOW,
            ).import_pdf(
                PdfImportInput(
                    work_id=None,
                    title="Validated restore evidence",
                    authors=("Ada Researcher",),
                    doi="10.5555/restore.evidence",
                    original_filename="evidence.pdf",
                    rights_confirmed=True,
                ),
                (PDF_BYTES,),
            )
        finally:
            engine.dispose()
    package = source_paths.backups / "portable.paper-reader-backup"
    BackupArchiveBuilder(paths=source_paths, clock=lambda: NOW).create(destination=package)
    return source_paths, package, imported


def _read_members(package: Path) -> tuple[dict[str, bytes], dict[str, object]]:
    with zipfile.ZipFile(package) as archive:
        members = {info.filename: archive.read(info) for info in archive.infolist()}
    manifest = json.loads(members["manifest.json"])
    assert isinstance(manifest, dict)
    return members, manifest


def _write_package(
    destination: Path,
    members: dict[str, bytes],
    manifest: dict[str, object],
    *,
    additions: tuple[tuple[zipfile.ZipInfo | str, bytes], ...] = (),
) -> None:
    from paper_reader.backups.manifest import canonical_json_bytes

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            if name == "manifest.json":
                continue
            archive.writestr(name, payload)
        archive.writestr("manifest.json", canonical_json_bytes(manifest))
        for name, payload in additions:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr(name, payload)


def _assert_no_staging(paths) -> None:  # type: ignore[no-untyped-def]
    assert not tuple(paths.state_root.glob(".restore-stage-*"))
    assert not tuple(paths.library_root.glob(".restore-stage-*"))


def _patch_central_declared_size(package: Path, member_name: str, size: int) -> None:
    payload = bytearray(package.read_bytes())
    cursor = 0
    while True:
        cursor = payload.find(b"PK\x01\x02", cursor)
        if cursor < 0:
            raise AssertionError(f"central member not found: {member_name}")
        name_length = struct.unpack_from("<H", payload, cursor + 28)[0]
        extra_length = struct.unpack_from("<H", payload, cursor + 30)[0]
        comment_length = struct.unpack_from("<H", payload, cursor + 32)[0]
        name_start = cursor + 46
        name = bytes(payload[name_start : name_start + name_length]).decode("utf-8")
        if name == member_name:
            struct.pack_into("<I", payload, cursor + 24, size)
            package.write_bytes(payload)
            return
        cursor = name_start + name_length + extra_length + comment_length


def _minimal_manifest_dict() -> dict[str, object]:
    from paper_reader import __version__
    from paper_reader.backups.manifest import BackupDatabaseMember, BackupManifest
    from paper_reader.migrations import migration_head_revision

    return BackupManifest(
        format_version="paper-reader-backup-v1",
        app_version=__version__,
        schema_revision=migration_head_revision(),
        created_at=NOW,
        database=BackupDatabaseMember(
            archive_path="database/paper-reader.sqlite3",
            byte_size=128,
            sha256="a" * 64,
        ),
        objects=(),
        included=("SQLite database",),
        excluded=("credentials",),
    ).as_dict()


def _rewrite_database(
    package: Path,
    destination: Path,
    mutation: Callable[[sqlite3.Connection], None],
) -> None:
    members, manifest = _read_members(package)
    database_path = destination.with_suffix(".sqlite3")
    database_path.write_bytes(members["database/paper-reader.sqlite3"])
    connection = sqlite3.connect(database_path)
    try:
        mutation(connection)
        connection.commit()
    finally:
        connection.close()
    changed = database_path.read_bytes()
    database_path.unlink()
    members["database/paper-reader.sqlite3"] = changed
    manifest["database"]["byte_size"] = len(changed)  # type: ignore[index]
    manifest["database"]["sha256"] = sha256(changed).hexdigest()  # type: ignore[index]
    _write_package(destination, members, manifest)


def test_plain_and_encrypted_packages_validate_into_separate_same_volume_staging(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.crypto import encrypt_backup
    from paper_reader.backups.restore import RestorePackageValidator

    _source, package, imported = _valid_backup(tmp_path)
    target = _paths(tmp_path, "target")
    target.database.write_bytes(b"CURRENT-DATABASE-CANARY")
    current_object = target.objects / "current.pdf"
    current_object.write_bytes(b"CURRENT-OBJECT-CANARY")
    validator = RestorePackageValidator(paths=target)

    plain = validator.validate(package)
    try:
        assert plain.manifest.format_version == "paper-reader-backup-v1"
        assert plain.database_path.is_file()
        assert plain.state_stage_root.parent == target.state_root.resolve()
        assert plain.library_stage_root.parent == target.library_root.resolve()
        assert plain.database_path.is_relative_to(plain.state_stage_root)
        assert plain.objects_root.is_relative_to(plain.library_stage_root)
        assert plain.object_count == 1
        assert imported is not None
        restored_object = (
            plain.objects_root
            / imported.content_sha256[:2]
            / imported.content_sha256[2:4]
            / f"{imported.content_sha256}.pdf"
        )
        assert restored_object.read_bytes() == PDF_BYTES
        connection = sqlite3.connect(plain.database_path)
        try:
            assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        finally:
            connection.close()
    finally:
        plain.cleanup()

    encrypted_package = package.with_suffix(".encrypted")
    encrypt_backup(package, encrypted_package, password="portable password")
    encrypted = validator.validate(encrypted_package, password="portable password")
    try:
        assert encrypted.encrypted is True
        assert encrypted.object_count == 1
        assert encrypted.database_path.is_file()
    finally:
        encrypted.cleanup()

    assert target.database.read_bytes() == b"CURRENT-DATABASE-CANARY"
    assert current_object.read_bytes() == b"CURRENT-OBJECT-CANARY"
    _assert_no_staging(target)


@pytest.mark.parametrize(
    "member_name",
    [
        "/absolute.pdf",
        "../escape.pdf",
        "objects/aa/../escape.pdf",
        "C:/drive.pdf",
        "//server/share.pdf",
        "objects\\aa\\escape.pdf",
        "nul\x00hidden.pdf",
    ],
)
def test_restore_rejects_unsafe_member_paths(tmp_path: Path, member_name: str) -> None:
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    _source, package, _imported = _valid_backup(tmp_path, with_object=False)
    members, manifest = _read_members(package)
    hostile = tmp_path / f"hostile-{abs(hash(member_name))}.backup"
    _write_package(hostile, members, manifest, additions=((member_name, b"escape"),))
    target = _paths(tmp_path, "target")

    with pytest.raises(RestoreValidationError) as captured:
        RestorePackageValidator(paths=target).validate(hostile)

    assert captured.value.code == "invalid_package"
    _assert_no_staging(target)
    assert not (tmp_path / "escape.pdf").exists()


def test_restore_rejects_duplicate_case_collision_and_symlink_members(tmp_path: Path) -> None:
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    _source, package, _imported = _valid_backup(tmp_path, with_object=False)
    members, manifest = _read_members(package)
    duplicate = tmp_path / "duplicate.backup"
    _write_package(duplicate, members, manifest, additions=(("manifest.json", b"{}"),))
    collision = tmp_path / "collision.backup"
    _write_package(collision, members, manifest, additions=(("MANIFEST.JSON", b"{}"),))
    symlink = tmp_path / "symlink.backup"
    link_info = zipfile.ZipInfo("linked.pdf")
    link_info.create_system = 3
    link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    _write_package(symlink, members, manifest, additions=((link_info, b"../target"),))
    target = _paths(tmp_path, "target")

    for candidate in (duplicate, collision, symlink):
        with pytest.raises(RestoreValidationError) as captured:
            RestorePackageValidator(paths=target).validate(candidate)
        assert captured.value.code == "invalid_package"
        _assert_no_staging(target)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda value: value.update({"format_version": "paper-reader-backup-v2"}),
            "unsupported_version",
        ),
        (lambda value: value.update({"app_version": "999.0.0"}), "unsupported_version"),
        (lambda value: value.update({"schema_revision": "9999_future"}), "unsupported_version"),
        (lambda value: value.update({"surprise": True}), "invalid_package"),
        (lambda value: value.pop("excluded"), "invalid_package"),
        (
            lambda value: value["database"].update({"sha256": "f" * 64}),  # type: ignore[union-attr]
            "invalid_package",
        ),
        (
            lambda value: value["database"].update({"byte_size": 1}),  # type: ignore[union-attr]
            "invalid_package",
        ),
    ],
)
def test_restore_rejects_unknown_future_or_inconsistent_manifests(
    tmp_path: Path,
    mutation: Callable[[dict[str, object]], object],
    expected_code: str,
) -> None:
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    _source, package, _imported = _valid_backup(tmp_path, with_object=False)
    members, manifest = _read_members(package)
    mutation(manifest)
    changed = tmp_path / f"manifest-{expected_code}-{len(list(tmp_path.iterdir()))}.backup"
    _write_package(changed, members, manifest)
    target = _paths(tmp_path, "target")

    with pytest.raises(RestoreValidationError) as captured:
        RestorePackageValidator(paths=target).validate(changed)

    assert captured.value.code == expected_code
    _assert_no_staging(target)


def test_restore_rejects_extra_undeclared_member_and_noncanonical_object_path(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    _source, package, _imported = _valid_backup(tmp_path)
    members, manifest = _read_members(package)
    extra = tmp_path / "extra.backup"
    _write_package(extra, members, manifest, additions=(("notes.txt", b"private"),))

    old_path = str(manifest["objects"][0]["archive_path"])  # type: ignore[index]
    wrong_path = "objects/ff/ff/" + Path(old_path).name
    object_payload = members.pop(old_path)
    members[wrong_path] = object_payload
    manifest["objects"][0]["archive_path"] = wrong_path  # type: ignore[index]
    noncanonical = tmp_path / "noncanonical.backup"
    _write_package(noncanonical, members, manifest)
    target = _paths(tmp_path, "target")

    for candidate in (extra, noncanonical):
        with pytest.raises(RestoreValidationError) as captured:
            RestorePackageValidator(paths=target).validate(candidate)
        assert captured.value.code == "invalid_package"
        _assert_no_staging(target)


def test_restore_rejects_zip_bomb_before_extraction(tmp_path: Path) -> None:
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    _source, package, _imported = _valid_backup(tmp_path, with_object=False)
    members, manifest = _read_members(package)
    payload = b"0" * (2 * 1024 * 1024)
    digest = sha256(payload).hexdigest()
    archive_path = f"objects/{digest[:2]}/{digest[2:4]}/{digest}.pdf"
    manifest["objects"] = [
        {
            "archive_path": archive_path,
            "byte_size": len(payload),
            "content_sha256": digest,
            "sha256": digest,
        }
    ]
    bomb = tmp_path / "bomb.backup"
    _write_package(bomb, members, manifest, additions=((archive_path, payload),))
    target = _paths(tmp_path, "target")

    with pytest.raises(RestoreValidationError) as captured:
        RestorePackageValidator(paths=target).validate(bomb)

    assert captured.value.code == "invalid_package"
    _assert_no_staging(target)


def test_restore_rejects_database_integrity_failure_after_hash_validation(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    _source, package, _imported = _valid_backup(tmp_path, with_object=False)
    members, manifest = _read_members(package)
    corrupt = b"not a sqlite database but the manifest agrees"
    members["database/paper-reader.sqlite3"] = corrupt
    manifest["database"]["byte_size"] = len(corrupt)  # type: ignore[index]
    manifest["database"]["sha256"] = sha256(corrupt).hexdigest()  # type: ignore[index]
    broken = tmp_path / "broken-database.backup"
    _write_package(broken, members, manifest)
    target = _paths(tmp_path, "target")

    with pytest.raises(RestoreValidationError) as captured:
        RestorePackageValidator(paths=target).validate(broken)

    assert captured.value.code == "invalid_package"
    _assert_no_staging(target)


def test_restore_rejects_zip_declared_size_that_disagrees_with_actual_member(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    _source, package, _imported = _valid_backup(tmp_path, with_object=False)
    members, manifest = _read_members(package)
    mismatched = tmp_path / "central-size-mismatch.backup"
    _write_package(mismatched, members, manifest)
    _patch_central_declared_size(mismatched, "database/paper-reader.sqlite3", 1)
    target = _paths(tmp_path, "target")

    with pytest.raises(RestoreValidationError) as captured:
        RestorePackageValidator(paths=target).validate(mismatched)

    assert captured.value.code == "invalid_package"
    _assert_no_staging(target)


def test_restore_failure_or_cancellation_cleans_plaintext_and_preserves_current_data(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.crypto import encrypt_backup
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    _source, package, _imported = _valid_backup(tmp_path)
    encrypted = tmp_path / "encrypted.backup"
    encrypt_backup(package, encrypted, password="correct password")
    target = _paths(tmp_path, "target")
    target.database.write_bytes(b"CURRENT-DB-UNCHANGED")
    current = target.objects / "keep.pdf"
    current.write_bytes(b"CURRENT-OBJECT-UNCHANGED")
    validator = RestorePackageValidator(paths=target)

    with pytest.raises(RestoreValidationError) as wrong:
        validator.validate(encrypted, password="wrong password")
    assert wrong.value.code == "invalid_encrypted_backup"
    _assert_no_staging(target)

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 6

    with pytest.raises(RestoreValidationError) as stopped:
        validator.validate(encrypted, password="correct password", cancelled=cancelled)
    assert stopped.value.code == "cancelled"
    assert target.database.read_bytes() == b"CURRENT-DB-UNCHANGED"
    assert current.read_bytes() == b"CURRENT-OBJECT-UNCHANGED"
    _assert_no_staging(target)


def test_unknown_bytes_are_not_treated_as_a_backup(tmp_path: Path) -> None:
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    unknown = tmp_path / "unknown.backup"
    unknown.write_bytes(os.urandom(512))
    target = _paths(tmp_path, "target")

    with pytest.raises(RestoreValidationError) as captured:
        RestorePackageValidator(paths=target).validate(unknown)

    assert captured.value.code == "invalid_package"
    _assert_no_staging(target)


@pytest.mark.parametrize(
    "case",
    [
        "invalid_semver",
        "database_type",
        "database_path",
        "database_zero",
        "database_bool",
        "database_hash",
        "objects_type",
        "object_type",
        "object_hash_mismatch",
        "object_zero",
        "duplicate_object",
        "included_type",
        "included_duplicate",
        "created_at_zone",
        "created_at_invalid",
        "created_at_noncanonical",
        "noncanonical_json",
    ],
)
def test_manifest_parser_rejects_ambiguous_or_invalid_field_shapes(case: str) -> None:
    from paper_reader.backups.manifest import canonical_json_bytes
    from paper_reader.backups.restore import RestoreValidationError, parse_backup_manifest

    manifest = _minimal_manifest_dict()
    object_hash = "b" * 64
    valid_object = {
        "archive_path": f"objects/bb/bb/{object_hash}.pdf",
        "byte_size": 12,
        "content_sha256": object_hash,
        "sha256": object_hash,
    }
    if case == "invalid_semver":
        manifest["app_version"] = "not-semver"
    elif case == "database_type":
        manifest["database"] = None
    elif case == "database_path":
        manifest["database"]["archive_path"] = "other.sqlite3"  # type: ignore[index]
    elif case == "database_zero":
        manifest["database"]["byte_size"] = 0  # type: ignore[index]
    elif case == "database_bool":
        manifest["database"]["byte_size"] = True  # type: ignore[index]
    elif case == "database_hash":
        manifest["database"]["sha256"] = "not-a-hash"  # type: ignore[index]
    elif case == "objects_type":
        manifest["objects"] = None
    elif case == "object_type":
        manifest["objects"] = [None]
    elif case == "object_hash_mismatch":
        changed = dict(valid_object)
        changed["sha256"] = "c" * 64
        manifest["objects"] = [changed]
    elif case == "object_zero":
        changed = dict(valid_object)
        changed["byte_size"] = 0
        manifest["objects"] = [changed]
    elif case == "duplicate_object":
        manifest["objects"] = [valid_object, dict(valid_object)]
    elif case == "included_type":
        manifest["included"] = "SQLite database"
    elif case == "included_duplicate":
        manifest["included"] = ["SQLite database", "SQLite database"]
    elif case == "created_at_zone":
        manifest["created_at"] = "2026-07-14T13:30:00+00:00"
    elif case == "created_at_invalid":
        manifest["created_at"] = "not-a-dateZ"
    elif case == "created_at_noncanonical":
        manifest["created_at"] = "2026-07-14T13:30:00.100000Z"

    if case == "noncanonical_json":
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    else:
        payload = canonical_json_bytes(manifest)
    with pytest.raises(RestoreValidationError) as captured:
        parse_backup_manifest(payload)
    assert captured.value.code == "invalid_package"


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"{",
        b"[]",
        b'{"value":NaN}',
        b'{"app_version":"0.5.0","app_version":"0.5.0"}',
    ],
)
def test_manifest_parser_rejects_malformed_duplicate_or_non_object_json(payload: bytes) -> None:
    from paper_reader.backups.restore import RestoreValidationError, parse_backup_manifest

    with pytest.raises(RestoreValidationError) as captured:
        parse_backup_manifest(payload)
    assert captured.value.code == "invalid_package"


def test_manifest_parser_rejects_oversized_payload() -> None:
    from paper_reader.backups.restore import (
        MAX_MANIFEST_BYTES,
        RestoreValidationError,
        parse_backup_manifest,
    )

    with pytest.raises(RestoreValidationError):
        parse_backup_manifest(b"x" * (MAX_MANIFEST_BYTES + 1))


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "directory",
        "symlink",
        "encrypted_member",
        "unsupported_compression",
        "negative_size",
        "negative_compressed_size",
        "zero_compressed_bomb",
    ],
)
def test_zip_metadata_rejects_non_regular_or_impossible_members(case: str) -> None:
    from paper_reader.backups.restore import RestoreValidationError, _safe_member_name

    info = zipfile.ZipInfo("manifest.json")
    info.compress_type = zipfile.ZIP_DEFLATED
    info.file_size = 10
    info.compress_size = 10
    if case == "empty":
        info.filename = ""
        info.orig_filename = ""
    elif case == "directory":
        info.filename = "folder/"
        info.orig_filename = "folder/"
    elif case == "symlink":
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
    elif case == "encrypted_member":
        info.flag_bits = 0x1
    elif case == "unsupported_compression":
        info.compress_type = zipfile.ZIP_BZIP2
    elif case == "negative_size":
        info.file_size = -1
    elif case == "negative_compressed_size":
        info.compress_size = -1
    else:
        info.file_size = 2 * 1024 * 1024
        info.compress_size = 0

    with pytest.raises(RestoreValidationError):
        _safe_member_name(info)


def test_zip_directory_limits_reject_empty_count_and_total_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.backups import restore
    from paper_reader.backups.restore import RestoreValidationError, _inspect_members

    class FakeArchive:
        def __init__(self, infos: list[zipfile.ZipInfo]) -> None:
            self._infos = infos

        def infolist(self) -> list[zipfile.ZipInfo]:
            return self._infos

    with pytest.raises(RestoreValidationError):
        _inspect_members(cast(Any, FakeArchive([])))

    first = zipfile.ZipInfo("first")
    second = zipfile.ZipInfo("second")
    monkeypatch.setattr(restore, "MAX_MEMBER_COUNT", 1)
    with pytest.raises(RestoreValidationError):
        _inspect_members(cast(Any, FakeArchive([first, second])))

    monkeypatch.setattr(restore, "MAX_MEMBER_COUNT", 200_000)
    monkeypatch.setattr(restore, "MAX_TOTAL_UNCOMPRESSED_BYTES", 1)
    first.file_size = 2
    first.compress_size = 2
    with pytest.raises(RestoreValidationError):
        _inspect_members(cast(Any, FakeArchive([first])))


def test_restore_requires_password_rejects_empty_password_and_context_cleans(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.crypto import encrypt_backup
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    _source, package, _imported = _valid_backup(tmp_path, with_object=False)
    encrypted = tmp_path / "protected.backup"
    encrypt_backup(package, encrypted, password="correct password")
    target = _paths(tmp_path, "target")
    validator = RestorePackageValidator(paths=target)

    with pytest.raises(RestoreValidationError) as required:
        validator.validate(encrypted)
    assert required.value.code == "password_required"
    with pytest.raises(RestoreValidationError) as invalid:
        validator.validate(encrypted, password="")
    assert invalid.value.code == "invalid_password"

    with validator.validate(package) as staged:
        assert staged.database_path.is_file()
    staged.cleanup()
    _assert_no_staging(target)


def test_restore_rejects_missing_directory_empty_and_oversized_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.backups import restore
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    target = _paths(tmp_path, "target")
    validator = RestorePackageValidator(paths=target)
    empty = tmp_path / "empty.backup"
    empty.write_bytes(b"")
    for candidate in (tmp_path / "missing.backup", tmp_path, empty):
        with pytest.raises(RestoreValidationError):
            validator.validate(candidate)

    oversized = tmp_path / "oversized.backup"
    oversized.write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(restore, "MAX_PACKAGE_BYTES", 1)
    with pytest.raises(RestoreValidationError):
        validator.validate(oversized)
    _assert_no_staging(target)


def test_restore_rejects_archive_without_manifest(tmp_path: Path) -> None:
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    package = tmp_path / "missing-manifest.backup"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("database/paper-reader.sqlite3", b"not-used")
    target = _paths(tmp_path, "target")
    with pytest.raises(RestoreValidationError):
        RestorePackageValidator(paths=target).validate(package)
    _assert_no_staging(target)


def test_restore_database_evidence_rejects_schema_jobs_and_reference_drift(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    _source, package, _imported = _valid_backup(tmp_path)
    target = _paths(tmp_path, "target")

    def schema_drift(connection: sqlite3.Connection) -> None:
        connection.execute("UPDATE alembic_version SET version_num = '0022_comparison_reports'")

    def active_job(connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT INTO jobs "
            "(id, kind, status, progress, payload, result, checkpoint, error_code, "
            "error_message, idempotency_key, cancel_requested, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, ?, ?)",
            (
                "active-job",
                "analysis.generate",
                "queued",
                0,
                "{}",
                0,
                "2026-07-14 13:30:00",
                "2026-07-14 13:30:00",
            ),
        )

    def reference_drift(connection: sqlite3.Connection) -> None:
        connection.execute("UPDATE document_versions SET byte_size = byte_size + 1")

    def foreign_key_drift(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("UPDATE document_versions SET work_id = 'missing-work'")

    def conflicting_reference(connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT INTO works "
            "(id, canonical_title, normalized_title, authors, abstract, doi, "
            "published_at, created_at, updated_at) "
            "SELECT 'conflicting-work', canonical_title || ' conflict', "
            "normalized_title || ' conflict', authors, abstract, NULL, published_at, "
            "created_at, updated_at FROM works LIMIT 1"
        )
        columns = [
            str(row[1])
            for row in connection.execute("PRAGMA table_info(document_versions)")
        ]
        selected = []
        for column in columns:
            if column == "id":
                selected.append("'conflicting-document-version'")
            elif column == "work_id":
                selected.append("'conflicting-work'")
            elif column == "source_record_id":
                selected.append("NULL")
            elif column == "relative_path":
                selected.append("'objects/ff/ff/conflicting.pdf'")
            elif column == "byte_size":
                selected.append("byte_size + 1")
            else:
                selected.append(f'"{column}"')
        connection.execute(
            f"INSERT INTO document_versions ({', '.join(columns)}) "
            f"SELECT {', '.join(selected)} FROM document_versions LIMIT 1"
        )

    for name, mutation in (
        ("schema", schema_drift),
        ("job", active_job),
        ("reference", reference_drift),
        ("foreign-key", foreign_key_drift),
        ("conflict", conflicting_reference),
    ):
        changed = tmp_path / f"{name}-drift.backup"
        _rewrite_database(package, changed, mutation)
        with pytest.raises(RestoreValidationError) as captured:
            RestorePackageValidator(paths=target).validate(changed)
        assert captured.value.code == "invalid_package"
        _assert_no_staging(target)


def test_restore_rejects_package_identity_change_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.backups import restore
    from paper_reader.backups.restore import RestorePackageValidator, RestoreValidationError

    _source, package, _imported = _valid_backup(tmp_path, with_object=False)
    target = _paths(tmp_path, "target")
    original = restore._file_identity
    calls = 0

    def changed_identity(details: os.stat_result) -> tuple[int, int, int]:
        nonlocal calls
        calls += 1
        identity = original(details)
        if calls >= 4:
            return (identity[0], identity[1] + 1, identity[2])
        return identity

    monkeypatch.setattr(restore, "_file_identity", changed_identity)
    with pytest.raises(RestoreValidationError):
        RestorePackageValidator(paths=target).validate(package)
    _assert_no_staging(target)
