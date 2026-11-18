from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock

import pytest
from sqlalchemy import select

NOW = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)


@dataclass(slots=True)
class RetentionContext:
    engine: object
    sessions: object
    store: object


@dataclass(frozen=True, slots=True)
class AddedDocument:
    work_id: str
    document_id: str
    content_sha256: str
    relative_path: str
    byte_size: int


@pytest.fixture
def retention_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery import models as discovery_models
    from paper_reader.reader import models as reader_models
    from paper_reader.reader.storage import ContentStore

    assert discovery_models.WorkRecord.__tablename__ == "works"
    assert reader_models.AnnotationRecord.__tablename__ == "annotations"
    engine = create_sqlite_engine(tmp_path / "retention.sqlite3")
    Base.metadata.create_all(engine)
    context = RetentionContext(
        engine=engine,
        sessions=create_session_factory(engine),
        store=ContentStore(tmp_path / "library"),
    )
    try:
        yield context
    finally:
        engine.dispose()


def add_document(
    context: RetentionContext,
    label: str,
    *,
    feedback: str = "dislike",
    last_accessed_at: datetime = NOW - timedelta(days=91),
    favorite: bool = False,
    pinned: bool = False,
    analysis_state: str = "not_analyzed",
    stored=None,  # type: ignore[no-untyped-def]
) -> AddedDocument:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.models import DocumentVersionRecord, LibraryStateRecord

    payload = (
        b"%PDF-1.7\n% paper-reader retention fixture "
        + label.encode("utf-8")
        + b"\n%%EOF\n"
    )
    stored = stored or context.store.ingest(  # type: ignore[attr-defined]
        (payload,),
        media_type="application/pdf",
        max_bytes=1024 * 1024,
    )
    ingested = IdentityRepository(context.sessions).ingest(  # type: ignore[arg-type]
        SourcePaper(
            connector_id="fixture",
            external_id=f"retention-{label}",
            title=f"Retention {label}",
            authors=("Alice",),
        )
    )
    with context.sessions() as session:  # type: ignore[operator]
        document = DocumentVersionRecord(
            work_id=ingested.work.id,
            source_record_id=ingested.source_record.id,
            content_sha256=stored.content_sha256,
            access_status="open",
            media_type="application/pdf",
            completeness=100,
            source_updated_at=last_accessed_at,
            extraction_status="complete",
            relative_path=stored.relative_path,
            byte_size=stored.byte_size,
            original_filename=f"{label}.pdf",
            acquisition_kind="download",
            acquisition_url=f"https://example.org/{label}.pdf",
            last_accessed_at=last_accessed_at,
            created_at=last_accessed_at,
        )
        session.add(document)
        session.add(
            LibraryStateRecord(
                work_id=ingested.work.id,
                favorite=favorite,
                feedback=feedback,
                feedback_reasons=[] if feedback == "neutral" else ["fixture"],
                pinned=pinned,
                reading_progress=0,
                analysis_state=analysis_state,
                created_at=last_accessed_at,
                updated_at=last_accessed_at,
            )
        )
        session.commit()
        return AddedDocument(
            work_id=ingested.work.id,
            document_id=document.id,
            content_sha256=stored.content_sha256,
            relative_path=stored.relative_path,
            byte_size=stored.byte_size,
        )


def test_retention_public_contract_is_available() -> None:
    from paper_reader.reader.retention import (
        CleanupCandidate,
        CleanupPreview,
        CleanupResult,
        RetentionService,
        StaleCleanupPreviewError,
    )

    assert CleanupCandidate.__name__ == "CleanupCandidate"
    assert CleanupPreview.__name__ == "CleanupPreview"
    assert CleanupResult.__name__ == "CleanupResult"
    assert RetentionService.__name__ == "RetentionService"
    assert issubclass(StaleCleanupPreviewError, ValueError)


