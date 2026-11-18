from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentCapability,
    AgentErrorCode,
    AgentFragment,
    AgentLocation,
    AgentModel,
    AgentProbeCode,
    AgentRequest,
    AgentResult,
    AgentUsage,
    ModelCatalog,
    ServiceTier,
)


def _local_reference() -> dict[str, object]:
    return {
        "evidence_id": "abstract:work",
        "kind": "abstract",
        "page_number": None,
        "locator": None,
        "url": None,
        "external_title": None,
    }


def _comparison_output(work_ids: list[str], *, title: str = "Comparison") -> dict[str, object]:
    references = [
        {"work_id": work_id, **_local_reference()}
        for work_id in work_ids
    ]
    statement = {
        "text": "The papers are compared against their frozen local evidence.",
        "references": references,
    }
    return {
        "title": title,
        "overview": statement,
        "axes": [{"name": "Method", "findings": [statement]}],
        "agreements": [statement],
        "differences": [statement],
        "tradeoffs": [statement],
        "limitations": [statement],
        "recommendations": [statement],
    }


def _paper_extraction(work_id: str) -> dict[str, object]:
    statement = {
        "text": f"Evidence extracted for {work_id}.",
        "references": [_local_reference()],
    }
    return {
        "work_id": work_id,
        "research_question": statement,
        "approach": [statement],
        "findings": [statement],
        "strengths": [statement],
        "limitations": [statement],
    }


class ComparisonBackend:
    def __init__(self) -> None:
        self.capability = AgentCapability(
            backend_id="codex_cli",
            display_name="Comparison fixture",
            location=AgentLocation.CLOUD,
            probe_code=AgentProbeCode.READY,
            installed=True,
            authenticated=True,
            available=True,
            supports_schema=True,
            supports_streaming=True,
            supports_vision=False,
            supports_external_research=False,
        )
        self.requests: list[AgentRequest] = []
        self.fail_final = False
        self.omit_last = False
        self.cancel_on_extraction: int | None = None
        self.cancel_job: Callable[[str], object] | None = None
        self.cancel_and_return_direct = False

    async def discover_models(self) -> ModelCatalog:
        return ModelCatalog(
            models=(AgentModel(model_id="fixture-model", display_name="Fixture model"),),
            supports_manual_model_id=True,
            supports_default_model=True,
        )

    async def generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: Callable[[AgentFragment], Awaitable[None]],
        cancelled: Callable[[], bool],
    ) -> AgentResult:
        self.requests.append(request)
        if "<PAPER_EXTRACTION" in request.prompt:
            extraction_count = sum(
                "<PAPER_EXTRACTION" in item.prompt for item in self.requests
            )
            if extraction_count == self.cancel_on_extraction:
                assert self.cancel_job is not None
                self.cancel_job(request.task_id)
                raise AgentBackendError(
                    AgentErrorCode.CANCELLED,
                    "Comparison was cancelled during extraction",
                )
        if self.fail_final and "<FINAL_COMPARISON" in request.prompt:
            raise AgentBackendError(
                AgentErrorCode.UNAVAILABLE,
                "Comparison synthesis is temporarily unavailable",
                retryable=True,
            )
        if (
            self.cancel_and_return_direct
            and "<PAPER_EXTRACTION" not in request.prompt
            and "<BATCH_COMPARISON" not in request.prompt
            and "<FINAL_COMPARISON" not in request.prompt
        ):
            assert self.cancel_job is not None
            self.cancel_job(request.task_id)
        if not self.cancel_and_return_direct:
            assert cancelled() is False
        await on_fragment(
            AgentFragment(index=0, kind="text", text=f"fragment-{len(self.requests)}")
        )
        work_ids = list(dict.fromkeys(re.findall(r'work_id="([^"]+)"', request.prompt)))
        if "<PAPER_EXTRACTION" in request.prompt:
            output = _paper_extraction(work_ids[0])
        else:
            if self.omit_last and len(work_ids) > 1:
                work_ids = work_ids[:-1]
            output = _comparison_output(work_ids, title=f"Comparison {len(self.requests)}")
        return AgentResult(
            raw_output="{\"comparison\":true}",
            structured_output=output,
            resolved_model_id=request.model_id or "fixture-model",
            usage=AgentUsage(input_tokens=50, output_tokens=25),
        )


