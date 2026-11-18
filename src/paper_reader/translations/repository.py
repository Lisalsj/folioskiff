"""Transactional persistence for versioned paper translation artifacts."""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal, cast
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.agents.evidence import canonical_json
from paper_reader.agents.process_policy import redact_diagnostic
from paper_reader.assistant.contracts import AssistantTaskStatus
from paper_reader.assistant.models import (
    AssistantAgentRunRecord,
    AssistantContextSnapshotRecord,
    AssistantSessionRecord,
    AssistantTaskRecord,
)
from paper_reader.assistant.repository import ContextSnapshotDraft
from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.jobs.models import JobRecord, utc_now
from paper_reader.translations.contracts import (
    SUPPORTED_LANGUAGES,
    TranslationArtifact,
    TranslationCommand,
    TranslationEvidenceLevel,
    TranslationLanguage,
    TranslationScope,
    TranslationStatus,
    normalize_source_term,
)
from paper_reader.translations.glossary import (
    GlossaryOrigin,
    GlossaryTerm,
    GlossaryTermUpdate,
)
from paper_reader.translations.models import (
    TranslationArtifactRecord,
    TranslationExportRecord,
    TranslationSegmentRecord,
    TranslationTermRecord,
)
from paper_reader.translations.segmenter import (
    MAX_SEGMENTS,
    ProtectedSpan,
    ProtectedSpanError,
    ProtectedText,
    SourceSegment,
    protect_spans,
)

_HASH = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"⟦PR:(?P<index>[0-9]{6}):(?P<digest>[0-9a-f]{8})⟧")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_TEXT_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MUTABLE_STATUSES = frozenset({TranslationStatus.DRAFT.value, TranslationStatus.RUNNING.value})
_TRANSLATION_JOB_KIND = "translation.generate"
_CONTEXT_SCOPE = {
    "selection": TranslationScope.SELECTION,
    "current_page": TranslationScope.PAGE,
    "current_section": TranslationScope.SECTION,
    "full_document": TranslationScope.FULL_DOCUMENT,
}


class TranslationRepositoryError(RuntimeError):
    """Base error for durable translation state."""


class TranslationConflictError(TranslationRepositoryError):
    """An immutable or idempotent translation identity conflicts."""


class TranslationIdentityError(TranslationConflictError):
    """The Work, document, snapshot, task, or Job chain is inconsistent."""


class TranslationStateError(TranslationConflictError):
    """A lifecycle transition would rewrite a completed artifact."""


@dataclass(frozen=True, slots=True)
class TranslationArtifactDraft:
    work_id: str
    document_version_id: str
    document_content_sha256: str
    context_snapshot_id: str
    task_id: str
    job_id: str
    source_language: TranslationLanguage
    target_language: TranslationLanguage
    scope: TranslationScope
    evidence_level: TranslationEvidenceLevel
    prompt_version: str

    def __post_init__(self) -> None:
        for name in (
            "work_id",
            "document_version_id",
            "context_snapshot_id",
            "task_id",
            "job_id",
        ):
            object.__setattr__(
                self,
                name,
                _identifier(getattr(self, name), name=name, maximum=80),
            )
        document_hash = _hash(
            self.document_content_sha256,
            name="document_content_sha256",
        )
        prompt_version = _identifier(
            self.prompt_version,
            name="prompt_version",
            maximum=80,
        )
        if (
            not isinstance(self.source_language, str)
            or self.source_language not in SUPPORTED_LANGUAGES
        ):
            raise ValueError("source_language is unsupported")
        if (
            not isinstance(self.target_language, str)
            or self.target_language not in SUPPORTED_LANGUAGES
        ):
            raise ValueError("target_language is unsupported")
        if self.source_language == self.target_language:
            raise ValueError("source and target languages must be different")
        if not isinstance(self.scope, TranslationScope):
            raise ValueError("scope must be a TranslationScope")
        if not isinstance(self.evidence_level, str) or self.evidence_level not in {
            "native_text",
            "ocr_text",
            "mixed_text",
        }:
            raise ValueError("evidence_level is unsupported")
        object.__setattr__(self, "document_content_sha256", document_hash)
        object.__setattr__(self, "prompt_version", prompt_version)


@dataclass(frozen=True, slots=True)
class StoredTranslationSegment:
    record_id: str
    artifact_id: str
    segment_id: str
    segment_index: int
    page_number: int
    block_numbers: tuple[int, ...]
    source_text: str
    source_sha256: str
    protected_text: str
    protected_map: dict[str, dict[str, str]]
    translated_text: str | None
    source_kind: Literal["native", "ocr"]
    ocr_confidence: float | None
    status: str
    diagnostic: str | None
    created_at: datetime
    updated_at: datetime

    def protected(self) -> ProtectedText:
        """Rebuild validated placeholder metadata without trusting database order."""

        ordered = sorted(
            self.protected_map.items(),
            key=lambda item: int(_TOKEN.fullmatch(item[0]).group("index")),  # type: ignore[union-attr]
        )
        return ProtectedText(
            text=self.protected_text,
            spans=tuple(
                ProtectedSpan(
                    token=token,
                    literal=value["literal"],
                    literal_sha256=value["sha256"],
                    kind=value["kind"],
                    start=index,
                    end=index + 1,
                )
                for index, (token, value) in enumerate(ordered)
            ),
        )


@dataclass(frozen=True, slots=True)
class StoredTranslationTerm:
    id: str
    artifact_id: str
    normalized_source_term: str
    source_term: str
    target_term: str | None
    origin: GlossaryOrigin
    locked: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredTranslationExport:
    id: str
    artifact_id: str
    format: str
    content_sha256: str
    relative_path: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TranslationExportMetadata:
    title: str
    authors: tuple[str, ...]
    backend_id: str
    requested_model_id: str | None
    resolved_model_id: str | None
    reasoning_effort: str
    service_tier: str


@dataclass(frozen=True, slots=True)
class TranslationWorkMetadata:
    work_id: str
    title: str
    abstract: str | None


class TranslationRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create(
        self,
        draft: TranslationArtifactDraft,
        segments: Sequence[SourceSegment],
        *,
        terms: Sequence[GlossaryTerm] = (),
    ) -> TranslationArtifact:
        if not isinstance(draft, TranslationArtifactDraft):
            raise ValueError("draft must be a TranslationArtifactDraft")
        source_segments = _validated_segments(segments)
        initial_terms = _validated_terms(terms, generated_only=False)
        with self._sessions() as session:
            existing = tuple(
                session.scalars(
                    select(TranslationArtifactRecord).where(
                        or_(
                            TranslationArtifactRecord.task_id == draft.task_id,
                            TranslationArtifactRecord.job_id == draft.job_id,
                        )
                    )
                )
            )
            if existing:
                if len(existing) != 1:
                    raise TranslationConflictError(
                        "translation task and Job resolve to different artifacts"
                    )
                record = existing[0]
                _require_artifact_match(record, draft)
                _require_segment_match(session, record.id, source_segments)
                return _artifact_domain(record)

            snapshot = _validate_identity(session, draft, source_segments)
            version = (
                session.scalar(
                    select(func.max(TranslationArtifactRecord.version)).where(
                        TranslationArtifactRecord.work_id == draft.work_id
                    )
                )
                or 0
            ) + 1
            artifact = TranslationArtifactRecord(
                id=str(uuid4()),
                work_id=draft.work_id,
                version=version,
                document_version_id=draft.document_version_id,
                document_content_sha256=draft.document_content_sha256,
                context_snapshot_id=draft.context_snapshot_id,
                task_id=draft.task_id,
                job_id=draft.job_id,
                source_language=draft.source_language,
                target_language=draft.target_language,
                scope=draft.scope.value,
                status=TranslationStatus.DRAFT.value,
                evidence_level=draft.evidence_level,
                prompt_version=draft.prompt_version,
            )
            session.add(artifact)
            session.flush()
            for segment in source_segments:
                session.add(_segment_record(artifact.id, segment))
            for term in initial_terms:
                session.add(_term_record(artifact.id, term))
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise TranslationConflictError(
                    "translation artifact violates an immutable uniqueness constraint"
                ) from error
            session.refresh(artifact)
            if snapshot.id != artifact.context_snapshot_id:  # pragma: no cover - identity guard
                raise TranslationIdentityError("translation context identity changed")
            return _artifact_domain(artifact)

    def work_for_document(self, document_version_id: str) -> TranslationWorkMetadata:
        identity = _identifier(
            document_version_id,
            name="document_version_id",
            maximum=80,
        )
        with self._sessions() as session:
            document = session.get(DocumentVersionRecord, identity)
            if document is None:
                raise KeyError(identity)
            work = _resolve_work(session, document.work_id)
            return TranslationWorkMetadata(
                work_id=work.id,
                title=work.canonical_title,
                abstract=work.abstract,
            )

    def create_command(
        self,
        command: TranslationCommand,
        snapshot: ContextSnapshotDraft,
        segments: Sequence[SourceSegment],
        *,
        terms: Sequence[GlossaryTerm],
        idempotency_key: str,
        prompt_version: str,
    ) -> TranslationArtifact:
        """Atomically create an independent translation task, Job, and artifact."""

        if not isinstance(command, TranslationCommand):
            raise ValueError("command must be a TranslationCommand")
        if not isinstance(snapshot, ContextSnapshotDraft):
            raise ValueError("snapshot must be a ContextSnapshotDraft")
        source_segments = _validated_segments(segments)
        initial_terms = _validated_terms(terms, generated_only=False)
        key = _identifier(idempotency_key, name="idempotency_key", maximum=160)
        version_name = _identifier(prompt_version, name="prompt_version", maximum=80)
        _require_command_snapshot_match(command, snapshot)
        command_sha256 = _translation_command_fingerprint(
            command,
            snapshot,
            prompt_version=version_name,
        )
        for attempt in range(3):
            try:
                return self._create_command_once(
                    command,
                    snapshot,
                    source_segments,
                    initial_terms,
                    idempotency_key=key,
                    prompt_version=version_name,
                    command_sha256=command_sha256,
                )
            except IntegrityError:
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    def _create_command_once(
        self,
        command: TranslationCommand,
        snapshot: ContextSnapshotDraft,
        segments: tuple[SourceSegment, ...],
        terms: tuple[GlossaryTerm, ...],
        *,
        idempotency_key: str,
        prompt_version: str,
        command_sha256: str,
    ) -> TranslationArtifact:
        with self._sessions() as session:
            existing_job = session.scalar(
                select(JobRecord).where(JobRecord.idempotency_key == idempotency_key)
            )
            if existing_job is not None:
                return _replayed_command_artifact(
                    session,
                    existing_job,
                    command_sha256=command_sha256,
                    segments=segments,
                )

            work = _resolve_work(session, snapshot.work_id)
            if work.id != snapshot.work_id:
                raise TranslationIdentityError("translation snapshot Work is not canonical")
            document = session.get(DocumentVersionRecord, snapshot.document_version_id)
            if (
                document is None
                or _resolve_work(session, document.work_id).id != work.id
                or not hmac.compare_digest(
                    document.content_sha256,
                    snapshot.document_content_sha256,
                )
            ):
                raise TranslationIdentityError("translation document identity or hash differs")

            assistant_session = session.scalar(
                select(AssistantSessionRecord).where(AssistantSessionRecord.work_id == work.id)
            )
            if assistant_session is None:
                assistant_session = AssistantSessionRecord(work_id=work.id)
                session.add(assistant_session)
                session.flush()
            snapshot_record = _snapshot_record(session, assistant_session, snapshot)

            artifact_id = str(uuid4())
            task_id = str(uuid4())
            job_id = str(uuid4())
            quick = (
                command.scope in {TranslationScope.SELECTION, TranslationScope.PAGE}
                and sum(len(segment.source_text) for segment in segments) <= 24_000
            )
            job = JobRecord(
                id=job_id,
                kind=_TRANSLATION_JOB_KIND,
                status="queued",
                progress=0,
                idempotency_key=idempotency_key,
                payload={
                    "artifact_id": artifact_id,
                    "task_id": task_id,
                    "context_snapshot_id": snapshot_record.id,
                    "command_sha256": command_sha256,
                    "scope": command.scope.value,
                    "quick": quick,
                },
            )
            session.add(job)
            session.flush()
            task = AssistantTaskRecord(
                id=task_id,
                session_id=assistant_session.id,
                context_snapshot_id=snapshot_record.id,
                retry_of_task_id=None,
                mode="translate",
                status=AssistantTaskStatus.QUEUED.value,
                network_policy="local_only",
                backend_id=command.agent.backend_id,
                requested_model_id=command.agent.model_id,
                reasoning_effort=command.agent.reasoning_effort.value,
                service_tier=command.agent.service_tier.value,
                output_language=_assistant_envelope_language(command.target_language),
                idempotency_key=idempotency_key,
                command_sha256=command_sha256,
                job_id=job.id,
            )
            session.add(task)
            session.flush()
            draft = TranslationArtifactDraft(
                work_id=work.id,
                document_version_id=document.id,
                document_content_sha256=document.content_sha256,
                context_snapshot_id=snapshot_record.id,
                task_id=task.id,
                job_id=job.id,
                source_language=command.source_language,
                target_language=command.target_language,
                scope=command.scope,
                evidence_level=_segment_evidence_level(segments),
                prompt_version=prompt_version,
            )
            artifact_version = (
                session.scalar(
                    select(func.max(TranslationArtifactRecord.version)).where(
                        TranslationArtifactRecord.work_id == work.id
                    )
                )
                or 0
            ) + 1
            artifact = TranslationArtifactRecord(
                id=artifact_id,
                work_id=draft.work_id,
                version=artifact_version,
                document_version_id=draft.document_version_id,
                document_content_sha256=draft.document_content_sha256,
                context_snapshot_id=draft.context_snapshot_id,
                task_id=draft.task_id,
                job_id=draft.job_id,
                source_language=draft.source_language,
                target_language=draft.target_language,
                scope=draft.scope.value,
                status=TranslationStatus.DRAFT.value,
                evidence_level=draft.evidence_level,
                prompt_version=draft.prompt_version,
            )
            session.add(artifact)
            session.flush()
            for segment in segments:
                session.add(_segment_record(artifact.id, segment))
            for term in terms:
                session.add(_term_record(artifact.id, term))
            _validate_identity(session, draft, segments)
            session.commit()
            session.refresh(artifact)
            return _artifact_domain(artifact)

    def artifact(self, artifact_id: str) -> TranslationArtifact:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        with self._sessions() as session:
            record = session.get(TranslationArtifactRecord, identity)
            if record is None:
                raise KeyError(identity)
            return _artifact_domain(record)

    def get(self, artifact_id: str) -> TranslationArtifact:
        return self.artifact(artifact_id)

    def artifact_for_job(self, job_id: str) -> TranslationArtifact:
        identity = _identifier(job_id, name="job_id", maximum=80)
        with self._sessions() as session:
            record = session.scalar(
                select(TranslationArtifactRecord).where(
                    TranslationArtifactRecord.job_id == identity
                )
            )
            if record is None:
                raise KeyError(identity)
            return _artifact_domain(record)

    def start(self, artifact_id: str) -> TranslationArtifact:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        with self._sessions() as session:
            artifact = session.get(TranslationArtifactRecord, identity)
            if artifact is None:
                raise KeyError(identity)
            if artifact.status == TranslationStatus.RUNNING.value:
                return _artifact_domain(artifact)
            if artifact.status != TranslationStatus.DRAFT.value:
                raise TranslationStateError("only a draft translation can start")
            artifact.status = TranslationStatus.RUNNING.value
            artifact.updated_at = utc_now()
            session.commit()
            session.refresh(artifact)
            return _artifact_domain(artifact)

    def resume(self, artifact_id: str, *, idempotency_key: str) -> TranslationArtifact:
        """Reopen one incomplete artifact while preserving committed segments and runs."""

        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        key = _identifier(idempotency_key, name="idempotency_key", maximum=160)
        key_hash = sha256(key.encode("utf-8")).hexdigest()
        with self._sessions() as session:
            artifact = session.get(TranslationArtifactRecord, identity)
            if artifact is None:
                raise KeyError(identity)
            task = session.get(AssistantTaskRecord, artifact.task_id)
            job = session.get(JobRecord, artifact.job_id)
            if (
                task is None
                or job is None
                or task.job_id != job.id
                or job.kind != _TRANSLATION_JOB_KIND
            ):
                raise TranslationIdentityError("translation task and Job identity changed")
            payload = dict(job.payload)
            raw_keys = payload.get("resume_idempotency_sha256", [])
            resume_keys = (
                [value for value in raw_keys if isinstance(value, str) and _HASH.fullmatch(value)]
                if isinstance(raw_keys, list)
                else []
            )
            if key_hash in resume_keys:
                return _artifact_domain(artifact)
            if artifact.status == TranslationStatus.SUCCEEDED.value:
                raise TranslationStateError("a succeeded translation cannot resume")
            if artifact.status == TranslationStatus.RUNNING.value or job.status in {
                "queued",
                "running",
            }:
                raise TranslationStateError("translation is already queued or running")
            if artifact.status not in {
                TranslationStatus.DRAFT.value,
                TranslationStatus.PARTIAL.value,
                TranslationStatus.FAILED.value,
                TranslationStatus.CANCELLED.value,
            }:
                raise TranslationStateError("translation cannot resume from its current state")
            if task.status not in {
                AssistantTaskStatus.FAILED.value,
                AssistantTaskStatus.CANCELLED.value,
            } or job.status not in {"failed", "cancelled"}:
                raise TranslationIdentityError(
                    "only a terminal incomplete translation can explicitly resume"
                )
            remaining = tuple(
                session.scalars(
                    select(TranslationSegmentRecord).where(
                        TranslationSegmentRecord.artifact_id == artifact.id,
                        TranslationSegmentRecord.status != "succeeded",
                    )
                )
            )
            if not remaining:
                raise TranslationStateError("translation has no incomplete segments to resume")
            now = utc_now()
            for segment in remaining:
                if segment.status == "failed":
                    segment.status = "pending"
                    segment.diagnostic = None
                    segment.updated_at = now
            artifact.status = TranslationStatus.DRAFT.value
            artifact.completed_at = None
            artifact.updated_at = now
            task.status = AssistantTaskStatus.QUEUED.value
            task.completed_at = None
            task.error_code = None
            task.diagnostic = None
            task.updated_at = now
            committed_ids = tuple(
                f"segment-{index:06d}"
                for index in session.scalars(
                    select(TranslationSegmentRecord.segment_index)
                    .where(
                        TranslationSegmentRecord.artifact_id == artifact.id,
                        TranslationSegmentRecord.status == "succeeded",
                    )
                    .order_by(TranslationSegmentRecord.segment_index)
                )
            )
            resume_keys.append(key_hash)
            payload["resume_idempotency_sha256"] = resume_keys[-32:]
            payload["resume_attempts"] = int(payload.get("resume_attempts", 0)) + 1
            job.payload = payload
            job.status = "queued"
            job.progress = 0
            job.result = None
            job.checkpoint = {"committed_segment_ids": list(committed_ids)}
            job.error_code = None
            job.error_message = None
            job.cancel_requested = False
            job.pause_requested = False
            job.paused_at = None
            session.commit()
            session.refresh(artifact)
            return _artifact_domain(artifact)

    def save_batch(
        self,
        artifact_id: str,
        translations: Mapping[str, str],
    ) -> tuple[StoredTranslationSegment, ...]:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        if not isinstance(translations, Mapping) or not 1 <= len(translations) <= 40:
            raise ValueError("translations must contain between 1 and 40 segments")
        translated = {
            _identifier(segment_id, name="segment_id", maximum=80): _translated_text(text)
            for segment_id, text in translations.items()
        }
        with self._sessions() as session:
            artifact = session.get(TranslationArtifactRecord, identity)
            if artifact is None:
                raise KeyError(identity)
            if artifact.status != TranslationStatus.RUNNING.value:
                raise TranslationStateError("translation batches require a running artifact")
            records = tuple(
                session.scalars(
                    select(TranslationSegmentRecord).where(
                        TranslationSegmentRecord.artifact_id == identity,
                        TranslationSegmentRecord.segment_index.in_(
                            tuple(_segment_index(value) for value in translated)
                        ),
                    )
                )
            )
            by_id = {f"segment-{record.segment_index:06d}": record for record in records}
            if set(by_id) != set(translated):
                raise TranslationConflictError("translated segment IDs do not match the artifact")
            for segment_id, text in translated.items():
                record = by_id[segment_id]
                if record.status == "succeeded":
                    if record.translated_text != text:
                        raise TranslationStateError("completed segment cannot be rewritten")
                    continue
                record.translated_text = text
                record.status = "succeeded"
                record.diagnostic = None
                record.updated_at = utc_now()
            artifact.updated_at = utc_now()
            session.commit()
        return tuple(
            segment for segment in self.segments(identity) if segment.segment_id in translated
        )

    def record_failure(
        self,
        artifact_id: str,
        segment_ids: Sequence[str],
        *,
        diagnostic: str,
    ) -> TranslationArtifact:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        identifiers = tuple(
            _identifier(value, name="segment_id", maximum=80) for value in segment_ids
        )
        if not identifiers or len(identifiers) > 40 or len(identifiers) != len(set(identifiers)):
            raise ValueError("segment_ids must contain between 1 and 40 unique values")
        details = redact_diagnostic(
            _free_text(diagnostic, name="diagnostic", maximum=20_000),
            limit=20_000,
        )
        with self._sessions() as session:
            artifact = session.get(TranslationArtifactRecord, identity)
            if artifact is None:
                raise KeyError(identity)
            if artifact.status not in {
                TranslationStatus.DRAFT.value,
                TranslationStatus.RUNNING.value,
            }:
                raise TranslationStateError("completed translation artifact is immutable")
            records = tuple(
                session.scalars(
                    select(TranslationSegmentRecord).where(
                        TranslationSegmentRecord.artifact_id == identity,
                        TranslationSegmentRecord.segment_index.in_(
                            tuple(_segment_index(value) for value in identifiers)
                        ),
                    )
                )
            )
            if len(records) != len(identifiers):
                raise TranslationConflictError("failed segment IDs do not match the artifact")
            for record in records:
                if record.status != "succeeded":
                    record.status = "failed"
                    record.translated_text = None
                    record.diagnostic = details
                    record.updated_at = utc_now()
            succeeded = session.scalar(
                select(func.count())
                .select_from(TranslationSegmentRecord)
                .where(
                    TranslationSegmentRecord.artifact_id == identity,
                    TranslationSegmentRecord.status == "succeeded",
                )
            )
            now = utc_now()
            if succeeded:
                artifact.status = TranslationStatus.PARTIAL.value
                artifact.completed_at = now
            else:
                artifact.status = TranslationStatus.DRAFT.value
                artifact.completed_at = None
            artifact.updated_at = now
            session.commit()
            session.refresh(artifact)
            return _artifact_domain(artifact)

    def cancel(self, artifact_id: str) -> TranslationArtifact:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        with self._sessions() as session:
            artifact = session.get(TranslationArtifactRecord, identity)
            if artifact is None:
                raise KeyError(identity)
            if artifact.status == TranslationStatus.CANCELLED.value:
                return _artifact_domain(artifact)
            if artifact.status not in {
                TranslationStatus.DRAFT.value,
                TranslationStatus.RUNNING.value,
            }:
                raise TranslationStateError("completed translation artifact is immutable")
            now = utc_now()
            artifact.status = TranslationStatus.CANCELLED.value
            artifact.completed_at = now
            artifact.updated_at = now
            session.commit()
            session.refresh(artifact)
            return _artifact_domain(artifact)

    def complete(self, artifact_id: str) -> TranslationArtifact:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        with self._sessions() as session:
            artifact = session.get(TranslationArtifactRecord, identity)
            if artifact is None:
                raise KeyError(identity)
            if artifact.status == TranslationStatus.SUCCEEDED.value:
                return _artifact_domain(artifact)
            if artifact.status != TranslationStatus.RUNNING.value:
                raise TranslationStateError("only a running translation can complete")
            total = session.scalar(
                select(func.count())
                .select_from(TranslationSegmentRecord)
                .where(TranslationSegmentRecord.artifact_id == identity)
            )
            succeeded = session.scalar(
                select(func.count())
                .select_from(TranslationSegmentRecord)
                .where(
                    TranslationSegmentRecord.artifact_id == identity,
                    TranslationSegmentRecord.status == "succeeded",
                )
            )
            if not total or total != succeeded:
                raise TranslationStateError("translation cannot complete with pending segments")
            task = session.get(AssistantTaskRecord, artifact.task_id)
            job = session.get(JobRecord, artifact.job_id)
            if task is None or job is None or task.job_id != job.id:
                raise TranslationIdentityError("translation task and Job identity changed")
            succeeded_run = session.scalar(
                select(AssistantAgentRunRecord.id)
                .where(
                    AssistantAgentRunRecord.task_id == task.id,
                    AssistantAgentRunRecord.status == AssistantTaskStatus.SUCCEEDED.value,
                )
                .limit(1)
            )
            if succeeded_run is None:
                raise TranslationStateError("translation completion requires a succeeded Agent run")
            now = utc_now()
            artifact.status = TranslationStatus.SUCCEEDED.value
            artifact.completed_at = now
            artifact.updated_at = now
            task.status = AssistantTaskStatus.SUCCEEDED.value
            task.completed_at = now
            task.updated_at = now
            task.error_code = None
            task.diagnostic = None
            job.status = "succeeded"
            job.progress = 100
            job.result = {
                "artifact_id": artifact.id,
                "task_id": task.id,
                "context_snapshot_id": artifact.context_snapshot_id,
            }
            job.error_code = None
            job.error_message = None
            session.commit()
            session.refresh(artifact)
            return _artifact_domain(artifact)

    def segments(self, artifact_id: str) -> tuple[StoredTranslationSegment, ...]:
        return self._segments(artifact_id, pending_only=False)

    def pending_segments(
        self,
        artifact_id: str,
    ) -> tuple[StoredTranslationSegment, ...]:
        return self._segments(artifact_id, pending_only=True)

    def terms(self, artifact_id: str) -> tuple[GlossaryTerm, ...]:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        with self._sessions() as session:
            if session.get(TranslationArtifactRecord, identity) is None:
                raise KeyError(identity)
            records = session.scalars(
                select(TranslationTermRecord)
                .where(TranslationTermRecord.artifact_id == identity)
                .order_by(TranslationTermRecord.normalized_source_term)
            )
            return tuple(_term_domain(record) for record in records)

    def term_records(self, artifact_id: str) -> tuple[StoredTranslationTerm, ...]:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        with self._sessions() as session:
            if session.get(TranslationArtifactRecord, identity) is None:
                raise KeyError(identity)
            records = session.scalars(
                select(TranslationTermRecord)
                .where(TranslationTermRecord.artifact_id == identity)
                .order_by(TranslationTermRecord.normalized_source_term)
            )
            return tuple(_stored_term(record) for record in records)

    def set_term_by_id(
        self,
        artifact_id: str,
        term_id: str,
        *,
        target_term: str,
        locked: bool,
    ) -> StoredTranslationTerm:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        term_identity = _identifier(term_id, name="term_id", maximum=80)
        if not isinstance(locked, bool):
            raise ValueError("locked must be a boolean")
        with self._sessions() as session:
            artifact = session.get(TranslationArtifactRecord, identity)
            if artifact is None:
                raise KeyError(identity)
            record = session.get(TranslationTermRecord, term_identity)
            if record is None or record.artifact_id != artifact.id:
                raise KeyError(term_identity)
            update = GlossaryTermUpdate(
                source_term=record.source_term,
                target_term=target_term,
                locked=locked,
            )
            if (
                record.target_term == update.target_term
                and record.locked == update.locked
                and record.origin == "user"
            ):
                return _stored_term(record)
            if artifact.status not in _MUTABLE_STATUSES:
                raise TranslationStateError("completed translation artifact is immutable")
            target_changed = record.target_term != update.target_term
            record.source_term = update.source_term
            record.target_term = update.target_term
            record.origin = "user"
            record.locked = update.locked
            record.updated_at = utc_now()
            if target_changed:
                _mark_affected_segments_stale(
                    session,
                    artifact.id,
                    update.normalized_source_term,
                )
            session.commit()
            session.refresh(record)
            return _stored_term(record)

    def export_metadata(self, artifact_id: str) -> TranslationExportMetadata:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        with self._sessions() as session:
            artifact = session.get(TranslationArtifactRecord, identity)
            if artifact is None:
                raise KeyError(identity)
            work = session.get(WorkRecord, artifact.work_id)
            task = session.get(AssistantTaskRecord, artifact.task_id)
            if work is None or task is None:
                raise TranslationIdentityError("translation export identity is incomplete")
            latest_run = session.scalar(
                select(AssistantAgentRunRecord)
                .where(AssistantAgentRunRecord.task_id == task.id)
                .order_by(AssistantAgentRunRecord.attempt.desc())
                .limit(1)
            )
            return TranslationExportMetadata(
                title=work.canonical_title,
                authors=tuple(str(author) for author in work.authors),
                backend_id=task.backend_id,
                requested_model_id=task.requested_model_id,
                resolved_model_id=(
                    latest_run.resolved_model_id if latest_run is not None else None
                ),
                reasoning_effort=task.reasoning_effort,
                service_tier=task.service_tier,
            )

    def record_export(
        self,
        artifact_id: str,
        *,
        format: str,
        content_sha256: str,
        relative_path: str,
    ) -> StoredTranslationExport:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        export_format = _identifier(format, name="format", maximum=16)
        content_hash = _hash(content_sha256, name="content_sha256")
        path = _free_text(relative_path, name="relative_path", maximum=2_000)
        if export_format not in {"markdown", "html", "print_pdf"}:
            raise ValueError("translation export format is unsupported")
        extension = {
            "markdown": "md",
            "html": "html",
            "print_pdf": "pdf",
        }[export_format]
        expected_path = f"{content_hash[:2]}/{content_hash}.{extension}"
        if path != expected_path:
            raise ValueError("translation export path must match its content hash")
        with self._sessions() as session:
            if session.get(TranslationArtifactRecord, identity) is None:
                raise KeyError(identity)
            record = session.scalar(
                select(TranslationExportRecord).where(
                    TranslationExportRecord.artifact_id == identity,
                    TranslationExportRecord.format == export_format,
                    TranslationExportRecord.content_sha256 == content_hash,
                )
            )
            if record is None:
                record = TranslationExportRecord(
                    artifact_id=identity,
                    format=export_format,
                    content_sha256=content_hash,
                    relative_path=path,
                )
                session.add(record)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    record = session.scalar(
                        select(TranslationExportRecord).where(
                            TranslationExportRecord.artifact_id == identity,
                            TranslationExportRecord.format == export_format,
                            TranslationExportRecord.content_sha256 == content_hash,
                        )
                    )
                    if record is None:  # pragma: no cover - unrelated database failure
                        raise
                else:
                    session.refresh(record)
            if record.relative_path != path:
                raise TranslationConflictError("translation export hash path conflicts")
            return _export_domain(record)

    def set_term(
        self,
        artifact_id: str,
        update: GlossaryTermUpdate,
    ) -> GlossaryTerm:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        if not isinstance(update, GlossaryTermUpdate):
            raise ValueError("update must be a GlossaryTermUpdate")
        with self._sessions() as session:
            artifact = _mutable_artifact(session, identity)
            record = session.scalar(
                select(TranslationTermRecord).where(
                    TranslationTermRecord.artifact_id == artifact.id,
                    TranslationTermRecord.normalized_source_term == update.normalized_source_term,
                )
            )
            target_changed = record is None or record.target_term != update.target_term
            if record is None:
                term_count = session.scalar(
                    select(func.count())
                    .select_from(TranslationTermRecord)
                    .where(TranslationTermRecord.artifact_id == artifact.id)
                )
                if (term_count or 0) >= 500:
                    raise TranslationConflictError(
                        "a translation artifact cannot contain more than 500 terms"
                    )
                record = TranslationTermRecord(
                    artifact_id=artifact.id,
                    normalized_source_term=update.normalized_source_term,
                    source_term=update.source_term,
                    target_term=update.target_term,
                    origin="user",
                    locked=update.locked,
                )
                session.add(record)
            else:
                record.source_term = update.source_term
                record.target_term = update.target_term
                record.origin = "user"
                record.locked = update.locked
                record.updated_at = utc_now()
            if target_changed:
                _mark_affected_segments_stale(
                    session,
                    artifact.id,
                    update.normalized_source_term,
                )
            session.commit()
            session.refresh(record)
            return _term_domain(record)

    def merge_generated_terms(
        self,
        artifact_id: str,
        generated: Sequence[GlossaryTerm],
    ) -> tuple[GlossaryTerm, ...]:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        incoming = _validated_terms(generated, generated_only=True)
        with self._sessions() as session:
            artifact = _mutable_artifact(session, identity)
            current = {
                item.normalized_source_term: item
                for item in session.scalars(
                    select(TranslationTermRecord).where(
                        TranslationTermRecord.artifact_id == artifact.id
                    )
                )
            }
            new_identities = {
                term.normalized_source_term
                for term in incoming
                if term.normalized_source_term not in current
            }
            if len(current) + len(new_identities) > 500:
                raise TranslationConflictError(
                    "a translation artifact cannot contain more than 500 terms"
                )
            for term in incoming:
                record = current.get(term.normalized_source_term)
                if record is not None and (record.locked or record.origin == "user"):
                    continue
                target_changed = record is not None and record.target_term != term.target_term
                if record is None:
                    record = _term_record(artifact.id, term)
                    session.add(record)
                    current[term.normalized_source_term] = record
                    target_changed = term.target_term is not None
                else:
                    record.source_term = term.source_term
                    record.target_term = term.target_term
                    record.origin = term.origin
                    record.locked = False
                    record.updated_at = utc_now()
                if target_changed:
                    _mark_affected_segments_stale(
                        session,
                        artifact.id,
                        term.normalized_source_term,
                    )
            session.commit()
        return self.terms(identity)

    def _segments(
        self,
        artifact_id: str,
        *,
        pending_only: bool,
    ) -> tuple[StoredTranslationSegment, ...]:
        identity = _identifier(artifact_id, name="artifact_id", maximum=80)
        with self._sessions() as session:
            if session.get(TranslationArtifactRecord, identity) is None:
                raise KeyError(identity)
            statement = select(TranslationSegmentRecord).where(
                TranslationSegmentRecord.artifact_id == identity
            )
            if pending_only:
                statement = statement.where(TranslationSegmentRecord.status != "succeeded")
            records = session.scalars(statement.order_by(TranslationSegmentRecord.segment_index))
            return tuple(_segment_domain(record) for record in records)


