"""HTTP commands and evidence reads for recommendation scoring."""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field

from paper_reader.discovery.runs import DiscoveryRunRepository
from paper_reader.jobs.models import JobRecord
from paper_reader.jobs.repository import JobRepository
from paper_reader.recommendation.ccf import load_builtin_ccf_catalog
from paper_reader.recommendation.enrichment_service import (
    ENRICHMENT_JOB_KIND,
    MetadataEnrichmentService,
    WorkLookupRepository,
)
from paper_reader.recommendation.observations import MetadataObservationRepository
from paper_reader.recommendation.orchestration import RecommendationOrchestrator
from paper_reader.recommendation.quality import QualityMetrics, score_quality
from paper_reader.recommendation.rating_catalogs import (
    MAX_CATALOG_BYTES,
    RatingCatalog,
    RatingCatalogKind,
    RatingCatalogRepository,
)
from paper_reader.recommendation.relevance import MetricEvidence
from paper_reader.recommendation.score_rules import (
    ScoreRuleDimensionInput,
    ScoreRuleRepository,
    ScoreRuleVersion,
)
from paper_reader.recommendation.score_snapshots import (
    ScoreSnapshot,
    ScoreSnapshotRepository,
)
from paper_reader.recommendation.workers import run_metadata_then_recommend
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1/scores",
    tags=["scores"],
    dependencies=[Depends(require_local_session)],
)

IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", max_length=120),
]


class MetadataEnrichmentRequest(BaseModel):
    enricher_ids: tuple[str, ...] | None = Field(default=None, max_length=20)


class AcceptedMetadataJob(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    progress: int
    cancel_requested: bool


class MetadataObservationResponse(BaseModel):
    id: str
    observation_sha256: str
    enricher_id: str
    enricher_version: str
    status: str
    external_id: str | None
    matched_by: str | None
    fields: dict[str, Any]
    field_provenance: dict[str, str]
    warnings: list[str]
    fetched_at: str
    response_sha256: str | None
    rate_limit: dict[str, Any] | None


class RatingCatalogResponse(BaseModel):
    id: str
    kind: RatingCatalogKind
    catalog_name: str
    catalog_version: str
    source_note: str
    filename: str
    file_sha256: str
    row_count: int
    imported_at: datetime
    active: bool


class CCFCatalogResponse(BaseModel):
    catalog_id: str
    edition: int
    rating_year: int
    rename_revision_date: str
    entry_count: int
    journal_count: int
    conference_count: int
    source_url: str
    source_pdf_sha256: str


class QualityRuleDimensionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80)
    enabled: bool
    weight_percent: float = Field(ge=0, le=100)


class QualityRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    dimensions: tuple[QualityRuleDimensionRequest, ...] = Field(
        min_length=7,
        max_length=7,
    )


class QualityRuleDimensionResponse(BaseModel):
    key: str
    label: str
    description: str
    enabled: bool
    weight_percent: float


class QualityRulePreviewComponent(BaseModel):
    key: str
    label: str
    enabled: bool
    weight_percent: float
    effective_weight_percent: float
    status: str
    observed_value: float | None
    imputed_value: float | None
    reason: str | None


class QualityRulePreview(BaseModel):
    score: float
    coverage: float
    confidence: float
    missing_policy: str
    components: tuple[QualityRulePreviewComponent, ...]


class QualityRuleResponse(BaseModel):
    id: str
    rule_version: str
    revision: int
    name: str
    builtin: bool
    active: bool
    dimensions: tuple[QualityRuleDimensionResponse, ...]
    rule_sha256: str
    parent_version_id: str | None
    created_at: datetime | None
    preview: QualityRulePreview


class ScoreSnapshotResponse(BaseModel):
    id: str
    work_id: str
    run_id: str | None
    context_key: str
    score_type: str
    rule_version: str
    score: float
    coverage: float
    confidence: float
    components: tuple[dict[str, Any], ...]
    weights: dict[str, float]
    missing_policy: dict[str, Any]
    evidence_observation_ids: tuple[str, ...]
    hard_constraints: dict[str, Any]
    snapshot_sha256: str
    created_at: datetime


