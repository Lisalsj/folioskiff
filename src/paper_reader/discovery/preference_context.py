"""Privacy-reduced research terms derived from explicit local preferences."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy.orm import Session, sessionmaker

from paper_reader.recommendation.preferences import (
    PreferenceContextWorkEvidence,
    PreferenceRepository,
)

PREFERENCE_CONTEXT_VERSION = "preference-context-v1"
MAX_CONTEXT_TERMS = 12
MAX_CONTEXT_WORKS = 500

PreferenceSource = Literal[
    "favorite",
    "liked",
    "interested_author",
    "aggregated_behavior",
]
SOURCE_ORDER: tuple[PreferenceSource, ...] = (
    "liked",
    "favorite",
    "interested_author",
    "aggregated_behavior",
)

_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]{2,24}")
_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "against",
        "among",
        "approach",
        "based",
        "before",
        "between",
        "from",
        "into",
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


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


@dataclass(frozen=True, slots=True)
class PreferenceContextTerm:
    term: str
    weight: float
    sources: tuple[PreferenceSource, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "weight": self.weight,
            "sources": list(self.sources),
        }


@dataclass(frozen=True, slots=True)
class PreferenceContext:
    version: str
    terms: tuple[PreferenceContextTerm, ...]
    source_categories: tuple[PreferenceSource, ...]
    contributing_work_count: int
    context_sha256: str

    @property
    def empty(self) -> bool:
        return not self.terms

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "terms": [term.as_dict() for term in self.terms],
            "source_categories": list(self.source_categories),
            "contributing_work_count": self.contributing_work_count,
            "context_sha256": self.context_sha256,
            "empty": self.empty,
        }


@dataclass(slots=True)
class _TermEvidence:
    display: str
    score: float
    sources: set[PreferenceSource]


class PreferenceContextService:
    """Build a bounded prompt context without reading raw behavior or private content."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def current(self) -> PreferenceContext:
        works = PreferenceRepository(self._sessions).preference_context_works(
            limit=MAX_CONTEXT_WORKS
        )
        if not works:
            return self._context(terms=(), source_categories=(), work_count=0)

        evidence: dict[str, _TermEvidence] = {}
        all_sources: set[PreferenceSource] = set()
        contributing = 0
        for work in works:
            sources = set(cast(tuple[PreferenceSource, ...], work.sources))
            strength = self._source_strength(sources)
            aggregate = work.aggregated_weight
            if aggregate > 0:
                sources.add("aggregated_behavior")
                strength += min(3.0, math.log1p(aggregate))
            phrases = self._work_phrases(work)
            if not phrases:
                continue
            contributing += 1
            all_sources.update(sources)
            for phrase, field_weight in phrases:
                key = _normalize(phrase)
                if not key:
                    continue
                current = evidence.get(key)
                contribution = strength * field_weight
                if current is None:
                    evidence[key] = _TermEvidence(
                        display=phrase,
                        score=contribution,
                        sources=set(sources),
                    )
                else:
                    current.score += contribution
                    current.sources.update(sources)

        ranked = sorted(
            evidence.items(),
            key=lambda item: (-item[1].score, item[0]),
        )[:MAX_CONTEXT_TERMS]
        strongest = max((item.score for _, item in ranked), default=0.0)
        terms = tuple(
            PreferenceContextTerm(
                term=item.display,
                weight=(round(100 * item.score / strongest, 6) if strongest else 0.0),
                sources=self._ordered_sources(item.sources),
            )
            for _, item in ranked
        )
        return self._context(
            terms=terms,
            source_categories=self._ordered_sources(all_sources),
            work_count=contributing,
        )

    @staticmethod
    def _source_strength(sources: set[PreferenceSource]) -> float:
        return sum(
            {
                "favorite": 3.0,
                "liked": 4.0,
                "interested_author": 2.5,
                "aggregated_behavior": 0.0,
            }[source]
            for source in sources
        )

    @classmethod
    def _work_phrases(
        cls,
        work: PreferenceContextWorkEvidence,
    ) -> tuple[tuple[str, float], ...]:
        weighted: dict[str, tuple[str, float]] = {}
        for value, field_weight in (
            (work.title, 3.0),
            (work.abstract or "", 1.0),
        ):
            for phrase in cls._phrases(value):
                key = _normalize(phrase)
                current = weighted.get(key)
                if current is None or field_weight > current[1]:
                    weighted[key] = (phrase, field_weight)
        return tuple(weighted[key] for key in sorted(weighted))

    @staticmethod
    def _phrases(value: str) -> tuple[str, ...]:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        phrases: list[str] = []
        for segment in re.split(r"[^A-Za-z0-9-]+", normalized):
            tokens = _LATIN_TOKEN.findall(segment)
            if not tokens:
                continue
            meaningful: list[str] = []
            for token in tokens:
                if token in _STOPWORDS or len(token) > 48:
                    if len(meaningful) >= 2:
                        phrases.extend(
                            f"{meaningful[index]} {meaningful[index + 1]}"
                            for index in range(len(meaningful) - 1)
                        )
                    meaningful.clear()
                    continue
                meaningful.append(token)
                if len(token) >= 5:
                    phrases.append(token)
            if len(meaningful) >= 2:
                phrases.extend(
                    f"{meaningful[index]} {meaningful[index + 1]}"
                    for index in range(len(meaningful) - 1)
                )
        for run in _CJK_RUN.findall(normalized):
            if len(run) <= 8:
                phrases.append(run)
            else:
                phrases.extend(run[index : index + 4] for index in range(0, len(run) - 3, 2))
        return tuple(dict.fromkeys(phrase for phrase in phrases if _normalize(phrase)))

    @staticmethod
    def _ordered_sources(
        sources: set[PreferenceSource],
    ) -> tuple[PreferenceSource, ...]:
        return tuple(source for source in SOURCE_ORDER if source in sources)

    @classmethod
    def _context(
        cls,
        *,
        terms: tuple[PreferenceContextTerm, ...],
        source_categories: tuple[PreferenceSource, ...],
        work_count: int,
    ) -> PreferenceContext:
        payload = {
            "version": PREFERENCE_CONTEXT_VERSION,
            "terms": [term.as_dict() for term in terms],
            "source_categories": list(source_categories),
            "contributing_work_count": work_count,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return PreferenceContext(
            version=PREFERENCE_CONTEXT_VERSION,
            terms=terms,
            source_categories=source_categories,
            contributing_work_count=work_count,
            context_sha256=hashlib.sha256(encoded).hexdigest(),
        )
