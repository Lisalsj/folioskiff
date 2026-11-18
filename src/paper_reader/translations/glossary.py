"""Deterministic paper-level terminology extraction and user overrides."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from paper_reader.translations.contracts import normalize_source_term
from paper_reader.translations.segmenter import SourceSegment

GlossaryOrigin = Literal["local_extraction", "user", "agent"]

_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
_HYPHENATED = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b")
_ABBREVIATION_DEFINITION = re.compile(
    r"\b(?P<long>[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*){1,7})"
    r"\s*\((?P<short>[A-Z][A-Z0-9-]{1,15})\)"
)
_CAPITALIZED_PHRASE = re.compile(r"\b[A-Z][A-Za-z0-9-]+(?:\s+[A-Z][A-Za-z0-9-]+){1,5}\b")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)


@dataclass(frozen=True, slots=True)
class GlossaryTerm:
    source_term: str
    target_term: str | None = None
    origin: GlossaryOrigin = "local_extraction"
    locked: bool = False
    normalized_source_term: str = field(init=False)

    def __post_init__(self) -> None:
        source = _term_text(self.source_term, name="source_term", maximum=500)
        normalized = normalize_source_term(source)
        if not normalized or not any(character.isalnum() for character in normalized):
            raise ValueError("source_term must contain a letter or number")
        target = (
            _term_text(self.target_term, name="target_term", maximum=1_000)
            if self.target_term is not None
            else None
        )
        if self.origin not in {"local_extraction", "user", "agent"}:
            raise ValueError("glossary origin is invalid")
        if self.locked and target is None:
            raise ValueError("locked glossary terms require a target translation")
        object.__setattr__(self, "source_term", source)
        object.__setattr__(self, "target_term", target)
        object.__setattr__(self, "normalized_source_term", normalized)


@dataclass(frozen=True, slots=True)
class GlossaryTermUpdate:
    source_term: str
    target_term: str
    locked: bool = True
    normalized_source_term: str = field(init=False)

    def __post_init__(self) -> None:
        source = _term_text(self.source_term, name="source_term", maximum=500)
        target = _term_text(self.target_term, name="target_term", maximum=1_000)
        normalized = normalize_source_term(source)
        if not normalized or not any(character.isalnum() for character in normalized):
            raise ValueError("source_term must contain a letter or number")
        object.__setattr__(self, "source_term", source)
        object.__setattr__(self, "target_term", target)
        object.__setattr__(self, "normalized_source_term", normalized)


class GlossaryBuilder:
    """Extract bounded terminology without invoking an Agent backend."""

    def __init__(self, *, max_terms: int = 500) -> None:
        if (
            isinstance(max_terms, bool)
            or not isinstance(max_terms, int)
            or not 1 <= max_terms <= 500
        ):
            raise ValueError("max_terms must be between 1 and 500")
        self._max_terms = max_terms

    def build(
        self,
        *,
        title: str,
        keywords: Iterable[str],
        segments: Sequence[SourceSegment],
        abstract: str | None = None,
    ) -> tuple[GlossaryTerm, ...]:
        safe_title = _bounded_source(title, name="title", maximum=10_000)
        safe_abstract = (
            _bounded_source(abstract, name="abstract", maximum=200_000)
            if abstract is not None and abstract.strip()
            else ""
        )
        segment_texts = tuple(_segment_text(item) for item in segments)
        corpus = "\n".join((safe_title, safe_abstract, *segment_texts))
        title_identity = normalize_source_term(safe_title)
        candidates: dict[str, str] = {}

        for keyword in _keyword_values(keywords):
            _add_candidate(candidates, keyword)

        for match in _ABBREVIATION_DEFINITION.finditer(corpus):
            _add_candidate(candidates, match.group("long"))
            _add_candidate(candidates, match.group("short"))

        for match in _HYPHENATED.finditer(corpus):
            term = match.group(0)
            if (
                _term_occurrences(corpus, term) >= 2
                or normalize_source_term(term) in title_identity
            ):
                _add_candidate(candidates, term)

        for match in _CAPITALIZED_PHRASE.finditer(corpus):
            term = match.group(0)
            if (
                _term_occurrences(corpus, term) >= 2
                or normalize_source_term(term) in title_identity
            ):
                _add_candidate(candidates, term)

        ordered = tuple(
            GlossaryTerm(source_term=candidates[normalized])
            for normalized in sorted(candidates)[: self._max_terms]
        )
        return ordered


def _term_text(value: str | None, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if _CONTROL_CHARACTER.search(value):
        raise ValueError(f"{name} cannot contain control characters")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} must be non-blank and at most {maximum} characters")
    return " ".join(normalized.split())


def _bounded_source(value: str, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds {maximum:,} characters")
    return unicodedata.normalize("NFKC", value).strip()


def _keyword_values(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("keywords must be a collection")
    try:
        items = tuple(values)
    except TypeError as error:
        raise ValueError("keywords must be a collection") from error
    if len(items) > 5_000:
        raise ValueError("keywords cannot contain more than 5,000 values")
    return tuple(_term_text(item, name="keyword", maximum=500) for item in items)


def _segment_text(segment: SourceSegment) -> str:
    if not isinstance(segment, SourceSegment):
        raise ValueError("segments must contain SourceSegment values")
    return segment.source_text


def _add_candidate(candidates: dict[str, str], raw: str) -> None:
    try:
        term = _term_text(raw, name="term", maximum=500)
    except ValueError:
        return
    words = _WORD.findall(term)
    if not words or len(words) > 12:
        return
    normalized = normalize_source_term(term)
    if all(word.casefold() in _STOPWORDS for word in words):
        return
    candidates.setdefault(normalized, term)


def _term_occurrences(corpus: str, term: str) -> int:
    normalized_corpus = unicodedata.normalize("NFKC", corpus).casefold()
    normalized_term = normalize_source_term(term)
    if not normalized_term:
        return 0
    pattern = re.compile(rf"(?<!\w){re.escape(normalized_term)}(?!\w)")
    return sum(1 for _ in pattern.finditer(normalized_corpus))


__all__ = [
    "GlossaryBuilder",
    "GlossaryOrigin",
    "GlossaryTerm",
    "GlossaryTermUpdate",
]
