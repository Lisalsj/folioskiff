from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


def observed_result(
    enricher_id: str,
    fields: dict[str, object],
    *,
    fetched_at: datetime | None = None,
):  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.enrichment import (
        EnrichmentResult,
        EnrichmentStatus,
    )

    return EnrichmentResult(
        work_id="work-1",
        enricher_id=enricher_id,
        enricher_version="fixture-v1",
        status=EnrichmentStatus.OBSERVED,
        external_id=f"{enricher_id}-work-1",
        matched_by="doi",
        fields=fields,
        field_provenance={key: f"{enricher_id}:{key}" for key in fields},
        warnings=(),
        fetched_at=fetched_at or datetime(2026, 7, 13, tzinfo=UTC),
        response_sha256="a" * 64,
    )


def crossref_update(update_type: str, date: str) -> dict[str, object]:
    return {
        "type": update_type,
        "label": update_type.replace("_", " ").title(),
        "DOI": f"10.5555/{update_type}",
        "updated": {"date-time": date},
        "source": "publisher",
    }


def test_retracted_work_is_visible_but_never_default_eligible() -> None:
    from paper_reader.recommendation.integrity import IntegrityStatus, assess_integrity

    assessment = assess_integrity(
        (observed_result("openalex", {"is_retracted": True}),)
    )

    assert assessment.status is IntegrityStatus.RETRACTED
    assert assessment.visible is True
    assert assessment.eligible_by_default is False
    assert assessment.requires_strong_warning is True
    assert assessment.warning_codes == ("retracted",)
    assert assessment.constrain_confidence(99) == 99
    assert assessment.to_hard_constraints()["eligible_by_default"] is False


def test_expression_of_concern_and_major_correction_cap_confidence() -> None:
    from paper_reader.recommendation.integrity import IntegrityStatus, assess_integrity

    concern = assess_integrity(
        (
            observed_result(
                "crossref",
                {
                    "updates": [
                        crossref_update(
                            "expression_of_concern",
                            "2026-06-01T00:00:00Z",
                        )
                    ]
                },
            ),
        )
    )
    correction = assess_integrity(
        (
            observed_result(
                "crossref",
                {
                    "updates": [
                        crossref_update("correction", "2026-06-01T00:00:00Z")
                    ]
                },
            ),
        )
    )

    assert concern.status is IntegrityStatus.EXPRESSION_OF_CONCERN
    assert concern.eligible_by_default is True
    assert concern.requires_strong_warning is True
    assert concern.constrain_confidence(100) == 40
    assert concern.warning_codes == ("expression_of_concern",)

    assert correction.status is IntegrityStatus.MAJOR_CORRECTION
    assert correction.eligible_by_default is True
    assert correction.requires_strong_warning is True
    assert correction.constrain_confidence(100) == 70
    assert correction.warning_codes == ("major_correction",)


def test_reinstatement_only_clears_an_older_retraction_when_sources_agree() -> None:
    from paper_reader.recommendation.integrity import IntegrityStatus, assess_integrity

    updates = [
        crossref_update("retraction", "2026-01-01T00:00:00Z"),
        crossref_update("reinstatement", "2026-07-01T00:00:00Z"),
    ]
    reinstated = assess_integrity(
        (
            observed_result("crossref", {"updates": updates}),
            observed_result("openalex", {"is_retracted": False}),
        )
    )
    conflicted = assess_integrity(
        (
            observed_result("crossref", {"updates": updates}),
            observed_result("openalex", {"is_retracted": True}),
        )
    )

    assert reinstated.status is IntegrityStatus.REINSTATED
    assert reinstated.eligible_by_default is True
    assert reinstated.warning_codes == ("reinstated",)
    assert reinstated.constrain_confidence(100) == 70

    assert conflicted.status is IntegrityStatus.CONFLICTED
    assert conflicted.eligible_by_default is False
    assert conflicted.warning_codes == ("integrity_metadata_conflict",)
    assert conflicted.constrain_confidence(100) == 40


