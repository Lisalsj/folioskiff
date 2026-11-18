"""In-process resume adapter for durable publication Jobs."""

from __future__ import annotations

from paper_reader.publishing.service import PublishingService


async def resume_publication(service: PublishingService, job_id: str) -> None:
    await service.execute(job_id)
