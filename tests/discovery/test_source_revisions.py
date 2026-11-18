from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

NOW = datetime(2026, 7, 15, 1, 30, tzinfo=UTC)


class RecordingWorkIndex:
    def __init__(self) -> None:
        self.synced: list[str] = []

    def sync_work(self, work_id: str) -> None:
        self.synced.append(work_id)


@pytest.fixture
def revision_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository

    engine = create_sqlite_engine(tmp_path / "source-revisions.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    work_index = RecordingWorkIndex()
    try:
        yield {
            "engine": engine,
            "sessions": sessions,
            "repository": IdentityRepository(sessions, work_index=work_index),
            "work_index": work_index,
        }
    finally:
        engine.dispose()


def _paper(**overrides):  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import SourcePaper

    values = {
        "connector_id": "arxiv",
        "external_id": "2607.01234",
        "title": "Reliable agents for paper discovery",
        "authors": ("Alice Example", "Bob Example"),
        "abstract": "A reproducible agent-assisted discovery method.",
        "doi": "10.1000/paper-reader.1",
        "published_at": datetime(2026, 7, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 10, tzinfo=UTC),
        "landing_url": "https://arxiv.org/abs/2607.01234",
        "field_provenance": {"title": "arxiv:2607.01234"},
        "raw_payload": {"version": "v1"},
    }
    values.update(overrides)
    return SourcePaper(**values)


def test_initial_ingest_creates_one_baseline_revision_and_identical_refresh_is_idempotent(
    revision_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import DiscoveryKind
    from paper_reader.discovery.models import (
        PaperUpdateRecord,
        SourceRecord,
        SourceRevisionRecord,
    )

    repository = revision_context["repository"]
    sessions = revision_context["sessions"]
    first = repository.ingest(_paper(), now=NOW)
    repeated = repository.ingest(_paper(), now=NOW + timedelta(hours=2))

    assert first.discovery_kind is DiscoveryKind.NEW_WORK
    assert first.paper_update_id is None
    assert repeated.discovery_kind is DiscoveryKind.UNCHANGED
    assert repeated.paper_update_id is None
    assert repeated.source_record.id == first.source_record.id

    with sessions() as session:
        revisions = tuple(
            session.scalars(
                select(SourceRevisionRecord)
                .where(SourceRevisionRecord.source_record_id == first.source_record.id)
                .order_by(SourceRevisionRecord.created_at, SourceRevisionRecord.id)
            )
        )
        updates = session.scalar(select(func.count()).select_from(PaperUpdateRecord))
        current = session.get(SourceRecord, first.source_record.id)

    assert len(revisions) == 1
    assert revisions[0].revision_kind == "baseline"
    assert revisions[0].changed_fields == []
    assert revisions[0].snapshot["title"] == _paper().title
    assert revisions[0].snapshot["authors"] == list(_paper().authors)
    assert revisions[0].field_provenance == {"title": "arxiv:2607.01234"}
    assert len(revisions[0].payload_sha256) == 64
    assert updates == 0
    assert current is not None
    assert current.fetched_at.replace(tzinfo=UTC) == NOW + timedelta(hours=2)


def test_metadata_only_change_is_a_hidden_refresh_and_does_not_rewrite_work(
    revision_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import DiscoveryKind
    from paper_reader.discovery.models import (
        PaperUpdateRecord,
        SourceRevisionRecord,
        WorkRecord,
    )

    repository = revision_context["repository"]
    sessions = revision_context["sessions"]
    initial = repository.ingest(_paper(), now=NOW)
    revision_context["work_index"].synced.clear()

    refreshed = repository.ingest(
        _paper(
            landing_url="https://export.arxiv.org/abs/2607.01234",
            field_provenance={
                "title": "arxiv:2607.01234",
                "landing_url": "arxiv-export",
            },
            raw_payload={"version": "v1", "mirror": "export"},
        ),
        now=NOW + timedelta(hours=3),
    )

    assert refreshed.discovery_kind is DiscoveryKind.METADATA_REFRESH
    assert refreshed.paper_update_id is not None
    assert revision_context["work_index"].synced == []

    with sessions() as session:
        work = session.get(WorkRecord, initial.work.id)
        revisions = tuple(
            session.scalars(
                select(SourceRevisionRecord)
                .where(SourceRevisionRecord.source_record_id == initial.source_record.id)
                .order_by(SourceRevisionRecord.created_at, SourceRevisionRecord.id)
            )
        )
        update = session.get(PaperUpdateRecord, refreshed.paper_update_id)

    assert work is not None
    assert work.canonical_title == _paper().title
    assert len(revisions) == 2
    assert revisions[-1].revision_kind == "metadata_refresh"
    assert set(revisions[-1].changed_fields) == {
        "field_provenance",
        "landing_url",
        "raw_payload",
    }
    assert update is not None
    assert update.update_type == "metadata_refresh"
    assert update.visibility == "internal"
    assert update.state == "unread"
    assert update.previous_revision_id == revisions[0].id
    assert update.current_revision_id == revisions[1].id


def test_source_revision_rows_cannot_be_updated_or_deleted(revision_context) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import SourceRevisionRecord

    result = revision_context["repository"].ingest(_paper(), now=NOW)
    sessions = revision_context["sessions"]
    with sessions() as session:
        revision_id = session.scalar(
            select(SourceRevisionRecord.id).where(
                SourceRevisionRecord.source_record_id == result.source_record.id
            )
        )
    assert revision_id is not None

    with sessions() as session, pytest.raises(IntegrityError, match="immutable"):
        session.execute(
            update(SourceRevisionRecord)
            .where(SourceRevisionRecord.id == revision_id)
            .values(revision_kind="metadata_refresh")
        )
        session.commit()

    with sessions() as session, pytest.raises(IntegrityError, match="immutable"):
        session.execute(
            delete(SourceRevisionRecord).where(SourceRevisionRecord.id == revision_id)
        )
        session.commit()


def test_canonical_work_selection_is_deterministic_across_source_arrival_order(
    tmp_path: Path,
) -> None:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper

    def canonical_title(database_name: str, order: tuple[str, str]) -> str:
        engine = create_sqlite_engine(tmp_path / database_name)
        Base.metadata.create_all(engine)
        sessions = create_session_factory(engine)
        repository = IdentityRepository(sessions)
        papers = {
            "arxiv": SourcePaper(
                connector_id="arxiv",
                external_id="2607.02020",
                title="Preprint title for an evidence agent",
                authors=("A. Author",),
                abstract="Short preprint abstract.",
                doi="10.1000/deterministic.1",
                updated_at=datetime(2026, 7, 12, tzinfo=UTC),
                field_provenance={"title": "arxiv"},
            ),
            "ieee": SourcePaper(
                connector_id="ieee",
                external_id="12345678",
                title="Publisher title for an evidence agent",
                authors=("Alice Author",),
                abstract="Complete publisher abstract for the paper.",
                doi="10.1000/deterministic.1",
                updated_at=datetime(2026, 7, 11, tzinfo=UTC),
                field_provenance={"title": "ieee-xplore"},
            ),
        }
        try:
            result = None
            for connector_id in order:
                result = repository.ingest(papers[connector_id], now=NOW)
            assert result is not None
            with sessions() as session:
                work = session.get(type(result.work), result.work.id)
                assert work is not None
                return work.canonical_title
        finally:
            engine.dispose()

    assert canonical_title("arxiv-first.sqlite3", ("arxiv", "ieee")) == (
        "Publisher title for an evidence agent"
    )
    assert canonical_title("ieee-first.sqlite3", ("ieee", "arxiv")) == (
        "Publisher title for an evidence agent"
    )


def test_0028_migration_backfills_revisions_and_candidate_discovery_kind(
    tmp_path: Path,
) -> None:
    from alembic import command
    from alembic.config import Config

    import paper_reader.migrations

    database = tmp_path / "upgrade.sqlite3"
    configuration = Config()
    configuration.set_main_option(
        "script_location",
        str(Path(paper_reader.migrations.__file__).parent),
    )
    configuration.set_main_option(
        "sqlalchemy.url",
        f"sqlite+pysqlite:///{database.resolve().as_posix()}",
    )
    command.upgrade(configuration, "0027_update_settings")

    connection = sqlite3.connect(database)
    try:
        now = "2026-07-15 01:30:00+00:00"
        work_ids = {
            "new": "work-new",
            "review_required": "work-review",
            "exact_doi": "work-doi",
            "auto_similarity": "work-similarity",
            "existing_source": "work-existing",
        }
        for match_kind, work_id in work_ids.items():
            connection.execute(
                "INSERT INTO works "
                "(id, canonical_title, normalized_title, authors, abstract, doi, "
                "published_at, status, merged_into_work_id, created_at, updated_at) "
                "VALUES (?, ?, ?, '[]', NULL, NULL, NULL, 'active', NULL, ?, ?)",
                (work_id, f"Title {match_kind}", f"title {match_kind}", now, now),
            )
            connection.execute(
                "INSERT INTO source_records "
                "(id, work_id, connector_id, external_id, title, normalized_title, "
                "authors, abstract, doi, published_at, source_updated_at, landing_url, "
                "field_provenance, raw_payload, fetched_at) "
                "VALUES (?, ?, 'fixture', ?, ?, ?, '[]', NULL, NULL, NULL, ?, NULL, "
                "'{}', '{}', ?)",
                (
                    f"source-{match_kind}",
                    work_id,
                    match_kind,
                    f"Title {match_kind}",
                    f"title {match_kind}",
                    now,
                    now,
                ),
            )

        connection.execute(
            "INSERT INTO jobs "
            "(id, kind, status, progress, payload, result, checkpoint, error_code, "
            "error_message, idempotency_key, cancel_requested, created_at, updated_at) "
            "VALUES ('job', 'discovery.run', 'succeeded', 100, '{}', NULL, NULL, NULL, "
            "NULL, 'migration-fixture', 0, ?, ?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO keyword_set_versions "
            "(id, parent_version_id, research_intent, required_groups, boost_groups, "
            "excluded_groups, change_summary, generator_info, created_at) "
            "VALUES ('keywords', NULL, 'fixture', '[]', '[]', '[]', '[]', '{}', ?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO discovery_plans "
            "(id, name, research_intent, keyword_version_id, date_mode, start_month, "
            "end_month, rolling_months, timezone, sources, source_filters, "
            "schedule_frequency, schedule_local_time, weekly_day, monthly_day, "
            "priority_limit, candidate_limit, enabled, schedule_checked_at, created_at, "
            "updated_at) VALUES ('plan', 'Fixture', 'fixture', 'keywords', 'fixed', "
            "'2026-07', '2026-07', NULL, 'UTC', '[\"fixture\"]', '{}', 'manual', "
            "'09:00', 0, 1, 1, 5, 1, ?, ?, ?)",
            (now, now, now),
        )
        connection.execute(
            "INSERT INTO plan_runs "
            "(id, plan_id, job_id, keyword_version_id, status, months, plan_snapshot, "
            "keyword_snapshot, connector_versions, total_batches, succeeded_batches, "
            "failed_batches, cancelled_batches, created_at, started_at, completed_at) "
            "VALUES ('run', 'plan', 'job', 'keywords', 'succeeded', '[\"2026-07\"]', "
            "'{}', '{}', '{}', 1, 1, 0, 0, ?, ?, ?)",
            (now, now, now),
        )
        connection.execute(
            "INSERT INTO run_batches "
            "(id, run_id, month, connector_id, status, query_fingerprint, checkpoint, "
            "fetched_count, accepted_count, ingested_count, attempt_count, error_code, "
            "error_message, retryable, retry_after_seconds, created_at, updated_at, "
            "completed_at) VALUES ('batch', 'run', '2026-07', 'fixture', 'succeeded', "
            "?, '{}', 5, 5, 4, 1, NULL, NULL, 0, NULL, ?, ?, ?)",
            ("a" * 64, now, now, now),
        )
        for match_kind, work_id in work_ids.items():
            connection.execute(
                "INSERT INTO run_candidates "
                "(id, run_id, batch_id, work_id, source_record_id, match_kind, created_at) "
                "VALUES (?, 'run', 'batch', ?, ?, ?, ?)",
                (
                    f"candidate-{match_kind}",
                    work_id,
                    f"source-{match_kind}",
                    match_kind,
                    now,
                ),
            )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(configuration, "0028_source_revisions_and_updates")
    connection = sqlite3.connect(database)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        baseline_rows = connection.execute(
            "SELECT source_record_id, revision_kind, payload_sha256, snapshot "
            "FROM source_record_revisions ORDER BY source_record_id"
        ).fetchall()
        candidate_kinds = dict(
            connection.execute(
                "SELECT match_kind, discovery_kind FROM run_candidates"
            ).fetchall()
        )
    finally:
        connection.close()

    assert revision == ("0028_source_revisions_and_updates",)
    assert len(baseline_rows) == 5
    assert all(row[1] == "baseline" and len(row[2]) == 64 for row in baseline_rows)
    assert json.loads(
        dict((row[0], row[3]) for row in baseline_rows)["source-existing_source"]
    )["title"] == "Title existing_source"
    assert candidate_kinds == {
        "new": "new_work",
        "review_required": "new_work",
        "exact_doi": "existing_work_new_source",
        "auto_similarity": "existing_work_new_source",
        "existing_source": "unchanged",
    }

    command.upgrade(configuration, "0028c_paper_update_runs")
    connection = sqlite3.connect(database)
    try:
        quota_counts = connection.execute(
            "SELECT new_work_count, update_count, duplicate_count, scanned_count "
            "FROM run_batches WHERE id = 'batch'"
        ).fetchone()
    finally:
        connection.close()
    assert quota_counts == (2, 0, 3, 5)

    command.downgrade(configuration, "0027_update_settings")
    command.upgrade(configuration, "0028c_paper_update_runs")
    connection = sqlite3.connect(database)
    try:
        sources = connection.execute("SELECT count(*) FROM source_records").fetchone()
        revisions = connection.execute(
            "SELECT count(*) FROM source_record_revisions"
        ).fetchone()
    finally:
        connection.close()
    assert sources == (5,)
    assert revisions == (5,)
