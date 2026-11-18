from __future__ import annotations

from pathlib import Path


def test_sqlite_engine_enables_wal_and_foreign_keys(tmp_path: Path) -> None:
    from paper_reader.database import create_sqlite_engine

    engine = create_sqlite_engine(tmp_path / "state.sqlite3")

    with engine.connect() as connection:
        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
        foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()

    assert str(journal_mode).lower() == "wal"
    assert foreign_keys == 1
    engine.dispose()
