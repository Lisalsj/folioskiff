from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from paper_reader.settings import AppPaths


@pytest.fixture
def identity_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "merge-reviews.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    try:
        yield IdentityRepository(sessions), sessions
    finally:
        engine.dispose()


def create_ambiguous_pair(repository):  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import SourcePaper

    existing = repository.ingest(
        SourcePaper(
            connector_id="openreview",
            external_id="forum-a",
            title="Tool-Augmented Agents for Scientific Discovery",
            authors=("A. Chen", "R. Kumar"),
        )
    )
    candidate = repository.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2601.00002v1",
            title="Tool Augmented Agent for Scientific Discoveries",
            authors=("Alice Chen", "Ravi Kumar"),
        )
    )
    assert candidate.merge_review is not None
    return existing, candidate


def test_user_merge_moves_sources_to_canonical_work_and_retains_audit_tombstone(
    identity_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import MergeReviewRecord, SourceRecord, WorkRecord

    repository, sessions = identity_context
    existing, candidate = create_ambiguous_pair(repository)

    decided = repository.decide_merge_review(
        candidate.merge_review.id,
        decision="merge",
        note="同一研究的跨来源版本",
    )
    repeated = repository.decide_merge_review(
        candidate.merge_review.id,
        decision="merge",
        note="重复请求不应再次移动记录",
    )

    assert decided.status == "merged"
    assert repeated.id == decided.id
    assert repository.count_works() == 1
    with sessions() as session:
        alias = session.get(WorkRecord, candidate.work.id)
        review = session.get(MergeReviewRecord, candidate.merge_review.id)
        source_work_ids = set(session.scalars(select(SourceRecord.work_id)))
    assert alias is not None
    assert alias.status == "merged"
    assert alias.merged_into_work_id == existing.work.id
    assert review is not None
    assert review.status == "merged"
    assert review.decision_note == "同一研究的跨来源版本"
    assert source_work_ids == {existing.work.id}


@pytest.mark.parametrize("failure_timing", ("before_commit", "after_commit"))
def test_work_merge_commit_fault_is_atomic_and_retry_idempotent(
    identity_context,
    monkeypatch: pytest.MonkeyPatch,
    failure_timing: str,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import MergeReviewRecord, SourceRecord, WorkRecord

    repository, sessions = identity_context
    existing, candidate = create_ambiguous_pair(repository)
    session_class = sessions.class_
    original_commit = session_class.commit
    fault_injected = False

    def commit_with_fault(session) -> None:  # type: ignore[no-untyped-def]
        nonlocal fault_injected
        if fault_injected:
            original_commit(session)
            return
        fault_injected = True
        if failure_timing == "after_commit":
            original_commit(session)
        raise OSError(f"injected Work merge {failure_timing} failure")

    monkeypatch.setattr(session_class, "commit", commit_with_fault)
    with pytest.raises(OSError, match=f"injected Work merge {failure_timing} failure"):
        repository.decide_merge_review(candidate.merge_review.id, decision="merge")
    monkeypatch.setattr(session_class, "commit", original_commit)

    with sessions() as session:
        review_after_fault = session.get(MergeReviewRecord, candidate.merge_review.id)
        alias_after_fault = session.get(WorkRecord, candidate.work.id)
        source_pairs_after_fault = set(
            session.execute(select(SourceRecord.connector_id, SourceRecord.work_id)).tuples()
        )
    assert review_after_fault is not None
    assert alias_after_fault is not None
    if failure_timing == "before_commit":
        assert review_after_fault.status == "pending"
        assert alias_after_fault.status == "active"
        assert source_pairs_after_fault == {
            ("openreview", existing.work.id),
            ("arxiv", candidate.work.id),
        }
    else:
        assert review_after_fault.status == "merged"
        assert alias_after_fault.status == "merged"
        assert source_pairs_after_fault == {
            ("openreview", existing.work.id),
            ("arxiv", existing.work.id),
        }

    recovered = repository.decide_merge_review(
        candidate.merge_review.id,
        decision="merge",
    )
    repeated = repository.decide_merge_review(
        candidate.merge_review.id,
        decision="merge",
    )

    assert recovered.status == repeated.status == "merged"
    assert repository.count_works() == 1
    with sessions() as session:
        assert set(session.scalars(select(SourceRecord.work_id))) == {existing.work.id}
        assert session.connection().exec_driver_sql("PRAGMA foreign_key_check").fetchone() is None


def test_keep_separate_is_audited_and_conflicting_second_decision_is_rejected(
    identity_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import MergeReviewAlreadyDecidedError

    repository, _ = identity_context
    _, candidate = create_ambiguous_pair(repository)

    decided = repository.decide_merge_review(
        candidate.merge_review.id,
        decision="keep_separate",
        note="题目相似但实验数据不同",
    )

    assert decided.status == "kept_separate"
    assert repository.count_works() == 2
    with pytest.raises(MergeReviewAlreadyDecidedError):
        repository.decide_merge_review(
            candidate.merge_review.id,
            decision="merge",
        )


def test_merge_rejects_two_different_nonempty_dois(identity_context) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import MergeConflictError, SourcePaper

    repository, _ = identity_context
    existing = repository.ingest(
        SourcePaper(
            connector_id="openreview",
            external_id="forum-doi-a",
            title="Reliable Agent Evidence for Oncology",
            authors=("Alice Chen",),
            doi="10.1000/first",
        )
    )
    candidate = repository.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.10000v1",
            title="Reliable Agent Evidences for Oncology",
            authors=("A. Chen",),
            doi="10.1000/second",
        )
    )
    assert existing.work.id != candidate.work.id
    assert candidate.merge_review is not None
    assert candidate.merge_review.evidence["doi_conflict"] == 1.0
    assert candidate.merge_review.evidence["candidate_search_degraded"] is True

    with pytest.raises(MergeConflictError, match="DOI"):
        repository.decide_merge_review(candidate.merge_review.id, decision="merge")

    assert repository.count_works() == 2


def test_merge_combines_optional_evidence_documents_and_supersedes_related_reviews(
    identity_context,
) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from paper_reader.discovery.identity import SourcePaper
    from paper_reader.discovery.models import (
        DocumentVersionRecord,
        MergeReviewRecord,
        WorkRecord,
    )

    repository, sessions = identity_context
    target = repository.ingest(
        SourcePaper(
            connector_id="openreview",
            external_id="forum-rich-target",
            title="Tool-Augmented Agents for Scientific Discovery",
            authors=("A. Chen",),
        )
    )
    candidate = repository.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.22222v1",
            title="Tool Augmented Agent for Scientific Discoveries",
            authors=("A. Chen", "New Author"),
            abstract="Full candidate abstract",
            doi="10.1000/rich-merge",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
    )
    unrelated = repository.ingest(
        SourcePaper(
            connector_id="europe_pmc",
            external_id="MED/999999",
            title="Unrelated oncology cohort study",
            authors=("Other Author",),
        )
    )
    assert candidate.merge_review is not None

    repository.add_document_version(
        work_id=target.work.id,
        content_sha256="a" * 64,
        access_status="open",
        completeness=80,
        source_updated_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    repository.add_document_version(
        work_id=candidate.work.id,
        content_sha256="a" * 64,
        access_status="open",
        completeness=90,
        source_updated_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    repository.add_document_version(
        work_id=candidate.work.id,
        content_sha256="b" * 64,
        access_status="user_provided",
        completeness=100,
        source_updated_at=datetime(2026, 7, 3, tzinfo=UTC),
    )
    with sessions() as session:
        related = MergeReviewRecord(
            candidate_work_id=candidate.work.id,
            matched_work_id=unrelated.work.id,
            similarity=0.76,
            evidence={"manual_fixture": 1.0},
        )
        session.add(related)
        session.commit()
        related_id = related.id

    repository.decide_merge_review(candidate.merge_review.id, decision="merge")

    with sessions() as session:
        canonical = session.get(WorkRecord, target.work.id)
        related_record = session.get(MergeReviewRecord, related_id)
        documents = list(
            session.scalars(
                select(DocumentVersionRecord).where(DocumentVersionRecord.work_id == target.work.id)
            )
        )
    assert canonical is not None
    assert canonical.doi == "10.1000/rich-merge"
    assert canonical.abstract == "Full candidate abstract"
    assert canonical.published_at is not None
    assert canonical.authors == ["A. Chen", "New Author"]
    assert {document.content_sha256 for document in documents} == {"a" * 64, "b" * 64}
    assert related_record is not None
    assert related_record.status == "superseded"
    assert related_record.decision_note == f"superseded_by:{candidate.merge_review.id}"


def test_merge_moves_and_deduplicates_local_library_membership(
    identity_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.reader.models import LibraryEntryRecord

    repository, sessions = identity_context
    existing, candidate = create_ambiguous_pair(repository)
    first_saved = datetime(2026, 7, 12, tzinfo=UTC)
    later_saved = first_saved + timedelta(hours=2)
    with sessions() as session:
        session.add_all(
            (
                LibraryEntryRecord(
                    work_id=existing.work.id,
                    added_via="url",
                    created_at=later_saved,
                    updated_at=later_saved,
                ),
                LibraryEntryRecord(
                    work_id=candidate.work.id,
                    added_via="pdf",
                    created_at=first_saved,
                    updated_at=later_saved + timedelta(hours=1),
                ),
            )
        )
        session.commit()

    repository.decide_merge_review(candidate.merge_review.id, decision="merge")

    with sessions() as session:
        entries = list(session.scalars(select(LibraryEntryRecord)))
    assert len(entries) == 1
    assert entries[0].work_id == existing.work.id
    assert entries[0].added_via == "pdf"
    assert entries[0].created_at == first_saved.replace(tzinfo=None)
    assert entries[0].updated_at == (later_saved + timedelta(hours=1)).replace(tzinfo=None)


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


@pytest.mark.asyncio
async def test_merge_review_api_lists_evidence_and_requires_explicit_decision(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.discovery.identity import IdentityRepository

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    app = create_app(paths=paths, bootstrap_token="secret")
    repository = IdentityRepository(app.state.sessions)
    existing, candidate = create_ambiguous_pair(repository)

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        csrf = client.cookies["paper_reader_csrf"]
        pending = await client.get("/api/v1/works/merge-reviews")
        decided = await client.post(
            f"/api/v1/works/merge-reviews/{candidate.merge_review.id}/decision",
            json={"decision": "merge", "note": "确认是同一论文"},
            headers={
                "Origin": "http://127.0.0.1",
                "X-CSRF-Token": csrf,
            },
        )
        after = await client.get("/api/v1/works/merge-reviews")

    assert pending.status_code == 200
    assert len(pending.json()) == 1
    assert pending.json()[0]["candidate_work"]["id"] == candidate.work.id
    assert pending.json()[0]["matched_work"]["id"] == existing.work.id
    assert pending.json()[0]["evidence"]["title"] >= 0.75
    assert decided.status_code == 200
    assert decided.json()["status"] == "merged"
    assert after.json() == []
