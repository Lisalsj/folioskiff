"""Append-only persistence for reproducible formal reports and failure drafts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.agents.contracts import ReasoningEffort, ServiceTier
from paper_reader.agents.evidence import EvidenceBundle, EvidenceLevel, canonical_json
from paper_reader.agents.models import (
    AnalysisFailureDraftRecord,
    AnalysisReportFragmentRecord,
    AnalysisReportRecord,
    PromptTemplateVersionRecord,
)
from paper_reader.agents.process_policy import redact_diagnostic
from paper_reader.agents.report_schema import (
    StructuredAnalysisReport,
    external_citations,
    render_report_markdown,
    validate_report_evidence,
)
from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.jobs.models import JobRecord, utc_now

ReportLanguage = Literal["zh-CN", "en", "bilingual"]
NetworkPolicy = Literal["local_evidence_only", "external_research"]
_REPORT_SCHEMA_VERSION = "analysis-report-v1"
_LANGUAGES = frozenset({"zh-CN", "en", "bilingual"})
_FRAGMENT_KINDS = frozenset({"text", "reasoning", "diagnostic"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PROMPT_CHARACTERS = 2_000_000
_MAX_FRAGMENT_CHARACTERS = 250_000
_MAX_VALIDATION_ERRORS = 100


class ReportConflictError(RuntimeError):
    """An append would contradict an already persisted report fact."""


@dataclass(frozen=True, slots=True)
class AnalysisProvenance:
    job_id: str
    work_id: str
    document_version_id: str | None
    evidence: EvidenceBundle
    backend_id: str
    requested_model_id: str | None
    resolved_model_id: str | None
    reasoning_effort: ReasoningEffort
    service_tier: ServiceTier
    language: ReportLanguage
    template_version_id: str
    complete_prompt: str
    prompt_sha256: str
    external_research: bool
    network_policy: NetworkPolicy
    request_fingerprint: str

    def __post_init__(self) -> None:
        _validate_identifier("job_id", self.job_id, maximum=80)
        _validate_identifier("work_id", self.work_id, maximum=80)
        _validate_identifier("backend_id", self.backend_id, maximum=80)
        _validate_identifier("template_version_id", self.template_version_id, maximum=80)
        _validate_model_id(self.requested_model_id)
        _validate_model_id(self.resolved_model_id)
        object.__setattr__(
            self,
            "reasoning_effort",
            ReasoningEffort(self.reasoning_effort),
        )
        object.__setattr__(self, "service_tier", ServiceTier(self.service_tier))
        if self.language not in _LANGUAGES:
            raise ValueError("report language is not supported")
        expected_policy = (
            "external_research" if self.external_research else "local_evidence_only"
        )
        if self.network_policy != expected_policy:
            raise ValueError("network policy does not match external research")
        if not self.complete_prompt.strip() or len(self.complete_prompt) > (
            _MAX_PROMPT_CHARACTERS
        ):
            raise ValueError("complete prompt is blank or exceeds the maximum size")
        expected_prompt_hash = _sha256_text(self.complete_prompt)
        if self.prompt_sha256 != expected_prompt_hash:
            raise ValueError("prompt SHA-256 does not match the complete prompt")
        expected_evidence_hash = _evidence_sha256(self.evidence)
        if self.evidence.evidence_sha256 != expected_evidence_hash:
            raise ValueError("evidence SHA-256 does not match the evidence manifest")
        if self.evidence.work.work_id != self.work_id:
            raise ValueError("evidence Work does not match report Work")
        document = self.evidence.document
        if self.evidence.evidence_level is EvidenceLevel.FULL_TEXT and document is None:
            raise ValueError("full-text evidence requires a selected document version")
        if document is None:
            if self.document_version_id is not None:
                raise ValueError("document version does not match evidence")
        elif self.document_version_id != document.document_version_id:
            raise ValueError("document version does not match evidence")
        expected_fingerprint = _request_fingerprint(
            work_id=self.work_id,
            document_version_id=self.document_version_id,
            evidence=self.evidence,
            backend_id=self.backend_id,
            requested_model_id=self.requested_model_id,
            reasoning_effort=self.reasoning_effort,
            service_tier=self.service_tier,
            language=self.language,
            template_version_id=self.template_version_id,
            prompt_sha256=self.prompt_sha256,
            network_policy=self.network_policy,
        )
        if self.request_fingerprint != expected_fingerprint:
            raise ValueError("request fingerprint does not match report provenance")

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        work_id: str,
        document_version_id: str | None,
        evidence: EvidenceBundle,
        backend_id: str,
        requested_model_id: str | None,
        resolved_model_id: str | None,
        reasoning_effort: ReasoningEffort,
        service_tier: ServiceTier,
        language: ReportLanguage,
        template_version_id: str,
        complete_prompt: str,
        external_research: bool,
    ) -> AnalysisProvenance:
        prompt_hash = _sha256_text(complete_prompt)
        network_policy: NetworkPolicy = (
            "external_research" if external_research else "local_evidence_only"
        )
        reasoning = ReasoningEffort(reasoning_effort)
        tier = ServiceTier(service_tier)
        values = {
            "job_id": job_id,
            "work_id": work_id,
            "document_version_id": document_version_id,
            "evidence": evidence,
            "backend_id": backend_id,
            "requested_model_id": requested_model_id,
            "resolved_model_id": resolved_model_id,
            "reasoning_effort": reasoning,
            "service_tier": tier,
            "language": language,
            "template_version_id": template_version_id,
            "complete_prompt": complete_prompt,
            "prompt_sha256": prompt_hash,
            "external_research": external_research,
            "network_policy": network_policy,
        }
        fingerprint = _request_fingerprint(
            work_id=work_id,
            document_version_id=document_version_id,
            evidence=evidence,
            backend_id=backend_id,
            requested_model_id=requested_model_id,
            reasoning_effort=reasoning,
            service_tier=tier,
            language=language,
            template_version_id=template_version_id,
            prompt_sha256=prompt_hash,
            network_policy=network_policy,
        )
        return cls(**values, request_fingerprint=fingerprint)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class StoredAnalysisReport:
    id: str
    work_id: str
    version: int
    job_id: str
    document_version_id: str | None
    document_content_sha256: str | None
    evidence_level: str
    evidence_sha256: str
    evidence_manifest: dict[str, Any]
    backend_id: str
    requested_model_id: str | None
    resolved_model_id: str | None
    reasoning_effort: str
    service_tier: str
    language: str
    template_version_id: str
    complete_prompt: str
    prompt_sha256: str
    network_policy: str
    content: StructuredAnalysisReport
    rendered_markdown: str
    external_citations: tuple[dict[str, str], ...]
    schema_version: str
    request_fingerprint: str
    report_sha256: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AnalysisFragment:
    job_id: str
    sequence: int
    kind: str
    text: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AnalysisFailureDraft:
    job_id: str
    work_id: str
    error_category: str
    error_message: str
    raw_response: str
    validation_errors: tuple[str, ...]
    repair_count: int
    request_fingerprint: str
    created_at: datetime


class AnalysisReportRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def save_formal(
        self,
        provenance: AnalysisProvenance,
        report: StructuredAnalysisReport,
    ) -> StoredAnalysisReport:
        if not isinstance(provenance, AnalysisProvenance):
            raise TypeError("provenance must be AnalysisProvenance")
        if not isinstance(report, StructuredAnalysisReport):
            raise TypeError("report must be StructuredAnalysisReport")
        validate_report_evidence(
            report,
            provenance.evidence,
            external_research=provenance.external_research,
        )
        report_payload = report.model_dump(mode="json")
        report_hash = sha256(canonical_json(report_payload).encode("utf-8")).hexdigest()
        markdown = render_report_markdown(report)
        citations = external_citations(report)
        evidence_manifest = provenance.evidence.model_dump(mode="json")
        document = provenance.evidence.document

        with self._sessions() as session:
            self._require_work_and_job(session, provenance.work_id, provenance.job_id)
            if session.get(PromptTemplateVersionRecord, provenance.template_version_id) is None:
                raise KeyError(provenance.template_version_id)
            self._validate_document(session, provenance)
            if session.get(AnalysisFailureDraftRecord, provenance.job_id) is not None:
                raise ReportConflictError(
                    "A failure draft already exists for this report Job"
                )
            existing = session.scalar(
                select(AnalysisReportRecord).where(
                    AnalysisReportRecord.job_id == provenance.job_id
                )
            )
            if existing is not None:
                if self._formal_matches(existing, provenance, report_hash):
                    return self._report_domain(existing)
                raise ReportConflictError(
                    "This Job already produced a different formal report"
                )
            highest = session.scalar(
                select(func.max(AnalysisReportRecord.version)).where(
                    AnalysisReportRecord.work_id == provenance.work_id
                )
            )
            record = AnalysisReportRecord(
                work_id=provenance.work_id,
                version=(highest or 0) + 1,
                job_id=provenance.job_id,
                document_version_id=provenance.document_version_id,
                document_content_sha256=(
                    document.content_sha256 if document is not None else None
                ),
                evidence_level=provenance.evidence.evidence_level.value,
                evidence_sha256=provenance.evidence.evidence_sha256,
                evidence_manifest=evidence_manifest,
                backend_id=provenance.backend_id,
                requested_model_id=provenance.requested_model_id,
                resolved_model_id=provenance.resolved_model_id,
                reasoning_effort=provenance.reasoning_effort.value,
                service_tier=provenance.service_tier.value,
                language=provenance.language,
                template_version_id=provenance.template_version_id,
                complete_prompt=provenance.complete_prompt,
                prompt_sha256=provenance.prompt_sha256,
                network_policy=provenance.network_policy,
                structured_report=report_payload,
                rendered_markdown=markdown,
                external_citations=list(citations),
                schema_version=_REPORT_SCHEMA_VERSION,
                request_fingerprint=provenance.request_fingerprint,
                report_sha256=report_hash,
                created_at=utc_now(),
            )
            session.add(record)
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                concurrent = session.scalar(
                    select(AnalysisReportRecord).where(
                        AnalysisReportRecord.job_id == provenance.job_id
                    )
                )
                if concurrent is not None and self._formal_matches(
                    concurrent, provenance, report_hash
                ):
                    return self._report_domain(concurrent)
                raise ReportConflictError(
                    "A concurrent report version conflicted with this append"
                ) from error
            session.refresh(record)
            return self._report_domain(record)

    def append_fragment(
        self,
        *,
        job_id: str,
        sequence: int,
        kind: str,
        text: str,
    ) -> AnalysisFragment:
        _validate_identifier("job_id", job_id, maximum=80)
        if sequence < 0:
            raise ValueError("fragment sequence cannot be negative")
        if kind not in _FRAGMENT_KINDS:
            raise ValueError("fragment kind is not supported")
        if not text or len(text) > _MAX_FRAGMENT_CHARACTERS:
            raise ValueError("fragment text is blank or exceeds the maximum size")
        with self._sessions() as session:
            self._require_report_job(session, job_id)
            existing = session.get(AnalysisReportFragmentRecord, (job_id, sequence))
            if existing is not None:
                if existing.kind == kind and existing.text == text:
                    return self._fragment_domain(existing)
                raise ReportConflictError(
                    "The sequence is already bound to a different fragment"
                )
            if self._has_outcome(session, job_id):
                raise ReportConflictError("The report Job is already finished")
            highest = session.scalar(
                select(func.max(AnalysisReportFragmentRecord.sequence)).where(
                    AnalysisReportFragmentRecord.job_id == job_id
                )
            )
            expected = 0 if highest is None else highest + 1
            if sequence != expected:
                raise ReportConflictError(
                    f"Fragment must use the next sequence number {expected}"
                )
            record = AnalysisReportFragmentRecord(
                job_id=job_id,
                sequence=sequence,
                kind=kind,
                text=text,
                created_at=utc_now(),
            )
            session.add(record)
            session.commit()
            return self._fragment_domain(record)

    def list_fragments(self, job_id: str) -> tuple[AnalysisFragment, ...]:
        with self._sessions() as session:
            records = session.scalars(
                select(AnalysisReportFragmentRecord)
                .where(AnalysisReportFragmentRecord.job_id == job_id)
                .order_by(AnalysisReportFragmentRecord.sequence)
            )
            return tuple(self._fragment_domain(record) for record in records)

    def save_failure(
        self,
        *,
        job_id: str,
        work_id: str,
        request_fingerprint: str,
        error_category: str,
        error_message: str,
        raw_response: str,
        validation_errors: tuple[str, ...],
        repair_count: int,
    ) -> AnalysisFailureDraft:
        _validate_identifier("job_id", job_id, maximum=80)
        _validate_identifier("work_id", work_id, maximum=80)
        _validate_hash("request_fingerprint", request_fingerprint)
        category = error_category.strip()
        if not category or len(category) > 80 or _CONTROL.search(category):
            raise ValueError("error_category is invalid")
        if repair_count not in {0, 1}:
            raise ValueError("repair_count must be 0 or 1")
        safe_message = redact_diagnostic(error_message, limit=4_000)
        safe_raw = redact_diagnostic(raw_response, limit=100_000)
        safe_errors = tuple(
            redact_diagnostic(str(value), limit=2_000)
            for value in validation_errors[:_MAX_VALIDATION_ERRORS]
        )
        with self._sessions() as session:
            self._require_work_and_job(session, work_id, job_id)
            formal = session.scalar(
                select(AnalysisReportRecord).where(AnalysisReportRecord.job_id == job_id)
            )
            if formal is not None:
                raise ReportConflictError(
                    "A formal report already exists for this report Job"
                )
            existing = session.get(AnalysisFailureDraftRecord, job_id)
            if existing is not None:
                if (
                    existing.work_id == work_id
                    and existing.request_fingerprint == request_fingerprint
                    and existing.error_category == category
                    and existing.error_message == safe_message
                    and existing.raw_response == safe_raw
                    and tuple(existing.validation_errors) == safe_errors
                    and existing.repair_count == repair_count
                ):
                    return self._failure_domain(existing)
                raise ReportConflictError(
                    "This Job already has a different failure draft"
                )
            record = AnalysisFailureDraftRecord(
                job_id=job_id,
                work_id=work_id,
                error_category=category,
                error_message=safe_message,
                raw_response=safe_raw,
                validation_errors=list(safe_errors),
                repair_count=repair_count,
                request_fingerprint=request_fingerprint,
                created_at=utc_now(),
            )
            session.add(record)
            session.commit()
            return self._failure_domain(record)

    def list_for_work(self, work_id: str) -> tuple[StoredAnalysisReport, ...]:
        with self._sessions() as session:
            records = session.scalars(
                select(AnalysisReportRecord)
                .where(AnalysisReportRecord.work_id == work_id)
                .order_by(
                    AnalysisReportRecord.version.desc(),
                    AnalysisReportRecord.id.desc(),
                )
            )
            return tuple(self._report_domain(record) for record in records)

    def get(self, report_id: str) -> StoredAnalysisReport:
        with self._sessions() as session:
            record = session.get(AnalysisReportRecord, report_id)
            if record is None:
                raise KeyError(report_id)
            return self._report_domain(record)

    def failure_for_job(self, job_id: str) -> AnalysisFailureDraft | None:
        with self._sessions() as session:
            record = session.get(AnalysisFailureDraftRecord, job_id)
            return self._failure_domain(record) if record is not None else None

    def report_for_job(self, job_id: str) -> StoredAnalysisReport | None:
        with self._sessions() as session:
            record = session.scalar(
                select(AnalysisReportRecord).where(AnalysisReportRecord.job_id == job_id)
            )
            return self._report_domain(record) if record is not None else None

    def fragment(self, job_id: str, sequence: int) -> AnalysisFragment | None:
        with self._sessions() as session:
            record = session.get(AnalysisReportFragmentRecord, (job_id, sequence))
            return self._fragment_domain(record) if record is not None else None

    @staticmethod
    def _require_report_job(session: Session, job_id: str) -> JobRecord:
        job = session.get(JobRecord, job_id)
        if job is None:
            raise KeyError(job_id)
        if job.kind != "analysis.report":
            raise ValueError("Job is not an analysis.report Job")
        return job

    @classmethod
    def _require_work_and_job(
        cls,
        session: Session,
        work_id: str,
        job_id: str,
    ) -> None:
        if session.get(WorkRecord, work_id) is None:
            raise KeyError(work_id)
        cls._require_report_job(session, job_id)

    @staticmethod
    def _validate_document(session: Session, provenance: AnalysisProvenance) -> None:
        document = provenance.evidence.document
        if document is None:
            return
        record = session.get(DocumentVersionRecord, document.document_version_id)
        if record is None:
            raise KeyError(document.document_version_id)
        if record.work_id != provenance.work_id:
            raise ValueError("selected document does not belong to report Work")
        if record.content_sha256 != document.content_sha256:
            raise ValueError("selected document content hash has changed")

    @staticmethod
    def _has_outcome(session: Session, job_id: str) -> bool:
        return (
            session.scalar(
                select(AnalysisReportRecord.id).where(
                    AnalysisReportRecord.job_id == job_id
                )
            )
            is not None
            or session.get(AnalysisFailureDraftRecord, job_id) is not None
        )

    @staticmethod
    def _formal_matches(
        record: AnalysisReportRecord,
        provenance: AnalysisProvenance,
        report_hash: str,
    ) -> bool:
        document = provenance.evidence.document
        return (
            record.work_id == provenance.work_id
            and record.document_version_id == provenance.document_version_id
            and record.document_content_sha256
            == (document.content_sha256 if document is not None else None)
            and record.evidence_sha256 == provenance.evidence.evidence_sha256
            and record.backend_id == provenance.backend_id
            and record.requested_model_id == provenance.requested_model_id
            and record.resolved_model_id == provenance.resolved_model_id
            and record.reasoning_effort == provenance.reasoning_effort.value
            and record.service_tier == provenance.service_tier.value
            and record.language == provenance.language
            and record.template_version_id == provenance.template_version_id
            and record.complete_prompt == provenance.complete_prompt
            and record.prompt_sha256 == provenance.prompt_sha256
            and record.network_policy == provenance.network_policy
            and record.request_fingerprint == provenance.request_fingerprint
            and record.report_sha256 == report_hash
        )

    @staticmethod
    def _report_domain(record: AnalysisReportRecord) -> StoredAnalysisReport:
        return StoredAnalysisReport(
            id=record.id,
            work_id=record.work_id,
            version=record.version,
            job_id=record.job_id,
            document_version_id=record.document_version_id,
            document_content_sha256=record.document_content_sha256,
            evidence_level=record.evidence_level,
            evidence_sha256=record.evidence_sha256,
            evidence_manifest=_json_copy(record.evidence_manifest),
            backend_id=record.backend_id,
            requested_model_id=record.requested_model_id,
            resolved_model_id=record.resolved_model_id,
            reasoning_effort=record.reasoning_effort,
            service_tier=record.service_tier,
            language=record.language,
            template_version_id=record.template_version_id,
            complete_prompt=record.complete_prompt,
            prompt_sha256=record.prompt_sha256,
            network_policy=record.network_policy,
            content=StructuredAnalysisReport.model_validate(record.structured_report),
            rendered_markdown=record.rendered_markdown,
            external_citations=tuple(
                dict(item) for item in record.external_citations
            ),
            schema_version=record.schema_version,
            request_fingerprint=record.request_fingerprint,
            report_sha256=record.report_sha256,
            created_at=_as_utc(record.created_at),
        )

    @staticmethod
    def _fragment_domain(record: AnalysisReportFragmentRecord) -> AnalysisFragment:
        return AnalysisFragment(
            job_id=record.job_id,
            sequence=record.sequence,
            kind=record.kind,
            text=record.text,
            created_at=_as_utc(record.created_at),
        )

    @staticmethod
    def _failure_domain(record: AnalysisFailureDraftRecord) -> AnalysisFailureDraft:
        return AnalysisFailureDraft(
            job_id=record.job_id,
            work_id=record.work_id,
            error_category=record.error_category,
            error_message=record.error_message,
            raw_response=record.raw_response,
            validation_errors=tuple(record.validation_errors),
            repair_count=record.repair_count,
            request_fingerprint=record.request_fingerprint,
            created_at=_as_utc(record.created_at),
        )


def _request_fingerprint(
    *,
    work_id: str,
    document_version_id: str | None,
    evidence: EvidenceBundle,
    backend_id: str,
    requested_model_id: str | None,
    reasoning_effort: ReasoningEffort,
    service_tier: ServiceTier,
    language: ReportLanguage,
    template_version_id: str,
    prompt_sha256: str,
    network_policy: NetworkPolicy,
) -> str:
    document = evidence.document
    payload = {
        "work_id": work_id,
        "document_version_id": document_version_id,
        "document_content_sha256": (
            document.content_sha256 if document is not None else None
        ),
        "evidence_level": evidence.evidence_level.value,
        "evidence_sha256": evidence.evidence_sha256,
        "backend_id": backend_id,
        "requested_model_id": requested_model_id,
        "reasoning_effort": reasoning_effort.value,
        "service_tier": service_tier.value,
        "language": language,
        "template_version_id": template_version_id,
        "prompt_sha256": prompt_sha256,
        "network_policy": network_policy,
    }
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def analysis_request_fingerprint(
    *,
    work_id: str,
    document_version_id: str | None,
    evidence: EvidenceBundle,
    backend_id: str,
    requested_model_id: str | None,
    reasoning_effort: ReasoningEffort,
    service_tier: ServiceTier,
    language: ReportLanguage,
    template_version_id: str,
    complete_prompt: str,
    external_research: bool,
) -> str:
    """Compute the stable preflight identity shared with formal provenance."""

    return _request_fingerprint(
        work_id=work_id,
        document_version_id=document_version_id,
        evidence=evidence,
        backend_id=backend_id,
        requested_model_id=requested_model_id,
        reasoning_effort=ReasoningEffort(reasoning_effort),
        service_tier=ServiceTier(service_tier),
        language=language,
        template_version_id=template_version_id,
        prompt_sha256=_sha256_text(complete_prompt),
        network_policy=(
            "external_research" if external_research else "local_evidence_only"
        ),
    )


def _evidence_sha256(evidence: EvidenceBundle) -> str:
    payload = evidence.model_dump(mode="json", exclude={"evidence_sha256"})
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _validate_identifier(name: str, value: str, *, maximum: int) -> None:
    if not value.strip() or len(value) > maximum or _CONTROL.search(value):
        raise ValueError(f"{name} is invalid")


def _validate_model_id(value: str | None) -> None:
    if value is not None and (
        not value.strip() or len(value) > 200 or _CONTROL.search(value)
    ):
        raise ValueError("Agent model ID is invalid")


def _validate_hash(name: str, value: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256")


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_copy(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))  # type: ignore[no-any-return]