def test_preview_only_marks_old_disliked_locally_present_content_eligible(
    retention_context: RetentionContext,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.retention import RetentionService

    eligible = add_document(retention_context, "eligible")
    neutral = add_document(
        retention_context,
        "neutral",
        feedback="neutral",
        last_accessed_at=NOW - timedelta(days=120),
    )
    liked = add_document(
        retention_context,
        "liked",
        feedback="like",
        last_accessed_at=NOW - timedelta(days=120),
    )
    boundary = add_document(
        retention_context,
        "boundary",
        last_accessed_at=NOW - timedelta(days=90),
    )
    unavailable = add_document(retention_context, "not-local")
    with retention_context.sessions() as session:  # type: ignore[operator]
        row = session.get(DocumentVersionRecord, unavailable.document_id)
        assert row is not None
        row.relative_path = None
        session.commit()

    preview = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    ).preview(now=NOW, retention_days=90)
    candidates = {item.content_sha256: item for item in preview.candidates}

    assert preview.eligible_count == 1
    assert preview.protected_count == 3
    assert preview.reclaimable_bytes == eligible.byte_size
    assert candidates[eligible.content_sha256].eligible is True
    assert candidates[neutral.content_sha256].protected_reasons == (
        "feedback_not_disliked",
    )
    assert candidates[liked.content_sha256].protected_reasons == (
        "feedback_not_disliked",
    )
    assert candidates[boundary.content_sha256].protected_reasons == (
        "within_retention",
    )
    assert unavailable.content_sha256 not in candidates


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("favorite", True, "favorite"),
        ("pinned", True, "pinned"),
        ("analysis_state", "available", "analysis_available"),
    ),
)
def test_preview_protects_explicit_library_states(
    retention_context: RetentionContext,
    field: str,
    value: object,
    reason: str,
) -> None:
    from paper_reader.reader.retention import RetentionService

    item = add_document(retention_context, field, **{field: value})

    preview = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    ).preview(now=NOW)

    candidate = next(
        value
        for value in preview.candidates
        if value.content_sha256 == item.content_sha256
    )
    assert reason in candidate.protected_reasons
    assert candidate.eligible is False


def test_preview_protects_shared_hash_when_any_work_is_protected_or_annotated(
    retention_context: RetentionContext,
) -> None:
    from paper_reader.reader.models import AnnotationRecord
    from paper_reader.reader.retention import RetentionService

    shared = add_document(retention_context, "shared-disliked")
    stored = type("Stored", (), {})()
    stored.content_sha256 = shared.content_sha256
    stored.relative_path = shared.relative_path
    stored.byte_size = shared.byte_size
    second = add_document(
        retention_context,
        "shared-favorite",
        feedback="neutral",
        favorite=True,
        stored=stored,
    )
    annotated = add_document(retention_context, "annotated")
    with retention_context.sessions() as session:  # type: ignore[operator]
        session.add(
            AnnotationRecord(
                id="annotation-1",
                document_version_id=annotated.document_id,
                document_content_sha256=annotated.content_sha256,
                kind="bookmark",
                page_number=1,
                page_width=420,
                page_height=595,
                quote_text=None,
                context_before=None,
                context_after=None,
                rects=[],
                color="amber",
                note=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()

    preview = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    ).preview(now=NOW)
    candidates = {item.content_sha256: item for item in preview.candidates}

    shared_candidate = candidates[shared.content_sha256]
    assert shared_candidate.work_ids == tuple(sorted((shared.work_id, second.work_id)))
    assert shared_candidate.eligible is False
    assert set(shared_candidate.protected_reasons) == {
        "favorite",
        "feedback_not_disliked",
    }
    assert candidates[annotated.content_sha256].protected_reasons == ("annotation",)


def test_preview_token_is_stable_and_apply_rejects_changed_preferences(
    retention_context: RetentionContext,
) -> None:
    from paper_reader.discovery.models import LibraryStateRecord
    from paper_reader.reader.retention import RetentionService, StaleCleanupPreviewError

    item = add_document(retention_context, "stale")
    service = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    )
    first = service.preview(now=NOW)
    second = service.preview(now=NOW)
    assert first.preview_token == second.preview_token
    assert len(first.preview_token) == 64

    with retention_context.sessions() as session:  # type: ignore[operator]
        state = session.get(LibraryStateRecord, item.work_id)
        assert state is not None
        state.favorite = True
        session.commit()

    with pytest.raises(StaleCleanupPreviewError):
        service.apply(first.preview_token)
    assert retention_context.store.resolve(item.relative_path).is_file()  # type: ignore[attr-defined]


