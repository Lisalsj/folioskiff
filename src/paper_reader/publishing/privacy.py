"""Server-side publication whitelist and deterministic preview fingerprints."""

from __future__ import annotations

import hashlib
import json
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.agents.models import AnalysisReportRecord
from paper_reader.agents.report_schema import (
    EvidenceReference,
    ReportStatement,
    StructuredAnalysisReport,
)
from paper_reader.discovery.models import ScoreSnapshotRecord, WorkRecord
from paper_reader.publishing.models import (
    PublicAnalysisReport,
    PublicationPayload,
    PublicationPreview,
    PublicationSelection,
    PublicEvidenceReference,
    PublicQualityScore,
    PublicReportContent,
    PublicReportStatement,
    PublicWork,
)


class InvalidPublicationSelection(ValueError):
    """The requested public selection is missing or crosses a Work boundary."""


class DuplicateCanonicalWorkError(InvalidPublicationSelection):
    """Two requested identities resolve to the same canonical Work."""


@dataclass(frozen=True, slots=True)
class _ReportView:
    id: str
    work_id: str
    version: int
    evidence_level: str
    language: str
    structured_report: dict[str, Any]
    schema_version: str
    report_sha256: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _QualityView:
    id: str
    score: float
    coverage: float
    confidence: float
    rule_version: str
    snapshot_sha256: str


