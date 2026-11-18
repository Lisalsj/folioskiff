"""Read models for the saved local paper library."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, column, exists, func, or_, select, table, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from paper_reader.discovery.models import (
    DocumentVersionRecord,
    LibraryStateRecord,
    SourceRecord,
    WorkRecord,
)
from paper_reader.reader.models import LibraryEntryRecord
from paper_reader.recommendation.lexical import compile_match_expression

LEGAL_ACCESS = {"open", "user_provided"}


@dataclass(frozen=True, slots=True)
class LibraryDocument:
    id: str
    work_id: str
    content_sha256: str
    access_status: str
    media_type: str
    completeness: int
    extraction_status: str
    available: bool
    byte_size: int
    original_filename: str | None
    acquisition_kind: str
    last_accessed_at: datetime | None
    created_at: datetime
    is_representative: bool


@dataclass(frozen=True, slots=True)
class LibraryWork:
    id: str
    title: str
    authors: tuple[str, ...]
    abstract: str | None
    doi: str | None
    published_at: datetime | None
    added_via: str
    added_at: datetime
    favorite: bool
    feedback: str
    pinned: bool
    reading_progress: float
    analysis_state: str
    document_count: int
    representative_document: LibraryDocument | None


@dataclass(frozen=True, slots=True)
class LibraryPage:
    items: tuple[LibraryWork, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class _LibraryEntryPageRow:
    work_id: str
    added_via: str
    created_at: datetime


_WORK_SEARCH = table("work_search", column("work_id"))


class LibraryRepository:
    """Expose saved Works without leaking local filesystem paths."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def list_entries(
        self,
        *,
        query: str | None,
        limit: int,
        offset: int,
        source: str | None = None,
        published_from: datetime | None = None,
        published_to: datetime | None = None,
        favorite: bool | None = None,
        unread: bool | None = None,
    ) -> LibraryPage:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        normalized_query = _search_key(query) if query is not None else ""
        with self._sessions() as session:
            total = self._count_entries(
                session,
                normalized_query,
                source=source,
                published_from=published_from,
                published_to=published_to,
                favorite=favorite,
                unread=unread,
            )
            if total == 0 or offset >= total:
                return LibraryPage(items=(), total=total, limit=limit, offset=offset)
            entries = self._page_entries(
                session,
                normalized_query,
                limit=limit,
                offset=offset,
                source=source,
                published_from=published_from,
                published_to=published_to,
                favorite=favorite,
                unread=unread,
            )
            items = self._work_domains(session, entries)
        return LibraryPage(items=items, total=total, limit=limit, offset=offset)

    @staticmethod
    def _count_entries(
        session: Session,
        normalized_query: str,
        *,
        source: str | None,
        published_from: datetime | None,
        published_to: datetime | None,
        favorite: bool | None,
        unread: bool | None,
    ) -> int:
        parameters: dict[str, str] = {}
        statement = select(func.count()).select_from(LibraryEntryRecord)
        doi_prefix = _doi_prefix(normalized_query)
        if doi_prefix is not None:
            statement = statement.join(
                WorkRecord,
                WorkRecord.id == LibraryEntryRecord.work_id,
            ).where(
                WorkRecord.status == "active",
                _doi_range(doi_prefix),
            )
        elif normalized_query:
            expression = compile_match_expression(
                normalized_query,
                operator="AND",
                prefix=True,
            )
            if not expression:
                return 0
            parameters["fts_query"] = expression
            statement = (
                statement.join(
                    _WORK_SEARCH,
                    _WORK_SEARCH.c.work_id == LibraryEntryRecord.work_id,
                )
                .join(WorkRecord, WorkRecord.id == LibraryEntryRecord.work_id)
                .where(
                    WorkRecord.status == "active",
                    text("work_search MATCH :fts_query"),
                )
            )
        else:
            statement = statement.where(
                exists(
                    select(1).where(
                        WorkRecord.id == LibraryEntryRecord.work_id,
                        WorkRecord.status == "active",
                    )
                )
            )
        statement = statement.where(
            *LibraryRepository._filter_clauses(
                source=source,
                published_from=published_from,
                published_to=published_to,
                favorite=favorite,
                unread=unread,
            )
        )
        return int(session.scalar(statement, parameters) or 0)

    @staticmethod
    def _page_entries(
        session: Session,
        normalized_query: str,
        *,
        limit: int,
        offset: int,
        source: str | None,
        published_from: datetime | None,
        published_to: datetime | None,
        favorite: bool | None,
        unread: bool | None,
    ) -> tuple[_LibraryEntryPageRow, ...]:
        parameters: dict[str, str] = {}
        statement = select(
            LibraryEntryRecord.work_id,
            LibraryEntryRecord.added_via,
            LibraryEntryRecord.created_at,
        )
        doi_prefix = _doi_prefix(normalized_query)
        if doi_prefix is not None:
            statement = statement.join(
                WorkRecord,
                WorkRecord.id == LibraryEntryRecord.work_id,
            ).where(
                WorkRecord.status == "active",
                _doi_range(doi_prefix),
            )
        elif normalized_query:
            expression = compile_match_expression(
                normalized_query,
                operator="AND",
                prefix=True,
            )
            if not expression:
                return ()
            parameters["fts_query"] = expression
            statement = (
                statement.join(
                    _WORK_SEARCH,
                    _WORK_SEARCH.c.work_id == LibraryEntryRecord.work_id,
                )
                .join(WorkRecord, WorkRecord.id == LibraryEntryRecord.work_id)
                .where(
                    WorkRecord.status == "active",
                    text("work_search MATCH :fts_query"),
                )
            )
        else:
            statement = statement.where(
                exists(
                    select(1).where(
                        WorkRecord.id == LibraryEntryRecord.work_id,
                        WorkRecord.status == "active",
                    )
                )
            )
        rows = session.execute(
            statement.where(
                *LibraryRepository._filter_clauses(
                    source=source,
                    published_from=published_from,
                    published_to=published_to,
                    favorite=favorite,
                    unread=unread,
                )
            ).order_by(
                LibraryEntryRecord.created_at.desc(),
                LibraryEntryRecord.work_id,
            )
            .limit(limit)
            .offset(offset),
            parameters,
        ).all()
        return tuple(
            _LibraryEntryPageRow(
                work_id=str(row.work_id),
                added_via=str(row.added_via),
                created_at=_utc(row.created_at),
            )
            for row in rows
        )

    @staticmethod
    def _filter_clauses(
        *,
        source: str | None,
        published_from: datetime | None,
        published_to: datetime | None,
        favorite: bool | None,
        unread: bool | None,
    ) -> tuple[ColumnElement[bool], ...]:
        clauses: list[ColumnElement[bool]] = []
        if source:
            clauses.append(
                exists(
                    select(1).where(
                        SourceRecord.work_id == LibraryEntryRecord.work_id,
                        SourceRecord.connector_id == source,
                    )
                )
            )
        if published_from is not None:
            clauses.append(
                exists(
                    select(1).where(
                        WorkRecord.id == LibraryEntryRecord.work_id,
                        WorkRecord.published_at >= published_from,
                    )
                )
            )
        if published_to is not None:
            clauses.append(
                exists(
                    select(1).where(
                        WorkRecord.id == LibraryEntryRecord.work_id,
                        WorkRecord.published_at <= published_to,
                    )
                )
            )
        if favorite is not None:
            clauses.append(
                exists(
                    select(1).where(
                        LibraryStateRecord.work_id == LibraryEntryRecord.work_id,
                        LibraryStateRecord.favorite.is_(favorite),
                    )
                )
            )
        if unread is not None:
            progress = LibraryStateRecord.reading_progress
            state_exists = exists(
                select(1).where(LibraryStateRecord.work_id == LibraryEntryRecord.work_id)
            )
            progress_match = exists(
                select(1).where(
                    LibraryStateRecord.work_id == LibraryEntryRecord.work_id,
                    (progress < 100.0) if unread else (progress >= 100.0),
                )
            )
            clauses.append(or_(~state_exists, progress_match) if unread else progress_match)
        return tuple(clauses)

    def _work_domains(
        self,
        session: Session,
        entries: tuple[_LibraryEntryPageRow, ...],
    ) -> tuple[LibraryWork, ...]:
        if not entries:
            return ()
        work_ids = tuple(entry.work_id for entry in entries)
        works = {
            work.id: work
            for work in session.scalars(
                select(WorkRecord).where(
                    WorkRecord.id.in_(work_ids),
                    WorkRecord.status == "active",
                )
            )
        }
        states = {
            state.work_id: state
            for state in session.scalars(
                select(LibraryStateRecord).where(LibraryStateRecord.work_id.in_(work_ids))
            )
        }
        document_records: dict[str, list[DocumentVersionRecord]] = {
            work_id: [] for work_id in work_ids
        }
        for record in session.scalars(
            select(DocumentVersionRecord)
            .where(DocumentVersionRecord.work_id.in_(work_ids))
            .order_by(
                DocumentVersionRecord.work_id,
                DocumentVersionRecord.source_updated_at.desc(),
                DocumentVersionRecord.completeness.desc(),
                DocumentVersionRecord.created_at.desc(),
                DocumentVersionRecord.id,
            )
        ):
            document_records[record.work_id].append(record)
        documents = {
            work_id: self._document_domains_from_records(records)
            for work_id, records in document_records.items()
        }
        return tuple(
            self._work_domain_from_parts(
                works[entry.work_id],
                entry,
                states.get(entry.work_id),
                documents[entry.work_id],
            )
            for entry in entries
            if entry.work_id in works
        )

    def get_work(self, work_id: str) -> LibraryWork:
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            entry = session.get(LibraryEntryRecord, work.id)
            if entry is None:
                raise KeyError(work_id)
            return self._work_domain(session, work, entry)

    def list_documents(self, work_id: str) -> tuple[LibraryDocument, ...]:
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            if session.get(LibraryEntryRecord, work.id) is None:
                raise KeyError(work_id)
            return self._document_domains(session, work.id)

    def _work_domain(
        self,
        session: Session,
        work: WorkRecord,
        entry: LibraryEntryRecord,
    ) -> LibraryWork:
        state = session.get(LibraryStateRecord, work.id)
        documents = self._document_domains(session, work.id)
        entry_row = _LibraryEntryPageRow(
            work_id=entry.work_id,
            added_via=entry.added_via,
            created_at=_utc(entry.created_at),
        )
        return self._work_domain_from_parts(work, entry_row, state, documents)

    @staticmethod
    def _work_domain_from_parts(
        work: WorkRecord,
        entry: _LibraryEntryPageRow,
        state: LibraryStateRecord | None,
        documents: tuple[LibraryDocument, ...],
    ) -> LibraryWork:
        representative = next(
            (document for document in documents if document.is_representative),
            None,
        )
        return LibraryWork(
            id=work.id,
            title=work.canonical_title,
            authors=tuple(work.authors),
            abstract=work.abstract,
            doi=work.doi,
            published_at=_optional_utc(work.published_at),
            added_via=entry.added_via,
            added_at=entry.created_at,
            favorite=state.favorite if state is not None else False,
            feedback=state.feedback if state is not None else "neutral",
            pinned=state.pinned if state is not None else False,
            reading_progress=state.reading_progress if state is not None else 0.0,
            analysis_state=(state.analysis_state if state is not None else "not_analyzed"),
            document_count=len(documents),
            representative_document=representative,
        )

    @staticmethod
    def _document_domains(
        session: Session,
        work_id: str,
    ) -> tuple[LibraryDocument, ...]:
        records = list(
            session.scalars(
                select(DocumentVersionRecord)
                .where(DocumentVersionRecord.work_id == work_id)
                .order_by(
                    DocumentVersionRecord.source_updated_at.desc(),
                    DocumentVersionRecord.completeness.desc(),
                    DocumentVersionRecord.created_at.desc(),
                    DocumentVersionRecord.id,
                )
            )
        )
        return LibraryRepository._document_domains_from_records(records)

    @staticmethod
    def _document_domains_from_records(
        records: list[DocumentVersionRecord],
    ) -> tuple[LibraryDocument, ...]:
        representative_id = next(
            (record.id for record in records if record.access_status in LEGAL_ACCESS),
            None,
        )
        return tuple(
            LibraryDocument(
                id=record.id,
                work_id=record.work_id,
                content_sha256=record.content_sha256,
                access_status=record.access_status,
                media_type=record.media_type,
                completeness=record.completeness,
                extraction_status=record.extraction_status,
                available=(
                    record.access_status in LEGAL_ACCESS and record.relative_path is not None
                ),
                byte_size=record.byte_size,
                original_filename=record.original_filename,
                acquisition_kind=record.acquisition_kind,
                last_accessed_at=_optional_utc(record.last_accessed_at),
                created_at=_utc(record.created_at),
                is_representative=record.id == representative_id,
            )
            for record in records
        )

    @staticmethod
    def _canonical_work(session: Session, work_id: str) -> WorkRecord:
        work = session.get(WorkRecord, work_id)
        if work is None:
            raise KeyError(work_id)
        visited: set[str] = set()
        while work.status == "merged":
            if work.id in visited or work.merged_into_work_id is None:
                raise RuntimeError("Invalid Work merge chain")
            visited.add(work.id)
            target = session.get(WorkRecord, work.merged_into_work_id)
            if target is None:
                raise RuntimeError("Merged Work target is missing")
            work = target
        return work


def _search_key(value: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if len(normalized) > 500:
        raise ValueError("query must contain at most 500 characters")
    return normalized


def _work_search_text(work: WorkRecord) -> str:
    return _search_key(
        " ".join(
            (
                work.canonical_title,
                *work.authors,
                work.doi or "",
            )
        )
    )


def _doi_prefix(normalized_query: str) -> str | None:
    candidate = normalized_query
    if candidate.startswith("doi:"):
        candidate = candidate[4:].strip()
    for prefix in ("https://doi.org/", "http://doi.org/"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    if (
        candidate.startswith("10.")
        and "/" in candidate
        and not any(character.isspace() for character in candidate)
    ):
        return candidate
    return None


def _doi_range(prefix: str) -> ColumnElement[bool]:
    return and_(WorkRecord.doi >= prefix, WorkRecord.doi < f"{prefix}\U0010ffff")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _utc(value) if value is not None else None
