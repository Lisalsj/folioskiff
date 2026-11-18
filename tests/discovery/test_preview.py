from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_preview_estimates_samples_filters_locally_and_never_persists(tmp_path: Path) -> None:
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.connectors import (
        ConnectorCapabilities,
        ConnectorEstimate,
        ConnectorRegistry,
        ConnectorRequestError,
        SourcePage,
        SourceQuery,
    )
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.keywords import (
        ConceptGroup,
        KeywordRepository,
        KeywordSetDefinition,
    )
    from paper_reader.discovery.plans import (
        DateRangeSpec,
        DiscoveryPlanCreate,
        DiscoveryPlanRepository,
    )
    from paper_reader.discovery.preview import DiscoveryPreviewService
    from paper_reader.migrations import upgrade_database

    class ArxivFixtureConnector:
        capabilities = ConnectorCapabilities(
            connector_id="arxiv",
            display_name="arXiv",
            credential_requirement="none",
            supports_exact_updated_date=True,
        )

        async def estimate(self, query: SourceQuery) -> ConnectorEstimate:
            assert query.months == ("2026-06", "2026-07")
            assert query.filters == {"categories": ["cs.AI"]}
            return ConnectorEstimate(total=137, exact=True)

        async def fetch_page(
            self,
            query: SourceQuery,
            *,
            cursor: str | None,
            limit: int,
        ) -> SourcePage:
            assert cursor is None
            records = [
                SourcePaper(
                    connector_id="arxiv",
                    external_id=f"2607.{index:05d}v1",
                    title=f"Reliable AI agent {index}",
                    authors=("A. Researcher",),
                    abstract="An autonomous agent with tool use.",
                    updated_at=datetime(2026, 7, index + 1, tzinfo=UTC),
                )
                for index in range(8)
            ]
            records.extend(
                (
                    SourcePaper(
                        connector_id="arxiv",
                        external_id="old",
                        title="Reliable AI agent from last year",
                        authors=("A. Researcher",),
                        updated_at=datetime(2025, 7, 1, tzinfo=UTC),
                    ),
                    SourcePaper(
                        connector_id="arxiv",
                        external_id="irrelevant",
                        title="Diffusion image generation",
                        authors=("B. Researcher",),
                        updated_at=datetime(2026, 7, 2, tzinfo=UTC),
                    ),
                    SourcePaper(
                        connector_id="arxiv",
                        external_id="unknown-date",
                        title="Reliable AI agent without date",
                        authors=("C. Researcher",),
                    ),
                )
            )
            return SourcePage(records=tuple(records[:limit]), next_cursor=None, total=137)

    class FailingOpenReviewConnector:
        capabilities = ConnectorCapabilities(
            connector_id="openreview",
            display_name="OpenReview",
            credential_requirement="none",
            supports_exact_updated_date=True,
        )

        async def estimate(self, query: SourceQuery) -> ConnectorEstimate:
            raise ConnectorRequestError("remote_5xx", "OpenReview unavailable", retryable=True)

        async def fetch_page(
            self,
            query: SourceQuery,
            *,
            cursor: str | None,
            limit: int,
        ) -> SourcePage:
            raise AssertionError("sample must not run after estimate failure")

    class DisabledIeeeConnector:
        capabilities = ConnectorCapabilities(
            connector_id="ieee",
            display_name="IEEE Xplore",
            credential_requirement="api_key",
            supports_exact_updated_date=False,
            enabled=False,
            disabled_reason="missing_api_key",
            setup_url="https://developer.ieee.org/",
        )

        async def estimate(self, query: SourceQuery) -> ConnectorEstimate:
            raise AssertionError("disabled connector must not be called")

        async def fetch_page(
            self,
            query: SourceQuery,
            *,
            cursor: str | None,
            limit: int,
        ) -> SourcePage:
            raise AssertionError("disabled connector must not be called")

    database = tmp_path / "preview.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    keywords = KeywordRepository(sessions)
    version = keywords.create_version(
        research_intent="reliable agents",
        definition=KeywordSetDefinition(
            required=(
                ConceptGroup(
                    key="agent",
                    label="智能体",
                    terms=("AI agent", "智能体"),
                ),
            )
        ),
    )
    keywords.confirm(version.id)
    plans = DiscoveryPlanRepository(sessions)
    plan = plans.create(
        DiscoveryPlanCreate(
            name="Agent watch",
            research_intent="reliable agents",
            keyword_version_id=version.id,
            date_range=DateRangeSpec(
                mode="fixed",
                start_month="2026-06",
                end_month="2026-07",
            ),
            sources=("arxiv", "openreview", "ieee"),
            source_filters={"arxiv": {"categories": ["cs.AI"]}},
        )
    )
    registry = ConnectorRegistry(
        (ArxivFixtureConnector(), FailingOpenReviewConnector(), DisabledIeeeConnector())
    )

    result = await DiscoveryPreviewService(
        plans=plans,
        keywords=keywords,
        connectors=registry,
    ).preview(plan.id, now=datetime(2026, 7, 13, tzinfo=UTC))

    arxiv, openreview, ieee = result.sources
    assert arxiv.status == "ready"
    assert arxiv.estimate == 137
    assert arxiv.estimate_exact is True
    assert len(arxiv.samples) == 8
    assert arxiv.keyword_impact == {
        "remote_candidates": 11,
        "in_date_range": 9,
        "matched_confirmed_keywords": 8,
    }
    assert openreview.status == "error"
    assert openreview.error_code == "remote_5xx"
    assert openreview.retryable is True
    assert ieee.status == "disabled"
    assert ieee.error_code == "missing_api_key"
    assert IdentityRepository(sessions).count_works() == 0
    engine.dispose()


def test_connector_registry_rejects_duplicate_ids() -> None:
    from paper_reader.discovery.connectors import ConnectorCapabilities, ConnectorRegistry

    class Duplicate:
        capabilities = ConnectorCapabilities(
            connector_id="arxiv",
            display_name="Duplicate",
            credential_requirement="none",
            supports_exact_updated_date=True,
        )

    with pytest.raises(ValueError, match="Duplicate connector"):
        ConnectorRegistry((Duplicate(), Duplicate()))  # type: ignore[arg-type]
