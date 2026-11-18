from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from tests.reader.pdf_fixtures import write_text_and_image_pdf, write_text_pdf

NOW = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)


@dataclass(slots=True)
class MigrationContext:
    sessions: Any
    service: Any
    annotations: Any
    source_document_id: str
    target_document_id: str
    source_hash: str
    target_hash: str
    annotation_ids: dict[str, str]


def _block_payload(block: Any) -> dict[str, object]:
    payload: dict[str, object] = {
        "x0": block.x0,
        "y0": block.y0,
        "x1": block.x1,
        "y1": block.y1,
        "text": block.text,
        "block_number": block.block_number,
    }
    if block.confidence is not None:
        payload["confidence"] = block.confidence
    return payload


def _persist_extraction(
    sessions: Any,
    document_id: str,
    path: Path,
    *,
    ocr_pages: dict[int, str] | None = None,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.extraction import PdfTextExtractor
    from paper_reader.reader.models import DocumentPageTextRecord

    result = PdfTextExtractor().extract(path, cancelled=lambda: False)
    ocr_pages = ocr_pages or {}
    with sessions() as session:
        document = session.get(DocumentVersionRecord, document_id)
        assert document is not None
        document.extraction_status = "complete"
        for page in result.pages:
            ocr_text = ocr_pages.get(page.page_number)
            native_text = "" if ocr_text is not None else page.text
            native_blocks = (
                [] if ocr_text is not None else [_block_payload(block) for block in page.blocks]
            )
            session.add(
                DocumentPageTextRecord(
                    document_version_id=document_id,
                    page_number=page.page_number,
                    page_width=page.page_width,
                    page_height=page.page_height,
                    native_text=native_text,
                    native_blocks=native_blocks,
                    native_character_count=sum(
                        not character.isspace() for character in native_text
                    ),
                    likely_scanned=ocr_text is not None or page.likely_scanned,
                    ocr_state="complete" if ocr_text is not None else page.ocr_state,
                    ocr_text=ocr_text,
                    ocr_blocks=[]
                    if ocr_text is None
                    else [
                        {
                            "x0": 48.0,
                            "y0": 48.0,
                            "x1": 372.0,
                            "y1": 82.0,
                            "text": ocr_text,
                            "block_number": 0,
                            "confidence": 0.93,
                        }
                    ],
                    ocr_confidence=None if ocr_text is None else 0.93,
                    ocr_engine_version=None if ocr_text is None else "rapidocr-fixture",
                    ocr_model_bundle_id=None if ocr_text is None else "fixture-bundle",
                    extractor_version=result.extractor_version,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        session.commit()


@pytest.fixture
def migration_context(tmp_path: Path) -> MigrationContext:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery import models as discovery_models
    from paper_reader.reader.annotations import (
        AnnotationColor,
        AnnotationInput,
        AnnotationKind,
        AnnotationRepository,
        PdfRect,
    )
    from paper_reader.reader.imports import LibraryImportService, PdfImportInput
    from paper_reader.reader.migrations import AnnotationMigrationService
    from paper_reader.reader.models import AnnotationMigrationBatchRecord
    from paper_reader.reader.storage import ContentStore

    assert discovery_models.WorkRecord.__tablename__ == "works"
    assert AnnotationMigrationBatchRecord.__tablename__ == "annotation_migration_batches"
    engine = create_sqlite_engine(tmp_path / "annotation-migrations.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    store = ContentStore(tmp_path / "library")
    imports = LibraryImportService(sessions=sessions, store=store, clock=lambda: NOW)

    source_path = write_text_pdf(
        tmp_path / "source.pdf",
        (
            "Lead context. The unchanged mechanism remains reliable. Tail context.",
            "Before. Diffusion, agents: cooperate. After.",
            "Duplicate marker: repeated evidence phrase.",
            "Deleted-only passage will disappear forever.",
            "OCR context. OCR anchored insight survives scan. OCR tail.",
        ),
    )
    target_path = write_text_and_image_pdf(
        tmp_path / "target.pdf",
        (
            "New introduction.",
            "Lead context. The unchanged mechanism remains reliable. Tail context.",
            "Target duplicate A: repeated evidence phrase.",
            "Before. Diffusion - agents cooperate. After.",
            "Target duplicate B: repeated evidence phrase.",
            None,
            "New conclusion.",
        ),
    )
    source = imports.import_pdf(
        PdfImportInput(
            work_id=None,
            title="Versioned scientific reader",
            authors=("Alice Chen",),
            doi="10.1000/versioned-reader",
            original_filename=source_path.name,
            rights_confirmed=True,
        ),
        (source_path.read_bytes(),),
    )
    target = imports.import_pdf(
        PdfImportInput(
            work_id=source.work_id,
            title="Versioned scientific reader",
            authors=("Alice Chen",),
            doi="10.1000/versioned-reader",
            original_filename=target_path.name,
            rights_confirmed=True,
        ),
        (target_path.read_bytes(),),
    )
    _persist_extraction(sessions, source.document_version_id, source_path)
    _persist_extraction(
        sessions,
        target.document_version_id,
        target_path,
        ocr_pages={6: "OCR context. OCR anchored insight survives scan. OCR tail."},
    )

    clock_values = iter(NOW + timedelta(seconds=index) for index in range(1000))
    annotations = AnnotationRepository(sessions, clock=lambda: next(clock_values))
    ids: dict[str, str] = {}

    def add(
        name: str,
        *,
        kind: AnnotationKind = AnnotationKind.HIGHLIGHT,
        page: int,
        quote: str | None,
        before: str | None = None,
        after: str | None = None,
        note: str | None = None,
    ) -> None:
        created = annotations.create(
            source.document_version_id,
            AnnotationInput(
                document_content_sha256=source.content_sha256,
                kind=kind,
                page_number=page,
                page_count=5,
                page_width=420,
                page_height=595,
                quote_text=quote,
                context_before=before,
                context_after=after,
                rects=()
                if kind is AnnotationKind.BOOKMARK
                else (PdfRect(x0=48, y0=48, x1=360, y1=82),),
                color=AnnotationColor.GREEN,
                note=note,
            ),
        )
        ids[name] = created.id

    add(
        "stable",
        kind=AnnotationKind.NOTE,
        page=1,
        quote="The unchanged mechanism remains reliable.",
        before="Lead context. ",
        after=" Tail context.",
        note="Keep this interpretation.",
    )
    add("bookmark", kind=AnnotationKind.BOOKMARK, page=1, quote=None)
    add(
        "punctuation",
        page=2,
        quote="Diffusion, agents: cooperate.",
        before="Before. ",
        after=" After.",
    )
    add(
        "medium",
        page=2,
        quote="Diffusion, agents: cooperate.",
        before="unrelated material before",
        after="unrelated material after",
    )
    add("duplicate", page=3, quote="repeated evidence phrase")
    add("deleted", page=4, quote="Deleted-only passage will disappear forever.")
    add(
        "ocr",
        page=5,
        quote="OCR anchored insight survives scan.",
        before="OCR context. ",
        after=" OCR tail.",
    )

    context = MigrationContext(
        sessions=sessions,
        service=AnnotationMigrationService(sessions, clock=lambda: NOW + timedelta(hours=1)),
        annotations=annotations,
        source_document_id=source.document_version_id,
        target_document_id=target.document_version_id,
        source_hash=source.content_sha256,
        target_hash=target.content_sha256,
        annotation_ids=ids,
    )
    yield context
    engine.dispose()


def _by_name(context: MigrationContext, preview: Any, name: str) -> Any:
    annotation_id = context.annotation_ids[name]
    return next(
        candidate
        for candidate in preview.candidates
        if candidate.source_annotation_id == annotation_id
    )


def test_preview_matches_exact_context_punctuation_duplicates_and_ocr_deterministically(
    migration_context: MigrationContext,
) -> None:
    from paper_reader.reader.models import (
        AnnotationMigrationBatchRecord,
        AnnotationMigrationCandidateRecord,
        AnnotationRecord,
    )

    context = migration_context
    before_source = context.annotations.list(context.source_document_id)
    preview = context.service.preview(
        context.source_document_id,
        context.target_document_id,
    )
    repeated = context.service.preview(
        context.source_document_id,
        context.target_document_id,
    )

    assert preview.id == repeated.id
    assert [candidate.id for candidate in preview.candidates] == [
        candidate.id for candidate in repeated.candidates
    ]
    assert preview.algorithm_version == "annotation-migration-v1"
    assert preview.coverage.total == 7
    assert preview.coverage.matched == 5
    assert preview.coverage.ambiguous == 1
    assert preview.coverage.orphaned == 1
    assert preview.coverage.pending == 7
    assert [candidate.source_page_number for candidate in preview.candidates] == sorted(
        candidate.source_page_number for candidate in preview.candidates
    )

    stable = _by_name(context, preview, "stable")
    punctuation = _by_name(context, preview, "punctuation")
    medium = _by_name(context, preview, "medium")
    duplicate = _by_name(context, preview, "duplicate")
    deleted = _by_name(context, preview, "deleted")
    ocr = _by_name(context, preview, "ocr")
    bookmark = _by_name(context, preview, "bookmark")

    assert (stable.match_state, stable.confidence, stable.target_page_number) == (
        "matched",
        "high",
        2,
    )
    assert stable.target_rects and stable.target_quote_text == stable.source_quote_text
    assert (punctuation.match_state, punctuation.confidence, punctuation.target_page_number) == (
        "matched",
        "high",
        4,
    )
    assert punctuation.target_quote_text == "Diffusion - agents cooperate."
    assert medium.match_state == "matched" and medium.confidence == "medium"
    assert duplicate.match_state == "ambiguous" and duplicate.confidence == "low"
    assert [alternative.page_number for alternative in duplicate.alternatives[:2]] == [3, 5]
    assert deleted.match_state == "orphaned" and deleted.confidence == "none"
    assert deleted.target_page_number is None and deleted.target_rects == ()
    assert (ocr.match_state, ocr.confidence, ocr.target_page_number, ocr.text_source) == (
        "matched",
        "high",
        6,
        "ocr",
    )
    assert ocr.target_rects
    assert (bookmark.match_state, bookmark.confidence, bookmark.target_page_number) == (
        "matched",
        "low",
        1,
    )

    assert context.annotations.list(context.source_document_id) == before_source
    assert context.annotations.list(context.target_document_id) == ()
    with context.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationMigrationBatchRecord)) == 1
        assert (
            session.scalar(select(func.count()).select_from(AnnotationMigrationCandidateRecord))
            == 7
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AnnotationRecord)
                .where(AnnotationRecord.document_version_id == context.target_document_id)
            )
            == 0
        )