def _context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.agents.comparison_service import ComparisonService
    from paper_reader.agents.comparisons import ComparisonRepository
    from paper_reader.agents.evidence import EvidenceBundleBuilder
    from paper_reader.agents.registry import AgentBackendRegistry
    from paper_reader.agents.templates import AgentSettingsRepository
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.models import WorkRecord
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.migrations import upgrade_database
    from paper_reader.reader.storage import ContentStore

    database = tmp_path / "paper-reader.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    with sessions() as session:
        works = [
            WorkRecord(
                id=f"work-{index:02d}",
                canonical_title=f"Comparison paper {index:02d}",
                normalized_title=f"comparison paper {index:02d}",
                authors=[f"Author {index:02d}"],
                abstract=f"Frozen abstract evidence for paper {index:02d}.",
                published_at=datetime(2026, 7, 1, tzinfo=UTC),
            )
            for index in range(1, 13)
        ]
        works.append(
            WorkRecord(
                id="work-alias",
                canonical_title="Merged alias",
                normalized_title="merged alias",
                authors=["Alias"],
                abstract="Alias evidence must never form a duplicate item.",
                status="merged",
                merged_into_work_id="work-01",
            )
        )
        works.append(
            WorkRecord(
                id="work-no-evidence",
                canonical_title="Paper without evidence",
                normalized_title="paper without evidence",
                authors=["Unknown"],
                abstract=None,
            )
        )
        session.add_all(works)
        session.commit()

    backend = ComparisonBackend()
    repository = ComparisonRepository(sessions)
    jobs = JobRepository(sessions)
    service = ComparisonService(
        jobs=jobs,
        comparisons=repository,
        settings=AgentSettingsRepository(sessions),
        evidence=EvidenceBundleBuilder(
            sessions=sessions,
            store=ContentStore(tmp_path / "library"),
            character_budget=80_000,
        ),
        backends=AgentBackendRegistry((backend,)),
    )
    return engine, sessions, backend, jobs, repository, service


def _selection(*work_ids: str):  # type: ignore[no-untyped-def]
    from paper_reader.agents.selections import (
        ComparisonItemSelection,
        ComparisonSelection,
    )

    return ComparisonSelection(
        items=tuple(ComparisonItemSelection(work_id=work_id) for work_id in work_ids),
        model_id="fixture-model",
        user_instruction="Compare methodological assumptions.",
    )


