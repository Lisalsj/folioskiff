"""Generate a deterministic large library and measure list/search latency."""

from __future__ import annotations

import argparse
import json
import math
import platform
import sqlite3
import statistics
import tempfile
import time
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.database import create_session_factory, create_sqlite_engine
from paper_reader.discovery.models import WorkRecord
from paper_reader.migrations import migration_head_revision, upgrade_database
from paper_reader.reader.models import LibraryEntryRecord
from paper_reader.reader.repository import (
    LibraryPage,
    LibraryRepository,
    _work_search_text,
)
from paper_reader.recommendation.lexical import compile_match_expression, multilingual_tokens

DEFAULT_SEED = 20260715


class _LegacyPythonScanRepository:
    """Diagnostic copy of the pre-0031 full-table implementation."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions
        self._domain = LibraryRepository(sessions)

    def list_entries(
        self,
        *,
        query: str | None,
        limit: int,
        offset: int,
    ) -> LibraryPage:
        normalized_query = (
            " ".join(unicodedata.normalize("NFKC", query).casefold().split())
            if query is not None
            else ""
        )
        with self._sessions() as session:
            rows = list(
                session.execute(
                    select(LibraryEntryRecord, WorkRecord)
                    .join(WorkRecord, WorkRecord.id == LibraryEntryRecord.work_id)
                    .where(WorkRecord.status == "active")
                    .order_by(LibraryEntryRecord.created_at.desc(), WorkRecord.id)
                )
            )
            if normalized_query:
                rows = [row for row in rows if normalized_query in _work_search_text(row[1])]
            selected = rows[offset : offset + limit]
            items = tuple(
                self._domain._work_domain(session, work, entry) for entry, work in selected
            )
        return LibraryPage(
            items=items,
            total=len(rows),
            limit=limit,
            offset=offset,
        )


def _work_id(index: int) -> str:
    return f"work-{index:031d}"


def _index_text(value: str) -> str:
    return " ".join(multilingual_tokens(value))


def _seed_database(database: Path, *, count: int, seed: int) -> None:
    upgrade_database(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        observed = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seed % 86_400)
        for start in range(0, count, 5_000):
            works: list[tuple[object, ...]] = []
            entries: list[tuple[object, ...]] = []
            searches: list[tuple[str, str, str, str]] = []
            states: list[tuple[object, ...]] = []
            documents: list[tuple[object, ...]] = []
            for index in range(start, min(start + 5_000, count)):
                work_id = _work_id(index)
                title = f"Evidence Synthesis Paper {index}"
                if index % 997 == 0:
                    title = f"癌症影像研究 {index}"
                authors = ["Alice Chen"] if index % 983 == 0 else [f"Author {index % 5000}"]
                doi = f"10.1234/paper.{index:06d}"
                timestamp = (observed + timedelta(seconds=index)).isoformat()
                works.append(
                    (
                        work_id,
                        title,
                        title.casefold(),
                        json.dumps(authors),
                        doi,
                        timestamp,
                    )
                )
                entries.append((work_id, timestamp))
                searches.append((work_id, _index_text(title), "", _index_text(" ".join(authors))))
                if index % 100 == 0:
                    states.append((work_id, index % 200 == 0, timestamp))
                if index % 1000 == 0:
                    documents.append(
                        (
                            f"doc-{index:032d}",
                            work_id,
                            f"{index:064x}"[-64:],
                            timestamp,
                        )
                    )
            connection.executemany(
                "INSERT INTO works "
                "(id, canonical_title, normalized_title, authors, abstract, doi, "
                "published_at, status, merged_into_work_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, ?, NULL, 'active', NULL, ?, ?)",
                ((*row, row[-1]) for row in works),
            )
            connection.executemany(
                "INSERT INTO library_entries "
                "(work_id, added_via, created_at, updated_at) "
                "VALUES (?, 'save', ?, ?)",
                ((work_id, timestamp, timestamp) for work_id, timestamp in entries),
            )
            connection.executemany(
                "INSERT INTO work_search (work_id, title, abstract, authors) VALUES (?, ?, ?, ?)",
                searches,
            )
            connection.executemany(
                "INSERT INTO library_states "
                "(work_id, favorite, feedback, feedback_reasons, pinned, reading_progress, "
                "analysis_state, created_at, updated_at) "
                "VALUES (?, ?, 'neutral', '[]', 0, 0, 'not_analyzed', ?, ?)",
                (
                    (work_id, favorite, timestamp, timestamp)
                    for work_id, favorite, timestamp in states
                ),
            )
            connection.executemany(
                "INSERT INTO document_versions "
                "(id, work_id, source_record_id, content_sha256, access_status, media_type, "
                "completeness, source_updated_at, extraction_status, relative_path, byte_size, "
                "original_filename, acquisition_kind, acquisition_url, last_accessed_at, "
                "created_at) VALUES (?, ?, NULL, ?, 'open', 'application/pdf', 100, ?, "
                "'complete', NULL, 0, NULL, 'source', NULL, NULL, ?)",
                (
                    (doc_id, work_id, sha256, timestamp, timestamp)
                    for doc_id, work_id, sha256, timestamp in documents
                ),
            )
            connection.commit()
    finally:
        connection.close()


def _percentile_95(samples: list[float]) -> float:
    ordered = sorted(samples)
    rank = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[rank]


def _measure(call: object, *, warmups: int, repeats: int) -> dict[str, object]:
    function = call
    assert callable(function)
    for _ in range(warmups):
        function()
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "min_ms": round(min(samples), 3),
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(_percentile_95(samples), 3),
        "max_ms": round(max(samples), 3),
        "samples_ms": [round(sample, 3) for sample in samples],
    }


def run_benchmark(
    database: Path,
    *,
    count: int,
    seed: int,
    warmups: int,
    repeats: int,
    legacy_python_scan: bool = False,
) -> dict[str, Any]:
    if not database.exists():
        _seed_database(database, count=count, seed=seed)
    else:
        upgrade_database(database)
    expected_start = (
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seed % 86_400)
    ).isoformat()
    validation_connection = sqlite3.connect(database)
    try:
        actual_shape = tuple(
            int(validation_connection.execute(sql).fetchone()[0])
            for sql in (
                "SELECT count(*) FROM works",
                "SELECT count(*) FROM library_entries",
                "SELECT count(*) FROM work_search",
                "SELECT count(*) FROM library_states",
                "SELECT count(*) FROM document_versions",
            )
        )
        first_work = validation_connection.execute(
            "SELECT canonical_title, authors, doi, created_at FROM works WHERE id = ?",
            (_work_id(0),),
        ).fetchone()
    finally:
        validation_connection.close()
    expected_shape = (
        count,
        count,
        count,
        ((count - 1) // 100) + 1,
        ((count - 1) // 1000) + 1,
    )
    expected_first = (
        "癌症影像研究 0",
        json.dumps(["Alice Chen"]),
        "10.1234/paper.000000",
        expected_start,
    )
    if actual_shape != expected_shape or first_work != expected_first:
        raise ValueError(
            "benchmark database does not match the requested deterministic seed/count"
        )
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    repository = (
        _LegacyPythonScanRepository(sessions) if legacy_python_scan else LibraryRepository(sessions)
    )
    queries = {
        "first_page": lambda: repository.list_entries(query=None, limit=20, offset=0),
        "deep_page": lambda: repository.list_entries(
            query=None,
            limit=20,
            offset=max(0, count // 2),
        ),
        "chinese_title": lambda: repository.list_entries(
            query="癌症影像",
            limit=20,
            offset=0,
        ),
        "english_author": lambda: repository.list_entries(
            query="Alice Chen",
            limit=20,
            offset=0,
        ),
        "doi_prefix": lambda: repository.list_entries(
            query=f"10.1234/paper.{max(0, count - 1):06d}"[:-1],
            limit=20,
            offset=0,
        ),
    }
    try:
        measurements = {
            name: _measure(call, warmups=warmups, repeats=repeats) for name, call in queries.items()
        }
        with engine.connect() as connection:
            order_plan = [
                str(row[3])
                for row in connection.exec_driver_sql(
                    "EXPLAIN QUERY PLAN "
                    "SELECT library_entries.work_id FROM library_entries "
                    "WHERE EXISTS (SELECT 1 FROM works "
                    "WHERE works.id = library_entries.work_id "
                    "AND works.status = 'active') "
                    "ORDER BY library_entries.created_at DESC, "
                    "library_entries.work_id ASC LIMIT 20 OFFSET ?",
                    (max(0, count // 2),),
                )
            ]
            fts_plan = [
                str(row[3])
                for row in connection.exec_driver_sql(
                    "EXPLAIN QUERY PLAN "
                    "SELECT library_entries.work_id FROM library_entries "
                    "JOIN work_search ON work_search.work_id = library_entries.work_id "
                    "JOIN works ON works.id = library_entries.work_id "
                    "WHERE works.status = 'active' AND work_search MATCH ? "
                    "ORDER BY library_entries.created_at DESC, "
                    "library_entries.work_id ASC LIMIT 20 OFFSET 0",
                    (
                        compile_match_expression(
                            "癌症影像",
                            operator="AND",
                            prefix=True,
                        ),
                    ),
                )
            ]
            doi_prefix = f"10.1234/paper.{max(0, count - 1):06d}"[:-1]
            doi_plan = [
                str(row[3])
                for row in connection.exec_driver_sql(
                    "EXPLAIN QUERY PLAN "
                    "SELECT library_entries.work_id FROM library_entries "
                    "JOIN works ON works.id = library_entries.work_id "
                    "WHERE works.status = 'active' AND works.doi >= ? AND works.doi < ? "
                    "ORDER BY library_entries.created_at DESC, "
                    "library_entries.work_id ASC LIMIT 20 OFFSET 0",
                    (doi_prefix, f"{doi_prefix}\U0010ffff"),
                )
            ]
    finally:
        engine.dispose()
    return {
        "schema_revision": migration_head_revision(),
        "implementation": "legacy_python_scan" if legacy_python_scan else "sqlite_paged",
        "seed": seed,
        "work_count": count,
        "warmups": warmups,
        "repeats": repeats,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "measurements": measurements,
        "query_plans": {
            "ordered_page": order_plan,
            "fts_search": fts_plan,
            "doi_prefix": doi_plan,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--count", type=int, default=200_000)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument(
        "--legacy-python-scan",
        action="store_true",
        help="Re-run the pre-0031 full-table implementation for before/after evidence",
    )
    arguments = parser.parse_args()
    if arguments.count < 1 or arguments.warmups < 0 or arguments.repeats < 1:
        parser.error("count/repeats must be positive and warmups must be non-negative")

    if arguments.database is None:
        with tempfile.TemporaryDirectory(prefix="paper-reader-library-") as directory:
            result = run_benchmark(
                Path(directory) / "benchmark.sqlite3",
                count=arguments.count,
                seed=arguments.seed,
                warmups=arguments.warmups,
                repeats=arguments.repeats,
                legacy_python_scan=arguments.legacy_python_scan,
            )
    else:
        result = run_benchmark(
            arguments.database.resolve(),
            count=arguments.count,
            seed=arguments.seed,
            warmups=arguments.warmups,
            repeats=arguments.repeats,
            legacy_python_scan=arguments.legacy_python_scan,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
