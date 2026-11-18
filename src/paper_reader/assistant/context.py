"""Build immutable Assistant context snapshots from explicit reader scope."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.agents.evidence import (
    EvidenceBundleBuilder,
    EvidenceConflictError,
    EvidenceSelectionExcerpt,
    EvidenceSelectionRect,
    EvidenceUnavailableError,
)
from paper_reader.assistant.contracts import ContextScope, ContextSelection
from paper_reader.assistant.repository import (
    AssistantContextSnapshot,
    AssistantRepository,
    ContextSnapshotDraft,
)
from paper_reader.assistant.sectioning import (
    SectionBlock,
    SectionPage,
    detect_section,
)
from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.reader.models import DocumentPageTextRecord

_MAX_SECTION_PAGES = 200
_MAX_DOCUMENT_PAGES = 2_000
_MAX_SYNTHETIC_BLOCKS_PER_PAGE = 2_000


class AssistantContextBuilder:
    """Freeze one exact document version and the user's explicit evidence scope."""

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        evidence: EvidenceBundleBuilder,
        repository: AssistantRepository | None = None,
    ) -> None:
        self._sessions = sessions
        self._evidence = evidence
        self._repository = repository

    def build(
        self,
        work_id: str,
        selection: ContextSelection,
    ) -> ContextSnapshotDraft:
        if not isinstance(selection, ContextSelection):
            raise ValueError("selection must be a ContextSelection")
        with self._sessions() as session:
            canonical = self._resolve_work(session, work_id)
            document = session.get(DocumentVersionRecord, selection.document_version_id)
            if document is None:
                raise EvidenceUnavailableError("Selected document version was not found")
            document_work = self._resolve_work(session, document.work_id)
            if document_work.id != canonical.id:
                raise EvidenceConflictError(
                    "Selected document version does not belong to the selected Work"
                )
            records = tuple(
                session.scalars(
                    select(DocumentPageTextRecord)
                    .where(DocumentPageTextRecord.document_version_id == document.id)
                    .order_by(DocumentPageTextRecord.page_number)
                )
            )
            page_numbers, section_fallback, scope_warnings = self._scope_pages(
                selection,
                records,
            )
            extraction_version = _extraction_version(records, page_numbers)

        page_filter: tuple[int, ...] | None
        if selection.scope is ContextScope.FULL_DOCUMENT:
            page_filter = None
        elif selection.scope is ContextScope.ANNOTATIONS:
            page_filter = ()
        else:
            page_filter = page_numbers
        annotation_filter = (
            selection.annotation_ids if selection.scope is ContextScope.ANNOTATIONS else ()
        )
        excerpt = (
            _selection_excerpt(selection) if selection.scope is ContextScope.SELECTION else None
        )
        evidence = self._evidence.build(
            canonical.id,
            document_version_id=document.id,
            page_numbers=page_filter,
            annotation_ids=annotation_filter,
            selection_excerpt=excerpt,
        )
        public_document = evidence.document
        if public_document is None:  # pragma: no cover - selected above and rechecked by builder
            raise EvidenceConflictError("Context evidence lost its document identity")
        warnings = _ordered_unique((*scope_warnings, *evidence.warnings))
        return ContextSnapshotDraft.create(
            work_id=evidence.work.work_id,
            document_version_id=public_document.document_version_id,
            document_content_sha256=public_document.content_sha256,
            scope=selection.scope,
            page_numbers=page_numbers,
            selection=selection.selection,
            annotation_ids=selection.annotation_ids,
            section_fallback=section_fallback,
            extraction_version=extraction_version,
            evidence=evidence.model_dump(mode="json"),
            evidence_sha256=evidence.evidence_sha256,
            warnings=warnings,
        )

    def stored(self, snapshot_id: str) -> AssistantContextSnapshot:
        """Read an already-frozen snapshot without resolving a newer document."""

        if self._repository is None:
            raise RuntimeError("stored snapshots require an AssistantRepository")
        return self._repository.snapshot(snapshot_id)

    def freeze(
        self,
        work_id: str,
        selection: ContextSelection,
    ) -> AssistantContextSnapshot:
        """Freeze an explicit reader scope without creating an Agent task."""

        if self._repository is None:
            raise RuntimeError("freezing context requires an AssistantRepository")
        draft = self.build(work_id, selection)
        return self._repository.freeze_context(work_id, draft)

    @staticmethod
    def _scope_pages(
        selection: ContextSelection,
        records: tuple[DocumentPageTextRecord, ...],
    ) -> tuple[tuple[int, ...], bool, tuple[str, ...]]:
        if selection.scope is ContextScope.SELECTION:
            assert selection.selection is not None
            return (selection.selection.page_number,), False, ()
        if selection.scope is ContextScope.CURRENT_PAGE:
            assert selection.page_number is not None
            return (selection.page_number,), False, ()
        if selection.scope is ContextScope.ANNOTATIONS:
            return (), False, ()
        if selection.scope is ContextScope.FULL_DOCUMENT:
            pages = tuple(record.page_number for record in records)
            if len(pages) > _MAX_DOCUMENT_PAGES or any(
                page < 1 or page > _MAX_DOCUMENT_PAGES for page in pages
            ):
                raise EvidenceUnavailableError(
                    "Full-document Assistant context exceeds the 2,000-page safety limit"
                )
            return pages, False, ()

        assert selection.page_number is not None
        section_pages = tuple(_section_page(record) for record in records)
        if selection.page_number not in {page.page_number for page in section_pages}:
            return (
                (selection.page_number,),
                True,
                ("section_detection_fallback", "requested_pages_unavailable"),
            )
        detected = detect_section(section_pages, current_page=selection.page_number)
        pages = tuple(range(detected.start_page, detected.end_page + 1))
        if len(pages) > _MAX_SECTION_PAGES:
            return (
                (selection.page_number,),
                True,
                ("section_page_limit_fallback",),
            )
        return (
            pages,
            detected.fallback,
            ("section_detection_fallback",) if detected.fallback else (),
        )

    @staticmethod
    def _resolve_work(session: Session, work_id: str) -> WorkRecord:
        visited: set[str] = set()
        current_id = work_id
        while True:
            if current_id in visited:
                raise EvidenceConflictError("Work merge chain contains a cycle")
            visited.add(current_id)
            work = session.get(WorkRecord, current_id)
            if work is None:
                raise EvidenceUnavailableError("Selected Work was not found")
            if work.status == "active" and work.merged_into_work_id is None:
                return work
            if work.status != "merged" or not work.merged_into_work_id:
                raise EvidenceConflictError("Work merge state is invalid")
            current_id = work.merged_into_work_id


