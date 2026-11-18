from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz  # type: ignore[import-untyped]
import pytest

from paper_reader.agents.contracts import (
    AgentCapability,
    AgentFragment,
    AgentLocation,
    AgentModel,
    AgentProbeCode,
    AgentRequest,
    AgentResult,
    AgentUsage,
    ModelCatalog,
)


def valid_structured_report(title: str = "Generated report") -> dict[str, object]:
    reference = {
        "evidence_id": "page:1:native",
        "kind": "page",
        "page_number": 1,
        "locator": None,
        "url": None,
        "external_title": None,
    }
    statement = {
        "text": "The selected page supports this statement.",
        "references": [reference],
    }
    return {
        "title": title,
        "overview": statement,
        "research_question": statement,
        "author_claims": [statement],
        "contributions": [statement],
        "method": [statement],
        "assumptions": [statement],
        "data_and_experiments": [statement],
        "findings": [statement],
        "limitations": [statement],
        "reproducibility": [statement],
        "relation_to_user_intent": [statement],
        "agent_judgments": [statement],
    }


class RecordingBackend:
    def __init__(self) -> None:
        self.capability = AgentCapability(
            backend_id="codex_cli",
            display_name="Codex CLI",
            location=AgentLocation.CLOUD,
            probe_code=AgentProbeCode.READY,
            installed=True,
            authenticated=True,
            available=True,
            supports_schema=True,
            supports_streaming=True,
            supports_vision=True,
            supports_external_research=True,
        )
        self.output: dict[str, object] = valid_structured_report()
        self.generation_calls = 0
        self.discovery_calls = 0
        self.requests: list[AgentRequest] = []

    async def discover_models(self) -> ModelCatalog:
        self.discovery_calls += 1
        return ModelCatalog(
            models=(AgentModel(model_id="fixture-model", display_name="Fixture model"),),
            supports_manual_model_id=True,
            supports_default_model=True,
            degradation_reasons=("model_catalog_partial",),
        )

    async def generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: Callable[[AgentFragment], Awaitable[None]],
        cancelled: Callable[[], bool],
    ) -> AgentResult:
        self.generation_calls += 1
        self.requests.append(request)
        assert cancelled() is False
        await on_fragment(AgentFragment(index=0, kind="reasoning", text="checking"))
        await on_fragment(AgentFragment(index=1, kind="text", text='{"title":'))
        return AgentResult(
            raw_output='{"fixture":true}',
            structured_output=self.output,
            resolved_model_id=request.model_id or "backend-default",
            usage=AgentUsage(input_tokens=100, output_tokens=50),
        )


class NoDefaultModelBackend(RecordingBackend):
    def __init__(self) -> None:
        super().__init__()
        self.capability = AgentCapability(
            backend_id="ollama_local",
            display_name="Ollama Local",
            location=AgentLocation.LOCAL,
            probe_code=AgentProbeCode.READY,
            installed=True,
            authenticated=True,
            available=True,
            supports_schema=True,
            supports_streaming=True,
            supports_vision=True,
            supports_external_research=False,
        )

    async def discover_models(self) -> ModelCatalog:
        self.discovery_calls += 1
        return ModelCatalog(
            models=(),
            supports_manual_model_id=True,
            supports_default_model=False,
        )


class BrokenDiscoveryBackend(RecordingBackend):
    async def discover_models(self) -> ModelCatalog:
        self.discovery_calls += 1
        raise RuntimeError("sensitive discovery failure")


class VisionRecordingBackend(RecordingBackend):
    async def discover_models(self) -> ModelCatalog:
        self.discovery_calls += 1
        return ModelCatalog(
            models=(
                AgentModel(
                    model_id="fixture-model",
                    display_name="Fixture vision model",
                    supports_vision=True,
                ),
            ),
            supports_manual_model_id=True,
            supports_default_model=False,
        )


class PersistOrderJobRepository:  # delegate with an observable event boundary
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.reports: Any = None
        self.fragment_event_checks = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def append_event(self, **kwargs: Any) -> Any:
        if kwargs["event_type"] == "analysis.report.fragment":
            sequence = int(kwargs["payload"]["sequence"])
            assert self.reports.fragment(kwargs["job_id"], sequence) is not None
            self.fragment_event_checks += 1
        return self._delegate.append_event(**kwargs)


