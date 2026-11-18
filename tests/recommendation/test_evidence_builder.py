from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def _observed(work_id: str, enricher_id: str, fields: dict[str, object], *, offset: int = 0):
    from paper_reader.recommendation.enrichment import (
        EnrichmentResult,
        EnrichmentStatus,
    )

    return EnrichmentResult(
        work_id=work_id,
        enricher_id=enricher_id,
        enricher_version="fixture-v1",
        status=EnrichmentStatus.OBSERVED,
        external_id=f"{enricher_id}-{work_id}",
        matched_by="doi",
        fields=fields,
        field_provenance={key: f"{enricher_id}:{key}" for key in fields},
        warnings=(),
        fetched_at=datetime(2026, 7, 15, tzinfo=UTC) + timedelta(minutes=offset),
        response_sha256=hashlib.sha256(enricher_id.encode("utf-8")).hexdigest(),
    )


@pytest.fixture
def evidence_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.migrations import upgrade_database
    from paper_reader.recommendation.evidence_builder import QualityEvidenceBuilder
    from paper_reader.recommendation.observations import MetadataObservationRepository
    from paper_reader.recommendation.rating_catalogs import RatingCatalogRepository
    from paper_reader.recommendation.venue_rating import VenueRatingService

    database = tmp_path / "quality-evidence.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    identities = IdentityRepository(sessions)
    observations = MetadataObservationRepository(sessions)
    works = tuple(
        identities.ingest(
            SourcePaper(
                connector_id="arxiv",
                external_id=f"2607.0100{index}v1",
                title=title,
                authors=(author,),
                doi=f"10.5555/evidence.{index}",
                published_at=datetime(2026, 7, index, tzinfo=UTC),
                updated_at=datetime(2026, 7, index + 1, tzinfo=UTC),
            )
        ).work
        for index, (title, author) in enumerate(
            (
                ("Reliable Research Agent", "Alice"),
                ("Clinical Decision Agent", "Bob"),
                ("Transparent Research Agent", "Carol"),
                ("Sparse Evidence Agent", "Dora"),
            ),
            start=1,
        )
    )
    builder = QualityEvidenceBuilder(
        sessions=sessions,
        observations=observations,
        venue_ratings=VenueRatingService(RatingCatalogRepository(sessions)),
    )
    try:
        yield {
            "sessions": sessions,
            "works": works,
            "observations": observations,
            "builder": builder,
        }
    finally:
        engine.dispose()


