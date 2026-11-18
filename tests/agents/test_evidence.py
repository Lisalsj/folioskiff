from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest


@pytest.fixture
def evidence_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.agents import models as agent_models
    from paper_reader.agents.evidence import EvidenceBundleBuilder
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
    from paper_reader.reader.models import AnnotationRecord, DocumentPageTextRecord
    from paper_reader.reader.storage import ContentStore

    assert agent_models.AgentSettingsRecord.__tablename__ == "agent_settings"
    engine = create_sqlite_engine(tmp_path / "evidence.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    store = ContentStore(tmp_path / "library")
    stored = store.ingest(
        (b"%PDF-1.7\nimmutable paper bytes\n%%EOF\n",),
        media_type="application/pdf",
        max_bytes=1024,
    )
    with sessions() as session:
        work = WorkRecord(
            id="work-main",
            canonical_title="Diffusion Fixture",
            normalized_title="diffusion fixture",
            authors=["Alice Chen", "Bob Li"],
            abstract="A concise fallback abstract.",
            doi="10.1000/fixture",
            published_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        document = DocumentVersionRecord(
            id="document-main",
            work_id=work.id,
            source_record_id=None,
            content_sha256=stored.content_sha256,
            access_status="user_provided",
            media_type="application/pdf",
            completeness=100,
            source_updated_at=datetime(2026, 7, 1, tzinfo=UTC),
            extraction_status="complete",
            relative_path=stored.relative_path,
            byte_size=stored.byte_size,
            original_filename="fixture.pdf",
            acquisition_kind="manual_import",
        )
        session.add(work)
        session.flush()
        session.add(document)
        session.flush()
        session.add_all(
            (
                DocumentPageTextRecord(
                    document_version_id=document.id,
                    page_number=2,
                    page_width=420,
                    page_height=595,
                    native_text="tiny header",
                    native_blocks=[],
                    native_character_count=11,
                    likely_scanned=True,
                    ocr_state="complete",
                    ocr_text="第二页 OCR evidence",
                    ocr_blocks=[],
                    ocr_confidence=0.92,
                    ocr_engine_version="fixture",
                    ocr_model_bundle_id="fixture-models",
                    extractor_version="fixture",
                ),
                DocumentPageTextRecord(
                    document_version_id=document.id,
                    page_number=1,
                    page_width=420,
                    page_height=595,
                    native_text="First page native evidence.",
                    native_blocks=[],
                    native_character_count=27,
                    likely_scanned=False,
                    ocr_state="not_needed",
                    ocr_text=None,
                    ocr_blocks=[],
                    ocr_confidence=None,
                    ocr_engine_version=None,
                    ocr_model_bundle_id=None,
                    extractor_version="fixture",
                ),
            )
        )
        session.add(
            AnnotationRecord(
                id="annotation-main",
                document_version_id=document.id,
                document_content_sha256=document.content_sha256,
                kind="highlight",
                page_number=1,
                page_width=420,
                page_height=595,
                quote_text="native evidence",
                context_before="First page ",
                context_after=".",
                rects=[{"x0": 10.0, "y0": 20.0, "x1": 100.0, "y1": 35.0}],
                color="amber",
                note="Important evidence",
            )
        )
        session.commit()
    builder = EvidenceBundleBuilder(sessions=sessions, store=store)
    try:
        yield builder, sessions, store, stored
    finally:
        engine.dispose()


def test_full_text_bundle_binds_exact_hash_pages_ocr_annotations_and_no_local_path(
    evidence_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.agents.evidence import EvidenceLevel

    builder, _, _, stored = evidence_context

    first = builder.build(
        "work-main",
        document_version_id="document-main",
        research_intent="Study diffusion evidence",
    )
    repeated = builder.build(
        "work-main",
        document_version_id="document-main",
        research_intent="Study diffusion evidence",
    )

    assert first == repeated
    assert first.evidence_level is EvidenceLevel.FULL_TEXT
    assert first.work.work_id == "work-main"
    assert first.document is not None
    assert first.document.document_version_id == "document-main"
    assert first.document.content_sha256 == stored.content_sha256
    assert [(page.page_number, page.source) for page in first.pages] == [
        (1, "native"),
        (2, "ocr"),
    ]
    assert first.pages[1].confidence == pytest.approx(0.92)
    assert first.annotations[0].evidence_id == "annotation:annotation-main"
    assert first.annotations[0].document_content_sha256 == stored.content_sha256
    assert first.coverage.percentage == 100
    assert len(first.evidence_sha256) == 64
    serialized = first.to_json()
    assert str(builder.store.root.resolve()) not in serialized
    assert stored.relative_path not in serialized


def test_page_annotation_or_research_intent_change_produces_a_new_evidence_hash(
    evidence_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.reader.models import AnnotationRecord, DocumentPageTextRecord

    builder, sessions, _, _ = evidence_context
    original = builder.build("work-main", document_version_id="document-main")
    with sessions() as session:
        page = session.get(DocumentPageTextRecord, ("document-main", 1))
        assert page is not None
        page.native_text += " Changed."
        page.native_character_count = len(page.native_text)
        session.commit()
    changed_page = builder.build("work-main", document_version_id="document-main")
    with sessions() as session:
        annotation = session.get(AnnotationRecord, "annotation-main")
        assert annotation is not None
        annotation.note = "Changed note"
        session.commit()
    changed_annotation = builder.build("work-main", document_version_id="document-main")
    changed_intent = builder.build(
        "work-main",
        document_version_id="document-main",
        research_intent="A different intent",
    )

    assert len(
        {
            original.evidence_sha256,
            changed_page.evidence_sha256,
            changed_annotation.evidence_sha256,
            changed_intent.evidence_sha256,
        }
    ) == 4


def test_corrupt_file_or_stale_annotation_hash_fails_closed(evidence_context) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.agents.evidence import EvidenceConflictError
    from paper_reader.reader.models import AnnotationRecord

    builder, sessions, store, stored = evidence_context
    object_path = store.resolve_object(stored.content_sha256, stored.relative_path)
    original = object_path.read_bytes()
    object_path.write_bytes(b"%PDF-1.7\ncorrupted\n")
    with pytest.raises(EvidenceConflictError, match="content hash"):
        builder.build("work-main", document_version_id="document-main")
    object_path.write_bytes(original)

    with sessions() as session:
        annotation = session.get(AnnotationRecord, "annotation-main")
        assert annotation is not None
        annotation.document_content_sha256 = "f" * 64
        session.commit()
    with pytest.raises(EvidenceConflictError, match="annotation"):
        builder.build("work-main", document_version_id="document-main")


def test_missing_full_text_uses_explicit_abstract_fallback_and_empty_evidence_rejects(
    evidence_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.agents.evidence import EvidenceLevel, EvidenceUnavailableError
    from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord

    builder, sessions, _, _ = evidence_context
    with sessions() as session:
        document = session.get(DocumentVersionRecord, "document-main")
        assert document is not None
        document.access_status = "restricted"
        session.commit()
    fallback = builder.build("work-main", document_version_id="document-main")

    assert fallback.evidence_level is EvidenceLevel.ABSTRACT_ONLY
    assert fallback.pages == ()
    assert fallback.document is not None
    assert "full_text_unavailable" in fallback.warnings
    assert fallback.abstract_evidence == "A concise fallback abstract."

    with sessions() as session:
        work = session.get(WorkRecord, "work-main")
        assert work is not None
        work.abstract = "  "
        session.commit()
    with pytest.raises(EvidenceUnavailableError, match="abstract"):
        builder.build("work-main", document_version_id="document-main")


def test_merged_work_resolves_to_canonical_and_cross_work_document_is_rejected(
    evidence_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.agents.evidence import EvidenceConflictError
    from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord

    builder, sessions, _, _ = evidence_context
    with sessions() as session:
        session.add(
            WorkRecord(
                id="work-merged",
                canonical_title="Old identity",
                normalized_title="old identity",
                authors=[],
                abstract=None,
                status="merged",
                merged_into_work_id="work-main",
            )
        )
        session.add(
            WorkRecord(
                id="work-other",
                canonical_title="Other",
                normalized_title="other",
                authors=[],
                abstract="Other abstract",
            )
        )
        session.flush()
        session.add(
            DocumentVersionRecord(
                id="document-other",
                work_id="work-other",
                source_record_id=None,
                content_sha256="e" * 64,
                access_status="unknown",
                media_type="application/pdf",
                completeness=0,
                extraction_status="pending",
                relative_path=None,
                byte_size=0,
                acquisition_kind="source",
            )
        )
        session.commit()

    merged = builder.build("work-merged", document_version_id="document-main")
    assert merged.work.work_id == "work-main"
    with pytest.raises(EvidenceConflictError, match="selected Work"):
        builder.build("work-main", document_version_id="document-other")


def test_partial_ocr_and_budget_truncation_are_visible_in_coverage(
    evidence_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.agents.evidence import EvidenceBundleBuilder
    from paper_reader.reader.models import DocumentPageTextRecord

    _, sessions, store, _ = evidence_context
    with sessions() as session:
        page = session.get(DocumentPageTextRecord, ("document-main", 2))
        assert page is not None
        page.ocr_state = "pending"
        page.ocr_text = None
        page.native_text = ""
        page.native_character_count = 0
        session.commit()
    builder = EvidenceBundleBuilder(sessions=sessions, store=store, character_budget=12)

    bundle = builder.build("work-main", document_version_id="document-main")

    assert bundle.coverage.total_pages == 2
    assert bundle.coverage.available_pages == 1
    assert bundle.coverage.included_pages == 1
    assert bundle.coverage.truncated is True
    assert bundle.pages[0].truncated is True
    assert len(bundle.pages[0].text) == 12
    assert "scanned_pages_without_ocr" in bundle.warnings
    assert "evidence_character_budget_reached" in bundle.warnings


def test_scoped_filters_are_exact_and_part_of_the_evidence_identity(
    evidence_context,
) -> None:  # type: ignore[no-untyped-def]
    builder, _, _, _ = evidence_context

    page_only = builder.build(
        "work-main",
        document_version_id="document-main",
        page_numbers=(2,),
        annotation_ids=(),
    )
    all_evidence = builder.build(
        "work-main",
        document_version_id="document-main",
    )

    assert [page.page_number for page in page_only.pages] == [2]
    assert page_only.annotations == ()
    assert page_only.filters.page_numbers == (2,)
    assert page_only.filters.annotation_ids == ()
    assert page_only.coverage.total_pages == 1
    assert page_only.evidence_sha256 != all_evidence.evidence_sha256
    assert page_only.schema_version == "agent-evidence-v2"


def test_selection_excerpt_is_verified_against_the_frozen_page_text(
    evidence_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.agents.evidence import (
        EvidenceSelectionExcerpt,
        EvidenceSelectionRect,
    )

    builder, _, _, _ = evidence_context
    excerpt = EvidenceSelectionExcerpt(
        page_number=1,
        page_width=420,
        page_height=595,
        quote_text="native evidence",
        context_before="First page",
        context_after=None,
        rects=(EvidenceSelectionRect(x0=10, y0=20, x1=100, y1=35),),
    )

    bundle = builder.build(
        "work-main",
        document_version_id="document-main",
        page_numbers=(1,),
        annotation_ids=(),
        selection_excerpt=excerpt,
    )

    assert bundle.selection_excerpt is not None
    assert bundle.selection_excerpt.verified is True
    assert "selection_unverified" not in bundle.warnings


@pytest.mark.parametrize(
    "page_numbers",
    [(2, 1), (1, 1), (0,), (2_001,), tuple(range(1, 202))],
)
def test_page_filters_reject_ambiguous_or_unbounded_ranges(
    evidence_context,
    page_numbers: tuple[int, ...],
) -> None:  # type: ignore[no-untyped-def]
    builder, _, _, _ = evidence_context

    with pytest.raises(ValueError, match="page_numbers"):
        builder.build(
            "work-main",
            document_version_id="document-main",
            page_numbers=page_numbers,
        )


def test_annotation_filter_fails_closed_when_an_id_is_not_on_the_document(
    evidence_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.agents.evidence import EvidenceUnavailableError

    builder, _, _, _ = evidence_context

    with pytest.raises(EvidenceUnavailableError, match="annotation"):
        builder.build(
            "work-main",
            document_version_id="document-main",
            page_numbers=(),
            annotation_ids=("missing-annotation",),
        )