def test_analysis_selection_rejects_control_characters_in_identifiers() -> None:
    from pydantic import ValidationError

    from paper_reader.agents.selections import AnalysisSelection

    with pytest.raises(ValidationError, match="control"):
        AnalysisSelection(model_id="unsafe\nmodel")


def _service_context(
    tmp_path: Path,
    backend: RecordingBackend | None = None,
    *,
    visual_pdf: bool = False,
) -> tuple[Any, Any, RecordingBackend, Any, Any, Any]:
    from paper_reader.agents.analysis_service import AnalysisService
    from paper_reader.agents.evidence import EvidenceBundleBuilder
    from paper_reader.agents.registry import AgentBackendRegistry
    from paper_reader.agents.reports import AnalysisReportRepository
    from paper_reader.agents.templates import (
        AgentSettingsRepository,
        PromptTemplateRepository,
    )
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.migrations import upgrade_database
    from paper_reader.reader.models import DocumentPageTextRecord
    from paper_reader.reader.storage import ContentStore
    from paper_reader.recommendation.preferences import PreferenceRepository

    database = tmp_path / "paper-reader.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    store = ContentStore(tmp_path / "library")
    if visual_pdf:
        source_pdf = tmp_path / "visual-source.pdf"
        document_fixture = fitz.open()
        try:
            page_fixture = document_fixture.new_page(width=420, height=595)
            page_fixture.insert_text((36, 48), "Figure 1")
            page_fixture.draw_rect(fitz.Rect(36, 72, 384, 300), width=2)
            document_fixture.save(source_pdf)
        finally:
            document_fixture.close()
        pdf_bytes = source_pdf.read_bytes()
        native_text = "See Figure 1 for the visual result."
    else:
        pdf_bytes = b"%PDF-1.7\nanalysis service fixture\n%%EOF\n"
        native_text = "Stable analysis evidence."
    stored = store.ingest(
        (pdf_bytes,),
        media_type="application/pdf",
        max_bytes=2 * 1024 * 1024,
    )
    with sessions() as session:
        work = WorkRecord(
            id="work-analysis",
            canonical_title="Analysis service fixture",
            normalized_title="analysis service fixture",
            authors=["Alice"],
            abstract="Fallback abstract.",
            published_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        session.add(work)
        session.flush()
        document = DocumentVersionRecord(
            id="document-analysis",
            work_id=work.id,
            source_record_id=None,
            content_sha256=stored.content_sha256,
            access_status="user_provided",
            media_type="application/pdf",
            completeness=100,
            extraction_status="complete",
            relative_path=stored.relative_path,
            byte_size=stored.byte_size,
            acquisition_kind="manual_import",
        )
        session.add(document)
        session.flush()
        session.add(
            DocumentPageTextRecord(
                document_version_id=document.id,
                page_number=1,
                page_width=420,
                page_height=595,
                native_text=native_text,
                native_blocks=[],
                native_character_count=len(native_text),
                likely_scanned=False,
                ocr_state="not_needed",
                ocr_text=None,
                ocr_blocks=[],
                ocr_confidence=None,
                ocr_engine_version=None,
                ocr_model_bundle_id=None,
                extractor_version="fixture",
            )
        )
        session.commit()

    selected_backend = backend or RecordingBackend()
    report_repository = AnalysisReportRepository(sessions)
    tracking_jobs = PersistOrderJobRepository(JobRepository(sessions))
    tracking_jobs.reports = report_repository
    service = AnalysisService(
        jobs=tracking_jobs,  # type: ignore[arg-type]
        reports=report_repository,
        preferences=PreferenceRepository(sessions),
        settings=AgentSettingsRepository(sessions),
        templates=PromptTemplateRepository(sessions),
        evidence=EvidenceBundleBuilder(sessions=sessions, store=store),
        backends=AgentBackendRegistry((selected_backend,)),
    )
    return engine, sessions, selected_backend, tracking_jobs, report_repository, service


def _seed_score_and_ranking_rows(sessions: Any, jobs: Any) -> None:
    """Create non-empty immutable scoring/ranking fixtures for isolation tests."""

    from paper_reader.discovery.models import (
        DiscoveryPlanRecord,
        KeywordConfirmationRecord,
        KeywordSetVersionRecord,
        PlanRunRecord,
        RunBatchRecord,
        RunCandidateRecord,
        ScoreSnapshotRecord,
        SourceRecord,
    )

    now = datetime(2026, 7, 14, 8, tzinfo=UTC)
    discovery_job = jobs.create(
        kind="discovery.run",
        payload={"fixture": "agent-isolation"},
        idempotency_key="agent-isolation-discovery",
    )
    with sessions() as session:
        keyword = KeywordSetVersionRecord(
            id="keyword-agent-isolation",
            research_intent="Agent isolation acceptance fixture",
            required_groups=[{"key": "agent", "label": "Agent", "terms": ["agent"]}],
            boost_groups=[],
            excluded_groups=[],
            change_summary={},
            generator_info={"backend": "fixture"},
            created_at=now,
        )
        plan = DiscoveryPlanRecord(
            id="plan-agent-isolation",
            name="Agent isolation plan",
            research_intent=keyword.research_intent,
            keyword_version_id=keyword.id,
            date_mode="fixed",
            start_month="2026-07",
            end_month="2026-07",
            rolling_months=None,
            timezone="Asia/Shanghai",
            sources=["arxiv"],
            source_filters={},
            schedule_frequency="manual",
            schedule_checked_at=now,
            created_at=now,
            updated_at=now,
        )
        source = SourceRecord(
            id="source-agent-isolation",
            work_id="work-analysis",
            connector_id="arxiv",
            external_id="2607.99999",
            title="Analysis service fixture",
            normalized_title="analysis service fixture",
            authors=["Alice"],
            abstract="Ranking fixture that must remain immutable.",
            field_provenance={"title": "arxiv"},
            raw_payload={"fixture": True},
            fetched_at=now,
        )
        run = PlanRunRecord(
            id="run-agent-isolation",
            plan_id=plan.id,
            job_id=discovery_job.id,
            keyword_version_id=keyword.id,
            status="succeeded",
            months=["2026-07"],
            plan_snapshot={"name": plan.name},
            keyword_snapshot={"version_id": keyword.id},
            connector_versions={"arxiv": "fixture-v1"},
            total_batches=1,
            succeeded_batches=1,
            failed_batches=0,
            cancelled_batches=0,
            created_at=now,
            started_at=now,
            completed_at=now,
        )
        batch = RunBatchRecord(
            id="batch-agent-isolation",
            run_id=run.id,
            month="2026-07",
            connector_id="arxiv",
            status="succeeded",
            query_fingerprint="1" * 64,
            checkpoint={"cursor": None},
            fetched_count=1,
            accepted_count=1,
            ingested_count=1,
            attempt_count=1,
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
        session.add(keyword)
        session.flush()
        session.add_all(
            [
                KeywordConfirmationRecord(keyword_version_id=keyword.id, confirmed_at=now),
                plan,
                source,
            ]
        )
        session.flush()
        session.add(run)
        session.flush()
        session.add(batch)
        session.flush()
        session.add_all(
            [
                RunCandidateRecord(
                    id="candidate-agent-isolation",
                    run_id=run.id,
                    batch_id=batch.id,
                    work_id="work-analysis",
                    source_record_id=source.id,
                    match_kind="new",
                    created_at=now,
                ),
                ScoreSnapshotRecord(
                    id="score-agent-isolation",
                    work_id="work-analysis",
                    run_id=run.id,
                    context_key="run:agent-isolation",
                    score_type="composite",
                    rule_version="ranking-v1",
                    score=87.5,
                    coverage=92.0,
                    confidence=89.0,
                    components=[{"dimension": "quality", "value": 90.0}],
                    weights={"quality": 0.4, "relevance": 0.4},
                    missing_policy={"unknown": "neutral_prior"},
                    evidence_observation_ids=[],
                    hard_constraints={"retracted": False},
                    snapshot_sha256="2" * 64,
                    created_at=now,
                ),
            ]
        )
        session.commit()


def _table_rows_bytes(sessions: Any, table: str) -> bytes:
    from sqlalchemy import text

    if table not in {"score_snapshots", "run_candidates"}:
        raise ValueError("unsupported acceptance table")
    with sessions() as session:
        rows = [list(row) for row in session.execute(text(f"SELECT * FROM {table} ORDER BY id"))]
    return json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize(("include_visual_evidence", "expected_count"), [(True, 1), (False, 0)])
async def test_analysis_sends_only_frozen_visual_pages_to_a_confirmed_vision_model(
    tmp_path: Path,
    include_visual_evidence: bool,
    expected_count: int,
) -> None:
    from paper_reader.agents.selections import (
        AnalysisJobCommand,
        AnalysisSelection,
    )

    backend = VisionRecordingBackend()
    engine, _, _, jobs, _, service = _service_context(
        tmp_path,
        backend,
        visual_pdf=True,
    )
    selection = AnalysisSelection(
        document_version_id="document-analysis",
        model_id="fixture-model",
        include_visual_evidence=include_visual_evidence,
    )
    try:
        preflight = await service.preflight("work-analysis", selection)
        job = await service.create_job(
            "work-analysis",
            AnalysisJobCommand(
                selection=selection,
                preflight_fingerprint=preflight.preflight_fingerprint,
            ),
            idempotency_key=f"analysis-visual-{include_visual_evidence}",
        )
        await service.execute(job.id)
        completed = jobs.get(job.id)
    finally:
        engine.dispose()

    assert completed is not None
    assert completed.status == "succeeded"
    assert f"visual-pages:{expected_count}" in preflight.scope
    assert len(backend.requests) == 1
    attachments = backend.requests[0].attachments
    assert len(attachments) == expected_count
    if attachments:
        assert preflight.document_content_sha256 is not None
        assert attachments[0].media_type == "image/png"
        assert attachments[0].name.startswith(
            f"visual-{preflight.document_content_sha256[:16]}-p0001"
        )
        assert attachments[0].content.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_preflight_is_read_only_complete_and_never_invokes_generation(
    tmp_path: Path,
) -> None:
    from sqlalchemy import func, select

    from paper_reader.agents.selections import AnalysisSelection
    from paper_reader.jobs.models import JobRecord

    engine, sessions, backend, _, _, service = _service_context(tmp_path)
    selection = AnalysisSelection(
        document_version_id="document-analysis",
        model_id="fixture-model",
        research_intent="Review robustness",
    )
    try:
        with sessions() as session:
            before = session.scalar(select(func.count()).select_from(JobRecord))
        first = await service.preflight("work-analysis", selection)
        repeated = await service.preflight("work-analysis", selection)
        with sessions() as session:
            after = session.scalar(select(func.count()).select_from(JobRecord))
    finally:
        engine.dispose()

    assert first == repeated
    assert before == after == 0
    assert first.ready is True
    assert first.work_id == "work-analysis"
    assert first.document_version_id == "document-analysis"
    assert first.document_content_sha256 is not None
    assert first.evidence_level == "full_text"
    assert first.backend_id == "codex_cli"
    assert first.backend_location == "cloud"
    assert first.cloud_backend is True
    assert first.model_id == "fixture-model"
    assert first.reasoning_effort == "high"
    assert first.service_tier == "flex"
    assert first.language == "zh-CN"
    assert first.template_version_id == "builtin-general-v1"
    assert first.external_research is False
    assert first.coverage == 100
    assert "model_catalog_partial" in first.warnings
    assert len(first.evidence_sha256) == 64
    assert len(first.prompt_sha256) == 64
    assert len(first.preflight_fingerprint) == 64
    assert backend.discovery_calls == 2
    assert backend.generation_calls == 0


@pytest.mark.asyncio
async def test_model_discovery_failure_allows_a_manual_model_without_leaking_error(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.selections import AnalysisSelection

    selected_backend = BrokenDiscoveryBackend()
    engine, _, backend, _, _, service = _service_context(tmp_path, selected_backend)
    try:
        preflight = await service.preflight(
            "work-analysis",
            AnalysisSelection(
                document_version_id="document-analysis",
                model_id="manual-model-id",
            ),
        )
    finally:
        engine.dispose()

    assert preflight.ready is True
    assert preflight.manual_model_id_allowed is True
    assert preflight.model_options == ()
    assert "model_discovery_failed" in preflight.warnings
    assert "manual_model_id" in preflight.warnings
    assert "sensitive discovery failure" not in str(preflight)
    assert backend.generation_calls == 0


@pytest.mark.asyncio
async def test_create_requires_current_preflight_and_idempotency_payload_match(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.preflight import AnalysisPreflightConflict
    from paper_reader.agents.selections import (
        AnalysisJobCommand,
        AnalysisSelection,
    )
    from paper_reader.reader.models import DocumentPageTextRecord

    engine, sessions, backend, _, _, service = _service_context(tmp_path)
    selection = AnalysisSelection(document_version_id="document-analysis")
    try:
        stale = await service.preflight("work-analysis", selection)
        with sessions() as session:
            page = session.get(DocumentPageTextRecord, ("document-analysis", 1))
            assert page is not None
            page.native_text += " Changed."
            page.native_character_count = len(page.native_text)
            session.commit()
        with pytest.raises(AnalysisPreflightConflict, match="changed"):
            await service.create_job(
                "work-analysis",
                AnalysisJobCommand(
                    selection=selection,
                    preflight_fingerprint=stale.preflight_fingerprint,
                ),
                idempotency_key="analysis-idempotent",
            )

        current = await service.preflight("work-analysis", selection)
        command = AnalysisJobCommand(
            selection=selection,
            preflight_fingerprint=current.preflight_fingerprint,
        )
        first = await service.create_job(
            "work-analysis",
            command,
            idempotency_key="analysis-idempotent",
        )
        repeated = await service.create_job(
            "work-analysis",
            command,
            idempotency_key="analysis-idempotent",
        )
        with sessions() as session:
            page = session.get(DocumentPageTextRecord, ("document-analysis", 1))
            assert page is not None
            page.native_text += " Changed again."
            page.native_character_count = len(page.native_text)
            session.commit()
        repeated_after_change = await service.create_job(
            "work-analysis",
            command,
            idempotency_key="analysis-idempotent",
        )
        english_selection = selection.model_copy(update={"language": "en"})
        english = await service.preflight("work-analysis", english_selection)
        with pytest.raises(AnalysisPreflightConflict, match="idempotency"):
            await service.create_job(
                "work-analysis",
                AnalysisJobCommand(
                    selection=english_selection,
                    preflight_fingerprint=english.preflight_fingerprint,
                ),
                idempotency_key="analysis-idempotent",
            )
    finally:
        engine.dispose()

    assert first.id == repeated.id == repeated_after_change.id
    assert first.kind == "analysis.report"
    assert first.status == "queued"
    assert first.payload["preflight_fingerprint"] == current.preflight_fingerprint
    assert "credential" not in str(first.payload).casefold()
    assert backend.generation_calls == 0


@pytest.mark.asyncio
async def test_worker_persists_fragments_formal_report_and_only_permitted_preferences(
    tmp_path: Path,
) -> None:
    from sqlalchemy import func, select

    from paper_reader.agents.selections import (
        AnalysisJobCommand,
        AnalysisSelection,
    )
    from paper_reader.discovery.models import RunCandidateRecord, ScoreSnapshotRecord
    from paper_reader.recommendation.preferences import (
        AnalysisState,
        LibraryFeedback,
        LibraryStateInput,
        PreferenceRepository,
    )

    engine, sessions, backend, jobs, reports, service = _service_context(tmp_path)
    preferences = PreferenceRepository(sessions)
    preferences.set_library_state(
        "work-analysis",
        LibraryStateInput(
            favorite=True,
            feedback=LibraryFeedback.LIKE,
            feedback_reasons=("method",),
            pinned=True,
            reading_progress=42,
            analysis_state=AnalysisState.NOT_ANALYZED,
        ),
    )
    selection = AnalysisSelection(
        document_version_id="document-analysis",
        model_id="fixture-model",
        temporary_instruction="Focus on assumptions.",
    )
    try:
        preflight = await service.preflight("work-analysis", selection)
        job = await service.create_job(
            "work-analysis",
            AnalysisJobCommand(
                selection=selection,
                preflight_fingerprint=preflight.preflight_fingerprint,
            ),
            idempotency_key="analysis-success",
        )
        assert backend.generation_calls == 0
        with sessions() as session:
            score_count = session.scalar(select(func.count()).select_from(ScoreSnapshotRecord))
            rank_count = session.scalar(select(func.count()).select_from(RunCandidateRecord))
        await service.execute(job.id)
        await service.execute(job.id)
        completed = jobs.get(job.id)
        stored_reports = reports.list_for_work("work-analysis")
        fragments = reports.list_fragments(job.id)
        state = preferences.get_library_state("work-analysis")
        events = preferences.list_raw_events("work-analysis")
        with sessions() as session:
            score_after = session.scalar(select(func.count()).select_from(ScoreSnapshotRecord))
            rank_after = session.scalar(select(func.count()).select_from(RunCandidateRecord))
    finally:
        engine.dispose()

    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.progress == 100
    assert len(stored_reports) == 1
    assert stored_reports[0].job_id == job.id
    assert stored_reports[0].content.title == "Generated report"
    assert [item.sequence for item in fragments] == [0, 1]
    assert jobs.fragment_event_checks == 2
    assert backend.generation_calls == 1
    assert backend.requests[0].task_kind == "report"
    assert backend.requests[0].model_id == "fixture-model"
    assert backend.requests[0].external_research is False
    assert state.favorite is True
    assert state.feedback is LibraryFeedback.LIKE
    assert state.feedback_reasons == ("method",)
    assert state.pinned is True
    assert state.reading_progress == 42
    assert state.analysis_state is AnalysisState.AVAILABLE
    assert [event.event_type.value for event in events] == ["analysis_report_generated"]
    assert score_count == score_after == 0
    assert rank_count == rank_after == 0


@pytest.mark.asyncio
async def test_success_and_failure_leave_score_and_ranking_rows_byte_identical(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.selections import (
        AnalysisJobCommand,
        AnalysisSelection,
    )

    engine, sessions, backend, jobs, _, service = _service_context(tmp_path)
    selection = AnalysisSelection(
        document_version_id="document-analysis",
        model_id="fixture-model",
    )
    try:
        _seed_score_and_ranking_rows(sessions, jobs)
        score_before = _table_rows_bytes(sessions, "score_snapshots")
        ranking_before = _table_rows_bytes(sessions, "run_candidates")
        assert json.loads(score_before)
        assert json.loads(ranking_before)

        successful_preflight = await service.preflight("work-analysis", selection)
        successful = await service.create_job(
            "work-analysis",
            AnalysisJobCommand(
                selection=selection,
                preflight_fingerprint=successful_preflight.preflight_fingerprint,
            ),
            idempotency_key="analysis-isolation-success",
        )
        await service.execute(successful.id)
        score_after_success = _table_rows_bytes(sessions, "score_snapshots")
        ranking_after_success = _table_rows_bytes(sessions, "run_candidates")

        backend.output = {"title": "invalid structured report"}
        failed_preflight = await service.preflight("work-analysis", selection)
        failed = await service.create_job(
            "work-analysis",
            AnalysisJobCommand(
                selection=selection,
                preflight_fingerprint=failed_preflight.preflight_fingerprint,
            ),
            idempotency_key="analysis-isolation-failure",
        )
        await service.execute(failed.id)
        score_after_failure = _table_rows_bytes(sessions, "score_snapshots")
        ranking_after_failure = _table_rows_bytes(sessions, "run_candidates")
        successful_state = jobs.get(successful.id)
        failed_state = jobs.get(failed.id)
    finally:
        engine.dispose()

    assert successful_state is not None and successful_state.status == "succeeded"
    assert failed_state is not None and failed_state.status == "failed"
    assert score_before == score_after_success == score_after_failure
    assert ranking_before == ranking_after_success == ranking_after_failure


@pytest.mark.asyncio
async def test_failure_draft_preserves_old_formal_report_and_available_state(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.selections import (
        AnalysisJobCommand,
        AnalysisSelection,
    )
    from paper_reader.recommendation.preferences import AnalysisState, PreferenceRepository

    engine, sessions, backend, jobs, reports, service = _service_context(tmp_path)
    selection = AnalysisSelection(document_version_id="document-analysis")
    try:
        first_preflight = await service.preflight("work-analysis", selection)
        first = await service.create_job(
            "work-analysis",
            AnalysisJobCommand(
                selection=selection,
                preflight_fingerprint=first_preflight.preflight_fingerprint,
            ),
            idempotency_key="analysis-formal",
        )
        await service.execute(first.id)
        backend.output = {"unexpected": "invalid"}
        second_preflight = await service.preflight("work-analysis", selection)
        second = await service.create_job(
            "work-analysis",
            AnalysisJobCommand(
                selection=selection,
                preflight_fingerprint=second_preflight.preflight_fingerprint,
            ),
            idempotency_key="analysis-failure",
        )
        await service.execute(second.id)
        await service.execute(second.id)

        failed_job = jobs.get(second.id)
        failure = reports.failure_for_job(second.id)
        formal_reports = reports.list_for_work("work-analysis")
        state = PreferenceRepository(sessions).get_library_state("work-analysis")
        implicit_events = PreferenceRepository(sessions).list_raw_events("work-analysis")
    finally:
        engine.dispose()

    assert failed_job is not None
    assert failed_job.status == "failed"
    assert failure is not None
    assert failure.error_category == "invalid_output"
    assert len(failure.validation_errors) > 0
    assert [item.job_id for item in formal_reports] == [first.id]
    assert state.analysis_state is AnalysisState.AVAILABLE
    assert len(implicit_events) == 1
    assert backend.generation_calls == 2


@pytest.mark.asyncio
async def test_cancelled_job_and_missing_required_model_never_call_backend(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.preflight import AnalysisNotReadyError
    from paper_reader.agents.registry import AgentBackendRegistry
    from paper_reader.agents.selections import (
        AnalysisJobCommand,
        AnalysisSelection,
    )

    engine, _, backend, jobs, reports, service = _service_context(tmp_path)
    try:
        selection = AnalysisSelection(document_version_id="document-analysis")
        preflight = await service.preflight("work-analysis", selection)
        job = await service.create_job(
            "work-analysis",
            AnalysisJobCommand(
                selection=selection,
                preflight_fingerprint=preflight.preflight_fingerprint,
            ),
            idempotency_key="analysis-cancel",
        )
        jobs.request_cancel(job.id)
        await service.execute(job.id)
        cancelled = jobs.get(job.id)

        no_model_backend = NoDefaultModelBackend()
        service.backends = AgentBackendRegistry((no_model_backend,))
        no_model_selection = AnalysisSelection(
            document_version_id="document-analysis",
            backend_id="ollama_local",
        )
        no_model = await service.preflight("work-analysis", no_model_selection)
        with pytest.raises(AnalysisNotReadyError, match="model"):
            await service.create_job(
                "work-analysis",
                AnalysisJobCommand(
                    selection=no_model_selection,
                    preflight_fingerprint=no_model.preflight_fingerprint,
                ),
                idempotency_key="analysis-no-model",
            )
    finally:
        engine.dispose()

    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert reports.failure_for_job(job.id) is None
    assert backend.generation_calls == 0
    assert no_model.ready is False
    assert "model_required" in no_model.warnings
    assert no_model_backend.generation_calls == 0


@pytest.mark.asyncio
async def test_tampered_frozen_payload_fails_before_backend_invocation(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.selections import (
        AnalysisJobCommand,
        AnalysisSelection,
    )
    from paper_reader.jobs.models import JobRecord

    engine, sessions, backend, jobs, reports, service = _service_context(tmp_path)
    try:
        selection = AnalysisSelection(document_version_id="document-analysis")
        preflight = await service.preflight("work-analysis", selection)
        job = await service.create_job(
            "work-analysis",
            AnalysisJobCommand(
                selection=selection,
                preflight_fingerprint=preflight.preflight_fingerprint,
            ),
            idempotency_key="analysis-tampered",
        )
        with sessions() as session:
            record = session.get(JobRecord, job.id)
            assert record is not None
            payload = dict(record.payload)
            payload["complete_prompt"] = f"{payload['complete_prompt']}\nINJECTED"
            record.payload = payload
            session.commit()

        await service.execute(job.id)
        failed = jobs.get(job.id)
        draft = reports.failure_for_job(job.id)
    finally:
        engine.dispose()

    assert failed is not None
    assert failed.status == "failed"
    assert draft is not None
    assert draft.error_category == "analysis_internal_error"
    assert draft.error_message == "Analysis execution failed safely"
    assert draft.validation_errors == ("ValueError",)
    assert "INJECTED" not in str(draft)
    assert backend.generation_calls == 0


@pytest.mark.asyncio
async def test_restart_appends_after_persisted_partial_attempt(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.selections import (
        AnalysisJobCommand,
        AnalysisSelection,
    )

    engine, _, backend, jobs, reports, service = _service_context(tmp_path)
    try:
        selection = AnalysisSelection(document_version_id="document-analysis")
        preflight = await service.preflight("work-analysis", selection)
        job = await service.create_job(
            "work-analysis",
            AnalysisJobCommand(
                selection=selection,
                preflight_fingerprint=preflight.preflight_fingerprint,
            ),
            idempotency_key="analysis-partial-restart",
        )
        reports.append_fragment(
            job_id=job.id,
            sequence=0,
            kind="text",
            text="partial fragment from interrupted attempt",
        )

        await service.execute(job.id)
        completed = jobs.get(job.id)
        fragments = reports.list_fragments(job.id)
        formal = reports.report_for_job(job.id)
    finally:
        engine.dispose()

    assert completed is not None
    assert completed.status == "succeeded"
    assert formal is not None
    assert [item.sequence for item in fragments] == [0, 1, 2]
    assert fragments[0].text == "partial fragment from interrupted attempt"
    assert fragments[1].text == "checking"
    assert backend.generation_calls == 1


@pytest.mark.asyncio
async def test_worker_rechecks_authentication_before_model_invocation(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from paper_reader.agents.selections import (
        AnalysisJobCommand,
        AnalysisSelection,
    )

    engine, _, backend, jobs, reports, service = _service_context(tmp_path)
    try:
        selection = AnalysisSelection(document_version_id="document-analysis")
        preflight = await service.preflight("work-analysis", selection)
        job = await service.create_job(
            "work-analysis",
            AnalysisJobCommand(
                selection=selection,
                preflight_fingerprint=preflight.preflight_fingerprint,
            ),
            idempotency_key="analysis-auth-changed",
        )
        backend.capability = replace(
            backend.capability,
            authenticated=False,
            available=True,
        )

        await service.execute(job.id)
        failed = jobs.get(job.id)
        draft = reports.failure_for_job(job.id)
    finally:
        engine.dispose()

    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_code == "authentication"
    assert draft is not None
    assert draft.error_category == "authentication"
    assert backend.generation_calls == 0


@pytest.mark.parametrize(
    ("module_name", "symbol_name"),
    (
        ("preflight", "AnalysisPreflightConflict"),
        ("preflight", "AnalysisNotReadyError"),
        ("preflight", "ComparisonNotReadyError"),
        ("preflight", "ComparisonSelectionConflict"),
        ("selections", "ComparisonItemSelection"),
        ("selections", "ComparisonSelection"),
        ("preflight", "PreflightModel"),
        ("preflight", "ComparisonPreflightItem"),
        ("preflight", "ComparisonPreflight"),
        ("selections", "ComparisonJobCommand"),
        ("selections", "AnalysisSelection"),
        ("selections", "AnalysisJobCommand"),
        ("preflight", "AnalysisPreflight"),
        ("analysis_service", "AnalysisService"),
        ("comparison_service", "ComparisonService"),
    ),
)
def test_agent_service_public_class_surface_is_characterized(
    module_name: str,
    symbol_name: str,
) -> None:
    """Freeze every class moved out of the pre-split service module."""

    import importlib
    import inspect

    module = importlib.import_module(f"paper_reader.agents.{module_name}")

    assert inspect.isclass(getattr(module, symbol_name))