def test_accept_creates_linked_target_annotation_reject_is_final_and_source_is_unchanged(
    migration_context: MigrationContext,
) -> None:
    from paper_reader.reader.migrations import MigrationConflictError

    context = migration_context
    preview = context.service.preview(context.source_document_id, context.target_document_id)
    source_before = context.annotations.list(context.source_document_id)
    stable = _by_name(context, preview, "stable")
    deleted = _by_name(context, preview, "deleted")

    with pytest.raises(MigrationConflictError, match="no target match"):
        context.service.decide(deleted.id, decision="accept")

    accepted = context.service.decide(stable.id, decision="accept")
    rejected = context.service.decide(deleted.id, decision="reject")

    assert accepted.decision == "accept"
    assert accepted.target_annotation_id is not None
    assert accepted.decided_at is not None
    assert rejected.decision == "reject"
    assert rejected.target_annotation_id is None
    target_annotations = context.annotations.list(context.target_document_id)
    assert len(target_annotations) == 1
    migrated = target_annotations[0]
    assert migrated.id == accepted.target_annotation_id
    assert migrated.document_content_sha256 == context.target_hash
    assert (migrated.kind, migrated.page_number, migrated.note, migrated.color) == (
        "note",
        2,
        "Keep this interpretation.",
        "green",
    )
    assert migrated.quote_text == "The unchanged mechanism remains reliable."
    assert context.annotations.list(context.source_document_id) == source_before

    refreshed = context.service.get(preview.id)
    assert refreshed.coverage.pending == 5
    assert refreshed.coverage.accepted == 1
    assert refreshed.coverage.rejected == 1
    with pytest.raises(MigrationConflictError, match="already been decided"):
        context.service.decide(stable.id, decision="accept")
    with pytest.raises(MigrationConflictError, match="already been decided"):
        context.service.decide(deleted.id, decision="reject")