def _catalog_response(catalog: RatingCatalog) -> RatingCatalogResponse:
    return RatingCatalogResponse(
        id=catalog.id,
        kind=catalog.kind,
        catalog_name=catalog.catalog_name,
        catalog_version=catalog.catalog_version,
        source_note=catalog.source_note,
        filename=catalog.filename,
        file_sha256=catalog.file_sha256,
        row_count=catalog.row_count,
        imported_at=catalog.imported_at,
        active=catalog.active,
    )


def _quality_rule_response(rule: ScoreRuleVersion) -> QualityRuleResponse:
    unknown = MetricEvidence.unknown(reason="evidence_not_loaded")
    preview = score_quality(
        QualityMetrics(
            citation_performance=unknown,
            venue_rating=unknown,
            author_impact=unknown,
            institution_impact=unknown,
            code_data_impact=unknown,
            research_transparency=unknown,
            publication_maturity=unknown,
        ),
        weights=rule.weights,
        rule_version=rule.rule_version,
    )
    dimension_by_key = {dimension.key: dimension for dimension in rule.dimensions}
    return QualityRuleResponse(
        id=rule.id,
        rule_version=rule.rule_version,
        revision=rule.revision,
        name=rule.name,
        builtin=rule.builtin,
        active=rule.active,
        dimensions=tuple(
            QualityRuleDimensionResponse(
                key=dimension.key,
                label=dimension.label,
                description=dimension.description,
                enabled=dimension.enabled,
                weight_percent=round(dimension.weight * 100, 6),
            )
            for dimension in rule.dimensions
        ),
        rule_sha256=rule.rule_sha256,
        parent_version_id=rule.parent_version_id,
        created_at=rule.created_at,
        preview=QualityRulePreview(
            score=preview.score,
            coverage=preview.coverage,
            confidence=preview.confidence,
            missing_policy="unknown_is_neutral_prior",
            components=tuple(
                QualityRulePreviewComponent(
                    key=component.dimension,
                    label=dimension_by_key[component.dimension].label,
                    enabled=dimension_by_key[component.dimension].enabled,
                    weight_percent=round(component.configured_weight * 100, 6),
                    effective_weight_percent=round(component.effective_weight * 100, 6),
                    status=component.status.value,
                    observed_value=component.observed_value,
                    imputed_value=component.imputed_value,
                    reason=component.reason,
                )
                for component in preview.components
            ),
        ),
    )


def _snapshot_response(snapshot: ScoreSnapshot) -> ScoreSnapshotResponse:
    return ScoreSnapshotResponse(
        id=snapshot.id,
        work_id=snapshot.work_id,
        run_id=snapshot.run_id,
        context_key=snapshot.context_key,
        score_type=snapshot.score_type,
        rule_version=snapshot.rule_version,
        score=snapshot.score,
        coverage=snapshot.coverage,
        confidence=snapshot.confidence,
        components=snapshot.components,
        weights=snapshot.weights,
        missing_policy=snapshot.missing_policy,
        evidence_observation_ids=snapshot.evidence_observation_ids,
        hard_constraints=snapshot.hard_constraints,
        snapshot_sha256=snapshot.snapshot_sha256,
        created_at=snapshot.created_at,
    )


def _service(request: Request) -> MetadataEnrichmentService:
    sessions = request.app.state.sessions
    return MetadataEnrichmentService(
        runs=DiscoveryRunRepository(sessions),
        jobs=JobRepository(sessions),
        lookups=WorkLookupRepository(sessions),
        observations=MetadataObservationRepository(sessions),
        enrichers=request.app.state.enricher_registry,
    )


async def _execute(
    service: MetadataEnrichmentService,
    jobs: JobRepository,
    recommendations: RecommendationOrchestrator,
    job_id: str,
) -> None:
    await run_metadata_then_recommend(
        metadata=service,
        jobs=jobs,
        recommendations=recommendations,
        job_id=job_id,
    )