@pytest.mark.asyncio
async def test_preflight_requires_distinct_canonical_works_and_discloses_missing_evidence(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.preflight import ComparisonSelectionConflict
    from paper_reader.agents.selections import ComparisonJobCommand
    from paper_reader.discovery.models import WorkRecord

    engine, sessions, backend, _, _, service = _context(tmp_path)
    try:
        with pytest.raises(ComparisonSelectionConflict, match="duplicate"):
            await service.preflight(_selection("work-01", "work-alias"))

        partial = await service.preflight(
            _selection("work-03", "work-no-evidence", "work-02")
        )
        insufficient = await service.preflight(
            _selection("work-no-evidence", "work-02")
        )
        stable_selection = _selection("work-01", "work-02")
        stale = await service.preflight(stable_selection)
        with sessions() as session:
            work = session.get(WorkRecord, "work-01")
            assert work is not None
            work.abstract = "Evidence changed after the comparison preflight."
            session.commit()
        with pytest.raises(ComparisonSelectionConflict, match="changed"):
            await service.create_job(
                ComparisonJobCommand(
                    selection=stable_selection,
                    preflight_fingerprint=stale.preflight_fingerprint,
                ),
                idempotency_key="comparison-stale-evidence",
            )
    finally:
        engine.dispose()

    assert backend.requests == []
    assert partial.mode == "direct"
    assert partial.ready is True
    assert [item.work_id for item in partial.items] == [
        "work-03",
        "work-no-evidence",
        "work-02",
    ]
    assert [item.position for item in partial.items] == [0, 1, 2]
    assert partial.items[1].status == "unavailable"
    assert partial.items[1].coverage == 0
    assert partial.available_count == 2
    assert partial.coverage < 100
    assert "partial_evidence" in partial.warnings
    assert insufficient.ready is False
    assert "fewer_than_two_usable_papers" in insufficient.warnings


@pytest.mark.asyncio
async def test_direct_comparison_is_one_request_versioned_ordered_and_side_effect_free(
    tmp_path: Path,
) -> None:
    from sqlalchemy import text

    from paper_reader.agents.comparisons import (
        ComparisonConflictError,
        ComparisonItemSnapshot,
        ComparisonProvenance,
    )
    from paper_reader.agents.selections import ComparisonJobCommand

    engine, sessions, backend, jobs, comparisons, service = _context(tmp_path)
    selection = _selection("work-03", "work-no-evidence", "work-01")
    try:
        preflight = await service.preflight(selection)
        command = ComparisonJobCommand(
            selection=selection,
            preflight_fingerprint=preflight.preflight_fingerprint,
        )
        job = await service.create_job(
            command,
            idempotency_key="comparison-direct",
        )
        repeated = await service.create_job(
            command,
            idempotency_key="comparison-direct",
        )
        assert repeated.id == job.id

        with sessions() as session:
            before = {
                table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in (
                    "analysis_reports",
                    "score_snapshots",
                    "library_states",
                    "assistant_messages",
                )
            }
        await service.execute(job.id)
        terminal = jobs.get(job.id)
        assert terminal is not None and terminal.status == "succeeded"
        report = comparisons.get(terminal.result["report_id"])
        assert report is not None
        with sessions() as session:
            after = {
                table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in before
            }
    finally:
        engine.dispose()

    assert len(backend.requests) == 1
    assert backend.requests[0].task_kind == "comparison"
    assert report.mode == "direct"
    assert report.version == 1
    assert [item.work_id for item in report.items] == [
        "work-03",
        "work-no-evidence",
        "work-01",
    ]
    assert [item.extraction_status for item in report.items] == [
        "direct",
        "unavailable",
        "direct",
    ]
    assert report.coverage < 100
    assert before == after
    assert comparisons.list_fragments(job.id)[0].sequence == 0

    conflicting_provenance = ComparisonProvenance(
        job_id=job.id,
        comparison_key=report.comparison_key,
        mode="direct",
        backend_id=report.backend_id,
        requested_model_id=report.requested_model_id,
        resolved_model_id="another-resolved-model",
        reasoning_effort=report.reasoning_effort,
        service_tier=report.service_tier,
        language=report.language,
        complete_prompt=report.complete_prompt,
        request_fingerprint=report.request_fingerprint,
        coverage=report.coverage,
        coverage_details=report.coverage_details,
        provenance=report.provenance,
        items=(
            ComparisonItemSnapshot(
                position=0,
                work_id="work-03",
                evidence=service.evidence.build("work-03"),
                extraction_status="direct",
            ),
            ComparisonItemSnapshot(
                position=1,
                work_id="work-no-evidence",
                evidence=None,
                extraction_status="unavailable",
            ),
            ComparisonItemSnapshot(
                position=2,
                work_id="work-01",
                evidence=service.evidence.build("work-01"),
                extraction_status="direct",
            ),
        ),
    )
    with pytest.raises(ComparisonConflictError, match="another formal report"):
        comparisons.save_formal(conflicting_provenance, report.content)

    from sqlalchemy.exc import IntegrityError

    for statement in (
        "UPDATE comparison_reports SET title = 'changed' WHERE id = :id",
        "DELETE FROM comparison_fragments WHERE job_id = :job_id",
    ):
        with engine.connect() as connection:
            transaction = connection.begin()
            with pytest.raises(IntegrityError, match="immutable"):
                connection.execute(
                    text(statement),
                    {"id": report.id, "job_id": job.id},
                )
            transaction.rollback()


@pytest.mark.asyncio
async def test_eleven_papers_extract_once_then_use_bounded_batches_and_final_synthesis(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.selections import ComparisonJobCommand

    engine, _, backend, jobs, comparisons, service = _context(tmp_path)
    selection = _selection(*(f"work-{index:02d}" for index in range(1, 12)))
    try:
        preflight = await service.preflight(selection)
        assert preflight.mode == "staged"
        assert "staged_comparison" in preflight.warnings
        job = await service.create_job(
            ComparisonJobCommand(
                selection=selection,
                preflight_fingerprint=preflight.preflight_fingerprint,
            ),
            idempotency_key="comparison-staged",
        )
        await service.execute(job.id)
        terminal = jobs.get(job.id)
        assert terminal is not None and terminal.status == "succeeded"
        report = comparisons.get(terminal.result["report_id"])
        assert report is not None
        evidence = comparisons.list_paper_evidence(job.id)
    finally:
        engine.dispose()

    extraction_requests = [
        request for request in backend.requests if "<PAPER_EXTRACTION" in request.prompt
    ]
    batch_requests = [
        request for request in backend.requests if "<BATCH_COMPARISON" in request.prompt
    ]
    final_requests = [
        request for request in backend.requests if "<FINAL_COMPARISON" in request.prompt
    ]
    assert len(extraction_requests) == 11
    assert len(batch_requests) == 2
    assert len(final_requests) == 1
    assert all(request.prompt.count("<EXTRACTED_PAPER ") <= 10 for request in batch_requests)
    assert len(evidence) == 11
    assert all(item.status == "complete" for item in evidence)
    assert report.mode == "staged"
    assert report.coverage == 100
    assert len(report.items) == 11
    assert all(item.extraction_status == "extracted" for item in report.items)
    assert report.provenance["batch_count"] == 2


@pytest.mark.asyncio
async def test_retry_reuses_completed_extractions_and_preserves_earlier_versions(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.selections import ComparisonJobCommand

    engine, _, backend, jobs, comparisons, service = _context(tmp_path)
    selection = _selection(*(f"work-{index:02d}" for index in range(1, 12)))
    try:
        preflight = await service.preflight(selection)
        command = ComparisonJobCommand(
            selection=selection,
            preflight_fingerprint=preflight.preflight_fingerprint,
        )
        initial = await service.create_job(
            command,
            idempotency_key="comparison-initial-version",
        )
        await service.execute(initial.id)
        initial_job = jobs.get(initial.id)
        assert initial_job is not None and initial_job.status == "succeeded"
        initial_report = comparisons.get(initial_job.result["report_id"])
        assert initial_report is not None and initial_report.version == 1

        backend.fail_final = True
        failed = await service.create_job(command, idempotency_key="comparison-failed")
        await service.execute(failed.id)
        assert jobs.get(failed.id).status == "failed"
        completed = comparisons.list_paper_evidence(failed.id)
        assert len(completed) == 11
        extraction_calls = sum(
            "<PAPER_EXTRACTION" in request.prompt for request in backend.requests
        )

        backend.fail_final = False
        retried = await service.create_job(command, idempotency_key="comparison-retry")
        await service.execute(retried.id)
        terminal = jobs.get(retried.id)
        assert terminal is not None and terminal.status == "succeeded"
        report = comparisons.get(terminal.result["report_id"])
        assert report is not None
        reused = comparisons.list_paper_evidence(retried.id)
    finally:
        engine.dispose()

    assert sum(
        "<PAPER_EXTRACTION" in request.prompt for request in backend.requests
    ) == extraction_calls
    assert all(item.reused_from_id is not None for item in reused)
    assert all(item.extraction_status == "reused" for item in report.items)
    history = comparisons.list_for_key(report.comparison_key)
    assert [item.version for item in history] == [2, 1]
    assert history[0].id == report.id
    assert history[1].id == initial_report.id


@pytest.mark.asyncio
async def test_staged_extractions_are_not_reused_across_service_tiers(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.comparisons import (
        ComparisonConflictError,
        ComparisonPaperExtraction,
    )
    from paper_reader.agents.selections import ComparisonJobCommand

    engine, _, backend, jobs, comparisons, service = _context(tmp_path)
    base = _selection(*(f"work-{index:02d}" for index in range(1, 12)))
    try:
        flex_preflight = await service.preflight(base)
        flex_job = await service.create_job(
            ComparisonJobCommand(
                selection=base,
                preflight_fingerprint=flex_preflight.preflight_fingerprint,
            ),
            idempotency_key="comparison-tier-flex",
        )
        await service.execute(flex_job.id)
        assert jobs.get(flex_job.id).status == "succeeded"

        fast = base.model_copy(update={"service_tier": ServiceTier.FAST})
        fast_preflight = await service.preflight(fast)
        fast_job = await service.create_job(
            ComparisonJobCommand(
                selection=fast,
                preflight_fingerprint=fast_preflight.preflight_fingerprint,
            ),
            idempotency_key="comparison-tier-fast",
        )
        await service.execute(fast_job.id)
        fast_evidence = comparisons.list_paper_evidence(fast_job.id)

        assert jobs.get(fast_job.id).status == "succeeded"
        assert sum(
            "<PAPER_EXTRACTION" in request.prompt for request in backend.requests
        ) == 22
        assert all(item.reused_from_id is None for item in fast_evidence)
        assert all(item.resolved_model_id == "fixture-model" for item in fast_evidence)
        assert all(item.service_tier == "fast" for item in fast_evidence)

        changed_payload = _paper_extraction("work-01")
        changed_payload["research_question"] = {
            "text": "A different extraction must not be accepted as an idempotent retry.",
            "references": [_local_reference()],
        }
        with pytest.raises(ComparisonConflictError, match="another outcome"):
            comparisons.save_paper_evidence(
                source_job_id=fast_job.id,
                position=0,
                evidence=service.evidence.build("work-01"),
                backend_id="codex_cli",
                requested_model_id="fixture-model",
                resolved_model_id="fixture-model",
                reasoning_effort=fast_preflight.reasoning_effort.value,
                service_tier="fast",
                prompt_sha256=fast_evidence[0].prompt_sha256,
                extraction=ComparisonPaperExtraction.model_validate(changed_payload),
            )
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_corrupt_frozen_evidence_omitted_papers_and_cancel_never_publish(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.selections import ComparisonJobCommand
    from paper_reader.jobs.models import JobRecord

    engine, sessions, backend, jobs, comparisons, service = _context(tmp_path)
    selection = _selection("work-01", "work-02", "work-03")
    try:
        preflight = await service.preflight(selection)
        command = ComparisonJobCommand(
            selection=selection,
            preflight_fingerprint=preflight.preflight_fingerprint,
        )

        corrupt = await service.create_job(
            command,
            idempotency_key="comparison-corrupt-evidence",
        )
        with sessions() as session:
            record = session.get(JobRecord, corrupt.id)
            assert record is not None
            payload = dict(record.payload)
            items = [dict(item) for item in payload["items"]]
            evidence = dict(items[0]["evidence"])
            work = dict(evidence["work"])
            work["abstract"] = "Tampered without updating the evidence hash."
            evidence["work"] = work
            items[0]["evidence"] = evidence
            payload["items"] = items
            record.payload = payload
            session.commit()
        await service.execute(corrupt.id)
        assert jobs.get(corrupt.id).status == "failed"
        assert comparisons.get_for_job(corrupt.id) is None
        assert backend.requests == []

        current = await service.preflight(selection)
        current_command = ComparisonJobCommand(
            selection=selection,
            preflight_fingerprint=current.preflight_fingerprint,
        )
        backend.omit_last = True
        omitted = await service.create_job(
            current_command,
            idempotency_key="comparison-omitted-paper",
        )
        await service.execute(omitted.id)
        assert jobs.get(omitted.id).status == "failed"
        assert jobs.get(omitted.id).error_code == "invalid_output"
        assert comparisons.get_for_job(omitted.id) is None

        backend.omit_last = False
        cancelled = await service.create_job(
            current_command,
            idempotency_key="comparison-cancelled",
        )
        jobs.request_cancel(cancelled.id)
        calls_before_cancel = len(backend.requests)
        await service.execute(cancelled.id)
        assert jobs.get(cancelled.id).status == "cancelled"
        assert comparisons.get_for_job(cancelled.id) is None
        assert len(backend.requests) == calls_before_cancel
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_cancelling_the_last_staged_extraction_stays_cancelled(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.selections import ComparisonJobCommand

    engine, _, backend, jobs, comparisons, service = _context(tmp_path)
    selection = _selection(*(f"work-{index:02d}" for index in range(1, 12)))
    try:
        preflight = await service.preflight(selection)
        job = await service.create_job(
            ComparisonJobCommand(
                selection=selection,
                preflight_fingerprint=preflight.preflight_fingerprint,
            ),
            idempotency_key="comparison-cancel-last-extraction",
        )
        backend.cancel_on_extraction = 11
        backend.cancel_job = jobs.request_cancel

        await service.execute(job.id)

        terminal = jobs.get(job.id)
        evidence = comparisons.list_paper_evidence(job.id)
        assert terminal is not None and terminal.status == "cancelled"
        assert comparisons.get_for_job(job.id) is None
        assert len(evidence) == 10
        assert all(item.status == "complete" for item in evidence)
        assert not any(
            "<BATCH_COMPARISON" in request.prompt for request in backend.requests
        )
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_backend_result_returned_after_cancel_never_becomes_a_formal_report(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.selections import ComparisonJobCommand

    engine, _, backend, jobs, comparisons, service = _context(tmp_path)
    selection = _selection("work-01", "work-02")
    try:
        preflight = await service.preflight(selection)
        job = await service.create_job(
            ComparisonJobCommand(
                selection=selection,
                preflight_fingerprint=preflight.preflight_fingerprint,
            ),
            idempotency_key="comparison-cancel-result-race",
        )
        backend.cancel_and_return_direct = True
        backend.cancel_job = jobs.request_cancel

        await service.execute(job.id)

        terminal = jobs.get(job.id)
        assert terminal is not None and terminal.status == "cancelled"
        assert comparisons.get_for_job(job.id) is None
        assert comparisons.list_for_key(preflight.comparison_key) == ()
    finally:
        engine.dispose()
