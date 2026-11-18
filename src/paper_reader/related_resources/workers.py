"""Background entry points for durable related-resource searches."""

from __future__ import annotations

from paper_reader.related_resources.service import RelatedResourceService


async def resume_related_search(service: RelatedResourceService, job_id: str) -> None:
    await service.execute(job_id)


__all__ = ["resume_related_search"]
