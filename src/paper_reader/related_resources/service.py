"""Durable orchestration for explicitly confirmed related-resource searches."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.agents.evidence import canonical_json
from paper_reader.assistant.context import AssistantContextBuilder
from paper_reader.assistant.contracts import AgentSelection, ContextSelection
from paper_reader.discovery.identity import IdentityRepository
from paper_reader.jobs.models import JobRecord, utc_now
from paper_reader.jobs.repository import JobRepository
from paper_reader.recommendation.preferences import (
    LibraryStateInput,
    PreferenceRepository,
)
from paper_reader.related_resources.academic import (
    AcademicCandidate,
    AcademicCandidatePage,
    AcademicSearchSummary,
)
from paper_reader.related_resources.contracts import RelatedSearchIntent, RelatedSetStatus
from paper_reader.related_resources.intent import RelatedIntentService
from paper_reader.related_resources.models import (
    KnowledgeResourceRecord,
    RelatedPaperCandidateRecord,
    RelatedResourceSetRecord,
    RelatedRunSourceRecord,
)
from paper_reader.related_resources.repository import RelatedIntentRepository
from paper_reader.related_resources.web_research import KnowledgeResearchResult

RELATED_SEARCH_JOB_KIND = "related.search"
EXTERNAL_WEB_SOURCE_ID = "external_web"
MAX_RELATED_CANDIDATES = 50


class RelatedRunConflict(RuntimeError):
    """A related search would violate its confirmed immutable boundary."""


class RelatedRunOptions(BaseModel):
    """Frozen retrieval choices persisted with the durable Job."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    intent_revision: int = Field(ge=1)
    include_academic_sources: bool = True
    include_external_web: bool = False
    external_web_consent: bool = False
    agent: AgentSelection | None = None

    @model_validator(mode="after")
    def _explicit_boundaries(self) -> RelatedRunOptions:
        if not self.include_academic_sources and not self.include_external_web:
            raise ValueError("at least one retrieval target must be selected")
        if self.include_external_web and not self.external_web_consent:
            raise ValueError("external web research requires explicit consent")
        if self.external_web_consent and not self.include_external_web:
            raise ValueError("external web consent requires include_external_web=true")
        if self.include_external_web and self.agent is None:
            raise ValueError("external web research requires an explicit Agent selection")
        if not self.include_external_web and self.agent is not None:
            raise ValueError("Agent selection is only valid for external web research")
        return self


class _ViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class RelatedRunStart(_ViewModel):
    set_id: str
    job_id: str
    status: str
    progress: int = Field(ge=0, le=100)
    replayed: bool = False


class RelatedSourceView(_ViewModel):
    connector_id: str
    status: str
    candidate_count: int = Field(ge=0)
    displayed_count: int = Field(ge=0)
    cursor: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    progress: int = Field(ge=0, le=100)
    checkpoint: dict[str, Any] | None = None
    completed_at: datetime | None = None


class RelatedPaperView(_ViewModel):
    id: str
    connector_id: str
    external_id: str
    normalized_doi: str | None = None
    title: str
    authors: tuple[str, ...]
    publication_year: int | None = None
    reason: str
    match_evidence: dict[str, Any]
    confidence: float = Field(ge=0, le=100)
    verification_status: str
    linked_work_id: str | None = None
    merge_review_id: str | None = None
    field_provenance: dict[str, str]
    saved: bool = False


class KnowledgeResourceView(_ViewModel):
    id: str
    title: str
    canonical_url: str
    final_url: str
    domain: str
    resource_type: str
    reason: str
    publisher: str | None = None
    verification_status: str
    http_status: int | None = None
    accessed_at: datetime | None = None
    field_provenance: dict[str, str]


class RelatedResourceSetView(_ViewModel):
    id: str
    work_id: str
    document_version_id: str
    document_content_sha256: str
    context_snapshot_id: str
    intent_revision: int
    intent: RelatedSearchIntent
    status: str
    include_academic_sources: bool
    include_external_web: bool
    external_web_consent: bool
    coverage: float = Field(ge=0, le=100)
    job_id: str | None = None
    sources: tuple[RelatedSourceView, ...]
    papers: tuple[RelatedPaperView, ...]
    knowledge_resources: tuple[KnowledgeResourceView, ...]
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None = None
    completed_at: datetime | None = None


class RelatedPaperSave(_ViewModel):
    set_id: str
    candidate_id: str
    linked_work_id: str | None = None
    merge_review_id: str | None = None
    saved: bool