def test_apply_deletes_one_shared_object_clears_all_paths_and_is_idempotent(
    retention_context: RetentionContext,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord, LibraryStateRecord
    from paper_reader.reader.retention import RetentionService

    first = add_document(retention_context, "delete-first")
    stored = type("Stored", (), {})()
    stored.content_sha256 = first.content_sha256
    stored.relative_path = first.relative_path
    stored.byte_size = first.byte_size
    second = add_document(retention_context, "delete-second", stored=stored)
    service = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    )
    preview = service.preview(now=NOW)

    result = service.apply(preview.preview_token)
    repeated = service.apply(preview.preview_token)

    assert result == repeated
    assert result.deleted_objects == 1
    assert result.missing_objects == 0
    assert result.cleared_document_versions == 2
    assert result.reclaimed_bytes == first.byte_size
    assert result.cancelled is False
    assert not retention_context.store.resolve(first.relative_path).exists()  # type: ignore[attr-defined]
    with retention_context.sessions() as session:  # type: ignore[operator]
        documents = list(
            session.scalars(
                select(DocumentVersionRecord).where(
                    DocumentVersionRecord.content_sha256 == first.content_sha256
                )
            )
        )
        states = [
            session.get(LibraryStateRecord, work_id)
            for work_id in (first.work_id, second.work_id)
        ]
    assert len(documents) == 2
    assert all(document.relative_path is None for document in documents)
    assert all(document.content_sha256 == first.content_sha256 for document in documents)
    assert all(document.extraction_status == "complete" for document in documents)
    assert all(state is not None and state.feedback == "dislike" for state in states)


def test_apply_treats_missing_file_as_idempotent_success(
    retention_context: RetentionContext,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.retention import RetentionService

    item = add_document(retention_context, "already-missing")
    service = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    )
    preview = service.preview(now=NOW)
    retention_context.store.resolve(item.relative_path).unlink()  # type: ignore[attr-defined]

    result = service.apply(preview.preview_token)

    assert result.deleted_objects == 0
    assert result.missing_objects == 1
    with retention_context.sessions() as session:  # type: ignore[operator]
        document = session.get(DocumentVersionRecord, item.document_id)
        assert document is not None
        assert document.relative_path is None


def test_apply_treats_a_file_removed_during_unlink_as_missing(
    retention_context: RetentionContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.reader.retention import RetentionService

    item = add_document(retention_context, "vanished-during-unlink")
    service = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    )
    preview = service.preview(now=NOW)
    real_path = retention_context.store.resolve(item.relative_path)  # type: ignore[attr-defined]

    class VanishingPath:
        @staticmethod
        def exists() -> bool:
            return True

        @staticmethod
        def unlink() -> None:
            real_path.unlink()
            raise FileNotFoundError(real_path)

    monkeypatch.setattr(
        retention_context.store,  # type: ignore[arg-type]
        "resolve_object",
        lambda *_: VanishingPath(),
    )

    result = service.apply(preview.preview_token)

    assert result.deleted_objects == 0
    assert result.missing_objects == 1
    assert result.cleared_document_versions == 1
    assert not real_path.exists()


def test_apply_with_only_protected_candidates_is_a_safe_noop(
    retention_context: RetentionContext,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.retention import RetentionService

    protected = add_document(retention_context, "protected-only", favorite=True)
    service = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    )
    preview = service.preview(now=NOW)

    result = service.apply(preview.preview_token)

    assert preview.eligible_count == 0
    assert result.deleted_objects == 0
    assert result.missing_objects == 0
    assert result.cleared_document_versions == 0
    assert result.reclaimed_bytes == 0
    assert retention_context.store.resolve(protected.relative_path).is_file()  # type: ignore[attr-defined]
    with retention_context.sessions() as session:  # type: ignore[operator]
        document = session.get(DocumentVersionRecord, protected.document_id)
        assert document is not None
        assert document.relative_path == protected.relative_path


def test_concurrent_apply_of_one_preview_unlinks_the_object_once(
    retention_context: RetentionContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.reader.retention import RetentionService

    item = add_document(retention_context, "concurrent")
    service = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    )
    preview = service.preview(now=NOW)
    real_path = retention_context.store.resolve(item.relative_path)  # type: ignore[attr-defined]

    class CoordinatedPath:
        def __init__(self) -> None:
            self.first_entered = Event()
            self.second_entered = Event()
            self.release = Event()
            self.lock = Lock()
            self.exists_calls = 0
            self.unlink_calls = 0

        def exists(self) -> bool:
            assert real_path.is_file()
            with self.lock:
                self.exists_calls += 1
                if self.exists_calls == 1:
                    self.first_entered.set()
                else:
                    self.second_entered.set()
            assert self.release.wait(2)
            return True

        def unlink(self) -> None:
            with self.lock:
                self.unlink_calls += 1
                if self.unlink_calls > 1:
                    raise AssertionError("content object was unlinked more than once")
            real_path.unlink()

    coordinated = CoordinatedPath()
    monkeypatch.setattr(
        retention_context.store,  # type: ignore[arg-type]
        "resolve_object",
        lambda *_: coordinated,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.apply, preview.preview_token)
        assert coordinated.first_entered.wait(2)
        second = executor.submit(service.apply, preview.preview_token)
        coordinated.second_entered.wait(0.25)
        coordinated.release.set()
        first_result = first.result(timeout=2)
        second_result = second.result(timeout=2)

    assert first_result == second_result
    assert coordinated.unlink_calls == 1
    assert not real_path.exists()


