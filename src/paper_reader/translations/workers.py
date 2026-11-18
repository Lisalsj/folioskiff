"""Restart-safe translation worker entry points."""

from __future__ import annotations

from paper_reader.translations.service import TranslationService


async def resume_translation(service: TranslationService, job_id: str) -> None:
    await service.execute(job_id)


__all__ = ["resume_translation"]
