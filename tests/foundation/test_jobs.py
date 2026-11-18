from __future__ import annotations

from pathlib import Path

import pytest


def test_job_repository_reuses_idempotency_key(tmp_path: Path) -> None:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.jobs.repository import JobRepository

    engine = create_sqlite_engine(tmp_path / "jobs.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    repository = JobRepository(sessions)

    first = repository.create(
        kind="discovery.run",
        payload={"plan_id": "plan-1"},
        idempotency_key="same",
    )
    second = repository.create(
        kind="discovery.run",
        payload={"plan_id": "plan-1"},
        idempotency_key="same",
    )

    assert first.id == second.id
    assert first.status == "queued"
    assert first.progress == 0
    engine.dispose()


def test_job_events_are_persisted_in_sequence_and_resume_after_event_id(tmp_path: Path) -> None:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.jobs.repository import JobRepository

    engine = create_sqlite_engine(tmp_path / "events.sqlite3")
    Base.metadata.create_all(engine)
    repository = JobRepository(create_session_factory(engine))
    job = repository.create(kind="discovery.run", payload={})

    first = repository.append_event(
        job_id=job.id,
        event_type="job.started",
        progress=0,
        payload={"message": "started"},
    )
    second = repository.append_event(
        job_id=job.id,
        event_type="job.progress",
        progress=25,
        payload={"completed": 1, "total": 4},
    )

    resumed = repository.list_events(after_event_id=first.event_id)

    assert first.sequence == 1
    assert second.sequence == 2
    assert second.event_id > first.event_id
    assert [event.event_id for event in resumed] == [second.event_id]
    engine.dispose()


def test_job_event_progress_must_stay_in_display_range(tmp_path: Path) -> None:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.jobs.repository import JobRepository

    engine = create_sqlite_engine(tmp_path / "progress.sqlite3")
    Base.metadata.create_all(engine)
    repository = JobRepository(create_session_factory(engine))
    job = repository.create(kind="discovery.run", payload={})

    with pytest.raises(ValueError, match="between 0 and 100"):
        repository.append_event(
            job_id=job.id,
            event_type="job.progress",
            progress=101,
            payload={},
        )

    engine.dispose()
