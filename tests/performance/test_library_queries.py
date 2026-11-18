from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, event, text

WORK_COUNT = 20_000


def _work_id(index: int) -> str:
    return f"work-{index:031d}"


def _indexed(value: str) -> str:
    from paper_reader.recommendation.lexical import multilingual_tokens

    return " ".join(multilingual_tokens(value))


def test_library_benchmark_releases_its_database_handle(tmp_path) -> None:
    from scripts.benchmark_library import run_benchmark

    database = tmp_path / "disposable-benchmark.sqlite3"
    result = run_benchmark(
        database,
        count=20,
        seed=20260715,
        warmups=0,
        repeats=1,
    )

    assert result["work_count"] == 20
    database.unlink()
    assert not database.exists()


@pytest.fixture(scope="module")
def large_library(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.migrations import upgrade_database
    from paper_reader.reader.repository import LibraryRepository

    root = tmp_path_factory.mktemp("large-library")
    database = root / "paper-reader.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    observed = datetime(2026, 1, 1, tzinfo=UTC)

    work_rows: list[dict[str, object]] = []
    entry_rows: list[dict[str, object]] = []
    search_rows: list[dict[str, str]] = []
    state_rows: list[dict[str, object]] = []
    document_rows: list[dict[str, object]] = []
    for index in range(WORK_COUNT):
        work_id = _work_id(index)
        title = f"Evidence Synthesis Paper {index}"
        if index % 997 == 0:
            title = f"癌症影像研究 {index}"
        authors = ["Alice Chen"] if index % 983 == 0 else [f"Author {index % 5000}"]
        doi = f"10.1234/paper.{index:06d}"
        created_at = (observed + timedelta(seconds=index)).isoformat()
        work_rows.append(
            {
                "id": work_id,
                "title": title,
                "normalized_title": title.casefold(),
                "authors": json.dumps(authors),
                "doi": doi,
                "created_at": created_at,
            }
        )
        entry_rows.append(
            {
                "work_id": work_id,
                "created_at": created_at,
            }
        )
        search_rows.append(
            {
                "work_id": work_id,
                "title": _indexed(title),
                "authors": _indexed(" ".join(authors)),
            }
        )
        if index % 100 == 0:
            state_rows.append(
                {
                    "work_id": work_id,
                    "favorite": index % 200 == 0,
                    "created_at": created_at,
                }
            )
        if index % 1000 == 0:
            document_rows.append(
                {
                    "id": f"doc-{index:032d}",
                    "work_id": work_id,
                    "sha256": f"{index:064x}"[-64:],
                    "created_at": created_at,
                }
            )

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO works "
                "(id, canonical_title, normalized_title, authors, abstract, doi, "
                "published_at, status, merged_into_work_id, created_at, updated_at) "
                "VALUES (:id, :title, :normalized_title, :authors, NULL, :doi, NULL, "
                "'active', NULL, :created_at, :created_at)"
            ),
            work_rows,
        )
        connection.execute(
            text(
                "INSERT INTO library_entries "
                "(work_id, added_via, created_at, updated_at) "
                "VALUES (:work_id, 'save', :created_at, :created_at)"
            ),
            entry_rows,
        )
        connection.execute(
            text(
                "INSERT INTO work_search (work_id, title, abstract, authors) "
                "VALUES (:work_id, :title, '', :authors)"
            ),
            search_rows,
        )
        connection.execute(
            text(
                "INSERT INTO library_states "
                "(work_id, favorite, feedback, feedback_reasons, pinned, "
                "reading_progress, analysis_state, created_at, updated_at) "
                "VALUES (:work_id, :favorite, 'neutral', '[]', 0, 0, "
                "'not_analyzed', :created_at, :created_at)"
            ),
            state_rows,
        )
        connection.execute(
            text(
                "INSERT INTO document_versions "
                "(id, work_id, source_record_id, content_sha256, access_status, "
                "media_type, completeness, source_updated_at, extraction_status, "
                "relative_path, byte_size, original_filename, acquisition_kind, "
                "acquisition_url, last_accessed_at, created_at) "
                "VALUES (:id, :work_id, NULL, :sha256, 'open', 'application/pdf', "
                "100, :created_at, 'complete', NULL, 0, NULL, 'source', NULL, NULL, "
                ":created_at)"
            ),
            document_rows,
        )

    try:
        yield LibraryRepository(sessions), engine
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


