from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from paper_reader.backups.startup import current_schema_revision
from paper_reader.migrations import migration_head_revision, upgrade_database
from paper_reader.onboarding.reset import (
    DataResetError,
    DataResetService,
    apply_pending_data_reset,
)
from paper_reader.settings import AppPaths


def _paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    paths.ensure()
    return paths


def _seeded_paths(tmp_path: Path) -> AppPaths:
    paths = _paths(tmp_path)
    upgrade_database(paths.database)
    with sqlite3.connect(paths.database) as connection:
        connection.execute("CREATE TABLE reset_canary(value TEXT NOT NULL)")
        connection.execute("INSERT INTO reset_canary VALUES ('before-reset')")
    return paths


def test_pending_reset_snapshots_database_rebuilds_metadata_and_preserves_objects(
    tmp_path: Path,
) -> None:
    paths = _seeded_paths(tmp_path)
    original_pdf = paths.objects / "aa" / "paper.pdf"
    original_pdf.parent.mkdir(parents=True)
    original_pdf.write_bytes(b"%PDF-1.7\nprivate fixture\n%%EOF")
    service = DataResetService(paths)

    pending = service.stage("REBUILD PAPER-READER DATA")
    outcome = apply_pending_data_reset(paths)

    assert outcome is not None
    assert outcome.request_id == pending.request_id
    assert outcome.snapshot_database is not None
    assert outcome.snapshot_database.is_file()
    assert outcome.snapshot_manifest is not None
    assert outcome.snapshot_manifest.is_file()
    assert current_schema_revision(paths.database) == migration_head_revision()
    with sqlite3.connect(paths.database) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'reset_canary'"
            ).fetchone()
            is None
        )
    assert original_pdf.read_bytes().startswith(b"%PDF-1.7")
    assert not service.pending_path.exists()
    assert json.loads(service.epoch_path.read_text(encoding="utf-8"))["request_id"] == str(
        pending.request_id
    )


def test_reset_rejects_wrong_confirmation_and_does_not_leak_database_path(
    tmp_path: Path,
) -> None:
    paths = _seeded_paths(tmp_path)
    service = DataResetService(paths)

    with pytest.raises(DataResetError, match="confirmation"):
        service.stage("yes")

    pending = service.stage("REBUILD PAPER-READER DATA")
    marker = service.pending_path.read_text(encoding="utf-8")
    assert str(paths.database) not in marker
    assert pending.database_path_sha256 in marker
    assert service.stage("REBUILD PAPER-READER DATA") == pending


def test_malformed_reset_marker_fails_closed_without_replacing_database(
    tmp_path: Path,
) -> None:
    paths = _seeded_paths(tmp_path)
    service = DataResetService(paths)
    service.stage("REBUILD PAPER-READER DATA")
    service.pending_path.write_text("{}", encoding="utf-8")

    with pytest.raises(DataResetError, match="pending data reset"):
        apply_pending_data_reset(paths)

    with sqlite3.connect(paths.database) as connection:
        assert connection.execute("SELECT value FROM reset_canary").fetchone() == (
            "before-reset",
        )


def test_reset_marker_link_is_rejected(tmp_path: Path) -> None:
    paths = _seeded_paths(tmp_path)
    service = DataResetService(paths)
    pending = service.stage("REBUILD PAPER-READER DATA")
    marker_payload = service.pending_path.read_bytes()
    service.pending_path.unlink()
    outside = tmp_path / "outside-reset.json"
    outside.write_bytes(marker_payload)
    try:
        service.pending_path.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are unavailable for this Windows account")

    with pytest.raises(DataResetError, match="pending data reset"):
        apply_pending_data_reset(paths)

    assert pending.request_id
    assert outside.read_bytes() == marker_payload


def test_interrupted_reset_recovers_from_the_same_pending_request(
    tmp_path: Path,
) -> None:
    paths = _seeded_paths(tmp_path)
    original_pdf = paths.objects / "bb" / "paper.pdf"
    original_pdf.parent.mkdir(parents=True)
    original_pdf.write_bytes(b"%PDF-1.7\nkeep me\n%%EOF")
    pending = DataResetService(paths).stage("REBUILD PAPER-READER DATA")

    class InjectedInterruption(BaseException):
        pass

    def interrupt(phase: str) -> None:
        if phase == "old_database_moved":
            raise InjectedInterruption

    with pytest.raises(InjectedInterruption):
        apply_pending_data_reset(paths, fault=interrupt)

    outcome = apply_pending_data_reset(paths)

    assert outcome is not None
    assert outcome.request_id == pending.request_id
    assert outcome.recovered is True
    assert current_schema_revision(paths.database) == migration_head_revision()
    assert original_pdf.read_bytes().endswith(b"%%EOF")
