from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def venue_service(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.recommendation.rating_catalogs import (
        RatingCatalogKind,
        RatingCatalogRepository,
    )
    from paper_reader.recommendation.venue_rating import VenueRatingService

    engine = create_sqlite_engine(tmp_path / "venue-rating.sqlite3")
    Base.metadata.create_all(engine)
    repository = RatingCatalogRepository(create_session_factory(engine))
    for kind, rating in (
        (RatingCatalogKind.JCR, "Q1"),
        (RatingCatalogKind.CAS, "2区"),
        (RatingCatalogKind.EMERGING, "C"),
    ):
        repository.import_bytes(
            kind=kind,
            catalog_name=f"{kind.value} local",
            catalog_version="2025",
            source_note="licensed local fixture",
            filename=f"{kind.value}.csv",
            content=f"venue,rating\nJournal One,{rating}\n".encode(),
            rights_confirmed=True,
        )
    try:
        yield repository, VenueRatingService(repository)
    finally:
        engine.dispose()


def test_journal_rating_keeps_jcr_cas_and_emerging_evidence_independent(
    venue_service,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.relevance import EvidenceStatus

    _, service = venue_service
    evidence = service.score(
        "Journal One",
        venue_type="journal",
        is_computer_science=False,
    )

    assert evidence.status is EvidenceStatus.OBSERVED
    assert evidence.value == 75.25
    assert evidence.confidence == 100
    assert evidence.evidence["weights"] == {"jcr": 0.34, "cas": 0.33, "emerging": 0.33}
    assert [item["kind"] for item in evidence.evidence["components"]] == [
        "jcr",
        "cas",
        "emerging",
    ]
    assert len({item["catalog_id"] for item in evidence.evidence["components"]}) == 3


def test_missing_local_catalog_evidence_reweights_observed_sources_and_lowers_confidence(
    venue_service,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.rating_catalogs import RatingCatalogKind

    repository, service = venue_service
    repository.deactivate(RatingCatalogKind.EMERGING)

    evidence = service.score(
        "Journal One",
        venue_type="journal",
        is_computer_science=False,
    )

    assert evidence.value == pytest.approx(87.686567, abs=0.000001)
    assert evidence.confidence == 67
    assert evidence.evidence["coverage"] == 67


def test_absence_from_a_rating_list_is_unknown_not_low_quality(venue_service) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.relevance import EvidenceStatus

    _, service = venue_service
    evidence = service.score(
        "An Unlisted Biomedical Journal",
        venue_type="journal",
        is_computer_science=False,
    )

    assert evidence.status is EvidenceStatus.UNKNOWN
    assert evidence.value is None
    assert evidence.reason == "venue_not_found_in_active_rating_catalogs"


def test_computer_conferences_use_ccf_while_non_computer_venues_do_not(
    venue_service,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.relevance import EvidenceStatus

    _, service = venue_service
    ccf = service.score("NeurIPS", venue_type="conference", is_computer_science=True)
    outside_scope = service.score(
        "NeurIPS",
        venue_type="conference",
        is_computer_science=False,
    )

    assert ccf.status is EvidenceStatus.OBSERVED
    assert ccf.value == 100
    assert ccf.evidence["catalog_id"] == "ccf-2022-sixth-2024-rename"
    assert outside_scope.status is EvidenceStatus.NOT_APPLICABLE