class AcademicSearchBackend(Protocol):
    async def search(
        self,
        intent: RelatedSearchIntent,
        on_page: Callable[[AcademicCandidatePage], Awaitable[None]],
        cancelled: Callable[[], bool],
        *,
        source_ids: tuple[str, ...] | None = None,
        candidate_limit: int = MAX_RELATED_CANDIDATES,
    ) -> AcademicSearchSummary: ...


class KnowledgeResearchBackend(Protocol):
    async def run(
        self,
        intent: RelatedSearchIntent,
        snapshot: Any,
        agent_selection: AgentSelection,
        *,
        cancelled: Callable[[], bool],
    ) -> KnowledgeResearchResult: ...


class RelatedResourceService:
    """Persist first, execute second, and resume only unfinished source units."""

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        jobs: JobRepository,
        intents: RelatedIntentRepository,
        intent_service: RelatedIntentService,
        academic: AcademicSearchBackend,
        knowledge: KnowledgeResearchBackend,
        identities: IdentityRepository,
        preferences: PreferenceRepository,
        context: AssistantContextBuilder | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self.jobs = jobs
        self.intents = intents
        self.intent_service = intent_service
        self._academic = academic
        self._knowledge = knowledge
        self._identities = identities
        self._preferences = preferences
        self._context = context
        self._clock = clock

    def draft(
        self,
        work_id: str,
        *,
        context_snapshot_id: str | None = None,
        context: ContextSelection | None = None,
    ) -> RelatedSearchIntent:
        """Create a local draft from existing or newly frozen reader evidence."""

        if (context_snapshot_id is None) == (context is None):
            raise ValueError("provide exactly one context snapshot or reader context")
        snapshot_id = context_snapshot_id
        if context is not None:
            if self._context is None:
                raise RuntimeError("reader context freezing is unavailable")
            snapshot_id = self._context.freeze(work_id, context).id
        assert snapshot_id is not None
        return self.intent_service.draft(work_id, snapshot_id)

    def start(
        self,
        set_id: str,
        options: RelatedRunOptions,
        *,
        idempotency_key: str,
    ) -> RelatedRunStart:
        if not isinstance(options, RelatedRunOptions):
            raise ValueError("options must be RelatedRunOptions")
        key = _bounded_identifier(idempotency_key, "idempotency_key", 160)
        identifier = _bounded_identifier(set_id, "set_id", 80)
        state = self.intents.get(identifier)
        payload = _job_payload(identifier, options)

        with self._sessions() as session:
            existing = session.scalar(select(JobRecord).where(JobRecord.idempotency_key == key))
            if existing is not None:
                if (
                    existing.kind != RELATED_SEARCH_JOB_KIND
                    or existing.payload.get("fingerprint") != payload["fingerprint"]
                ):
                    raise RelatedRunConflict("idempotency key is already bound to another command")
                if existing.payload.get("set_id") != identifier:
                    raise RelatedRunConflict(
                        "idempotency key is already bound to another related set"
                    )
                return _start_view(identifier, existing, replayed=True)

            record = session.get(RelatedResourceSetRecord, identifier)
            if record is None:
                raise KeyError(identifier)
            if record.job_id is not None:
                raise RelatedRunConflict("related-resource set already has a retrieval Job")
            if record.status != RelatedSetStatus.CONFIRMED.value or not state.intent.confirmed:
                raise RelatedRunConflict("related-search intent must be confirmed")
            if (
                record.intent_revision != options.intent_revision
                or state.intent.revision != options.intent_revision
            ):
                raise RelatedRunConflict("related-search intent revision is not current")
            now = self._now()
            job = JobRecord(
                kind=RELATED_SEARCH_JOB_KIND,
                payload=payload,
                idempotency_key=key,
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            session.flush()
            source_ids = state.intent.source_ids if options.include_academic_sources else ()
            if options.include_external_web:
                source_ids = (*source_ids, EXTERNAL_WEB_SOURCE_ID)
            session.add_all(
                RelatedRunSourceRecord(
                    set_id=identifier,
                    connector_id=source_id,
                    status="queued",
                    progress=0,
                    created_at=now,
                    updated_at=now,
                )
                for source_id in source_ids
            )
            claimed = session.execute(
                update(RelatedResourceSetRecord)
                .where(
                    RelatedResourceSetRecord.id == identifier,
                    RelatedResourceSetRecord.status == RelatedSetStatus.CONFIRMED.value,
                    RelatedResourceSetRecord.job_id.is_(None),
                    RelatedResourceSetRecord.intent_revision == options.intent_revision,
                )
                .values(
                    include_academic_sources=options.include_academic_sources,
                    include_external_web=options.include_external_web,
                    external_web_consent=options.external_web_consent,
                    status=RelatedSetStatus.QUEUED.value,
                    coverage=0,
                    job_id=job.id,
                    updated_at=now,
                    completed_at=None,
                )
            )
            if getattr(claimed, "rowcount", 0) != 1:
                session.rollback()
                raise RelatedRunConflict("related-resource set already has a retrieval Job")
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                replay = session.scalar(select(JobRecord).where(JobRecord.idempotency_key == key))
                if (
                    replay is not None
                    and replay.kind == RELATED_SEARCH_JOB_KIND
                    and replay.payload.get("fingerprint") == payload["fingerprint"]
                ):
                    return _start_view(identifier, replay, replayed=True)
                raise RelatedRunConflict("related-resource set was started concurrently") from error
            return _start_view(identifier, job, replayed=False)

    def resumable_job_ids(self) -> tuple[str, ...]:
        return self.jobs.list_resumable_ids(kind=RELATED_SEARCH_JOB_KIND)

    async def execute(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.kind != RELATED_SEARCH_JOB_KIND:
            raise ValueError("Job is not a related.search Job")
        if job.status in {"succeeded", "failed", "cancelled"}:
            return

        set_id, options = _options_from_job(job)
        state = self.intents.get(set_id)
        if (
            not state.intent.confirmed
            or state.intent.revision != options.intent_revision
            or state.id != set_id
        ):
            self._fail_set(set_id, job.id, "intent_changed")
            return
        if job.cancel_requested:
            self._cancel_set(set_id, job.id)
            return

        self._mark_running(set_id)
        self.jobs.update_state(job.id, status="running", progress=max(job.progress, 1))
        self.jobs.append_event(
            job_id=job.id,
            event_type="related.search.started",
            progress=max(job.progress, 1),
            payload={"set_id": set_id, "intent_revision": options.intent_revision},
        )

        pending_academic = self._pending_academic_sources(set_id, state.intent.source_ids)
        if pending_academic and not self._cancel_requested(job.id):
            await self._execute_academic(
                set_id,
                job.id,
                state.intent,
                pending_academic,
            )

        if (
            options.include_external_web
            and self._source_is_pending(set_id, EXTERNAL_WEB_SOURCE_ID)
            and not self._cancel_requested(job.id)
        ):
            assert options.agent is not None
            await self._execute_external(
                set_id,
                job.id,
                state.intent,
                options.agent,
            )

        if self._cancel_requested(job.id):
            self._cancel_set(set_id, job.id)
            return
        self._finish_set(set_id, job.id)

    def get(self, set_id: str) -> RelatedResourceSetView:
        identifier = _bounded_identifier(set_id, "set_id", 80)
        with self._sessions() as session:
            record = session.get(RelatedResourceSetRecord, identifier)
            if record is None:
                raise KeyError(identifier)
            source_records = tuple(
                session.scalars(
                    select(RelatedRunSourceRecord)
                    .where(RelatedRunSourceRecord.set_id == identifier)
                    .order_by(RelatedRunSourceRecord.connector_id)
                )
            )
            paper_records = tuple(
                session.scalars(
                    select(RelatedPaperCandidateRecord)
                    .where(RelatedPaperCandidateRecord.set_id == identifier)
                    .order_by(
                        RelatedPaperCandidateRecord.confidence.desc(),
                        RelatedPaperCandidateRecord.title,
                    )
                )
            )
            knowledge_records = tuple(
                session.scalars(
                    select(KnowledgeResourceRecord)
                    .where(KnowledgeResourceRecord.set_id == identifier)
                    .order_by(KnowledgeResourceRecord.resource_type, KnowledgeResourceRecord.title)
                )
            )

        papers = tuple(self._paper_view(item) for item in paper_records)
        return RelatedResourceSetView(
            id=record.id,
            work_id=record.work_id,
            document_version_id=record.document_version_id,
            document_content_sha256=record.document_content_sha256,
            context_snapshot_id=record.context_snapshot_id,
            intent_revision=record.intent_revision,
            intent=RelatedSearchIntent.model_validate(record.intent),
            status=record.status,
            include_academic_sources=record.include_academic_sources,
            include_external_web=record.include_external_web,
            external_web_consent=record.external_web_consent,
            coverage=float(record.coverage),
            job_id=record.job_id,
            sources=tuple(_source_view(item) for item in source_records),
            papers=papers,
            knowledge_resources=tuple(_knowledge_view(item) for item in knowledge_records),
            created_at=_utc(record.created_at),
            updated_at=_utc(record.updated_at),
            confirmed_at=_utc_optional(record.confirmed_at),
            completed_at=_utc_optional(record.completed_at),
        )

    def save_paper(self, set_id: str, candidate_id: str) -> RelatedPaperSave:
        identifier = _bounded_identifier(set_id, "set_id", 80)
        paper_id = _bounded_identifier(candidate_id, "candidate_id", 80)
        with self._sessions() as session:
            candidate = session.get(RelatedPaperCandidateRecord, paper_id)
            if candidate is None or candidate.set_id != identifier:
                raise KeyError(paper_id)
            linked_work_id = candidate.linked_work_id
            merge_review_id = candidate.merge_review_id
            verification_status = candidate.verification_status

        if linked_work_id is None:
            return RelatedPaperSave(
                set_id=identifier,
                candidate_id=paper_id,
                linked_work_id=None,
                merge_review_id=merge_review_id,
                saved=False,
            )
        if verification_status != "verified":
            raise RelatedRunConflict(
                "related paper requires a verified connector identity before saving"
            )

        current = self._preferences.get_library_state(linked_work_id)
        if not current.favorite:
            self._preferences.set_library_state(
                linked_work_id,
                LibraryStateInput(
                    favorite=True,
                    feedback=current.feedback,
                    feedback_reasons=current.feedback_reasons,
                    pinned=current.pinned,
                    reading_progress=current.reading_progress,
                    analysis_state=current.analysis_state,
                ),
            )
        return RelatedPaperSave(
            set_id=identifier,
            candidate_id=paper_id,
            linked_work_id=linked_work_id,
            merge_review_id=None,
            saved=True,
        )

    async def _execute_academic(
        self,
        set_id: str,
        job_id: str,
        intent: RelatedSearchIntent,
        source_ids: tuple[str, ...],
    ) -> None:
        self._mark_sources_running(set_id, source_ids)
        remaining = MAX_RELATED_CANDIDATES - self._paper_count(set_id)
        if remaining <= 0:
            self._mark_sources_skipped(set_id, source_ids, code="candidate_limit")
            return

        async def persist_page(page: AcademicCandidatePage) -> None:
            self._persist_academic_page(set_id, page)
            self.jobs.append_event(
                job_id=job_id,
                event_type="related.source.page",
                progress=self._progress(set_id),
                payload={
                    "set_id": set_id,
                    "connector_id": page.connector_id,
                    "page_number": page.page_number,
                    "candidate_count": len(page.candidates),
                },
            )

        try:
            summary = await self._academic.search(
                intent,
                persist_page,
                lambda: self._cancel_requested(job_id),
                source_ids=source_ids,
                candidate_limit=remaining,
            )
        except Exception as error:
            self._mark_sources_failed(set_id, source_ids, error)
            return
        self._persist_academic_summary(set_id, source_ids, summary)

    async def _execute_external(
        self,
        set_id: str,
        job_id: str,
        intent: RelatedSearchIntent,
        agent: AgentSelection,
    ) -> None:
        self._mark_sources_running(set_id, (EXTERNAL_WEB_SOURCE_ID,))
        try:
            snapshot = self.intents.context_for_set(set_id)
            result = await self._knowledge.run(
                intent,
                snapshot,
                agent,
                cancelled=lambda: self._cancel_requested(job_id),
            )
        except Exception as error:
            self._mark_sources_failed(set_id, (EXTERNAL_WEB_SOURCE_ID,), error)
            return
        self._persist_knowledge_result(set_id, result)
        self.jobs.append_event(
            job_id=job_id,
            event_type="related.external.verified",
            progress=self._progress(set_id),
            payload={
                "set_id": set_id,
                "verified_count": len(result.resources),
                "rejected_count": result.rejected_count,
            },
        )

    def _persist_academic_page(self, set_id: str, page: AcademicCandidatePage) -> None:
        now = self._now()
        with self._sessions() as session:
            source = self._source_record(session, set_id, page.connector_id)
            if source.status == "succeeded":
                return
            previous_page = (
                source.checkpoint.get("page_number")
                if isinstance(source.checkpoint, dict)
                else None
            )
            new_checkpoint = not isinstance(previous_page, int) or page.page_number > previous_page
            source.status = "running"
            source.progress = min(90, max(source.progress, page.page_number * 20))
            source.cursor = page.next_cursor
            if new_checkpoint:
                source.candidate_count += len(page.candidates)
            source.displayed_count = min(source.candidate_count, 20)
            source.checkpoint = {
                "page_number": page.page_number,
                "next_cursor": page.next_cursor,
                "fetched_count": page.fetched_count,
            }
            source.updated_at = now
            for candidate in page.candidates:
                self._upsert_candidate(session, set_id, candidate, now=now)
            session.commit()

    def _upsert_candidate(
        self,
        session: Session,
        set_id: str,
        candidate: AcademicCandidate,
        *,
        now: datetime,
    ) -> None:
        identity_clause = and_(
            RelatedPaperCandidateRecord.connector_id == candidate.connector_id,
            RelatedPaperCandidateRecord.external_id == candidate.external_id,
        )
        if candidate.normalized_doi is not None:
            candidate_clause = or_(
                identity_clause,
                RelatedPaperCandidateRecord.normalized_doi == candidate.normalized_doi,
            )
        else:
            candidate_clause = identity_clause
        record = session.scalar(
            select(RelatedPaperCandidateRecord).where(
                RelatedPaperCandidateRecord.set_id == set_id,
                candidate_clause,
            )
        )
        if record is None:
            session.add(
                RelatedPaperCandidateRecord(
                    set_id=set_id,
                    connector_id=candidate.connector_id,
                    external_id=candidate.external_id,
                    normalized_doi=candidate.normalized_doi,
                    title=candidate.title,
                    authors=list(candidate.authors),
                    publication_year=candidate.publication_year,
                    reason=candidate.reason,
                    match_evidence=dict(candidate.match_evidence),
                    confidence=candidate.confidence,
                    verification_status=candidate.verification_status.value,
                    linked_work_id=candidate.linked_work_id,
                    merge_review_id=candidate.merge_review_id,
                    field_provenance=dict(candidate.field_provenance),
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        if (
            record.connector_id == candidate.connector_id
            and record.external_id == candidate.external_id
        ):
            record.normalized_doi = candidate.normalized_doi
            record.title = candidate.title
            record.authors = list(candidate.authors)
            record.publication_year = candidate.publication_year
            record.reason = candidate.reason
            record.match_evidence = dict(candidate.match_evidence)
            record.confidence = candidate.confidence
            record.verification_status = candidate.verification_status.value
            record.linked_work_id = candidate.linked_work_id
            record.merge_review_id = candidate.merge_review_id
            record.field_provenance = dict(candidate.field_provenance)
            record.updated_at = now

    def _persist_academic_summary(
        self,
        set_id: str,
        requested_sources: tuple[str, ...],
        summary: AcademicSearchSummary,
    ) -> None:
        now = self._now()
        with self._sessions() as session:
            for connector_id in requested_sources:
                source = self._source_record(session, set_id, connector_id)
                result = summary.sources.get(connector_id)
                if result is None:
                    source.status = "failed"
                    source.error_code = "missing_source_summary"
                    source.error_message = "The connector returned no source summary."
                else:
                    source.status = result.status
                    source.candidate_count = max(
                        source.candidate_count,
                        result.candidate_count,
                    )
                    source.displayed_count = min(source.candidate_count, 20)
                    source.cursor = None
                    source.error_code = result.error_code
                    source.error_message = (
                        _source_error_message(result.error_code)
                        if result.error_code is not None
                        else None
                    )
                    source.checkpoint = {
                        "pages_fetched": result.pages_fetched,
                        "fetched_count": result.fetched_count,
                        "accepted_count": result.accepted_count,
                        "candidate_count": result.candidate_count,
                        "stop_reason": result.stop_reason,
                        "retryable": result.retryable,
                        "retry_after_seconds": result.retry_after_seconds,
                    }
                source.progress = 100
                source.completed_at = now
                source.updated_at = now
            session.commit()

    def _persist_knowledge_result(
        self,
        set_id: str,
        result: KnowledgeResearchResult,
    ) -> None:
        now = self._now()
        with self._sessions() as session:
            source = self._source_record(session, set_id, EXTERNAL_WEB_SOURCE_ID)
            for item in result.resources:
                record = session.scalar(
                    select(KnowledgeResourceRecord).where(
                        KnowledgeResourceRecord.set_id == set_id,
                        KnowledgeResourceRecord.canonical_url == item.canonical_url,
                    )
                )
                values = {
                    "title": item.title,
                    "final_url": item.final_url,
                    "domain": item.domain,
                    "resource_type": item.resource_type.value,
                    "reason": item.reason,
                    "publisher": item.publisher,
                    "verification_status": item.verification_status.value,
                    "http_status": item.http_status,
                    "accessed_at": item.accessed_at,
                    "field_provenance": dict(item.field_provenance),
                    "updated_at": now,
                }
                if record is None:
                    session.add(
                        KnowledgeResourceRecord(
                            set_id=set_id,
                            canonical_url=item.canonical_url,
                            created_at=now,
                            **values,
                        )
                    )
                else:
                    for name, value in values.items():
                        setattr(record, name, value)
            source.status = "succeeded"
            source.candidate_count = len(result.resources)
            source.displayed_count = len(result.resources)
            source.progress = 100
            source.error_code = None
            source.error_message = None
            source.checkpoint = {
                "verified_count": len(result.resources),
                "rejected_count": result.rejected_count,
                "rejection_codes": sorted({item.code for item in result.rejections}),
                "partial": result.partial,
                "truncated": result.truncated,
                "backend_id": result.backend_id,
                "requested_model_id": result.requested_model_id,
                "resolved_model_id": result.resolved_model_id,
                "reasoning_effort": result.reasoning_effort.value,
                "service_tier": result.service_tier.value,
                "external_research": result.external_research,
                "prompt_sha256": result.prompt_sha256,
                "raw_output_sha256": result.raw_output_sha256,
            }
            source.completed_at = now
            source.updated_at = now
            session.commit()

    def _mark_running(self, set_id: str) -> None:
        now = self._now()
        with self._sessions() as session:
            record = session.get(RelatedResourceSetRecord, set_id)
            if record is None:
                raise KeyError(set_id)
            record.status = "running"
            record.completed_at = None
            record.updated_at = now
            session.commit()

    def _mark_sources_running(self, set_id: str, source_ids: tuple[str, ...]) -> None:
        now = self._now()
        with self._sessions() as session:
            for connector_id in source_ids:
                source = self._source_record(session, set_id, connector_id)
                source.status = "running"
                source.completed_at = None
                source.error_code = None
                source.error_message = None
                source.updated_at = now
            session.commit()

    def _mark_sources_failed(
        self,
        set_id: str,
        source_ids: tuple[str, ...],
        error: Exception,
    ) -> None:
        now = self._now()
        code = _error_code(error)
        with self._sessions() as session:
            for connector_id in source_ids:
                source = self._source_record(session, set_id, connector_id)
                source.status = "failed"
                source.progress = 100
                source.error_code = code
                source.error_message = f"Source execution failed safely ({type(error).__name__})."
                source.completed_at = now
                source.updated_at = now
            session.commit()

    def _mark_sources_skipped(
        self,
        set_id: str,
        source_ids: tuple[str, ...],
        *,
        code: str,
    ) -> None:
        now = self._now()
        with self._sessions() as session:
            for connector_id in source_ids:
                source = self._source_record(session, set_id, connector_id)
                source.status = "skipped"
                source.progress = 100
                source.error_code = code
                source.error_message = (
                    "Source was not queried because the candidate limit was reached."
                )
                source.completed_at = now
                source.updated_at = now
            session.commit()

    def _finish_set(self, set_id: str, job_id: str) -> None:
        now = self._now()
        with self._sessions() as session:
            record = session.get(RelatedResourceSetRecord, set_id)
            if record is None:
                raise KeyError(set_id)
            sources = tuple(
                session.scalars(
                    select(RelatedRunSourceRecord).where(RelatedRunSourceRecord.set_id == set_id)
                )
            )
            succeeded = tuple(item for item in sources if item.status == "succeeded")
            incomplete = tuple(item for item in sources if item.status != "succeeded")
            external_partial = any(
                item.connector_id == EXTERNAL_WEB_SOURCE_ID
                and isinstance(item.checkpoint, dict)
                and bool(item.checkpoint.get("partial"))
                for item in sources
            )
            coverage = (
                sum(_source_coverage(item) for item in sources) / len(sources) if sources else 0
            )
            record.coverage = round(coverage, 6)
            if succeeded and not incomplete and not external_partial:
                record.status = "succeeded"
            elif succeeded:
                record.status = "partial"
            else:
                record.status = "failed"
            record.completed_at = now
            record.updated_at = now
            status = record.status
            paper_count = session.scalar(
                select(RelatedPaperCandidateRecord.id)
                .where(RelatedPaperCandidateRecord.set_id == set_id)
                .limit(1)
            )
            knowledge_count = session.scalar(
                select(KnowledgeResourceRecord.id)
                .where(KnowledgeResourceRecord.set_id == set_id)
                .limit(1)
            )
            session.commit()

        partial = status == "partial"
        result = {
            "set_id": set_id,
            "status": status,
            "partial": partial,
            "coverage": round(coverage, 6),
            "has_papers": paper_count is not None,
            "has_knowledge_resources": knowledge_count is not None,
        }
        if status in {"succeeded", "partial"}:
            self.jobs.update_state(job_id, status="succeeded", progress=100, result=result)
            event_type = "related.search.succeeded"
        else:
            self.jobs.update_state(
                job_id,
                status="failed",
                progress=100,
                result=result,
                error_code="all_sources_failed",
                error_message="No selected source completed successfully.",
            )
            event_type = "related.search.failed"
        self.jobs.append_event(
            job_id=job_id,
            event_type=event_type,
            progress=100,
            payload=result,
        )

    def _cancel_set(self, set_id: str, job_id: str) -> None:
        now = self._now()
        with self._sessions() as session:
            record = session.get(RelatedResourceSetRecord, set_id)
            if record is None:
                raise KeyError(set_id)
            for source in session.scalars(
                select(RelatedRunSourceRecord).where(
                    RelatedRunSourceRecord.set_id == set_id,
                    RelatedRunSourceRecord.status.in_(("queued", "running")),
                )
            ):
                source.status = "cancelled"
                source.progress = 100
                source.completed_at = now
                source.updated_at = now
            record.status = "cancelled"
            record.completed_at = now
            record.updated_at = now
            session.commit()
        self.jobs.update_state(job_id, status="cancelled", progress=100)
        self.jobs.append_event(
            job_id=job_id,
            event_type="related.search.cancelled",
            progress=100,
            payload={"set_id": set_id},
        )

    def _fail_set(self, set_id: str, job_id: str, code: str) -> None:
        now = self._now()
        with self._sessions() as session:
            record = session.get(RelatedResourceSetRecord, set_id)
            if record is not None:
                record.status = "failed"
                record.completed_at = now
                record.updated_at = now
                session.commit()
        self.jobs.update_state(
            job_id,
            status="failed",
            progress=100,
            error_code=code,
            error_message="The confirmed related-search boundary is no longer executable.",
        )

    def _pending_academic_sources(
        self,
        set_id: str,
        ordered_source_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        with self._sessions() as session:
            status_by_source = {
                item.connector_id: item.status
                for item in session.scalars(
                    select(RelatedRunSourceRecord).where(RelatedRunSourceRecord.set_id == set_id)
                )
            }
        return tuple(
            source_id
            for source_id in ordered_source_ids
            if status_by_source.get(source_id) in {"queued", "running"}
        )

    def _source_is_pending(self, set_id: str, connector_id: str) -> bool:
        with self._sessions() as session:
            source = session.scalar(
                select(RelatedRunSourceRecord).where(
                    RelatedRunSourceRecord.set_id == set_id,
                    RelatedRunSourceRecord.connector_id == connector_id,
                )
            )
            return source is not None and source.status in {"queued", "running"}

    def _paper_count(self, set_id: str) -> int:
        with self._sessions() as session:
            return len(
                tuple(
                    session.scalars(
                        select(RelatedPaperCandidateRecord.id).where(
                            RelatedPaperCandidateRecord.set_id == set_id
                        )
                    )
                )
            )

    def _progress(self, set_id: str) -> int:
        with self._sessions() as session:
            progress = tuple(
                session.scalars(
                    select(RelatedRunSourceRecord.progress).where(
                        RelatedRunSourceRecord.set_id == set_id
                    )
                )
            )
        return round(sum(progress) / len(progress)) if progress else 0

    def _cancel_requested(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        return job is None or job.cancel_requested

    def _paper_view(self, record: RelatedPaperCandidateRecord) -> RelatedPaperView:
        saved = False
        if record.linked_work_id is not None:
            saved = self._preferences.get_library_state(record.linked_work_id).favorite
        return RelatedPaperView(
            id=record.id,
            connector_id=record.connector_id,
            external_id=record.external_id,
            normalized_doi=record.normalized_doi,
            title=record.title,
            authors=tuple(record.authors),
            publication_year=record.publication_year,
            reason=record.reason,
            match_evidence=dict(record.match_evidence),
            confidence=float(record.confidence),
            verification_status=record.verification_status,
            linked_work_id=record.linked_work_id,
            merge_review_id=record.merge_review_id,
            field_provenance=dict(record.field_provenance),
            saved=saved,
        )

    @staticmethod
    def _source_record(
        session: Session,
        set_id: str,
        connector_id: str,
    ) -> RelatedRunSourceRecord:
        source = session.scalar(
            select(RelatedRunSourceRecord).where(
                RelatedRunSourceRecord.set_id == set_id,
                RelatedRunSourceRecord.connector_id == connector_id,
            )
        )
        if source is None:
            raise RelatedRunConflict("related-search source checkpoint is missing")
        return source

    def _now(self) -> datetime:
        return _utc(self._clock())


def _job_payload(set_id: str, options: RelatedRunOptions) -> dict[str, Any]:
    command = {
        "set_id": set_id,
        "options": options.model_dump(mode="json"),
    }
    return {
        **command,
        "fingerprint": sha256(canonical_json(command).encode("utf-8")).hexdigest(),
    }


def _options_from_job(job: JobRecord) -> tuple[str, RelatedRunOptions]:
    payload = dict(job.payload)
    set_id = _bounded_identifier(payload.get("set_id"), "set_id", 80)
    options = RelatedRunOptions.model_validate(payload.get("options"))
    expected = _job_payload(set_id, options)["fingerprint"]
    if payload.get("fingerprint") != expected:
        raise RelatedRunConflict("related-search Job payload fingerprint is invalid")
    return set_id, options


def _start_view(set_id: str, job: JobRecord, *, replayed: bool) -> RelatedRunStart:
    return RelatedRunStart(
        set_id=set_id,
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        replayed=replayed,
    )


def _source_view(record: RelatedRunSourceRecord) -> RelatedSourceView:
    return RelatedSourceView(
        connector_id=record.connector_id,
        status=record.status,
        candidate_count=record.candidate_count,
        displayed_count=record.displayed_count,
        cursor=record.cursor,
        error_code=record.error_code,
        error_message=record.error_message,
        progress=record.progress,
        checkpoint=dict(record.checkpoint) if record.checkpoint is not None else None,
        completed_at=_utc_optional(record.completed_at),
    )


def _knowledge_view(record: KnowledgeResourceRecord) -> KnowledgeResourceView:
    return KnowledgeResourceView(
        id=record.id,
        title=record.title,
        canonical_url=record.canonical_url,
        final_url=record.final_url,
        domain=record.domain,
        resource_type=record.resource_type,
        reason=record.reason,
        publisher=record.publisher,
        verification_status=record.verification_status,
        http_status=record.http_status,
        accessed_at=_utc_optional(record.accessed_at),
        field_provenance=dict(record.field_provenance),
    )


def _error_code(error: Exception) -> str:
    raw = getattr(error, "code", None)
    if isinstance(raw, str) and raw and len(raw) <= 80:
        return raw
    return "source_execution_failed"


def _source_error_message(error_code: str) -> str:
    return f"Connector did not complete ({error_code})."


def _source_coverage(record: RelatedRunSourceRecord) -> float:
    if record.status != "succeeded":
        return 0.0
    if record.connector_id != EXTERNAL_WEB_SOURCE_ID:
        return 100.0
    checkpoint = record.checkpoint
    if not isinstance(checkpoint, dict) or not checkpoint.get("partial"):
        return 100.0
    verified = checkpoint.get("verified_count")
    rejected = checkpoint.get("rejected_count")
    if not isinstance(verified, int) or not isinstance(rejected, int):
        return 0.0
    total = verified + rejected
    if total <= 0:
        return 0.0
    return 100.0 * verified / total


def _bounded_identifier(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or any(ord(char) < 32 for char in normalized):
        raise ValueError(f"{name} must contain 1-{maximum} safe characters")
    return normalized


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_optional(value: datetime | None) -> datetime | None:
    return _utc(value) if value is not None else None


__all__ = [
    "EXTERNAL_WEB_SOURCE_ID",
    "RELATED_SEARCH_JOB_KIND",
    "KnowledgeResourceView",
    "RelatedPaperSave",
    "RelatedPaperView",
    "RelatedResourceService",
    "RelatedResourceSetView",
    "RelatedRunConflict",
    "RelatedRunOptions",
    "RelatedRunStart",
    "RelatedSourceView",
]
