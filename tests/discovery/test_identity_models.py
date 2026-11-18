from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def identity_repository(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository
    from paper_reader.migrations import upgrade_database
    from paper_reader.recommendation.lexical import LexicalIndex

    database = tmp_path / "identity.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    repository = IdentityRepository(sessions, work_index=LexicalIndex(sessions))
    try:
        yield repository
    finally:
        engine.dispose()


def test_source_identity_is_unique_and_retains_field_provenance(identity_repository) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import SourcePaper

    paper = SourcePaper(
        connector_id="arxiv",
        external_id="2401.01234v2",
        title="A Reliable Agent for Literature Review",
        authors=("Alice Example", "Bob Example"),
        doi="https://doi.org/10.1000/ABC.123",
        published_at=datetime(2024, 1, 4, tzinfo=UTC),
        updated_at=datetime(2024, 2, 9, tzinfo=UTC),
        field_provenance={
            "title": "arxiv:2401.01234v2",
            "doi": "arxiv:2401.01234v2",
        },
        raw_payload={"id": "2401.01234v2"},
    )

    result = identity_repository.ingest(paper)

    assert result.work.doi == "10.1000/abc.123"
    assert result.source_record.field_provenance["title"] == "arxiv:2401.01234v2"
    assert result.source_record.raw_payload == {"id": "2401.01234v2"}

    with pytest.raises(IntegrityError):
        identity_repository.insert_source_record_for_test(
            work_id=result.work.id,
            paper=paper,
        )


def test_exact_doi_merges_cross_source_records_into_one_work(identity_repository) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import MatchKind, SourcePaper

    arxiv = identity_repository.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2401.01234v1",
            title="Diffusion Models for Cancer Imaging",
            authors=("Li Ming", "Ana Smith"),
            doi="10.5555/example.42",
        )
    )
    europe_pmc = identity_repository.ingest(
        SourcePaper(
            connector_id="europe_pmc",
            external_id="MED/12345678",
            title="Diffusion Models for Cancer Imaging",
            authors=("Ming Li", "Ana Smith"),
            doi="doi:10.5555/EXAMPLE.42",
        )
    )

    assert europe_pmc.work.id == arxiv.work.id
    assert europe_pmc.match_kind is MatchKind.EXACT_DOI
    assert identity_repository.count_works() == 1
    assert identity_repository.count_source_records() == 2


def test_ambiguous_title_author_match_creates_review_instead_of_silent_merge(
    identity_repository,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import MatchKind, SourcePaper

    first = identity_repository.ingest(
        SourcePaper(
            connector_id="openreview",
            external_id="forum-a",
            title="Tool-Augmented Agents for Scientific Discovery",
            authors=("A. Chen", "R. Kumar"),
        )
    )
    candidate = identity_repository.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2601.00002v1",
            title="Tool Augmented Agent for Scientific Discoveries",
            authors=("Alice Chen", "Ravi Kumar"),
        )
    )

    assert candidate.work.id != first.work.id
    assert candidate.match_kind is MatchKind.REVIEW_REQUIRED
    assert candidate.merge_review is not None
    assert candidate.merge_review.status == "pending"
    assert candidate.merge_review.similarity >= 0.75
    assert identity_repository.count_works() == 2


@pytest.mark.parametrize(
    (
        "case_id",
        "original_title",
        "incoming_title",
        "authors",
        "incoming_authors",
        "expected_kind",
    ),
    (
        (
            "punctuation-case-author-order",
            "Reliable Agents: Evidence Synthesis",
            "RELIABLE AGENTS — EVIDENCE SYNTHESIS",
            ("Alice Chen", "Ravi Kumar"),
            ("Ravi Kumar", "Alice Chen"),
            "auto_similarity",
        ),
        (
            "mixed-language-spacing",
            "癌症影像 Diffusion 模型研究",
            "癌症影像Diffusion模型研究",
            ("李明", "Ana Smith"),
            ("Ana Smith", "李明"),
            "review_required",
        ),
        (
            "light-rewrite",
            "Robust Agent Planning for Literature Review",
            "Robust Agents Planning in Literature Reviews",
            ("A. Chen", "R. Kumar"),
            ("Alice Chen", "Ravi Kumar"),
            "review_required",
        ),
        (
            "unrelated-title",
            "Diffusion Models for Oncology Imaging",
            "Quantum Networking Security Guarantees",
            ("Shared Author",),
            ("Shared Author",),
            "new",
        ),
    ),
)
def test_cross_source_identity_golden_boundaries(
    identity_repository,
    case_id: str,
    original_title: str,
    incoming_title: str,
    authors: tuple[str, ...],
    incoming_authors: tuple[str, ...],
    expected_kind: str,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import MatchKind, SourcePaper

    original = identity_repository.ingest(
        SourcePaper(
            connector_id="openreview",
            external_id=f"golden-original-{case_id}",
            title=original_title,
            authors=authors,
        )
    )
    incoming = identity_repository.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id=f"golden-incoming-{case_id}",
            title=incoming_title,
            authors=incoming_authors,
        )
    )

    assert incoming.match_kind is MatchKind(expected_kind)
    assert incoming.candidate_search is not None
    assert incoming.candidate_search.strategy == "fts"
    assert incoming.candidate_search.degraded is False
    if incoming.match_kind is MatchKind.AUTO_SIMILARITY:
        assert incoming.work.id == original.work.id
    else:
        assert incoming.work.id != original.work.id
    if incoming.match_kind is MatchKind.REVIEW_REQUIRED:
        assert incoming.merge_review is not None
        assert incoming.merge_review.evidence["candidate_strategy"] == "fts"


