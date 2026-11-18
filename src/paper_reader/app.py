"""FastAPI application factory."""

from __future__ import annotations

import json
import mimetypes
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from paper_reader import PROJECT_NAME, __version__
from paper_reader.agents.analysis_service import AnalysisService
from paper_reader.agents.api import comparison_router
from paper_reader.agents.api import router as analysis_router
from paper_reader.agents.comparison_service import ComparisonService
from paper_reader.agents.comparisons import ComparisonRepository
from paper_reader.agents.evidence import EvidenceBundleBuilder
from paper_reader.agents.registry import AgentBackendRegistry, build_agent_registry
from paper_reader.agents.reports import AnalysisReportRepository
from paper_reader.agents.templates import (
    AgentSettingsRepository,
    PromptTemplateRepository,
)
from paper_reader.agents.workers import (
    resume_analysis_report,
    resume_comparison_generation,
)
from paper_reader.assets.api import router as model_asset_router
from paper_reader.assets.downloader import AssetDownloader
from paper_reader.assets.manifest import load_packaged_model_manifests
from paper_reader.assets.repository import ModelAssetRepository
from paper_reader.assets.service import ModelAssetService
from paper_reader.assistant.api import router as assistant_router
from paper_reader.assistant.compat import conversation_router
from paper_reader.assistant.context import AssistantContextBuilder
from paper_reader.assistant.repository import AssistantRepository
from paper_reader.assistant.service import AssistantService
from paper_reader.assistant.workers import resume_assistant_task
from paper_reader.backups.api import router as backup_router
from paper_reader.backups.archive import BackupArchiveBuilder
from paper_reader.backups.repository import BackupRepository
from paper_reader.backups.service import BackupService, RestoreService, WeeklyBackupScheduler
from paper_reader.backups.startup import RestoreStartupManager
from paper_reader.backups.workers import resume_backup, run_backup_scheduler
from paper_reader.credentials import (
    CredentialStore,
    NullCredentialStore,
    resolve_ollama_cloud_api_key,
    resolve_source_credentials,
)
from paper_reader.database import create_session_factory, create_sqlite_engine
from paper_reader.discovery.api import router as keyword_router
from paper_reader.discovery.connectors import ConnectorRegistry
from paper_reader.discovery.identity import IdentityRepository
from paper_reader.discovery.keywords import KeywordRepository
from paper_reader.discovery.plan_api import router as plan_router
from paper_reader.discovery.plans import DiscoveryPlanRepository, PlanScheduler
from paper_reader.discovery.run_api import router as run_router
from paper_reader.discovery.runs import DiscoveryRunRepository, DiscoveryRunService
from paper_reader.discovery.source_registry import build_source_registry
from paper_reader.discovery.update_api import router as paper_update_router
from paper_reader.discovery.work_api import router as work_router
from paper_reader.jobs.events import stream_job_events
from paper_reader.jobs.manager import BackgroundTaskManager
from paper_reader.jobs.repository import JobRepository
from paper_reader.migrations import upgrade_database
from paper_reader.onboarding.api import router as onboarding_router
from paper_reader.onboarding.probes import OnboardingCapabilityProbe
from paper_reader.onboarding.repository import OnboardingRepository
from paper_reader.onboarding.service import OnboardingService
from paper_reader.packaging.resources import discover_runtime_resources
from paper_reader.plugins.adapters import (
    PluginAdapterManager,
    PublisherRegistry,
    ScoreDimensionRegistry,
)
from paper_reader.plugins.api import router as plugin_router
from paper_reader.plugins.catalog import PluginCatalog, PluginManifestInspector
from paper_reader.plugins.discovery import PluginInstallationRegistry
from paper_reader.plugins.host import PluginHost
from paper_reader.plugins.repository import PluginHealthRepository
from paper_reader.plugins.sandbox import SandboxLauncher, WindowsSandboxLauncher
from paper_reader.publishing.api import router as publishing_router
from paper_reader.publishing.git import GitPublisher
from paper_reader.publishing.privacy import PublishingPrivacyBoundary
from paper_reader.publishing.repository import PublishedSnapshotRepository
from paper_reader.publishing.service import PublishingService, SnapshotPublisher
from paper_reader.publishing.workers import resume_publication
from paper_reader.reader.acquisition import (
    DocumentAcquisitionService,
    OpenPdfDownloader,
    PublicUrlPolicy,
)
from paper_reader.reader.api import router as reader_router
from paper_reader.reader.extraction import (
    DocumentExtractionService,
    IsolatedPdfTextExtractor,
)
from paper_reader.reader.ocr_runtime import (
    IsolatedOcrDocumentProcessor,
    OcrModelInstaller,
    RapidOcrBackend,
    load_ocr_manifest,
    probe_ocr_runtime,
)
from paper_reader.reader.ocr_service import OcrService
from paper_reader.reader.retention import RetentionService
from paper_reader.reader.storage import ContentStore
from paper_reader.reader.workers import (
    resume_document_acquisition,
    resume_document_extraction,
    resume_ocr_model_installation,
    resume_ocr_recognition,
)
from paper_reader.recommendation.api import router as score_router
from paper_reader.recommendation.briefing import router as briefing_router
from paper_reader.recommendation.enrichers.registry import build_enricher_registry
from paper_reader.recommendation.enrichment_service import (
    MetadataEnrichmentService,
    WorkLookupRepository,
)
from paper_reader.recommendation.evidence_builder import QualityEvidenceBuilder
from paper_reader.recommendation.lexical import LexicalIndex
from paper_reader.recommendation.observations import MetadataObservationRepository
from paper_reader.recommendation.orchestration import (
    DynamicRunRelevanceScorer,
    RecommendationOrchestrator,
)
from paper_reader.recommendation.preference_api import router as library_router
from paper_reader.recommendation.preferences import PreferenceRepository
from paper_reader.recommendation.rating_catalogs import RatingCatalogRepository
from paper_reader.recommendation.result_api import router as recommendation_result_router
from paper_reader.recommendation.results import RecommendationSetRepository
from paper_reader.recommendation.score_rules import ScoreRuleRepository
from paper_reader.recommendation.score_snapshots import ScoreSnapshotRepository
from paper_reader.recommendation.semantic import (
    FastEmbedBackend,
    SemanticIndex,
    SqliteVecStore,
    module_is_usable,
    probe_vector_capability,
)
from paper_reader.recommendation.service import RelevanceService
from paper_reader.recommendation.venue_rating import VenueRatingService
from paper_reader.recommendation.workers import (
    resume_recommendation,
    run_discovery_then_recommend,
    run_metadata_then_recommend,
)
from paper_reader.related_resources.academic import AcademicRelatedSearch
from paper_reader.related_resources.api import router as related_resource_router
from paper_reader.related_resources.intent import RelatedIntentService
from paper_reader.related_resources.repository import RelatedIntentRepository
from paper_reader.related_resources.service import RelatedResourceService
from paper_reader.related_resources.web_policy import (
    PublicUrlPolicy as RelatedPublicUrlPolicy,
)
from paper_reader.related_resources.web_policy import PublicUrlVerifier
from paper_reader.related_resources.web_research import KnowledgeResearchService
from paper_reader.related_resources.workers import resume_related_search
from paper_reader.runtime_control import RuntimeControl
from paper_reader.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    BootstrapTokenStore,
    LocalSessionStore,
    is_loopback_address,
    is_loopback_origin,
    require_local_mutation,
    require_local_session,
)
from paper_reader.settings import AppPaths, SourceCredentials
from paper_reader.settings_api import router as settings_router
from paper_reader.translations.api import router as translation_router
from paper_reader.translations.export import TranslationExporter
from paper_reader.translations.repository import TranslationRepository
from paper_reader.translations.service import TranslationService
from paper_reader.translations.workers import resume_translation
from paper_reader.updates.api import router as update_router
from paper_reader.updates.service import UpdateService
from paper_reader.user_preferences import UserPreferencesService
from paper_reader.user_preferences_api import router as user_preferences_router


class BootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class ActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activation_secret: str = Field(
        min_length=43,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class ActivationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bootstrap_token: str = Field(
        min_length=43,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    expires_in_seconds: int = Field(ge=1, le=300)
    pid: int = Field(ge=1, le=2**32 - 1)


async def _read_activation_request(request: Request) -> ActivationRequest:
    if request.headers.get("Content-Type", "").partition(";")[0].strip().casefold() != (
        "application/json"
    ):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="JSON activation required",
        )
    content_length = request.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as error:
            raise HTTPException(status_code=413, detail="Activation payload too large") from error
        if not 0 <= declared_size <= 1024:
            raise HTTPException(status_code=413, detail="Activation payload too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 1024:
            raise HTTPException(status_code=413, detail="Activation payload too large")
        chunks.append(chunk)
    try:
        raw = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="Invalid activation payload") from error
    if (
        isinstance(raw, dict)
        and isinstance(raw.get("activation_secret"), str)
        and len(raw["activation_secret"]) > 128
    ):
        raise HTTPException(status_code=413, detail="Activation payload too large")
    try:
        return ActivationRequest.model_validate(raw)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="Invalid activation payload") from error


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    progress: int
    cancel_requested: bool
    pause_requested: bool


class JobEventSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: int
    job_id: str
    sequence: int
    type: str = Field(validation_alias="event_type")
    timestamp: datetime = Field(validation_alias="created_at")
    progress: int
    payload: dict[str, object]


class JobDetail(JobSummary):
    payload: dict[str, Any]
    result: dict[str, Any] | None
    checkpoint: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