def _launch(request: Request, service: MetadataEnrichmentService, job: JobRecord) -> None:
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"metadata-enrichment:{job.id}",
            partial(
                _execute,
                service,
                JobRepository(request.app.state.sessions),
                request.app.state.recommendation_orchestrator,
                job.id,
            ),
        )


@router.post(
    "/runs/{run_id}/metadata",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedMetadataJob,
    dependencies=[Depends(require_local_mutation)],
)
async def start_metadata_enrichment(
    run_id: str,
    payload: MetadataEnrichmentRequest,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> AcceptedMetadataJob:
    service = _service(request)
    try:
        job = service.create_job(
            run_id,
            enricher_ids=payload.enricher_ids,
            idempotency_key=(
                f"metadata.enrichment:{idempotency_key}"
                if idempotency_key is not None
                else None
            ),
        )
    except KeyError as error:
        missing = str(error.args[0]) if error.args else "unknown"
        if missing == run_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Discovery run not found",
            ) from error
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown metadata enricher: {missing}",
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    _launch(request, service, job)
    return AcceptedMetadataJob.model_validate(job)


@router.post(
    "/jobs/{job_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedMetadataJob,
    dependencies=[Depends(require_local_mutation)],
)
async def retry_metadata_enrichment(job_id: str, request: Request) -> AcceptedMetadataJob:
    jobs = JobRepository(request.app.state.sessions)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.kind != ENRICHMENT_JOB_KIND:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job is not a metadata enrichment command",
        )
    retryable = (
        int(job.result.get("retryable_failed", 0))
        if isinstance(job.result, dict)
        else 0
    )
    if retryable < 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job has no retryable metadata failure",
        )
    total = int(job.payload.get("total", 0))
    completed = (
        job.checkpoint.get("completed", {})
        if isinstance(job.checkpoint, dict)
        else {}
    )
    progress = int((len(completed) * 100) / total) if total else 100
    job = jobs.update_state(
        job.id,
        status="queued",
        progress=progress,
        checkpoint=job.checkpoint,
    )
    service = _service(request)
    _launch(request, service, job)
    return AcceptedMetadataJob.model_validate(job)


@router.get(
    "/works/{work_id}/metadata",
    response_model=list[MetadataObservationResponse],
)
def latest_metadata(work_id: str, request: Request) -> list[MetadataObservationResponse]:
    observations = MetadataObservationRepository(request.app.state.sessions).latest_by_enricher(
        work_id
    )
    return [
        MetadataObservationResponse(
            id=item.id,
            observation_sha256=item.observation_sha256,
            enricher_id=item.result.enricher_id,
            enricher_version=item.result.enricher_version,
            status=item.result.status.value,
            external_id=item.result.external_id,
            matched_by=item.result.matched_by,
            fields=dict(item.result.fields),
            field_provenance=dict(item.result.field_provenance),
            warnings=list(item.result.warnings),
            fetched_at=item.result.fetched_at.isoformat(),
            response_sha256=item.result.response_sha256,
            rate_limit=(
                {
                    "limit": item.result.rate_limit.limit,
                    "remaining": item.result.rate_limit.remaining,
                    "reset_after_seconds": item.result.rate_limit.reset_after_seconds,
                    "interval_seconds": item.result.rate_limit.interval_seconds,
                }
                if item.result.rate_limit is not None
                else None
            ),
        )
        for item in (observations[key] for key in sorted(observations))
    ]


@router.get(
    "/snapshots/{snapshot_id}",
    response_model=ScoreSnapshotResponse,
)
def score_snapshot_detail(
    snapshot_id: str,
    request: Request,
) -> ScoreSnapshotResponse:
    try:
        snapshot = ScoreSnapshotRepository(request.app.state.sessions).get(snapshot_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Score snapshot not found",
        ) from error
    return _snapshot_response(snapshot)


