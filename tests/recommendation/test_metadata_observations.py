from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def observation_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.recommendation.observations import MetadataObservationRepository

    engine = create_sqlite_engine(tmp_path / "metadata-observations.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    identities = IdentityRepository(sessions)
    work = identities.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.00001",
            title="Reliable Paper Reading Agents",
            authors=("Alice Researcher",),
            doi="10.5555/agent.2026.1",
        )
    ).work
    try:
        yield sessions, identities, MetadataObservationRepository(sessions), work
    finally:
        engine.dispose()


def enrichment_result(work_id: str, *, fetched_at: datetime, citation_count: int = 42):
    from paper_reader.recommendation.enrichment import (
        EnrichmentResult,
        EnrichmentStatus,
        RateLimitObservation,
    )

    return EnrichmentResult(
        work_id=work_id,
        enricher_id="openalex",
        enricher_version="work-v1",
        status=EnrichmentStatus.OBSERVED,
        external_id="W4400000001",
        matched_by="doi",
        fields={
            "citation_count": citation_count,
            "fwci": 3.25,
            "is_retracted": False,
        },
        field_provenance={
            "citation_count": "openalex:cited_by_count",
            "fwci": "openalex:fwci",
            "is_retracted": "openalex:is_retracted",
        },
        warnings=(),
        fetched_at=fetched_at,
        response_sha256="a" * 64,
        rate_limit=RateLimitObservation(
            limit=10_000,
            remaining=9_999,
            reset_after_seconds=43_200,
        ),
    )


def test_metadata_observations_are_retry_idempotent_but_preserve_refresh_history(
    observation_context,
) -> None:  # type: ignore[no-untyped-def]
    _, _, repository, work = observation_context
    first_at = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    first = repository.record(enrichment_result(work.id, fetched_at=first_at))
    duplicate = repository.record(enrichment_result(work.id, fetched_at=first_at))
    refreshed = repository.record(
        enrichment_result(
            work.id,
            fetched_at=first_at + timedelta(days=1),
            citation_count=45,
        )
    )

    history = repository.list_history(work.id, enricher_id="openalex")
    latest = repository.latest_by_enricher(work.id)

    assert duplicate.id == first.id
    assert refreshed.id != first.id
    assert [item.result.fields["citation_count"] for item in history] == [45, 42]
    assert latest["openalex"].id == refreshed.id
    assert latest["openalex"].result.field_provenance["fwci"] == "openalex:fwci"
    assert latest["openalex"].result.rate_limit is not None
    assert latest["openalex"].result.rate_limit.remaining == 9_999
    assert len(first.observation_sha256) == 64


def test_non_observed_terminal_outcomes_are_auditable_without_fake_response_hash(
    observation_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.enrichment import EnrichmentResult, EnrichmentStatus

    _, _, repository, work = observation_context
    stored = repository.record(
        EnrichmentResult(
            work_id=work.id,
            enricher_id="openalex",
            enricher_version="work-v1",
            status=EnrichmentStatus.DISABLED,
            external_id=None,
            matched_by=None,
            fields={},
            field_provenance={},
            warnings=("missing_api_key",),
            fetched_at=datetime(2026, 7, 13, 8, 0, tzinfo=UTC),
            response_sha256=None,
        )
    )

    assert stored.result.status == "disabled"
    assert stored.result.response_sha256 is None
    assert stored.result.warnings == ("missing_api_key",)


@pytest.mark.parametrize(
    "invalid_override",
    (
        {"response_sha256": None},
        {"field_provenance": {}},
        {"fetched_at": datetime(2026, 7, 13, 8, 0)},
    ),
)
def test_enrichment_result_rejects_incomplete_or_untraceable_observations(
    invalid_override: dict[str, object],
) -> None:
    from dataclasses import replace

    baseline = enrichment_result(
        "work-1",
        fetched_at=datetime(2026, 7, 13, 8, 0, tzinfo=UTC),
    )

    with pytest.raises(ValueError):
        replace(baseline, **invalid_override)


def test_work_merge_transfers_observations_and_deduplicates_identical_evidence(
    observation_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import SourcePaper
    from paper_reader.discovery.models import MergeReviewRecord

    sessions, identities, repository, target = observation_context
    candidate = identities.ingest(
        SourcePaper(
            connector_id="openreview",
            external_id="forum-candidate",
            title="Different candidate identity",
            authors=("Bob Researcher",),
        )
    ).work
    observed_at = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    repository.record(enrichment_result(target.id, fetched_at=observed_at))
    repository.record(enrichment_result(candidate.id, fetched_at=observed_at))
    repository.record(
        enrichment_result(
            candidate.id,
            fetched_at=observed_at + timedelta(hours=1),
            citation_count=43,
        )
    )
    with sessions() as session:
        review = MergeReviewRecord(
            candidate_work_id=candidate.id,
            matched_work_id=target.id,
            similarity=0.8,
            evidence={"manual_test": 1.0},
            status="pending",
            created_at=observed_at,
        )
        session.add(review)
        session.commit()
        review_id = review.id

    identities.decide_merge_review(review_id, decision="merge", now=observed_at)

    assert repository.list_history(candidate.id, enricher_id="openalex") == ()
    assert [
        item.result.fields["citation_count"]
        for item in repository.list_history(target.id, enricher_id="openalex")
    ] == [43, 42]
