"""Programmatic Alembic migration entry point."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from paper_reader.backups.startup import MigrationSnapshotManager
from paper_reader.packaging.resources import discover_runtime_resources


def _migration_root() -> Path:
    return discover_runtime_resources().migrations_root


def _configuration(database_path: Path) -> Config:
    configuration = Config()
    configuration.set_main_option("script_location", str(_migration_root()))
    configuration.set_main_option(
        "sqlalchemy.url",
        f"sqlite+pysqlite:///{database_path.as_posix()}",
    )
    return configuration


def migration_head_revision() -> str:
    configuration = Config()
    configuration.set_main_option("script_location", str(_migration_root()))
    heads = ScriptDirectory.from_config(configuration).get_heads()
    if len(heads) != 1:
        raise RuntimeError("paper-reader requires exactly one migration head")
    return heads[0]


def upgrade_database(
    database_path: Path,
    *,
    snapshot_root: Path | None = None,
    snapshot_clock: Callable[[], datetime] | None = None,
) -> None:
    """Upgrade a local SQLite database to the latest application schema."""
    path = Path(database_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    root = (
        Path(snapshot_root).resolve()
        if snapshot_root is not None
        else path.parent / "migration-snapshots"
    )
    if snapshot_clock is None:
        manager = MigrationSnapshotManager(database=path, snapshot_root=root)
    else:
        manager = MigrationSnapshotManager(
            database=path,
            snapshot_root=root,
            clock=snapshot_clock,
        )
    manager.prepare(target_revision=migration_head_revision())
    command.upgrade(_configuration(path), "head")
    manager.prune(keep=4)
