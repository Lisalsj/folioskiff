from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from paper_reader.discovery.identity import IdentityRepository
from paper_reader.discovery.models import WorkRecord
from paper_reader.jobs.repository import JobRepository
from paper_reader.recommendation.preferences import PreferenceRepository
from paper_reader.related_resources.academic import (
    AcademicCandidate,
    AcademicCandidatePage,
    AcademicSearchSummary,
    AcademicSourceSummary,
)
from paper_reader.related_resources.contracts import PaperVerificationStatus
from paper_reader.related_resources.models import (
    RelatedPaperCandidateRecord,
    RelatedRunSourceRecord,
)
from paper_reader.related_resources.service import (
    RelatedResourceService,
    RelatedRunConflict,
    RelatedRunOptions,
)
from paper_reader.related_resources.web_research import (
    KnowledgeResearchResult,
    KnowledgeResourceRejection,
    VerifiedKnowledgeResource,
)

RelatedIntentTestContext = Any
NOW = datetime(2026, 8, 1, 9, tzinfo=UTC)


class RecordingAcademicSearch:
    def __init__(self, *, fail_source: str | None = None) -> None:
        self.fail_source = fail_source
        self.calls: list[tuple[tuple[str, ...], int]] = []

    async def search(
        self,
        intent: Any,
        on_page: Callable[[AcademicCandidatePage], Awaitable[None]],
        cancelled: Callable[[], bool],
        *,
        source_ids: tuple[str, ...] | None = None,
        candidate_limit: int = 50,
    ) -> AcademicSearchSummary:
        selected = source_ids or intent.source_ids
        self.calls.append((selected, candidate_limit))
        summaries: dict[str, AcademicSourceSummary] = {}
        candidates: list[AcademicCandidate] = []
        for source_id in selected:
            if cancelled():
                summaries[source_id] = AcademicSourceSummary(
                    connector_id=source_id,
                    status="cancelled",
                    stop_reason="cancelled",
                )
                continue
            if source_id == self.fail_source:
                summaries[source_id] = AcademicSourceSummary(
                    connector_id=source_id,
                    status="failed",
                    error_code="rate_limited",
                    error_message="Fixture source was rate limited",
                    retryable=True,
                    retry_after_seconds=5,
                )
                continue
            candidate = AcademicCandidate(
                connector_id=source_id,
                external_id=f"{source_id}-related-1",
                raw_doi="10.1234/related.1",
                normalized_doi="10.1234/related.1",
                title="Diffusion model planning with verified evidence",
                authors=("Related Researcher",),
                published_at=NOW,
                updated_at=NOW,
                landing_url=f"https://example.org/{source_id}/related-1",
                publication_year=2026,
                linked_work_id="work-related-target",
                merge_review_id=None,
                match_kind="exact_doi",
                reason="Matched required group: diffusion model",
                match_evidence={"required_groups": ["diffusion model"]},
                confidence=91,
                verification_status=PaperVerificationStatus.VERIFIED,
                field_provenance={
                    "title": f"{source_id}:title",
                    "authors": f"{source_id}:authors",
                },
                cited_by_current_paper=False,
            )
            page = AcademicCandidatePage(
                connector_id=source_id,
                page_number=1,
                cursor=None,
                next_cursor=None,
                fetched_count=1,
                candidates=(candidate,),
            )
            await on_page(page)
            candidates.append(candidate)
            summaries[source_id] = AcademicSourceSummary(
                connector_id=source_id,
                status="succeeded",
                pages_fetched=1,
                fetched_count=1,
                accepted_count=1,
                candidate_count=1,
                stop_reason="source_exhausted",
            )
        return AcademicSearchSummary(
            set_id=intent.set_id,
            intent_revision=intent.revision,
            months=("2026-08",),
            sources=summaries,
            candidates=tuple(candidates),
            display_candidates=tuple(candidates),
            total_fetched=len(candidates),
            partial=any(item.status != "succeeded" for item in summaries.values()),
            cancelled=any(item.status == "cancelled" for item in summaries.values()),
        )


