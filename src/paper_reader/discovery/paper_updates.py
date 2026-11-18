"""Paper-update state queries over immutable source revision evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import (
    DocumentVersionRecord,
    PaperUpdateRecord,
    SourceRecord,
    SourceRevisionRecord,
    WorkRecord,
)

LEGAL_DOCUMENT_ACCESS = ("open", "user_provided")


class PaperUpdateState(StrEnum):
    UNREAD = "unread"
    READ = "read"
    DISMISSED = "dismissed"


@dataclass(frozen=True, slots=True)
class PaperUpdate:
    id: str
    run_id: str | None
    work_id: str
    source_record_id: str
    previous_revision_id: str
    current_revision_id: str
    update_type: str
    visibility: str
    state: PaperUpdateState
    evidence: dict[str, object]
    detected_at: datetime
    acted_at: datetime | None


@dataclass(frozen=True, slots=True)
class PaperUpdateWork:
    id: str
    title: str
    authors: tuple[str, ...]
    doi: str | None


@dataclass(frozen=True, slots=True)
class PaperUpdateSource:
    connector_id: str
    external_id: str


@dataclass(frozen=True, slots=True)
class PaperUpdateListItem:
    id: str
    run_id: str | None
    state: PaperUpdateState
    changed_fields: tuple[str, ...]
    source_updated_at: datetime | None
    detected_at: datetime
    acted_at: datetime | None
    work: PaperUpdateWork
    source: PaperUpdateSource
    has_readable_document: bool


@dataclass(frozen=True, slots=True)
class PaperUpdatePage:
    items: tuple[PaperUpdateListItem, ...]
    total: int
    limit: int
    offset: int


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)


class PaperUpdateRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def list_for_work(
        self,
        work_id: str,
        *,
        visible_only: bool = True,
    ) -> tuple[PaperUpdate, ...]:
        with self._sessions() as session:
            query = select(PaperUpdateRecord).where(PaperUpdateRecord.work_id == work_id)
            if visible_only:
                query = query.where(PaperUpdateRecord.visibility == "updates")
            records = session.scalars(
                query.order_by(PaperUpdateRecord.detected_at, PaperUpdateRecord.id)
            )
            return tuple(self._domain(record) for record in records)

    def list_visible(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        state: PaperUpdateState | None = None,
    ) -> PaperUpdatePage:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        filters = [PaperUpdateRecord.visibility == "updates"]
        if run_id is not None:
            filters.append(PaperUpdateRecord.run_id == run_id)
        if state is not None:
            filters.append(PaperUpdateRecord.state == state.value)
        with self._sessions() as session:
            total = int(
                session.scalar(
                    select(func.count()).select_from(PaperUpdateRecord).where(*filters)
                )
                or 0
            )
            rows = tuple(
                session.execute(
                    select(
                        PaperUpdateRecord,
                        WorkRecord,
                        SourceRecord,
                        SourceRevisionRecord,
                    )
                    .join(WorkRecord, WorkRecord.id == PaperUpdateRecord.work_id)
                    .join(SourceRecord, SourceRecord.id == PaperUpdateRecord.source_record_id)
                    .join(
                        SourceRevisionRecord,
                        SourceRevisionRecord.id == PaperUpdateRecord.current_revision_id,
                    )
                    .where(*filters)
                    .order_by(PaperUpdateRecord.detected_at.desc(), PaperUpdateRecord.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            work_ids = {row[0].work_id for row in rows}
            readable_work_ids = (
                set(
                    session.scalars(
                        select(DocumentVersionRecord.work_id)
                        .where(
                            DocumentVersionRecord.work_id.in_(work_ids),
                            DocumentVersionRecord.access_status.in_(LEGAL_DOCUMENT_ACCESS),
                        )
                        .distinct()
                    )
                )
                if work_ids
                else set()
            )
            items = tuple(
                self._list_item(
                    update,
                    work,
                    source,
                    revision,
                    has_readable_document=update.work_id in readable_work_ids,
                )
                for update, work, source, revision in rows
            )
        return PaperUpdatePage(items=items, total=total, limit=limit, offset=offset)

    def set_state(
        self,
        update_id: str | None,
        state: PaperUpdateState,
        *,
        now: datetime,
    ) -> PaperUpdate:
        observed_at = _require_aware(now)
        if update_id is None:
            raise KeyError("Paper update not found")
        with self._sessions() as session:
            record = session.get(PaperUpdateRecord, update_id)
            if record is None or record.visibility != "updates":
                raise KeyError("Paper update not found")
            record.state = state.value
            record.acted_at = observed_at
            session.commit()
            return self._domain(record)

    @staticmethod
    def _domain(record: PaperUpdateRecord) -> PaperUpdate:
        return PaperUpdate(
            id=record.id,
            run_id=record.run_id,
            work_id=record.work_id,
            source_record_id=record.source_record_id,
            previous_revision_id=record.previous_revision_id,
            current_revision_id=record.current_revision_id,
            update_type=record.update_type,
            visibility=record.visibility,
            state=PaperUpdateState(record.state),
            evidence=dict(record.evidence),
            detected_at=_aware_utc(record.detected_at),
            acted_at=_aware_utc(record.acted_at) if record.acted_at is not None else None,
        )

    @staticmethod
    def _list_item(
        update: PaperUpdateRecord,
        work: WorkRecord,
        source: SourceRecord,
        revision: SourceRevisionRecord,
        *,
        has_readable_document: bool,
    ) -> PaperUpdateListItem:
        return PaperUpdateListItem(
            id=update.id,
            run_id=update.run_id,
            state=PaperUpdateState(update.state),
            changed_fields=tuple(str(field) for field in revision.changed_fields),
            source_updated_at=(
                _aware_utc(revision.source_updated_at)
                if revision.source_updated_at is not None
                else None
            ),
            detected_at=_aware_utc(update.detected_at),
            acted_at=_aware_utc(update.acted_at) if update.acted_at is not None else None,
            work=PaperUpdateWork(
                id=work.id,
                title=work.canonical_title,
                authors=tuple(work.authors),
                doi=work.doi,
            ),
            source=PaperUpdateSource(
                connector_id=source.connector_id,
                external_id=source.external_id,
            ),
            has_readable_document=has_readable_document,
        )