def _validated_segments(values: Sequence[SourceSegment]) -> tuple[SourceSegment, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("segments must be a collection")
    segments = tuple(values)
    if not segments or len(segments) > MAX_SEGMENTS:
        raise ValueError(f"segments must contain between 1 and {MAX_SEGMENTS:,} values")
    if any(not isinstance(item, SourceSegment) for item in segments):
        raise ValueError("segments must contain SourceSegment values")
    if tuple(item.segment_index for item in segments) != tuple(range(1, len(segments) + 1)):
        raise TranslationConflictError("translation segments must be contiguous and ordered")
    if len({item.segment_id for item in segments}) != len(segments):
        raise TranslationConflictError("translation segment IDs must be unique")
    return segments


def _validated_terms(
    values: Sequence[GlossaryTerm],
    *,
    generated_only: bool,
) -> tuple[GlossaryTerm, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("terms must be a collection")
    terms = tuple(values)
    if len(terms) > 500 or any(not isinstance(item, GlossaryTerm) for item in terms):
        raise ValueError("terms must contain at most 500 GlossaryTerm values")
    identities = tuple(item.normalized_source_term for item in terms)
    if len(identities) != len(set(identities)):
        raise TranslationConflictError("duplicate normalized glossary terms are not allowed")
    if generated_only and any(item.origin == "user" or item.locked for item in terms):
        raise TranslationConflictError("generated terms cannot claim user or locked state")
    return terms


def _resolve_work(session: Session, work_id: str) -> WorkRecord:
    current_id = _identifier(work_id, name="work_id", maximum=80)
    visited: set[str] = set()
    while True:
        if current_id in visited:
            raise TranslationIdentityError("translation Work merge chain contains a cycle")
        visited.add(current_id)
        work = session.get(WorkRecord, current_id)
        if work is None:
            raise KeyError(current_id)
        if work.status == "active" and work.merged_into_work_id is None:
            return work
        if work.status != "merged" or not work.merged_into_work_id:
            raise TranslationIdentityError("translation Work merge state is invalid")
        current_id = work.merged_into_work_id


def _require_command_snapshot_match(
    command: TranslationCommand,
    snapshot: ContextSnapshotDraft,
) -> None:
    if command.context.document_version_id != snapshot.document_version_id:
        raise TranslationIdentityError("translation command document differs from its snapshot")
    if command.context.scope is not snapshot.scope:
        raise TranslationIdentityError("translation command scope differs from its snapshot")
    if command.context.page_number is not None and (
        command.context.page_number not in snapshot.page_numbers
    ):
        raise TranslationIdentityError("translation command page is absent from its snapshot")
    if command.context.selection is not None and snapshot.selection != command.context.selection:
        raise TranslationIdentityError("translation selection differs from its snapshot")


def _translation_command_fingerprint(
    command: TranslationCommand,
    snapshot: ContextSnapshotDraft,
    *,
    prompt_version: str,
) -> str:
    payload = {
        "work_id": snapshot.work_id,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "prompt_version": prompt_version,
        "command": command.model_dump(mode="json"),
    }
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _snapshot_record(
    session: Session,
    assistant_session: AssistantSessionRecord,
    snapshot: ContextSnapshotDraft,
) -> AssistantContextSnapshotRecord:
    record = session.scalar(
        select(AssistantContextSnapshotRecord).where(
            AssistantContextSnapshotRecord.snapshot_sha256 == snapshot.snapshot_sha256
        )
    )
    expected = (
        assistant_session.id,
        snapshot.work_id,
        snapshot.document_version_id,
        snapshot.document_content_sha256,
        snapshot.scope.value,
        list(snapshot.page_numbers),
        snapshot.selection.model_dump(mode="json") if snapshot.selection is not None else None,
        list(snapshot.annotation_ids),
        snapshot.section_fallback,
        snapshot.extraction_version,
        snapshot.evidence,
        snapshot.evidence_sha256,
        list(snapshot.warnings),
    )
    if record is not None:
        actual = (
            record.session_id,
            record.work_id,
            record.document_version_id,
            record.document_content_sha256,
            record.scope,
            list(record.page_numbers),
            record.selection,
            list(record.annotation_ids),
            record.section_fallback,
            record.extraction_version,
            dict(record.evidence),
            record.evidence_sha256,
            list(record.warnings),
        )
        if actual != expected:
            raise TranslationConflictError("translation context snapshot identity conflicts")
        return record
    record = AssistantContextSnapshotRecord(
        session_id=assistant_session.id,
        work_id=snapshot.work_id,
        document_version_id=snapshot.document_version_id,
        document_content_sha256=snapshot.document_content_sha256,
        scope=snapshot.scope.value,
        page_numbers=list(snapshot.page_numbers),
        selection=(
            snapshot.selection.model_dump(mode="json") if snapshot.selection is not None else None
        ),
        annotation_ids=list(snapshot.annotation_ids),
        section_fallback=snapshot.section_fallback,
        extraction_version=snapshot.extraction_version,
        evidence=snapshot.evidence,
        evidence_sha256=snapshot.evidence_sha256,
        warnings=list(snapshot.warnings),
        snapshot_sha256=snapshot.snapshot_sha256,
    )
    session.add(record)
    session.flush()
    return record


def _replayed_command_artifact(
    session: Session,
    job: JobRecord,
    *,
    command_sha256: str,
    segments: tuple[SourceSegment, ...],
) -> TranslationArtifact:
    payload = dict(job.payload)
    artifact_id = payload.get("artifact_id")
    if (
        job.kind != _TRANSLATION_JOB_KIND
        or payload.get("command_sha256") != command_sha256
        or not isinstance(artifact_id, str)
    ):
        raise TranslationConflictError("idempotency key belongs to another command")
    artifact = session.get(TranslationArtifactRecord, artifact_id)
    if artifact is None or artifact.job_id != job.id:
        raise TranslationIdentityError("idempotent translation Job lost its artifact")
    _require_segment_match(session, artifact.id, segments)
    return _artifact_domain(artifact)


def _segment_evidence_level(
    segments: tuple[SourceSegment, ...],
) -> TranslationEvidenceLevel:
    kinds = {segment.source_kind for segment in segments}
    if kinds == {"native"}:
        return "native_text"
    if kinds == {"ocr"}:
        return "ocr_text"
    return "mixed_text"


def _assistant_envelope_language(target_language: TranslationLanguage) -> str:
    # AssistantTaskRecord predates translation artifacts and has a narrower UI-language
    # envelope. The artifact's target_language remains the authoritative translation value.
    if target_language == "en":
        return "en"
    if target_language == "zh-CN":
        return "zh-CN"
    return "bilingual"


def _segment_index(segment_id: str) -> int:
    match = re.fullmatch(r"segment-([0-9]{6})", segment_id)
    if match is None or int(match.group(1)) < 1:
        raise ValueError("segment_id must use the canonical segment-NNNNNN form")
    return int(match.group(1))


def _translated_text(value: str) -> str:
    return _free_text(value, name="translated text", maximum=50_000, preserve_edges=True)


def _free_text(
    value: str,
    *,
    name: str,
    maximum: int,
    preserve_edges: bool = False,
) -> str:
    if not isinstance(value, str) or _TEXT_CONTROL_CHARACTER.search(value):
        raise ValueError(f"{name} is invalid")
    normalized = value if preserve_edges else value.strip()
    if not normalized.strip() or len(normalized) > maximum:
        raise ValueError(f"{name} must be non-blank and at most {maximum:,} characters")
    return normalized


def _validate_identity(
    session: Session,
    draft: TranslationArtifactDraft,
    segments: tuple[SourceSegment, ...],
) -> AssistantContextSnapshotRecord:
    work = session.get(WorkRecord, draft.work_id)
    if work is None or work.status != "active" or work.merged_into_work_id is not None:
        raise TranslationIdentityError("translation Work identity is not canonical and active")
    document = session.get(DocumentVersionRecord, draft.document_version_id)
    if (
        document is None
        or document.work_id != work.id
        or not hmac.compare_digest(document.content_sha256, draft.document_content_sha256)
    ):
        raise TranslationIdentityError("translation document identity or content hash differs")
    snapshot = session.get(AssistantContextSnapshotRecord, draft.context_snapshot_id)
    if (
        snapshot is None
        or snapshot.work_id != work.id
        or snapshot.document_version_id != document.id
        or not hmac.compare_digest(
            snapshot.document_content_sha256,
            draft.document_content_sha256,
        )
    ):
        raise TranslationIdentityError("translation context does not match the document")
    expected_scope = _CONTEXT_SCOPE.get(snapshot.scope)
    if expected_scope is None or expected_scope is not draft.scope:
        raise TranslationIdentityError("translation scope does not match the frozen context")
    allowed_pages = set(snapshot.page_numbers)
    if any(segment.page_numbers[0] not in allowed_pages for segment in segments):
        raise TranslationIdentityError("translation segment page is outside the frozen context")
    expected_level: TranslationEvidenceLevel
    kinds = {segment.source_kind for segment in segments}
    if kinds == {"native"}:
        expected_level = "native_text"
    elif kinds == {"ocr"}:
        expected_level = "ocr_text"
    else:
        expected_level = "mixed_text"
    if draft.evidence_level != expected_level:
        raise TranslationIdentityError("translation evidence level does not match source segments")
    task = session.get(AssistantTaskRecord, draft.task_id)
    if task is None or task.mode != "translate":
        raise TranslationIdentityError("translation artifact requires a translate task")
    if task.network_policy != "local_only":
        raise TranslationIdentityError("translation task must use network_policy=local_only")
    if (
        task.context_snapshot_id != snapshot.id
        or task.session_id != snapshot.session_id
        or task.job_id != draft.job_id
    ):
        raise TranslationIdentityError("translation task does not match context and Job identity")
    if session.get(JobRecord, draft.job_id) is None:
        raise TranslationIdentityError("translation Job was not found")
    return snapshot


def _require_artifact_match(
    record: TranslationArtifactRecord,
    draft: TranslationArtifactDraft,
) -> None:
    expected = (
        draft.work_id,
        draft.document_version_id,
        draft.document_content_sha256,
        draft.context_snapshot_id,
        draft.task_id,
        draft.job_id,
        draft.source_language,
        draft.target_language,
        draft.scope.value,
        draft.evidence_level,
        draft.prompt_version,
    )
    actual = (
        record.work_id,
        record.document_version_id,
        record.document_content_sha256,
        record.context_snapshot_id,
        record.task_id,
        record.job_id,
        record.source_language,
        record.target_language,
        record.scope,
        record.evidence_level,
        record.prompt_version,
    )
    if actual != expected:
        raise TranslationConflictError("translation task idempotency identity conflicts")


def _require_segment_match(
    session: Session,
    artifact_id: str,
    expected: tuple[SourceSegment, ...],
) -> None:
    records = tuple(
        session.scalars(
            select(TranslationSegmentRecord)
            .where(TranslationSegmentRecord.artifact_id == artifact_id)
            .order_by(TranslationSegmentRecord.segment_index)
        )
    )
    if len(records) != len(expected):
        raise TranslationConflictError("translation segment idempotency content conflicts")
    for record, segment in zip(records, expected, strict=True):
        if (
            record.segment_index != segment.segment_index
            or record.page_number != segment.page_numbers[0]
            or tuple(record.block_numbers) != segment.block_numbers
            or record.source_text != segment.source_text
            or record.source_sha256 != segment.source_sha256
            or dict(record.protected_map) != segment.protected_map
            or record.source_kind != segment.source_kind
            or record.ocr_confidence != segment.ocr_confidence
        ):
            raise TranslationConflictError("translation segment idempotency content conflicts")


def _segment_record(artifact_id: str, segment: SourceSegment) -> TranslationSegmentRecord:
    return TranslationSegmentRecord(
        id=f"{artifact_id}:{segment.segment_id}",
        artifact_id=artifact_id,
        segment_index=segment.segment_index,
        page_number=segment.page_numbers[0],
        block_numbers=list(segment.block_numbers),
        source_text=segment.source_text,
        source_sha256=segment.source_sha256,
        protected_map=segment.protected_map,
        translated_text=None,
        source_kind=segment.source_kind,
        ocr_confidence=segment.ocr_confidence,
        status="pending",
        diagnostic=None,
    )


def _term_record(artifact_id: str, term: GlossaryTerm) -> TranslationTermRecord:
    return TranslationTermRecord(
        artifact_id=artifact_id,
        normalized_source_term=term.normalized_source_term,
        source_term=term.source_term,
        target_term=term.target_term,
        origin=term.origin,
        locked=term.locked,
    )


def _mutable_artifact(session: Session, artifact_id: str) -> TranslationArtifactRecord:
    artifact = session.get(TranslationArtifactRecord, artifact_id)
    if artifact is None:
        raise KeyError(artifact_id)
    if artifact.status not in _MUTABLE_STATUSES:
        raise TranslationStateError("completed translation artifact is immutable")
    return artifact


def _mark_affected_segments_stale(
    session: Session,
    artifact_id: str,
    normalized_term: str,
) -> None:
    records = session.scalars(
        select(TranslationSegmentRecord).where(
            TranslationSegmentRecord.artifact_id == artifact_id,
            TranslationSegmentRecord.status == "succeeded",
        )
    )
    for record in records:
        if _contains_term(record.source_text, normalized_term):
            record.status = "stale"
            record.updated_at = utc_now()


def _contains_term(source: str, normalized_term: str) -> bool:
    normalized_source = normalize_source_term(source)
    pattern = re.compile(rf"(?<!\w){re.escape(normalized_term)}(?!\w)")
    return pattern.search(normalized_source) is not None


def _artifact_domain(record: TranslationArtifactRecord) -> TranslationArtifact:
    return TranslationArtifact(
        id=record.id,
        work_id=record.work_id,
        version=record.version,
        document_version_id=record.document_version_id,
        document_content_sha256=record.document_content_sha256,
        context_snapshot_id=record.context_snapshot_id,
        task_id=record.task_id,
        job_id=record.job_id,
        source_language=cast(TranslationLanguage, record.source_language),
        target_language=cast(TranslationLanguage, record.target_language),
        scope=TranslationScope(record.scope),
        status=TranslationStatus(record.status),
        evidence_level=cast(TranslationEvidenceLevel, record.evidence_level),
        prompt_version=record.prompt_version,
        created_at=record.created_at,
        updated_at=record.updated_at,
        completed_at=record.completed_at,
    )


def _segment_domain(record: TranslationSegmentRecord) -> StoredTranslationSegment:
    source_hash = sha256(record.source_text.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(source_hash, record.source_sha256):
        raise TranslationConflictError("stored translation segment source hash is invalid")
    raw_map = dict(record.protected_map)
    protected_map: dict[str, dict[str, str]] = {}
    ordered_map: list[tuple[int, str, dict[str, str]]] = []
    for token, raw_value in raw_map.items():
        if not isinstance(token, str) or not isinstance(raw_value, dict):
            raise TranslationConflictError("stored protected span map is invalid")
        token_match = _TOKEN.fullmatch(token)
        if token_match is None:
            raise TranslationConflictError("stored protected span token is invalid")
        values = dict(raw_value)
        if set(values) != {"literal", "sha256", "kind"} or any(
            not isinstance(value, str) for value in values.values()
        ):
            raise TranslationConflictError("stored protected span map is invalid")
        typed_values = cast(dict[str, str], values)
        literal_hash = sha256(typed_values["literal"].encode("utf-8")).hexdigest()
        if not hmac.compare_digest(literal_hash, typed_values["sha256"]) or not hmac.compare_digest(
            token_match.group("digest"),
            literal_hash[:8],
        ):
            raise TranslationConflictError("stored protected span literal hash is invalid")
        protected_map[token] = typed_values
        ordered_map.append((int(token_match.group("index")), token, typed_values))
    ordered_map.sort(key=lambda item: item[0])
    if len({item[0] for item in ordered_map}) != len(ordered_map):
        raise TranslationConflictError("stored protected span indexes are duplicated")
    try:
        local_protected = protect_spans(record.source_text)
    except ProtectedSpanError as error:
        raise TranslationConflictError("stored translation source cannot be protected") from error
    if len(local_protected.spans) != len(ordered_map):
        raise TranslationConflictError("stored protected span map does not match source text")
    token_rebinding: dict[str, str] = {}
    for local_span, (_, stored_token, stored_values) in zip(
        local_protected.spans,
        ordered_map,
        strict=True,
    ):
        if (
            local_span.literal != stored_values["literal"]
            or local_span.literal_sha256 != stored_values["sha256"]
            or local_span.kind != stored_values["kind"]
        ):
            raise TranslationConflictError("stored protected span map does not match source text")
        token_rebinding[local_span.token] = stored_token
    protected_text = _TOKEN.sub(
        lambda match: token_rebinding.get(match.group(0), match.group(0)),
        local_protected.text,
    )
    return StoredTranslationSegment(
        record_id=record.id,
        artifact_id=record.artifact_id,
        segment_id=f"segment-{record.segment_index:06d}",
        segment_index=record.segment_index,
        page_number=record.page_number,
        block_numbers=tuple(record.block_numbers),
        source_text=record.source_text,
        source_sha256=record.source_sha256,
        protected_text=protected_text,
        protected_map=protected_map,
        translated_text=record.translated_text,
        source_kind=cast(Literal["native", "ocr"], record.source_kind),
        ocr_confidence=record.ocr_confidence,
        status=record.status,
        diagnostic=record.diagnostic,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _term_domain(record: TranslationTermRecord) -> GlossaryTerm:
    term = GlossaryTerm(
        source_term=record.source_term,
        target_term=record.target_term,
        origin=cast(GlossaryOrigin, record.origin),
        locked=record.locked,
    )
    if term.normalized_source_term != record.normalized_source_term:
        raise TranslationConflictError("stored glossary normalization is invalid")
    return term


def _stored_term(record: TranslationTermRecord) -> StoredTranslationTerm:
    term = _term_domain(record)
    return StoredTranslationTerm(
        id=record.id,
        artifact_id=record.artifact_id,
        normalized_source_term=term.normalized_source_term,
        source_term=term.source_term,
        target_term=term.target_term,
        origin=term.origin,
        locked=term.locked,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _export_domain(record: TranslationExportRecord) -> StoredTranslationExport:
    return StoredTranslationExport(
        id=record.id,
        artifact_id=record.artifact_id,
        format=record.format,
        content_sha256=record.content_sha256,
        relative_path=record.relative_path,
        created_at=record.created_at,
    )


def _identifier(value: str, *, name: str, maximum: int) -> str:
    if not isinstance(value, str) or _CONTROL_CHARACTER.search(value):
        raise ValueError(f"{name} is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} must be non-blank and at most {maximum} characters")
    return normalized


def _hash(value: str, *, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


__all__ = [
    "StoredTranslationExport",
    "StoredTranslationSegment",
    "StoredTranslationTerm",
    "TranslationArtifactDraft",
    "TranslationConflictError",
    "TranslationExportMetadata",
    "TranslationIdentityError",
    "TranslationRepository",
    "TranslationRepositoryError",
    "TranslationStateError",
    "TranslationWorkMetadata",
]
