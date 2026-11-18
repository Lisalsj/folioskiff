"""HTTP preflight, explicit report commands, history, diagnostics, and exports."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from paper_reader.agents.analysis_service import AnalysisService
from paper_reader.agents.comparison_service import ComparisonService
from paper_reader.agents.comparisons import (
    ComparisonFragment,
    ComparisonPaperExtraction,
    StoredComparisonReport,
    StoredPaperEvidence,
    StructuredComparisonReport,
)
from paper_reader.agents.evidence import (
    EvidenceConflictError,
    EvidenceUnavailableError,
)
from paper_reader.agents.preflight import (
    AnalysisNotReadyError,
    AnalysisPreflight,
    AnalysisPreflightConflict,
    ComparisonNotReadyError,
    ComparisonPreflight,
    ComparisonSelectionConflict,
)
from paper_reader.agents.report_schema import StructuredAnalysisReport
from paper_reader.agents.reports import (
    AnalysisFailureDraft,
    AnalysisFragment,
    StoredAnalysisReport,
)
from paper_reader.agents.selections import (
    AnalysisJobCommand,
    AnalysisSelection,
    ComparisonJobCommand,
    ComparisonSelection,
)
from paper_reader.agents.templates import PromptTemplateError
from paper_reader.security import require_local_mutation, require_local_session

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}
_HTML_HEADERS = {
    **_PRIVATE_HEADERS,
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    ),
}


def _protect_private_response(response: Response) -> None:
    response.headers.update(_PRIVATE_HEADERS)


router = APIRouter(
    prefix="/api/v1/analysis",
    tags=["analysis"],
    dependencies=[
        Depends(require_local_session),
        Depends(_protect_private_response),
    ],
)

comparison_router = APIRouter(
    prefix="/api/v1/comparisons",
    tags=["comparisons"],
    dependencies=[
        Depends(require_local_session),
        Depends(_protect_private_response),
    ],
)


class AnalysisJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    progress: int
    payload: dict[str, Any]
    result: dict[str, Any] | None
    checkpoint: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime


class AnalysisReportSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    work_id: str
    version: int
    job_id: str
    document_version_id: str | None
    document_content_sha256: str | None
    evidence_level: str
    evidence_sha256: str
    backend_id: str
    requested_model_id: str | None
    resolved_model_id: str | None
    reasoning_effort: str
    language: str
    schema_version: str
    created_at: datetime


class AnalysisReportResponse(AnalysisReportSummaryResponse):
    evidence_manifest: dict[str, Any]
    service_tier: str
    template_version_id: str
    complete_prompt: str
    prompt_sha256: str
    network_policy: str
    content: StructuredAnalysisReport
    rendered_markdown: str
    external_citations: tuple[dict[str, str], ...]
    request_fingerprint: str
    report_sha256: str


class AnalysisFailureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    work_id: str
    error_category: str
    error_message: str
    raw_response: str
    validation_errors: tuple[str, ...]
    repair_count: int
    request_fingerprint: str
    created_at: datetime


class AnalysisFragmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    sequence: int
    kind: str
    text: str
    created_at: datetime


class ComparisonItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    report_id: str
    position: int
    work_id: str
    document_version_id: str | None
    document_content_sha256: str | None
    evidence_level: str | None
    evidence_sha256: str | None
    evidence_manifest: dict[str, Any]
    extraction_status: str
    extraction_id: str | None
    coverage: int
    created_at: datetime


class ComparisonReportSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    comparison_key: str
    version: int
    job_id: str
    title: str
    mode: str
    backend_id: str
    requested_model_id: str | None
    resolved_model_id: str | None
    reasoning_effort: str
    language: str
    coverage: int
    schema_version: str
    created_at: datetime
    items: tuple[ComparisonItemResponse, ...]


class ComparisonReportResponse(ComparisonReportSummaryResponse):
    service_tier: str
    complete_prompt: str
    prompt_sha256: str
    content: StructuredComparisonReport
    rendered_markdown: str
    coverage_details: dict[str, Any]
    provenance: dict[str, Any]
    request_fingerprint: str
    report_sha256: str


class ComparisonFragmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    sequence: int
    stage: str
    item_position: int | None
    kind: str
    text: str
    created_at: datetime


class ComparisonPaperEvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_job_id: str
    position: int
    work_id: str
    document_version_id: str | None
    document_content_sha256: str | None
    evidence_level: str
    evidence_sha256: str
    backend_id: str
    requested_model_id: str | None
    resolved_model_id: str | None
    reasoning_effort: str
    service_tier: str
    prompt_sha256: str
    schema_version: str
    status: str
    extraction: ComparisonPaperExtraction | None
    error_category: str | None
    error_message: str | None
    extraction_sha256: str | None
    reused_from_id: str | None
    created_at: datetime


def _service(request: Request) -> AnalysisService:
    return cast(AnalysisService, request.app.state.analysis_service)


def _comparison_service(request: Request) -> ComparisonService:
    return cast(ComparisonService, request.app.state.comparison_service)


@router.post(
    "/works/{work_id}/preflight",
    response_model=AnalysisPreflight,
)
async def preflight_analysis(
    work_id: str,
    selection: AnalysisSelection,
    request: Request,
) -> AnalysisPreflight:
    try:
        return await _service(request).preflight(work_id, selection)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work, document, template, or backend was not found",
        ) from error
    except EvidenceUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except (EvidenceConflictError, AnalysisNotReadyError, PromptTemplateError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


@router.post(
    "/works/{work_id}/reports",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def create_analysis_report(
    work_id: str,
    command: AnalysisJobCommand,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> AnalysisJobResponse:
    service = _service(request)
    try:
        job = await service.create_job(
            work_id,
            command,
            idempotency_key=idempotency_key,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work, document, template, or backend was not found",
        ) from error
    except EvidenceUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except (
        AnalysisPreflightConflict,
        AnalysisNotReadyError,
        EvidenceConflictError,
        PromptTemplateError,
        ValueError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"analysis-report:{job.id}",
            lambda: service.execute(job.id),
        )
    return AnalysisJobResponse.model_validate(job)


@router.get(
    "/works/{work_id}/reports",
    response_model=tuple[AnalysisReportSummaryResponse, ...],
)
def list_analysis_reports(
    work_id: str,
    request: Request,
) -> tuple[AnalysisReportSummaryResponse, ...]:
    return tuple(
        AnalysisReportSummaryResponse.model_validate(report)
        for report in _service(request).reports.list_for_work(work_id)
    )


@router.get(
    "/jobs/{job_id}/failure",
    response_model=AnalysisFailureResponse,
)
def get_analysis_failure(job_id: str, request: Request) -> AnalysisFailureResponse:
    failure: AnalysisFailureDraft | None = _service(request).reports.failure_for_job(
        job_id
    )
    if failure is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis failure draft was not found",
        )
    return AnalysisFailureResponse.model_validate(failure)


@router.get(
    "/jobs/{job_id}/fragments",
    response_model=tuple[AnalysisFragmentResponse, ...],
)
def list_analysis_fragments(
    job_id: str,
    request: Request,
) -> tuple[AnalysisFragmentResponse, ...]:
    fragments: tuple[AnalysisFragment, ...] = _service(
        request
    ).reports.list_fragments(job_id)
    return tuple(AnalysisFragmentResponse.model_validate(item) for item in fragments)


@router.get("/reports/{report_id}/markdown")
def export_analysis_markdown(report_id: str, request: Request) -> Response:
    report = _report_or_404(request, report_id)
    return Response(
        content=report.rendered_markdown,
        media_type="text/markdown",
        headers={
            **_PRIVATE_HEADERS,
            "Content-Disposition": f'attachment; filename="paper-reader-{report.id}.md"'
        },
    )


@router.get("/reports/{report_id}/html", response_class=HTMLResponse)
def export_analysis_html(report_id: str, request: Request) -> HTMLResponse:
    report = _report_or_404(request, report_id)
    return HTMLResponse(
        content=_html_document(report, print_ready=False),
        headers={
            **_HTML_HEADERS,
            "Content-Disposition": f'attachment; filename="paper-reader-{report.id}.html"'
        },
    )


@router.get("/reports/{report_id}/print", response_class=HTMLResponse)
def print_analysis_report(report_id: str, request: Request) -> HTMLResponse:
    report = _report_or_404(request, report_id)
    return HTMLResponse(
        content=_html_document(report, print_ready=True),
        headers=_HTML_HEADERS,
    )


@router.get(
    "/reports/{report_id}",
    response_model=AnalysisReportResponse,
)
def get_analysis_report(report_id: str, request: Request) -> AnalysisReportResponse:
    return AnalysisReportResponse.model_validate(_report_or_404(request, report_id))


def _report_or_404(request: Request, report_id: str) -> StoredAnalysisReport:
    try:
        return _service(request).reports.get(report_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis report was not found",
        ) from error


@comparison_router.post(
    "/preflight",
    response_model=ComparisonPreflight,
)
async def preflight_comparison(
    selection: ComparisonSelection,
    request: Request,
) -> ComparisonPreflight:
    try:
        return await _comparison_service(request).preflight(selection)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="A selected Work, document, or backend was not found",
        ) from error
    except EvidenceUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except (
        ComparisonNotReadyError,
        ComparisonSelectionConflict,
        EvidenceConflictError,
        ValueError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


@comparison_router.post(
    "/reports",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def create_comparison_report(
    command: ComparisonJobCommand,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> AnalysisJobResponse:
    service = _comparison_service(request)
    try:
        job = await service.create_job(command, idempotency_key=idempotency_key)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="A selected Work, document, or backend was not found",
        ) from error
    except EvidenceUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except (
        ComparisonNotReadyError,
        ComparisonSelectionConflict,
        EvidenceConflictError,
        ValueError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"comparison-generate:{job.id}",
            lambda: service.execute(job.id),
        )
    return AnalysisJobResponse.model_validate(job)


@comparison_router.get(
    "/reports",
    response_model=tuple[ComparisonReportSummaryResponse, ...],
)
def list_comparison_reports(
    request: Request,
) -> tuple[ComparisonReportSummaryResponse, ...]:
    reports = _comparison_service(request).comparisons.list_recent()
    return tuple(
        ComparisonReportSummaryResponse.model_validate(report) for report in reports
    )


@comparison_router.get(
    "/jobs/{job_id}/fragments",
    response_model=tuple[ComparisonFragmentResponse, ...],
)
def list_comparison_fragments(
    job_id: str,
    request: Request,
) -> tuple[ComparisonFragmentResponse, ...]:
    fragments: tuple[ComparisonFragment, ...] = _comparison_service(
        request
    ).comparisons.list_fragments(job_id)
    return tuple(
        ComparisonFragmentResponse.model_validate(fragment) for fragment in fragments
    )


@comparison_router.get(
    "/jobs/{job_id}/paper-evidence",
    response_model=tuple[ComparisonPaperEvidenceResponse, ...],
)
def list_comparison_paper_evidence(
    job_id: str,
    request: Request,
) -> tuple[ComparisonPaperEvidenceResponse, ...]:
    evidence: tuple[StoredPaperEvidence, ...] = _comparison_service(
        request
    ).comparisons.list_paper_evidence(job_id)
    return tuple(
        ComparisonPaperEvidenceResponse.model_validate(item) for item in evidence
    )


@comparison_router.get("/reports/{report_id}/markdown")
def export_comparison_markdown(report_id: str, request: Request) -> Response:
    report = _comparison_report_or_404(request, report_id)
    return Response(
        content=report.rendered_markdown,
        media_type="text/markdown",
        headers={
            **_PRIVATE_HEADERS,
            "Content-Disposition": (
                f'attachment; filename="paper-reader-comparison-{report.id}.md"'
            ),
        },
    )


@comparison_router.get(
    "/reports/{report_id}",
    response_model=ComparisonReportResponse,
)
def get_comparison_report(
    report_id: str,
    request: Request,
) -> ComparisonReportResponse:
    return ComparisonReportResponse.model_validate(
        _comparison_report_or_404(request, report_id)
    )


def _comparison_report_or_404(
    request: Request,
    report_id: str,
) -> StoredComparisonReport:
    report = _comparison_service(request).comparisons.get(report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comparison report was not found",
        )
    return report


def _html_document(report: StoredAnalysisReport, *, print_ready: bool) -> str:
    title = html.escape(report.content.title)
    markdown = html.escape(report.rendered_markdown)
    ready = "true" if print_ready else "false"
    return f"""<!doctype html>
<html lang="{html.escape(report.language)}" data-print-ready="{ready}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · paper-reader</title>
<style>
:root {{ color-scheme: light; background: #f4f0e6; color: #183c32; }}
body {{ margin: 0; font: 16px/1.7 system-ui, sans-serif; }}
main {{ box-sizing: border-box; max-width: 900px; margin: 0 auto; padding: 48px; }}
pre {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }}
@media print {{
  :root {{ background: white; }}
  main {{ max-width: none; padding: 0; }}
  @page {{ margin: 18mm; }}
}}
</style>
</head>
<body><main><pre>{markdown}</pre></main></body>
</html>"""
