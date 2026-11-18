"""Measure bounded no-DOI identity ingestion against a deterministic Work corpus."""

from __future__ import annotations

import argparse
import json
import math
import platform
import shutil
import sqlite3
import statistics
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from paper_reader.database import create_session_factory, create_sqlite_engine
from paper_reader.discovery.identity import (
    IDENTITY_CANDIDATE_LIMIT,
    IdentityRepository,
    SourcePaper,
    normalize_title,
)
from paper_reader.migrations import migration_head_revision, upgrade_database
from paper_reader.recommendation.lexical import (
    LexicalIndex,
    compile_match_expression,
    multilingual_tokens,
)

DEFAULT_SEED = 20260715
TARGET_TITLE = "Tool-Augmented Agents for Scientific Discovery"
INCOMING_TITLE = "Tool Augmented Agent for Scientific Discoveries"
TARGET_AUTHORS = ("Alice Chen", "Ravi Kumar")


def _work_id(index: int) -> str:
    return f"identity-{index:027d}"


def _index_text(value: str) -> str:
    return " ".join(multilingual_tokens(value))


def _seed_database(database: Path, *, count: int, seed: int) -> None:
    upgrade_database(database)
    observed = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seed % 86_400)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        for start in range(0, count, 5_000):
            works: list[tuple[object, ...]] = []
            searches: list[tuple[str, str, str, str]] = []
            for index in range(start, min(start + 5_000, count)):
                title = f"Evidence Synthesis Method {index} for Research"
                authors = [f"Researcher {index % 40_000}"]
                if index == count - 1:
                    title = TARGET_TITLE
                    authors = list(TARGET_AUTHORS)
                timestamp = (observed + timedelta(seconds=index)).isoformat()
                works.append(
                    (
                        _work_id(index),
                        title,
                        normalize_title(title),
                        json.dumps(authors),
                        timestamp,
                    )
                )
                searches.append(
                    (
                        _work_id(index),
                        _index_text(title),
                        "",
                        _index_text(" ".join(authors)),
                    )
                )
            connection.executemany(
                "INSERT INTO works "
                "(id, canonical_title, normalized_title, authors, abstract, doi, "
                "published_at, status, merged_into_work_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, NULL, NULL, 'active', NULL, ?, ?)",
                ((*row, row[-1]) for row in works),
            )
            connection.executemany(
                "INSERT INTO work_search (work_id, title, abstract, authors) VALUES (?, ?, ?, ?)",
                searches,
            )
            connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def _validate_database(database: Path, *, count: int, seed: int) -> None:
    expected_created_at = (
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=(seed % 86_400) + count - 1)
    ).isoformat()
    connection = sqlite3.connect(database)
    try:
        shape = (
            int(connection.execute("SELECT count(*) FROM works").fetchone()[0]),
            int(connection.execute("SELECT count(*) FROM work_search").fetchone()[0]),
            int(connection.execute("SELECT count(*) FROM source_records").fetchone()[0]),
        )
        sentinel = connection.execute(
            "SELECT canonical_title, normalized_title, authors, created_at FROM works WHERE id = ?",
            (_work_id(count - 1),),
        ).fetchone()
    finally:
        connection.close()
    expected_sentinel = (
        TARGET_TITLE,
        normalize_title(TARGET_TITLE),
        json.dumps(list(TARGET_AUTHORS)),
        expected_created_at,
    )
    if shape != (count, count, 0) or sentinel != expected_sentinel:
        raise ValueError("benchmark database does not match the requested deterministic seed/count")


def _percentile_95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _ingest_sample(
    repository: IdentityRepository,
    *,
    sequence: int,
) -> tuple[float, int, str]:
    started = time.perf_counter()
    result = repository.ingest(
        SourcePaper(
            connector_id="identity_benchmark",
            external_id=f"sample-{sequence}",
            title=INCOMING_TITLE,
            authors=TARGET_AUTHORS,
        ),
        now=datetime(2026, 7, 15, 1, tzinfo=UTC) + timedelta(minutes=sequence),
    )
    elapsed_ms = (time.perf_counter() - started) * 1_000
    diagnostics = result.candidate_search
    if diagnostics is None:
        raise RuntimeError("benchmark ingest did not execute candidate retrieval")
    if diagnostics.candidate_count > IDENTITY_CANDIDATE_LIMIT:
        raise RuntimeError("identity candidate hard limit was exceeded")
    return elapsed_ms, diagnostics.candidate_count, result.match_kind.value


