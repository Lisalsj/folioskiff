from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DatabaseError


def observed_quality_metrics():  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.quality import QualityMetrics
    from paper_reader.recommendation.relevance import MetricEvidence

    return QualityMetrics(
        citation_performance=MetricEvidence.observed(80),
        venue_rating=MetricEvidence.observed(60),
        author_impact=MetricEvidence.observed(70),
        institution_impact=MetricEvidence.observed(50),
        code_data_impact=MetricEvidence.observed(90),
        research_transparency=MetricEvidence.observed(40),
        publication_maturity=MetricEvidence.observed(100),
    )


@pytest.fixture
def snapshot_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.recommendation.score_snapshots import ScoreSnapshotRepository

    engine = create_sqlite_engine(tmp_path / "score-snapshots.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    work = IdentityRepository(sessions).ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.00001",
            title="Reliable Paper Reading Agents",
            authors=("Alice",),
        )
    ).work
    try:
        yield sessions, ScoreSnapshotRepository(sessions), work
    finally:
        engine.dispose()


def test_quality_score_snapshot_is_content_idempotent_and_fully_reproducible(
    snapshot_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.quality import score_quality

    sessions, repository, work = snapshot_context
    quality = score_quality(observed_quality_metrics())
    evidence_ids = ("observation-openalex-1", "observation-crossref-1")

    first = repository.record_quality(
        work_id=work.id,
        context_key="library",
        quality=quality,
        evidence_observation_ids=evidence_ids,
    )
    duplicate = repository.record_quality(
        work_id=work.id,
        context_key="library",
        quality=quality,
        evidence_observation_ids=evidence_ids,
    )
    latest = repository.latest(work.id, score_type="quality", context_key="library")

    assert duplicate.id == first.id
    assert latest == first
    assert first.score == 73
    assert first.rule_version == "quality-v1"
    assert first.missing_policy == {
        "unknown": "neutral_prior",
        "neutral_value": 50.0,
        "not_applicable": "removed_from_denominator",
    }
    assert first.evidence_observation_ids == evidence_ids
    assert first.components[0]["dimension"] == "citation_performance"
    assert len(first.snapshot_sha256) == 64

    from paper_reader.discovery.models import ScoreSnapshotRecord

    with sessions() as session, pytest.raises(DatabaseError, match="immutable"):
        session.execute(
            update(ScoreSnapshotRecord)
            .where(ScoreSnapshotRecord.id == first.id)
            .values(score=1)
        )
        session.commit()
    with sessions() as session, pytest.raises(DatabaseError, match="immutable"):
        session.execute(delete(ScoreSnapshotRecord).where(ScoreSnapshotRecord.id == first.id))
        session.commit()


def test_score_snapshot_requires_existing_work_and_nonblank_context(
    snapshot_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.quality import score_quality

    _, repository, work = snapshot_context
    quality = score_quality(observed_quality_metrics())

    with pytest.raises(KeyError):
        repository.record_quality(
            work_id="missing-work",
            context_key="library",
            quality=quality,
            evidence_observation_ids=(),
        )
    with pytest.raises(ValueError, match="context_key"):
        repository.record_quality(
            work_id=work.id,
            context_key=" ",
            quality=quality,
            evidence_observation_ids=(),
        )


def test_generic_score_snapshot_supports_closed_score_types_and_payload_versions(
    snapshot_context,
) -> None:  # type: ignore[no-untyped-def]
    _, repository, work = snapshot_context
    payload = {
        "work_id": work.id,
        "context_key": "run:fixture",
        "score_type": "relevance",
        "rule_version": "relevance-v1",
        "score": 82.5,
        "coverage": 75,
        "confidence": 70,
        "components": (
            {
                "dimension": "lexical",
                "score": 90,
                "weight": 0.4,
                "evidence": {"query": "diffusion agent"},
            },
        ),
        "weights": {"lexical": 0.4, "semantic": 0.4, "keyword": 0.2},
        "missing_policy": {"semantic": "neutral_prior"},
        "evidence_observation_ids": ("observation-a",),
        "hard_constraints": {"keyword_eligible": True},
    }

    first = repository.record(**payload)
    duplicate = repository.record(**payload)
    changed_evidence = repository.record(
        **{
            **payload,
            "evidence_observation_ids": ("observation-a", "observation-b"),
        }
    )

    assert duplicate.id == first.id
    assert changed_evidence.id != first.id
    assert changed_evidence.snapshot_sha256 != first.snapshot_sha256
    assert first.score_type == "relevance"
    assert first.components[0]["evidence"] == {"query": "diffusion agent"}
    assert repository.latest(
        work.id,
        score_type="relevance",
        context_key="run:fixture",
    ) == changed_evidence

    with pytest.raises(ValueError, match="score_type"):
        repository.record(**{**payload, "score_type": "agent"})
    with pytest.raises(ValueError, match="rule_version"):
        repository.record(**{**payload, "rule_version": " "})
    with pytest.raises(ValueError, match="score"):
        repository.record(**{**payload, "score": 101})