class RecordingKnowledgeResearch:
    def __init__(self, *, partial: bool = False) -> None:
        self.calls = 0
        self.partial = partial

    async def run(
        self,
        intent: Any,
        snapshot: Any,
        selection: Any,
        *,
        cancelled: Callable[[], bool],
    ) -> KnowledgeResearchResult:
        self.calls += 1
        assert cancelled() is False
        rejections = (
            (
                KnowledgeResourceRejection(
                    code="unsafe_dns",
                    title="Rejected private resource",
                    url="https://private.invalid/resource",
                    retryable=False,
                ),
            )
            if self.partial
            else ()
        )
        return KnowledgeResearchResult(
            set_id=intent.set_id,
            intent_revision=intent.revision,
            resources=(
                VerifiedKnowledgeResource(
                    title="Official diffusion documentation",
                    canonical_url="https://docs.example.org/diffusion",
                    final_url="https://docs.example.org/diffusion",
                    domain="docs.example.org",
                    resource_type="official_docs",
                    reason="Primary documentation for the paper method.",
                    publisher="Example Lab",
                    verification_status="verified",
                    http_status=200,
                    accessed_at=NOW,
                    source_quality="preferred",
                    field_provenance={"title": "agent:related_external"},
                ),
            ),
            rejections=rejections,
            rejected_count=len(rejections),
            partial=self.partial,
            truncated=False,
            backend_id=selection.backend_id,
            requested_model_id=selection.model_id,
            resolved_model_id=selection.model_id,
            reasoning_effort=selection.reasoning_effort,
            service_tier=selection.service_tier,
            external_research=True,
            completed_at=NOW,
            prompt_sha256="a" * 64,
            raw_output_sha256="b" * 64,
        )


def _confirmed_intent(
    context: RelatedIntentTestContext,
    *,
    source_ids: tuple[str, ...] = ("arxiv", "openreview"),
) -> Any:
    draft = context.service.draft(context.work_id, context.snapshot_id)
    edited = draft.model_copy(
        update={
            "summary": "Diffusion models with planning agents",
            "required_groups": (("diffusion model", "扩散模型"),),
            "optional_terms": ("planning agent", "规划智能体", "evaluation", "评估"),
            "source_ids": source_ids,
        }
    )
    return context.service.confirm(draft.set_id, revision=draft.revision, intent=edited)


def _resource_service(
    context: RelatedIntentTestContext,
    *,
    academic: RecordingAcademicSearch | None = None,
    knowledge: RecordingKnowledgeResearch | None = None,
) -> tuple[RelatedResourceService, RecordingAcademicSearch, RecordingKnowledgeResearch]:
    academic_backend = academic or RecordingAcademicSearch()
    knowledge_backend = knowledge or RecordingKnowledgeResearch()
    service = RelatedResourceService(
        sessions=context.sessions,
        jobs=JobRepository(context.sessions),
        intents=context.repository,
        intent_service=context.service,
        academic=academic_backend,
        knowledge=knowledge_backend,
        identities=IdentityRepository(context.sessions),
        preferences=PreferenceRepository(context.sessions, clock=lambda: NOW),
        clock=lambda: NOW,
    )
    return service, academic_backend, knowledge_backend


def _seed_target_work(context: RelatedIntentTestContext) -> None:
    with context.sessions() as session:
        session.add(
            WorkRecord(
                id="work-related-target",
                canonical_title="Diffusion model planning with verified evidence",
                normalized_title="diffusion model planning with verified evidence",
                authors=["Related Researcher"],
                doi="10.1234/related.1",
            )
        )
        session.commit()


