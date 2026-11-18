from __future__ import annotations

import json
from pathlib import Path


def test_sse_frame_carries_recoverable_event_identity(tmp_path: Path) -> None:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.jobs.events import encode_sse_event
    from paper_reader.jobs.repository import JobRepository

    engine = create_sqlite_engine(tmp_path / "sse.sqlite3")
    Base.metadata.create_all(engine)
    repository = JobRepository(create_session_factory(engine))
    job = repository.create(kind="analysis.report", payload={})
    event = repository.append_event(
        job_id=job.id,
        event_type="report.delta",
        progress=40,
        payload={"text": "evidence first"},
    )

    frame = encode_sse_event(event)
    data_line = next(line for line in frame.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))

    assert f"id: {event.event_id}" in frame
    assert "event: report.delta" in frame
    assert payload["sequence"] == 1
    assert payload["job_id"] == job.id
    assert payload["payload"] == {"text": "evidence first"}
    engine.dispose()
