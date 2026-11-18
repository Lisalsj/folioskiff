from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def annotation_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery import models as discovery_models
    from paper_reader.reader.annotations import AnnotationRepository
    from paper_reader.reader.imports import LibraryImportService, PdfImportInput
    from paper_reader.reader.models import AnnotationRecord, ReadingPositionRecord
    from paper_reader.reader.storage import ContentStore

    assert discovery_models.WorkRecord.__tablename__ == "works"
    assert AnnotationRecord.__tablename__ == "annotations"
    assert ReadingPositionRecord.__tablename__ == "reading_positions"
    engine = create_sqlite_engine(tmp_path / "annotations.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    observed = [datetime(2026, 7, 13, 9, 0, tzinfo=UTC)]
    imports = LibraryImportService(
        sessions=sessions,
        store=ContentStore(tmp_path / "library"),
        clock=lambda: observed[0],
    )

    def add_document(label: str):  # type: ignore[no-untyped-def]
        return imports.import_pdf(
            PdfImportInput(
                work_id=None,
                title=f"Annotation paper {label}",
                authors=("Alice Chen",),
                doi=None,
                original_filename=f"{label}.pdf",
                rights_confirmed=True,
            ),
            (f"%PDF-1.7\n{label}\n%%EOF\n".encode(),),
        )

    first = add_document("first")
    second = add_document("second")
    repository = AnnotationRepository(sessions, clock=lambda: observed[0])
    try:
        yield repository, sessions, observed, first, second
    finally:
        engine.dispose()


def test_annotation_inputs_enforce_page_geometry_and_preserve_exact_evidence() -> None:
    from paper_reader.reader.annotations import (
        AnnotationColor,
        AnnotationInput,
        AnnotationKind,
        PdfRect,
        ReadingPositionInput,
    )

    quote = "  exact evidence\nwith spacing  "
    context_before = "before\n"
    context_after = "\nafter"
    value = AnnotationInput(
        document_content_sha256="a" * 64,
        kind=AnnotationKind.HIGHLIGHT,
        page_number=2,
        page_count=4,
        page_width=420,
        page_height=595,
        quote_text=quote,
        context_before=context_before,
        context_after=context_after,
        rects=(PdfRect(x0=12, y0=20, x1=180, y1=36),),
        color=AnnotationColor.AMBER,
        note=None,
    )

    assert value.quote_text == quote
    assert value.context_before == context_before
    assert value.context_after == context_after
    bookmark = AnnotationInput(
        document_content_sha256="a" * 64,
        kind="bookmark",
        page_number=1,
        page_count=1,
        page_width=420,
        page_height=595,
        quote_text=None,
        context_before=None,
        context_after=None,
        rects=(),
        color="green",
        note="Return to this figure",
    )
    assert bookmark.kind is AnnotationKind.BOOKMARK
    assert bookmark.rects == ()
    assert bookmark.color is AnnotationColor.GREEN

    with pytest.raises(ValueError, match="page_number"):
        ReadingPositionInput(
            document_content_sha256="a" * 64,
            page_number=5,
            page_count=4,
            scale=1.1,
            rotation=0,
        )
    with pytest.raises(ValueError, match="finite"):
        PdfRect(x0=0, y0=0, x1=float("nan"), y1=10)
    with pytest.raises(ValueError, match="positive area"):
        PdfRect(x0=10, y0=0, x1=10, y1=10)
    with pytest.raises(ValueError, match="page dimensions"):
        AnnotationInput(
            document_content_sha256="a" * 64,
            kind="highlight",
            page_number=1,
            page_count=1,
            page_width=100,
            page_height=100,
            quote_text="outside",
            context_before=None,
            context_after=None,
            rects=(PdfRect(x0=0, y0=0, x1=101, y1=10),),
            color="amber",
            note=None,
        )
    with pytest.raises(ValueError, match="quote_text"):
        AnnotationInput(
            document_content_sha256="a" * 64,
            kind="highlight",
            page_number=1,
            page_count=1,
            page_width=100,
            page_height=100,
            quote_text=" ",
            context_before=None,
            context_after=None,
            rects=(PdfRect(x0=0, y0=0, x1=10, y1=10),),
            color="amber",
            note=None,
        )
    with pytest.raises(ValueError, match="note kind"):
        AnnotationInput(
            document_content_sha256="a" * 64,
            kind="note",
            page_number=1,
            page_count=1,
            page_width=100,
            page_height=100,
            quote_text="evidence",
            context_before=None,
            context_after=None,
            rects=(PdfRect(x0=0, y0=0, x1=10, y1=10),),
            color="rose",
            note=None,
        )


def test_reading_positions_restore_per_document_are_idempotent_and_sync_work_progress(
    annotation_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import DocumentVersionRecord, LibraryStateRecord
    from paper_reader.reader.annotations import ReadingPositionInput

    repository, sessions, observed, first, second = annotation_context
    with sessions() as session:
        first_document = session.get(DocumentVersionRecord, first.document_version_id)
        second_document = session.get(DocumentVersionRecord, second.document_version_id)
        assert first_document is not None and second_document is not None
        first_hash = first_document.content_sha256
        second_hash = second_document.content_sha256

    saved = repository.save_position(
        first.document_version_id,
        ReadingPositionInput(
            document_content_sha256=first_hash,
            page_number=1,
            page_count=4,
            scale=1.25,
            rotation=90,
        ),
    )
    assert saved.progress == 25
    assert saved.scale == 1.25
    assert saved.rotation == 90
    first_updated_at = saved.updated_at

    observed[0] += timedelta(minutes=2)
    repeated = repository.save_position(
        first.document_version_id,
        ReadingPositionInput(
            document_content_sha256=first_hash,
            page_number=1,
            page_count=4,
            scale=1.25,
            rotation=90,
        ),
    )
    assert repeated.updated_at == first_updated_at

    observed[0] += timedelta(minutes=2)
    advanced = repository.save_position(
        first.document_version_id,
        ReadingPositionInput(
            document_content_sha256=first_hash,
            page_number=4,
            page_count=4,
            scale=0.8,
            rotation=180,
        ),
    )
    second_position = repository.save_position(
        second.document_version_id,
        ReadingPositionInput(
            document_content_sha256=second_hash,
            page_number=2,
            page_count=10,
            scale=1.0,
            rotation=0,
        ),
    )

    assert advanced.progress == 100
    assert repository.get_position(first.document_version_id) == advanced
    assert repository.get_position(second.document_version_id) == second_position
    assert repository.get_position(first.document_version_id).page_number == 4  # type: ignore[union-attr]
    with sessions() as session:
        first_state = session.get(LibraryStateRecord, first.work_id)
        second_state = session.get(LibraryStateRecord, second.work_id)
        assert first_state is not None and first_state.reading_progress == 100
        assert second_state is not None and second_state.reading_progress == 20


def test_annotation_crud_keeps_document_identity_and_extracted_evidence_immutable(
    annotation_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.annotations import (
        AnnotationConflictError,
        AnnotationInput,
        AnnotationNotFoundError,
        AnnotationRepository,
        PdfRect,
    )
    from paper_reader.reader.models import DocumentPageTextRecord

    repository: AnnotationRepository
    repository, sessions, observed, first, second = annotation_context
    with sessions() as session:
        document = session.get(DocumentVersionRecord, first.document_version_id)
        other = session.get(DocumentVersionRecord, second.document_version_id)
        assert document is not None and other is not None
        content_hash = document.content_sha256
        other_hash = other.content_sha256
        session.add(
            DocumentPageTextRecord(
                document_version_id=document.id,
                page_number=1,
                page_width=420,
                page_height=595,
                native_text="immutable extracted evidence",
                native_blocks=[],
                native_character_count=28,
                likely_scanned=False,
                ocr_state="not_needed",
                ocr_text=None,
                ocr_blocks=[],
                ocr_confidence=None,
                extractor_version="fixture-v1",
            )
        )
        session.commit()

    def input_value(*, note: str | None = None, color: str = "amber") -> AnnotationInput:
        return AnnotationInput(
            document_content_sha256=content_hash,
            kind="highlight",
            page_number=1,
            page_count=2,
            page_width=420,
            page_height=595,
            quote_text="  immutable evidence  ",
            context_before="before",
            context_after="after",
            rects=(PdfRect(x0=10, y0=20, x1=160, y1=38),),
            color=color,
            note=note,
        )

    later = repository.create(first.document_version_id, input_value(note="later"))
    observed[0] -= timedelta(minutes=1)
    earlier = repository.create(first.document_version_id, input_value(note="earlier"))
    listed = repository.list(first.document_version_id)

    assert [item.id for item in listed] == [earlier.id, later.id]
    assert earlier.document_version_id == first.document_version_id
    assert earlier.document_content_sha256 == content_hash
    assert earlier.quote_text == "  immutable evidence  "

    observed[0] += timedelta(minutes=3)
    updated = repository.update(
        first.document_version_id,
        earlier.id,
        input_value(note="revised", color="blue"),
    )
    assert updated.id == earlier.id
    assert updated.color.value == "blue"
    assert updated.note == "revised"
    assert updated.created_at == earlier.created_at
    assert updated.updated_at > earlier.updated_at

    repository.delete(first.document_version_id, later.id)
    assert [item.id for item in repository.list(first.document_version_id)] == [earlier.id]
    with sessions() as session:
        page = session.get(DocumentPageTextRecord, (first.document_version_id, 1))
        assert page is not None
        assert page.native_text == "immutable extracted evidence"

    with pytest.raises(AnnotationConflictError, match="content hash"):
        repository.create(
            first.document_version_id,
            replace(input_value(), document_content_sha256="f" * 64),
        )
    with pytest.raises(AnnotationNotFoundError):
        repository.update(
            second.document_version_id,
            earlier.id,
            replace(input_value(), document_content_sha256=other_hash),
        )
    with pytest.raises(AnnotationNotFoundError):
        repository.delete(first.document_version_id, "missing-annotation")


@pytest.mark.parametrize("target_has_position", [True, False])
def test_work_merge_rebinds_annotations_and_keeps_latest_duplicate_document_position(
    tmp_path: Path,
    target_has_position: bool,
) -> None:
    from sqlalchemy import select

    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery import models as discovery_models
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.models import (
        AnnotationRecord,
        LibraryEntryRecord,
        ReadingPositionRecord,
    )

    assert discovery_models.WorkRecord.__tablename__ == "works"
    engine = create_sqlite_engine(tmp_path / "merge-annotations.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    identity = IdentityRepository(sessions)
    target = identity.ingest(
        SourcePaper(
            connector_id="openreview",
            external_id="annotation-merge-target",
            title="Tool-Augmented Agents for Scientific Discovery",
            authors=("A. Chen", "R. Kumar"),
        )
    )
    candidate = identity.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.annotation-merge",
            title="Tool Augmented Agent for Scientific Discoveries",
            authors=("Alice Chen", "Ravi Kumar"),
        )
    )
    assert candidate.merge_review is not None
    content_hash = "e" * 64
    target_document = identity.add_document_version(
        work_id=target.work.id,
        source_record_id=target.source_record.id,
        content_sha256=content_hash,
        access_status="open",
        completeness=100,
        source_updated_at=datetime(2026, 7, 13, 8, 0, tzinfo=UTC),
    )
    candidate_document = identity.add_document_version(
        work_id=candidate.work.id,
        source_record_id=candidate.source_record.id,
        content_sha256=content_hash,
        access_status="open",
        completeness=90,
        source_updated_at=datetime(2026, 7, 13, 8, 5, tzinfo=UTC),
    )
    with sessions() as session:
        records = [
            LibraryEntryRecord(work_id=target.work.id, added_via="save"),
            LibraryEntryRecord(work_id=candidate.work.id, added_via="save"),
            ReadingPositionRecord(
                document_version_id=candidate_document.id,
                document_content_sha256=content_hash,
                page_number=3,
                page_count=4,
                scale=1.25,
                rotation=90,
                progress=75,
                created_at=datetime(2026, 7, 13, 8, 1, tzinfo=UTC),
                updated_at=datetime(2026, 7, 13, 8, 5, tzinfo=UTC),
            ),
            AnnotationRecord(
                id="merge-annotation",
                document_version_id=candidate_document.id,
                document_content_sha256=content_hash,
                kind="highlight",
                page_number=3,
                page_width=420,
                page_height=595,
                quote_text="preserve this evidence",
                context_before="before",
                context_after="after",
                rects=[{"x0": 10, "y0": 20, "x1": 120, "y1": 38}],
                color="green",
                note=None,
                created_at=datetime(2026, 7, 13, 8, 4, tzinfo=UTC),
                updated_at=datetime(2026, 7, 13, 8, 4, tzinfo=UTC),
            ),
        ]
        if target_has_position:
            records.append(
                ReadingPositionRecord(
                    document_version_id=target_document.id,
                    document_content_sha256=content_hash,
                    page_number=1,
                    page_count=4,
                    scale=1,
                    rotation=0,
                    progress=25,
                    created_at=datetime(2026, 7, 13, 8, 0, tzinfo=UTC),
                    updated_at=datetime(2026, 7, 13, 8, 0, tzinfo=UTC),
                )
            )
        session.add_all(records)
        session.commit()

    identity.decide_merge_review(candidate.merge_review.id, decision="merge")

    with sessions() as session:
        documents = list(
            session.scalars(
                select(DocumentVersionRecord).where(
                    DocumentVersionRecord.work_id == target.work.id
                )
            )
        )
        assert [document.id for document in documents] == [target_document.id]
        annotation = session.get(AnnotationRecord, "merge-annotation")
        assert annotation is not None
        assert annotation.document_version_id == target_document.id
        assert annotation.document_content_sha256 == content_hash
        position = session.get(ReadingPositionRecord, target_document.id)
        assert position is not None
        assert (position.page_number, position.scale, position.rotation, position.progress) == (
            3,
            1.25,
            90,
            75,
        )
        assert session.get(ReadingPositionRecord, candidate_document.id) is None
    engine.dispose()