def test_preview_never_makes_a_path_escape_eligible(
    retention_context: RetentionContext,
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.retention import RetentionService

    item = add_document(retention_context, "escape")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    with retention_context.sessions() as session:  # type: ignore[operator]
        document = session.get(DocumentVersionRecord, item.document_id)
        assert document is not None
        document.relative_path = "../outside.pdf"
        session.commit()

    preview = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    ).preview(now=NOW)
    candidate = next(
        value
        for value in preview.candidates
        if value.content_sha256 == item.content_sha256
    )

    assert candidate.eligible is False
    assert "unsafe_path" in candidate.protected_reasons
    assert outside.read_bytes() == b"outside"


def test_apply_rejects_a_symlink_introduced_after_preview_when_supported(
    retention_context: RetentionContext,
    tmp_path: Path,
) -> None:
    from paper_reader.reader.retention import RetentionService
    from paper_reader.reader.storage import UnsafeStoredPathError

    item = add_document(retention_context, "symlink")
    service = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    )
    preview = service.preview(now=NOW)
    object_path = retention_context.store.resolve(item.relative_path)  # type: ignore[attr-defined]
    outside = tmp_path / "outside-symlink.pdf"
    outside.write_bytes(b"outside")
    object_path.unlink()
    try:
        object_path.symlink_to(outside)
    except OSError:
        pytest.skip("This Windows environment does not permit test symlinks")

    with pytest.raises(UnsafeStoredPathError):
        service.apply(preview.preview_token)
    assert outside.read_bytes() == b"outside"


def test_apply_reports_an_unsafe_path_before_a_generic_stale_preview(
    retention_context: RetentionContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.reader.retention import RetentionService
    from paper_reader.reader.storage import UnsafeStoredPathError

    item = add_document(retention_context, "unsafe-after-preview")
    service = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    )
    preview = service.preview(now=NOW)

    def reject_new_unsafe_path(*_: object) -> Path:
        raise UnsafeStoredPathError("Stored object path became unsafe")

    monkeypatch.setattr(
        retention_context.store,  # type: ignore[arg-type]
        "resolve_object",
        reject_new_unsafe_path,
    )

    with pytest.raises(UnsafeStoredPathError, match="became unsafe"):
        service.apply(preview.preview_token)
    assert retention_context.store.resolve(item.relative_path).is_file()  # type: ignore[attr-defined]


def test_apply_can_cancel_between_objects_without_touching_the_rest(
    retention_context: RetentionContext,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.retention import RetentionService

    first = add_document(retention_context, "cancel-first")
    second = add_document(retention_context, "cancel-second")
    service = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    )
    preview = service.preview(now=NOW)
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    result = service.apply(preview.preview_token, cancelled=cancelled)

    assert result.cancelled is True
    assert result.deleted_objects == 1
    with retention_context.sessions() as session:  # type: ignore[operator]
        documents = {
            item.id: item.relative_path
            for item in session.scalars(select(DocumentVersionRecord))
        }
    assert sorted(path is None for path in documents.values()) == [False, True]
    assert sum(path.exists() for path in (
        retention_context.store.resolve(first.relative_path),  # type: ignore[attr-defined]
        retention_context.store.resolve(second.relative_path),  # type: ignore[attr-defined]
    )) == 1


def test_preview_validates_time_and_retention_bounds(
    retention_context: RetentionContext,
) -> None:
    from paper_reader.reader.retention import RetentionService

    service = RetentionService(
        sessions=retention_context.sessions,  # type: ignore[arg-type]
        store=retention_context.store,  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        service.preview(now=datetime(2026, 7, 13), retention_days=90)
    for days in (0, 3651):
        with pytest.raises(ValueError, match="retention_days"):
            service.preview(now=NOW, retention_days=days)