def test_missing_integrity_metadata_is_unknown_not_clean() -> None:
    from paper_reader.recommendation.integrity import IntegrityStatus, assess_integrity

    assessment = assess_integrity(())

    assert assessment.status is IntegrityStatus.UNKNOWN
    assert assessment.evidence_coverage == 0
    assert assessment.warning_codes == ()
    assert assessment.eligible_by_default is True
    assert assessment.constrain_confidence(63) == 63

    unrelated_openalex_data = assess_integrity(
        (observed_result("openalex", {"citation_count": 999}),)
    )
    assert unrelated_openalex_data.status is IntegrityStatus.UNKNOWN
    assert unrelated_openalex_data.evidence_coverage == 0


def test_integrity_constraint_is_frozen_into_quality_snapshot(tmp_path: Path) -> None:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.recommendation.integrity import assess_integrity
    from paper_reader.recommendation.quality import QualityMetrics, score_quality
    from paper_reader.recommendation.relevance import MetricEvidence
    from paper_reader.recommendation.score_snapshots import ScoreSnapshotRepository

    engine = create_sqlite_engine(tmp_path / "integrity-snapshot.sqlite3")
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
    quality = score_quality(
        QualityMetrics(
            citation_performance=MetricEvidence.observed(80),
            venue_rating=MetricEvidence.observed(60),
            author_impact=MetricEvidence.observed(70),
            institution_impact=MetricEvidence.observed(50),
            code_data_impact=MetricEvidence.observed(90),
            research_transparency=MetricEvidence.observed(40),
            publication_maturity=MetricEvidence.observed(100),
        )
    )
    integrity = assess_integrity(
        (
            observed_result(
                "crossref",
                {
                    "updates": [
                        crossref_update("correction", "2026-06-01T00:00:00Z")
                    ]
                },
            ),
        )
    )

    try:
        snapshot = ScoreSnapshotRepository(sessions).record_quality(
            work_id=work.id,
            context_key="integrity-fixture",
            quality=quality,
            evidence_observation_ids=("crossref-observation",),
            integrity=integrity,
        )

        assert snapshot.score == 73
        assert snapshot.confidence == 70
        assert snapshot.hard_constraints["integrity"]["status"] == "major_correction"
        assert snapshot.hard_constraints["integrity"]["requires_strong_warning"] is True
    finally:
        engine.dispose()


def test_integrity_assessment_validates_public_confidence_boundaries() -> None:
    import pytest

    from paper_reader.recommendation.integrity import (
        INTEGRITY_RULE_VERSION,
        IntegrityAssessment,
        IntegrityStatus,
        assess_integrity,
    )

    with pytest.raises(ValueError, match="evidence_coverage"):
        IntegrityAssessment(
            rule_version=INTEGRITY_RULE_VERSION,
            status=IntegrityStatus.UNKNOWN,
            eligible_by_default=True,
            visible=True,
            requires_strong_warning=False,
            confidence_cap=None,
            evidence_coverage=-1,
            warnings=(),
            observed_sources=(),
        )
    with pytest.raises(ValueError, match="confidence_cap"):
        IntegrityAssessment(
            rule_version=INTEGRITY_RULE_VERSION,
            status=IntegrityStatus.UNKNOWN,
            eligible_by_default=True,
            visible=True,
            requires_strong_warning=False,
            confidence_cap=float("nan"),
            evidence_coverage=0,
            warnings=(),
            observed_sources=(),
        )
    assessment = assess_integrity(())
    with pytest.raises(ValueError, match="confidence"):
        assessment.constrain_confidence(101)


