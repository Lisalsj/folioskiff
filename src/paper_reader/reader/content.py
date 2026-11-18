"""Secure resolution of locally stored PDF content for the reader."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath

from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.reader.models import LibraryEntryRecord
from paper_reader.reader.storage import ContentStore, UnsafeStoredPathError

LEGAL_ACCESS = {"open", "user_provided"}
PDF_MEDIA_TYPE = "application/pdf"


class DocumentContentNotFoundError(KeyError):
    """The document does not exist or is not part of the saved library."""


class DocumentContentUnavailableError(RuntimeError):
    """The document metadata exists but its lawful local bytes cannot be read."""


@dataclass(frozen=True, slots=True)
class DocumentContent:
    path: Path
    media_type: str
    filename: str


class DocumentContentService:
    """Resolve one content-addressed PDF without exposing persisted paths."""

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        store: ContentStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._store = store
        self._clock = clock

    def open(self, document_version_id: str) -> DocumentContent:
        with self._sessions() as session:
            document = session.get(DocumentVersionRecord, document_version_id)
            if document is None:
                raise DocumentContentNotFoundError(document_version_id)
            canonical_work_id = _canonical_work_id(session, document.work_id)
            if session.get(LibraryEntryRecord, canonical_work_id) is None:
                raise DocumentContentNotFoundError(document_version_id)
            if (
                document.access_status not in LEGAL_ACCESS
                or document.media_type.casefold() != PDF_MEDIA_TYPE
                or document.relative_path is None
            ):
                raise DocumentContentUnavailableError(document_version_id)
            try:
                path = self._store.resolve_object(
                    document.content_sha256,
                    document.relative_path,
                )
            except UnsafeStoredPathError as error:
                raise DocumentContentUnavailableError(document_version_id) from error
            try:
                stat = path.stat()
            except OSError as error:
                raise DocumentContentUnavailableError(document_version_id) from error
            if not path.is_file() or stat.st_size != document.byte_size:
                raise DocumentContentUnavailableError(document_version_id)

            document.last_accessed_at = self._clock()
            filename = _safe_pdf_filename(document.original_filename)
            session.commit()
        return DocumentContent(
            path=path,
            media_type=PDF_MEDIA_TYPE,
            filename=filename,
        )


def _canonical_work_id(session: Session, work_id: str) -> str:
    work = session.get(WorkRecord, work_id)
    if work is None:
        raise DocumentContentNotFoundError(work_id)
    visited: set[str] = set()
    while work.status == "merged":
        if work.id in visited or work.merged_into_work_id is None:
            raise DocumentContentUnavailableError(work_id)
        visited.add(work.id)
        target = session.get(WorkRecord, work.merged_into_work_id)
        if target is None:
            raise DocumentContentUnavailableError(work_id)
        work = target
    if work.status != "active":
        raise DocumentContentUnavailableError(work_id)
    return work.id


def _safe_pdf_filename(value: str | None) -> str:
    filename = PurePath(value.replace("\\", "/")).name if value else "paper.pdf"
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename).strip()
    if not filename:
        return "paper.pdf"
    if not filename.casefold().endswith(".pdf"):
        filename = f"{filename}.pdf"
    if len(filename) > 240:
        filename = f"{filename[:-4][:236]}.pdf"
    return filename
