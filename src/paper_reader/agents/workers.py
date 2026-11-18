"""In-process entry point for resumable explicit analysis Jobs."""

from __future__ import annotations

from paper_reader.agents.analysis_service import AnalysisService
from paper_reader.agents.comparison_service import ComparisonService


async def resume_analysis_report(service: AnalysisService, job_id: str) -> None:
    await service.execute(job_id)


async def resume_comparison_generation(
    service: ComparisonService,
    job_id: str,
) -> None:
    await service.execute(job_id)
