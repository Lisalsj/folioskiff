"""Bounded related-research hints derived only from durable local facts."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import islice
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import (
    DiscoveryPlanRecord,
    KeywordConfirmationRecord,
    KeywordSetVersionRecord,
    WorkRecord,
)
from paper_reader.discovery.preference_context import (
    PreferenceContext,
    PreferenceContextService,
)
from paper_reader.reader.models import LibraryEntryRecord
from paper_reader.recommendation.lexical import LexicalHit, LexicalIndex
from paper_reader.related_resources.references import reference_identities

MAX_LOCAL_CANDIDATES = 50
MAX_RELATED_WORKS = 3
MAX_SUGGESTED_TERMS = 5
MIN_LEXICAL_SCORE = 25.0
MAX_QUERY_TERMS = 12
MAX_SOURCE_TERMS = 48
MAX_WORK_TEXT_CHARACTERS = 100_000

_EXPLICIT_PREFERENCE_SOURCES = frozenset({"favorite", "liked", "interested_author"})

_LATIN_TERM = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,47}")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]{2,24}")
_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "against",
        "among",
        "analysis",
        "approach",
        "approaches",
        "based",
        "before",
        "between",
        "data",
        "evaluation",
        "for",
        "from",
        "into",
        "method",
        "methods",
        "model",
        "models",
        "paper",
        "results",
        "study",
        "their",
        "these",
        "this",
        "through",
        "using",
        "with",
        "without",
    }
)


class LocalExplorationHint(BaseModel):
    """One quiet, non-executable suggestion based on the local library."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    work_id: str = Field(min_length=1, max_length=80)
    suggested_terms: tuple[str, ...] = Field(min_length=1, max_length=MAX_SUGGESTED_TERMS)
    related_work_ids: tuple[str, ...] = Field(min_length=1, max_length=MAX_RELATED_WORKS)
    reason: str = Field(min_length=1, max_length=500)


class LexicalSearch(Protocol):
    def search(self, query: str, *, limit: int = 50) -> list[LexicalHit]: ...


@dataclass(frozen=True, slots=True)
class _Candidate:
    work: WorkRecord
    lexical_score: float
    overlap: tuple[str, ...]
    referenced: bool

    @property
    def sort_key(self) -> tuple[float, float, int, str]:
        return (
            -1.0 if self.referenced else 0.0,
            -self.lexical_score,
            -len(self.overlap),
            self.work.id,
        )


