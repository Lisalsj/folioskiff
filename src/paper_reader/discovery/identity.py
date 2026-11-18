"""Canonical Work matching and source provenance persistence."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from enum import StrEnum
from itertools import islice
from typing import Any, Literal, Protocol, cast

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import (
    DocumentVersionRecord,
    ImplicitPreferenceEventRecord,
    LibraryStateRecord,
    MergeReviewRecord,
    MetadataObservationRecord,
    PaperUpdateRecord,
    PreferenceAggregateRecord,
    RunCandidateRecord,
    SourceRecord,
    SourceRevisionRecord,
    WorkRecord,
)
from paper_reader.reader.models import (
    AnnotationRecord,
    LibraryEntryRecord,
    ReadingPositionRecord,
)

DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
LEGAL_ACCESS = ("open", "user_provided")
IDENTITY_CANDIDATE_LIMIT = 256
IDENTITY_PREFIX_BUDGET = 192
MAX_IDENTITY_BATCH_SIZE = 50


class MatchKind(StrEnum):
    NEW = "new"
    EXISTING_SOURCE = "existing_source"
    EXACT_DOI = "exact_doi"
    AUTO_SIMILARITY = "auto_similarity"
    REVIEW_REQUIRED = "review_required"


class DiscoveryKind(StrEnum):
    NEW_WORK = "new_work"
    EXISTING_WORK_NEW_SOURCE = "existing_work_new_source"
    UNCHANGED = "unchanged"
    METADATA_REFRESH = "metadata_refresh"
    IMPORTANT_REVISION = "important_revision"


@dataclass(frozen=True, slots=True)
class SourcePaper:
    connector_id: str
    external_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str | None = None
    doi: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    landing_url: str | None = None
    field_provenance: dict[str, str] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IngestResult:
    work: WorkRecord
    source_record: SourceRecord
    match_kind: MatchKind
    discovery_kind: DiscoveryKind
    merge_review: MergeReviewRecord | None = None
    paper_update_id: str | None = None
    candidate_search: CandidateSearchDiagnostics | None = None


@dataclass(frozen=True, slots=True)
class CandidateSearchDiagnostics:
    strategy: Literal["fts", "sql_prefix", "sql_prefix_year"]
    degraded: bool
    candidate_count: int
    limit: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class WorkIdentity:
    id: str
    title: str
    authors: tuple[str, ...]
    doi: str | None
    status: str
    source_count: int


@dataclass(frozen=True, slots=True)
class MergeReview:
    id: str
    candidate_work: WorkIdentity
    matched_work: WorkIdentity
    similarity: float
    evidence: dict[str, Any]
    status: str
    decision_note: str | None
    decided_by: str | None
    created_at: datetime
    decided_at: datetime | None


class MergeReviewAlreadyDecidedError(RuntimeError):
    pass


class MergeConflictError(RuntimeError):
    pass


class WorkIndex(Protocol):
    """Minimal derived-index boundary kept independent of recommendation internals."""

    def sync_work(self, work_id: str) -> None: ...


def normalize_doi(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = DOI_PREFIX.sub("", value.strip()).strip().lower()
    return normalized.rstrip(".") or None


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(NON_WORD.sub(" ", normalized).split())


def _author_keys(authors: tuple[str, ...] | list[str]) -> set[str]:
    keys: set[str] = set()
    for author in authors:
        tokens = normalize_title(author).split()
        if tokens:
            keys.add(tokens[-1])
    return keys


def paper_similarity(paper: SourcePaper, work: WorkRecord) -> tuple[float, dict[str, float]]:
    title_score = SequenceMatcher(
        None,
        normalize_title(paper.title),
        work.normalized_title,
        autojunk=False,
    ).ratio()
    incoming_authors = _author_keys(paper.authors)
    known_authors = _author_keys(work.authors)
    union = incoming_authors | known_authors
    author_score = len(incoming_authors & known_authors) / len(union) if union else 0.0
    combined = (0.8 * title_score) + (0.2 * author_score)
    return combined, {"title": title_score, "authors": author_score}


SOURCE_RESPONSE_FIELDS = (
    "title",
    "authors",
    "abstract",
    "doi",
    "published_at",
    "source_updated_at",
    "landing_url",
    "field_provenance",
    "raw_payload",
)
IMPORTANT_CONTENT_FIELDS = frozenset(
    {"title", "authors", "abstract", "doi", "published_at"}
)
CANONICAL_FIELDS = ("title", "authors", "abstract", "doi", "published_at")
CANONICAL_SOURCE_ORDER = (
    "springer_nature",
    "ieee",
    "europe_pmc",
    "openreview",
    "arxiv",
)
CANONICAL_SOURCE_PRIORITY = {
    connector_id: len(CANONICAL_SOURCE_ORDER) - index
    for index, connector_id in enumerate(CANONICAL_SOURCE_ORDER)
}


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)


def _normalized_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat().replace("+00:00", "Z")


def _source_snapshot_from_paper(paper: SourcePaper, doi: str | None) -> dict[str, Any]:
    return {
        "connector_id": paper.connector_id.strip(),
        "external_id": paper.external_id.strip(),
        "title": paper.title.strip(),
        "authors": [author.strip() for author in paper.authors],
        "abstract": paper.abstract,
        "doi": doi,
        "published_at": _normalized_datetime(paper.published_at),
        "source_updated_at": _normalized_datetime(paper.updated_at),
        "landing_url": paper.landing_url,
        "field_provenance": dict(paper.field_provenance),
        "raw_payload": dict(paper.raw_payload),
    }


def _source_snapshot_from_record(record: SourceRecord) -> dict[str, Any]:
    return {
        "connector_id": record.connector_id,
        "external_id": record.external_id,
        "title": record.title,
        "authors": list(record.authors),
        "abstract": record.abstract,
        "doi": record.doi,
        "published_at": _normalized_datetime(record.published_at),
        "source_updated_at": _normalized_datetime(record.source_updated_at),
        "landing_url": record.landing_url,
        "field_provenance": dict(record.field_provenance),
        "raw_payload": dict(record.raw_payload),
    }


def _payload_sha256(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return [field for field in SOURCE_RESPONSE_FIELDS if before[field] != after[field]]


def _revision_kind(
    before: dict[str, Any],
    after: dict[str, Any],
    changed_fields: list[str],
) -> DiscoveryKind:
    if IMPORTANT_CONTENT_FIELDS.intersection(changed_fields):
        return DiscoveryKind.IMPORTANT_REVISION
    if "source_updated_at" in changed_fields:
        before_value = before["source_updated_at"]
        after_value = after["source_updated_at"]
        if after_value is not None and (before_value is None or after_value > before_value):
            return DiscoveryKind.IMPORTANT_REVISION
    return DiscoveryKind.METADATA_REFRESH


class IdentityRepository:
    """Single write boundary used by every connector during ingestion."""

    AUTO_MERGE_THRESHOLD = 0.97
    REVIEW_THRESHOLD = 0.75

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        work_index: WorkIndex | None = None,
    ) -> None:
        self._sessions = sessions
        self._work_index = work_index

    def ingest_candidates(
        self,
        papers: Iterable[SourcePaper],
        *,
        now: datetime | None = None,
        run_id: str | None = None,
    ) -> tuple[IngestResult, ...]:
        """Ingest one connector page without allowing an unbounded identity batch."""

        bounded = tuple(islice(papers, MAX_IDENTITY_BATCH_SIZE + 1))
        if len(bounded) > MAX_IDENTITY_BATCH_SIZE:
            raise ValueError(
                f"candidate identity batches may contain at most {MAX_IDENTITY_BATCH_SIZE} papers"
            )
        observed_at = _require_aware_utc(now or datetime.now(UTC))
        return tuple(
            self.ingest(paper, now=observed_at, run_id=run_id) for paper in bounded
        )

    def ingest(
        self,
        paper: SourcePaper,
        *,
        now: datetime | None = None,
        run_id: str | None = None,
    ) -> IngestResult:
        if not paper.connector_id.strip() or not paper.external_id.strip():
            raise ValueError("connector_id and external_id are required")
        if not normalize_title(paper.title):
            raise ValueError("title is required")
        observed_at = _require_aware_utc(now or datetime.now(UTC))
        doi = normalize_doi(paper.doi)
        sync_work_id: str | None = None

        with self._sessions() as session:
            existing_source = session.scalar(
                select(SourceRecord).where(
                    SourceRecord.connector_id == paper.connector_id,
                    SourceRecord.external_id == paper.external_id,
                )
            )
            if existing_source is not None:
                work = session.get(WorkRecord, existing_source.work_id)
                if work is None:  # pragma: no cover - protected by foreign keys
                    raise RuntimeError("Source record points to a missing Work")
                result, canonical_changed = self._refresh_existing_source(
                    session,
                    work=work,
                    source_record=existing_source,
                    paper=paper,
                    doi=doi,
                    observed_at=observed_at,
                    run_id=run_id,
                )
                session.commit()
                if canonical_changed:
                    sync_work_id = work.id
            else:
                result, sync_work_id = self._ingest_new_source(
                    session,
                    paper=paper,
                    doi=doi,
                    observed_at=observed_at,
                )
                session.commit()

        if self._work_index is not None and sync_work_id is not None:
            self._work_index.sync_work(sync_work_id)
        return result

    def _ingest_new_source(
        self,
        session: Session,
        *,
        paper: SourcePaper,
        doi: str | None,
        observed_at: datetime,
    ) -> tuple[IngestResult, str]:
        matched_work: WorkRecord | None = None
        match_kind = MatchKind.NEW
        merge_candidate: tuple[WorkRecord, float, dict[str, Any]] | None = None
        candidate_search: CandidateSearchDiagnostics | None = None

        if doi is not None:
            matched_work = session.scalar(
                select(WorkRecord).where(
                    WorkRecord.doi == doi,
                    WorkRecord.status == "active",
                )
            )
            if matched_work is not None:
                match_kind = MatchKind.EXACT_DOI

        if matched_work is None:
            candidates, candidate_search = self._identity_candidates(session, paper)
            best: tuple[WorkRecord, float, dict[str, Any], bool] | None = None
            for candidate in candidates:
                score, similarity_evidence = paper_similarity(paper, candidate)
                evidence: dict[str, Any] = dict(similarity_evidence)
                doi_conflict = (
                    doi is not None and candidate.doi is not None and doi != candidate.doi
                )
                if doi_conflict:
                    evidence["doi_conflict"] = 1.0
                evidence["candidate_strategy"] = candidate_search.strategy
                evidence["candidate_search_degraded"] = candidate_search.degraded
                if best is None or score > best[1]:
                    best = (candidate, score, evidence, doi_conflict)
            if best is not None and best[1] >= self.AUTO_MERGE_THRESHOLD and not best[3]:
                matched_work = best[0]
                match_kind = MatchKind.AUTO_SIMILARITY
            elif best is not None and best[1] >= self.REVIEW_THRESHOLD:
                merge_candidate = (best[0], best[1], best[2])
                match_kind = MatchKind.REVIEW_REQUIRED

        created_work = matched_work is None
        if matched_work is None:
            matched_work = self._new_work(paper, doi, observed_at=observed_at)
            session.add(matched_work)
            session.flush()

        source_record = self._source_record(
            matched_work.id,
            paper,
            doi,
            fetched_at=observed_at,
        )
        session.add(source_record)
        session.flush()
        baseline = self._source_revision(
            source_record,
            snapshot=_source_snapshot_from_paper(paper, doi),
            revision_kind="baseline",
            changed_fields=[],
            observed_at=observed_at,
        )
        session.add(baseline)

        merge_review: MergeReviewRecord | None = None
        if merge_candidate is not None:
            candidate, score, evidence = merge_candidate
            merge_review = MergeReviewRecord(
                candidate_work_id=matched_work.id,
                matched_work_id=candidate.id,
                similarity=score,
                evidence=evidence,
            )
            session.add(merge_review)

        if not created_work:
            self._recompute_canonical_work(session, matched_work, observed_at=observed_at)
        discovery_kind = (
            DiscoveryKind.NEW_WORK
            if created_work
            else DiscoveryKind.EXISTING_WORK_NEW_SOURCE
        )
        return (
            IngestResult(
                work=matched_work,
                source_record=source_record,
                match_kind=match_kind,
                discovery_kind=discovery_kind,
                merge_review=merge_review,
                candidate_search=candidate_search,
            ),
            matched_work.id,
        )

    def _identity_candidates(
        self,
        session: Session,
        paper: SourcePaper,
    ) -> tuple[tuple[WorkRecord, ...], CandidateSearchDiagnostics]:
        candidate_lookup = (
            getattr(self._work_index, "identity_candidates", None)
            if self._work_index is not None
            else None
        )
        if callable(candidate_lookup):
            try:
                candidate_ids = tuple(
                    dict.fromkeys(
                        candidate_lookup(
                            title=paper.title,
                            authors=paper.authors,
                            limit=IDENTITY_CANDIDATE_LIMIT,
                        )
                    )
                )[:IDENTITY_CANDIDATE_LIMIT]
            except OperationalError:
                pass
            else:
                works_by_id = {
                    work.id: work
                    for work in session.scalars(
                        select(WorkRecord).where(
                            WorkRecord.id.in_(candidate_ids),
                            WorkRecord.status == "active",
                        )
                    )
                } if candidate_ids else {}
                candidates = tuple(
                    works_by_id[work_id]
                    for work_id in candidate_ids
                    if work_id in works_by_id
                )
                return candidates, CandidateSearchDiagnostics(
                    strategy="fts",
                    degraded=False,
                    candidate_count=len(candidates),
                    limit=IDENTITY_CANDIDATE_LIMIT,
                )

        candidates, used_year = self._fallback_identity_candidates(session, paper)
        return candidates, CandidateSearchDiagnostics(
            strategy="sql_prefix_year" if used_year else "sql_prefix",
            degraded=True,
            candidate_count=len(candidates),
            limit=IDENTITY_CANDIDATE_LIMIT,
            reason="fts_unavailable",
        )

    @staticmethod
    def _fallback_identity_candidates(
        session: Session,
        paper: SourcePaper,
    ) -> tuple[tuple[WorkRecord, ...], bool]:
        normalized = normalize_title(paper.title)
        words = normalized.split()
        prefix = " ".join(words[:2]) if len(words) > 1 else normalized[:12]
        upper_bound = f"{prefix}\U0010ffff"
        prefix_budget = (
            IDENTITY_PREFIX_BUDGET
            if paper.published_at is not None
            else IDENTITY_CANDIDATE_LIMIT
        )
        exact_candidates = tuple(
            session.scalars(
                select(WorkRecord)
                .where(
                    WorkRecord.status == "active",
                    WorkRecord.normalized_title == normalized,
                )
                .order_by(WorkRecord.id)
                .limit(prefix_budget)
            )
        )
        remaining_prefix_budget = prefix_budget - len(exact_candidates)
        prefix_candidates = exact_candidates
        if remaining_prefix_budget:
            prefix_candidates = (
                *exact_candidates,
                *tuple(
                    session.scalars(
                        select(WorkRecord)
                        .where(
                            WorkRecord.status == "active",
                            WorkRecord.normalized_title >= prefix,
                            WorkRecord.normalized_title < upper_bound,
                            WorkRecord.normalized_title != normalized,
                        )
                        .order_by(WorkRecord.normalized_title, WorkRecord.id)
                        .limit(remaining_prefix_budget)
                    )
                ),
            )
        if paper.published_at is None:
            return prefix_candidates, False

        published = paper.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        else:
            published = published.astimezone(UTC)
        year_start = datetime(published.year, 1, 1, tzinfo=UTC)
        year_end = datetime(published.year + 1, 1, 1, tzinfo=UTC)
        prefix_ids = {work.id for work in prefix_candidates}
        year_candidates = tuple(
            session.scalars(
                select(WorkRecord)
                .where(
                    WorkRecord.status == "active",
                    WorkRecord.published_at >= year_start,
                    WorkRecord.published_at < year_end,
                    WorkRecord.id.not_in(tuple(sorted(prefix_ids))),
                )
                .order_by(WorkRecord.published_at, WorkRecord.id)
                .limit(IDENTITY_CANDIDATE_LIMIT - len(prefix_candidates))
            )
        )
        return (*prefix_candidates, *year_candidates), True

    def _refresh_existing_source(
        self,
        session: Session,
        *,
        work: WorkRecord,
        source_record: SourceRecord,
        paper: SourcePaper,
        doi: str | None,
        observed_at: datetime,
        run_id: str | None,
    ) -> tuple[IngestResult, bool]:
        incoming = _source_snapshot_from_paper(paper, doi)
        incoming_sha256 = _payload_sha256(incoming)
        prior_same_payload = session.scalar(
            select(SourceRevisionRecord).where(
                SourceRevisionRecord.source_record_id == source_record.id,
                SourceRevisionRecord.payload_sha256 == incoming_sha256,
            )
        )
        if prior_same_payload is not None:
            source_record.fetched_at = observed_at
            return (
                IngestResult(
                    work=work,
                    source_record=source_record,
                    match_kind=MatchKind.EXISTING_SOURCE,
                    discovery_kind=DiscoveryKind.UNCHANGED,
                ),
                False,
            )

        current = _source_snapshot_from_record(source_record)
        current_sha256 = _payload_sha256(current)
        previous_revision = session.scalar(
            select(SourceRevisionRecord).where(
                SourceRevisionRecord.source_record_id == source_record.id,
                SourceRevisionRecord.payload_sha256 == current_sha256,
            )
        )
        if previous_revision is None:
            previous_revision = self._source_revision(
                source_record,
                snapshot=current,
                revision_kind="baseline",
                changed_fields=[],
                observed_at=source_record.fetched_at,
            )
            session.add(previous_revision)
            session.flush()

        changed = _changed_fields(current, incoming)
        discovery_kind = _revision_kind(current, incoming, changed)
        self._apply_source_snapshot(
            source_record,
            incoming,
            observed_at=observed_at,
        )
        revision = self._source_revision(
            source_record,
            snapshot=incoming,
            revision_kind=discovery_kind.value,
            changed_fields=changed,
            observed_at=observed_at,
        )
        session.add(revision)
        session.flush()
        update_record = PaperUpdateRecord(
            run_id=run_id,
            work_id=work.id,
            source_record_id=source_record.id,
            previous_revision_id=previous_revision.id,
            current_revision_id=revision.id,
            update_type=discovery_kind.value,
            visibility=(
                "updates"
                if discovery_kind is DiscoveryKind.IMPORTANT_REVISION
                else "internal"
            ),
            state="unread",
            evidence={
                "changed_fields": changed,
                "previous_payload_sha256": previous_revision.payload_sha256,
                "current_payload_sha256": revision.payload_sha256,
            },
            detected_at=observed_at,
        )
        session.add(update_record)
        session.flush()

        canonical_changed = False
        if discovery_kind is DiscoveryKind.IMPORTANT_REVISION:
            canonical_changed = self._recompute_canonical_work(
                session,
                work,
                observed_at=observed_at,
            )
        return (
            IngestResult(
                work=work,
                source_record=source_record,
                match_kind=MatchKind.EXISTING_SOURCE,
                discovery_kind=discovery_kind,
                paper_update_id=update_record.id,
            ),
            canonical_changed,
        )

    def insert_source_record_for_test(self, *, work_id: str, paper: SourcePaper) -> None:
        """Exercise the database uniqueness boundary without weakening production ingestion."""
        with self._sessions() as session:
            session.add(self._source_record(work_id, paper, normalize_doi(paper.doi)))
            session.commit()

    def add_document_version(
        self,
        *,
        work_id: str,
        content_sha256: str,
        access_status: str,
        completeness: int,
        source_updated_at: datetime | None,
        source_record_id: str | None = None,
        media_type: str = "application/pdf",
    ) -> DocumentVersionRecord:
        if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
            raise ValueError("content_sha256 must be 64 lowercase hexadecimal characters")
        if access_status not in {*LEGAL_ACCESS, "restricted", "unknown"}:
            raise ValueError("unsupported access_status")
        if not 0 <= completeness <= 100:
            raise ValueError("completeness must be between 0 and 100")

        with self._sessions() as session:
            version = DocumentVersionRecord(
                work_id=work_id,
                source_record_id=source_record_id,
                content_sha256=content_sha256,
                access_status=access_status,
                completeness=completeness,
                source_updated_at=source_updated_at,
                media_type=media_type,
            )
            session.add(version)
            session.commit()
            return version

    def representative_document(self, work_id: str) -> DocumentVersionRecord | None:
        with self._sessions() as session:
            return session.scalar(
                select(DocumentVersionRecord)
                .where(
                    DocumentVersionRecord.work_id == work_id,
                    DocumentVersionRecord.access_status.in_(LEGAL_ACCESS),
                )
                .order_by(
                    DocumentVersionRecord.source_updated_at.desc(),
                    DocumentVersionRecord.completeness.desc(),
                    DocumentVersionRecord.created_at.desc(),
                )
                .limit(1)
            )

    def count_works(self) -> int:
        with self._sessions() as session:
            return (
                session.scalar(
                    select(func.count())
                    .select_from(WorkRecord)
                    .where(WorkRecord.status == "active")
                )
                or 0
            )

    def count_source_records(self) -> int:
        with self._sessions() as session:
            return session.scalar(select(func.count()).select_from(SourceRecord)) or 0

    def list_merge_reviews(self, *, status: str = "pending") -> list[MergeReview]:
        valid = {"pending", "merged", "kept_separate", "superseded"}
        if status not in valid:
            raise ValueError("unsupported merge review status")
        with self._sessions() as session:
            records = session.scalars(
                select(MergeReviewRecord)
                .where(MergeReviewRecord.status == status)
                .order_by(MergeReviewRecord.created_at, MergeReviewRecord.id)
            )
            return [self._review_domain(session, record) for record in records]

    def decide_merge_review(
        self,
        review_id: str,
        *,
        decision: Literal["merge", "keep_separate"],
        note: str | None = None,
        now: datetime | None = None,
    ) -> MergeReview:
        expected_status = "merged" if decision == "merge" else "kept_separate"
        decided_at = now or datetime.now(UTC)
        if decided_at.tzinfo is None or decided_at.utcoffset() is None:
            raise ValueError("decision datetime must be timezone-aware")
        decided_at = decided_at.astimezone(UTC)
        cleaned_note = " ".join(note.split()) if note is not None else None
        cleaned_note = cleaned_note or None

        with self._sessions() as session:
            review = session.get(MergeReviewRecord, review_id)
            if review is None:
                raise KeyError(review_id)
            if review.status != "pending":
                if review.status == expected_status:
                    return self._review_domain(session, review)
                raise MergeReviewAlreadyDecidedError(
                    f"Merge review was already decided as {review.status}"
                )

            candidate = session.get(WorkRecord, review.candidate_work_id)
            target = session.get(WorkRecord, review.matched_work_id)
            if candidate is None or target is None:  # protected by foreign keys
                raise MergeConflictError("Merge review references a missing Work")

            if decision == "merge":
                self._merge_works(
                    session,
                    review=review,
                    candidate=candidate,
                    target=target,
                    decided_at=decided_at,
                )

            review.status = expected_status
            review.decision_note = cleaned_note
            review.decided_by = "user"
            review.decided_at = decided_at
            session.commit()
            result = self._review_domain(session, review)
            changed_work_ids = (
                (target.id, candidate.id) if decision == "merge" else ()
            )

        if self._work_index is not None:
            for work_id in changed_work_ids:
                self._work_index.sync_work(work_id)
        return result

    @staticmethod
    def _merge_works(
        session: Session,
        *,
        review: MergeReviewRecord,
        candidate: WorkRecord,
        target: WorkRecord,
        decided_at: datetime,
    ) -> None:
        if candidate.status != "active" or target.status != "active":
            raise MergeConflictError("Only two active Works can be merged")
        if candidate.doi is not None and target.doi is not None and candidate.doi != target.doi:
            raise MergeConflictError("Works with different DOI values cannot be merged")

        if target.doi is None and candidate.doi is not None:
            transferred_doi = candidate.doi
            candidate.doi = None
            session.flush([candidate])
            target.doi = transferred_doi
        if target.abstract is None:
            target.abstract = candidate.abstract
        if target.published_at is None:
            target.published_at = candidate.published_at
        known_authors = {normalize_title(author) for author in target.authors}
        target.authors = [
            *target.authors,
            *(
                author
                for author in candidate.authors
                if normalize_title(author) not in known_authors
            ),
        ]
        target.updated_at = decided_at

        session.execute(
            update(SourceRecord)
            .where(SourceRecord.work_id == candidate.id)
            .values(work_id=target.id)
        )
        session.execute(
            update(PaperUpdateRecord)
            .where(PaperUpdateRecord.work_id == candidate.id)
            .values(work_id=target.id)
        )
        session.execute(
            update(RunCandidateRecord)
            .where(RunCandidateRecord.work_id == candidate.id)
            .values(work_id=target.id)
        )
        target_documents = {
            document.content_sha256: document
            for document in session.scalars(
                select(DocumentVersionRecord).where(
                    DocumentVersionRecord.work_id == target.id
                )
            )
        }
        candidate_documents = list(
            session.scalars(
                select(DocumentVersionRecord).where(DocumentVersionRecord.work_id == candidate.id)
            )
        )
        for document in candidate_documents:
            duplicate = target_documents.get(document.content_sha256)
            if duplicate is not None:
                IdentityRepository._merge_duplicate_document_reader_state(
                    session,
                    candidate_document=document,
                    target_document=duplicate,
                )
                session.delete(document)
            else:
                document.work_id = target.id
                target_documents[document.content_sha256] = document

        target_observation_keys = set(
            session.execute(
                select(
                    MetadataObservationRecord.enricher_id,
                    MetadataObservationRecord.observation_sha256,
                ).where(MetadataObservationRecord.work_id == target.id)
            ).tuples()
        )
        candidate_observations = list(
            session.scalars(
                select(MetadataObservationRecord).where(
                    MetadataObservationRecord.work_id == candidate.id
                )
            )
        )
        for observation in candidate_observations:
            identity = (observation.enricher_id, observation.observation_sha256)
            if identity in target_observation_keys:
                session.delete(observation)
            else:
                observation.work_id = target.id
                target_observation_keys.add(identity)

        IdentityRepository._merge_library_membership(
            session,
            candidate=candidate,
            target=target,
        )
        IdentityRepository._merge_preference_state(
            session,
            candidate=candidate,
            target=target,
            decided_at=decided_at,
        )

        candidate.status = "merged"
        candidate.merged_into_work_id = target.id
        candidate.updated_at = decided_at
        related = session.scalars(
            select(MergeReviewRecord).where(
                MergeReviewRecord.id != review.id,
                MergeReviewRecord.status == "pending",
                or_(
                    MergeReviewRecord.candidate_work_id == candidate.id,
                    MergeReviewRecord.matched_work_id == candidate.id,
                ),
            )
        )
        for pending in related:
            pending.status = "superseded"
            pending.decision_note = f"superseded_by:{review.id}"
            pending.decided_by = "system"
            pending.decided_at = decided_at

    @staticmethod
    def _merge_duplicate_document_reader_state(
        session: Session,
        *,
        candidate_document: DocumentVersionRecord,
        target_document: DocumentVersionRecord,
    ) -> None:
        """Rebind hash-identical user state before deleting a duplicate document row."""
        session.execute(
            update(AnnotationRecord)
            .where(AnnotationRecord.document_version_id == candidate_document.id)
            .values(document_version_id=target_document.id)
        )
        candidate_position = session.get(ReadingPositionRecord, candidate_document.id)
        if candidate_position is None:
            return
        target_position = session.get(ReadingPositionRecord, target_document.id)
        if target_position is None:
            candidate_position.document_version_id = target_document.id
            session.flush([candidate_position])
            return
        if IdentityRepository._aware_utc(
            candidate_position.updated_at
        ) > IdentityRepository._aware_utc(target_position.updated_at):
            target_position.document_content_sha256 = (
                candidate_position.document_content_sha256
            )
            target_position.page_number = candidate_position.page_number
            target_position.page_count = candidate_position.page_count
            target_position.scale = candidate_position.scale
            target_position.rotation = candidate_position.rotation
            target_position.progress = candidate_position.progress
            target_position.updated_at = candidate_position.updated_at
        target_position.created_at = min(
            IdentityRepository._aware_utc(target_position.created_at),
            IdentityRepository._aware_utc(candidate_position.created_at),
        )
        session.delete(candidate_position)
        session.flush()

    @staticmethod
    def _merge_library_membership(
        session: Session,
        *,
        candidate: WorkRecord,
        target: WorkRecord,
    ) -> None:
        target_entry = session.get(LibraryEntryRecord, target.id)
        candidate_entry = session.get(LibraryEntryRecord, candidate.id)
        if candidate_entry is None:
            return
        if target_entry is None:
            candidate_entry.work_id = target.id
            return
        if candidate_entry.created_at < target_entry.created_at:
            target_entry.added_via = candidate_entry.added_via
        target_entry.created_at = min(
            target_entry.created_at,
            candidate_entry.created_at,
        )
        target_entry.updated_at = max(
            target_entry.updated_at,
            candidate_entry.updated_at,
        )
        session.delete(candidate_entry)

    @staticmethod
    def _merge_preference_state(
        session: Session,
        *,
        candidate: WorkRecord,
        target: WorkRecord,
        decided_at: datetime,
    ) -> None:
        target_state = session.get(LibraryStateRecord, target.id)
        candidate_state = session.get(LibraryStateRecord, candidate.id)
        if candidate_state is not None:
            if target_state is None:
                candidate_state.work_id = target.id
            else:
                feedback_rank = {"neutral": 0, "like": 1, "dislike": 2}
                winning_rank = max(
                    feedback_rank[target_state.feedback],
                    feedback_rank[candidate_state.feedback],
                )
                winning_feedback = next(
                    feedback
                    for feedback, rank in feedback_rank.items()
                    if rank == winning_rank
                )
                reasons: list[str] = []
                seen_reasons: set[str] = set()
                if winning_feedback != "neutral":
                    for state in (target_state, candidate_state):
                        if state.feedback != winning_feedback:
                            continue
                        for reason in state.feedback_reasons:
                            identity = unicodedata.normalize("NFKC", reason).casefold()
                            if identity not in seen_reasons:
                                reasons.append(reason)
                                seen_reasons.add(identity)

                analysis_rank = {"not_analyzed": 0, "failed": 1, "available": 2}
                winning_analysis = max(
                    (target_state.analysis_state, candidate_state.analysis_state),
                    key=analysis_rank.__getitem__,
                )
                target_state.favorite = target_state.favorite or candidate_state.favorite
                target_state.feedback = winning_feedback
                target_state.feedback_reasons = reasons[:8]
                target_state.pinned = target_state.pinned or candidate_state.pinned
                target_state.reading_progress = max(
                    target_state.reading_progress,
                    candidate_state.reading_progress,
                )
                target_state.analysis_state = winning_analysis
                target_state.created_at = min(
                    target_state.created_at,
                    candidate_state.created_at,
                )
                target_state.updated_at = decided_at
                session.delete(candidate_state)

        session.execute(
            update(ImplicitPreferenceEventRecord)
            .where(ImplicitPreferenceEventRecord.work_id == candidate.id)
            .values(work_id=target.id)
        )

        target_aggregate = session.get(PreferenceAggregateRecord, target.id)
        candidate_aggregate = session.get(PreferenceAggregateRecord, candidate.id)
        if candidate_aggregate is not None:
            if target_aggregate is None:
                candidate_aggregate.work_id = target.id
            else:
                target_aggregate.total_weight += candidate_aggregate.total_weight
                target_aggregate.aggregated_through = max(
                    target_aggregate.aggregated_through,
                    candidate_aggregate.aggregated_through,
                )
                target_aggregate.updated_at = decided_at
                session.delete(candidate_aggregate)

    @classmethod
    def _review_domain(cls, session: Session, record: MergeReviewRecord) -> MergeReview:
        candidate = session.get(WorkRecord, record.candidate_work_id)
        matched = session.get(WorkRecord, record.matched_work_id)
        if candidate is None or matched is None:  # pragma: no cover - foreign keys
            raise RuntimeError("Merge review points to a missing Work")
        return MergeReview(
            id=record.id,
            candidate_work=cls._work_identity(session, candidate),
            matched_work=cls._work_identity(session, matched),
            similarity=record.similarity,
            evidence=dict(record.evidence),
            status=record.status,
            decision_note=record.decision_note,
            decided_by=record.decided_by,
            created_at=cls._aware_utc(record.created_at),
            decided_at=(
                cls._aware_utc(record.decided_at) if record.decided_at is not None else None
            ),
        )

    @staticmethod
    def _work_identity(session: Session, work: WorkRecord) -> WorkIdentity:
        source_count = (
            session.scalar(
                select(func.count())
                .select_from(SourceRecord)
                .where(SourceRecord.work_id == work.id)
            )
            or 0
        )
        return WorkIdentity(
            id=work.id,
            title=work.canonical_title,
            authors=tuple(work.authors),
            doi=work.doi,
            status=work.status,
            source_count=source_count,
        )

    @staticmethod
    def _aware_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _new_work(
        paper: SourcePaper,
        doi: str | None,
        *,
        observed_at: datetime | None = None,
    ) -> WorkRecord:
        return WorkRecord(
            canonical_title=paper.title.strip(),
            normalized_title=normalize_title(paper.title),
            authors=list(paper.authors),
            abstract=paper.abstract,
            doi=doi,
            published_at=paper.published_at,
            created_at=observed_at or datetime.now(UTC),
            updated_at=observed_at or datetime.now(UTC),
        )

    @staticmethod
    def _source_record(
        work_id: str,
        paper: SourcePaper,
        doi: str | None,
        *,
        fetched_at: datetime | None = None,
    ) -> SourceRecord:
        return SourceRecord(
            work_id=work_id,
            connector_id=paper.connector_id.strip(),
            external_id=paper.external_id.strip(),
            title=paper.title.strip(),
            normalized_title=normalize_title(paper.title),
            authors=list(paper.authors),
            abstract=paper.abstract,
            doi=doi,
            published_at=paper.published_at,
            source_updated_at=paper.updated_at,
            landing_url=paper.landing_url,
            field_provenance=dict(paper.field_provenance),
            raw_payload=dict(paper.raw_payload),
            fetched_at=fetched_at or datetime.now(UTC),
        )

    @staticmethod
    def _source_revision(
        source_record: SourceRecord,
        *,
        snapshot: dict[str, Any],
        revision_kind: str,
        changed_fields: list[str],
        observed_at: datetime,
    ) -> SourceRevisionRecord:
        return SourceRevisionRecord(
            source_record_id=source_record.id,
            work_id=source_record.work_id,
            revision_kind=revision_kind,
            payload_sha256=_payload_sha256(snapshot),
            snapshot=snapshot,
            changed_fields=changed_fields,
            field_provenance=dict(snapshot["field_provenance"]),
            source_updated_at=source_record.source_updated_at,
            fetched_at=observed_at,
            created_at=observed_at,
        )

    @staticmethod
    def _apply_source_snapshot(
        source_record: SourceRecord,
        snapshot: dict[str, Any],
        *,
        observed_at: datetime,
    ) -> None:
        source_record.title = str(snapshot["title"])
        source_record.normalized_title = normalize_title(source_record.title)
        source_record.authors = list(snapshot["authors"])
        source_record.abstract = snapshot["abstract"]
        source_record.doi = snapshot["doi"]
        source_record.published_at = IdentityRepository._parse_snapshot_datetime(
            snapshot["published_at"]
        )
        source_record.source_updated_at = IdentityRepository._parse_snapshot_datetime(
            snapshot["source_updated_at"]
        )
        source_record.landing_url = snapshot["landing_url"]
        source_record.field_provenance = dict(snapshot["field_provenance"])
        source_record.raw_payload = dict(snapshot["raw_payload"])
        source_record.fetched_at = observed_at

    @staticmethod
    def _parse_snapshot_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC)

    @classmethod
    def _recompute_canonical_work(
        cls,
        session: Session,
        work: WorkRecord,
        *,
        observed_at: datetime,
    ) -> bool:
        sources = tuple(
            session.scalars(
                select(SourceRecord)
                .where(SourceRecord.work_id == work.id)
                .order_by(SourceRecord.connector_id, SourceRecord.external_id)
            )
        )
        if not sources:
            return False

        selected = {
            field: cls._select_canonical_value(sources, field)
            for field in CANONICAL_FIELDS
        }
        before = (
            work.canonical_title,
            tuple(work.authors),
            work.abstract,
            work.doi,
            _normalized_datetime(work.published_at),
        )
        title = cast(str, selected["title"])
        work.canonical_title = title
        work.normalized_title = normalize_title(title)
        selected_authors = cast(list[str] | None, selected["authors"])
        work.authors = list(selected_authors) if selected_authors is not None else []
        work.abstract = cast(str | None, selected["abstract"])
        work.doi = cast(str | None, selected["doi"])
        work.published_at = cast(datetime | None, selected["published_at"])
        after = (
            work.canonical_title,
            tuple(work.authors),
            work.abstract,
            work.doi,
            _normalized_datetime(work.published_at),
        )
        if before != after:
            work.updated_at = observed_at
            return True
        return False

    @classmethod
    def _select_canonical_value(
        cls,
        sources: tuple[SourceRecord, ...],
        field: str,
    ) -> object:
        candidates = [source for source in sources if cls._source_field_value(source, field)]
        if not candidates:
            return None
        selected = max(
            candidates,
            key=lambda source: (
                CANONICAL_SOURCE_PRIORITY.get(source.connector_id, 0),
                cls._source_sort_time(source),
                cls._field_completeness(cls._source_field_value(source, field)),
                source.connector_id,
                source.external_id,
            ),
        )
        return cls._source_field_value(selected, field)

    @staticmethod
    def _source_field_value(source: SourceRecord, field: str) -> object:
        if field == "title":
            return source.title
        return getattr(source, field)

    @classmethod
    def _source_sort_time(cls, source: SourceRecord) -> datetime:
        value = source.source_updated_at or source.published_at or source.fetched_at
        return cls._aware_utc(value)

    @staticmethod
    def _field_completeness(value: object) -> int:
        if isinstance(value, str):
            return len(value.strip())
        if isinstance(value, list):
            return sum(len(str(item).strip()) for item in value)
        return 1 if value is not None else 0