def test_latest_field_specific_observations_define_no_known_issue() -> None:
    from paper_reader.recommendation.enrichment import (
        EnrichmentResult,
        EnrichmentStatus,
    )
    from paper_reader.recommendation.integrity import IntegrityStatus, assess_integrity

    stale_correction = observed_result(
        "crossref",
        {"updates": [crossref_update("correction", "2026-06-01T00:00:00Z")]},
        fetched_at=datetime(2026, 7, 12, tzinfo=UTC),
    )
    refreshed_clear = observed_result(
        "crossref",
        {"updates": []},
        fetched_at=datetime(2026, 7, 14, tzinfo=UTC),
    )
    unavailable = EnrichmentResult(
        work_id="work-1",
        enricher_id="crossref",
        enricher_version="fixture-v1",
        status=EnrichmentStatus.NOT_FOUND,
        external_id=None,
        matched_by=None,
        fields={},
        field_provenance={},
        warnings=("not_found",),
        fetched_at=datetime(2026, 7, 15, tzinfo=UTC),
        response_sha256=None,
    )
    assessment = assess_integrity(
        (
            stale_correction,
            observed_result("semantic_scholar", {"citation_count": 10}),
            unavailable,
            refreshed_clear,
            observed_result("openalex", {"is_retracted": False}),
        )
    )

    assert assessment.status is IntegrityStatus.NO_KNOWN_ISSUE
    assert assessment.evidence_coverage == 100
    assert assessment.observed_sources == ("crossref", "openalex")


def test_crossref_withdrawal_partial_retraction_and_lone_reinstatement_are_guarded() -> None:
    from paper_reader.recommendation.integrity import IntegrityStatus, assess_integrity

    withdrawn = assess_integrity(
        (
            observed_result(
                "crossref",
                {"updates": [crossref_update("withdrawal", "2026-06-01T00:00:00Z")]},
            ),
        )
    )
    partial = assess_integrity(
        (
            observed_result(
                "crossref",
                {
                    "updates": [
                        crossref_update("partial-retraction", "2026-06-01T00:00:00Z")
                    ]
                },
            ),
        )
    )
    reinstated = assess_integrity(
        (
            observed_result(
                "crossref",
                {"updates": [crossref_update("reinstatement", "2026-06-01T00:00:00Z")]},
            ),
        )
    )

    assert withdrawn.status is IntegrityStatus.RETRACTED
    assert withdrawn.eligible_by_default is False
    assert partial.status is IntegrityStatus.EXPRESSION_OF_CONCERN
    assert partial.warning_codes == ("expression_of_concern",)
    assert reinstated.status is IntegrityStatus.REINSTATED
    assert reinstated.constrain_confidence(100) == 70


def test_crossref_update_parser_handles_date_parts_and_ignores_malformed_rows() -> None:
    from paper_reader.recommendation.integrity import IntegrityStatus, assess_integrity

    correction = {
        "type": "corrigendum",
        "DOI": "10.5555/corrigendum",
        "updated": {"date-parts": [[2026, 6, 2]]},
    }
    assessment = assess_integrity(
        (
            observed_result(
                "crossref",
                {
                    "updates": [
                        "invalid-row",
                        {"type": "  ", "updated": None},
                        correction,
                        {
                            "type": "erratum",
                            "updated": {"date-time": "not-a-date"},
                        },
                        {
                            "type": "clarification",
                            "updated": {"date-parts": [["bad-year"]]},
                        },
                    ]
                },
            ),
        )
    )
    invalid_container = assess_integrity(
        (observed_result("crossref", {"updates": "not-a-list"}),)
    )

    assert assessment.status is IntegrityStatus.MAJOR_CORRECTION
    assert assessment.warnings[0].update_type == "corrigendum"
    assert assessment.warnings[0].observed_at == "2026-06-02T00:00:00+00:00"
    assert invalid_container.status is IntegrityStatus.UNKNOWN
    assert invalid_container.evidence_coverage == 0


def test_naive_reinstatement_timestamp_cannot_clear_a_verified_retraction() -> None:
    from paper_reader.recommendation.integrity import IntegrityStatus, assess_integrity

    assessment = assess_integrity(
        (
            observed_result(
                "crossref",
                {
                    "updates": [
                        crossref_update("retraction", "2026-01-01T00:00:00Z"),
                        crossref_update("reinstatement", "2026-07-01T00:00:00"),
                    ]
                },
            ),
        )
    )

    assert assessment.status is IntegrityStatus.RETRACTED
    assert assessment.eligible_by_default is False
