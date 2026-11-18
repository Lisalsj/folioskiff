from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

NOW = datetime(2026, 7, 15, 2, tzinfo=UTC)


class RecordingWorkIndex:
    def __init__(self) -> None:
        self.synced: list[str] = []

    def sync_work(self, work_id: str) -> None:
        self.synced.append(work_id)


@pytest.fixture
def update_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository

    engine = create_sqlite_engine(tmp_path / "paper-updates.sqlite3")
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
        "connector_id": "openreview",
        "external_id": "forum-paper-reader",
        "title": "Agent-assisted literature review",
        "authors": ("Alice Example",),
        "abstract": "Initial abstract.",
        "doi": "10.1000/agent-review.1",
        "published_at": datetime(2026, 6, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 6, 10, tzinfo=UTC),
        "landing_url": "https://openreview.net/forum?id=paper-reader",
        "field_provenance": {"title": "openreview:forum-paper-reader"},
        "raw_payload": {"revision": 1},
    }
    values.update(overrides)
    return SourcePaper(**values)


def test_important_revision_updates_current_source_and_work_but_keeps_history(
    update_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import DiscoveryKind
    from paper_reader.discovery.models import (
        PaperUpdateRecord,
        SourceRecord,
        SourceRevisionRecord,
        WorkRecord,
    )

    repository = update_context["repository"]
    sessions = update_context["sessions"]
    first = repository.ingest(_paper(), now=NOW)
    update_context["work_index"].synced.clear()

    revised = repository.ingest(
        _paper(
            title="Agent-assisted systematic literature review",
            authors=("Alice Example", "Bob Example"),
            abstract="Expanded abstract with evaluation evidence.",
            updated_at=datetime(2026, 7, 14, tzinfo=UTC),
            raw_payload={"revision": 2},
        ),
        now=NOW + timedelta(days=1),
    )

    assert revised.discovery_kind is DiscoveryKind.IMPORTANT_REVISION
    assert revised.paper_update_id is not None
    assert update_context["work_index"].synced == [first.work.id]

    with sessions() as session:
        source = session.get(SourceRecord, first.source_record.id)
        work = session.get(WorkRecord, first.work.id)
        revisions = tuple(
            session.scalars(
                select(SourceRevisionRecord)
                .where(SourceRevisionRecord.source_record_id == first.source_record.id)
                .order_by(SourceRevisionRecord.created_at, SourceRevisionRecord.id)
            )
        )
        update = session.get(PaperUpdateRecord, revised.paper_update_id)

    assert source is not None
    assert source.title == "Agent-assisted systematic literature review"
    assert source.authors == ["Alice Example", "Bob Example"]
    assert work is not None
    assert work.canonical_title == source.title
    assert work.authors == source.authors
    assert work.abstract == source.abstract
    assert len(revisions) == 2
    assert revisions[0].snapshot["title"] == "Agent-assisted literature review"
    assert revisions[1].snapshot["title"] == source.title
    assert revisions[1].revision_kind == "important_revision"
    assert {"title", "authors", "abstract", "source_updated_at", "raw_payload"}.issubset(
        revisions[1].changed_fields
    )
    assert update is not None
    assert update.update_type == "important_revision"
    assert update.visibility == "updates"
    assert update.state == "unread"
    assert update.evidence["changed_fields"] == revisions[1].changed_fields


def test_update_state_is_explicit_and_metadata_refreshes_are_hidden(
    update_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.paper_updates import PaperUpdateRepository, PaperUpdateState

    identity = update_context["repository"]
    first = identity.ingest(_paper(), now=NOW)
    important = identity.ingest(
        _paper(
            abstract="Important replacement abstract.",
            updated_at=datetime(2026, 7, 1, tzinfo=UTC),
        ),
        now=NOW + timedelta(hours=1),
    )
    metadata = identity.ingest(
        _paper(
            abstract="Important replacement abstract.",
            updated_at=datetime(2026, 7, 1, tzinfo=UTC),
            landing_url="https://openreview.net/pdf?id=paper-reader",
        ),
        now=NOW + timedelta(hours=2),
    )
    updates = PaperUpdateRepository(update_context["sessions"])

    visible = updates.list_for_work(first.work.id, visible_only=True)
    all_updates = updates.list_for_work(first.work.id, visible_only=False)
    assert [item.id for item in visible] == [important.paper_update_id]
    assert {item.id for item in all_updates} == {
        important.paper_update_id,
        metadata.paper_update_id,
    }

    read = updates.set_state(
        important.paper_update_id,
        PaperUpdateState.READ,
        now=NOW + timedelta(hours=3),
    )
    assert read.state is PaperUpdateState.READ
    assert read.acted_at == NOW + timedelta(hours=3)
    dismissed = updates.set_state(
        important.paper_update_id,
        PaperUpdateState.DISMISSED,
        now=NOW + timedelta(hours=4),
    )
    assert dismissed.state is PaperUpdateState.DISMISSED
    with pytest.raises(ValueError, match="timezone-aware"):
        updates.set_state(
            important.paper_update_id,
            PaperUpdateState.READ,
            now=datetime(2026, 7, 15),
        )


def test_failed_revision_transaction_leaves_source_work_history_and_index_unchanged(
    update_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import (
        PaperUpdateRecord,
        SourceRecord,
        SourceRevisionRecord,
        WorkRecord,
    )

    repository = update_context["repository"]
    sessions = update_context["sessions"]
    first = repository.ingest(_paper(), now=NOW)
    update_context["work_index"].synced.clear()
    with update_context["engine"].begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER fail_important_revision "
                "BEFORE INSERT ON source_record_revisions "
                "WHEN NEW.revision_kind = 'important_revision' "
                "BEGIN SELECT RAISE(ABORT, 'injected revision failure'); END"
            )
        )

    with pytest.raises(IntegrityError, match="injected revision failure"):
        repository.ingest(
            _paper(
                title="Transaction must roll back",
                updated_at=datetime(2026, 7, 14, tzinfo=UTC),
            ),
            now=NOW + timedelta(days=1),
        )

    with sessions() as session:
        source = session.get(SourceRecord, first.source_record.id)
        work = session.get(WorkRecord, first.work.id)
        revision_count = session.scalar(
            select(func.count())
            .select_from(SourceRevisionRecord)
            .where(SourceRevisionRecord.source_record_id == first.source_record.id)
        )
        update_count = session.scalar(select(func.count()).select_from(PaperUpdateRecord))

    assert source is not None
    assert source.title == "Agent-assisted literature review"
    assert work is not None
    assert work.canonical_title == "Agent-assisted literature review"
    assert revision_count == 1
    assert update_count == 0
    assert update_context["work_index"].synced == []
