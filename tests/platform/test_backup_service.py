from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from paper_reader.backups.archive import (
    BackupArchiveBuilder,
    BackupArchiveResult,
    BackupCancelledError,
    BackupInsufficientSpaceError,
)
from paper_reader.backups.manifest import (
    BACKUP_FORMAT_VERSION,
    DATABASE_ARCHIVE_PATH,
    BackupDatabaseMember,
    BackupManifest,
)
from paper_reader.backups.repository import BackupRepository
from paper_reader.backups.service import (
    BACKUP_JOB_KIND,
    BackupKind,
    BackupService,
    WeeklyBackupScheduler,
)
from paper_reader.database import create_session_factory, create_sqlite_engine
from paper_reader.jobs.repository import JobRepository
from paper_reader.migrations import upgrade_database
from paper_reader.settings import AppPaths


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


class TrackingJobs(JobRepository):
    def __init__(self, sessions) -> None:  # type: ignore[no-untyped-def]
        super().__init__(sessions)
        self.checkpoints_before_events: list[tuple[str, dict[str, Any] | None]] = []

    def append_event(  # type: ignore[override]
        self,
        *,
        job_id: str,
        event_type: str,
        progress: int,
        payload: dict[str, Any],
    ):
        current = self.get(job_id)
        self.checkpoints_before_events.append(
            (event_type, dict(current.checkpoint) if current and current.checkpoint else None)
        )
        return super().append_event(
            job_id=job_id,
            event_type=event_type,
            progress=progress,
            payload=payload,
        )


class FixtureArchiveBuilder:
    def __init__(
        self,
        clock: MutableClock,
        *,
        error: Exception | None = None,
    ) -> None:
        self.clock = clock
        self.error = error
        self.calls = 0
        self.last_result: BackupArchiveResult | None = None

    def create(  # type: ignore[no-untyped-def]
        self,
        *,
        destination: Path,
        cancelled=lambda: False,
        progress=lambda _phase, _completed, _total: None,
    ) -> BackupArchiveResult:
        self.calls += 1
        if cancelled():
            raise BackupCancelledError("cancelled")
        if self.error is not None:
            raise self.error
        progress("database_snapshot", 1, 2)
        destination.parent.mkdir(parents=True, exist_ok=True)
        package = f"portable-backup-{self.calls}-{self.clock.value.isoformat()}".encode()
        destination.write_bytes(package)
        progress("database_archive", 2, 2)
        database_hash = sha256(b"fixture-database").hexdigest()
        manifest = BackupManifest(
            format_version=BACKUP_FORMAT_VERSION,
            app_version="0.6.0-test",
            schema_revision="0023_backup_archives",
            created_at=self.clock.value,
            database=BackupDatabaseMember(
                archive_path=DATABASE_ARCHIVE_PATH,
                byte_size=len(b"fixture-database"),
                sha256=database_hash,
            ),
            objects=(),
            included=("SQLite database",),
            excluded=("credentials", "logs", "models"),
        )
        self.last_result = BackupArchiveResult(
            path=destination,
            package_sha256=sha256(package).hexdigest(),
            manifest_sha256=manifest.sha256(),
            manifest=manifest,
        )
        return self.last_result


class BlockingArchiveBuilder:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.stopped = threading.Event()

    def create(  # type: ignore[no-untyped-def]
        self,
        *,
        destination: Path,
        cancelled=lambda: False,
        progress=lambda _phase, _completed, _total: None,
    ) -> BackupArchiveResult:
        self.entered.set()
        while not cancelled():
            self.stopped.wait(0.005)
        self.stopped.set()
        raise BackupCancelledError("cancelled by application shutdown")


def make_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    paths.ensure()
    upgrade_database(paths.database)
    return paths


def make_service(
    tmp_path: Path,
    *,
    clock: MutableClock | None = None,
    builder: object | None = None,
    tracking_jobs: bool = False,
):  # type: ignore[no-untyped-def]
    paths = make_paths(tmp_path)
    engine = create_sqlite_engine(paths.database)
    sessions = create_session_factory(engine)
    jobs = TrackingJobs(sessions) if tracking_jobs else JobRepository(sessions)
    archives = BackupRepository(sessions)
    effective_clock = clock or MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    effective_builder = builder or FixtureArchiveBuilder(effective_clock)
    service = BackupService(
        paths=paths,
        jobs=jobs,
        archives=archives,
        builder=effective_builder,  # type: ignore[arg-type]
        clock=effective_clock,
    )
    return paths, engine, sessions, jobs, archives, service, effective_builder