@router.get(
    "/works/{work_id}/snapshots/{score_type}",
    response_model=ScoreSnapshotResponse,
)
def latest_score_snapshot(
    work_id: str,
    score_type: Literal["relevance", "quality", "freshness", "preference", "composite"],
    request: Request,
    context_key: Annotated[str, Query(min_length=1, max_length=255)],
) -> ScoreSnapshotResponse:
    snapshot = ScoreSnapshotRepository(request.app.state.sessions).latest(
        work_id,
        score_type=score_type,
        context_key=context_key,
    )
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Score snapshot not found",
        )
    return _snapshot_response(snapshot)


@router.get("/rules/quality", response_model=QualityRuleResponse)
def active_quality_rule(request: Request) -> QualityRuleResponse:
    rule = ScoreRuleRepository(request.app.state.sessions).get_active_quality_rule()
    return _quality_rule_response(rule)


@router.put(
    "/rules/quality",
    response_model=QualityRuleResponse,
    dependencies=[Depends(require_local_mutation)],
)
def save_quality_rule(
    payload: QualityRuleRequest,
    request: Request,
) -> QualityRuleResponse:
    try:
        rule = ScoreRuleRepository(request.app.state.sessions).save_quality_rule(
            name=payload.name,
            dimensions=tuple(
                ScoreRuleDimensionInput(
                    key=dimension.key,
                    enabled=dimension.enabled,
                    weight=dimension.weight_percent / 100,
                )
                for dimension in payload.dimensions
            ),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return _quality_rule_response(rule)


@router.delete(
    "/rules/quality",
    response_model=QualityRuleResponse,
    dependencies=[Depends(require_local_mutation)],
)
def reset_quality_rule(request: Request) -> QualityRuleResponse:
    rule = ScoreRuleRepository(request.app.state.sessions).reset_quality_rule()
    return _quality_rule_response(rule)


@router.get("/rating-catalogs/ccf", response_model=CCFCatalogResponse)
def ccf_catalog_metadata() -> CCFCatalogResponse:
    catalog = load_builtin_ccf_catalog()
    return CCFCatalogResponse(
        catalog_id=catalog.catalog_id,
        edition=catalog.edition,
        rating_year=catalog.rating_year,
        rename_revision_date=catalog.rename_revision_date,
        entry_count=len(catalog.entries),
        journal_count=sum(entry.venue_type == "journal" for entry in catalog.entries),
        conference_count=sum(entry.venue_type == "conference" for entry in catalog.entries),
        source_url=catalog.source_url,
        source_pdf_sha256=catalog.source_pdf_sha256,
    )


@router.get("/rating-catalogs", response_model=list[RatingCatalogResponse])
def list_rating_catalogs(request: Request) -> list[RatingCatalogResponse]:
    catalogs = RatingCatalogRepository(request.app.state.sessions).list_catalogs()
    return [_catalog_response(catalog) for catalog in catalogs]


@router.post(
    "/rating-catalogs/import",
    status_code=status.HTTP_201_CREATED,
    response_model=RatingCatalogResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def import_rating_catalog(
    request: Request,
    kind: Annotated[RatingCatalogKind, Form()],
    catalog_name: Annotated[str, Form(max_length=160)],
    catalog_version: Annotated[str, Form(max_length=80)],
    source_note: Annotated[str, Form(max_length=2_000)],
    rights_confirmed: Annotated[bool, Form()],
    file: Annotated[UploadFile, File()],
) -> RatingCatalogResponse:
    content = await file.read(MAX_CATALOG_BYTES + 1)
    if len(content) > MAX_CATALOG_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Rating catalog exceeds the 10 MiB limit",
        )
    try:
        catalog = RatingCatalogRepository(request.app.state.sessions).import_bytes(
            kind=kind,
            catalog_name=catalog_name,
            catalog_version=catalog_version,
            source_note=source_note,
            filename=file.filename or "",
            content=content,
            rights_confirmed=rights_confirmed,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return _catalog_response(catalog)


@router.delete(
    "/rating-catalogs/{kind}/active",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_local_mutation)],
)
def deactivate_rating_catalog(kind: RatingCatalogKind, request: Request) -> Response:
    RatingCatalogRepository(request.app.state.sessions).deactivate(kind)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