def test_large_library_uses_database_pagination_and_batched_related_reads(
    large_library,
) -> None:  # type: ignore[no-untyped-def]
    repository, engine = large_library
    with _record_selects(engine) as statements:
        page = repository.list_entries(query=None, limit=20, offset=10_000)

    assert page.total == WORK_COUNT
    assert len(page.items) == 20
    assert page.items[0].id == _work_id(WORK_COUNT - 1 - 10_000)
    page_reads = [statement for statement in statements if "library_entries" in statement]
    assert any("LIMIT" in statement.upper() for statement in page_reads)
    assert len(statements) <= 5


@pytest.mark.parametrize(
    ("query", "expected_id"),
    (
        ("癌症影像", _work_id(19_940)),
        ("Alice Chen", _work_id(19_660)),
        ("10.1234/PAPER.019999", _work_id(19_999)),
        ("10.1234/paper.01999", _work_id(19_999)),
    ),
)
def test_large_library_search_uses_bounded_sql_candidates_without_python_scan(
    large_library,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected_id: str,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.reader import repository as repository_module

    repository, engine = large_library

    def reject_python_scan(_work: object) -> str:
        raise AssertionError("library search must not scan Work rows in Python")

    monkeypatch.setattr(repository_module, "_work_search_text", reject_python_scan)
    with _record_selects(engine) as statements:
        page = repository.list_entries(query=query, limit=20, offset=0)

    assert page.total > 0
    assert page.items[0].id == expected_id
    assert len(statements) <= 5
    assert any(
        "WORK_SEARCH MATCH" in statement.upper() or "WORKS.DOI" in statement.upper()
        for statement in statements
    )


def test_library_query_indexes_exist_and_sqlite_uses_the_ordering_index(
    large_library,
) -> None:  # type: ignore[no-untyped-def]
    from scripts.benchmark_library import _percentile_95

    assert _percentile_95([1.0, 2.0, 3.0, 4.0, 5.0]) == 5.0
    _, engine = large_library
    with engine.connect() as connection:
        index_names = {
            str(row[1]) for row in connection.execute(text("PRAGMA index_list('library_entries')"))
        }
        document_indexes = {
            str(row[1])
            for row in connection.execute(text("PRAGMA index_list('document_versions')"))
        }
        work_indexes = {
            str(row[1]) for row in connection.execute(text("PRAGMA index_list('works')"))
        }
        plan = tuple(
            str(row[3])
            for row in connection.execute(
                text(
                    "EXPLAIN QUERY PLAN "
                    "SELECT library_entries.work_id FROM library_entries "
                    "WHERE EXISTS (SELECT 1 FROM works "
                    "WHERE works.id = library_entries.work_id "
                    "AND works.status = 'active') "
                    "ORDER BY library_entries.created_at DESC, "
                    "library_entries.work_id ASC LIMIT 20 OFFSET 10000"
                )
            )
        )
        doi_plan = tuple(
            str(row[3])
            for row in connection.execute(
                text(
                    "EXPLAIN QUERY PLAN "
                    "SELECT library_entries.work_id FROM library_entries "
                    "JOIN works ON works.id = library_entries.work_id "
                    "WHERE works.status = 'active' "
                    "AND works.doi >= '10.1234/paper.01999' "
                    "AND works.doi < '10.1234/paper.01999z' "
                    "ORDER BY library_entries.created_at DESC, "
                    "library_entries.work_id ASC LIMIT 20"
                )
            )
        )
        document_plan = tuple(
            str(row[3])
            for row in connection.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT id FROM document_versions "
                    "WHERE work_id = :work_id ORDER BY work_id, "
                    "source_updated_at DESC, completeness DESC, created_at DESC, id"
                ),
                {"work_id": _work_id(19_000)},
            )
        )

    assert "ix_library_entries_created_work" in index_names
    assert "ix_document_versions_work_representative" in document_indexes
    assert "ix_works_status_doi" in work_indexes
    assert any("ix_library_entries_created_work" in detail for detail in plan)
    assert any("ix_works_status_doi" in detail for detail in doi_plan)
    assert any("ix_document_versions_work_representative" in detail for detail in document_plan)
