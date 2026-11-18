from __future__ import annotations

import json
import os
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from paper_reader import __version__

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
NOW = datetime(2026, 7, 14, 13, 30, tzinfo=UTC)


def _paths(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.settings import AppPaths

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    paths.ensure()
    return paths


def _seed_library(paths, *, shared: bool = True):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery import models as discovery_models
    from paper_reader.reader.imports import LibraryImportService, PdfImportInput
    from paper_reader.reader.models import LibraryEntryRecord
    from paper_reader.reader.storage import ContentStore

    assert discovery_models.DocumentVersionRecord.__tablename__ == "document_versions"
    assert LibraryEntryRecord.__tablename__ == "library_entries"
    engine = create_sqlite_engine(paths.database)
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    service = LibraryImportService(
        sessions=sessions,
        store=ContentStore(paths.library_root),
        clock=lambda: NOW,
    )
    first = service.import_pdf(
        PdfImportInput(
            work_id=None,
            title="Backup Evidence One",
            authors=("Alice",),
            doi="10.5555/backup.one",
            original_filename="one.pdf",
            rights_confirmed=True,
        ),
        (PDF_BYTES,),
    )
    second = None
    if shared:
        from paper_reader.discovery.identity import IdentityRepository, SourcePaper

        second_work = (
            IdentityRepository(sessions)
            .ingest(
                SourcePaper(
                    connector_id="arxiv",
                    external_id="2607.12345v1",
                    title="Backup Evidence Two",
                    authors=("Bob",),
                    doi="10.5555/backup.two",
                )
            )
            .work.id
        )
        second = service.import_pdf(
            PdfImportInput(
                work_id=second_work,
                title="Backup Evidence Two",
                authors=("Bob",),
                doi=None,
                original_filename="two.pdf",
                rights_confirmed=True,
            ),
            (PDF_BYTES,),
        )
    engine.dispose()
    return first, second


def _build(paths, destination: Path, **kwargs):  # type: ignore[no-untyped-def]
    from paper_reader.backups.archive import BackupArchiveBuilder

    return BackupArchiveBuilder(paths=paths, clock=lambda: NOW, **kwargs).create(
        destination=destination,
    )


def _object_path(paths, content_sha256: str) -> Path:  # type: ignore[no-untyped-def]
    return paths.objects / content_sha256[:2] / content_sha256[2:4] / f"{content_sha256}.pdf"


def test_sqlite_snapshot_captures_committed_wal_without_sidecars(tmp_path: Path) -> None:
    from paper_reader.backups.archive import create_sqlite_snapshot

    source_path = tmp_path / "source.sqlite3"
    destination = tmp_path / "snapshot.sqlite3"
    source = sqlite3.connect(source_path)
    try:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("PRAGMA wal_autocheckpoint=0")
        source.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, value TEXT)")
        source.execute("INSERT INTO evidence(value) VALUES ('first')")
        source.commit()
        source.execute("INSERT INTO evidence(value) VALUES ('from-wal')")
        source.commit()
        assert source_path.with_name(f"{source_path.name}-wal").exists()

        create_sqlite_snapshot(source_path, destination)
    finally:
        source.close()

    snapshot = sqlite3.connect(destination)
    try:
        assert snapshot.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert snapshot.execute("SELECT value FROM evidence ORDER BY id").fetchall() == [
            ("first",),
            ("from-wal",),
        ]
    finally:
        snapshot.close()
    assert not destination.with_name(f"{destination.name}-wal").exists()
    assert not destination.with_name(f"{destination.name}-shm").exists()


def test_sqlite_snapshot_cancellation_removes_partial_file(tmp_path: Path) -> None:
    from paper_reader.backups.archive import BackupCancelledError, create_sqlite_snapshot

    source_path = tmp_path / "large.sqlite3"
    source = sqlite3.connect(source_path)
    try:
        source.execute("CREATE TABLE payloads (value BLOB NOT NULL)")
        source.executemany(
            "INSERT INTO payloads(value) VALUES (?)",
            [(b"x" * 8192,) for _ in range(256)],
        )
        source.commit()
    finally:
        source.close()

    destination = tmp_path / "cancelled.sqlite3"
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 2

    with pytest.raises(BackupCancelledError):
        create_sqlite_snapshot(
            source_path,
            destination,
            cancelled=cancelled,
            pages_per_step=1,
        )

    assert not destination.exists()


