"""Optimistic, evidence-bound persistence for related-search intent revisions."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.agents.contracts import ReasoningEffort, ServiceTier
from paper_reader.agents.evidence import EvidenceBundle, canonical_json
from paper_reader.assistant.contracts import ContextScope, TextSelectionContext
from paper_reader.assistant.models import (
    AssistantContextSnapshotRecord,
    AssistantSessionRecord,
)
from paper_reader.assistant.repository import ContextSnapshotDraft
from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.jobs.models import utc_now
from paper_reader.related_resources.contracts import (
    RelatedIntentDiff,
    RelatedSearchIntent,
    RelatedSetStatus,
    normalize_intent_term,
)
from paper_reader.related_resources.models import RelatedResourceSetRecord

RelatedIntentRevisionOrigin = Literal["local_draft", "manual_edit", "agent_refinement"]
MAX_INTENT_REVISIONS = 1_000

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_EXECUTED_STATUSES = frozenset(
    {
        RelatedSetStatus.QUEUED.value,
        RelatedSetStatus.RUNNING.value,
        RelatedSetStatus.PARTIAL.value,
        RelatedSetStatus.SUCCEEDED.value,
        RelatedSetStatus.FAILED.value,
        RelatedSetStatus.CANCELLED.value,
    }
)


class RelatedIntentError(RuntimeError):
    """Base error for related-intent persistence."""


class RelatedIntentConflict(RelatedIntentError):
    """An optimistic revision or immutable identity no longer matches."""


class RelatedIntentStateError(RelatedIntentConflict):
    """A lifecycle transition would rewrite an executed fact."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class RelatedIntentAgentMetadata(_StrictModel):
    backend_id: str = Field(min_length=1, max_length=80)
    requested_model_id: str | None = Field(default=None, max_length=200)
    resolved_model_id: str | None = Field(default=None, max_length=200)
    reasoning_effort: ReasoningEffort
    service_tier: ServiceTier
    external_research: bool = False
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("backend_id")
    @classmethod
    def _safe_backend_id(cls, value: str) -> str:
        if _SAFE_IDENTIFIER.fullmatch(value) is None:
            raise ValueError("backend_id must be a safe identifier")
        return value

    @field_validator("requested_model_id", "resolved_model_id")
    @classmethod
    def _safe_model_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or _CONTROL_CHARACTER.search(value):
            raise ValueError("model IDs must be non-blank and contain no controls")
        return value

    @model_validator(mode="after")
    def _local_refinement_only(self) -> RelatedIntentAgentMetadata:
        if self.external_research:
            raise ValueError("related intent refinement cannot use external research")
        return self


class RelatedIntentRevision(_StrictModel):
    revision: int = Field(ge=1)
    intent: RelatedSearchIntent
    origin: RelatedIntentRevisionOrigin
    agent: RelatedIntentAgentMetadata | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _coherent_revision(self) -> RelatedIntentRevision:
        if self.intent.revision != self.revision:
            raise ValueError("history revision does not match its intent")
        if self.intent.confirmed or self.intent.confirmed_at is not None:
            raise ValueError("history stores immutable unconfirmed revision content")
        if (self.origin == "agent_refinement") != (self.agent is not None):
            raise ValueError("agent metadata must match the revision origin")
        if self.created_at.utcoffset() is None:
            raise ValueError("history created_at must include a timezone")
        return self


@dataclass(frozen=True, slots=True)
class RelatedIntentState:
    id: str
    work_id: str
    document_version_id: str
    document_content_sha256: str
    context_snapshot_id: str
    status: RelatedSetStatus
    intent: RelatedSearchIntent
    history: tuple[RelatedIntentRevision, ...]
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None


@dataclass(frozen=True, slots=True)
class RelatedIntentContext:
    work_id: str
    document_version_id: str
    document_content_sha256: str
    context_snapshot_id: str
    scope: ContextScope
    evidence: EvidenceBundle
    snapshot_sha256: str