def _selection_excerpt(selection: ContextSelection) -> EvidenceSelectionExcerpt:
    selected = selection.selection
    assert selected is not None
    return EvidenceSelectionExcerpt(
        page_number=selected.page_number,
        page_width=selected.page_width,
        page_height=selected.page_height,
        quote_text=selected.quote_text,
        context_before=selected.context_before,
        context_after=selected.context_after,
        rects=tuple(
            EvidenceSelectionRect(
                x0=rect.x0,
                y0=rect.y0,
                x1=rect.x1,
                y1=rect.y1,
            )
            for rect in selected.rects
        ),
        verified=False,
    )


def _section_page(record: DocumentPageTextRecord) -> SectionPage:
    if (
        not math.isfinite(record.page_width)
        or not math.isfinite(record.page_height)
        or record.page_width <= 0
        or record.page_height <= 0
    ):
        raise EvidenceConflictError("Stored page dimensions are invalid")
    use_ocr = bool(record.likely_scanned and record.ocr_state == "complete" and record.ocr_text)
    text = record.ocr_text if use_ocr else record.native_text
    raw_blocks = record.ocr_blocks if use_ocr else record.native_blocks
    blocks = tuple(
        _section_block(value, page_width=record.page_width, page_height=record.page_height)
        for value in raw_blocks
    )
    if not blocks:
        blocks = _synthetic_blocks(
            text or "",
            page_width=record.page_width,
            page_height=record.page_height,
        )
    return SectionPage(
        page_number=record.page_number,
        text=text or "",
        blocks=blocks,
    )


def _section_block(
    value: Mapping[str, object],
    *,
    page_width: float,
    page_height: float,
) -> SectionBlock:
    try:
        block_number = value["block_number"]
        text = value["text"]
        coordinates = tuple(_stored_float(value[key]) for key in ("x0", "y0", "x1", "y1"))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise EvidenceConflictError("Stored page block is invalid") from error
    if (
        isinstance(block_number, bool)
        or not isinstance(block_number, int)
        or not isinstance(text, str)
        or any(not math.isfinite(item) for item in coordinates)
        or coordinates[0] < 0
        or coordinates[1] < 0
        or coordinates[2] > page_width
        or coordinates[3] > page_height
    ):
        raise EvidenceConflictError("Stored page block is outside the PDF page")
    try:
        return SectionBlock(
            block_number=block_number,
            text=text,
            x0=coordinates[0],
            y0=coordinates[1],
            x1=coordinates[2],
            y1=coordinates[3],
        )
    except ValueError as error:
        raise EvidenceConflictError("Stored page block is invalid") from error


def _synthetic_blocks(
    text: str,
    *,
    page_width: float,
    page_height: float,
) -> tuple[SectionBlock, ...]:
    lines = tuple(line.strip() for line in text.splitlines() if line.strip())
    if len(lines) > _MAX_SYNTHETIC_BLOCKS_PER_PAGE:
        raise EvidenceConflictError("Stored page has too many text lines for section detection")
    if not lines:
        return ()
    step = max(1.0, min(12.0, page_height / (len(lines) + 1)))
    height = max(0.5, step * 0.8)
    right = page_width
    return tuple(
        SectionBlock(
            block_number=index,
            text=line,
            x0=0,
            y0=min(page_height - height, index * step),
            x1=right,
            y1=min(page_height, min(page_height - height, index * step) + height),
        )
        for index, line in enumerate(lines, start=1)
    )


def _stored_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("stored coordinate is not numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("stored coordinate is not finite")
    return result


def _extraction_version(
    records: tuple[DocumentPageTextRecord, ...],
    selected_pages: tuple[int, ...],
) -> str | None:
    selected = set(selected_pages)
    versions = sorted(
        {
            record.extractor_version
            for record in records
            if (not selected or record.page_number in selected) and record.extractor_version
        }
    )
    if not versions:
        return None
    if len(versions) == 1:
        return versions[0]
    digest = hashlib.sha256("\0".join(versions).encode("utf-8")).hexdigest()[:24]
    return f"mixed:{digest}"


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


__all__ = ["AssistantContextBuilder"]
