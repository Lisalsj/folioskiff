from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

NOW = datetime(2026, 7, 14, 14, 30, tzinfo=UTC)
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


def _paths(tmp_path: Path, name: str):  # type: ignore[no-untyped-def]
    from paper_reader.settings import AppPaths

    paths = AppPaths.from_overrides(
        state_root=tmp_path / name / "state",
        cache_root=tmp_path / name / "cache",
        library_root=tmp_path / name / "library",
    )
    paths.ensure()
    return paths


def _seed_source_backup(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.backups.archive import BackupArchiveBuilder
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.migrations import upgrade_database
    from paper_reader.reader.imports import LibraryImportService, PdfImportInput
    from paper_reader.reader.storage import ContentStore

    paths = _paths(tmp_path, "source")
    upgrade_database(paths.database, snapshot_root=paths.migration_snapshots)
    connection = sqlite3.connect(paths.database)
    try:
        connection.execute("CREATE TABLE restored_gold_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO restored_gold_marker VALUES ('from-backup')")
        connection.commit()
    finally:
        connection.close()
    engine = create_sqlite_engine(paths.database)
    try:
        sessions = create_session_factory(engine)
        imported = LibraryImportService(
            sessions=sessions,
            store=ContentStore(paths.library_root),
            clock=lambda: NOW,
        ).import_pdf(
            PdfImportInput(
                work_id=None,
                title="Startup restore evidence",
                authors=("Ada Researcher",),
                doi="10.5555/startup.restore",
                original_filename="restore.pdf",
                rights_confirmed=True,
            ),
            (PDF_BYTES,),
        )
        jobs = JobRepository(sessions)
        for index, kind in enumerate(
            (
                "discovery.connector",
                "analysis.generate_report",
                "reader.ocr",
                "plugin.invoke",
                "publishing.git",
            )
        ):
            job = jobs.create(kind=kind, payload={"canary": index})
            if index % 2:
                jobs.update_state(job.id, status="running", progress=30)
    finally:
        engine.dispose()
    package = paths.backups / "gold.paper-reader-backup"
    BackupArchiveBuilder(paths=paths, clock=lambda: NOW).create(destination=package)
    return paths, package, imported


def _seed_current_target(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.migrations import upgrade_database

    paths = _paths(tmp_path, "target")
    upgrade_database(paths.database, snapshot_root=paths.migration_snapshots)
    connection = sqlite3.connect(paths.database)
    try:
        connection.execute("CREATE TABLE current_gold_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO current_gold_marker VALUES ('keep-on-failure')")
        connection.commit()
    finally:
        connection.close()
    old_object = paths.objects / "current-only.pdf"
    old_object.write_bytes(b"CURRENT-OBJECT-CANARY")
    cache_canary = paths.cache_root / "machine-model-cache.bin"
    cache_canary.write_bytes(b"MACHINE-CACHE-CANARY")
    return paths, old_object, cache_canary


def _prepare_pending(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.backups.restore import (
        RestorePackageValidator,
        schedule_pending_restore,
        staged_restore_fingerprint,
    )

    _source, package, imported = _seed_source_backup(tmp_path)
    paths, old_object, cache_canary = _seed_current_target(tmp_path)
    staged = RestorePackageValidator(paths=paths).validate(package)
    fingerprint = staged_restore_fingerprint(paths=paths, package=staged)
    pending = schedule_pending_restore(
        paths=paths,
        package=staged,
        restore_id="11111111-2222-4333-8444-555555555555",
        expected_fingerprint=fingerprint,
        confirmed_at=NOW,
    )
    return paths, pending, imported, old_object, cache_canary


def _migrate(database: Path, snapshot_root: Path) -> None:
    from paper_reader.migrations import upgrade_database

    upgrade_database(database, snapshot_root=snapshot_root)


def _table_exists(database: Path, table: str) -> bool:
    connection = sqlite3.connect(database)
    try:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            is not None
        )
    finally:
        connection.close()


def _receipt_rows(database: Path) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(
            "SELECT package_sha256, source_format_version, source_app_version, "
            "source_schema_revision, object_count, migrated_to_revision "
            "FROM restore_receipts"
        ).fetchall()
    finally:
        connection.close()


def _assert_old_state(paths, old_object: Path, cache_canary: Path) -> None:  # type: ignore[no-untyped-def]
    assert _table_exists(paths.database, "current_gold_marker")
    assert not _table_exists(paths.database, "restored_gold_marker")
    assert old_object.read_bytes() == b"CURRENT-OBJECT-CANARY"
    assert cache_canary.read_bytes() == b"MACHINE-CACHE-CANARY"


def _assert_restored_state(
    paths,  # type: ignore[no-untyped-def]
    imported,  # type: ignore[no-untyped-def]
    cache_canary: Path,
) -> None:
    assert _table_exists(paths.database, "restored_gold_marker")
    assert not _table_exists(paths.database, "current_gold_marker")
    restored_pdf = (
        paths.objects
        / imported.content_sha256[:2]
        / imported.content_sha256[2:4]
        / f"{imported.content_sha256}.pdf"
    )
    assert restored_pdf.read_bytes() == PDF_BYTES
    assert cache_canary.read_bytes() == b"MACHINE-CACHE-CANARY"


def test_confirmed_restore_waits_for_restart_then_commits_with_path_free_receipt(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.startup import RestoreStartupManager

    paths, pending, imported, old_object, cache_canary = _prepare_pending(tmp_path)
    _assert_old_state(paths, old_object, cache_canary)
    assert (paths.state_root / "pending-restore.json").is_file()
    assert not (paths.state_root / "restore-journal.json").exists()

    outcome = RestoreStartupManager(paths=paths, clock=lambda: NOW).apply_pending(migrate=_migrate)

    assert outcome is not None
    assert outcome.restore_id == pending.restore_id
    assert outcome.package_sha256 == pending.package_sha256
    _assert_restored_state(paths, imported, cache_canary)
    receipts = _receipt_rows(paths.database)
    assert len(receipts) == 1
    serialized = json.dumps(receipts)
    assert pending.package_sha256 in serialized
    assert str(paths.state_root) not in serialized
    assert str(paths.library_root) not in serialized
    assert not (paths.state_root / "pending-restore.json").exists()
    assert not (paths.state_root / "restore-journal.json").exists()
    assert not tuple(paths.state_root.glob(".restore-transaction-*"))
    assert not tuple(paths.library_root.glob(".restore-transaction-*"))
    assert not tuple(paths.state_root.glob(".restore-stage-*"))
    assert not tuple(paths.library_root.glob(".restore-stage-*"))

    connection = sqlite3.connect(paths.database)
    try:
        statuses = connection.execute("SELECT status FROM jobs ORDER BY id").fetchall()
    finally:
        connection.close()
    assert statuses == [("restored_interrupted",)] * 5


def test_restore_accepts_default_nested_cache_layout_without_overwriting_cache(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from paper_reader.backups.startup import RestoreStartupManager

    paths, _pending, imported, _old_object, _old_cache_canary = _prepare_pending(tmp_path)
    default_layout = replace(paths, cache_root=paths.state_root / "Cache")
    default_layout.cache_root.mkdir()
    cache_canary = default_layout.cache_root / "machine-cache.bin"
    cache_canary.write_bytes(b"DEFAULT-WINDOWS-CACHE")

    outcome = RestoreStartupManager(
        paths=default_layout,
        clock=lambda: NOW,
    ).apply_pending(migrate=_migrate)

    assert outcome is not None
    assert _table_exists(default_layout.database, "restored_gold_marker")
    assert cache_canary.read_bytes() == b"DEFAULT-WINDOWS-CACHE"
    restored_pdf = (
        default_layout.objects
        / imported.content_sha256[:2]
        / imported.content_sha256[2:4]
        / f"{imported.content_sha256}.pdf"
    )
    assert restored_pdf.read_bytes() == PDF_BYTES


def test_restore_rejects_cache_inside_replaceable_object_tree(tmp_path: Path) -> None:
    from dataclasses import replace

    from paper_reader.backups.startup import RestoreStartupError, RestoreStartupManager

    paths, _pending, _imported, old_object, cache_canary = _prepare_pending(tmp_path)
    unsafe_cache = paths.objects / "machine-cache"
    unsafe_cache.mkdir()
    unsafe_layout = replace(paths, cache_root=unsafe_cache)

    with pytest.raises(RestoreStartupError) as captured:
        RestoreStartupManager(paths=unsafe_layout, clock=lambda: NOW).apply_pending(
            migrate=_migrate
        )

    assert captured.value.code == "invalid_pending_restore"
    _assert_old_state(paths, old_object, cache_canary)


@pytest.mark.parametrize(
    "failed_phase",
    [
        "database_rollback",
        "objects_rollback",
        "database_publish",
        "objects_publish",
        "quick_check",
        "migration",
    ],
)
def test_every_precommit_failure_restores_old_database_and_objects_then_can_retry(
    tmp_path: Path,
    failed_phase: str,
) -> None:
    from paper_reader.backups.startup import RestoreStartupError, RestoreStartupManager

    paths, _pending, imported, old_object, cache_canary = _prepare_pending(tmp_path)
    injected = False

    def fault(phase: str) -> None:
        nonlocal injected
        if phase == failed_phase and not injected:
            injected = True
            raise OSError("injected startup restore failure with private path D:/secret")

    with pytest.raises(RestoreStartupError) as captured:
        RestoreStartupManager(
            paths=paths,
            clock=lambda: NOW,
            fault=fault,
        ).apply_pending(migrate=_migrate)

    assert captured.value.code == "restore_deferred"
    assert "secret" not in str(captured.value).casefold()
    _assert_old_state(paths, old_object, cache_canary)
    assert (paths.state_root / "pending-restore.json").is_file()
    assert not (paths.state_root / "restore-journal.json").exists()
    assert not tuple(paths.state_root.glob(".restore-transaction-*"))
    assert not tuple(paths.library_root.glob(".restore-transaction-*"))

    outcome = RestoreStartupManager(paths=paths, clock=lambda: NOW).apply_pending(
        migrate=_migrate
    )
    assert outcome is not None
    _assert_restored_state(paths, imported, cache_canary)


@pytest.mark.skipif(os.name != "nt", reason="Windows transient sharing contract")
def test_restore_retries_transient_windows_journal_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.backups import startup
    from paper_reader.backups.startup import RestoreStartupManager

    class TransientPermissionError(PermissionError):
        winerror = 5

    paths, _pending, imported, _old_object, cache_canary = _prepare_pending(tmp_path)
    journal = paths.state_root / "restore-journal.json"
    original_replace = startup.os.replace
    transient_failures = 0

    def replace(source: object, destination: object) -> None:
        nonlocal transient_failures
        source_path = Path(source)  # type: ignore[arg-type]
        destination_path = Path(destination)  # type: ignore[arg-type]
        if destination_path == journal and transient_failures < 2:
            transient_failures += 1
            raise TransientPermissionError("injected scanner sharing race")
        original_replace(source_path, destination_path)

    monkeypatch.setattr(startup.os, "replace", replace)

    outcome = RestoreStartupManager(paths=paths, clock=lambda: NOW).apply_pending(
        migrate=_migrate
    )

    assert transient_failures == 2
    assert outcome is not None
    _assert_restored_state(paths, imported, cache_canary)


@pytest.mark.skipif(os.name != "nt", reason="Windows transient sharing contract")
@pytest.mark.parametrize("winerror", [32, 87])
def test_restore_journal_retry_is_bounded_or_rejects_nontransient_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    winerror: int,
) -> None:
    from paper_reader.backups import startup

    class ReplacePermissionError(PermissionError):
        pass

    ReplacePermissionError.winerror = winerror  # type: ignore[attr-defined]
    source = tmp_path / "journal.part"
    destination = tmp_path / "restore-journal.json"
    calls = 0

    def replace(_source: Path, _destination: Path) -> None:
        nonlocal calls
        calls += 1
        raise ReplacePermissionError("injected replacement failure")

    times = iter((10.0, 11.0))
    monkeypatch.setattr(startup.os, "replace", replace)
    monkeypatch.setattr(startup, "monotonic", lambda: next(times))
    monkeypatch.setattr(startup, "sleep", lambda _seconds: pytest.fail("unexpected sleep"))

    with pytest.raises(ReplacePermissionError):
        startup._replace_journal_file(source, destination)

    assert calls == 1


def test_cleanup_failure_keeps_committed_journal_and_next_start_finishes_once(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.startup import RestoreStartupError, RestoreStartupManager

    paths, _pending, imported, _old_object, cache_canary = _prepare_pending(tmp_path)
    injected = False

    def fault(phase: str) -> None:
        nonlocal injected
        if phase == "cleanup" and not injected:
            injected = True
            raise OSError("injected cleanup failure")

    with pytest.raises(RestoreStartupError) as captured:
        RestoreStartupManager(
            paths=paths,
            clock=lambda: NOW,
            fault=fault,
        ).apply_pending(migrate=_migrate)
    assert captured.value.code == "cleanup_deferred"
    _assert_restored_state(paths, imported, cache_canary)
    journal = json.loads((paths.state_root / "restore-journal.json").read_text())
    assert journal["phase"] == "committed"
    assert len(_receipt_rows(paths.database)) == 1

    outcome = RestoreStartupManager(paths=paths, clock=lambda: NOW).apply_pending(migrate=_migrate)
    assert outcome is not None and outcome.recovered_committed is True
    assert len(_receipt_rows(paths.database)) == 1
    assert not (paths.state_root / "pending-restore.json").exists()
    assert not (paths.state_root / "restore-journal.json").exists()


def test_real_app_restart_recovers_crash_window_with_missing_database_and_objects(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.backups.startup import (
        RestoreJournal,
        _transaction_paths,
        _write_journal,
    )

    paths, pending, imported, _old_object, cache_canary = _prepare_pending(tmp_path)
    transaction = _transaction_paths(paths, pending.restore_id)
    transaction["state_transaction"].mkdir()
    transaction["library_transaction"].mkdir()
    wal = paths.database.with_name(f"{paths.database.name}-wal")
    shm = paths.database.with_name(f"{paths.database.name}-shm")
    had_wal = wal.exists()
    had_shm = shm.exists()
    os.replace(paths.database, transaction["old_database"])
    if had_wal:
        os.replace(wal, transaction["old_database_wal"])
    if had_shm:
        os.replace(shm, transaction["old_database_shm"])
    os.replace(paths.objects, transaction["old_objects"])
    _write_journal(
        paths,
        RestoreJournal(
            pending=pending,
            phase="objects_rolled_back",
            started_at=NOW,
            had_database=True,
            had_database_wal=had_wal,
            had_database_shm=had_shm,
            had_objects=True,
        ),
    )
    assert not paths.database.exists()
    assert not paths.objects.exists()

    app = create_app(paths=paths, bootstrap_token="crash-recovery")
    try:
        _assert_restored_state(paths, imported, cache_canary)
        assert not (paths.state_root / "restore-journal.json").exists()
        assert not tuple(paths.state_root.glob(".restore-transaction-*"))
        assert not tuple(paths.library_root.glob(".restore-transaction-*"))
    finally:
        app.state.engine.dispose()


def test_database_rollback_preserves_old_wal_when_sidecar_rename_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.backups import startup
    from paper_reader.backups.startup import RestoreStartupError, RestoreStartupManager

    paths, _pending, imported, old_object, cache_canary = _prepare_pending(tmp_path)
    wal = paths.database.with_name(f"{paths.database.name}-wal")
    wal.write_bytes(b"")
    original_replace = startup.os.replace
    injected = False

    def fail_old_wal_rename(source: Path, destination: Path) -> None:
        nonlocal injected
        if Path(source) == wal and not injected:
            injected = True
            raise OSError("injected sidecar rename failure")
        original_replace(source, destination)

    monkeypatch.setattr(startup.os, "replace", fail_old_wal_rename)
    with pytest.raises(RestoreStartupError) as captured:
        RestoreStartupManager(paths=paths, clock=lambda: NOW).apply_pending(migrate=_migrate)

    assert captured.value.code == "restore_deferred"
    _assert_old_state(paths, old_object, cache_canary)
    outcome = RestoreStartupManager(paths=paths, clock=lambda: NOW).apply_pending(migrate=_migrate)
    assert outcome is not None
    _assert_restored_state(paths, imported, cache_canary)


@pytest.mark.parametrize(
    "mutation",
    ["outside_path", "case_alias", "stage_content", "wrong_volume"],
)
def test_startup_rejects_path_alias_replacement_content_change_or_wrong_volume_before_rename(
    tmp_path: Path,
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.backups import startup
    from paper_reader.backups.manifest import canonical_json_bytes
    from paper_reader.backups.startup import RestoreStartupError, RestoreStartupManager

    paths, pending, _imported, old_object, cache_canary = _prepare_pending(tmp_path)
    pending_path = paths.state_root / "pending-restore.json"
    if mutation in {"outside_path", "case_alias"}:
        payload = json.loads(pending_path.read_text(encoding="utf-8"))
        if mutation == "outside_path":
            payload["state_stage_root"] = str((tmp_path / "outside").resolve())
        else:
            original = str(payload["state_stage_root"])
            payload["state_stage_root"] = original.swapcase()
        pending_path.write_bytes(canonical_json_bytes(payload))
    elif mutation == "stage_content":
        Path(pending.staged_database).write_bytes(b"changed after confirmation")
    else:
        monkeypatch.setattr(startup, "_same_volume", lambda _left, _right: False)

    with pytest.raises(RestoreStartupError) as captured:
        RestoreStartupManager(paths=paths, clock=lambda: NOW).apply_pending(migrate=_migrate)

    assert captured.value.code == "invalid_pending_restore"
    _assert_old_state(paths, old_object, cache_canary)
    assert not (paths.state_root / "restore-journal.json").exists()


def test_startup_rejects_link_replaced_stage_when_supported(tmp_path: Path) -> None:
    from paper_reader.backups.startup import RestoreStartupError, RestoreStartupManager

    probe_target = tmp_path / "directory-link-probe-target"
    probe_link = tmp_path / "directory-link-probe"
    probe_target.mkdir()
    try:
        os.symlink(probe_target, probe_link, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create directory symbolic links")
    else:
        probe_link.unlink()
        probe_target.rmdir()

    paths, pending, _imported, old_object, cache_canary = _prepare_pending(tmp_path)
    stage = Path(pending.library_stage_root)
    real_stage = stage.with_name(f"{stage.name}-real")
    stage.rename(real_stage)
    os.symlink(real_stage, stage, target_is_directory=True)

    with pytest.raises(RestoreStartupError) as captured:
        RestoreStartupManager(paths=paths, clock=lambda: NOW).apply_pending(migrate=_migrate)
    assert captured.value.code == "invalid_pending_restore"
    _assert_old_state(paths, old_object, cache_canary)