class RelatedIntentRepository:
    """Own the append-only revision history and optimistic confirmation boundary."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    def create_draft(
        self,
        work_id: str,
        context_snapshot_id: str,
        intent: RelatedSearchIntent,
    ) -> RelatedSearchIntent:
        if not isinstance(intent, RelatedSearchIntent):
            raise ValueError("intent must be a RelatedSearchIntent")
        with self._sessions() as session:
            snapshot, canonical, _ = self._validated_snapshot(
                session,
                work_id,
                context_snapshot_id,
            )
            record = RelatedResourceSetRecord(
                work_id=canonical.id,
                document_version_id=snapshot.document_version_id,
                document_content_sha256=snapshot.document_content_sha256,
                context_snapshot_id=snapshot.id,
                intent_revision=1,
                intent={},
                intent_history=[],
                status=RelatedSetStatus.DRAFT.value,
                include_academic_sources=True,
                include_external_web=False,
                external_web_consent=False,
                coverage=0,
            )
            session.add(record)
            session.flush()
            draft = self._normalized_revision(
                intent,
                set_id=record.id,
                revision=1,
                diff=RelatedIntentDiff(),
            )
            created_at = self._now()
            history = RelatedIntentRevision(
                revision=1,
                intent=draft,
                origin="local_draft",
                created_at=created_at,
            )
            record.intent = draft.model_dump(mode="json")
            record.intent_history = [history.model_dump(mode="json")]
            record.created_at = created_at
            record.updated_at = created_at
            session.commit()
            return draft

    def context(
        self,
        work_id: str,
        context_snapshot_id: str,
    ) -> RelatedIntentContext:
        with self._sessions() as session:
            snapshot, canonical, evidence = self._validated_snapshot(
                session,
                work_id,
                context_snapshot_id,
            )
            return self._context_domain(snapshot, canonical, evidence)

    def context_for_set(self, set_id: str) -> RelatedIntentContext:
        identifier = self._identifier(set_id, name="set_id")
        with self._sessions() as session:
            record = session.get(RelatedResourceSetRecord, identifier)
            if record is None:
                raise KeyError(identifier)
            state = self._state(record)
            snapshot, canonical, evidence = self._validated_snapshot(
                session,
                state.work_id,
                state.context_snapshot_id,
            )
            if (
                snapshot.document_version_id != state.document_version_id
                or snapshot.document_content_sha256 != state.document_content_sha256
            ):
                raise RelatedIntentConflict("related set context identity is invalid")
            return self._context_domain(snapshot, canonical, evidence)

    def get(self, set_id: str) -> RelatedIntentState:
        identifier = self._identifier(set_id, name="set_id")
        with self._sessions() as session:
            record = session.get(RelatedResourceSetRecord, identifier)
            if record is None:
                raise KeyError(identifier)
            return self._state(record)

    def revise(
        self,
        set_id: str,
        *,
        expected_revision: int,
        intent: RelatedSearchIntent,
        origin: RelatedIntentRevisionOrigin,
        agent: RelatedIntentAgentMetadata | None = None,
    ) -> RelatedSearchIntent:
        if origin not in {"manual_edit", "agent_refinement"}:
            raise ValueError("revisions must be manual_edit or agent_refinement")
        if (origin == "agent_refinement") != (agent is not None):
            raise ValueError("agent metadata must match the revision origin")
        identifier = self._identifier(set_id, name="set_id")
        with self._sessions() as session:
            record = session.get(RelatedResourceSetRecord, identifier)
            if record is None:
                raise KeyError(identifier)
            state = self._state(record)
            self._require_editable(state, expected_revision)
            self._require_revision_capacity(expected_revision)
            self._require_candidate_identity(intent, state)
            diff = related_intent_diff(state.intent, intent)
            if not _diff_has_changes(diff):
                raise RelatedIntentConflict("intent revision contains no semantic changes")
            revision = expected_revision + 1
            revised = self._normalized_revision(
                intent,
                set_id=state.id,
                revision=revision,
                diff=diff,
            )
            now = self._now()
            history = (
                *state.history,
                RelatedIntentRevision(
                    revision=revision,
                    intent=revised,
                    origin=origin,
                    agent=agent,
                    created_at=now,
                ),
            )
            result = session.execute(
                update(RelatedResourceSetRecord)
                .where(
                    RelatedResourceSetRecord.id == state.id,
                    RelatedResourceSetRecord.intent_revision == expected_revision,
                    RelatedResourceSetRecord.status == state.status.value,
                )
                .values(
                    intent_revision=revision,
                    intent=revised.model_dump(mode="json"),
                    intent_history=[item.model_dump(mode="json") for item in history],
                    status=RelatedSetStatus.DRAFT.value,
                    confirmed_at=None,
                    updated_at=now,
                )
            )
            if not _updated_one(result):
                session.rollback()
                raise RelatedIntentConflict("intent revision changed concurrently")
            session.commit()
            return revised

    def confirm(
        self,
        set_id: str,
        *,
        expected_revision: int,
        intent: RelatedSearchIntent,
    ) -> RelatedSearchIntent:
        identifier = self._identifier(set_id, name="set_id")
        with self._sessions() as session:
            record = session.get(RelatedResourceSetRecord, identifier)
            if record is None:
                raise KeyError(identifier)
            state = self._state(record)
            self._require_editable(state, expected_revision)
            self._require_candidate_identity(intent, state)
            changed = _intent_content(state.intent) != _intent_content(intent)
            if state.status is RelatedSetStatus.CONFIRMED and not changed:
                return state.intent

            now = self._now()
            revision = expected_revision
            history = state.history
            candidate = state.intent
            if changed:
                self._require_revision_capacity(expected_revision)
                revision += 1
                candidate = self._normalized_revision(
                    intent,
                    set_id=state.id,
                    revision=revision,
                    diff=related_intent_diff(state.intent, intent),
                )
                history = (
                    *history,
                    RelatedIntentRevision(
                        revision=revision,
                        intent=candidate,
                        origin="manual_edit",
                        created_at=now,
                    ),
                )
                if state.status is RelatedSetStatus.CONFIRMED:
                    return self._publish_unconfirmed_edit(
                        session,
                        state,
                        candidate,
                        history,
                        now=now,
                    )

            confirmed = candidate.model_copy(
                update={
                    "confirmed": True,
                    "confirmed_at": now,
                }
            )
            result = session.execute(
                update(RelatedResourceSetRecord)
                .where(
                    RelatedResourceSetRecord.id == state.id,
                    RelatedResourceSetRecord.intent_revision == expected_revision,
                    RelatedResourceSetRecord.status == state.status.value,
                )
                .values(
                    intent_revision=revision,
                    intent=confirmed.model_dump(mode="json"),
                    intent_history=[item.model_dump(mode="json") for item in history],
                    status=RelatedSetStatus.CONFIRMED.value,
                    confirmed_at=now,
                    updated_at=now,
                )
            )
            if not _updated_one(result):
                session.rollback()
                raise RelatedIntentConflict("intent revision changed concurrently")
            session.commit()
            return confirmed

    def _publish_unconfirmed_edit(
        self,
        session: Session,
        state: RelatedIntentState,
        intent: RelatedSearchIntent,
        history: tuple[RelatedIntentRevision, ...],
        *,
        now: datetime,
    ) -> RelatedSearchIntent:
        result = session.execute(
            update(RelatedResourceSetRecord)
            .where(
                RelatedResourceSetRecord.id == state.id,
                RelatedResourceSetRecord.intent_revision == state.intent.revision,
                RelatedResourceSetRecord.status == RelatedSetStatus.CONFIRMED.value,
            )
            .values(
                intent_revision=intent.revision,
                intent=intent.model_dump(mode="json"),
                intent_history=[item.model_dump(mode="json") for item in history],
                status=RelatedSetStatus.DRAFT.value,
                confirmed_at=None,
                updated_at=now,
            )
        )
        if not _updated_one(result):
            session.rollback()
            raise RelatedIntentConflict("intent revision changed concurrently")
        session.commit()
        return intent

    @staticmethod
    def _normalized_revision(
        intent: RelatedSearchIntent,
        *,
        set_id: str,
        revision: int,
        diff: RelatedIntentDiff,
    ) -> RelatedSearchIntent:
        return intent.model_copy(
            update={
                "set_id": set_id,
                "revision": revision,
                "confirmed": False,
                "confirmed_at": None,
                "diff": diff,
            }
        )

    @staticmethod
    def _require_candidate_identity(
        candidate: RelatedSearchIntent,
        state: RelatedIntentState,
    ) -> None:
        if not isinstance(candidate, RelatedSearchIntent):
            raise ValueError("intent must be a RelatedSearchIntent")
        if candidate.set_id not in {None, state.id}:
            raise RelatedIntentConflict("intent belongs to another related set")
        if candidate.revision != state.intent.revision:
            raise RelatedIntentConflict("intent revision does not match the current revision")

    @staticmethod
    def _require_editable(state: RelatedIntentState, expected_revision: int) -> None:
        if state.status.value in _EXECUTED_STATUSES:
            raise RelatedIntentStateError("an executed intent revision is immutable")
        if expected_revision != state.intent.revision:
            raise RelatedIntentConflict("intent revision does not match the current revision")

    @staticmethod
    def _require_revision_capacity(expected_revision: int) -> None:
        if expected_revision >= MAX_INTENT_REVISIONS:
            raise RelatedIntentStateError("related intent revision limit has been reached")

    def _validated_snapshot(
        self,
        session: Session,
        work_id: str,
        context_snapshot_id: str,
    ) -> tuple[AssistantContextSnapshotRecord, WorkRecord, EvidenceBundle]:
        requested_work = self._identifier(work_id, name="work_id")
        snapshot_id = self._identifier(context_snapshot_id, name="context_snapshot_id")
        snapshot = session.get(AssistantContextSnapshotRecord, snapshot_id)
        if snapshot is None:
            raise KeyError(snapshot_id)
        try:
            canonical = self._resolve_work(session, requested_work)
        except KeyError as error:
            raise RelatedIntentConflict("context snapshot belongs to another Work") from error
        if snapshot.work_id != canonical.id:
            raise RelatedIntentConflict("context snapshot belongs to another Work")
        assistant_session = session.get(AssistantSessionRecord, snapshot.session_id)
        if assistant_session is None or assistant_session.work_id != canonical.id:
            raise RelatedIntentConflict("context snapshot Assistant session identity is invalid")
        document = session.get(DocumentVersionRecord, snapshot.document_version_id)
        if (
            document is None
            or self._resolve_work(session, document.work_id).id != canonical.id
            or document.content_sha256 != snapshot.document_content_sha256
        ):
            raise RelatedIntentConflict("context snapshot document identity is invalid")
        evidence = self._validate_snapshot_identity(snapshot)
        return snapshot, canonical, evidence

    @staticmethod
    def _validate_snapshot_identity(
        snapshot: AssistantContextSnapshotRecord,
    ) -> EvidenceBundle:
        evidence_payload = dict(snapshot.evidence)
        embedded_hash = evidence_payload.pop("evidence_sha256", None)
        calculated_hash = sha256(canonical_json(evidence_payload).encode("utf-8")).hexdigest()
        if embedded_hash != snapshot.evidence_sha256 or calculated_hash != snapshot.evidence_sha256:
            raise RelatedIntentConflict("context snapshot evidence identity is invalid")
        try:
            evidence = EvidenceBundle.model_validate(snapshot.evidence)
            selection = (
                TextSelectionContext.model_validate(snapshot.selection)
                if snapshot.selection is not None
                else None
            )
            draft = ContextSnapshotDraft.create(
                work_id=snapshot.work_id,
                document_version_id=snapshot.document_version_id,
                document_content_sha256=snapshot.document_content_sha256,
                scope=ContextScope(snapshot.scope),
                page_numbers=tuple(snapshot.page_numbers),
                selection=selection,
                annotation_ids=tuple(snapshot.annotation_ids),
                section_fallback=snapshot.section_fallback,
                extraction_version=snapshot.extraction_version,
                evidence=snapshot.evidence,
                evidence_sha256=snapshot.evidence_sha256,
                warnings=tuple(snapshot.warnings),
            )
        except (ValidationError, ValueError) as error:
            raise RelatedIntentConflict("context snapshot evidence identity is invalid") from error
        if draft.snapshot_sha256 != snapshot.snapshot_sha256:
            raise RelatedIntentConflict("context snapshot identity is invalid")
        if (
            evidence.work.work_id != snapshot.work_id
            or evidence.document is None
            or evidence.document.document_version_id != snapshot.document_version_id
            or evidence.document.content_sha256 != snapshot.document_content_sha256
        ):
            raise RelatedIntentConflict("context snapshot evidence identity is invalid")
        for page in evidence.pages:
            if sha256(page.text.encode("utf-8")).hexdigest() != page.source_text_sha256:
                raise RelatedIntentConflict("context snapshot page evidence identity is invalid")
        return evidence

    @staticmethod
    def _context_domain(
        snapshot: AssistantContextSnapshotRecord,
        canonical: WorkRecord,
        evidence: EvidenceBundle,
    ) -> RelatedIntentContext:
        return RelatedIntentContext(
            work_id=canonical.id,
            document_version_id=snapshot.document_version_id,
            document_content_sha256=snapshot.document_content_sha256,
            context_snapshot_id=snapshot.id,
            scope=ContextScope(snapshot.scope),
            evidence=evidence,
            snapshot_sha256=snapshot.snapshot_sha256,
        )

    @classmethod
    def _state(cls, record: RelatedResourceSetRecord) -> RelatedIntentState:
        raw_history = record.intent_history
        if (
            not isinstance(raw_history, list)
            or not 1 <= record.intent_revision <= MAX_INTENT_REVISIONS
            or len(raw_history) != record.intent_revision
        ):
            raise RelatedIntentConflict("related intent history is invalid")
        try:
            status = RelatedSetStatus(record.status)
            intent = RelatedSearchIntent.model_validate(record.intent)
            history = tuple(RelatedIntentRevision.model_validate(item) for item in raw_history)
        except (TypeError, ValueError, ValidationError) as error:
            raise RelatedIntentConflict("related intent history is invalid") from error
        if (
            intent.set_id != record.id
            or intent.revision != record.intent_revision
            or tuple(item.revision for item in history)
            != tuple(range(1, record.intent_revision + 1))
            or not history
            or history[0].origin != "local_draft"
            or any(item.intent.set_id != record.id for item in history)
            or _revision_payload(history[-1].intent) != _revision_payload(intent)
            or history[0].intent.diff != RelatedIntentDiff()
            or any(
                child.intent.diff != related_intent_diff(parent.intent, child.intent)
                for parent, child in zip(history[:-1], history[1:], strict=True)
            )
        ):
            raise RelatedIntentConflict("related intent history is invalid")
        confirmed_at = _optional_utc(record.confirmed_at)
        if status is RelatedSetStatus.DRAFT:
            if intent.confirmed or intent.confirmed_at is not None or confirmed_at is not None:
                raise RelatedIntentConflict("related intent confirmation state is invalid")
        elif not intent.confirmed or intent.confirmed_at is None or confirmed_at is None:
            raise RelatedIntentConflict("related intent confirmation state is invalid")
        elif _utc(intent.confirmed_at) != confirmed_at:
            raise RelatedIntentConflict("related intent confirmation timestamp is invalid")
        return RelatedIntentState(
            id=record.id,
            work_id=record.work_id,
            document_version_id=record.document_version_id,
            document_content_sha256=record.document_content_sha256,
            context_snapshot_id=record.context_snapshot_id,
            status=status,
            intent=intent,
            history=history,
            created_at=_utc(record.created_at),
            updated_at=_utc(record.updated_at),
            confirmed_at=confirmed_at,
        )

    @staticmethod
    def _resolve_work(session: Session, work_id: str) -> WorkRecord:
        current_id = work_id
        visited: set[str] = set()
        while True:
            if current_id in visited:
                raise RelatedIntentConflict("Work merge chain contains a cycle")
            visited.add(current_id)
            work = session.get(WorkRecord, current_id)
            if work is None:
                raise KeyError(work_id)
            if work.status == "active" and work.merged_into_work_id is None:
                return work
            if work.status != "merged" or not work.merged_into_work_id:
                raise RelatedIntentConflict("Work merge state is invalid")
            current_id = work.merged_into_work_id

    def _now(self) -> datetime:
        return _utc(self._clock())

    @staticmethod
    def _identifier(value: str, *, name: str) -> str:
        if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
            raise ValueError(f"{name} must be a safe identifier")
        return value


def related_intent_diff(
    parent: RelatedSearchIntent,
    child: RelatedSearchIntent,
) -> RelatedIntentDiff:
    old_sections, old_display = _term_sections(parent)
    new_sections, new_display = _term_sections(child)
    old_terms = set(old_sections)
    new_terms = set(new_sections)
    moved = {
        identity
        for identity in old_terms & new_terms
        if old_sections[identity] != new_sections[identity]
    }
    return RelatedIntentDiff(
        added_terms=tuple(new_display[item] for item in sorted(new_terms - old_terms)),
        removed_terms=tuple(old_display[item] for item in sorted(old_terms - new_terms)),
        moved_terms=tuple(new_display[item] for item in sorted(moved)),
        added_source_ids=tuple(sorted(set(child.source_ids) - set(parent.source_ids))),
        removed_source_ids=tuple(sorted(set(parent.source_ids) - set(child.source_ids))),
        summary_changed=parent.summary != child.summary,
        recent_months_changed=parent.recent_months != child.recent_months,
    )


def _term_sections(
    intent: RelatedSearchIntent,
) -> tuple[dict[str, str], dict[str, str]]:
    sections: dict[str, str] = {}
    display: dict[str, str] = {}
    for group in intent.required_groups:
        identities = tuple(sorted(normalize_intent_term(term) for term in group))
        section = f"required:{canonical_json(identities)}"
        for term in group:
            identity = normalize_intent_term(term)
            sections[identity] = section
            display[identity] = term
    for section, terms in (
        ("optional", intent.optional_terms),
        ("excluded", intent.excluded_terms),
    ):
        for term in terms:
            identity = normalize_intent_term(term)
            sections[identity] = section
            display[identity] = term
    return sections, display


def _intent_content(intent: RelatedSearchIntent) -> dict[str, Any]:
    return intent.model_dump(
        mode="json",
        exclude={"set_id", "revision", "confirmed", "confirmed_at", "diff"},
    )


def _revision_payload(intent: RelatedSearchIntent) -> dict[str, Any]:
    return intent.model_dump(mode="json", exclude={"confirmed", "confirmed_at"})


def _updated_one(result: object) -> bool:
    return getattr(result, "rowcount", None) == 1


def _diff_has_changes(diff: RelatedIntentDiff) -> bool:
    return any(
        (
            diff.added_terms,
            diff.removed_terms,
            diff.moved_terms,
            diff.added_source_ids,
            diff.removed_source_ids,
            diff.summary_changed,
            diff.recent_months_changed,
        )
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _utc(value) if value is not None else None


__all__ = [
    "RelatedIntentAgentMetadata",
    "RelatedIntentConflict",
    "RelatedIntentContext",
    "RelatedIntentError",
    "RelatedIntentRepository",
    "RelatedIntentRevision",
    "RelatedIntentState",
    "RelatedIntentStateError",
    "related_intent_diff",
]