def test_preview_rejects_invalid_pairs_and_decision_rejects_stale_target_extraction(
    migration_context: MigrationContext,
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.imports import LibraryImportService, PdfImportInput
    from paper_reader.reader.migrations import MigrationConflictError
    from paper_reader.reader.models import DocumentPageTextRecord
    from paper_reader.reader.storage import ContentStore

    context = migration_context
    with pytest.raises(MigrationConflictError, match="different document versions"):
        context.service.preview(context.source_document_id, context.source_document_id)

    other_path = write_text_pdf(tmp_path / "other-work.pdf", ("other work",))
    other = LibraryImportService(
        sessions=context.sessions,
        store=ContentStore(tmp_path / "library"),
        clock=lambda: NOW,
    ).import_pdf(
        PdfImportInput(
            work_id=None,
            title="Different work",
            authors=("Other Author",),
            doi=None,
            original_filename=other_path.name,
            rights_confirmed=True,
        ),
        (other_path.read_bytes(),),
    )
    _persist_extraction(context.sessions, other.document_version_id, other_path)
    with pytest.raises(MigrationConflictError, match="same Work"):
        context.service.preview(context.source_document_id, other.document_version_id)

    with context.sessions() as session:
        target = session.get(DocumentVersionRecord, context.target_document_id)
        assert target is not None
        target.extraction_status = "pending"
        session.commit()
    with pytest.raises(MigrationConflictError, match="extraction is not complete"):
        context.service.preview(context.source_document_id, context.target_document_id)
    with context.sessions() as session:
        target = session.get(DocumentVersionRecord, context.target_document_id)
        assert target is not None
        target.extraction_status = "complete"
        session.commit()

    preview = context.service.preview(context.source_document_id, context.target_document_id)
    stable = _by_name(context, preview, "stable")
    with context.sessions() as session:
        page = session.get(DocumentPageTextRecord, (context.target_document_id, 2))
        assert page is not None
        page.native_text += " changed after preview"
        session.commit()
    with pytest.raises(MigrationConflictError, match="extraction changed"):
        context.service.decide(stable.id, decision="accept")
    assert context.annotations.list(context.target_document_id) == ()


def test_large_preview_cancellation_persists_no_partial_batch(
    migration_context: MigrationContext,
) -> None:
    from paper_reader.reader.migrations import MigrationCancelledError
    from paper_reader.reader.models import (
        AnnotationMigrationBatchRecord,
        AnnotationMigrationCandidateRecord,
        AnnotationRecord,
    )

    context = migration_context
    with context.sessions() as session:
        session.add_all(
            [
                AnnotationRecord(
                    id=str(uuid4()),
                    document_version_id=context.source_document_id,
                    document_content_sha256=context.source_hash,
                    kind="bookmark",
                    page_number=1 + (index % 5),
                    page_width=420,
                    page_height=595,
                    quote_text=None,
                    context_before=None,
                    context_after=None,
                    rects=[],
                    color="amber",
                    note=None,
                    created_at=NOW + timedelta(minutes=index),
                    updated_at=NOW + timedelta(minutes=index),
                )
                for index in range(128)
            ]
        )
        session.commit()

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks > 20

    with pytest.raises(MigrationCancelledError):
        context.service.preview(
            context.source_document_id,
            context.target_document_id,
            cancelled=cancelled,
        )
    with context.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationMigrationBatchRecord)) == 0
        assert (
            session.scalar(select(func.count()).select_from(AnnotationMigrationCandidateRecord))
            == 0
        )
