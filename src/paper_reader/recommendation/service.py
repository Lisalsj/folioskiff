"""Candidate-scoped relevance scoring for immutable discovery runs."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.keywords import KeywordSetDefinition
from paper_reader.discovery.models import SourceRecord, WorkRecord
from paper_reader.discovery.runs import DiscoveryRun
from paper_reader.recommendation.lexical import TOKENIZER_VERSION, LexicalHit
from paper_reader.recommendation.relevance import (
    KeywordAlignment,
    MetricEvidence,
    WeightedScore,
    score_keyword_alignment,
    score_relevance,
)
from paper_reader.recommendation.semantic import SemanticHit


class RunCandidates(Protocol):
    def get(self, run_id: str) -> DiscoveryRun: ...

    def list_candidate_work_ids(self, run_id: str) -> tuple[str, ...]: ...


class LexicalRetriever(Protocol):
    def search(self, query: str, *, limit: int = 50) -> list[LexicalHit]: ...


class SemanticRetriever(Protocol):
    def sync_work(self, work_id: str) -> bool: ...

    def score_candidates(
        self,
        query: str,
        work_ids: tuple[str, ...],
    ) -> dict[str, SemanticHit]: ...


@dataclass(frozen=True, slots=True)
class CandidateRelevance:
    work_id: str
    title: str
    authors: tuple[str, ...]
    published_at: datetime | None
    updated_at: datetime | None
    source_ids: tuple[str, ...]
    eligible: bool
    lexical_hit: LexicalHit | None
    semantic_hit: SemanticHit | None
    keyword_alignment: KeywordAlignment
    relevance: WeightedScore


@dataclass(frozen=True, slots=True)
class RunRelevanceResult:
    run_id: str
    keyword_version_id: str
    query: str
    candidates: tuple[CandidateRelevance, ...]


class RelevanceService:
    """Compose deterministic evidence only for papers accepted by one run."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        runs: RunCandidates,
        lexical: LexicalRetriever,
        semantic: SemanticRetriever | None,
        semantic_degradation_reasons: tuple[str, ...] = (),
    ) -> None:
        self._sessions = sessions
        self._runs = runs
        self._lexical = lexical
        self._semantic = semantic
        self._semantic_degradation_reasons = semantic_degradation_reasons

    def score_run(self, run_id: str) -> RunRelevanceResult:
        run = self._runs.get(run_id)
        definition = KeywordSetDefinition.model_validate(
            run.keyword_snapshot["definition"]
        )
        query = self._query(definition)
        work_ids = self._runs.list_candidate_work_ids(run_id)
        lexical_hits = {
            hit.work_id: hit
            for hit in self._lexical.search(query, limit=500)
            if hit.work_id in work_ids
        }
        semantic_hits, semantic_failure = self._semantic_scores(query, work_ids)

        with self._sessions() as session:
            works = {
                work_id: work
                for work_id in work_ids
                if (work := session.get(WorkRecord, work_id)) is not None
            }
            source_records = tuple(
                session.scalars(
                    select(SourceRecord)
                    .where(SourceRecord.work_id.in_(work_ids))
                    .order_by(SourceRecord.connector_id, SourceRecord.external_id)
                )
            )
        source_ids: dict[str, list[str]] = {}
        updated_at: dict[str, datetime] = {}
        for record in source_records:
            source_ids.setdefault(record.work_id, []).append(record.connector_id)
            observed = record.source_updated_at or record.published_at
            if observed is not None:
                normalized = self._utc(observed)
                previous = updated_at.get(record.work_id)
                if previous is None or normalized > previous:
                    updated_at[record.work_id] = normalized

        candidates = tuple(
            sorted(
                (
                    self._score_candidate(
                        work=works[work_id],
                        definition=definition,
                        query=query,
                        lexical_hit=lexical_hits.get(work_id),
                        semantic_hit=semantic_hits.get(work_id),
                        semantic_failure=semantic_failure,
                        updated_at=updated_at.get(work_id),
                        source_ids=tuple(dict.fromkeys(source_ids.get(work_id, ()))),
                    )
                    for work_id in work_ids
                    if work_id in works
                ),
                key=lambda candidate: (-candidate.relevance.score, candidate.work_id),
            )
        )
        return RunRelevanceResult(
            run_id=run.id,
            keyword_version_id=run.keyword_version_id,
            query=query,
            candidates=candidates,
        )

    def _semantic_scores(
        self,
        query: str,
        work_ids: tuple[str, ...],
    ) -> tuple[dict[str, SemanticHit], str | None]:
        if self._semantic is None:
            reason = next(
                iter(self._semantic_degradation_reasons),
                "semantic_runtime_unavailable",
            )
            return {}, reason
        try:
            for work_id in work_ids:
                self._semantic.sync_work(work_id)
            return self._semantic.score_candidates(query, work_ids), None
        except Exception:  # The optional native/model boundary must degrade atomically.
            return {}, "semantic_runtime_failed"

    @staticmethod
    def _score_candidate(
        *,
        work: WorkRecord,
        definition: KeywordSetDefinition,
        query: str,
        lexical_hit: LexicalHit | None,
        semantic_hit: SemanticHit | None,
        semantic_failure: str | None,
        updated_at: datetime | None,
        source_ids: tuple[str, ...],
    ) -> CandidateRelevance:
        alignment = score_keyword_alignment(
            definition,
            title=work.canonical_title,
            abstract=work.abstract,
        )
        lexical = MetricEvidence.observed(
            lexical_hit.lexical_score if lexical_hit is not None else 0,
            evidence={
                "query": query,
                "tokenizer_version": TOKENIZER_VERSION,
                "raw_bm25": lexical_hit.raw_bm25 if lexical_hit is not None else None,
                "matched_fields": (
                    lexical_hit.matched_fields if lexical_hit is not None else ()
                ),
            },
        )
        if semantic_hit is not None:
            semantic = MetricEvidence.observed(
                semantic_hit.semantic_score,
                evidence={"query": query, "distance": semantic_hit.distance},
            )
        else:
            semantic = MetricEvidence.unknown(
                reason=semantic_failure or "semantic_candidate_unavailable",
                evidence={"query": query},
            )
        keyword = MetricEvidence.observed(
            alignment.score,
            evidence={
                "required_matches": dict(alignment.required_matches),
                "boost_matches": dict(alignment.boost_matches),
                "excluded_matches": dict(alignment.excluded_matches),
                "eligible": alignment.eligible,
            },
        )
        return CandidateRelevance(
            work_id=work.id,
            title=work.canonical_title,
            authors=tuple(work.authors),
            published_at=(
                RelevanceService._utc(work.published_at)
                if work.published_at is not None
                else None
            ),
            updated_at=updated_at,
            source_ids=source_ids,
            eligible=alignment.eligible,
            lexical_hit=lexical_hit,
            semantic_hit=semantic_hit,
            keyword_alignment=alignment,
            relevance=score_relevance(
                lexical=lexical,
                semantic=semantic,
                keyword=keyword,
            ),
        )

    @staticmethod
    def _query(definition: KeywordSetDefinition) -> str:
        terms: list[str] = []
        seen: set[str] = set()
        for group in (*definition.required, *definition.boost):
            for term in group.terms:
                identity = unicodedata.normalize("NFKC", term).casefold()
                if identity not in seen:
                    terms.append(term)
                    seen.add(identity)
        return " ".join(terms)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
