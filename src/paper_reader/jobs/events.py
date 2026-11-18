"""Durable Server-Sent Event serialization and streaming."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.jobs.models import JobEventRecord
from paper_reader.jobs.repository import JobRepository


def event_payload(event: JobEventRecord) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "job_id": event.job_id,
        "sequence": event.sequence,
        "type": event.event_type,
        "timestamp": event.created_at.isoformat(),
        "progress": event.progress,
        "payload": event.payload,
    }


def encode_sse_event(event: JobEventRecord) -> str:
    serialized = json.dumps(event_payload(event), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {serialized}\n\n"


async def stream_job_events(
    *,
    sessions: sessionmaker[Session],
    request: Request,
    after_event_id: int,
    poll_interval: float = 0.5,
    heartbeat_interval: float = 15.0,
) -> AsyncIterator[str]:
    cursor = after_event_id
    time_since_heartbeat = 0.0
    repository = JobRepository(sessions)

    while not await request.is_disconnected():
        events = repository.list_events(after_event_id=cursor)
        if events:
            for event in events:
                cursor = event.event_id
                yield encode_sse_event(event)
            time_since_heartbeat = 0.0
        elif time_since_heartbeat >= heartbeat_interval:
            yield ": keep-alive\n\n"
            time_since_heartbeat = 0.0

        await asyncio.sleep(poll_interval)
        time_since_heartbeat += poll_interval