class PublishingPrivacyBoundary:
    """Build a public payload without reading any private-content table."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        transaction_session: Session | None = None,
    ) -> None:
        self._sessions = sessions
        self._transaction_session = transaction_session

    def preview(self, selection: PublicationSelection) -> PublicationPreview:
        if not isinstance(selection, PublicationSelection):
            raise TypeError("selection must be PublicationSelection")
        context: AbstractContextManager[Session]
        if self._transaction_session is None:
            context = self._sessions()
        else:
            context = nullcontext(self._transaction_session)
        with context as session:
            work_map = {record.id: record for record in session.scalars(select(WorkRecord))}
            public_works: list[PublicWork] = []
            dependencies: list[dict[str, Any]] = []
            seen: set[str] = set()
            for requested in selection.works:
                canonical = self._resolve_canonical(work_map, requested.work_id)
                if canonical.id in seen:
                    raise DuplicateCanonicalWorkError(
                        "multiple selections resolve to the same canonical Work"
                    )
                seen.add(canonical.id)
                lineage = tuple(
                    work_id
                    for work_id in work_map
                    if self._resolve_canonical(work_map, work_id).id == canonical.id
                )
                selected_report = self._selected_report(
                    session,
                    work_map=work_map,
                    lineage=lineage,
                    canonical_id=canonical.id,
                    report_id=requested.report_id,
                )
                latest_report = session.execute(
                    select(
                        AnalysisReportRecord.id,
                        AnalysisReportRecord.version,
                        AnalysisReportRecord.report_sha256,
                    )
                    .where(AnalysisReportRecord.work_id.in_(lineage))
                    .order_by(
                        AnalysisReportRecord.created_at.desc(),
                        AnalysisReportRecord.id.desc(),
                    )
                    .limit(1)
                ).one_or_none()
                quality_row = session.execute(
                    select(
                        ScoreSnapshotRecord.id,
                        ScoreSnapshotRecord.score,
                        ScoreSnapshotRecord.coverage,
                        ScoreSnapshotRecord.confidence,
                        ScoreSnapshotRecord.rule_version,
                        ScoreSnapshotRecord.snapshot_sha256,
                    )
                    .where(
                        ScoreSnapshotRecord.work_id.in_(lineage),
                        ScoreSnapshotRecord.score_type == "quality",
                    )
                    .order_by(
                        ScoreSnapshotRecord.created_at.desc(),
                        ScoreSnapshotRecord.id.desc(),
                    )
                    .limit(1)
                ).one_or_none()
                quality = (
                    None
                    if quality_row is None
                    else _QualityView(
                        id=quality_row.id,
                        score=quality_row.score,
                        coverage=quality_row.coverage,
                        confidence=quality_row.confidence,
                        rule_version=quality_row.rule_version,
                        snapshot_sha256=quality_row.snapshot_sha256,
                    )
                )
                public_works.append(
                    PublicWork(
                        canonical_work_id=canonical.id,
                        title=canonical.canonical_title,
                        authors=tuple(canonical.authors),
                        abstract=canonical.abstract,
                        doi=canonical.doi,
                        published_at=_utc_text(canonical.published_at),
                        quality_score=self._public_quality(quality),
                        report=self._public_report(selected_report),
                    )
                )
                dependencies.append(
                    {
                        "canonical_work_id": canonical.id,
                        "work_updated_at": _utc_text(canonical.updated_at),
                        "latest_formal_report": (
                            None
                            if latest_report is None
                            else {
                                "id": latest_report.id,
                                "version": latest_report.version,
                                "report_sha256": latest_report.report_sha256,
                            }
                        ),
                        "quality_snapshot": (
                            None
                            if quality is None
                            else {
                                "id": quality.id,
                                "snapshot_sha256": quality.snapshot_sha256,
                            }
                        ),
                    }
                )

        payload = PublicationPayload(
            slug=selection.slug,
            title=selection.title,
            public_intro=selection.public_intro,
            works=tuple(public_works),
        )
        fingerprint = _sha256_json(
            {
                "preview_schema": "paper-reader-publication-preview-v1",
                "selection": selection.model_dump(mode="json"),
                "payload": payload.model_dump(mode="json"),
                "dependencies": dependencies,
            }
        )
        return PublicationPreview(
            selection=selection,
            payload=payload,
            fingerprint=fingerprint,
        )

    @staticmethod
    def _resolve_canonical(work_map: dict[str, WorkRecord], work_id: str) -> WorkRecord:
        current = work_map.get(work_id)
        if current is None:
            raise InvalidPublicationSelection(f"Work {work_id!r} does not exist")
        visited: set[str] = set()
        while current.status == "merged":
            if current.id in visited or current.merged_into_work_id is None:
                raise InvalidPublicationSelection("canonical Work merge chain is invalid")
            visited.add(current.id)
            target = work_map.get(current.merged_into_work_id)
            if target is None:
                raise InvalidPublicationSelection("canonical Work merge target is missing")
            current = target
        if current.status != "active":
            raise InvalidPublicationSelection("canonical Work is not active")
        return current

    def _selected_report(
        self,
        session: Session,
        *,
        work_map: dict[str, WorkRecord],
        lineage: tuple[str, ...],
        canonical_id: str,
        report_id: str | None,
    ) -> _ReportView | None:
        if report_id is None:
            return None
        row = session.execute(
            select(
                AnalysisReportRecord.id,
                AnalysisReportRecord.work_id,
                AnalysisReportRecord.version,
                AnalysisReportRecord.evidence_level,
                AnalysisReportRecord.language,
                AnalysisReportRecord.structured_report,
                AnalysisReportRecord.schema_version,
                AnalysisReportRecord.report_sha256,
                AnalysisReportRecord.created_at,
            ).where(AnalysisReportRecord.id == report_id)
        ).one_or_none()
        if row is None:
            raise InvalidPublicationSelection(
                "only an explicitly selected formal report can be published"
            )
        if row.work_id not in lineage or (
            self._resolve_canonical(work_map, row.work_id).id != canonical_id
        ):
            raise InvalidPublicationSelection(
                "the formal report must belong to the same canonical Work"
            )
        return _ReportView(
            id=row.id,
            work_id=row.work_id,
            version=row.version,
            evidence_level=row.evidence_level,
            language=row.language,
            structured_report=dict(row.structured_report),
            schema_version=row.schema_version,
            report_sha256=row.report_sha256,
            created_at=row.created_at,
        )

    @staticmethod
    def _public_quality(record: _QualityView | None) -> PublicQualityScore | None:
        if record is None:
            return None
        return PublicQualityScore(
            snapshot_id=record.id,
            score=record.score,
            coverage=record.coverage,
            confidence=record.confidence,
            rule_version=record.rule_version,
            snapshot_sha256=record.snapshot_sha256,
        )

    @classmethod
    def _public_report(
        cls,
        record: _ReportView | None,
    ) -> PublicAnalysisReport | None:
        if record is None:
            return None
        structured = StructuredAnalysisReport.model_validate(record.structured_report)

        def statement(value: ReportStatement) -> PublicReportStatement:
            return PublicReportStatement(
                text=value.text,
                references=tuple(cls._public_reference(item) for item in value.references),
            )

        content = PublicReportContent(
            title=structured.title,
            overview=statement(structured.overview),
            research_question=statement(structured.research_question),
            author_claims=tuple(statement(item) for item in structured.author_claims),
            contributions=tuple(statement(item) for item in structured.contributions),
            method=tuple(statement(item) for item in structured.method),
            assumptions=tuple(statement(item) for item in structured.assumptions),
            data_and_experiments=tuple(
                statement(item) for item in structured.data_and_experiments
            ),
            findings=tuple(statement(item) for item in structured.findings),
            limitations=tuple(statement(item) for item in structured.limitations),
            reproducibility=tuple(statement(item) for item in structured.reproducibility),
            relation_to_user_intent=tuple(
                statement(item) for item in structured.relation_to_user_intent
            ),
            agent_judgments=tuple(statement(item) for item in structured.agent_judgments),
        )
        return PublicAnalysisReport(
            id=record.id,
            version=record.version,
            evidence_level=cast(
                Literal["full_text", "abstract_only"],
                record.evidence_level,
            ),
            language=cast(Literal["zh-CN", "en", "bilingual"], record.language),
            schema_version=record.schema_version,
            report_sha256=record.report_sha256,
            created_at=_utc_text(record.created_at) or "",
            content=content,
        )

    @staticmethod
    def _public_reference(reference: EvidenceReference) -> PublicEvidenceReference:
        return PublicEvidenceReference(
            kind=reference.kind,
            page_number=reference.page_number,
            locator=reference.locator,
            url=reference.url,
            external_title=reference.external_title,
        )


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat().replace("+00:00", "Z")
