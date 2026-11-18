"""In-process entry point for resumable explicit Assistant Jobs."""

from __future__ import annotations

from paper_reader.assistant.service import AssistantService


async def resume_assistant_task(service: AssistantService, job_id: str) -> None:
    await service.execute(job_id)


__all__ = ["resume_assistant_task"]
