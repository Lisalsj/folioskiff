from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from paper_reader.runtime_config import RuntimeConfigStore, RuntimeConfigUpdate
from paper_reader.runtime_migration import (
    DataRootMigrationError,
    complete_data_root_migration,
    migrate_data_root,
    pending_data_root,
)
from paper_reader.settings import AppPaths


def _legacy_layout(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    state = tmp_path / "legacy-state"
    cache = tmp_path / "legacy-cache"
    library = tmp_path / "legacy-library"
    target = tmp_path / "new-root"
    state.mkdir()
    cache.mkdir()
    library.mkdir()
    RuntimeConfigStore(state).save(
        RuntimeConfigUpdate(cache_root=cache, library_root=library)
    )
    return state, cache, library, target


def test_data_root_migration_moves_legacy_roots_and_leaves_redirect(tmp_path: Path) -> None:
    state, cache, library, target_root = _legacy_layout(tmp_path)
    connection = sqlite3.connect(state / "paper-reader.sqlite3")
    try:
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES ('db')")
        connection.commit()
    finally:
        connection.close()
    (cache / "model.bin").write_text("cache", encoding="utf-8")
    (library / "paper.pdf").write_text("paper", encoding="utf-8")

    migrate_data_root(source_state=state, target=AppPaths.from_data_root(target_root))

    target = AppPaths.from_data_root(target_root)
    # The bootstrap phase leaves a journal until the restarted application has
    # opened and health-checked the migrated database.
    complete_data_root_migration(target)
    connection = sqlite3.connect(target.state_root / "paper-reader.sqlite3")
    try:
        assert connection.execute("SELECT value FROM marker").fetchone() == ("db",)
    finally:
        connection.close()
    assert (target.cache_root / "model.bin").read_text(encoding="utf-8") == "cache"
    assert (target.library_root / "paper.pdf").read_text(encoding="utf-8") == "paper"
    assert not cache.exists()
    assert not library.exists()
    assert (state / "runtime-redirect.json").read_text(encoding="utf-8")
    assert not (target.state_root / ".data-root-migration.json").exists()


def test_data_root_migration_handles_old_library_equal_to_target_root(tmp_path: Path) -> None:
    state = tmp_path / "legacy-state"
    cache = tmp_path / "legacy-cache"
    target_root = tmp_path / "legacy-library"
    state.mkdir()
    cache.mkdir()
    target_root.mkdir()
    RuntimeConfigStore(state).save(
        RuntimeConfigUpdate(cache_root=cache, library_root=target_root)
    )
    (target_root / "paper.pdf").write_text("paper", encoding="utf-8")
    connection = sqlite3.connect(state / "paper-reader.sqlite3")
    connection.close()

    migrate_data_root(source_state=state, target=AppPaths.from_data_root(target_root))

    target = AppPaths.from_data_root(target_root)
    complete_data_root_migration(target)
    assert (target.library_root / "paper.pdf").read_text(encoding="utf-8") == "paper"
    assert (target.state_root / "runtime-config.json").exists()


def test_data_root_migration_accepts_windows_default_nested_cache_layout(
    tmp_path: Path,
) -> None:
    state = tmp_path / "legacy-state"
    cache = state / "Cache"
    target_root = tmp_path / "legacy-library"
    state.mkdir()
    cache.mkdir()
    target_root.mkdir()
    RuntimeConfigStore(state).save(
        RuntimeConfigUpdate(cache_root=cache, library_root=target_root)
    )
    (cache / "models").mkdir()
    (cache / "models" / "model.bin").write_bytes(b"cache")
    (state / "logs").mkdir()
    (state / "logs" / "app.log").write_text("log", encoding="utf-8")
    (target_root / "objects").mkdir()
    connection = sqlite3.connect(state / "paper-reader.sqlite3")
    connection.close()

    migrate_data_root(source_state=state, target=AppPaths.from_data_root(target_root))
    target = AppPaths.from_data_root(target_root)
    complete_data_root_migration(target)

    assert (target.cache_root / "models" / "model.bin").read_bytes() == b"cache"
    assert (target.state_root / "logs" / "app.log").read_text(encoding="utf-8") == "log"
    assert not (target.state_root / "Cache").exists()
    assert not cache.exists()


def test_data_root_migration_rolls_back_when_library_conflicts(tmp_path: Path) -> None:
    state, cache, library, target_root = _legacy_layout(tmp_path)
    (cache / "model.bin").write_text("cache", encoding="utf-8")
    (library / "paper.pdf").write_text("old", encoding="utf-8")
    target = AppPaths.from_data_root(target_root)
    target.library_root.mkdir(parents=True)
    (target.library_root / "paper.pdf").write_text("new", encoding="utf-8")

    with pytest.raises(DataRootMigrationError):
        migrate_data_root(source_state=state, target=target)

    assert (cache / "model.bin").read_text(encoding="utf-8") == "cache"
    assert (library / "paper.pdf").read_text(encoding="utf-8") == "old"
    assert not (target.cache_root / "model.bin").exists()


def test_interrupted_migration_is_rolled_back_from_the_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, cache, library, target_root = _legacy_layout(tmp_path)
    (cache / "model.bin").write_text("cache", encoding="utf-8")
    (library / "paper.pdf").write_text("paper", encoding="utf-8")
    target = AppPaths.from_data_root(target_root)

    import paper_reader.runtime_migration as migration

    original_transfer = migration._transfer
    calls = 0

    def interrupt_after_first(item: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        original_transfer(item)  # type: ignore[arg-type]

    monkeypatch.setattr(migration, "_transfer", interrupt_after_first)
    with pytest.raises(KeyboardInterrupt):
        migrate_data_root(source_state=state, target=target)

    assert (state / ".data-root-migration.json").exists()
    monkeypatch.setattr(migration, "_transfer", original_transfer)
    assert pending_data_root(state) == target_root
    assert (cache / "model.bin").read_text(encoding="utf-8") == "cache"
    assert (library / "paper.pdf").read_text(encoding="utf-8") == "paper"
    assert not (target.cache_root / "model.bin").exists()
    assert not (state / ".data-root-migration.json").exists()


def test_failed_post_restart_health_check_restores_the_legacy_layout(tmp_path: Path) -> None:
    state, cache, library, target_root = _legacy_layout(tmp_path)
    (state / "paper-reader.sqlite3").write_text("not sqlite", encoding="utf-8")
    (cache / "model.bin").write_text("cache", encoding="utf-8")
    target = AppPaths.from_data_root(target_root)

    migrate_data_root(source_state=state, target=target)
    with pytest.raises(DataRootMigrationError, match="健康检查"):
        complete_data_root_migration(target)

    assert (state / "paper-reader.sqlite3").read_text(encoding="utf-8") == "not sqlite"
    assert (cache / "model.bin").read_text(encoding="utf-8") == "cache"
    assert not (target.cache_root / "model.bin").exists()


def test_startup_can_finish_when_journal_mirror_was_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, cache, library, target_root = _legacy_layout(tmp_path)
    connection = sqlite3.connect(state / "paper-reader.sqlite3")
    connection.close()
    target = AppPaths.from_data_root(target_root)
    monkeypatch.setattr(AppPaths, "bootstrap_state_root", classmethod(lambda cls: state))

    migrate_data_root(source_state=state, target=target)
    (target.state_root / ".data-root-migration.json").unlink()
    complete_data_root_migration(target)

    assert not (state / ".data-root-migration.json").exists()
    assert (target.state_root / "paper-reader.sqlite3").exists()