def run_benchmark(
    database: Path,
    *,
    count: int,
    seed: int,
    warmups: int,
    repeats: int,
) -> dict[str, Any]:
    if not database.exists():
        _seed_database(database, count=count, seed=seed)
    else:
        upgrade_database(database)
    _validate_database(database, count=count, seed=seed)

    with tempfile.TemporaryDirectory(prefix="paper-reader-identity-run-") as directory:
        working_database = Path(directory) / "benchmark.sqlite3"
        shutil.copy2(database, working_database)
        engine = create_sqlite_engine(working_database)
        sessions = create_session_factory(engine)
        repository = IdentityRepository(sessions, work_index=LexicalIndex(sessions))
        try:
            for sequence in range(warmups):
                _ingest_sample(repository, sequence=sequence)
            samples: list[float] = []
            candidate_counts: list[int] = []
            match_kinds: list[str] = []
            for sequence in range(warmups, warmups + repeats):
                elapsed_ms, candidate_count, match_kind = _ingest_sample(
                    repository,
                    sequence=sequence,
                )
                samples.append(elapsed_ms)
                candidate_counts.append(candidate_count)
                match_kinds.append(match_kind)

            title_expression = compile_match_expression(INCOMING_TITLE, prefix=True)
            author_expression = compile_match_expression(
                " ".join(TARGET_AUTHORS),
                prefix=True,
            )
            with engine.connect() as connection:
                fts_plan = [
                    str(row[3])
                    for row in connection.exec_driver_sql(
                        "EXPLAIN QUERY PLAN SELECT work_id FROM work_search "
                        "WHERE work_search MATCH ? "
                        "ORDER BY bm25(work_search, 0.0, 5.0, 0.0, 2.0), work_id "
                        "LIMIT ?",
                        (
                            f"title : ({title_expression}) OR authors : ({author_expression})",
                            IDENTITY_CANDIDATE_LIMIT,
                        ),
                    )
                ]
                prefix_plan = [
                    str(row[3])
                    for row in connection.exec_driver_sql(
                        "EXPLAIN QUERY PLAN SELECT id FROM works "
                        "WHERE status = 'active' AND normalized_title >= ? "
                        "AND normalized_title < ? ORDER BY normalized_title, id LIMIT ?",
                        (
                            "tool augmented",
                            "tool augmented\U0010ffff",
                            192,
                        ),
                    )
                ]
                year_plan = [
                    str(row[3])
                    for row in connection.exec_driver_sql(
                        "EXPLAIN QUERY PLAN SELECT id FROM works "
                        "WHERE status = 'active' AND published_at >= ? "
                        "AND published_at < ? ORDER BY published_at, id LIMIT ?",
                        (
                            "2026-01-01T00:00:00Z",
                            "2027-01-01T00:00:00Z",
                            64,
                        ),
                    )
                ]
        finally:
            engine.dispose()

    return {
        "schema_revision": migration_head_revision(),
        "seed": seed,
        "work_count": count,
        "database_bytes": database.stat().st_size,
        "warmups": warmups,
        "repeats": repeats,
        "candidate_limit": IDENTITY_CANDIDATE_LIMIT,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "measurement": {
            "min_ms": round(min(samples), 3),
            "median_ms": round(statistics.median(samples), 3),
            "p95_ms": round(_percentile_95(samples), 3),
            "max_ms": round(max(samples), 3),
            "samples_ms": [round(sample, 3) for sample in samples],
            "candidate_counts": candidate_counts,
            "match_kinds": match_kinds,
        },
        "query_plans": {
            "fts_candidates": fts_plan,
            "sql_prefix_fallback": prefix_plan,
            "sql_year_fallback": year_plan,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--count", type=int, default=200_000)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=20)
    arguments = parser.parse_args()
    if arguments.count < 1 or arguments.warmups < 0 or arguments.repeats < 1:
        parser.error("count/repeats must be positive and warmups must be non-negative")

    if arguments.database is None:
        with tempfile.TemporaryDirectory(prefix="paper-reader-identity-") as directory:
            result = run_benchmark(
                Path(directory) / "benchmark.sqlite3",
                count=arguments.count,
                seed=arguments.seed,
                warmups=arguments.warmups,
                repeats=arguments.repeats,
            )
    else:
        result = run_benchmark(
            arguments.database.resolve(),
            count=arguments.count,
            seed=arguments.seed,
            warmups=arguments.warmups,
            repeats=arguments.repeats,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