def test_openalex_normalized_citations_are_primary_and_names_never_imply_impact(
    evidence_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.integrity import IntegrityStatus
    from paper_reader.recommendation.relevance import EvidenceStatus

    work = evidence_context["works"][0]
    observations = evidence_context["observations"]
    observations.record(
        _observed(
            work.id,
            "openalex",
            {
                "citation_count": 42,
                "fwci": 3.25,
                "citation_normalized_percentile": {"value": 0.94},
                "is_retracted": False,
                "primary_topic": {
                    "display_name": "Artificial intelligence",
                    "domain": {"display_name": "Computer Science"},
                },
                "authors": [
                    {
                        "id": "A1",
                        "name": "Famous Researcher",
                        "institutions": [
                            {"id": "I1", "name": "Famous University"},
                        ],
                    }
                ],
            },
        )
    )
    observations.record(
        _observed(
            work.id,
            "semantic_scholar",
            {
                "citation_count": 9999,
                "influential_citation_count": 999,
                "fields_of_study": ["Computer Science"],
            },
            offset=1,
        )
    )
    observations.record(
        _observed(work.id, "crossref", {"updates": []}, offset=2)
    )

    evidence = evidence_context["builder"].build(work.id)

    citation = evidence.metrics.citation_performance
    assert citation.status is EvidenceStatus.OBSERVED
    assert citation.value == 94
    assert citation.evidence["primary_source"] == "openalex"
    assert citation.evidence["fwci"] == 3.25
    assert citation.evidence["semantic_scholar_cross_validation"] == {
        "citation_count": 9999,
        "influential_citation_count": 999,
    }
    assert evidence.metrics.author_impact.status is EvidenceStatus.UNKNOWN
    assert evidence.metrics.author_impact.reason == "public_author_impact_metrics_unavailable"
    assert evidence.metrics.institution_impact.status is EvidenceStatus.UNKNOWN
    assert (
        evidence.metrics.institution_impact.reason
        == "versioned_institution_impact_rule_unavailable"
    )
    assert evidence.integrity.status is IntegrityStatus.NO_KNOWN_ISSUE
    assert len(evidence.observation_ids) == 3


def test_ccf_is_used_only_when_observed_subject_scope_is_computer_science(
    evidence_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.relevance import EvidenceStatus

    cs_work, medicine_work = evidence_context["works"][:2]
    observations = evidence_context["observations"]
    for work, field in ((cs_work, "Computer Science"), (medicine_work, "Medicine")):
        observations.record(
            _observed(
                work.id,
                "semantic_scholar",
                {
                    "venue": {"name": "ICML", "type": "conference"},
                    "publication_types": ["Conference"],
                    "fields_of_study": [field],
                },
            )
        )

    computer_science = evidence_context["builder"].build(cs_work.id)
    medicine = evidence_context["builder"].build(medicine_work.id)

    assert computer_science.metrics.venue_rating.status is EvidenceStatus.OBSERVED
    assert computer_science.metrics.venue_rating.value == 100
    assert (
        computer_science.metrics.venue_rating.evidence["catalog_id"]
        == "ccf-2022-sixth-2024-rename"
    )
    assert medicine.metrics.venue_rating.status is EvidenceStatus.NOT_APPLICABLE
    assert (
        medicine.metrics.venue_rating.reason
        == "ccf_not_applicable_outside_computer_science"
    )


def test_artifact_metrics_require_an_explicit_paper_link_and_also_support_transparency(
    evidence_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.artifact_impact import ArtifactImpactMetric
    from paper_reader.recommendation.evidence_builder import QualityEvidenceBuilder
    from paper_reader.recommendation.rating_catalogs import RatingCatalogRepository
    from paper_reader.recommendation.relevance import EvidenceStatus
    from paper_reader.recommendation.venue_rating import VenueRatingService

    linked_work, unlinked_work = evidence_context["works"][2:]
    observations = evidence_context["observations"]
    observations.record(
        _observed(
            linked_work.id,
            "crossref",
            {
                "relations": {
                    "is-supplemented-by": [
                        {
                            "id-type": "uri",
                            "asserted-by": "subject",
                            "id": "https://github.com/example/paper-code",
                        }
                    ]
                }
            },
        )
    )

    class Metrics:
        def metrics_for(self, links):  # type: ignore[no-untyped-def]
            return (
                ArtifactImpactMetric(
                    url="https://github.com/example/paper-code",
                    impact_score=88,
                    metrics={"stars": 1200, "activity": "active"},
                ),
                ArtifactImpactMetric(
                    url="https://github.com/unrelated/repository",
                    impact_score=100,
                    metrics={"stars": 999999},
                ),
            )

    builder = QualityEvidenceBuilder(
        sessions=evidence_context["sessions"],
        observations=observations,
        venue_ratings=VenueRatingService(
            RatingCatalogRepository(evidence_context["sessions"])
        ),
        artifact_metrics=Metrics(),
    )
    linked = builder.build(linked_work.id)
    unlinked = builder.build(unlinked_work.id)

    assert linked.metrics.code_data_impact.status is EvidenceStatus.OBSERVED
    assert linked.metrics.code_data_impact.value == 88
    assert linked.metrics.code_data_impact.evidence["ignored_metric_count"] == 1
    assert linked.metrics.research_transparency.status is EvidenceStatus.OBSERVED
    assert unlinked.metrics.code_data_impact.status is EvidenceStatus.UNKNOWN
    assert unlinked.metrics.code_data_impact.reason == "explicit_artifact_link_missing"
    assert unlinked.metrics.research_transparency.status is EvidenceStatus.UNKNOWN


def test_missing_evidence_uses_neutral_prior_and_arxiv_only_sets_maturity(
    evidence_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.integrity import IntegrityStatus
    from paper_reader.recommendation.relevance import EvidenceStatus

    work = evidence_context["works"][3]
    evidence = evidence_context["builder"].build(work.id)

    assert evidence.metrics.citation_performance.status is EvidenceStatus.UNKNOWN
    assert evidence.metrics.venue_rating.status is EvidenceStatus.UNKNOWN
    assert evidence.metrics.publication_maturity.status is EvidenceStatus.OBSERVED
    assert evidence.metrics.publication_maturity.value == 50
    assert evidence.quality.score == 50
    assert evidence.quality.coverage == 10
    assert evidence.integrity.status is IntegrityStatus.UNKNOWN