@pytest.mark.asyncio
async def test_manual_backup_is_idempotent_persistent_and_checkpointed_before_events(
    tmp_path: Path,
) -> None:
    paths, engine, _sessions, jobs, archives, service, builder = make_service(
        tmp_path,
        tracking_jobs=True,
    )
    try:
        created = service.create_job(kind=BackupKind.MANUAL, idempotency_key="download-one")
        duplicate = service.create_job(kind=BackupKind.MANUAL, idempotency_key="download-one")
        assert duplicate.id == created.id
        assert created.kind == BACKUP_JOB_KIND
        assert created.payload == {"kind": "manual", "encrypted": False}

        await service.execute(created.id)

        finished = jobs.get(created.id)
        archive = archives.get_by_job(created.id)
        assert finished is not None and finished.status == "succeeded"
        assert finished.progress == 100
        assert archive is not None
        package = paths.backups / archive.relative_path
        assert package.is_file()
        assert archive.byte_size == package.stat().st_size
        assert archive.package_sha256 == sha256(package.read_bytes()).hexdigest()
        assert builder.last_result is not None
        assert archive.manifest_sha256 == builder.last_result.manifest_sha256
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_manual_backup_metadata_and_event_order_are_reproducible(tmp_path: Path) -> None:
    paths, engine, _sessions, jobs, archives, service, _builder = make_service(
        tmp_path,
        tracking_jobs=True,
    )
    try:
        job = service.create_job(kind=BackupKind.MANUAL, idempotency_key="metadata")
        await service.execute(job.id)
        archive = archives.get_by_job(job.id)
        assert archive is not None
        assert archive.kind == "manual"
        assert archive.format_version == BACKUP_FORMAT_VERSION
        assert archive.object_count == 0
        assert archive.encrypted is False
        assert len(archive.package_sha256) == 64
        assert len(archive.manifest_sha256) == 64

        tracked = dict(jobs.checkpoints_before_events)  # type: ignore[attr-defined]
        assert tracked["backup.started"] == {"phase": "snapshot"}
        assert tracked["backup.succeeded"]["phase"] == "complete"
        assert tracked["backup.succeeded"]["backup_id"] == archive.id
        event_types = [
            event.event_type
            for event in jobs.list_events()
            if event.job_id == job.id
        ]
        assert event_types == [
            "backup.started",
            "backup.progress",
            "backup.progress",
            "backup.archived",
            "backup.succeeded",
        ]
        serialized = json.dumps(jobs.get(job.id).result, sort_keys=True)  # type: ignore[union-attr]
        assert str(paths.state_root) not in serialized
        assert str(paths.library_root) not in serialized
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_cancel_failure_and_shutdown_have_stable_resumable_semantics(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 7, 14, 3, 0, tzinfo=UTC))
    cancelled_builder = FixtureArchiveBuilder(clock)
    paths, engine, _sessions, jobs, _archives, service, _ = make_service(
        tmp_path / "cancelled",
        clock=clock,
        builder=cancelled_builder,
    )
    try:
        cancelled = service.create_job(kind=BackupKind.MANUAL, idempotency_key="cancel")
        jobs.request_cancel(cancelled.id)
        await service.execute(cancelled.id)
        cancelled_state = jobs.get(cancelled.id)
        assert cancelled_state is not None and cancelled_state.status == "cancelled"
        assert cancelled_state.error_code == "cancelled"
        assert cancelled_builder.calls == 0
        assert not tuple(paths.backups.glob("*.paper-reader-backup"))
    finally:
        engine.dispose()

    failure = BackupInsufficientSpaceError("D:/private/location has no space")
    _, failure_engine, _, failure_jobs, _, failure_service, _ = make_service(
        tmp_path / "failed",
        clock=clock,
        builder=FixtureArchiveBuilder(clock, error=failure),
    )
    try:
        failed = failure_service.create_job(
            kind=BackupKind.MANUAL,
            idempotency_key="no-space",
        )
        await failure_service.execute(failed.id)
        failed_state = failure_jobs.get(failed.id)
        assert failed_state is not None and failed_state.status == "failed"
        assert failed_state.error_code == "insufficient_space"
        assert failed_state.result == {"retryable": True}
        assert "private" not in (failed_state.error_message or "")
    finally:
        failure_engine.dispose()

    occupied_paths, occupied_engine, _, occupied_jobs, _, occupied_service, occupied_builder = (
        make_service(
            tmp_path / "occupied",
            clock=clock,
            builder=FixtureArchiveBuilder(clock),
        )
    )
    try:
        occupied = occupied_service.create_job(
            kind=BackupKind.MANUAL,
            idempotency_key="occupied",
        )
        created_at = occupied.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        destination = occupied_paths.backups / (
            f"paper-reader-manual-{created_at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
            f"{occupied.id}.paper-reader-backup"
        )
        destination.write_bytes(b"personal bytes that must be preserved")
        await occupied_service.execute(occupied.id)
        occupied_state = occupied_jobs.get(occupied.id)
        assert occupied_state is not None and occupied_state.status == "failed"
        assert occupied_state.error_code == "integrity"
        assert destination.read_bytes() == b"personal bytes that must be preserved"
        assert occupied_builder.calls == 0
    finally:
        occupied_engine.dispose()

    blocker = BlockingArchiveBuilder()
    _, shutdown_engine, _, shutdown_jobs, _, shutdown_service, _ = make_service(
        tmp_path / "shutdown",
        clock=clock,
        builder=blocker,
    )
    try:
        interrupted = shutdown_service.create_job(
            kind=BackupKind.MANUAL,
            idempotency_key="shutdown",
        )
        task = asyncio.create_task(shutdown_service.execute(interrupted.id))
        assert await asyncio.to_thread(blocker.entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        assert blocker.stopped.is_set()
        interrupted_state = shutdown_jobs.get(interrupted.id)
        assert interrupted_state is not None
        assert interrupted_state.status == "queued"
        assert interrupted_state.checkpoint == {"phase": "interrupted"}
        assert interrupted.id in shutdown_service.resumable_job_ids()
    finally:
        shutdown_engine.dispose()


@pytest.mark.asyncio
async def test_weekly_scheduler_honors_boundary_debounce_retry_and_concurrency(
    tmp_path: Path,
) -> None:
    due = datetime(2026, 7, 14, 6, 0, tzinfo=UTC)
    clock = MutableClock(due - timedelta(days=7))
    paths, engine, sessions, jobs, archives, service, builder = make_service(
        tmp_path,
        clock=clock,
    )
    try:
        previous = service.create_job(
            kind=BackupKind.AUTOMATIC,
            idempotency_key="previous-auto",
        )
        await service.execute(previous.id)
        assert archives.latest_automatic() is not None

        clock.value = due - timedelta(seconds=1)
        assert (
            WeeklyBackupScheduler(service=service, archives=archives, clock=clock).check()
            is None
        )

        clock.value = due
        first_scheduler = WeeklyBackupScheduler(service=service, archives=archives, clock=clock)
        due_job = first_scheduler.check()
        assert due_job is not None and due_job.status == "queued"
        assert first_scheduler.check(now=due + timedelta(hours=5, minutes=59)) is None

        def concurrent_check() -> str | None:
            candidate = WeeklyBackupScheduler(
                service=BackupService(
                    paths=paths,
                    jobs=JobRepository(sessions),
                    archives=BackupRepository(sessions),
                    builder=builder,  # type: ignore[arg-type]
                    clock=clock,
                ),
                archives=BackupRepository(sessions),
                clock=clock,
            ).check(now=clock.value)
            return candidate.id if candidate is not None else None

        with ThreadPoolExecutor(max_workers=2) as executor:
            ids = list(executor.map(lambda _index: concurrent_check(), range(2)))
        assert set(ids) == {due_job.id}

        failing_builder = FixtureArchiveBuilder(
            clock,
            error=BackupInsufficientSpaceError("no room"),
        )
        failed_service = BackupService(
            paths=paths,
            jobs=jobs,
            archives=archives,
            builder=failing_builder,
            clock=clock,
        )
        await failed_service.execute(due_job.id)
        assert jobs.get(due_job.id).status == "failed"  # type: ignore[union-attr]
        assert archives.latest_automatic().id == archives.get_by_job(previous.id).id  # type: ignore[union-attr]

        clock.value = due + timedelta(hours=6)
        with ThreadPoolExecutor(max_workers=2) as executor:
            retry_ids = list(executor.map(lambda _index: concurrent_check(), range(2)))
        assert None not in retry_ids
        assert len(set(retry_ids)) == 1
        assert retry_ids[0] != due_job.id
    finally:
        engine.dispose()


def test_first_automatic_backup_waits_for_persisted_seven_day_boundary(
    tmp_path: Path,
) -> None:
    started = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    clock = MutableClock(started)
    _paths, engine, _sessions, _jobs, archives, service, _builder = make_service(
        tmp_path,
        clock=clock,
    )
    try:
        first = WeeklyBackupScheduler(service=service, archives=archives, clock=clock)
        assert first.check() is None
        assert service.policy().next_automatic_at == started + timedelta(days=7)

        clock.value = started + timedelta(days=7) - timedelta(microseconds=1)
        before = WeeklyBackupScheduler(service=service, archives=archives, clock=clock)
        assert before.check() is None

        clock.value = started + timedelta(days=7)
        boundary = WeeklyBackupScheduler(service=service, archives=archives, clock=clock)
        job = boundary.check()
        assert job is not None and job.payload["kind"] == "automatic"
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_automatic_retention_deletes_only_verified_old_automatic_packages(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 6, 1, tzinfo=UTC))
    paths, engine, _sessions, _jobs, archives, service, _builder = make_service(
        tmp_path,
        clock=clock,
    )
    try:
        manual = service.create_job(kind=BackupKind.MANUAL, idempotency_key="manual-keep")
        await service.execute(manual.id)
        manual_archive = archives.get_by_job(manual.id)
        assert manual_archive is not None

        automatic_ids: list[str] = []
        for index in range(5):
            clock.value += timedelta(days=1)
            job = service.create_job(
                kind=BackupKind.AUTOMATIC,
                idempotency_key=f"auto-{index}",
            )
            await service.execute(job.id)
            automatic_ids.append(job.id)

        automatic = [
            item for item in archives.list(include_deleted=True) if item.kind == "automatic"
        ]
        assert len(automatic) == 5
        deleted = [item for item in automatic if item.deleted_at is not None]
        assert len(deleted) == 1
        assert deleted[0].job_id == automatic_ids[0]
        assert not (paths.backups / deleted[0].relative_path).exists()
        assert (paths.backups / manual_archive.relative_path).is_file()
        assert archives.get(manual_archive.id).deleted_at is None  # type: ignore[union-attr]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_retention_never_follows_anomalous_record_paths(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 6, 1, tzinfo=UTC))
    paths, engine, sessions, _jobs, archives, service, _builder = make_service(
        tmp_path,
        clock=clock,
    )
    try:
        jobs = []
        for index in range(4):
            job = service.create_job(
                kind=BackupKind.AUTOMATIC,
                idempotency_key=f"unsafe-auto-{index}",
            )
            await service.execute(job.id)
            jobs.append(job)
            clock.value += timedelta(days=1)
        oldest = archives.get_by_job(jobs[0].id)
        assert oldest is not None
        outside = paths.library_root / "must-not-delete.paper-reader-backup"
        outside.write_bytes(b"personal file")
        with sessions() as session:
            record = session.get(type(oldest), oldest.id)
            record.relative_path = "../must-not-delete.paper-reader-backup"
            session.commit()

        fifth = service.create_job(
            kind=BackupKind.AUTOMATIC,
            idempotency_key="unsafe-auto-4",
        )
        await service.execute(fifth.id)

        preserved = archives.get(oldest.id)
        assert preserved is not None and preserved.deleted_at is None
        assert outside.read_bytes() == b"personal file"
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_portable_snapshot_marks_open_jobs_as_restored_interrupted(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    engine = create_sqlite_engine(paths.database)
    sessions = create_session_factory(engine)
    jobs = JobRepository(sessions)
    archives = BackupRepository(sessions)
    clock = MutableClock(datetime(2026, 7, 14, 8, 0, tzinfo=UTC))
    service = BackupService(
        paths=paths,
        jobs=jobs,
        archives=archives,
        builder=BackupArchiveBuilder(paths=paths, clock=clock),
        clock=clock,
    )
    try:
        queued_side_effect = jobs.create(
            kind="reader.acquire_document",
            payload={"url": "https://example.org/side-effect.pdf"},
            idempotency_key="queued-side-effect",
        )
        running_side_effect = jobs.create(
            kind="analysis.generate_report",
            payload={"work_id": "work-a"},
            idempotency_key="running-side-effect",
        )
        jobs.update_state(running_side_effect.id, status="running", progress=30)
        backup = service.create_job(kind=BackupKind.MANUAL, idempotency_key="safe-restore")
        await service.execute(backup.id)
        archive = archives.get_by_job(backup.id)
        assert archive is not None

        restored_database = tmp_path / "snapshot.sqlite3"
        with zipfile.ZipFile(paths.backups / archive.relative_path) as package:
            restored_database.write_bytes(package.read(DATABASE_ARCHIVE_PATH))
        connection = sqlite3.connect(restored_database)
        try:
            states = dict(
                connection.execute(
                    "SELECT id, status FROM jobs WHERE id IN (?, ?, ?)",
                    (queued_side_effect.id, running_side_effect.id, backup.id),
                )
            )
            errors = dict(
                connection.execute(
                    "SELECT id, error_code FROM jobs WHERE id IN (?, ?, ?)",
                    (queued_side_effect.id, running_side_effect.id, backup.id),
                )
            )
        finally:
            connection.close()
        assert set(states.values()) == {"restored_interrupted"}
        assert set(errors.values()) == {"restored_interrupted"}
        assert jobs.get(queued_side_effect.id).status == "queued"  # type: ignore[union-attr]
        assert jobs.get(running_side_effect.id).status == "running"  # type: ignore[union-attr]
    finally:
        engine.dispose()
