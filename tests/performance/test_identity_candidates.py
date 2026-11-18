from __future__ import annotations

import json
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import OperationalError

WORK_COUNT = 20_000
EXPECTED_CANDIDATE_LIMIT = 256
TARGET_INDEX = WORK_COUNT - 1
TARGET_TITLE = "Tool-Augmented Agents for Scientific Discovery"
INCOMING_TITLE = "Tool Augmented Agent for Scientific Discoveries"
TARGET_AUTHORS = ("Alice Chen", "Ravi Kumar")


class MissingFtsIndex:
    def identity_candidates(self, **_kwargs: object) -> list[str]:
        raise OperationalError("work_search unavailable", {}, RuntimeError("missing FTS5"))

    def sync_work(self, _work_id: str) -> None:
        return None


def _work_id(index: int) -> str:
    return f"identity-{index:027d}"


def _indexed(value: str) -> str:
    from paper_reader.recommendation.lexical import multilingual_tokens

    return " ".join(multilingual_tokens(value))


def _percentile_95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


@pytest.fixture(scope="module")
def large_identity_database(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.migrations import upgrade_database

    root = tmp_path_factory.mktemp("large-identity")
    database = root / "paper-reader.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    observed = datetime(2026, 1, 1, tzinfo=UTC)

    work_rows: list[dict[str, object]] = []
    search_rows: list[dict[str, str]] = []
    for index in range(WORK_COUNT):
        title = f"Bounded Evidence Synthesis Method {index}"
        authors = [f"Researcher {index % 4_000}"]
        if index == TARGET_INDEX:
            title = TARGET_TITLE
            authors = list(TARGET_AUTHORS)
        created_at = (observed + timedelta(seconds=index)).isoformat()
        work_rows.append(
            {
                "id": _work_id(index),
                "title": title,
                "normalized_title": title.casefold().replace("-", " "),
                "authors": json.dumps(authors),
                "created_at": created_at,
            }
        )
        search_rows.append(
            {
                "work_id": _work_id(index),
                "title": _indexed(title),
                "authors": _indexed(" ".join(authors)),
            }
        )

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO works "
                "(id, canonical_title, normalized_title, authors, abstract, doi, "
                "published_at, status, merged_into_work_id, created_at, updated_at) "
                "VALUES (:id, :title, :normalized_title, :authors, NULL, NULL, NULL, "
                "'active', NULL, :created_at, :created_at)"
            ),
            work_rows,
        )
        connection.execute(
            text(
                "INSERT INTO work_search (work_id, title, abstract, authors) "
                "VALUES (:work_id, :title, '', :authors)"
            ),
            search_rows,
        )

    try:
        yield sessions, engine
    finally:
        engine.dispose()