def test_representative_document_is_legal_then_latest_and_most_complete(
    identity_repository,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import SourcePaper

    result = identity_repository.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2502.01010v1",
            title="Multilingual Retrieval for Research Agents",
            authors=("A. Researcher",),
        )
    )
    identity_repository.add_document_version(
        work_id=result.work.id,
        content_sha256="1" * 64,
        access_status="restricted",
        completeness=100,
        source_updated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    older = identity_repository.add_document_version(
        work_id=result.work.id,
        content_sha256="2" * 64,
        access_status="open",
        completeness=80,
        source_updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    latest = identity_repository.add_document_version(
        work_id=result.work.id,
        content_sha256="3" * 64,
        access_status="user_provided",
        completeness=90,
        source_updated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    representative = identity_repository.representative_document(result.work.id)

    assert representative is not None
    assert representative.id == latest.id
    assert representative.id != older.id


def test_identity_domain_validates_inputs_reuses_sources_and_auto_merges_exact_titles(
    identity_repository,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import MatchKind, SourcePaper, paper_similarity

    with pytest.raises(ValueError, match="connector_id"):
        identity_repository.ingest(
            SourcePaper(
                connector_id=" ",
                external_id="record",
                title="Valid title",
                authors=(),
            )
        )
    with pytest.raises(ValueError, match="title"):
        identity_repository.ingest(
            SourcePaper(
                connector_id="arxiv",
                external_id="record",
                title=" -- ",
                authors=(),
            )
        )

    original_paper = SourcePaper(
        connector_id="arxiv",
        external_id="2607.01010v1",
        title="Exact Identity for Reliable Research Agents",
        authors=("Alice Chen",),
    )
    original = identity_repository.ingest(original_paper)
    repeated = identity_repository.ingest(original_paper)
    cross_source = identity_repository.ingest(
        SourcePaper(
            connector_id="openreview",
            external_id="forum-exact-identity",
            title=original_paper.title,
            authors=original_paper.authors,
        )
    )

    assert repeated.match_kind is MatchKind.EXISTING_SOURCE
    assert repeated.work.id == original.work.id
    assert cross_source.match_kind is MatchKind.AUTO_SIMILARITY
    assert cross_source.work.id == original.work.id
    score, evidence = paper_similarity(
        SourcePaper(
            connector_id="fixture",
            external_id="blank-author",
            title=original_paper.title,
            authors=("   ",),
        ),
        original.work,
    )
    assert score > 0
    assert evidence["authors"] == 0

    with pytest.raises(ValueError, match="64 lowercase"):
        identity_repository.add_document_version(
            work_id=original.work.id,
            content_sha256="invalid",
            access_status="open",
            completeness=100,
            source_updated_at=None,
        )
    with pytest.raises(ValueError, match="access_status"):
        identity_repository.add_document_version(
            work_id=original.work.id,
            content_sha256="a" * 64,
            access_status="pirated",
            completeness=100,
            source_updated_at=None,
        )
    with pytest.raises(ValueError, match="completeness"):
        identity_repository.add_document_version(
            work_id=original.work.id,
            content_sha256="a" * 64,
            access_status="open",
            completeness=101,
            source_updated_at=None,
        )
    with pytest.raises(ValueError, match="unsupported"):
        identity_repository.list_merge_reviews(status="invalid")
    with pytest.raises(ValueError, match="timezone-aware"):
        identity_repository.decide_merge_review(
            "missing-review",
            decision="keep_separate",
            now=datetime(2026, 7, 13),
        )
    with pytest.raises(KeyError):
        identity_repository.decide_merge_review(
            "missing-review",
            decision="keep_separate",
            now=datetime(2026, 7, 13, tzinfo=UTC),
        )