def test_start_requires_current_confirmed_revision_and_is_idempotent(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    draft = context.service.draft(context.work_id, context.snapshot_id)
    service, academic, knowledge = _resource_service(context)

    with pytest.raises(RelatedRunConflict, match="confirmed"):
        service.start(
            draft.set_id,
            RelatedRunOptions(intent_revision=draft.revision),
            idempotency_key="related-run-unconfirmed",
        )

    confirmed = context.service.confirm(draft.set_id, revision=draft.revision, intent=draft)
    first = service.start(
        confirmed.set_id,
        RelatedRunOptions(intent_revision=confirmed.revision),
        idempotency_key="related-run-idempotent",
    )
    replay = service.start(
        confirmed.set_id,
        RelatedRunOptions(intent_revision=confirmed.revision),
        idempotency_key="related-run-idempotent",
    )

    assert first.job_id == replay.job_id
    assert replay.replayed is True
    assert academic.calls == []
    assert knowledge.calls == 0
    with pytest.raises(RelatedRunConflict, match="already has"):
        service.start(
            confirmed.set_id,
            RelatedRunOptions(intent_revision=confirmed.revision),
            idempotency_key="related-run-different",
        )


@pytest.mark.asyncio
async def test_execute_preserves_academic_partial_success_and_verified_web_resources(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    _seed_target_work(context)
    intent = _confirmed_intent(context)
    academic = RecordingAcademicSearch(fail_source="openreview")
    service, _, knowledge = _resource_service(context, academic=academic)
    started = service.start(
        intent.set_id,
        RelatedRunOptions(
            intent_revision=intent.revision,
            include_academic_sources=True,
            include_external_web=True,
            external_web_consent=True,
            agent=context.agent_selection,
        ),
        idempotency_key="related-run-partial",
    )

    await service.execute(started.job_id)
    view = service.get(intent.set_id)
    job = service.jobs.get(started.job_id)

    assert job is not None and job.status == "succeeded"
    assert view.status == "partial"
    assert 0 < view.coverage < 100
    assert len(view.papers) == 1
    assert view.papers[0].linked_work_id == "work-related-target"
    assert len(view.knowledge_resources) == 1
    assert {source.connector_id: source.status for source in view.sources} == {
        "arxiv": "succeeded",
        "external_web": "succeeded",
        "openreview": "failed",
    }
    assert knowledge.calls == 1
    assert (
        PreferenceRepository(context.sessions).get_library_state("work-related-target").persisted
        is False
    )


@pytest.mark.asyncio
async def test_resume_executes_only_unfinished_sources(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    _seed_target_work(context)
    intent = _confirmed_intent(context)
    service, academic, knowledge = _resource_service(context)
    started = service.start(
        intent.set_id,
        RelatedRunOptions(intent_revision=intent.revision),
        idempotency_key="related-run-resume",
    )
    with context.sessions() as session:
        completed = (
            session.query(RelatedRunSourceRecord)
            .filter_by(
                set_id=intent.set_id,
                connector_id="arxiv",
            )
            .one()
        )
        completed.status = "succeeded"
        completed.progress = 100
        completed.completed_at = NOW
        session.commit()

    await service.execute(started.job_id)

    assert academic.calls == [(("openreview",), 50)]
    assert knowledge.calls == 0
    assert service.get(intent.set_id).status == "succeeded"


@pytest.mark.asyncio
async def test_verified_item_coverage_marks_partial_external_research(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    _seed_target_work(context)
    intent = _confirmed_intent(context, source_ids=("arxiv",))
    knowledge = RecordingKnowledgeResearch(partial=True)
    service, _, _ = _resource_service(context, knowledge=knowledge)
    started = service.start(
        intent.set_id,
        RelatedRunOptions(
            intent_revision=intent.revision,
            include_external_web=True,
            external_web_consent=True,
            agent=context.agent_selection,
        ),
        idempotency_key="related-run-web-coverage",
    )

    await service.execute(started.job_id)
    view = service.get(intent.set_id)

    assert view.status == "partial"
    assert 0 < view.coverage < 100
    assert {item.status for item in view.sources} == {"succeeded"}


@pytest.mark.asyncio
async def test_user_save_favorites_verified_linked_work_idempotently(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    _seed_target_work(context)
    intent = _confirmed_intent(context, source_ids=("arxiv",))
    service, _, _ = _resource_service(context)
    started = service.start(
        intent.set_id,
        RelatedRunOptions(intent_revision=intent.revision),
        idempotency_key="related-run-save",
    )
    await service.execute(started.job_id)
    candidate = service.get(intent.set_id).papers[0]

    first = service.save_paper(intent.set_id, candidate.id)
    second = service.save_paper(intent.set_id, candidate.id)

    assert first == second
    assert first.saved is True
    assert first.linked_work_id == "work-related-target"
    state = PreferenceRepository(context.sessions).get_library_state("work-related-target")
    assert state.favorite is True
    assert state.feedback == "neutral"


@pytest.mark.asyncio
async def test_user_cannot_save_candidate_without_verified_connector_identity(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    _seed_target_work(context)
    intent = _confirmed_intent(context, source_ids=("arxiv",))
    service, _, _ = _resource_service(context)
    started = service.start(
        intent.set_id,
        RelatedRunOptions(intent_revision=intent.revision),
        idempotency_key="related-run-unverified-save",
    )
    await service.execute(started.job_id)
    candidate = service.get(intent.set_id).papers[0]
    with context.sessions() as session:
        record = session.get(RelatedPaperCandidateRecord, candidate.id)
        assert record is not None
        record.verification_status = "pending_verification"
        session.commit()

    with pytest.raises(RelatedRunConflict, match="verified"):
        service.save_paper(intent.set_id, candidate.id)

    state = PreferenceRepository(context.sessions).get_library_state("work-related-target")
    assert state.persisted is False


@pytest.mark.asyncio
async def test_resume_skips_unfinished_sources_after_global_candidate_budget(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    _seed_target_work(context)
    intent = _confirmed_intent(context)
    service, academic, _ = _resource_service(context)
    started = service.start(
        intent.set_id,
        RelatedRunOptions(intent_revision=intent.revision),
        idempotency_key="related-run-budget",
    )
    with context.sessions() as session:
        completed = (
            session.query(RelatedRunSourceRecord)
            .filter_by(
                set_id=intent.set_id,
                connector_id="arxiv",
            )
            .one()
        )
        completed.status = "succeeded"
        completed.progress = 100
        completed.candidate_count = 50
        completed.displayed_count = 20
        completed.completed_at = NOW
        session.add_all(
            RelatedPaperCandidateRecord(
                set_id=intent.set_id,
                connector_id="arxiv",
                external_id=f"budget-{index}",
                normalized_doi=f"10.1234/budget.{index}",
                title=f"Verified related paper {index}",
                authors=["Verified Author"],
                publication_year=2026,
                reason="Matched the confirmed intent through arXiv.",
                match_evidence={"required_groups": ["diffusion model"]},
                confidence=90,
                verification_status="verified",
                linked_work_id="work-related-target",
                merge_review_id=None,
                field_provenance={"title": "arxiv:title"},
            )
            for index in range(50)
        )
        session.commit()

    await service.execute(started.job_id)

    assert academic.calls == []
    sources = {item.connector_id: item.status for item in service.get(intent.set_id).sources}
    assert sources == {"arxiv": "succeeded", "openreview": "skipped"}
    assert service.jobs.get(started.job_id).status == "succeeded"


@pytest.mark.asyncio
async def test_replayed_checkpoint_page_does_not_duplicate_candidate_counts(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    _seed_target_work(context)
    intent = _confirmed_intent(context, source_ids=("arxiv",))
    service, _, _ = _resource_service(context)
    started = service.start(
        intent.set_id,
        RelatedRunOptions(intent_revision=intent.revision),
        idempotency_key="related-run-page-replay",
    )
    with context.sessions() as session:
        source = (
            session.query(RelatedRunSourceRecord)
            .filter_by(
                set_id=intent.set_id,
                connector_id="arxiv",
            )
            .one()
        )
        source.status = "running"
        source.progress = 20
        source.candidate_count = 1
        source.displayed_count = 1
        source.checkpoint = {
            "page_number": 1,
            "next_cursor": None,
            "fetched_count": 1,
        }
        session.add(
            RelatedPaperCandidateRecord(
                set_id=intent.set_id,
                connector_id="arxiv",
                external_id="arxiv-related-1",
                normalized_doi="10.1234/related.1",
                title="Diffusion model planning with verified evidence",
                authors=["Related Researcher"],
                publication_year=2026,
                reason="Matched required group: diffusion model",
                match_evidence={"required_groups": ["diffusion model"]},
                confidence=91,
                verification_status="verified",
                linked_work_id="work-related-target",
                merge_review_id=None,
                field_provenance={"title": "arxiv:title"},
            )
        )
        session.commit()

    await service.execute(started.job_id)
    view = service.get(intent.set_id)

    assert len(view.papers) == 1
    assert view.sources[0].candidate_count == 1