def test_archive_contains_snapshot_manifest_and_one_shared_object(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    first, second = _seed_library(paths)
    destination = paths.backups / "manual.paper-reader-backup"

    result = _build(paths, destination)

    assert result.path == destination
    assert result.path.is_file()
    assert result.package_sha256 and len(result.package_sha256) == 64
    assert result.manifest_sha256 and len(result.manifest_sha256) == 64
    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
        object_names = [name for name in names if name.startswith("objects/")]
        assert names.count("database/paper-reader.sqlite3") == 1
        assert names.count("manifest.json") == 1
        assert len(object_names) == 1
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format_version"] == "paper-reader-backup-v1"
        assert manifest["app_version"] == __version__
        assert manifest["created_at"] == "2026-07-14T13:30:00Z"
        assert manifest["database"]["archive_path"] == "database/paper-reader.sqlite3"
        assert manifest["database"]["byte_size"] > 0
        assert len(manifest["database"]["sha256"]) == 64
        assert len(manifest["objects"]) == 1
        assert manifest["objects"][0]["content_sha256"] == first.content_sha256
        assert manifest["objects"][0]["archive_path"] == object_names[0]
        assert archive.read(object_names[0]) == PDF_BYTES
        assert manifest["included"] == sorted(
            [
                "complete local SQLite application state",
                "research plans, metadata and score evidence",
                "extracted and OCR paper text",
                "library state, annotations and reading progress",
                "analysis reports, complete prompts, failure diagnostics, "
                "conversations and preferences",
                "referenced lawful PDF objects",
            ]
        )
        assert manifest["excluded"] == sorted(
            [
                "operating-system credentials",
                "logs",
                "models",
                "vector and derived caches",
                "temporary files",
                "existing backups",
            ]
        )

        restored_database = tmp_path / "from-backup.sqlite3"
        restored_database.write_bytes(archive.read("database/paper-reader.sqlite3"))

    connection = sqlite3.connect(restored_database)
    try:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        rows = connection.execute(
            "SELECT work_id, content_sha256 FROM document_versions ORDER BY work_id"
        ).fetchall()
    finally:
        connection.close()
    assert second is not None
    assert len(rows) == 2
    assert {row[1] for row in rows} == {first.content_sha256}


@pytest.mark.skipif(os.name != "nt", reason="Windows legacy path headroom contract")
def test_archive_staging_name_does_not_repeat_a_long_destination(tmp_path: Path) -> None:
    template = tmp_path / "x" / "library" / "backups"
    padding = max(1, 132 - len(str(template)) + 1)
    paths = _paths(tmp_path / ("x" * padding))
    _seed_library(paths, shared=False)
    destination = paths.backups / (
        "paper-reader-manual-20260714T133000Z-"
        "00000000-0000-0000-0000-000000000000.paper-reader-backup"
    )
    legacy_partial = paths.backups / f".{destination.name}.{'a' * 32}.part"

    assert len(str(destination)) < 260
    assert len(str(legacy_partial)) >= 260

    result = _build(paths, destination)

    assert result.path == destination
    assert result.path.is_file()
    assert not tuple(paths.backups.glob("*.part"))


def test_archive_preserves_explicit_paper_update_state(tmp_path: Path) -> None:
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.paper_updates import PaperUpdateRepository, PaperUpdateState

    paths = _paths(tmp_path)
    _seed_library(paths, shared=False)
    engine = create_sqlite_engine(paths.database)
    sessions = create_session_factory(engine)
    try:
        identities = IdentityRepository(sessions)
        identities.ingest(
            SourcePaper(
                connector_id="arxiv",
                external_id="2607.41000v1",
                title="Backup paper update state",
                authors=("Alice",),
                abstract="Initial evidence.",
                updated_at=NOW,
            ),
            now=NOW,
        )
        important = identities.ingest(
            SourcePaper(
                connector_id="arxiv",
                external_id="2607.41000v1",
                title="Backup paper update state",
                authors=("Alice",),
                abstract="Revised evidence.",
                updated_at=NOW + timedelta(hours=1),
            ),
            now=NOW + timedelta(hours=1),
        )
        assert important.paper_update_id is not None
        PaperUpdateRepository(sessions).set_state(
            important.paper_update_id,
            PaperUpdateState.DISMISSED,
            now=NOW + timedelta(hours=2),
        )
    finally:
        engine.dispose()

    destination = paths.backups / "paper-update-state.paper-reader-backup"
    _build(paths, destination)
    with zipfile.ZipFile(destination) as archive:
        restored_database = tmp_path / "paper-update-state.sqlite3"
        restored_database.write_bytes(archive.read("database/paper-reader.sqlite3"))

    connection = sqlite3.connect(restored_database)
    try:
        row = connection.execute(
            "SELECT state, acted_at FROM paper_updates WHERE id = ?",
            (important.paper_update_id,),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert row[0] == "dismissed"
    assert row[1] is not None


def test_archive_excludes_cache_logs_old_backups_and_process_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    _seed_library(paths, shared=False)
    canaries = {
        "log": "CANARY-LOG-70669",
        "model": "CANARY-MODEL-70669",
        "old_backup": "CANARY-OLD-BACKUP-70669",
        "environment": "CANARY-ENV-70669",
        "credential": "CANARY-CREDENTIAL-70669",
    }
    (paths.logs / "local.log").write_text(canaries["log"], encoding="utf-8")
    (paths.models / "weights.bin").write_text(canaries["model"], encoding="utf-8")
    (paths.backups / "old.backup").write_text(canaries["old_backup"], encoding="utf-8")
    monkeypatch.setenv("PAPER_READER_TEST_SECRET", canaries["environment"])
    destination = paths.backups / "clean.paper-reader-backup"

    _build(paths, destination)

    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
        decoded_members = b"\n".join(archive.read(name) for name in names)
    assert all(not name.startswith(("logs/", "models/", "cache/", "backups/")) for name in names)
    for value in canaries.values():
        assert value.encode() not in decoded_members


@pytest.mark.parametrize("mutation", ["missing", "changed", "unsafe_path"])
def test_archive_rejects_invalid_referenced_objects_without_partial_output(
    tmp_path: Path,
    mutation: str,
) -> None:
    from paper_reader.backups.archive import BackupIntegrityError

    paths = _paths(tmp_path)
    first, _ = _seed_library(paths, shared=False)
    object_path = _object_path(paths, first.content_sha256)
    if mutation == "missing":
        object_path.unlink()
    elif mutation == "changed":
        object_path.write_bytes(PDF_BYTES + b"changed")
    else:
        connection = sqlite3.connect(paths.database)
        try:
            connection.execute(
                "UPDATE document_versions SET relative_path = '../escape.pdf' WHERE id = ?",
                (first.document_version_id,),
            )
            connection.commit()
        finally:
            connection.close()

    destination = paths.backups / f"{mutation}.paper-reader-backup"
    with pytest.raises(BackupIntegrityError):
        _build(paths, destination)

    assert not destination.exists()
    assert not tuple(paths.backups.glob("*.part"))
    assert not tuple(paths.backups.glob(".backup-stage-*"))


def test_archive_rejects_destination_outside_backup_root(tmp_path: Path) -> None:
    from paper_reader.backups.archive import UnsafeBackupPathError

    paths = _paths(tmp_path)
    _seed_library(paths, shared=False)

    with pytest.raises(UnsafeBackupPathError):
        _build(paths, tmp_path / "outside.paper-reader-backup")


def test_archive_checks_free_space_before_creating_partial_files(tmp_path: Path) -> None:
    from paper_reader.backups.archive import BackupInsufficientSpaceError

    paths = _paths(tmp_path)
    _seed_library(paths, shared=False)
    destination = paths.backups / "no-space.paper-reader-backup"

    with pytest.raises(BackupInsufficientSpaceError):
        _build(paths, destination, free_space=lambda _path: 0)

    assert not destination.exists()
    assert set(paths.backups.iterdir()) == set()


def test_archive_cancellation_cleans_snapshot_zip_and_stage(tmp_path: Path) -> None:
    from paper_reader.backups.archive import BackupArchiveBuilder, BackupCancelledError

    paths = _paths(tmp_path)
    _seed_library(paths, shared=False)
    destination = paths.backups / "cancelled.paper-reader-backup"
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(BackupCancelledError):
        BackupArchiveBuilder(paths=paths, clock=lambda: NOW).create(
            destination=destination,
            cancelled=cancelled,
        )

    assert not destination.exists()
    assert set(paths.backups.iterdir()) == set()


def test_archive_writer_failure_cleans_snapshot_zip_and_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    _seed_library(paths, shared=False)
    destination = paths.backups / "writer-failed.paper-reader-backup"

    def fail_copy(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError("injected archive writer failure")

    monkeypatch.setattr("paper_reader.backups.archive._copy_member", fail_copy)
    with pytest.raises(OSError, match="injected archive writer failure"):
        _build(paths, destination)

    assert not destination.exists()
    assert set(paths.backups.iterdir()) == set()


def test_archive_finalize_failure_cleans_then_same_destination_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_reader.backups.archive as archive_module

    paths = _paths(tmp_path)
    _seed_library(paths, shared=False)
    destination = paths.backups / "finalize-retry.paper-reader-backup"
    original_publish = archive_module._publish_without_overwrite

    def fail_publish(_source: Path, _destination: Path) -> None:
        raise OSError("injected backup finalize failure")

    monkeypatch.setattr(archive_module, "_publish_without_overwrite", fail_publish)
    with pytest.raises(OSError, match="injected backup finalize failure"):
        _build(paths, destination)

    assert not destination.exists()
    assert not tuple(paths.backups.glob("*.part"))
    assert not tuple(paths.backups.glob(".backup-stage-*"))

    monkeypatch.setattr(archive_module, "_publish_without_overwrite", original_publish)
    recovered = _build(paths, destination)

    assert recovered.path == destination
    assert recovered.package_sha256 == sha256(destination.read_bytes()).hexdigest()
    with zipfile.ZipFile(destination) as package:
        assert package.testzip() is None


def test_publish_conflict_never_overwrites_or_deletes_another_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    _seed_library(paths, shared=False)
    destination = paths.backups / "conflict.paper-reader-backup"
    other_bytes = b"another backup won the destination race"

    def conflict(_source: Path, target: Path) -> None:
        Path(target).write_bytes(other_bytes)
        raise FileExistsError("injected destination race")

    monkeypatch.setattr(
        "paper_reader.backups.archive._publish_without_overwrite",
        conflict,
    )
    with pytest.raises(FileExistsError, match="injected destination race"):
        _build(paths, destination)

    assert destination.read_bytes() == other_bytes
    assert not tuple(paths.backups.glob("*.part"))
    assert not tuple(paths.backups.glob(".backup-stage-*"))


def test_manifest_bytes_are_canonical_for_unicode_and_input_order() -> None:
    from paper_reader.backups.manifest import (
        BackupDatabaseMember,
        BackupManifest,
        BackupObjectMember,
    )

    database = BackupDatabaseMember(
        archive_path="database/paper-reader.sqlite3",
        byte_size=12,
        sha256="a" * 64,
    )
    first = BackupObjectMember(
        content_sha256="b" * 64,
        archive_path=f"objects/bb/bb/{'b' * 64}.pdf",
        byte_size=4,
        sha256="b" * 64,
    )
    second = BackupObjectMember(
        content_sha256="c" * 64,
        archive_path=f"objects/cc/cc/{'c' * 64}.pdf",
        byte_size=5,
        sha256="c" * 64,
    )
    common = {
        "format_version": "paper-reader-backup-v1",
        "app_version": "0.5.0",
        "schema_revision": "unversioned",
        "created_at": NOW,
        "database": database,
        "included": ("数据库", "合法 PDF"),
        "excluded": ("凭据", "日志"),
    }

    left = BackupManifest(objects=(second, first), **common)
    right = BackupManifest(objects=(first, second), **common)

    assert left.canonical_bytes() == right.canonical_bytes()
    assert left.sha256() == right.sha256()
    assert b"\\u6570" not in left.canonical_bytes()


def test_referenced_object_reader_fetches_in_bounded_batches(tmp_path: Path) -> None:
    from paper_reader.backups.archive import iter_referenced_objects

    database = tmp_path / "many.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            CREATE TABLE document_versions (
                content_sha256 TEXT,
                relative_path TEXT,
                byte_size INTEGER,
                media_type TEXT,
                access_status TEXT
            )
            """
        )
        rows = [
            (
                f"{index:064x}",
                f"objects/{index:064x}"[:11] + f"/{index:064x}.pdf",
                index + 1,
                "application/pdf",
                "user_provided",
            )
            for index in range(2_049)
        ]
        connection.executemany(
            "INSERT INTO document_versions VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()

        iterator = iter_referenced_objects(connection, batch_size=128)
        assert iter(iterator) is iterator
        result = list(iterator)
    finally:
        connection.close()

    assert len(result) == 2_049
    assert result[0].content_sha256 == f"{0:064x}"
    assert result[-1].content_sha256 == f"{2_048:064x}"


def test_referenced_object_reader_deduplicates_compatible_access_statuses(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.archive import iter_referenced_objects

    content_sha256 = "d" * 64
    relative_path = f"objects/dd/dd/{content_sha256}.pdf"
    connection = sqlite3.connect(tmp_path / "shared.sqlite3")
    try:
        connection.execute(
            """
            CREATE TABLE document_versions (
                content_sha256 TEXT,
                relative_path TEXT,
                byte_size INTEGER,
                media_type TEXT,
                access_status TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO document_versions VALUES (?, ?, ?, ?, ?)",
            [
                (content_sha256, relative_path, 42, "application/pdf", "open"),
                (
                    content_sha256,
                    relative_path,
                    42,
                    "application/pdf",
                    "user_provided",
                ),
            ],
        )

        result = list(iter_referenced_objects(connection, batch_size=1))
    finally:
        connection.close()

    assert len(result) == 1
    assert result[0].content_sha256 == content_sha256


def test_archive_rejects_symlinked_object_when_supported(tmp_path: Path) -> None:
    from paper_reader.backups.archive import BackupIntegrityError

    paths = _paths(tmp_path)
    first, _ = _seed_library(paths, shared=False)
    object_path = _object_path(paths, first.content_sha256)
    real_path = tmp_path / "real.pdf"
    object_path.replace(real_path)
    try:
        os.symlink(real_path, object_path)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")

    destination = paths.backups / "linked.paper-reader-backup"
    with pytest.raises(BackupIntegrityError):
        _build(paths, destination)
    assert not destination.exists()
