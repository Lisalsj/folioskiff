"""Persistence for append-only, field-provenanced enrichment observations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import MetadataObservationRecord, WorkRecord
from paper_reader.recommendation.enrichment import (
    EnrichmentResult,
    EnrichmentStatus,
    RateLimitObservation,
    canonical_response_sha256,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class StoredEnrichment:
    id: str
    observation_sha256: str
    result: EnrichmentResult
    created_at: datetime


class MetadataObservationRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def record(self, result: EnrichmentResult) -> StoredEnrichment:
        with self._sessions() as session:
            work = self._canonical_work(session, result.work_id)
            canonical_result = (
                result if work.id == result.work_id else replace(result, work_id=work.id)
            )
            fingerprint = self._fingerprint(canonical_result)
            existing = session.scalar(
                select(MetadataObservationRecord).where(
                    MetadataObservationRecord.work_id == work.id,
                    MetadataObservationRecord.enricher_id == canonical_result.enricher_id,
                    MetadataObservationRecord.observation_sha256 == fingerprint,
                )
            )
            if existing is not None:
                return self._domain(existing)
            record = MetadataObservationRecord(
                work_id=work.id,
                enricher_id=canonical_result.enricher_id,
                enricher_version=canonical_result.enricher_version,
                status=canonical_result.status.value,
                external_id=canonical_result.external_id,
                matched_by=canonical_result.matched_by,
                fields=dict(canonical_result.fields),
                field_provenance=dict(canonical_result.field_provenance),
                warnings=list(canonical_result.warnings),
                response_sha256=canonical_result.response_sha256,
                observation_sha256=fingerprint,
                rate_limit=(
                    asdict(canonical_result.rate_limit)
                    if canonical_result.rate_limit is not None
                    else None
                ),
                fetched_at=self._utc(canonical_result.fetched_at),
                created_at=utc_now(),
            )
            session.add(record)
            session.commit()
            return self._domain(record)

    def list_history(
        self,
        work_id: str,
        *,
        enricher_id: str | None = None,
    ) -> tuple[StoredEnrichment, ...]:
        with self._sessions() as session:
            query = select(MetadataObservationRecord).where(
                MetadataObservationRecord.work_id == work_id
            )
            if enricher_id is not None:
                query = query.where(MetadataObservationRecord.enricher_id == enricher_id)
            records = session.scalars(
                query.order_by(
                    MetadataObservationRecord.fetched_at.desc(),
                    MetadataObservationRecord.id.desc(),
                )
            )
            return tuple(self._domain(record) for record in records)

    def latest_by_enricher(self, work_id: str) -> dict[str, StoredEnrichment]:
        latest: dict[str, StoredEnrichment] = {}
        for observation in self.list_history(work_id):
            latest.setdefault(observation.result.enricher_id, observation)
        return latest

    def latest_by_enricher_many(
        self,
        work_ids: Sequence[str],
    ) -> dict[str, dict[str, StoredEnrichment]]:
        unique_ids = tuple(dict.fromkeys(work_ids))
        if not unique_ids:
            return {}
        latest: dict[str, dict[str, StoredEnrichment]] = {
            work_id: {} for work_id in unique_ids
        }
        with self._sessions() as session:
            records = session.scalars(
                select(MetadataObservationRecord)
                .where(MetadataObservationRecord.work_id.in_(unique_ids))
                .order_by(
                    MetadataObservationRecord.work_id,
                    MetadataObservationRecord.fetched_at.desc(),
                    MetadataObservationRecord.id.desc(),
                )
            )
            for record in records:
                latest[record.work_id].setdefault(
                    record.enricher_id,
                    self._domain(record),
                )
        return latest

    def get_many(self, observation_ids: Sequence[str]) -> tuple[StoredEnrichment, ...]:
        unique_ids = tuple(dict.fromkeys(observation_ids))
        if len(unique_ids) != len(tuple(observation_ids)):
            raise ValueError("observation IDs must be unique")
        if not unique_ids:
            return ()
        with self._sessions() as session:
            records = {
                record.id: record
                for record in session.scalars(
                    select(MetadataObservationRecord).where(
                        MetadataObservationRecord.id.in_(unique_ids)
                    )
                )
            }
            missing = set(unique_ids) - set(records)
            if missing:
                raise KeyError(sorted(missing)[0])
            return tuple(self._domain(records[observation_id]) for observation_id in unique_ids)

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

    @classmethod
    def _fingerprint(cls, result: EnrichmentResult) -> str:
        return canonical_response_sha256(
            {
                "enricher_id": result.enricher_id,
                "enricher_version": result.enricher_version,
                "status": result.status.value,
                "external_id": result.external_id,
                "matched_by": result.matched_by,
                "fields": dict(result.fields),
                "field_provenance": dict(result.field_provenance),
                "warnings": list(result.warnings),
                "fetched_at": cls._utc(result.fetched_at).isoformat(),
                "response_sha256": result.response_sha256,
                "rate_limit": asdict(result.rate_limit) if result.rate_limit is not None else None,
            }
        )

    @classmethod
    def _domain(cls, record: MetadataObservationRecord) -> StoredEnrichment:
        rate_limit = (
            RateLimitObservation(**record.rate_limit)
            if record.rate_limit is not None
            else None
        )
        return StoredEnrichment(
            id=record.id,
            observation_sha256=record.observation_sha256,
            result=EnrichmentResult(
                work_id=record.work_id,
                enricher_id=record.enricher_id,
                enricher_version=record.enricher_version,
                status=EnrichmentStatus(record.status),
                external_id=record.external_id,
                matched_by=record.matched_by,
                fields=dict(record.fields),
                field_provenance=dict(record.field_provenance),
                warnings=tuple(record.warnings),
                fetched_at=cls._utc(record.fetched_at),
                response_sha256=record.response_sha256,
                rate_limit=rate_limit,
            ),
            created_at=cls._utc(record.created_at),
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