@contextmanager
def _record_selects(engine: Engine) -> Iterator[list[str]]:
    statements: list[str] = []

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_identity_fts_candidates_are_ranked_and_hard_bounded(
    large_identity_database,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.lexical import LexicalIndex

    sessions, _ = large_identity_database
    candidate_ids = LexicalIndex(sessions).identity_candidates(
        title=INCOMING_TITLE,
        authors=TARGET_AUTHORS,
        limit=EXPECTED_CANDIDATE_LIMIT,
    )

    assert _work_id(TARGET_INDEX) in candidate_ids
    assert len(candidate_ids) <= EXPECTED_CANDIDATE_LIMIT


def test_no_doi_ingest_scores_only_bounded_candidates(
    large_identity_database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery import identity as identity_module
    from paper_reader.discovery.identity import IdentityRepository, MatchKind, SourcePaper
    from paper_reader.recommendation.lexical import LexicalIndex

    sessions, engine = large_identity_database
    similarity_calls: list[str] = []
    original_similarity = identity_module.paper_similarity

    def recording_similarity(paper: SourcePaper, work):  # type: ignore[no-untyped-def]
        similarity_calls.append(work.id)
        return original_similarity(paper, work)

    monkeypatch.setattr(identity_module, "paper_similarity", recording_similarity)
    repository = IdentityRepository(sessions, work_index=LexicalIndex(sessions))
    with _record_selects(engine) as statements:
        result = repository.ingest(
            SourcePaper(
                connector_id="openreview",
                external_id="bounded-candidate-ingest",
                title=INCOMING_TITLE,
                authors=TARGET_AUTHORS,
            ),
            now=datetime(2026, 7, 15, tzinfo=UTC),
        )

    assert result.match_kind is MatchKind.REVIEW_REQUIRED
    assert result.merge_review is not None
    assert result.merge_review.matched_work_id == _work_id(TARGET_INDEX)
    assert len(similarity_calls) <= EXPECTED_CANDIDATE_LIMIT
    assert result.candidate_search is not None
    assert result.candidate_search.strategy == "fts"
    assert result.candidate_search.degraded is False
    assert result.candidate_search.candidate_count == len(similarity_calls)
    assert result.candidate_search.limit == EXPECTED_CANDIDATE_LIMIT
    active_work_queries = [
        statement
        for statement in statements
        if "FROM works" in statement and "works.status" in statement
    ]
    assert active_work_queries
    assert all(
        " IN (" in statement or " LIMIT " in statement or "WHERE works.id =" in statement
        for statement in active_work_queries
    ), active_work_queries


def test_fts_unavailable_uses_bounded_prefix_fallback_and_marks_degradation(
    tmp_path: Path,
) -> None:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, MatchKind, SourcePaper
    from paper_reader.discovery.models import WorkRecord

    engine = create_sqlite_engine(tmp_path / "identity-fallback.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    target = WorkRecord(
        canonical_title=TARGET_TITLE,
        normalized_title="tool augmented agents for scientific discovery",
        authors=list(TARGET_AUTHORS),
    )
    with sessions() as session:
        session.add_all(
            [
                WorkRecord(
                    canonical_title=f"Fallback Evidence Paper {index}",
                    normalized_title=f"fallback evidence paper {index}",
                    authors=[f"Author {index}"],
                )
                for index in range(2_000)
            ]
        )
        session.add(target)
        session.commit()

    try:
        with _record_selects(engine) as statements:
            result = IdentityRepository(sessions, work_index=MissingFtsIndex()).ingest(
                SourcePaper(
                    connector_id="arxiv",
                    external_id="fallback-candidate-ingest",
                    title="Tool Augmented Agents for Scientific Discovery",
                    authors=tuple(reversed(TARGET_AUTHORS)),
                ),
                now=datetime(2026, 7, 15, tzinfo=UTC),
            )

        assert result.match_kind is MatchKind.AUTO_SIMILARITY
        assert result.work.id == target.id
        assert result.candidate_search is not None
        assert result.candidate_search.strategy == "sql_prefix"
        assert result.candidate_search.degraded is True
        assert result.candidate_search.reason == "fts_unavailable"
        assert result.candidate_search.candidate_count <= EXPECTED_CANDIDATE_LIMIT
        candidate_queries = [
            statement
            for statement in statements
            if "FROM works" in statement and "works.normalized_title" in statement
        ]
        assert candidate_queries
        assert all(" LIMIT " in statement for statement in candidate_queries)
    finally:
        engine.dispose()


def test_sql_fallback_indexes_exist_and_are_selected(
    large_identity_database,
) -> None:  # type: ignore[no-untyped-def]
    from scripts.benchmark_identity import _percentile_95

    assert _percentile_95([1.0, 2.0, 3.0, 4.0, 5.0]) == 5.0
    _, engine = large_identity_database
    with engine.connect() as connection:
        index_names = {
            str(row[1]) for row in connection.execute(text("PRAGMA index_list('works')"))
        }
        prefix_plan = tuple(
            str(row[3])
            for row in connection.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT id FROM works "
                    "WHERE status = 'active' "
                    "AND normalized_title >= 'tool augmented' "
                    "AND normalized_title < 'tool augmentedz' "
                    "ORDER BY normalized_title, id LIMIT 192"
                )
            )
        )
        year_plan = tuple(
            str(row[3])
            for row in connection.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT id FROM works "
                    "WHERE status = 'active' "
                    "AND published_at >= '2026-01-01T00:00:00Z' "
                    "AND published_at < '2027-01-01T00:00:00Z' "
                    "ORDER BY published_at, id LIMIT 64"
                )
            )
        )

    assert "ix_works_status_normalized_title" in index_names
    assert "ix_works_status_published_at" in index_names
    assert any("ix_works_status_normalized_title" in detail for detail in prefix_plan)
    assert any("ix_works_status_published_at" in detail for detail in year_plan)
    assert not any("TEMP B-TREE" in detail for detail in prefix_plan)
    assert not any("TEMP B-TREE" in detail for detail in year_plan)


def test_20k_identity_candidate_ingest_p95_is_within_library_budget(
    large_identity_database,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.recommendation.lexical import LexicalIndex

    sessions, _ = large_identity_database
    repository = IdentityRepository(sessions, work_index=LexicalIndex(sessions))
    samples: list[float] = []
    for index in range(20):
        started = time.perf_counter()
        result = repository.ingest(
            SourcePaper(
                connector_id="ieee",
                external_id=f"identity-performance-{index}",
                title=INCOMING_TITLE,
                authors=TARGET_AUTHORS,
            ),
            now=datetime(2026, 7, 15, 1, index, tzinfo=UTC),
        )
        samples.append((time.perf_counter() - started) * 1_000)
        assert result.candidate_search is not None
        assert result.candidate_search.candidate_count <= EXPECTED_CANDIDATE_LIMIT

    assert _percentile_95(samples) <= 800.0
