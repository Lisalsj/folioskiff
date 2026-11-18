"""Transactional persistence for evidence-bound Assistant tasks.

The repository owns the transaction that publishes a Job, frozen context,
task, and first user message.  Callers never receive a runnable Job before the
corresponding user intent is durable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, Literal, cast
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.agents.contracts import ReasoningEffort, ServiceTier
from paper_reader.agents.process_policy import redact_diagnostic
from paper_reader.assistant.contracts import (
    AssistantMode,
    AssistantOutputLanguage,
    AssistantTaskCommand,
    AssistantTaskStatus,
    ContextScope,
    NetworkPolicy,
    TextSelectionContext,
)
from paper_reader.assistant.models import (
    AssistantAgentRunRecord,
    AssistantCheckpointRecord,
    AssistantContextSnapshotRecord,
    AssistantFragmentRecord,
    AssistantMessageRecord,
    AssistantSessionRecord,
    AssistantTaskRecord,
)
from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.jobs.models import JobRecord, utc_now

AssistantMessageRole = Literal["user", "assistant", "system_diagnostic"]
AssistantMessageStatus = Literal["committed", "complete", "failed", "cancelled"]
AssistantFragmentKind = Literal["text", "reasoning", "diagnostic"]
AssistantTerminalStatus = Literal["succeeded", "failed", "cancelled"]

_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_TERMINAL_STATUSES = frozenset(
    {
        AssistantTaskStatus.SUCCEEDED.value,
        AssistantTaskStatus.FAILED.value,
        AssistantTaskStatus.CANCELLED.value,
    }
)
_FRAGMENT_KINDS = frozenset({"text", "reasoning", "diagnostic"})
_MESSAGE_ROLES = frozenset({"user", "assistant", "system_diagnostic"})
_MESSAGE_STATUSES = frozenset({"committed", "complete", "failed", "cancelled"})


class AssistantRepositoryError(RuntimeError):
    """Base error for durable Assistant history."""


class AssistantConflictError(AssistantRepositoryError):
    """An immutable identity or idempotency key has conflicting content."""


class AssistantIdentityError(AssistantConflictError):
    """A Work merge chain or document identity is invalid."""


class AssistantStateError(AssistantConflictError):
    """A lifecycle transition would rewrite a terminal fact."""


class AssistantSequenceError(AssistantConflictError):
    """An append-only sequence contains a gap or changes an existing item."""


@dataclass(frozen=True, slots=True)
class ContextSnapshotDraft:
    """Validated immutable input produced by the explicit context builder."""

    work_id: str
    document_version_id: str
    document_content_sha256: str
    scope: ContextScope
    page_numbers: tuple[int, ...]
    selection: TextSelectionContext | None
    annotation_ids: tuple[str, ...]
    section_fallback: bool
    extraction_version: str | None
    evidence: dict[str, Any]
    evidence_sha256: str
    warnings: tuple[str, ...]
    snapshot_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ContextScope):
            raise ValueError("scope must be a ContextScope")
        if not isinstance(self.section_fallback, bool):
            raise ValueError("section_fallback must be a boolean")
        if self.selection is not None and not isinstance(
            self.selection,
            TextSelectionContext,
        ):
            raise ValueError("selection must be a TextSelectionContext")
        work_id = _identifier(self.work_id, name="work_id", maximum=80)
        document_id = _identifier(
            self.document_version_id,
            name="document_version_id",
            maximum=80,
        )
        document_hash = _hash(self.document_content_sha256, name="document_content_sha256")
        evidence_hash = _hash(self.evidence_sha256, name="evidence_sha256")
        snapshot_hash = _hash(self.snapshot_sha256, name="snapshot_sha256")
        pages = tuple(self.page_numbers)
        if (
            len(pages) > 2_000
            or any(isinstance(page, bool) or not isinstance(page, int) for page in pages)
            or any(page < 1 or page > 1_000_000 for page in pages)
            or pages != tuple(sorted(set(pages)))
        ):
            raise ValueError("page_numbers must be unique, sorted positive page numbers")
        annotations = tuple(
            _identifier(item, name="annotation_id", maximum=80) for item in self.annotation_ids
        )
        if len(annotations) > 500 or annotations != tuple(sorted(set(annotations))):
            raise ValueError("annotation_ids must be unique, sorted, and contain at most 500 IDs")
        warnings = tuple(_bounded_text(item, name="warning", maximum=500) for item in self.warnings)
        if len(warnings) > 100 or len(warnings) != len(set(warnings)):
            raise ValueError("warnings must be unique and contain at most 100 values")
        extraction_version = (
            _identifier(self.extraction_version, name="extraction_version", maximum=80)
            if self.extraction_version is not None
            else None
        )
        evidence = _json_object(self.evidence, name="evidence")
        if evidence.get("evidence_sha256") != evidence_hash:
            raise ValueError("evidence_sha256 does not match the evidence payload identity")
        evidence_work = evidence.get("work")
        evidence_document = evidence.get("document")
        if not isinstance(evidence_work, dict) or evidence_work.get("work_id") != work_id:
            raise ValueError("evidence Work identity does not match the snapshot")
        if (
            not isinstance(evidence_document, dict)
            or evidence_document.get("document_version_id") != document_id
            or evidence_document.get("content_sha256") != document_hash
        ):
            raise ValueError("evidence document identity does not match the snapshot")
        _validate_snapshot_scope(
            scope=self.scope,
            pages=pages,
            selection=self.selection,
            annotation_ids=annotations,
            section_fallback=self.section_fallback,
        )

        object.__setattr__(self, "work_id", work_id)
        object.__setattr__(self, "document_version_id", document_id)
        object.__setattr__(self, "document_content_sha256", document_hash)
        object.__setattr__(self, "page_numbers", pages)
        object.__setattr__(self, "annotation_ids", annotations)
        object.__setattr__(self, "extraction_version", extraction_version)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "evidence_sha256", evidence_hash)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "snapshot_sha256", snapshot_hash)
        expected = context_snapshot_fingerprint(self)
        if snapshot_hash != expected:
            raise ValueError("snapshot_sha256 does not match the canonical context payload")

    @classmethod
    def create(
        cls,
        *,
        work_id: str,
        document_version_id: str,
        document_content_sha256: str,
        scope: ContextScope,
        page_numbers: tuple[int, ...],
        selection: TextSelectionContext | None,
        annotation_ids: tuple[str, ...],
        section_fallback: bool,
        extraction_version: str | None,
        evidence: dict[str, Any],
        evidence_sha256: str,
        warnings: tuple[str, ...],
    ) -> ContextSnapshotDraft:
        payload = _context_snapshot_payload(
            work_id=work_id,
            document_version_id=document_version_id,
            document_content_sha256=document_content_sha256,
            scope=scope,
            page_numbers=page_numbers,
            selection=selection,
            annotation_ids=annotation_ids,
            section_fallback=section_fallback,
            extraction_version=extraction_version,
            evidence=evidence,
            evidence_sha256=evidence_sha256,
            warnings=warnings,
        )
        fingerprint = sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        return cls(snapshot_sha256=fingerprint, **payload)


@dataclass(frozen=True, slots=True)
class AssistantSession:
    id: str
    work_id: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AssistantContextSnapshot:
    id: str
    session_id: str
    work_id: str
    document_version_id: str
    document_content_sha256: str
    scope: ContextScope
    page_numbers: tuple[int, ...]
    selection: dict[str, Any] | None
    annotation_ids: tuple[str, ...]
    section_fallback: bool
    extraction_version: str | None
    evidence: dict[str, Any]
    evidence_sha256: str
    warnings: tuple[str, ...]
    snapshot_sha256: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AssistantTask:
    id: str
    session_id: str
    context_snapshot_id: str
    retry_of_task_id: str | None
    mode: AssistantMode
    status: AssistantTaskStatus
    network_policy: NetworkPolicy
    backend_id: str
    requested_model_id: str | None
    reasoning_effort: ReasoningEffort
    service_tier: ServiceTier
    output_language: AssistantOutputLanguage
    idempotency_key: str
    command_sha256: str
    job_id: str
    error_code: str | None
    diagnostic: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AssistantAgentRun:
    id: str
    task_id: str
    attempt: int
    status: AssistantTaskStatus
    backend_id: str
    requested_model_id: str | None
    resolved_model_id: str | None
    reasoning_effort: ReasoningEffort
    service_tier: ServiceTier
    request_sha256: str | None
    prompt_sha256: str | None
    raw_output_sha256: str | None
    usage: dict[str, Any]
    repair_count: int
    error_code: str | None
    diagnostic: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    id: str
    session_id: str
    task_id: str
    sequence: int
    role: AssistantMessageRole
    status: AssistantMessageStatus
    content: str
    citations: tuple[dict[str, Any], ...]
    evidence_manifest: dict[str, Any]
    job_id: str
    reply_to_message_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AssistantFragment:
    task_id: str
    sequence: int
    run_id: str
    kind: AssistantFragmentKind
    text: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AssistantCheckpoint:
    id: str
    session_id: str
    source_start_sequence: int
    through_sequence: int
    summary: str
    summary_sha256: str
    backend_id: str
    model_id: str | None
    estimated_tokens: int
    estimated_characters: int
    created_at: datetime


class AssistantRepository:
    """Append-oriented persistence with canonical Work and task invariants."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def canonical_work_id(self, work_id: str) -> str:
        with self._sessions() as session:
            return self._resolve_work(session, work_id).id

    def session_for_work(self, work_id: str) -> AssistantSession:
        for attempt in range(3):
            try:
                with self._sessions() as session:
                    canonical = self._resolve_work(session, work_id)
                    record = session.scalar(
                        select(AssistantSessionRecord).where(
                            AssistantSessionRecord.work_id == canonical.id
                        )
                    )
                    if record is None:
                        record = AssistantSessionRecord(work_id=canonical.id)
                        session.add(record)
                        session.commit()
                        session.refresh(record)
                    return _session_domain(record)
            except IntegrityError:
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    def find_session_for_work(self, work_id: str) -> AssistantSession | None:
        """Return an existing canonical session without mutating on a read."""

        with self._sessions() as session:
            canonical = self._resolve_work(session, work_id)
            record = session.scalar(
                select(AssistantSessionRecord).where(AssistantSessionRecord.work_id == canonical.id)
            )
            return _session_domain(record) if record is not None else None

    def session(self, session_id: str) -> AssistantSession | None:
        """Return a session by its stable ID for API compatibility views."""

        identifier = _identifier(session_id, name="session_id", maximum=80)
        with self._sessions() as session:
            record = session.get(AssistantSessionRecord, identifier)
            return _session_domain(record) if record is not None else None

    def document_version_id_for_work(
        self,
        work_id: str,
        requested_document_version_id: str | None = None,
    ) -> str:
        """Resolve the explicit or representative document for a canonical Work."""

        with self._sessions() as session:
            canonical = self._resolve_work(session, work_id)
            if requested_document_version_id is not None:
                document_id = _identifier(
                    requested_document_version_id,
                    name="document_version_id",
                    maximum=80,
                )
                document = session.get(DocumentVersionRecord, document_id)
                if document is None:
                    raise KeyError(document_id)
                document_work = self._resolve_work(session, document.work_id)
                if document_work.id != canonical.id:
                    raise AssistantIdentityError(
                        "document version belongs to another canonical Work"
                    )
                return document.id
            document = session.scalar(
                select(DocumentVersionRecord)
                .where(DocumentVersionRecord.work_id == canonical.id)
                .order_by(
                    DocumentVersionRecord.completeness.desc(),
                    DocumentVersionRecord.source_updated_at.desc(),
                    DocumentVersionRecord.created_at.desc(),
                    DocumentVersionRecord.id,
                )
                .limit(1)
            )
            if document is None:
                raise KeyError(canonical.id)
            return document.id

    def freeze_context(
        self,
        work_id: str,
        snapshot: ContextSnapshotDraft,
    ) -> AssistantContextSnapshot:
        """Persist immutable reader evidence without creating a task or Job."""

        if not isinstance(snapshot, ContextSnapshotDraft):
            raise ValueError("snapshot must be a ContextSnapshotDraft")
        for attempt in range(3):
            try:
                with self._sessions() as session:
                    canonical = self._resolve_work(session, work_id)
                    self._validate_snapshot_identity(session, canonical, snapshot)
                    assistant_session = session.scalar(
                        select(AssistantSessionRecord).where(
                            AssistantSessionRecord.work_id == canonical.id
                        )
                    )
                    if assistant_session is None:
                        assistant_session = AssistantSessionRecord(work_id=canonical.id)
                        session.add(assistant_session)
                        session.flush()
                    record = self._snapshot_record(session, assistant_session, snapshot)
                    assistant_session.updated_at = utc_now()
                    session.commit()
                    session.refresh(record)
                    return _snapshot_domain(record)
            except IntegrityError:
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    def create_task(
        self,
        work_id: str,
        command: AssistantTaskCommand,
        snapshot: ContextSnapshotDraft,
        *,
        idempotency_key: str,
        retry_of_task_id: str | None = None,
    ) -> AssistantTask:
        if not isinstance(command, AssistantTaskCommand):
            raise ValueError("command must be an AssistantTaskCommand")
        if not isinstance(snapshot, ContextSnapshotDraft):
            raise ValueError("snapshot must be a ContextSnapshotDraft")
        key = _identifier(idempotency_key, name="idempotency_key", maximum=160)
        retry_id = (
            _identifier(retry_of_task_id, name="retry_of_task_id", maximum=80)
            if retry_of_task_id is not None
            else None
        )
        for attempt in range(3):
            try:
                return self._create_task_once(
                    work_id,
                    command,
                    snapshot,
                    idempotency_key=key,
                    retry_of_task_id=retry_id,
                )
            except IntegrityError:
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    def _create_task_once(
        self,
        work_id: str,
        command: AssistantTaskCommand,
        snapshot: ContextSnapshotDraft,
        *,
        idempotency_key: str,
        retry_of_task_id: str | None,
    ) -> AssistantTask:
        with self._sessions() as session:
            canonical = self._resolve_work(session, work_id)
            self._validate_snapshot(session, canonical, command, snapshot)
            command_sha256 = _command_fingerprint(
                canonical.id,
                command,
                snapshot,
                retry_of_task_id=retry_of_task_id,
            )
            existing = session.scalar(
                select(AssistantTaskRecord).where(
                    AssistantTaskRecord.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                if existing.command_sha256 != command_sha256:
                    raise AssistantConflictError(
                        "idempotency key is bound to a different Assistant command"
                    )
                return _task_domain(existing)

            existing_job = session.scalar(
                select(JobRecord).where(JobRecord.idempotency_key == idempotency_key)
            )
            if existing_job is not None:
                raise AssistantConflictError(
                    "idempotency key is already bound to another durable job"
                )
            assistant_session = session.scalar(
                select(AssistantSessionRecord).where(AssistantSessionRecord.work_id == canonical.id)
            )
            if assistant_session is None:
                assistant_session = AssistantSessionRecord(work_id=canonical.id)
                session.add(assistant_session)
                session.flush()

            snapshot_record = self._snapshot_record(
                session,
                assistant_session,
                snapshot,
            )
            retry_record: AssistantTaskRecord | None = None
            if retry_of_task_id is not None:
                retry_record = session.get(AssistantTaskRecord, retry_of_task_id)
                if retry_record is None:
                    raise KeyError(retry_of_task_id)
                if retry_record.session_id != assistant_session.id:
                    raise AssistantConflictError("retry task belongs to another Assistant session")
                if retry_record.status not in {
                    AssistantTaskStatus.FAILED.value,
                    AssistantTaskStatus.CANCELLED.value,
                }:
                    raise AssistantStateError("only a failed or cancelled task can be retried")

            task_id = str(uuid4())
            job = JobRecord(
                kind="assistant.reply",
                idempotency_key=idempotency_key,
                payload={
                    "task_id": task_id,
                    "session_id": assistant_session.id,
                    "context_snapshot_id": snapshot_record.id,
                    "command_sha256": command_sha256,
                    "mode": command.mode.value,
                },
            )
            session.add(job)
            session.flush()
            task = AssistantTaskRecord(
                id=task_id,
                session_id=assistant_session.id,
                context_snapshot_id=snapshot_record.id,
                retry_of_task_id=retry_record.id if retry_record is not None else None,
                mode=command.mode.value,
                status=AssistantTaskStatus.QUEUED.value,
                network_policy=command.network_policy.value,
                backend_id=command.agent.backend_id,
                requested_model_id=command.agent.model_id,
                reasoning_effort=command.agent.reasoning_effort.value,
                service_tier=command.agent.service_tier.value,
                output_language=command.output_language,
                idempotency_key=idempotency_key,
                command_sha256=command_sha256,
                job_id=job.id,
            )
            session.add(task)
            session.flush()
            user_message = AssistantMessageRecord(
                session_id=assistant_session.id,
                task_id=task.id,
                sequence=self._next_message_sequence(session, assistant_session.id),
                role="user",
                status="committed",
                content=command.message,
                citations=[],
                evidence_manifest=_snapshot_manifest(snapshot_record),
                job_id=job.id,
                reply_to_message_id=None,
            )
            session.add(user_message)
            assistant_session.updated_at = utc_now()
            session.commit()
            session.refresh(task)
            return _task_domain(task)

    def task(self, task_id: str) -> AssistantTask:
        with self._sessions() as session:
            record = session.get(AssistantTaskRecord, task_id)
            if record is None:
                raise KeyError(task_id)
            return _task_domain(record)

    def task_for_job(self, job_id: str) -> AssistantTask | None:
        with self._sessions() as session:
            record = session.scalar(
                select(AssistantTaskRecord).where(AssistantTaskRecord.job_id == job_id)
            )
            return _task_domain(record) if record is not None else None

    def tasks(self, session_id: str) -> tuple[AssistantTask, ...]:
        with self._sessions() as session:
            if session.get(AssistantSessionRecord, session_id) is None:
                raise KeyError(session_id)
            records = session.scalars(
                select(AssistantTaskRecord)
                .where(AssistantTaskRecord.session_id == session_id)
                .order_by(AssistantTaskRecord.created_at, AssistantTaskRecord.id)
            )
            return tuple(_task_domain(item) for item in records)

    def snapshot(self, snapshot_id: str) -> AssistantContextSnapshot:
        with self._sessions() as session:
            record = session.get(AssistantContextSnapshotRecord, snapshot_id)
            if record is None:
                raise KeyError(snapshot_id)
            return _snapshot_domain(record)

    def messages_for_work(self, work_id: str) -> tuple[AssistantMessage, ...]:
        with self._sessions() as session:
            canonical = self._resolve_work(session, work_id)
            assistant_session = session.scalar(
                select(AssistantSessionRecord).where(AssistantSessionRecord.work_id == canonical.id)
            )
            if assistant_session is None:
                return ()
            return self._messages(session, assistant_session.id)

    def messages(self, session_id: str) -> tuple[AssistantMessage, ...]:
        with self._sessions() as session:
            if session.get(AssistantSessionRecord, session_id) is None:
                raise KeyError(session_id)
            return self._messages(session, session_id)

    def append_message(
        self,
        *,
        task_id: str,
        role: AssistantMessageRole,
        status: AssistantMessageStatus,
        content: str,
        citations: tuple[dict[str, object], ...],
        evidence_manifest: dict[str, object],
        reply_to_message_id: str | None,
    ) -> AssistantMessage:
        normalized = _bounded_text(content, name="message content", maximum=50_000)
        if role not in _MESSAGE_ROLES or status not in _MESSAGE_STATUSES:
            raise ValueError("message role or status is unsupported")
        citation_payload = _json_array(citations, name="citations", maximum=100)
        manifest = _json_object(evidence_manifest, name="evidence_manifest")
        with self._sessions() as session:
            task = session.get(AssistantTaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status in _TERMINAL_STATUSES:
                raise AssistantStateError("cannot append to a completed task")
            record = AssistantMessageRecord(
                session_id=task.session_id,
                task_id=task.id,
                sequence=self._next_message_sequence(session, task.session_id),
                role=role,
                status=status,
                content=normalized,
                citations=citation_payload,
                evidence_manifest=manifest,
                job_id=task.job_id,
                reply_to_message_id=reply_to_message_id,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return _message_domain(record)

    def create_run(self, task_id: str) -> AssistantAgentRun:
        with self._sessions() as session:
            task = session.get(AssistantTaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status in _TERMINAL_STATUSES:
                raise AssistantStateError("cannot create a run for a completed task")
            latest = session.scalar(
                select(AssistantAgentRunRecord)
                .where(AssistantAgentRunRecord.task_id == task.id)
                .order_by(AssistantAgentRunRecord.attempt.desc())
                .limit(1)
            )
            if latest is not None and latest.status in {
                AssistantTaskStatus.QUEUED.value,
                AssistantTaskStatus.RUNNING.value,
            }:
                return _run_domain(latest)
            if latest is not None and latest.status == AssistantTaskStatus.SUCCEEDED.value:
                raise AssistantStateError("a succeeded run cannot be replaced")
            record = AssistantAgentRunRecord(
                task_id=task.id,
                attempt=(latest.attempt + 1 if latest is not None else 1),
                status=AssistantTaskStatus.QUEUED.value,
                backend_id=task.backend_id,
                requested_model_id=task.requested_model_id,
                reasoning_effort=task.reasoning_effort,
                service_tier=task.service_tier,
                usage={},
                repair_count=0,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return _run_domain(record)

    def start_run(
        self,
        run_id: str,
        *,
        request_sha256: str,
        prompt_sha256: str,
    ) -> AssistantAgentRun:
        request_hash = _hash(request_sha256, name="request_sha256")
        prompt_hash = _hash(prompt_sha256, name="prompt_sha256")
        with self._sessions() as session:
            run = session.get(AssistantAgentRunRecord, run_id)
            if run is None:
                raise KeyError(run_id)
            if run.status in _TERMINAL_STATUSES:
                raise AssistantStateError("cannot start a completed run")
            if run.status == AssistantTaskStatus.RUNNING.value:
                if run.request_sha256 != request_hash or run.prompt_sha256 != prompt_hash:
                    raise AssistantStateError("running request identity cannot be changed")
                return _run_domain(run)
            task = session.get(AssistantTaskRecord, run.task_id)
            if task is None:  # pragma: no cover - protected by the foreign key
                raise KeyError(run.task_id)
            if task.status in _TERMINAL_STATUSES:
                raise AssistantStateError("cannot start a run for a completed task")
            now = utc_now()
            run.status = AssistantTaskStatus.RUNNING.value
            run.request_sha256 = request_hash
            run.prompt_sha256 = prompt_hash
            run.started_at = now
            task.status = AssistantTaskStatus.RUNNING.value
            task.started_at = task.started_at or now
            task.updated_at = now
            job = session.get(JobRecord, task.job_id)
            if job is None:  # pragma: no cover - protected by the foreign key
                raise KeyError(task.job_id)
            if job.status in _TERMINAL_STATUSES:
                raise AssistantStateError("cannot start a run for a completed Job")
            job.status = "running"
            job.progress = max(job.progress, 1)
            session.commit()
            session.refresh(run)
            return _run_domain(run)

    def finish_run(
        self,
        run_id: str,
        *,
        status: AssistantTerminalStatus,
        resolved_model_id: str | None,
        raw_output_sha256: str | None,
        usage: dict[str, object],
        repair_count: int = 0,
        error_code: str | None = None,
        diagnostic: str | None = None,
    ) -> AssistantAgentRun:
        if status not in _TERMINAL_STATUSES:
            raise ValueError("run status must be terminal")
        model_id = (
            _identifier(resolved_model_id, name="resolved_model_id", maximum=200)
            if resolved_model_id is not None
            else None
        )
        raw_hash = (
            _hash(raw_output_sha256, name="raw_output_sha256")
            if raw_output_sha256 is not None
            else None
        )
        usage_payload = _json_object(usage, name="usage")
        if repair_count not in {0, 1}:
            raise ValueError("repair_count must be zero or one")
        code = (
            _identifier(error_code, name="error_code", maximum=80)
            if error_code is not None
            else None
        )
        details = (
            redact_diagnostic(
                _bounded_text(diagnostic, name="diagnostic", maximum=20_000),
                limit=20_000,
            )
            if diagnostic is not None
            else None
        )
        if status == AssistantTaskStatus.SUCCEEDED.value:
            if model_id is None or raw_hash is None or code is not None or details is not None:
                raise ValueError(
                    "a succeeded run requires resolved model/output and cannot contain an error"
                )
        elif status == AssistantTaskStatus.FAILED.value and code is None:
            raise ValueError("a failed run requires error_code")

        with self._sessions() as session:
            run = session.get(AssistantAgentRunRecord, run_id)
            if run is None:
                raise KeyError(run_id)
            desired = (
                status,
                model_id,
                raw_hash,
                usage_payload,
                repair_count,
                code,
                details,
            )
            if run.status in _TERMINAL_STATUSES:
                existing = (
                    run.status,
                    run.resolved_model_id,
                    run.raw_output_sha256,
                    dict(run.usage),
                    run.repair_count,
                    run.error_code,
                    run.diagnostic,
                )
                if existing == desired:
                    return _run_domain(run)
                raise AssistantStateError("completed run cannot be rewritten")
            if run.status != AssistantTaskStatus.RUNNING.value:
                raise AssistantStateError("run must be running before it can finish")
            run.status = status
            run.resolved_model_id = model_id
            run.raw_output_sha256 = raw_hash
            run.usage = usage_payload
            run.repair_count = repair_count
            run.error_code = code
            run.diagnostic = details
            run.completed_at = utc_now()
            session.commit()
            session.refresh(run)
            return _run_domain(run)

    def run(self, run_id: str) -> AssistantAgentRun:
        with self._sessions() as session:
            record = session.get(AssistantAgentRunRecord, run_id)
            if record is None:
                raise KeyError(run_id)
            return _run_domain(record)

    def runs(self, task_id: str) -> tuple[AssistantAgentRun, ...]:
        with self._sessions() as session:
            if session.get(AssistantTaskRecord, task_id) is None:
                raise KeyError(task_id)
            records = session.scalars(
                select(AssistantAgentRunRecord)
                .where(AssistantAgentRunRecord.task_id == task_id)
                .order_by(AssistantAgentRunRecord.attempt)
            )
            return tuple(_run_domain(item) for item in records)

    def append_fragment(
        self,
        *,
        task_id: str,
        run_id: str,
        sequence: int,
        kind: str,
        text: str,
    ) -> AssistantFragment:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("fragment sequence must be a non-negative integer")
        if kind not in _FRAGMENT_KINDS:
            raise ValueError("fragment kind is unsupported")
        if not isinstance(text, str) or not text or len(text) > 1_000_000:
            raise ValueError("fragment text must contain between 1 and 1,000,000 characters")
        persisted_text = redact_diagnostic(text, limit=1_000_000) if kind == "diagnostic" else text
        with self._sessions() as session:
            existing = session.get(AssistantFragmentRecord, (task_id, sequence))
            if existing is not None:
                if (
                    existing.run_id != run_id
                    or existing.kind != kind
                    or existing.text != persisted_text
                ):
                    raise AssistantConflictError("Assistant fragment identity conflict")
                return _fragment_domain(existing)
            task = session.get(AssistantTaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            run = session.get(AssistantAgentRunRecord, run_id)
            if run is None or run.task_id != task.id:
                raise AssistantConflictError("fragment run does not belong to the task")
            if task.status != AssistantTaskStatus.RUNNING.value or run.status != "running":
                raise AssistantStateError("fragments can only append to a running task and run")
            current = session.scalar(
                select(func.max(AssistantFragmentRecord.sequence)).where(
                    AssistantFragmentRecord.task_id == task.id
                )
            )
            expected = int(current) + 1 if current is not None else 0
            if sequence != expected:
                raise AssistantSequenceError(
                    f"fragment sequence must be contiguous; expected {expected}"
                )
            record = AssistantFragmentRecord(
                task_id=task.id,
                sequence=sequence,
                run_id=run.id,
                kind=kind,
                text=persisted_text,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return _fragment_domain(record)

    def fragment(self, task_id: str, sequence: int) -> AssistantFragment | None:
        with self._sessions() as session:
            record = session.get(AssistantFragmentRecord, (task_id, sequence))
            return _fragment_domain(record) if record is not None else None

    def fragments(
        self,
        task_id: str,
        *,
        after_sequence: int = -1,
    ) -> tuple[AssistantFragment, ...]:
        with self._sessions() as session:
            records = session.scalars(
                select(AssistantFragmentRecord)
                .where(
                    AssistantFragmentRecord.task_id == task_id,
                    AssistantFragmentRecord.sequence > after_sequence,
                )
                .order_by(AssistantFragmentRecord.sequence)
            )
            return tuple(_fragment_domain(item) for item in records)

    def commit_assistant_message(
        self,
        *,
        task_id: str,
        content: str,
        citations: tuple[dict[str, object], ...],
        evidence_manifest: dict[str, object],
    ) -> AssistantMessage:
        normalized = _bounded_text(content, name="assistant message", maximum=200_000)
        citation_payload = _json_array(citations, name="citations", maximum=200)
        manifest = _json_object(evidence_manifest, name="evidence_manifest")
        with self._sessions() as session:
            task = session.get(AssistantTaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            existing = session.scalar(
                select(AssistantMessageRecord).where(
                    AssistantMessageRecord.task_id == task.id,
                    AssistantMessageRecord.role == "assistant",
                )
            )
            if existing is not None:
                if (
                    existing.content == normalized
                    and list(existing.citations) == citation_payload
                    and dict(existing.evidence_manifest) == manifest
                ):
                    return _message_domain(existing)
                raise AssistantStateError("completed task answer cannot be rewritten")
            if task.status in _TERMINAL_STATUSES:
                raise AssistantStateError("completed task cannot be rewritten")
            snapshot = session.get(AssistantContextSnapshotRecord, task.context_snapshot_id)
            if snapshot is None:  # pragma: no cover - protected by the foreign key
                raise KeyError(task.context_snapshot_id)
            _validate_manifest(snapshot, manifest)
            succeeded_run = session.scalar(
                select(AssistantAgentRunRecord.id)
                .where(
                    AssistantAgentRunRecord.task_id == task.id,
                    AssistantAgentRunRecord.status == AssistantTaskStatus.SUCCEEDED.value,
                )
                .limit(1)
            )
            if succeeded_run is None:
                raise AssistantStateError("Assistant answer requires a succeeded Agent run")
            user_message = session.scalar(
                select(AssistantMessageRecord).where(
                    AssistantMessageRecord.task_id == task.id,
                    AssistantMessageRecord.role == "user",
                )
            )
            if user_message is None:  # pragma: no cover - created in the same task transaction
                raise AssistantStateError("Assistant task has no committed user message")
            record = AssistantMessageRecord(
                session_id=task.session_id,
                task_id=task.id,
                sequence=self._next_message_sequence(session, task.session_id),
                role="assistant",
                status="complete",
                content=normalized,
                citations=citation_payload,
                evidence_manifest=manifest,
                job_id=task.job_id,
                reply_to_message_id=user_message.id,
            )
            now = utc_now()
            session.add(record)
            task.status = AssistantTaskStatus.SUCCEEDED.value
            task.completed_at = now
            task.updated_at = now
            assistant_session = session.get(AssistantSessionRecord, task.session_id)
            if assistant_session is not None:
                assistant_session.updated_at = now
            job = session.get(JobRecord, task.job_id)
            if job is None:  # pragma: no cover - protected by the foreign key
                raise KeyError(task.job_id)
            job.status = "succeeded"
            job.progress = 100
            job.result = {
                "task_id": task.id,
                "message_id": record.id,
                "context_snapshot_id": snapshot.id,
            }
            job.error_code = None
            job.error_message = None
            session.commit()
            session.refresh(record)
            return _message_domain(record)

    def fail_task(
        self,
        task_id: str,
        *,
        code: str,
        diagnostic: str | None,
    ) -> AssistantTask:
        error_code = _identifier(code, name="error_code", maximum=80)
        details = (
            redact_diagnostic(
                _bounded_text(diagnostic, name="diagnostic", maximum=20_000),
                limit=20_000,
            )
            if diagnostic is not None
            else None
        )
        with self._sessions() as session:
            task = session.get(AssistantTaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status in _TERMINAL_STATUSES:
                if (
                    task.status == AssistantTaskStatus.FAILED.value
                    and task.error_code == error_code
                    and task.diagnostic == details
                ):
                    return _task_domain(task)
                raise AssistantStateError("completed task cannot be rewritten")
            now = utc_now()
            active_runs = session.scalars(
                select(AssistantAgentRunRecord).where(
                    AssistantAgentRunRecord.task_id == task.id,
                    AssistantAgentRunRecord.status.in_(
                        (
                            AssistantTaskStatus.QUEUED.value,
                            AssistantTaskStatus.RUNNING.value,
                        )
                    ),
                )
            )
            for run in active_runs:
                run.status = AssistantTaskStatus.FAILED.value
                run.error_code = error_code
                run.diagnostic = details
                run.completed_at = now
            task.status = AssistantTaskStatus.FAILED.value
            task.error_code = error_code
            task.diagnostic = details
            task.completed_at = now
            task.updated_at = now
            job = session.get(JobRecord, task.job_id)
            if job is None:  # pragma: no cover - protected by the foreign key
                raise KeyError(task.job_id)
            job.status = "failed"
            job.progress = 100
            job.error_code = error_code
            job.error_message = details
            session.commit()
            session.refresh(task)
            return _task_domain(task)

    def cancel_task(
        self,
        task_id: str,
        *,
        diagnostic: str | None,
    ) -> AssistantTask:
        details = (
            redact_diagnostic(
                _bounded_text(diagnostic, name="diagnostic", maximum=20_000),
                limit=20_000,
            )
            if diagnostic is not None
            else None
        )
        with self._sessions() as session:
            task = session.get(AssistantTaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status in _TERMINAL_STATUSES:
                if (
                    task.status == AssistantTaskStatus.CANCELLED.value
                    and task.error_code == "cancelled"
                    and task.diagnostic == details
                ):
                    return _task_domain(task)
                raise AssistantStateError("completed task cannot be rewritten")
            now = utc_now()
            active_runs = session.scalars(
                select(AssistantAgentRunRecord).where(
                    AssistantAgentRunRecord.task_id == task.id,
                    AssistantAgentRunRecord.status.in_(
                        (
                            AssistantTaskStatus.QUEUED.value,
                            AssistantTaskStatus.RUNNING.value,
                        )
                    ),
                )
            )
            for run in active_runs:
                run.status = AssistantTaskStatus.CANCELLED.value
                run.error_code = "cancelled"
                run.diagnostic = details
                run.completed_at = now
            task.status = AssistantTaskStatus.CANCELLED.value
            task.error_code = "cancelled"
            task.diagnostic = details
            task.completed_at = now
            task.updated_at = now
            job = session.get(JobRecord, task.job_id)
            if job is None:  # pragma: no cover - protected by the foreign key
                raise KeyError(task.job_id)
            job.status = "cancelled"
            job.progress = 100
            job.error_code = "cancelled"
            job.error_message = details
            session.commit()
            session.refresh(task)
            return _task_domain(task)

    def checkpoint(
        self,
        *,
        session_id: str,
        source_start_sequence: int,
        through_sequence: int,
        summary: str,
        backend_id: str,
        model_id: str | None,
        estimated_tokens: int,
    ) -> AssistantCheckpoint:
        if (
            isinstance(source_start_sequence, bool)
            or isinstance(through_sequence, bool)
            or source_start_sequence < 1
            or through_sequence < source_start_sequence
        ):
            raise ValueError("checkpoint message range is invalid")
        if isinstance(estimated_tokens, bool) or estimated_tokens < 0:
            raise ValueError("estimated_tokens must be non-negative")
        normalized = _bounded_text(summary, name="checkpoint summary", maximum=50_000)
        backend = _identifier(backend_id, name="backend_id", maximum=80)
        model = (
            _identifier(model_id, name="model_id", maximum=200) if model_id is not None else None
        )
        digest = sha256(normalized.encode("utf-8")).hexdigest()
        with self._sessions() as session:
            if session.get(AssistantSessionRecord, session_id) is None:
                raise KeyError(session_id)
            last_sequence = session.scalar(
                select(func.max(AssistantMessageRecord.sequence)).where(
                    AssistantMessageRecord.session_id == session_id
                )
            )
            if last_sequence is None or through_sequence > int(last_sequence):
                raise AssistantSequenceError("checkpoint range exceeds committed messages")
            existing = session.scalar(
                select(AssistantCheckpointRecord).where(
                    AssistantCheckpointRecord.session_id == session_id,
                    AssistantCheckpointRecord.through_sequence == through_sequence,
                )
            )
            desired = (
                source_start_sequence,
                normalized,
                digest,
                backend,
                model,
                estimated_tokens,
                len(normalized),
            )
            if existing is not None:
                actual = (
                    existing.source_start_sequence,
                    existing.summary,
                    existing.summary_sha256,
                    existing.backend_id,
                    existing.model_id,
                    existing.estimated_tokens,
                    existing.estimated_characters,
                )
                if actual == desired:
                    return _checkpoint_domain(existing)
                raise AssistantConflictError("checkpoint identity conflict")
            record = AssistantCheckpointRecord(
                session_id=session_id,
                source_start_sequence=source_start_sequence,
                through_sequence=through_sequence,
                summary=normalized,
                summary_sha256=digest,
                backend_id=backend,
                model_id=model,
                estimated_tokens=estimated_tokens,
                estimated_characters=len(normalized),
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return _checkpoint_domain(record)

    def latest_checkpoint(self, session_id: str) -> AssistantCheckpoint | None:
        with self._sessions() as session:
            record = session.scalar(
                select(AssistantCheckpointRecord)
                .where(AssistantCheckpointRecord.session_id == session_id)
                .order_by(AssistantCheckpointRecord.through_sequence.desc())
                .limit(1)
            )
            return _checkpoint_domain(record) if record is not None else None

    def checkpoints(self, session_id: str) -> tuple[AssistantCheckpoint, ...]:
        """Return every visible checkpoint without removing original messages."""

        identifier = _identifier(session_id, name="session_id", maximum=80)
        with self._sessions() as session:
            if session.get(AssistantSessionRecord, identifier) is None:
                raise KeyError(identifier)
            records = session.scalars(
                select(AssistantCheckpointRecord)
                .where(AssistantCheckpointRecord.session_id == identifier)
                .order_by(AssistantCheckpointRecord.through_sequence)
            )
            return tuple(_checkpoint_domain(record) for record in records)

    @staticmethod
    def _messages(session: Session, session_id: str) -> tuple[AssistantMessage, ...]:
        records = session.scalars(
            select(AssistantMessageRecord)
            .where(AssistantMessageRecord.session_id == session_id)
            .order_by(AssistantMessageRecord.sequence)
        )
        return tuple(_message_domain(item) for item in records)

    @staticmethod
    def _next_message_sequence(session: Session, session_id: str) -> int:
        current = session.scalar(
            select(func.max(AssistantMessageRecord.sequence)).where(
                AssistantMessageRecord.session_id == session_id
            )
        )
        return int(current or 0) + 1

    @staticmethod
    def _resolve_work(session: Session, work_id: str) -> WorkRecord:
        requested = _identifier(work_id, name="work_id", maximum=80)
        current_id = requested
        visited: set[str] = set()
        while True:
            if current_id in visited:
                raise AssistantIdentityError("Work merge chain contains a cycle")
            visited.add(current_id)
            work = session.get(WorkRecord, current_id)
            if work is None:
                raise KeyError(requested)
            if work.status == "active" and work.merged_into_work_id is None:
                return work
            if work.status != "merged" or not work.merged_into_work_id:
                raise AssistantIdentityError("Work merge state is invalid")
            current_id = work.merged_into_work_id

    def _validate_snapshot(
        self,
        session: Session,
        canonical: WorkRecord,
        command: AssistantTaskCommand,
        snapshot: ContextSnapshotDraft,
    ) -> None:
        self._validate_snapshot_identity(session, canonical, snapshot)
        selection = command.context
        if selection.document_version_id != snapshot.document_version_id:
            raise AssistantConflictError("command and snapshot select different documents")
        if selection.scope is not snapshot.scope:
            raise AssistantConflictError("command and snapshot select different context scopes")
        if selection.scope is ContextScope.SELECTION and selection.selection != snapshot.selection:
            raise AssistantConflictError("command and snapshot contain different text selections")
        if selection.scope is ContextScope.CURRENT_PAGE and snapshot.page_numbers != (
            selection.page_number,
        ):
            raise AssistantConflictError("current-page snapshot must bind the requested page")
        if selection.scope is ContextScope.CURRENT_SECTION and selection.page_number not in set(
            snapshot.page_numbers
        ):
            raise AssistantConflictError("section snapshot does not contain the requested page")
        if selection.scope is ContextScope.ANNOTATIONS and (
            snapshot.annotation_ids != selection.annotation_ids
        ):
            raise AssistantConflictError("command and snapshot select different annotations")

    def _validate_snapshot_identity(
        self,
        session: Session,
        canonical: WorkRecord,
        snapshot: ContextSnapshotDraft,
    ) -> None:
        if snapshot.snapshot_sha256 != context_snapshot_fingerprint(snapshot):
            raise AssistantConflictError("context snapshot hash is invalid")
        if snapshot.work_id != canonical.id:
            raise AssistantIdentityError("context snapshot changed canonical Work identity")
        document = session.get(DocumentVersionRecord, snapshot.document_version_id)
        if document is None:
            raise KeyError(snapshot.document_version_id)
        document_work = self._resolve_work(session, document.work_id)
        if document_work.id != canonical.id:
            raise AssistantIdentityError("context document belongs to another Work")
        if document.content_sha256 != snapshot.document_content_sha256:
            raise AssistantIdentityError("context document content hash changed")

    @staticmethod
    def _snapshot_record(
        session: Session,
        assistant_session: AssistantSessionRecord,
        draft: ContextSnapshotDraft,
    ) -> AssistantContextSnapshotRecord:
        existing = session.scalar(
            select(AssistantContextSnapshotRecord).where(
                AssistantContextSnapshotRecord.snapshot_sha256 == draft.snapshot_sha256
            )
        )
        if existing is not None:
            if existing.session_id != assistant_session.id or not _snapshot_matches(
                existing,
                draft,
            ):
                raise AssistantConflictError("context snapshot hash identity conflict")
            return existing
        record = AssistantContextSnapshotRecord(
            session_id=assistant_session.id,
            work_id=draft.work_id,
            document_version_id=draft.document_version_id,
            document_content_sha256=draft.document_content_sha256,
            scope=draft.scope.value,
            page_numbers=list(draft.page_numbers),
            selection=(
                draft.selection.model_dump(mode="json") if draft.selection is not None else None
            ),
            annotation_ids=list(draft.annotation_ids),
            section_fallback=draft.section_fallback,
            extraction_version=draft.extraction_version,
            evidence=draft.evidence,
            evidence_sha256=draft.evidence_sha256,
            warnings=list(draft.warnings),
            snapshot_sha256=draft.snapshot_sha256,
        )
        session.add(record)
        session.flush()
        return record


def context_snapshot_fingerprint(snapshot: ContextSnapshotDraft) -> str:
    payload = _context_snapshot_payload(
        work_id=snapshot.work_id,
        document_version_id=snapshot.document_version_id,
        document_content_sha256=snapshot.document_content_sha256,
        scope=snapshot.scope,
        page_numbers=snapshot.page_numbers,
        selection=snapshot.selection,
        annotation_ids=snapshot.annotation_ids,
        section_fallback=snapshot.section_fallback,
        extraction_version=snapshot.extraction_version,
        evidence=snapshot.evidence,
        evidence_sha256=snapshot.evidence_sha256,
        warnings=snapshot.warnings,
    )
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _context_snapshot_payload(
    *,
    work_id: str,
    document_version_id: str,
    document_content_sha256: str,
    scope: ContextScope,
    page_numbers: tuple[int, ...],
    selection: TextSelectionContext | None,
    annotation_ids: tuple[str, ...],
    section_fallback: bool,
    extraction_version: str | None,
    evidence: dict[str, Any],
    evidence_sha256: str,
    warnings: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "work_id": work_id,
        "document_version_id": document_version_id,
        "document_content_sha256": document_content_sha256,
        "scope": scope,
        "page_numbers": tuple(page_numbers),
        "selection": selection,
        "annotation_ids": tuple(annotation_ids),
        "section_fallback": section_fallback,
        "extraction_version": extraction_version,
        "evidence": evidence,
        "evidence_sha256": evidence_sha256,
        "warnings": tuple(warnings),
    }


def _validate_snapshot_scope(
    *,
    scope: ContextScope,
    pages: tuple[int, ...],
    selection: TextSelectionContext | None,
    annotation_ids: tuple[str, ...],
    section_fallback: bool,
) -> None:
    if section_fallback and scope is not ContextScope.CURRENT_SECTION:
        raise ValueError("section_fallback is valid only for current_section scope")
    if scope is ContextScope.SELECTION:
        if selection is None or pages != (selection.page_number,) or annotation_ids:
            raise ValueError("selection snapshot must bind exactly the selected page")
        return
    if selection is not None:
        raise ValueError("selection data is valid only for selection scope")
    if scope is ContextScope.CURRENT_PAGE and len(pages) != 1:
        raise ValueError("current_page snapshot must bind exactly one page")
    if scope is ContextScope.CURRENT_SECTION and not pages:
        raise ValueError("current_section snapshot must bind at least one page")
    if scope is ContextScope.ANNOTATIONS and not annotation_ids:
        raise ValueError("annotations snapshot requires annotation IDs")
    if scope is not ContextScope.ANNOTATIONS and annotation_ids:
        raise ValueError("annotation IDs are valid only for annotations scope")


def _command_fingerprint(
    canonical_work_id: str,
    command: AssistantTaskCommand,
    snapshot: ContextSnapshotDraft,
    *,
    retry_of_task_id: str | None,
) -> str:
    payload = {
        "canonical_work_id": canonical_work_id,
        "command": command.model_dump(mode="json"),
        "snapshot_sha256": snapshot.snapshot_sha256,
        "retry_of_task_id": retry_of_task_id,
    }
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _snapshot_matches(
    record: AssistantContextSnapshotRecord,
    draft: ContextSnapshotDraft,
) -> bool:
    return (
        record.work_id == draft.work_id
        and record.document_version_id == draft.document_version_id
        and record.document_content_sha256 == draft.document_content_sha256
        and record.scope == draft.scope.value
        and tuple(record.page_numbers) == draft.page_numbers
        and record.selection
        == (draft.selection.model_dump(mode="json") if draft.selection is not None else None)
        and tuple(record.annotation_ids) == draft.annotation_ids
        and record.section_fallback is draft.section_fallback
        and record.extraction_version == draft.extraction_version
        and dict(record.evidence) == draft.evidence
        and record.evidence_sha256 == draft.evidence_sha256
        and tuple(record.warnings) == draft.warnings
        and record.snapshot_sha256 == draft.snapshot_sha256
    )


def _snapshot_manifest(record: AssistantContextSnapshotRecord) -> dict[str, Any]:
    return {
        "work_id": record.work_id,
        "document_version_id": record.document_version_id,
        "document_content_sha256": record.document_content_sha256,
        "evidence_sha256": record.evidence_sha256,
        "snapshot_sha256": record.snapshot_sha256,
    }


def _validate_manifest(
    snapshot: AssistantContextSnapshotRecord,
    manifest: dict[str, Any],
) -> None:
    expected = _snapshot_manifest(snapshot)
    if manifest != expected:
        raise AssistantConflictError("evidence manifest does not match the frozen context")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, ContextScope):
        return value.value
    if isinstance(value, TextSelectionContext):
        return value.model_dump(mode="json")
    raise TypeError(f"Unsupported canonical JSON value: {type(value).__name__}")


def _identifier(value: str | None, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if _CONTROL_CHARACTER.search(value):
        raise ValueError(f"{name} cannot contain control characters")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} must be non-blank and at most {maximum} characters")
    return normalized


def _bounded_text(value: str, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} must be non-blank and at most {maximum} characters")
    return normalized


def _hash(value: str, *, name: str) -> str:
    if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _json_object(value: object, *, name: str) -> dict[str, Any]:
    try:
        normalized = json.loads(_canonical_json(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain finite JSON data") from error
    if not isinstance(normalized, dict):
        raise ValueError(f"{name} must be a JSON object")
    return cast(dict[str, Any], normalized)


def _json_array(
    value: object,
    *,
    name: str,
    maximum: int,
) -> list[dict[str, Any]]:
    try:
        normalized = json.loads(_canonical_json(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain finite JSON data") from error
    if not isinstance(normalized, list) or len(normalized) > maximum:
        raise ValueError(f"{name} must be a JSON array with at most {maximum} items")
    if any(not isinstance(item, dict) for item in normalized):
        raise ValueError(f"{name} items must be JSON objects")
    return cast(list[dict[str, Any]], normalized)


def _session_domain(record: AssistantSessionRecord) -> AssistantSession:
    return AssistantSession(
        id=record.id,
        work_id=record.work_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _snapshot_domain(record: AssistantContextSnapshotRecord) -> AssistantContextSnapshot:
    return AssistantContextSnapshot(
        id=record.id,
        session_id=record.session_id,
        work_id=record.work_id,
        document_version_id=record.document_version_id,
        document_content_sha256=record.document_content_sha256,
        scope=ContextScope(record.scope),
        page_numbers=tuple(record.page_numbers),
        selection=dict(record.selection) if record.selection is not None else None,
        annotation_ids=tuple(record.annotation_ids),
        section_fallback=record.section_fallback,
        extraction_version=record.extraction_version,
        evidence=_json_object(record.evidence, name="stored evidence"),
        evidence_sha256=record.evidence_sha256,
        warnings=tuple(record.warnings),
        snapshot_sha256=record.snapshot_sha256,
        created_at=record.created_at,
    )


def _task_domain(record: AssistantTaskRecord) -> AssistantTask:
    return AssistantTask(
        id=record.id,
        session_id=record.session_id,
        context_snapshot_id=record.context_snapshot_id,
        retry_of_task_id=record.retry_of_task_id,
        mode=AssistantMode(record.mode),
        status=AssistantTaskStatus(record.status),
        network_policy=NetworkPolicy(record.network_policy),
        backend_id=record.backend_id,
        requested_model_id=record.requested_model_id,
        reasoning_effort=ReasoningEffort(record.reasoning_effort),
        service_tier=ServiceTier(record.service_tier),
        output_language=cast(AssistantOutputLanguage, record.output_language),
        idempotency_key=record.idempotency_key,
        command_sha256=record.command_sha256,
        job_id=record.job_id,
        error_code=record.error_code,
        diagnostic=record.diagnostic,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        updated_at=record.updated_at,
    )


def _run_domain(record: AssistantAgentRunRecord) -> AssistantAgentRun:
    return AssistantAgentRun(
        id=record.id,
        task_id=record.task_id,
        attempt=record.attempt,
        status=AssistantTaskStatus(record.status),
        backend_id=record.backend_id,
        requested_model_id=record.requested_model_id,
        resolved_model_id=record.resolved_model_id,
        reasoning_effort=ReasoningEffort(record.reasoning_effort),
        service_tier=ServiceTier(record.service_tier),
        request_sha256=record.request_sha256,
        prompt_sha256=record.prompt_sha256,
        raw_output_sha256=record.raw_output_sha256,
        usage=_json_object(record.usage, name="stored usage"),
        repair_count=record.repair_count,
        error_code=record.error_code,
        diagnostic=record.diagnostic,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
    )


def _message_domain(record: AssistantMessageRecord) -> AssistantMessage:
    return AssistantMessage(
        id=record.id,
        session_id=record.session_id,
        task_id=record.task_id,
        sequence=record.sequence,
        role=cast(AssistantMessageRole, record.role),
        status=cast(AssistantMessageStatus, record.status),
        content=record.content,
        citations=tuple(dict(item) for item in record.citations),
        evidence_manifest=_json_object(record.evidence_manifest, name="stored evidence manifest"),
        job_id=record.job_id,
        reply_to_message_id=record.reply_to_message_id,
        created_at=record.created_at,
    )


def _fragment_domain(record: AssistantFragmentRecord) -> AssistantFragment:
    return AssistantFragment(
        task_id=record.task_id,
        sequence=record.sequence,
        run_id=record.run_id,
        kind=cast(AssistantFragmentKind, record.kind),
        text=record.text,
        created_at=record.created_at,
    )


def _checkpoint_domain(record: AssistantCheckpointRecord) -> AssistantCheckpoint:
    return AssistantCheckpoint(
        id=record.id,
        session_id=record.session_id,
        source_start_sequence=record.source_start_sequence,
        through_sequence=record.through_sequence,
        summary=record.summary,
        summary_sha256=record.summary_sha256,
        backend_id=record.backend_id,
        model_id=record.model_id,
        estimated_tokens=record.estimated_tokens,
        estimated_characters=record.estimated_characters,
        created_at=record.created_at,
    )


__all__ = [
    "AssistantAgentRun",
    "AssistantCheckpoint",
    "AssistantConflictError",
    "AssistantContextSnapshot",
    "AssistantFragment",
    "AssistantIdentityError",
    "AssistantMessage",
    "AssistantRepository",
    "AssistantRepositoryError",
    "AssistantSequenceError",
    "AssistantSession",
    "AssistantStateError",
    "AssistantTask",
    "ContextSnapshotDraft",
    "context_snapshot_fingerprint",
]
