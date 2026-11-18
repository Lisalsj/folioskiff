from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

NOW = datetime(2026, 7, 14, 14, tzinfo=UTC)


def _revision(database: Path) -> str:
    connection = sqlite3.connect(database)
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        connection.close()
    assert row is not None
    return str(row[0])


def test_new_and_current_databases_do_not_create_migration_snapshots(tmp_path: Path) -> None:
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "state" / "paper-reader.sqlite3"
    snapshot_root = tmp_path / "state" / "migration-snapshots"

    upgrade_database(database, snapshot_root=snapshot_root, snapshot_clock=lambda: NOW)
    upgrade_database(database, snapshot_root=snapshot_root, snapshot_clock=lambda: NOW)

    assert _revision(database) == "0038_remove_legacy_conversations"
    assert not snapshot_root.exists() or set(snapshot_root.iterdir()) == set()


def test_failed_upgrade_keeps_verified_pre_migration_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "state" / "paper-reader.sqlite3"
    snapshot_root = tmp_path / "state" / "migration-snapshots"
    upgrade_database(database, snapshot_root=snapshot_root, snapshot_clock=lambda: NOW)
    connection = sqlite3.connect(database)
    try:
        connection.execute("UPDATE alembic_version SET version_num = '0021_paper_conversations'")
        connection.execute("CREATE TABLE migration_canary(value TEXT NOT NULL)")
        connection.execute("INSERT INTO migration_canary VALUES ('before-upgrade')")
        connection.commit()
    finally:
        connection.close()

    def fail_upgrade(*_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr("paper_reader.migrations.command.upgrade", fail_upgrade)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        upgrade_database(database, snapshot_root=snapshot_root, snapshot_clock=lambda: NOW)

    manifests = tuple(snapshot_root.glob("*.manifest.json"))
    snapshots = tuple(snapshot_root.glob("*.sqlite3"))
    assert len(manifests) == len(snapshots) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["format_version"] == "paper-reader-migration-snapshot-v1"
    assert manifest["source_revision"] == "0021_paper_conversations"
    assert manifest["target_revision"] == "0038_remove_legacy_conversations"
    assert manifest["database_sha256"] and len(manifest["database_sha256"]) == 64
    assert manifest["database_byte_size"] == snapshots[0].stat().st_size
    snapshot = sqlite3.connect(snapshots[0])
    try:
        assert snapshot.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert snapshot.execute("SELECT value FROM migration_canary").fetchone() == (
            "before-upgrade",
        )
    finally:
        snapshot.close()


def test_successful_upgrade_prunes_only_old_snapshot_pairs(tmp_path: Path) -> None:
    from paper_reader.backups.startup import MigrationSnapshotManager

    database = tmp_path / "paper-reader.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE evidence(value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('stable')")
        connection.commit()
    finally:
        connection.close()
    snapshot_root = tmp_path / "migration-snapshots"
    clocks = iter(NOW + timedelta(minutes=index) for index in range(6))
    manager = MigrationSnapshotManager(
        database=database,
        snapshot_root=snapshot_root,
        clock=lambda: next(clocks),
    )

    for index in range(6):
        manager.create(
            source_revision=f"old-{index}",
            target_revision=f"new-{index}",
        )
    unrelated = snapshot_root / "user-file.txt"
    unrelated.write_text("do not delete", encoding="utf-8")

    manager.prune(keep=4)

    manifests = sorted(snapshot_root.glob("*.manifest.json"))
    snapshots = sorted(snapshot_root.glob("*.sqlite3"))
    assert len(manifests) == len(snapshots) == 4
    assert unrelated.read_text(encoding="utf-8") == "do not delete"
    sources = {
        json.loads(manifest.read_text(encoding="utf-8"))["source_revision"]
        for manifest in manifests
    }
    assert sources == {"old-2", "old-3", "old-4", "old-5"}


@pytest.mark.parametrize("failure_call", (1, 2))
def test_migration_snapshot_failure_leaves_no_partial_and_retry_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
) -> None:
    import paper_reader.backups.startup as startup
    from paper_reader.backups.startup import MigrationSnapshotManager

    database = tmp_path / "paper-reader.sqlite3"
    sqlite3.connect(database).close()
    root = tmp_path / "migration-snapshots"
    manager = MigrationSnapshotManager(
        database=database,
        snapshot_root=root,
        clock=lambda: NOW,
    )

    original_replace = startup.os.replace
    calls = 0

    def fail_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("injected publish failure")
        original_replace(source, destination)

    monkeypatch.setattr(startup.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected publish failure"):
        manager.create(source_revision="old", target_revision="new")

    assert not tuple(root.glob("*.sqlite3"))
    assert not tuple(root.glob("*.manifest.json"))
    assert not tuple(root.glob("*.part"))

    monkeypatch.setattr(startup.os, "replace", original_replace)
    recovered = manager.create(source_revision="old", target_revision="new")

    assert recovered.database_path.is_file()
    assert recovered.manifest_path.is_file()
    assert len(tuple(root.glob("*.sqlite3"))) == 1
    assert len(tuple(root.glob("*.manifest.json"))) == 1
    assert not tuple(root.glob("*.part"))


def test_settings_expose_dedicated_migration_snapshot_root(tmp_path: Path) -> None:
    from paper_reader.settings import AppPaths

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )

    assert paths.migration_snapshots == paths.state_root / "migration-snapshots"
    paths.ensure()
    assert paths.migration_snapshots.is_dir()