async def _resume_run(
    service: DiscoveryRunService,
    recommendations: RecommendationOrchestrator,
    run_id: str,
) -> None:
    await run_discovery_then_recommend(
        discovery=service,
        recommendations=recommendations,
        run_id=run_id,
        retry=False,
    )


async def _resume_metadata_enrichment(
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


def create_app(
    *,
    paths: AppPaths,
    bootstrap_token: str,
    activation_secret: str | None = None,
    static_dir: Path | None = None,
    connector_registry: ConnectorRegistry | None = None,
    source_credentials: SourceCredentials | None = None,
    credential_store: CredentialStore | None = None,
    agent_registry: AgentBackendRegistry | None = None,
    plugin_sandbox_launcher: SandboxLauncher | None = None,
    plugin_adapter_refresh: Callable[[], None] | None = None,
    publishing_git_publisher: SnapshotPublisher | None = None,
    update_transport: httpx.AsyncBaseTransport | None = None,
    related_url_verifier: PublicUrlVerifier | None = None,
    system_timezone_provider: Callable[[], str] | None = None,
    runtime_control: RuntimeControl | None = None,
    onboarding_capability_probe: OnboardingCapabilityProbe | None = None,
) -> FastAPI:
    control = runtime_control or RuntimeControl()
    # Do not recreate a missing objects directory before the restore journal has
    # recovered it.  A process may have stopped in the narrow interval after the
    # old tree was renamed into rollback storage.
    for directory in (
        paths.state_root,
        paths.logs,
        paths.cache_root,
        paths.models,
        paths.library_root,
        paths.backups,
        paths.migration_snapshots,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    RestoreStartupManager(paths=paths).apply_pending(
        migrate=lambda database, snapshot_root: upgrade_database(
            database,
            snapshot_root=snapshot_root,
        )
    )
    paths.objects.mkdir(parents=True, exist_ok=True)
    upgrade_database(paths.database)
    engine = create_sqlite_engine(paths.database)
    sessions = create_session_factory(engine)
    onboarding_service = OnboardingService(
        repository=OnboardingRepository(sessions),
        paths=paths,
        runtime_control=control,
        capability_probe=onboarding_capability_probe,
    )
    user_preferences_service = UserPreferencesService(
        sessions,
        system_timezone_provider=system_timezone_provider,
    )
    plugin_installations = PluginInstallationRegistry(sessions)
    plugin_health = PluginHealthRepository(sessions)
    plugin_launcher = plugin_sandbox_launcher or WindowsSandboxLauncher()
    plugin_host = PluginHost(
        registry=plugin_installations,
        health=plugin_health,
        launcher=plugin_launcher,
        executable=Path(sys.executable),
        work_root=paths.cache_root / "plugins" / "calls",
        environment_source=os.environ,
    )
    plugin_catalog = PluginCatalog(
        registry=plugin_installations,
        inspector=PluginManifestInspector(
            launcher=plugin_launcher,
            executable=Path(sys.executable),
            work_root=paths.cache_root / "plugins" / "manifests",
            environment_source=os.environ,
        ),
    )
    lexical_index = LexicalIndex(sessions)
    model_asset_repository = ModelAssetRepository(sessions)
    packaged_model_manifests = load_packaged_model_manifests()
    for packaged_manifest in packaged_model_manifests:
        model_asset_repository.register(packaged_manifest)
    vector_capability = probe_vector_capability(paths.models)
    semantic_asset = model_asset_repository.find(
        packaged_model_manifests[0].bundle_id,
        packaged_model_manifests[0].revision,
    )
    semantic_enabled = (
        semantic_asset is not None
        and model_asset_repository.active_id(packaged_model_manifests[0].bundle_id)
        == semantic_asset.id
    )
    if not semantic_enabled:
        vector_capability = replace(
            vector_capability,
            enabled=False,
            degradation_reasons=(
                *vector_capability.degradation_reasons,
                "embedding_model_not_enabled",
            ),
        )
    semantic_index: SemanticIndex | None = None
    if vector_capability.enabled:
        try:
            semantic_index = SemanticIndex(
                sessions,
                backend=FastEmbedBackend(model_root=paths.models),
                store=SqliteVecStore(
                    paths.cache_root / "vectors" / "work-embeddings.sqlite3",
                    dimension=vector_capability.manifest.dimension,
                ),
            )
        except Exception:  # optional native runtime must not block lexical search
            vector_capability = replace(
                vector_capability,
                enabled=False,
                degradation_reasons=(
                    *vector_capability.degradation_reasons,
                    "vector_runtime_load_failed",
                ),
            )
            semantic_index = None
    background_tasks = BackgroundTaskManager()
    credentials = credential_store or NullCredentialStore()
    backup_archives = BackupRepository(sessions)
    backup_service = BackupService(
        paths=paths,
        jobs=JobRepository(sessions),
        archives=backup_archives,
        builder=BackupArchiveBuilder(paths=paths),
    )
    restore_service = RestoreService(
        paths=paths,
        jobs=JobRepository(sessions),
        archives=backup_archives,
    )
    backup_scheduler = WeeklyBackupScheduler(
        service=backup_service,
        archives=backup_archives,
    )
    publishing_service = PublishingService(
        jobs=JobRepository(sessions),
        snapshots=PublishedSnapshotRepository(sessions),
        privacy=PublishingPrivacyBoundary(sessions),
        publisher=publishing_git_publisher
        or GitPublisher(repository_root=Path.cwd(), cache_root=paths.cache_root),
    )
    update_service = UpdateService(
        sessions=sessions,
        current_version=__version__,
        transport=update_transport,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(30, connect=10),
                    follow_redirects=True,
                ) as http_client,
                httpx.AsyncClient(
                    timeout=httpx.Timeout(60, connect=10),
                    follow_redirects=False,
                    trust_env=False,
                ) as document_http_client,
                httpx.AsyncClient(
                    timeout=httpx.Timeout(120, connect=10),
                    follow_redirects=False,
                    trust_env=False,
                ) as agent_http_client,
            ):
                try:
                    app.state.http_client = http_client
                    runtime_credentials = source_credentials or resolve_source_credentials(
                        credentials
                    )
                    app.state.connector_registry = connector_registry or build_source_registry(
                        client=http_client,
                        credentials=runtime_credentials,
                    )
                    app.state.enricher_registry = build_enricher_registry(
                        client=http_client,
                        credentials=runtime_credentials,
                    )
                    app.state.agent_http_client = agent_http_client

                    async def activate_semantic_model(_: object) -> None:
                        refreshed = probe_vector_capability(paths.models)
                        app.state.vector_capability = refreshed
                        if not refreshed.enabled:
                            app.state.semantic_index = None
                            app.state.vector_runtime_error = "unavailable"
                            raise RuntimeError("semantic model runtime is unavailable")
                        try:
                            app.state.semantic_index = SemanticIndex(
                                sessions,
                                backend=FastEmbedBackend(model_root=paths.models),
                                store=SqliteVecStore(
                                    paths.cache_root / "vectors" / "work-embeddings.sqlite3",
                                    dimension=refreshed.manifest.dimension,
                                ),
                            )
                            app.state.vector_runtime_error = None
                        except Exception as error:
                            app.state.semantic_index = None
                            app.state.vector_runtime_error = "vector_runtime_load_failed"
                            raise RuntimeError(
                                "semantic model runtime could not be initialized"
                            ) from error

                    model_asset_service = ModelAssetService(
                        manifests=packaged_model_manifests,
                        repository=model_asset_repository,
                        jobs=JobRepository(sessions),
                        downloader=AssetDownloader(
                            paths.models,
                            client=document_http_client,
                        ),
                        runtime_probes={
                            "semantic": lambda: (
                                module_is_usable("fastembed") and module_is_usable("sqlite_vec")
                            ),
                            "ocr": probe_ocr_runtime,
                        },
                        activation_hooks={"semantic": activate_semantic_model},
                    )
                    app.state.model_asset_service = model_asset_service
                    for job_id in model_asset_service.resumable_job_ids():
                        background_tasks.start(
                            f"model-asset:{job_id}",
                            partial(model_asset_service.install, job_id),
                        )
                    if agent_registry is None:

                        def agent_registry_factory(
                            ollama_cloud_api_key: str | None,
                        ) -> AgentBackendRegistry:
                            return build_agent_registry(
                                cache_root=paths.cache_root,
                                client=agent_http_client,
                                ollama_cloud_api_key=ollama_cloud_api_key,
                            )

                        app.state.agent_registry_factory = agent_registry_factory
                        app.state.agent_registry = agent_registry_factory(
                            resolve_ollama_cloud_api_key(credentials)
                        )
                    else:
                        app.state.agent_registry_factory = None
                        app.state.agent_registry = agent_registry
                    app.state.score_dimension_registry = ScoreDimensionRegistry()
                    app.state.publisher_registry = PublisherRegistry()
                    plugin_adapter_manager = PluginAdapterManager(
                        installations=plugin_installations.list_all,
                        permissions=plugin_installations,
                        host=plugin_host,
                        sandbox_available=lambda: plugin_launcher.probe().available,
                        sources=app.state.connector_registry,
                        metadata_enrichers=app.state.enricher_registry,
                        score_dimensions=app.state.score_dimension_registry,
                        agent_backends=app.state.agent_registry,
                        publishers=app.state.publisher_registry,
                    )

                    def refresh_plugin_adapters() -> None:
                        plugin_adapter_manager.refresh()
                        if plugin_adapter_refresh is not None:
                            plugin_adapter_refresh()

                    app.state.plugin_adapter_manager = plugin_adapter_manager
                    app.state.plugin_adapter_refresh = refresh_plugin_adapters
                    plugin_adapter_manager.refresh()
                    assistant_repository = AssistantRepository(sessions)
                    assistant_context = AssistantContextBuilder(
                        sessions=sessions,
                        evidence=EvidenceBundleBuilder(
                            sessions=sessions,
                            store=ContentStore(paths.library_root),
                        ),
                        repository=assistant_repository,
                    )
                    assistant_service = AssistantService(
                        jobs=JobRepository(sessions),
                        repository=assistant_repository,
                        context=assistant_context,
                        backends=app.state.agent_registry,
                    )
                    app.state.assistant_service = assistant_service
                    for job_id in assistant_service.resumable_job_ids():
                        background_tasks.start(
                            f"assistant-reply:{job_id}",
                            partial(
                                resume_assistant_task,
                                assistant_service,
                                job_id,
                            ),
                        )
                    translation_repository = TranslationRepository(sessions)
                    translation_service = TranslationService(
                        jobs=JobRepository(sessions),
                        repository=translation_repository,
                        assistant_repository=assistant_repository,
                        context=assistant_context,
                        backends=app.state.agent_registry,
                    )
                    app.state.translation_service = translation_service
                    app.state.translation_exporter = TranslationExporter(
                        repository=translation_repository,
                        storage_root=paths.library_root / "translations" / "exports",
                    )
                    for job_id in translation_service.resumable_job_ids():
                        background_tasks.start(
                            f"translation:{job_id}",
                            partial(resume_translation, translation_service, job_id),
                        )
                    related_intents = RelatedIntentRepository(sessions)
                    related_intent_service = RelatedIntentService(
                        sessions,
                        repository=related_intents,
                        backends=app.state.agent_registry,
                    )
                    related_resource_service = RelatedResourceService(
                        sessions=sessions,
                        jobs=JobRepository(sessions),
                        intents=related_intents,
                        intent_service=related_intent_service,
                        academic=AcademicRelatedSearch(
                            sessions=sessions,
                            connectors=app.state.connector_registry,
                            identities=IdentityRepository(sessions),
                            intents=related_intents,
                            timezone=user_preferences_service.resolved_timezone(),
                        ),
                        knowledge=KnowledgeResearchService(
                            sessions=sessions,
                            intents=related_intents,
                            backends=app.state.agent_registry,
                            verifier=(
                                related_url_verifier or PublicUrlVerifier(RelatedPublicUrlPolicy())
                            ),
                        ),
                        identities=IdentityRepository(sessions),
                        preferences=PreferenceRepository(sessions),
                        context=assistant_context,
                    )
                    app.state.related_resource_service = related_resource_service
                    for job_id in related_resource_service.resumable_job_ids():
                        background_tasks.start(
                            f"related-search:{job_id}",
                            partial(
                                resume_related_search,
                                related_resource_service,
                                job_id,
                            ),
                        )
                    analysis_service = AnalysisService(
                        jobs=JobRepository(sessions),
                        reports=AnalysisReportRepository(sessions),
                        preferences=PreferenceRepository(sessions),
                        settings=AgentSettingsRepository(sessions),
                        templates=PromptTemplateRepository(sessions),
                        evidence=EvidenceBundleBuilder(
                            sessions=sessions,
                            store=ContentStore(paths.library_root),
                        ),
                        backends=app.state.agent_registry,
                    )
                    app.state.analysis_service = analysis_service
                    for job_id in analysis_service.resumable_job_ids():
                        background_tasks.start(
                            f"analysis-report:{job_id}",
                            partial(
                                resume_analysis_report,
                                analysis_service,
                                job_id,
                            ),
                        )
                    comparison_service = ComparisonService(
                        jobs=JobRepository(sessions),
                        comparisons=ComparisonRepository(sessions),
                        settings=AgentSettingsRepository(sessions),
                        evidence=EvidenceBundleBuilder(
                            sessions=sessions,
                            store=ContentStore(paths.library_root),
                            character_budget=80_000,
                        ),
                        backends=app.state.agent_registry,
                    )
                    app.state.comparison_service = comparison_service
                    for job_id in comparison_service.resumable_job_ids():
                        background_tasks.start(
                            f"comparison-generate:{job_id}",
                            partial(
                                resume_comparison_generation,
                                comparison_service,
                                job_id,
                            ),
                        )
                    lexical_index.ensure_current()
                    PreferenceRepository(sessions).aggregate_expired_events(now=datetime.now(UTC))
                    PlanScheduler(sessions).collect_missed()
                    run_repository = DiscoveryRunRepository(sessions)
                    run_service = DiscoveryRunService(
                        plans=DiscoveryPlanRepository(sessions),
                        keywords=KeywordRepository(sessions),
                        connectors=app.state.connector_registry,
                        identities=IdentityRepository(sessions, work_index=lexical_index),
                        jobs=JobRepository(sessions),
                        runs=run_repository,
                    )
                    recommendation_orchestrator = RecommendationOrchestrator(
                        runs=run_repository,
                        jobs=JobRepository(sessions),
                        relevance=DynamicRunRelevanceScorer(
                            lambda: RelevanceService(
                                sessions,
                                runs=run_repository,
                                lexical=lexical_index,
                                semantic=app.state.semantic_index,
                                semantic_degradation_reasons=tuple(
                                    dict.fromkeys(
                                        (
                                            *app.state.vector_capability.degradation_reasons,
                                            *(
                                                (app.state.vector_runtime_error,)
                                                if app.state.vector_runtime_error is not None
                                                else ()
                                            ),
                                        )
                                    )
                                ),
                            )
                        ),
                        quality=QualityEvidenceBuilder(
                            sessions=sessions,
                            observations=MetadataObservationRepository(sessions),
                            venue_ratings=VenueRatingService(RatingCatalogRepository(sessions)),
                        ),
                        preferences=PreferenceRepository(sessions),
                        rules=ScoreRuleRepository(sessions),
                        snapshots=ScoreSnapshotRepository(sessions),
                        results=RecommendationSetRepository(sessions),
                    )
                    app.state.recommendation_orchestrator = recommendation_orchestrator
                    for job_id in recommendation_orchestrator.resumable_job_ids():
                        background_tasks.start(
                            f"recommendation:{job_id}",
                            partial(
                                resume_recommendation,
                                recommendation_orchestrator,
                                job_id,
                            ),
                        )
                    for run_id in run_repository.list_resumable_ids():
                        background_tasks.start(
                            f"run:{run_id}",
                            partial(
                                _resume_run,
                                run_service,
                                recommendation_orchestrator,
                                run_id,
                            ),
                        )
                    enrichment_service = MetadataEnrichmentService(
                        runs=run_repository,
                        jobs=JobRepository(sessions),
                        lookups=WorkLookupRepository(sessions),
                        observations=MetadataObservationRepository(sessions),
                        enrichers=app.state.enricher_registry,
                    )
                    for job_id in enrichment_service.resumable_job_ids():
                        background_tasks.start(
                            f"metadata-enrichment:{job_id}",
                            partial(
                                _resume_metadata_enrichment,
                                enrichment_service,
                                JobRepository(sessions),
                                recommendation_orchestrator,
                                job_id,
                            ),
                        )
                    acquisition_service = DocumentAcquisitionService(
                        sessions=sessions,
                        jobs=JobRepository(sessions),
                        downloader=OpenPdfDownloader(
                            client=document_http_client,
                            store=ContentStore(paths.library_root),
                            policy=PublicUrlPolicy(),
                        ),
                    )
                    app.state.document_acquisition_service = acquisition_service
                    for job_id in acquisition_service.resumable_job_ids():
                        background_tasks.start(
                            f"reader-acquisition:{job_id}",
                            partial(
                                resume_document_acquisition,
                                acquisition_service,
                                job_id,
                            ),
                        )
                    extraction_service = DocumentExtractionService(
                        sessions=sessions,
                        jobs=JobRepository(sessions),
                        store=ContentStore(paths.library_root),
                        extractor=IsolatedPdfTextExtractor(
                            work_root=paths.cache_root / "native-reader"
                        ),
                    )
                    app.state.document_extraction_service = extraction_service
                    for job_id in extraction_service.resumable_job_ids():
                        background_tasks.start(
                            f"reader-extraction:{job_id}",
                            partial(
                                resume_document_extraction,
                                extraction_service,
                                job_id,
                            ),
                        )
                    ocr_bundle = load_ocr_manifest()
                    ocr_installer = OcrModelInstaller(
                        paths.models / "rapidocr",
                        client=document_http_client,
                    )
                    ocr_model_paths = ocr_installer.model_paths(ocr_bundle)
                    ocr_service = OcrService(
                        sessions=sessions,
                        jobs=JobRepository(sessions),
                        store=ContentStore(paths.library_root),
                        bundle=ocr_bundle,
                        installer=ocr_installer,
                        backend=RapidOcrBackend(model_paths=ocr_model_paths),
                        document_processor=IsolatedOcrDocumentProcessor(
                            model_paths=ocr_model_paths,
                            work_root=paths.cache_root / "native-reader",
                        ),
                        asset_service=model_asset_service,
                    )
                    app.state.ocr_service = ocr_service
                    for job_id in ocr_service.resumable_install_job_ids():
                        background_tasks.start(
                            f"reader-ocr-models:{job_id}",
                            partial(
                                resume_ocr_model_installation,
                                ocr_service,
                                job_id,
                            ),
                        )
                    for job_id in ocr_service.resumable_recognition_job_ids():
                        background_tasks.start(
                            f"reader-ocr:{job_id}",
                            partial(
                                resume_ocr_recognition,
                                ocr_service,
                                job_id,
                            ),
                        )
                    for job_id in backup_service.resumable_job_ids():
                        background_tasks.start(
                            f"backup:{job_id}",
                            partial(resume_backup, backup_service, job_id),
                        )
                    for job_id in publishing_service.resumable_job_ids():
                        background_tasks.start(
                            f"publishing:{job_id}",
                            partial(resume_publication, publishing_service, job_id),
                        )
                    background_tasks.start(
                        "backup-scheduler",
                        partial(
                            run_backup_scheduler,
                            scheduler=backup_scheduler,
                            service=backup_service,
                            tasks=background_tasks,
                        ),
                    )
                    from paper_reader.runtime_migration import complete_data_root_migration

                    complete_data_root_migration(paths)
                    yield
                finally:
                    await background_tasks.shutdown()
        finally:
            await update_service.aclose()
            restore_service.close()
            backup_service.close()
            engine.dispose()

    app = FastAPI(title=PROJECT_NAME, version=__version__, lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def redact_activation_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> Response:
        if request.url.path not in {
            "/api/v1/session/activate",
            "/api/v1/session/bootstrap",
        }:
            return await request_validation_exception_handler(request, error)
        details = [
            {key: value for key, value in item.items() if key not in {"input", "url"}}
            for item in error.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": details},
        )

    app.state.paths = paths
    app.state.runtime_control = control
    app.state.onboarding_service = onboarding_service
    app.state.engine = engine
    app.state.sessions = sessions
    app.state.lexical_index = lexical_index
    app.state.vector_capability = vector_capability
    app.state.vector_runtime_error = None if semantic_index is not None else "unavailable"
    app.state.semantic_index = semantic_index
    bootstrap_tokens = BootstrapTokenStore()
    bootstrap_tokens.issue(token=bootstrap_token)
    app.state.bootstrap_tokens = bootstrap_tokens
    app.state.activation_secret = activation_secret
    app.state.local_sessions = LocalSessionStore()
    app.state.background_tasks = background_tasks
    app.state.credential_store = credentials
    app.state.plugin_installations = plugin_installations
    app.state.plugin_health = plugin_health
    app.state.plugin_sandbox_launcher = plugin_launcher
    app.state.plugin_adapter_refresh = plugin_adapter_refresh
    app.state.plugin_host = plugin_host
    app.state.plugin_catalog = plugin_catalog
    app.state.plugin_adapter_manager = None
    app.state.agent_registry = agent_registry
    app.state.agent_registry_factory = None
    app.state.assistant_service = None
    app.state.translation_service = None
    app.state.translation_exporter = None
    app.state.related_resource_service = None
    app.state.analysis_service = None
    app.state.comparison_service = None
    app.state.model_asset_service = None
    app.state.recommendation_orchestrator = None
    app.state.backup_service = backup_service
    app.state.restore_service = restore_service
    app.state.backup_scheduler = backup_scheduler
    app.state.publishing_service = publishing_service
    app.state.update_service = update_service
    app.state.user_preferences_service = user_preferences_service
    app.state.retention_service = RetentionService(
        sessions=sessions,
        store=ContentStore(paths.library_root),
    )
    app.include_router(keyword_router)
    app.include_router(plan_router)
    app.include_router(run_router)
    app.include_router(paper_update_router)
    app.include_router(work_router)
    app.include_router(settings_router)
    app.include_router(onboarding_router)
    app.include_router(score_router)
    app.include_router(recommendation_result_router)
    app.include_router(briefing_router)
    app.include_router(library_router)
    app.include_router(reader_router)
    app.include_router(assistant_router)
    app.include_router(translation_router)
    app.include_router(related_resource_router)
    app.include_router(analysis_router)
    app.include_router(conversation_router)
    app.include_router(comparison_router)
    app.include_router(backup_router)
    app.include_router(plugin_router)
    app.include_router(publishing_router)
    app.include_router(model_asset_router)
    app.include_router(update_router)
    app.include_router(user_preferences_router)

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        from paper_reader.migrations.versions import FROZEN_DATABASE_HEAD
        from paper_reader.openapi import API_CONTRACT_VERSION, openapi_contract_sha256
        from paper_reader.plugins.contracts import PLUGIN_PROTOCOL_VERSION

        with engine.connect() as connection:
            database_revision = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
        if database_revision != FROZEN_DATABASE_HEAD:
            raise RuntimeError("Database schema does not match the frozen application contract")
        return {
            "name": PROJECT_NAME,
            "version": __version__,
            "status": "ok",
            "api_version": API_CONTRACT_VERSION,
            "openapi_sha256": openapi_contract_sha256(app.openapi()),
            "database_schema_revision": str(database_revision),
            "plugin_protocol_version": PLUGIN_PROTOCOL_VERSION,
        }

    @app.post("/api/v1/session/bootstrap", status_code=status.HTTP_204_NO_CONTENT)
    def bootstrap_session(
        payload: BootstrapRequest,
        response: Response,
        origin: Annotated[str | None, Header(alias="Origin")] = None,
    ) -> None:
        if not is_loopback_origin(origin) or not app.state.bootstrap_tokens.consume(payload.token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid bootstrap",
            )

        response.set_cookie(
            key=SESSION_COOKIE,
            value=app.state.local_sessions.issue(),
            max_age=app.state.local_sessions.lifetime_seconds,
            httponly=True,
            secure=False,
            samesite="strict",
            path="/api/v1",
        )
        response.set_cookie(
            key=CSRF_COOKIE,
            value=token_urlsafe(32),
            max_age=app.state.local_sessions.lifetime_seconds,
            httponly=False,
            secure=False,
            samesite="strict",
            path="/",
        )

    @app.post(
        "/api/v1/session/activate",
        response_model=ActivationResponse,
        responses={
            401: {"description": "Activation secret rejected"},
            403: {"description": "Loopback caller required"},
            404: {"description": "Activation is disabled"},
            413: {"description": "Activation payload exceeds 1 KiB"},
            415: {"description": "JSON request required"},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": ActivationRequest.model_json_schema()}},
            }
        },
    )
    async def activate_instance(
        request: Request,
        response: Response,
        origin: Annotated[str | None, Header(alias="Origin")] = None,
    ) -> ActivationResponse:
        expected = app.state.activation_secret
        if expected is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        if not is_loopback_address(request.client.host if request.client else None) or (
            origin is not None and not is_loopback_origin(origin)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Loopback activation required",
            )
        payload = await _read_activation_request(request)
        if not compare_digest(payload.activation_secret, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid activation",
            )
        issued = app.state.bootstrap_tokens.issue()
        response.headers["Cache-Control"] = "no-store"
        return ActivationResponse(
            bootstrap_token=issued.token,
            expires_in_seconds=app.state.bootstrap_tokens.lifetime_seconds,
            pid=os.getpid(),
        )

    @app.get("/api/v1/jobs", dependencies=[Depends(require_local_session)])
    def list_jobs() -> list[JobSummary]:
        jobs = JobRepository(app.state.sessions).list_jobs()
        return [JobSummary.model_validate(job) for job in jobs]

    @app.get(
        "/api/v1/jobs/{job_id}",
        dependencies=[Depends(require_local_session)],
        response_model=JobDetail,
    )
    def get_job(job_id: str) -> JobDetail:
        job = JobRepository(app.state.sessions).get(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        return JobDetail.model_validate(job)

    @app.post(
        "/api/v1/jobs/{job_id}/cancel",
        dependencies=[Depends(require_local_mutation)],
    )
    def cancel_job(job_id: str) -> JobSummary:
        repository = JobRepository(app.state.sessions)
        current = repository.get(job_id)
        model_service = getattr(app.state, "model_asset_service", None)
        if current is not None and current.kind == "models.install" and model_service is not None:
            job = model_service.cancel(job_id)
        else:
            job = repository.request_cancel(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        return JobSummary.model_validate(job)

    @app.get(
        "/api/v1/events/poll",
        dependencies=[Depends(require_local_session)],
    )
    def poll_events(
        after_event_id: Annotated[int, Query(ge=0)] = 0,
    ) -> list[JobEventSummary]:
        events = JobRepository(app.state.sessions).list_events(after_event_id=after_event_id)
        return [JobEventSummary.model_validate(event) for event in events]

    @app.get(
        "/api/v1/events",
        dependencies=[Depends(require_local_session)],
        response_class=StreamingResponse,
    )
    async def events(
        request: Request,
        last_event_id: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        return StreamingResponse(
            stream_job_events(
                sessions=app.state.sessions,
                request=request,
                after_event_id=last_event_id or 0,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # Windows MIME registrations differ across runner and user installations. Chromium
    # rejects PDF.js' module worker when ``.mjs`` falls back to ``text/plain``.
    mimetypes.add_type("text/javascript", ".mjs", strict=True)
    static_root = (static_dir or discover_runtime_resources().static_root).resolve()
    index_file = static_root / "index.html"
    if index_file.is_file():
        assets_dir = static_root / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def serve_spa(path: str) -> FileResponse:
            if path == "api" or path.startswith("api/"):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

            requested_file = (static_root / path).resolve()
            try:
                requested_file.relative_to(static_root)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None

            if requested_file.is_file():
                return FileResponse(requested_file)
            return FileResponse(index_file)

    return app
