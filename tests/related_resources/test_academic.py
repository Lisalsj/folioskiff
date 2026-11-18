from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select

from paper_reader.discovery.connectors import (
    ConnectorCapabilities,
    ConnectorEstimate,
    ConnectorRegistry,
    ConnectorRequestError,
    SourcePage,
    SourceQuery,
)
from paper_reader.discovery.identity import IdentityRepository, SourcePaper
from paper_reader.discovery.models import SourceRecord, WorkRecord
from paper_reader.related_resources.academic import (
    AcademicCandidatePage,
    AcademicRelatedSearch,
)

NOW = datetime(2026, 8, 1, 9, tzinfo=UTC)
RelatedIntentTestContext = Any


class FixtureConnector:
    def __init__(
        self,
        connector_id: str,
        pages: tuple[tuple[SourcePaper, ...], ...] = (),
        *,
        failure: ConnectorRequestError | None = None,
        failure_on_call: int = 1,
    ) -> None:
        self.capabilities = ConnectorCapabilities(
            connector_id=connector_id,
            display_name=connector_id,
            credential_requirement="none",
            supports_exact_updated_date=False,
        )
        self._pages = pages
        self._failure = failure
        self._failure_on_call = failure_on_call
        self.calls: list[tuple[SourceQuery, str | None, int]] = []

    async def estimate(self, query: SourceQuery) -> ConnectorEstimate:
        return ConnectorEstimate(total=sum(len(page) for page in self._pages), exact=False)

    async def fetch_page(
        self,
        query: SourceQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> SourcePage:
        self.calls.append((query, cursor, limit))
        if self._failure is not None and len(self.calls) == self._failure_on_call:
            raise self._failure
        index = int(cursor or "0")
        records = self._pages[index][:limit] if index < len(self._pages) else ()
        next_cursor = str(index + 1) if index + 1 < len(self._pages) else None
        return SourcePage(
            records=records,
            next_cursor=next_cursor,
            total=sum(len(page) for page in self._pages),
        )


class PageCollector:
    def __init__(self) -> None:
        self.pages: list[AcademicCandidatePage] = []

    async def __call__(self, page: AcademicCandidatePage) -> None:
        self.pages.append(page)

    @property
    def candidates(self) -> tuple[Any, ...]:
        return tuple(candidate for page in self.pages for candidate in page.candidates)


def _paper(
    connector_id: str,
    external_id: str,
    *,
    title: str,
    doi: str | None = None,
    updated_at: datetime | None = NOW,
    authors: tuple[str, ...] = ("Related Researcher",),
) -> SourcePaper:
    return SourcePaper(
        connector_id=connector_id,
        external_id=external_id,
        title=title,
        authors=authors,
        abstract="A diffusion model is evaluated with an agent planning method.",
        doi=doi,
        published_at=updated_at,
        updated_at=updated_at,
        landing_url=f"https://example.org/{connector_id}/{external_id}",
        field_provenance={
            "title": f"{connector_id}:title",
            "authors": f"{connector_id}:authors",
            "updated_at": f"{connector_id}:updated_at",
        },
    )


def _confirmed_intent(
    context: RelatedIntentTestContext,
    *,
    source_ids: tuple[str, ...],
    recent_months: int = 2,
):
    draft = context.service.draft(context.work_id, context.snapshot_id)
    edited = draft.model_copy(
        update={
            "summary": "Diffusion models with agent planning",
            "required_groups": (("diffusion model", "扩散模型"),),
            "optional_terms": ("agent planning", "智能体规划", "evaluation", "评估"),
            "excluded_terms": ("survey",),
            "recent_months": recent_months,
            "source_ids": source_ids,
        }
    )
    return context.service.confirm(
        draft.set_id,
        revision=draft.revision,
        intent=edited,
    )


def _search(
    context: RelatedIntentTestContext,
    *connectors: FixtureConnector,
) -> AcademicRelatedSearch:
    return AcademicRelatedSearch(
        sessions=context.sessions,
        connectors=ConnectorRegistry(connectors),
        identities=IdentityRepository(context.sessions),
        intents=context.repository,
        timezone="Asia/Shanghai",
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_academic_search_is_bounded_and_preserves_partial_source_failure(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    arxiv = FixtureConnector(
        "arxiv",
        (
            (
                _paper(
                    "arxiv",
                    "2608.02000v1",
                    title="Diffusion model for distinct mechanism topic 0",
                    doi="10.1234/bounded.0",
                ),
                _paper(
                    "arxiv",
                    "2608.02999v1",
                    title="Survey of diffusion model planning methods",
                    doi="10.1234/excluded.survey",
                ),
            ),
            *tuple(
                (
                    _paper(
                        "arxiv",
                        f"2608.0200{index}v1",
                        title=f"Diffusion model for distinct mechanism topic {index}",
                        doi=f"10.1234/bounded.{index}",
                    ),
                )
                for index in range(1, 4)
            ),
        ),
    )
    openreview = FixtureConnector(
        "openreview",
        failure=ConnectorRequestError(
            "rate_limited",
            "slow down",
            retryable=True,
            retry_after_seconds=7,
        ),
    )
    intent = _confirmed_intent(context, source_ids=("arxiv", "openreview"))
    collector = PageCollector()

    summary = await _search(context, arxiv, openreview).search(
        intent,
        on_page=collector,
        cancelled=lambda: False,
    )

    assert len(arxiv.calls) == 3
    assert arxiv.calls[0][0].months == ("2026-07", "2026-08")
    assert arxiv.calls[0][0].compiled_keywords.boolean_expression == (
        '("diffusion model" OR "扩散模型") AND NOT ("survey")'
    )
    assert arxiv.calls[0][0].compiled_keywords.boost_terms == (
        "agent planning",
        "智能体规划",
        "evaluation",
        "评估",
    )
    assert summary.sources["arxiv"].status == "succeeded"
    assert summary.sources["arxiv"].stop_reason == "page_limit"
    assert summary.sources["openreview"].status == "failed"
    assert summary.sources["openreview"].error_code == "rate_limited"
    assert summary.sources["openreview"].retryable is True
    assert summary.partial is True
    assert len(collector.candidates) == 3
    assert "2608.02999v1" not in {item.external_id for item in collector.candidates}


@pytest.mark.asyncio
async def test_resume_scope_queries_only_selected_sources_with_remaining_budget(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    arxiv = FixtureConnector(
        "arxiv",
        (
            (
                _paper(
                    "arxiv",
                    "2608.03001v1",
                    title="Diffusion model result that was already checkpointed",
                ),
            ),
        ),
    )
    openreview = FixtureConnector(
        "openreview",
        (
            (
                _paper(
                    "openreview",
                    "openreview-resume-1",
                    title="Diffusion model result from an unfinished source",
                ),
                _paper(
                    "openreview",
                    "openreview-resume-2",
                    title="Diffusion model result beyond the remaining budget",
                ),
            ),
        ),
    )
    intent = _confirmed_intent(context, source_ids=("arxiv", "openreview"))
    collector = PageCollector()

    summary = await _search(context, arxiv, openreview).search(
        intent,
        on_page=collector,
        cancelled=lambda: False,
        source_ids=("openreview",),
        candidate_limit=1,
    )

    assert arxiv.calls == []
    assert len(openreview.calls) == 1
    assert openreview.calls[0][2] == 1
    assert tuple(summary.sources) == ("openreview",)
    assert len(collector.candidates) == 1


@pytest.mark.asyncio
async def test_cross_source_duplicate_links_to_one_display_work(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    arxiv = FixtureConnector(
        "arxiv",
        (
            (
                _paper(
                    "arxiv",
                    "2608.03001v1",
                    title="Diffusion model planning for protein design",
                    doi="https://doi.org/10.5555/SHARED.PAPER",
                    authors=("Alice Duplicate",),
                ),
            ),
        ),
    )
    openreview = FixtureConnector(
        "openreview",
        (
            (
                _paper(
                    "openreview",
                    "shared-paper-forum",
                    title="Diffusion model planning for protein design",
                    doi="10.5555/shared.paper",
                    authors=("Alice Duplicate",),
                ),
            ),
        ),
    )
    intent = _confirmed_intent(context, source_ids=("arxiv", "openreview"))
    collector = PageCollector()

    summary = await _search(context, arxiv, openreview).search(
        intent,
        on_page=collector,
        cancelled=lambda: False,
    )

    assert len(summary.display_candidates) == 1
    candidate = summary.display_candidates[0]
    assert candidate.raw_doi == "https://doi.org/10.5555/SHARED.PAPER"
    assert candidate.normalized_doi == "10.5555/shared.paper"
    assert candidate.linked_work_id is not None
    assert candidate.merge_review_id is None
    with context.sessions() as session:
        source_work_ids = tuple(
            session.scalars(
                select(SourceRecord.work_id)
                .where(SourceRecord.doi == "10.5555/shared.paper")
                .order_by(SourceRecord.connector_id)
            )
        )
    assert len(source_work_ids) == 2
    assert len(set(source_work_ids)) == 1


@pytest.mark.asyncio
async def test_single_source_failure_after_a_saved_page_is_partial_success(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    arxiv = FixtureConnector(
        "arxiv",
        (
            (
                _paper(
                    "arxiv",
                    "2608.03009v1",
                    title="Diffusion model with one durable related result",
                    doi="10.5555/partial.page",
                ),
            ),
            (
                _paper(
                    "arxiv",
                    "2608.03010v1",
                    title="Diffusion model result after a failed checkpoint",
                ),
            ),
        ),
        failure=ConnectorRequestError(
            "rate_limited",
            "slow down after one page",
            retryable=True,
        ),
        failure_on_call=2,
    )
    intent = _confirmed_intent(context, source_ids=("arxiv",))
    collector = PageCollector()

    summary = await _search(context, arxiv).search(
        intent,
        on_page=collector,
        cancelled=lambda: False,
    )

    assert summary.sources["arxiv"].status == "failed"
    assert summary.sources["arxiv"].candidate_count == 1
    assert len(summary.candidates) == 1
    assert summary.partial is True


@pytest.mark.asyncio
async def test_same_doi_deduplicates_when_first_source_requires_merge_review(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    title = "Diffusion model planning for constrained protein design"
    authors = ("Alice Conflict",)
    IdentityRepository(context.sessions).ingest(
        _paper(
            "seed_source",
            "existing-conflict",
            title=title,
            doi="10.5555/existing.identity",
            authors=authors,
        ),
        now=NOW,
    )
    arxiv = FixtureConnector(
        "arxiv",
        (
            (
                _paper(
                    "arxiv",
                    "2608.03002v1",
                    title=title,
                    doi="10.5555/new.identity",
                    authors=authors,
                ),
            ),
        ),
    )
    openreview = FixtureConnector(
        "openreview",
        (
            (
                _paper(
                    "openreview",
                    "new-identity-forum",
                    title=title,
                    doi="10.5555/new.identity",
                    authors=authors,
                ),
            ),
        ),
    )
    intent = _confirmed_intent(context, source_ids=("arxiv", "openreview"))
    collector = PageCollector()

    summary = await _search(context, arxiv, openreview).search(
        intent,
        on_page=collector,
        cancelled=lambda: False,
    )

    assert len(summary.candidates) == 1
    assert summary.candidates[0].normalized_doi == "10.5555/new.identity"
    assert summary.candidates[0].merge_review_id is not None


@pytest.mark.asyncio
async def test_cached_doi_and_source_references_can_enter_outside_month_window(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    with context.sessions() as session:
        current = session.scalar(
            select(SourceRecord).where(SourceRecord.work_id == context.work_id)
        )
        assert current is not None
        current.raw_payload = {
            **current.raw_payload,
            "references": [
                "doi:10.7777/older-related",
                {"connector_id": "openreview", "external_id": "older-forum"},
            ],
        }
        session.commit()

    old = datetime(2020, 1, 15, tzinfo=UTC)
    arxiv = FixtureConnector(
        "arxiv",
        (
            (
                _paper(
                    "arxiv",
                    "2001.00001v1",
                    title="Diffusion model for historical molecular design",
                    doi="10.7777/older-related",
                    updated_at=old,
                ),
                _paper(
                    "arxiv",
                    "2001.00002v1",
                    title="Diffusion model without a cached reference",
                    doi="10.7777/not-referenced",
                    updated_at=old,
                ),
            ),
        ),
    )
    openreview = FixtureConnector(
        "openreview",
        (
            (
                _paper(
                    "openreview",
                    "older-forum",
                    title="Diffusion model with an older agent planning result",
                    updated_at=old,
                ),
            ),
        ),
    )
    intent = _confirmed_intent(
        context,
        source_ids=("arxiv", "openreview"),
        recent_months=1,
    )
    collector = PageCollector()

    summary = await _search(context, arxiv, openreview).search(
        intent,
        on_page=collector,
        cancelled=lambda: False,
    )

    assert {item.external_id for item in summary.display_candidates} == {
        "2001.00001v1",
        "older-forum",
    }
    assert all(item.cited_by_current_paper for item in summary.display_candidates)


@pytest.mark.asyncio
async def test_academic_search_stops_at_fifty_total_candidates(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    first_page = tuple(
        _paper(
            "arxiv",
            f"2608.04{index:03d}v1",
            title=f"Diffusion model candidate from bounded family alpha {index}",
            doi=f"10.8888/alpha.{index}",
        )
        for index in range(30)
    )
    second_page = tuple(
        _paper(
            "arxiv",
            f"2608.05{index:03d}v1",
            title=f"Diffusion model candidate from bounded family beta {index}",
            doi=f"10.8888/beta.{index}",
        )
        for index in range(30)
    )
    arxiv = FixtureConnector("arxiv", (first_page, second_page, first_page))
    intent = _confirmed_intent(context, source_ids=("arxiv",))
    collector = PageCollector()

    summary = await _search(context, arxiv).search(
        intent,
        on_page=collector,
        cancelled=lambda: False,
    )

    assert [call[2] for call in arxiv.calls] == [50, 20]
    assert summary.total_fetched == 50
    assert len(collector.candidates) == 50
    assert len(summary.display_candidates) == 20
    assert summary.sources["arxiv"].stop_reason == "candidate_limit"


@pytest.mark.asyncio
async def test_connector_cannot_spoof_candidate_source_identity(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    arxiv = FixtureConnector(
        "arxiv",
        (
            (
                _paper(
                    "openreview",
                    "spoofed-source",
                    title="Diffusion model with a spoofed source identity",
                ),
            ),
        ),
    )
    intent = _confirmed_intent(context, source_ids=("arxiv",))
    collector = PageCollector()

    summary = await _search(context, arxiv).search(
        intent,
        on_page=collector,
        cancelled=lambda: False,
    )

    assert summary.sources["arxiv"].status == "failed"
    assert summary.sources["arxiv"].error_code == "response_incompatible"
    assert collector.candidates == ()


@pytest.mark.asyncio
async def test_malformed_connector_field_type_is_isolated_to_its_source(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    malformed = replace(
        _paper(
            "arxiv",
            "will-be-replaced",
            title="Diffusion model with malformed connector data",
        ),
        external_id=123,
    )
    arxiv = FixtureConnector("arxiv", ((malformed,),))
    intent = _confirmed_intent(context, source_ids=("arxiv",))
    collector = PageCollector()

    summary = await _search(context, arxiv).search(
        intent,
        on_page=collector,
        cancelled=lambda: False,
    )

    assert summary.sources["arxiv"].status == "failed"
    assert summary.sources["arxiv"].error_code == "response_incompatible"
    assert collector.candidates == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_url",
    (
        "javascript:alert(document.domain)",
        "https://reader.local/paper",
        "https://127.0.0.1/paper",
        "https://user:pass@example.org/paper",
    ),
)
async def test_connector_cannot_publish_an_unsafe_paper_link(
    related_intent_context: RelatedIntentTestContext,
    unsafe_url: str,
) -> None:
    context = related_intent_context
    unsafe = replace(
        _paper(
            "arxiv",
            "unsafe-link",
            title="Diffusion model with an unsafe landing link",
        ),
        landing_url=unsafe_url,
    )
    arxiv = FixtureConnector("arxiv", ((unsafe,),))
    intent = _confirmed_intent(context, source_ids=("arxiv",))
    collector = PageCollector()

    summary = await _search(context, arxiv).search(
        intent,
        on_page=collector,
        cancelled=lambda: False,
    )

    assert summary.sources["arxiv"].status == "failed"
    assert summary.sources["arxiv"].error_code == "response_incompatible"
    assert collector.candidates == ()


def test_identity_batch_rejects_more_than_fifty_before_writing(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    identities = IdentityRepository(context.sessions)
    papers = tuple(
        _paper(
            "arxiv",
            f"batch-overflow-{index}",
            title=f"Diffusion model overflow candidate {index}",
        )
        for index in range(51)
    )
    with context.sessions() as session:
        before = int(session.scalar(select(func.count()).select_from(WorkRecord)) or 0)

    with pytest.raises(ValueError, match="50"):
        identities.ingest_candidates(papers, now=NOW)

    with context.sessions() as session:
        after = int(session.scalar(select(func.count()).select_from(WorkRecord)) or 0)
    assert after == before