class LocalExplorationHintService:
    """Suggest local neighbors without importing an Agent or connector boundary."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        lexical_index: LexicalSearch | None = None,
    ) -> None:
        self._sessions = sessions
        self._lexical = lexical_index or LexicalIndex(sessions)

    def for_work(self, work_id: str) -> LocalExplorationHint | None:
        with self._sessions() as session:
            source = session.get(WorkRecord, work_id)
            if source is None or source.status != "active":
                return None
            source_terms = _extract_terms(
                f"{source.canonical_title}\n{source.abstract or ''}",
                limit=MAX_SOURCE_TERMS,
            )
            if len(source_terms) < 2:
                return None
            reference_dois = reference_identities(session, source.id).dois

        query = " ".join(source_terms[:MAX_QUERY_TERMS])
        hits = tuple(
            hit
            for hit in self._lexical.search(query, limit=MAX_LOCAL_CANDIDATES)
            if hit.work_id != source.id
        )
        strongest_neighbor = max(
            (max(0.0, -hit.raw_bm25) for hit in hits),
            default=0.0,
        )
        lexical_by_id = {
            hit.work_id: LexicalHit(
                work_id=hit.work_id,
                lexical_score=(
                    round(100.0 * max(0.0, -hit.raw_bm25) / strongest_neighbor, 6)
                    if strongest_neighbor > 0
                    else hit.lexical_score
                ),
                raw_bm25=hit.raw_bm25,
                matched_fields=hit.matched_fields,
            )
            for hit in hits
        }
        candidates = self._candidates(
            source=source,
            source_terms=source_terms,
            lexical_by_id=lexical_by_id,
            reference_dois=reference_dois,
        )
        if not candidates:
            return None

        with self._sessions() as session:
            plan_terms = self._confirmed_plan_terms(session)
        preference_terms = _explicit_preference_terms(
            PreferenceContextService(self._sessions).current()
        )

        selected = tuple(sorted(candidates, key=lambda item: item.sort_key)[:MAX_RELATED_WORKS])
        suggested = self._suggested_terms(
            source_terms=source_terms,
            selected=selected,
            plan_terms=plan_terms,
            preference_terms=preference_terms,
        )
        if not suggested:
            return None
        return LocalExplorationHint(
            work_id=source.id,
            suggested_terms=suggested,
            related_work_ids=tuple(item.work.id for item in selected),
            reason=self._reason(selected, suggested),
        )

    def _candidates(
        self,
        *,
        source: WorkRecord,
        source_terms: tuple[str, ...],
        lexical_by_id: Mapping[str, LexicalHit],
        reference_dois: tuple[str, ...],
    ) -> tuple[_Candidate, ...]:
        with self._sessions() as session:
            referenced = (
                tuple(
                    session.scalars(
                        select(WorkRecord)
                        .join(
                            LibraryEntryRecord,
                            LibraryEntryRecord.work_id == WorkRecord.id,
                        )
                        .where(
                            WorkRecord.status == "active",
                            WorkRecord.id != source.id,
                            WorkRecord.doi.in_(reference_dois),
                        )
                        .order_by(WorkRecord.id)
                        .limit(MAX_LOCAL_CANDIDATES)
                    )
                )
                if reference_dois
                else ()
            )
            candidate_ids = tuple(
                dict.fromkeys(
                    (
                        *(work.id for work in referenced),
                        *lexical_by_id.keys(),
                    )
                )
            )[:MAX_LOCAL_CANDIDATES]
            if not candidate_ids:
                return ()
            works = {
                work.id: work
                for work in session.scalars(
                    select(WorkRecord)
                    .join(
                        LibraryEntryRecord,
                        LibraryEntryRecord.work_id == WorkRecord.id,
                    )
                    .where(
                        WorkRecord.status == "active",
                        WorkRecord.id.in_(candidate_ids),
                    )
                    .order_by(WorkRecord.id)
                    .limit(MAX_LOCAL_CANDIDATES)
                )
            }

        source_identity = set(source_terms)
        reference_identity = set(reference_dois)
        accepted: list[_Candidate] = []
        for candidate_id in candidate_ids:
            work = works.get(candidate_id)
            if work is None:
                continue
            hit = lexical_by_id.get(candidate_id)
            score = hit.lexical_score if hit is not None else 0.0
            is_referenced = work.doi is not None and work.doi in reference_identity
            if not is_referenced and score < MIN_LEXICAL_SCORE:
                continue
            terms = _extract_terms(
                f"{work.canonical_title}\n{work.abstract or ''}",
                limit=MAX_SOURCE_TERMS,
            )
            overlap = tuple(sorted(source_identity.intersection(terms)))
            if not is_referenced and not overlap:
                continue
            accepted.append(
                _Candidate(
                    work=work,
                    lexical_score=score,
                    overlap=overlap,
                    referenced=is_referenced,
                )
            )
        return tuple(accepted)

    @classmethod
    def _suggested_terms(
        cls,
        *,
        source_terms: tuple[str, ...],
        selected: tuple[_Candidate, ...],
        plan_terms: tuple[str, ...],
        preference_terms: tuple[str, ...],
    ) -> tuple[str, ...]:
        source_identity = set(source_terms)
        scores: dict[str, float] = {}
        for candidate in selected:
            for term in _extract_terms(candidate.work.canonical_title, limit=24):
                if term not in source_identity:
                    scores[term] = scores.get(term, 0.0) + 3.0
            for term in _extract_terms(candidate.work.abstract or "", limit=32):
                if term not in source_identity:
                    scores[term] = scores.get(term, 0.0) + 1.0

        candidate_text = _normalize_text(
            "\n".join(
                f"{item.work.canonical_title}\n{item.work.abstract or ''}"[
                    :MAX_WORK_TEXT_CHARACTERS
                ]
                for item in selected
            )
        )
        source_text = " ".join(source_terms)
        for terms, bonus in ((plan_terms, 5.0), (preference_terms, 4.0)):
            for value in terms:
                phrase = _safe_phrase(value)
                if phrase and phrase not in source_text and phrase in candidate_text:
                    scores[phrase] = scores.get(phrase, 0.0) + bonus

        if not scores:
            for candidate in selected:
                for term in candidate.overlap:
                    scores[term] = scores.get(term, 0.0) + 1.0
        return tuple(
            term
            for term, _ in sorted(
                scores.items(),
                key=lambda item: (-item[1], item[0]),
            )[:MAX_SUGGESTED_TERMS]
        )

    @staticmethod
    def _reason(
        selected: tuple[_Candidate, ...],
        suggested: tuple[str, ...],
    ) -> str:
        references = sum(item.referenced for item in selected)
        lexical = len(selected) - references
        evidence: list[str] = []
        if references:
            evidence.append(f"{references} 篇引用关系")
        if lexical:
            evidence.append(f"{lexical} 篇达到本地词法阈值")
        return f"基于本地论文库的{'、'.join(evidence)}，可继续探索 {'、'.join(suggested)}。"

    @staticmethod
    def _confirmed_plan_terms(session: Session) -> tuple[str, ...]:
        versions = session.scalars(
            select(KeywordSetVersionRecord)
            .join(
                DiscoveryPlanRecord,
                DiscoveryPlanRecord.keyword_version_id == KeywordSetVersionRecord.id,
            )
            .join(
                KeywordConfirmationRecord,
                KeywordConfirmationRecord.keyword_version_id == KeywordSetVersionRecord.id,
            )
            .where(DiscoveryPlanRecord.enabled.is_(True))
            .order_by(DiscoveryPlanRecord.updated_at.desc(), DiscoveryPlanRecord.id)
            .limit(20)
        )
        terms: list[str] = []
        for version in versions:
            for group in (*version.required_groups, *version.boost_groups):
                if not isinstance(group, Mapping):
                    continue
                raw_terms = group.get("terms")
                if not isinstance(raw_terms, list):
                    continue
                for raw_term in islice(raw_terms, 20):
                    term = _safe_phrase(raw_term)
                    if term and term not in terms:
                        terms.append(term)
                    if len(terms) == 100:
                        return tuple(terms)
        return tuple(terms)


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _safe_phrase(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _normalize_text(value)
    if not normalized or len(normalized) > 120 or any(ord(char) < 32 for char in normalized):
        return None
    return normalized


def _extract_terms(value: str, *, limit: int) -> tuple[str, ...]:
    if limit < 1:
        return ()
    normalized = unicodedata.normalize("NFKC", value[:MAX_WORK_TEXT_CHARACTERS]).casefold()
    terms: list[str] = []
    for match in _LATIN_TERM.finditer(normalized):
        term = match.group(0)
        if term not in _STOPWORDS and term not in terms:
            terms.append(term)
        if len(terms) == limit:
            return tuple(terms)
    for run in _CJK_RUN.findall(normalized):
        candidates = (
            (run,)
            if len(run) <= 8
            else tuple(run[index : index + 4] for index in range(0, len(run) - 3, 2))
        )
        for term in candidates:
            if term not in terms:
                terms.append(term)
            if len(terms) == limit:
                return tuple(terms)
    return tuple(terms)


def _explicit_preference_terms(context: PreferenceContext) -> tuple[str, ...]:
    return tuple(
        item.term
        for item in context.terms
        if _EXPLICIT_PREFERENCE_SOURCES.intersection(item.sources)
    )


__all__ = [
    "MAX_LOCAL_CANDIDATES",
    "MAX_RELATED_WORKS",
    "MAX_SUGGESTED_TERMS",
    "LocalExplorationHint",
    "LocalExplorationHintService",
]
