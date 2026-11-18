"""Bounded academic-connector retrieval for a confirmed related-search intent."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.connectors import (
    ConnectorRegistry,
    ConnectorRequestError,
    SourceConnector,
    SourceQuery,
)
from paper_reader.discovery.identity import (
    IdentityRepository,
    IngestResult,
    MatchKind,
    SourcePaper,
    normalize_doi,
    normalize_title,
)
from paper_reader.discovery.keywords import (
    CompiledKeywordQuery,
    compile_concept_groups,
)
from paper_reader.discovery.plans import DateRangeSpec, ResolvedDateRange
from paper_reader.jobs.models import utc_now
from paper_reader.related_resources.contracts import (
    PaperVerificationStatus,
    RelatedSearchIntent,
    RelatedSetStatus,
)
from paper_reader.related_resources.references import (
    ReferenceIdentities,
    reference_identities,
)
from paper_reader.related_resources.repository import (
    RelatedIntentConflict,
    RelatedIntentRepository,
    RelatedIntentState,
)

MAX_ACADEMIC_CANDIDATES = 50
MAX_DISPLAYED_PAPERS = 20
MAX_PAGES_PER_SOURCE = 3
MAX_EXTERNAL_ID_CHARACTERS = 500
MAX_TITLE_CHARACTERS = 20_000
MAX_ABSTRACT_CHARACTERS = 500_000
MAX_AUTHOR_COUNT = 500
MAX_AUTHOR_CHARACTERS = 500
MAX_DOI_CHARACTERS = 255
MAX_RAW_DOI_CHARACTERS = 2_048
MAX_LANDING_URL_CHARACTERS = 2_048
MAX_FIELD_PROVENANCE_ITEMS = 100
MAX_RAW_PAYLOAD_BYTES = 1_000_000

AcademicSourceStatus = Literal["succeeded", "failed", "cancelled", "skipped"]
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE_TEXT_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DOI_IDENTITY = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class AcademicCandidate:
    connector_id: str
    external_id: str
    raw_doi: str | None
    normalized_doi: str | None
    title: str
    authors: tuple[str, ...]
    published_at: datetime | None
    updated_at: datetime | None
    landing_url: str | None
    publication_year: int | None
    linked_work_id: str | None
    merge_review_id: str | None
    match_kind: str
    reason: str
    match_evidence: dict[str, object]
    confidence: float
    verification_status: PaperVerificationStatus
    field_provenance: dict[str, str]
    cited_by_current_paper: bool


@dataclass(frozen=True, slots=True)
class AcademicCandidatePage:
    connector_id: str
    page_number: int
    cursor: str | None
    next_cursor: str | None
    fetched_count: int
    candidates: tuple[AcademicCandidate, ...]


@dataclass(frozen=True, slots=True)
class AcademicSourceSummary:
    connector_id: str
    status: AcademicSourceStatus
    pages_fetched: int = 0
    fetched_count: int = 0
    accepted_count: int = 0
    candidate_count: int = 0
    stop_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    retry_after_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class AcademicSearchSummary:
    set_id: str
    intent_revision: int
    months: tuple[str, ...]
    sources: Mapping[str, AcademicSourceSummary]
    candidates: tuple[AcademicCandidate, ...]
    display_candidates: tuple[AcademicCandidate, ...]
    total_fetched: int
    partial: bool
    cancelled: bool


PageSink = Callable[[AcademicCandidatePage], Awaitable[None]]
CancellationCheck = Callable[[], bool]


class AcademicRelatedSearch:
    """Query configured sources without allowing Agent text to define identity facts."""

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        connectors: ConnectorRegistry,
        identities: IdentityRepository,
        intents: RelatedIntentRepository,
        timezone: str = "Asia/Shanghai",
        source_filters: Mapping[str, Mapping[str, object]] | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("unknown related-search timezone") from error
        self._sessions = sessions
        self._connectors = connectors
        self._identities = identities
        self._intents = intents
        self._timezone = timezone
        self._source_filters = {
            connector_id: dict(filters) for connector_id, filters in (source_filters or {}).items()
        }
        self._clock = clock

    async def search(
        self,
        intent: RelatedSearchIntent,
        on_page: PageSink,
        cancelled: CancellationCheck,
        *,
        source_ids: tuple[str, ...] | None = None,
        candidate_limit: int = MAX_ACADEMIC_CANDIDATES,
    ) -> AcademicSearchSummary:
        state = self._confirmed_state(intent)
        if isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int):
            raise ValueError("candidate_limit must be an integer")
        if not 1 <= candidate_limit <= MAX_ACADEMIC_CANDIDATES:
            raise ValueError("candidate_limit must be between 1 and 50")
        selected_sources = intent.source_ids if source_ids is None else tuple(source_ids)
        if not selected_sources:
            raise ValueError("source_ids must contain at least one source")
        if len(selected_sources) != len(set(selected_sources)):
            raise ValueError("source_ids cannot contain duplicates")
        if any(source_id not in intent.source_ids for source_id in selected_sources):
            raise ValueError("source_ids must be a subset of the confirmed intent")
        now = _aware_utc(self._clock())
        resolved = DateRangeSpec(
            mode="rolling",
            rolling_months=min(intent.recent_months, 120),
            timezone=self._timezone,
        ).resolve(now)
        compiled = compile_concept_groups(
            f"related:{state.id}:{intent.revision}",
            required_groups=intent.required_groups,
            boost_terms=intent.optional_terms,
            excluded_groups=tuple((term,) for term in intent.excluded_terms),
        )
        with self._sessions() as session:
            references = reference_identities(session, state.work_id)

        summaries: dict[str, AcademicSourceSummary] = {}
        candidates: list[AcademicCandidate] = []
        seen_candidates: set[str] = set()
        total_fetched = 0
        search_cancelled = False

        for connector_index, connector_id in enumerate(selected_sources):
            if cancelled():
                search_cancelled = True
                self._mark_cancelled_sources(
                    summaries,
                    selected_sources[connector_index:],
                )
                break
            if total_fetched >= candidate_limit:
                summaries[connector_id] = AcademicSourceSummary(
                    connector_id=connector_id,
                    status="skipped",
                    stop_reason="candidate_limit",
                )
                continue
            connector = self._connectors.get(connector_id)
            if connector is None:
                summaries[connector_id] = AcademicSourceSummary(
                    connector_id=connector_id,
                    status="skipped",
                    error_code="connector_not_installed",
                )
                continue
            if not connector.capabilities.enabled:
                summaries[connector_id] = AcademicSourceSummary(
                    connector_id=connector_id,
                    status="skipped",
                    error_code=connector.capabilities.disabled_reason or "connector_disabled",
                )
                continue

            query = SourceQuery(
                connector_id=connector_id,
                months=resolved.months,
                compiled_keywords=compiled,
                filters=dict(self._source_filters.get(connector_id, {})),
                fingerprint=_query_fingerprint(
                    state,
                    connector_id=connector_id,
                    months=resolved.months,
                    compiled=compiled,
                    filters=self._source_filters.get(connector_id, {}),
                ),
            )
            source_summary, fetched_delta, source_cancelled = await self._search_source(
                connector=connector,
                query=query,
                resolved=resolved,
                references=references,
                source_work_id=state.work_id,
                seen_candidates=seen_candidates,
                candidates=candidates,
                already_fetched=total_fetched,
                candidate_limit=candidate_limit,
                on_page=on_page,
                cancelled=cancelled,
                now=now,
            )
            summaries[connector_id] = source_summary
            total_fetched += fetched_delta
            if source_cancelled:
                search_cancelled = True
                self._mark_cancelled_sources(
                    summaries,
                    selected_sources[connector_index + 1 :],
                )
                break

        ordered = tuple(sorted(candidates, key=_candidate_sort_key))
        statuses = {item.status for item in summaries.values()}
        return AcademicSearchSummary(
            set_id=state.id,
            intent_revision=intent.revision,
            months=resolved.months,
            sources=dict(summaries),
            candidates=ordered,
            display_candidates=ordered[:MAX_DISPLAYED_PAPERS],
            total_fetched=total_fetched,
            partial="failed" in statuses and ("succeeded" in statuses or bool(ordered)),
            cancelled=search_cancelled,
        )

    async def _search_source(
        self,
        *,
        connector: SourceConnector,
        query: SourceQuery,
        resolved: ResolvedDateRange,
        references: ReferenceIdentities,
        source_work_id: str,
        seen_candidates: set[str],
        candidates: list[AcademicCandidate],
        already_fetched: int,
        candidate_limit: int,
        on_page: PageSink,
        cancelled: CancellationCheck,
        now: datetime,
    ) -> tuple[AcademicSourceSummary, int, bool]:
        connector_id = connector.capabilities.connector_id
        pages_fetched = 0
        fetched_count = 0
        accepted_count = 0
        candidate_count = 0
        cursor: str | None = None
        seen_cursors: set[str] = set()
        stop_reason: str | None = None

        while pages_fetched < MAX_PAGES_PER_SOURCE:
            if cancelled():
                return (
                    AcademicSourceSummary(
                        connector_id=connector_id,
                        status="cancelled",
                        pages_fetched=pages_fetched,
                        fetched_count=fetched_count,
                        accepted_count=accepted_count,
                        candidate_count=candidate_count,
                        stop_reason="cancelled",
                    ),
                    fetched_count,
                    True,
                )
            remaining = candidate_limit - already_fetched - fetched_count
            if remaining <= 0:
                stop_reason = "candidate_limit"
                break
            try:
                page = await connector.fetch_page(query, cursor=cursor, limit=remaining)
            except ConnectorRequestError as error:
                return (
                    _failed_source_summary(
                        connector_id,
                        error,
                        pages_fetched=pages_fetched,
                        fetched_count=fetched_count,
                        accepted_count=accepted_count,
                        candidate_count=candidate_count,
                    ),
                    fetched_count,
                    False,
                )
            except Exception as error:
                internal = ConnectorRequestError(
                    "connector_internal_error",
                    type(error).__name__,
                    retryable=False,
                )
                return (
                    _failed_source_summary(
                        connector_id,
                        internal,
                        pages_fetched=pages_fetched,
                        fetched_count=fetched_count,
                        accepted_count=accepted_count,
                        candidate_count=candidate_count,
                    ),
                    fetched_count,
                    False,
                )

            pages_fetched += 1
            page_records = tuple(page.records[:remaining])
            fetched_count += len(page_records)
            prepared: list[SourcePaper] = []
            cited_flags: list[bool] = []
            raw_dois: list[str | None] = []
            try:
                for raw_paper in page_records:
                    paper = _validated_source_paper(raw_paper, connector_id)
                    cited = _is_cached_reference(paper, references)
                    in_window = paper.updated_at is not None and resolved.contains_updated_at(
                        paper.updated_at
                    )
                    if not in_window and not cited:
                        continue
                    if not query.compiled_keywords.matches(
                        title=paper.title,
                        abstract=paper.abstract,
                    ):
                        continue
                    prepared.append(paper)
                    cited_flags.append(cited)
                    raw_dois.append(raw_paper.doi)
                ingested = self._identities.ingest_candidates(prepared, now=now)
            except ValueError as error:
                incompatible = ConnectorRequestError(
                    "response_incompatible",
                    str(error),
                    retryable=False,
                )
                return (
                    _failed_source_summary(
                        connector_id,
                        incompatible,
                        pages_fetched=pages_fetched,
                        fetched_count=fetched_count,
                        accepted_count=accepted_count,
                        candidate_count=candidate_count,
                    ),
                    fetched_count,
                    False,
                )

            accepted_count += len(prepared)
            published: list[AcademicCandidate] = []
            for paper, result, cited, raw_doi in zip(
                prepared,
                ingested,
                cited_flags,
                raw_dois,
                strict=True,
            ):
                candidate = _candidate_from_ingest(
                    paper,
                    result,
                    cited=cited,
                    compiled=query.compiled_keywords,
                    raw_doi=raw_doi,
                )
                if candidate.linked_work_id == source_work_id:
                    continue
                identity = _candidate_identity(candidate)
                if identity in seen_candidates:
                    continue
                seen_candidates.add(identity)
                candidates.append(candidate)
                published.append(candidate)
                candidate_count += 1
            if published:
                await on_page(
                    AcademicCandidatePage(
                        connector_id=connector_id,
                        page_number=pages_fetched,
                        cursor=cursor,
                        next_cursor=page.next_cursor,
                        fetched_count=len(page_records),
                        candidates=tuple(published),
                    )
                )

            if cancelled():
                stop_reason = "cancelled"
                break
            if already_fetched + fetched_count >= candidate_limit:
                stop_reason = "candidate_limit"
                break
            if page.next_cursor is None or not page_records:
                stop_reason = "source_exhausted"
                break
            if page.next_cursor == cursor or page.next_cursor in seen_cursors:
                incompatible = ConnectorRequestError(
                    "response_incompatible",
                    "connector returned a repeated cursor",
                    retryable=False,
                )
                return (
                    _failed_source_summary(
                        connector_id,
                        incompatible,
                        pages_fetched=pages_fetched,
                        fetched_count=fetched_count,
                        accepted_count=accepted_count,
                        candidate_count=candidate_count,
                    ),
                    fetched_count,
                    False,
                )
            if cursor is not None:
                seen_cursors.add(cursor)
            cursor = page.next_cursor

        if stop_reason == "cancelled":
            return (
                AcademicSourceSummary(
                    connector_id=connector_id,
                    status="cancelled",
                    pages_fetched=pages_fetched,
                    fetched_count=fetched_count,
                    accepted_count=accepted_count,
                    candidate_count=candidate_count,
                    stop_reason=stop_reason,
                ),
                fetched_count,
                True,
            )
        if stop_reason is None:
            stop_reason = "page_limit"
        return (
            AcademicSourceSummary(
                connector_id=connector_id,
                status="succeeded",
                pages_fetched=pages_fetched,
                fetched_count=fetched_count,
                accepted_count=accepted_count,
                candidate_count=candidate_count,
                stop_reason=stop_reason,
            ),
            fetched_count,
            False,
        )

    def _confirmed_state(self, intent: RelatedSearchIntent) -> RelatedIntentState:
        if not isinstance(intent, RelatedSearchIntent) or not intent.confirmed:
            raise ValueError("academic related search requires a confirmed intent")
        if intent.set_id is None:
            raise ValueError("confirmed related-search intent requires a set_id")
        state = self._intents.get(intent.set_id)
        if state.intent != intent:
            raise RelatedIntentConflict(
                "related-search intent is not the current persisted revision"
            )
        if state.status not in {
            RelatedSetStatus.CONFIRMED,
            RelatedSetStatus.QUEUED,
            RelatedSetStatus.RUNNING,
        }:
            raise RelatedIntentConflict("related-search intent is not executable")
        return state

    @staticmethod
    def _mark_cancelled_sources(
        summaries: dict[str, AcademicSourceSummary],
        source_ids: tuple[str, ...],
    ) -> None:
        for connector_id in source_ids:
            summaries.setdefault(
                connector_id,
                AcademicSourceSummary(
                    connector_id=connector_id,
                    status="cancelled",
                    stop_reason="cancelled",
                ),
            )


def _validated_source_paper(paper: SourcePaper, connector_id: str) -> SourcePaper:
    if not isinstance(paper, SourcePaper):
        raise ValueError("connector candidate must be a SourcePaper")
    if not isinstance(paper.connector_id, str):
        raise ValueError("connector candidate connector_id must be a string")
    actual_connector = paper.connector_id.strip().casefold()
    if actual_connector != connector_id:
        raise ValueError("connector candidate source identity does not match the request")
    if not isinstance(paper.external_id, str):
        raise ValueError("connector candidate external_id must be a string")
    external_id = paper.external_id.strip()
    if (
        not external_id
        or len(external_id) > MAX_EXTERNAL_ID_CHARACTERS
        or _CONTROL_CHARACTER.search(external_id)
    ):
        raise ValueError("connector candidate external_id is invalid")
    if not isinstance(paper.title, str):
        raise ValueError("connector candidate title must be a string")
    title = paper.title.strip()
    if (
        not title
        or len(title) > MAX_TITLE_CHARACTERS
        or _CONTROL_CHARACTER.search(title)
        or not normalize_title(title)
    ):
        raise ValueError("connector candidate title is invalid")
    if not isinstance(paper.authors, tuple) or len(paper.authors) > MAX_AUTHOR_COUNT:
        raise ValueError("connector candidate has too many authors")
    if any(not isinstance(author, str) for author in paper.authors):
        raise ValueError("connector candidate authors must be strings")
    authors = tuple(author.strip() for author in paper.authors)
    if any(
        not author or len(author) > MAX_AUTHOR_CHARACTERS or _CONTROL_CHARACTER.search(author)
        for author in authors
    ):
        raise ValueError("connector candidate authors are invalid")
    if paper.abstract is not None and (
        not isinstance(paper.abstract, str)
        or len(paper.abstract) > MAX_ABSTRACT_CHARACTERS
        or _UNSAFE_TEXT_CONTROL.search(paper.abstract)
    ):
        raise ValueError("connector candidate abstract is invalid")
    for value in (paper.published_at, paper.updated_at):
        if value is not None and (
            not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("connector candidate dates must include a timezone")
    if paper.doi is not None and (
        not isinstance(paper.doi, str)
        or len(paper.doi) > MAX_RAW_DOI_CHARACTERS
        or _CONTROL_CHARACTER.search(paper.doi)
    ):
        raise ValueError("connector candidate DOI is invalid")
    if (
        not isinstance(paper.field_provenance, dict)
        or len(paper.field_provenance) > MAX_FIELD_PROVENANCE_ITEMS
        or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or not value
            or len(key) > 200
            or len(value) > 500
            or _CONTROL_CHARACTER.search(key)
            or _CONTROL_CHARACTER.search(value)
            for key, value in paper.field_provenance.items()
        )
    ):
        raise ValueError("connector candidate field provenance is invalid")
    if not isinstance(paper.raw_payload, dict):
        raise ValueError("connector candidate raw payload must be an object")
    try:
        raw_payload_bytes = json.dumps(
            paper.raw_payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as error:
        raise ValueError("connector candidate raw payload is invalid") from error
    if len(raw_payload_bytes) > MAX_RAW_PAYLOAD_BYTES:
        raise ValueError("connector candidate raw payload is too large")
    normalized_doi = normalize_doi(paper.doi)
    if normalized_doi is not None and (
        len(normalized_doi) > MAX_DOI_CHARACTERS
        or _CONTROL_CHARACTER.search(normalized_doi)
        or _DOI_IDENTITY.fullmatch(normalized_doi) is None
    ):
        normalized_doi = None
    landing_url = _validated_landing_url(paper.landing_url)
    return replace(
        paper,
        connector_id=actual_connector,
        external_id=external_id,
        title=title,
        authors=authors,
        doi=normalized_doi,
        landing_url=landing_url,
        field_provenance=dict(paper.field_provenance),
        raw_payload=dict(paper.raw_payload),
    )


def _validated_landing_url(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("connector candidate landing_url must be a string")
    url = value.strip()
    if not url or len(url) > MAX_LANDING_URL_CHARACTERS or _CONTROL_CHARACTER.search(url):
        raise ValueError("connector candidate landing_url is invalid")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValueError("connector candidate landing_url is invalid") from error
    hostname = parsed.hostname
    if (
        parsed.scheme.casefold() != "https"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or hostname.casefold() == "localhost"
        or hostname.casefold().endswith(".local")
        or "." not in hostname
    ):
        raise ValueError("connector candidate landing_url must be public HTTPS")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return url
    raise ValueError("connector candidate landing_url cannot use an IP literal")


def _candidate_from_ingest(
    paper: SourcePaper,
    result: IngestResult,
    *,
    cited: bool,
    compiled: CompiledKeywordQuery,
    raw_doi: str | None,
) -> AcademicCandidate:
    review = result.merge_review
    linked_work_id = None if review is not None else result.work.id
    merge_review_id = review.id if review is not None else None
    optional_matches = tuple(
        term
        for term in compiled.boost_terms
        if _normalized(term) in _normalized(f"{paper.title}\n{paper.abstract or ''}")
    )
    evidence: dict[str, object] = {
        "match_kind": result.match_kind.value,
        "discovery_kind": result.discovery_kind.value,
        "optional_term_matches": list(optional_matches),
        "cited_by_current_paper": cited,
        "date_basis": "updated_at",
    }
    if review is not None:
        evidence["merge_similarity"] = review.similarity
        evidence["merge_evidence"] = dict(review.evidence)
    if result.candidate_search is not None:
        evidence["identity_candidate_search"] = {
            "strategy": result.candidate_search.strategy,
            "degraded": result.candidate_search.degraded,
            "candidate_count": result.candidate_search.candidate_count,
            "limit": result.candidate_search.limit,
            "reason": result.candidate_search.reason,
        }
    if review is not None:
        confidence = round(max(0.0, min(100.0, review.similarity * 100)), 6)
        verification = PaperVerificationStatus.PENDING_VERIFICATION
        identity_reason = "title-author similarity requires merge review"
    elif paper.doi is not None or result.match_kind in {
        MatchKind.EXISTING_SOURCE,
        MatchKind.EXACT_DOI,
    }:
        confidence = 100.0
        verification = PaperVerificationStatus.VERIFIED
        identity_reason = "verified source or DOI identity"
    elif result.match_kind is MatchKind.AUTO_SIMILARITY:
        confidence = 97.0
        verification = PaperVerificationStatus.VERIFIED
        identity_reason = "high-confidence title-author identity"
    else:
        confidence = 85.0
        verification = PaperVerificationStatus.VERIFIED
        identity_reason = "verified connector and external source identity"
    publication_basis = paper.published_at or paper.updated_at
    return AcademicCandidate(
        connector_id=paper.connector_id,
        external_id=paper.external_id,
        raw_doi=raw_doi,
        normalized_doi=paper.doi,
        title=paper.title,
        authors=paper.authors,
        published_at=paper.published_at,
        updated_at=paper.updated_at,
        landing_url=paper.landing_url,
        publication_year=publication_basis.year if publication_basis is not None else None,
        linked_work_id=linked_work_id,
        merge_review_id=merge_review_id,
        match_kind=result.match_kind.value,
        reason=f"Matched the confirmed intent through {paper.connector_id}; {identity_reason}.",
        match_evidence=evidence,
        confidence=confidence,
        verification_status=verification,
        field_provenance=dict(paper.field_provenance),
        cited_by_current_paper=cited,
    )


def _is_cached_reference(paper: SourcePaper, references: ReferenceIdentities) -> bool:
    return (
        paper.doi is not None
        and paper.doi in references.dois
        or (paper.connector_id, paper.external_id) in references.source_ids
    )


def _candidate_identity(candidate: AcademicCandidate) -> str:
    if candidate.normalized_doi is not None:
        return f"doi:{candidate.normalized_doi}"
    if candidate.linked_work_id is not None:
        return f"work:{candidate.linked_work_id}"
    if candidate.merge_review_id is not None:
        return f"review:{candidate.merge_review_id}"
    return f"source:{candidate.connector_id}:{candidate.external_id}"


def _candidate_sort_key(candidate: AcademicCandidate) -> tuple[object, ...]:
    optional_matches = candidate.match_evidence.get("optional_term_matches", [])
    optional_count = len(optional_matches) if isinstance(optional_matches, list) else 0
    updated = candidate.updated_at or candidate.published_at or datetime.min.replace(tzinfo=UTC)
    return (
        -int(candidate.cited_by_current_paper),
        -optional_count,
        -candidate.confidence,
        -updated.timestamp(),
        candidate.connector_id,
        candidate.external_id,
    )


def _failed_source_summary(
    connector_id: str,
    error: ConnectorRequestError,
    *,
    pages_fetched: int,
    fetched_count: int,
    accepted_count: int,
    candidate_count: int,
) -> AcademicSourceSummary:
    return AcademicSourceSummary(
        connector_id=connector_id,
        status="failed",
        pages_fetched=pages_fetched,
        fetched_count=fetched_count,
        accepted_count=accepted_count,
        candidate_count=candidate_count,
        error_code=error.code,
        error_message=str(error),
        retryable=error.retryable,
        retry_after_seconds=error.retry_after_seconds,
    )


def _query_fingerprint(
    state: RelatedIntentState,
    *,
    connector_id: str,
    months: tuple[str, ...],
    compiled: CompiledKeywordQuery,
    filters: Mapping[str, object],
) -> str:
    payload = json.dumps(
        {
            "set_id": state.id,
            "intent_revision": state.intent.revision,
            "connector_id": connector_id,
            "months": months,
            "boolean_expression": compiled.boolean_expression,
            "filters": dict(filters),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("related-search clock must include a timezone")
    return value.astimezone(UTC)


__all__ = [
    "AcademicCandidate",
    "AcademicCandidatePage",
    "AcademicRelatedSearch",
    "AcademicSearchSummary",
    "AcademicSourceSummary",
    "MAX_ACADEMIC_CANDIDATES",
    "MAX_DISPLAYED_PAPERS",
    "MAX_PAGES_PER_SOURCE",
]
