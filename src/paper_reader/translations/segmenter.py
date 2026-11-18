"""Deterministic translation segmentation with exact scholarly-span protection."""

from __future__ import annotations

import hmac
import re
from bisect import bisect_left
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal, Protocol

from paper_reader.assistant.contracts import ContextScope

MAX_SOURCE_CHARACTERS = 20_000_000
MAX_SEGMENTS = 100_000
MAX_PROTECTED_SPANS = 100_000
_MIN_SEGMENT_CHARACTERS = 64
_MAX_SEGMENT_CHARACTERS = 50_000

_TOKEN = re.compile(r"⟦PR:(?P<index>[0-9]{6}):(?P<digest>[0-9a-f]{8})⟧")
_RESERVED_TOKEN = re.compile(r"⟦\s*PR\s*:[^⟧\r\n]{0,80}⟧", re.IGNORECASE)
_RESERVED_PREFIX = re.compile(r"⟦\s*PR\s*:", re.IGNORECASE)
_HASH = re.compile(r"^[0-9a-f]{64}$")
_PARAGRAPH_BREAK = re.compile(r"(?:\r?\n[ \t]*){2,}")
_SENTENCE_BOUNDARY = re.compile(r"(?:[.!?。！？][\"'”’）)\]]*|\r?\n)[ \t\r\n]+")
_WHITESPACE = re.compile(r"\s+")


class ProtectedSpanError(ValueError):
    """A frozen source or returned placeholder sequence is not trustworthy."""


class SegmentationLimitError(ProtectedSpanError):
    """A source exceeds a deterministic local translation safety bound."""


@dataclass(frozen=True, slots=True)
class ProtectedSpan:
    token: str
    literal: str
    literal_sha256: str
    kind: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if _TOKEN.fullmatch(self.token) is None:
            raise ValueError("protected token is invalid")
        expected_hash = sha256(self.literal.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(expected_hash, self.literal_sha256):
            raise ValueError("protected literal hash is invalid")
        if not self.kind or self.start < 0 or self.end <= self.start:
            raise ValueError("protected span metadata is invalid")


@dataclass(frozen=True, slots=True)
class ProtectedText:
    text: str
    spans: tuple[ProtectedSpan, ...]

    def __post_init__(self) -> None:
        expected = tuple(span.token for span in self.spans)
        if len(expected) != len(set(expected)):
            raise ValueError("protected placeholder tokens must be unique")
        _require_exact_placeholder_sequence(self.text, expected)

    @property
    def protected_map(self) -> dict[str, dict[str, str]]:
        return {
            span.token: {
                "literal": span.literal,
                "sha256": span.literal_sha256,
                "kind": span.kind,
            }
            for span in self.spans
        }


@dataclass(frozen=True, slots=True)
class SourceSegment:
    segment_id: str
    segment_index: int
    page_numbers: tuple[int, ...]
    block_numbers: tuple[int, ...]
    source_text: str
    source_sha256: str
    protected: ProtectedText
    source_kind: Literal["native", "ocr"]
    ocr_confidence: float | None

    def __post_init__(self) -> None:
        if self.segment_id != f"segment-{self.segment_index:06d}":
            raise ValueError("segment ID must match its one-based index")
        if self.segment_index < 1 or self.segment_index > MAX_SEGMENTS:
            raise ValueError("segment index is outside the supported range")
        if len(self.page_numbers) != 1 or self.page_numbers[0] < 1:
            raise ValueError("translation segments must bind exactly one PDF page")
        if (
            not self.block_numbers
            or self.block_numbers != tuple(sorted(set(self.block_numbers)))
            or self.block_numbers[0] < 1
        ):
            raise ValueError("block numbers must be unique, sorted, and positive")
        expected_hash = sha256(self.source_text.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(expected_hash, self.source_sha256):
            raise ValueError("segment source hash is invalid")
        if restore_spans(self.protected.text, self.protected) != self.source_text:
            raise ValueError("protected segment does not round-trip to its source")
        if self.source_kind == "native" and self.ocr_confidence is not None:
            raise ValueError("native segments cannot carry OCR confidence")
        if self.source_kind == "ocr" and (
            self.ocr_confidence is None
            or isinstance(self.ocr_confidence, bool)
            or not 0 <= self.ocr_confidence <= 1
        ):
            raise ValueError("OCR segments require a confidence between zero and one")

    @property
    def protected_text(self) -> str:
        return self.protected.text

    @property
    def protected_spans(self) -> tuple[ProtectedSpan, ...]:
        return self.protected.spans

    @property
    def protected_map(self) -> dict[str, dict[str, str]]:
        return self.protected.protected_map


class ContextSnapshot(Protocol):
    @property
    def scope(self) -> ContextScope: ...

    @property
    def evidence(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class _PageSource:
    page_number: int
    text: str
    source_kind: Literal["native", "ocr"]
    ocr_confidence: float | None


@dataclass(frozen=True, slots=True)
class _Candidate:
    start: int
    end: int
    kind: str


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "fenced_code",
        re.compile(r"(?P<fence>```|~~~)[^\r\n]*\r?\n[\s\S]*?(?P=fence)"),
    ),
    (
        "latex_environment",
        re.compile(
            r"\\begin\{(?P<environment>equation\*?|align\*?|gather\*?|"
            r"multline\*?|math|displaymath)\}[\s\S]*?"
            r"\\end\{(?P=environment)\}"
        ),
    ),
    ("display_math", re.compile(r"\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]")),
    ("inline_code", re.compile(r"(?<!`)`[^`\r\n]+`(?!`)")),
    ("inline_math", re.compile(r"\\\([^\r\n]*?\\\)|(?<![\\$])\$(?!\$)(?:\\.|[^$\r\n])+?(?<!\\)\$")),
    ("url", re.compile(r"https?://[^\s<>{}\[\]\"'`]+", re.IGNORECASE)),
    (
        "doi",
        re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]*[A-Z0-9]", re.IGNORECASE),
    ),
    (
        "email",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}\b", re.IGNORECASE),
    ),
    (
        "numbered_reference",
        re.compile(
            r"\b(?:Eq(?:uation)?s?|Fig(?:ure)?s?|Tables?|Algorithms?|Algs?)\.?"
            r"\s*(?:\([A-Za-z0-9][A-Za-z0-9.,:\-– ]*\)|"
            r"[A-Za-z]?\d+(?:[A-Za-z]|\.\d+)?(?:\s*[-–]\s*\d+[A-Za-z]?)?)",
            re.IGNORECASE,
        ),
    ),
    (
        "numeric_citation",
        re.compile(r"\[(?:\d+[A-Za-z]?(?:\s*[-–,;]\s*\d+[A-Za-z]?){0,100})\]"),
    ),
    (
        "author_year_citation",
        re.compile(
            r"\((?=[^()\r\n]{1,200}(?:19|20)\d{2})"
            r"[A-Z][^()\r\n]{0,199}(?:19|20)\d{2}[a-z]?\)"
        ),
    ),
    (
        "variable",
        re.compile(r"(?<!\w)[A-Za-z](?:_[A-Za-z0-9{}]+|\^[A-Za-z0-9{}]+)+(?!\w)"),
    ),
)


def protect_spans(text: str) -> ProtectedText:
    """Replace scholarly literals with ordered, content-bound placeholders."""

    protected, _ = _protect_spans(text, start_index=1)
    return protected


def restore_spans(translated: str, protected: ProtectedText) -> str:
    """Restore exact literals only when every placeholder round-trips unchanged."""

    if not isinstance(translated, str):
        raise ProtectedSpanError("translated text must be a string")
    if not isinstance(protected, ProtectedText):
        raise ProtectedSpanError("protected text metadata is invalid")
    expected = tuple(span.token for span in protected.spans)
    _require_exact_placeholder_sequence(translated, expected)
    restored = translated
    for span in protected.spans:
        actual_hash = sha256(span.literal.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(actual_hash, span.literal_sha256):
            raise ProtectedSpanError("protected placeholder literal hash changed")
        restored = restored.replace(span.token, span.literal, 1)
    if _RESERVED_PREFIX.search(restored) is not None:
        raise ProtectedSpanError("reserved placeholder remained after restoration")
    return restored


def segment_context(
    snapshot: ContextSnapshot,
    max_characters: int = 12_000,
) -> tuple[SourceSegment, ...]:
    """Create stable one-page segments from one immutable Assistant snapshot."""

    if (
        isinstance(max_characters, bool)
        or not isinstance(max_characters, int)
        or max_characters < _MIN_SEGMENT_CHARACTERS
        or max_characters > _MAX_SEGMENT_CHARACTERS
    ):
        raise ValueError(
            f"max_characters must be between {_MIN_SEGMENT_CHARACTERS:,} "
            f"and {_MAX_SEGMENT_CHARACTERS:,}"
        )
    evidence = getattr(snapshot, "evidence", None)
    if not isinstance(evidence, Mapping):
        raise ProtectedSpanError("translation context evidence is invalid")
    try:
        scope = ContextScope(snapshot.scope)
    except (TypeError, ValueError) as error:
        raise ProtectedSpanError("translation context scope is invalid") from error
    if scope is ContextScope.ANNOTATIONS:
        raise ProtectedSpanError("annotation scope is not supported for translation")
    if evidence.get("evidence_level") == "abstract_only":
        raise ProtectedSpanError("translation requires extracted paper text")

    pages = _validated_pages(evidence.get("pages"))
    if scope is ContextScope.SELECTION:
        pages = (_selection_source(evidence, pages),)
    total_characters = sum(len(page.text) for page in pages)
    if total_characters > MAX_SOURCE_CHARACTERS:
        raise SegmentationLimitError(
            f"translation source exceeds {MAX_SOURCE_CHARACTERS:,} characters"
        )
    if not pages or total_characters == 0:
        raise ProtectedSpanError("translation context contains no source text")

    segments: list[SourceSegment] = []
    next_token = 1
    for page in pages:
        protected_page, next_token = _protect_spans(page.text, start_index=next_token)
        span_by_token = {span.token: span for span in protected_page.spans}
        blocks = tuple(
            block.strip() for block in _PARAGRAPH_BREAK.split(protected_page.text) if block.strip()
        )
        for block_number, block in enumerate(blocks, start=1):
            for bounded in _bounded_chunks(block, max_characters=max_characters):
                tokens = tuple(match.group(0) for match in _TOKEN.finditer(bounded))
                try:
                    block_spans = tuple(span_by_token[token] for token in tokens)
                except KeyError as error:  # pragma: no cover - protected-page invariant
                    raise ProtectedSpanError("segment placeholder metadata is missing") from error
                protected = ProtectedText(text=bounded, spans=block_spans)
                source_text = restore_spans(bounded, protected)
                segment_index = len(segments) + 1
                if segment_index > MAX_SEGMENTS:
                    raise SegmentationLimitError(
                        f"translation source exceeds {MAX_SEGMENTS:,} segments"
                    )
                segments.append(
                    SourceSegment(
                        segment_id=f"segment-{segment_index:06d}",
                        segment_index=segment_index,
                        page_numbers=(page.page_number,),
                        block_numbers=(block_number,),
                        source_text=source_text,
                        source_sha256=sha256(source_text.encode("utf-8")).hexdigest(),
                        protected=protected,
                        source_kind=page.source_kind,
                        ocr_confidence=page.ocr_confidence,
                    )
                )
    if not segments:
        raise ProtectedSpanError("translation context contains no translatable segments")
    return tuple(segments)


def _protect_spans(text: str, *, start_index: int) -> tuple[ProtectedText, int]:
    if not isinstance(text, str):
        raise ProtectedSpanError("source text must be a string")
    if len(text) > MAX_SOURCE_CHARACTERS:
        raise SegmentationLimitError(
            f"translation source exceeds {MAX_SOURCE_CHARACTERS:,} characters"
        )
    if _RESERVED_PREFIX.search(text) is not None:
        raise ProtectedSpanError("source text contains a reserved placeholder token")
    selected: list[_Candidate] = []
    starts: list[int] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            start, end = _trim_candidate(kind, match.start(), match.end(), text)
            if start >= end or _overlaps(selected, starts, start, end):
                continue
            position = bisect_left(starts, start)
            starts.insert(position, start)
            selected.insert(position, _Candidate(start=start, end=end, kind=kind))
            if len(selected) > MAX_PROTECTED_SPANS:
                raise SegmentationLimitError(
                    f"translation source exceeds {MAX_PROTECTED_SPANS:,} protected spans"
                )

    parts: list[str] = []
    spans: list[ProtectedSpan] = []
    cursor = 0
    for offset, candidate in enumerate(selected):
        literal = text[candidate.start : candidate.end]
        literal_hash = sha256(literal.encode("utf-8")).hexdigest()
        token_index = start_index + offset
        token = f"⟦PR:{token_index:06d}:{literal_hash[:8]}⟧"
        parts.append(text[cursor : candidate.start])
        parts.append(token)
        spans.append(
            ProtectedSpan(
                token=token,
                literal=literal,
                literal_sha256=literal_hash,
                kind=candidate.kind,
                start=candidate.start,
                end=candidate.end,
            )
        )
        cursor = candidate.end
    parts.append(text[cursor:])
    return ProtectedText(text="".join(parts), spans=tuple(spans)), start_index + len(spans)


def _trim_candidate(kind: str, start: int, end: int, text: str) -> tuple[int, int]:
    if kind not in {"url", "doi", "email"}:
        return start, end
    while end > start and text[end - 1] in ".,;:!?":
        end -= 1
    if kind == "url":
        while (
            end > start
            and text[end - 1] in ")]"
            and text[start:end].count(text[end - 1])
            > text[start:end].count("(" if text[end - 1] == ")" else "[")
        ):
            end -= 1
    return start, end


def _overlaps(
    selected: list[_Candidate],
    starts: list[int],
    start: int,
    end: int,
) -> bool:
    position = bisect_left(starts, start)
    if position > 0 and selected[position - 1].end > start:
        return True
    return position < len(selected) and selected[position].start < end


def _require_exact_placeholder_sequence(text: str, expected: tuple[str, ...]) -> None:
    exact = tuple(match.group(0) for match in _TOKEN.finditer(text))
    reserved = tuple(match.group(0) for match in _RESERVED_TOKEN.finditer(text))
    prefix_count = sum(1 for _ in _RESERVED_PREFIX.finditer(text))
    if exact != expected or reserved != exact or prefix_count != len(exact):
        raise ProtectedSpanError(
            "protected placeholder sequence is missing, duplicated, reordered, altered, or invented"
        )


def _validated_pages(raw_pages: Any) -> tuple[_PageSource, ...]:
    if not isinstance(raw_pages, (list, tuple)) or not raw_pages:
        raise ProtectedSpanError("translation context pages are missing")
    preliminary: list[tuple[Mapping[str, Any], int, str]] = []
    total_characters = 0
    for raw in raw_pages:
        if not isinstance(raw, Mapping):
            raise ProtectedSpanError("translation context page is invalid")
        page_number = raw.get("page_number")
        text = raw.get("text")
        if (
            isinstance(page_number, bool)
            or not isinstance(page_number, int)
            or page_number < 1
            or not isinstance(text, str)
        ):
            raise ProtectedSpanError("translation context page identity is invalid")
        total_characters += len(text)
        if total_characters > MAX_SOURCE_CHARACTERS:
            raise SegmentationLimitError(
                f"translation source exceeds {MAX_SOURCE_CHARACTERS:,} characters"
            )
        preliminary.append((raw, page_number, text))

    page_numbers = tuple(item[1] for item in preliminary)
    if page_numbers != tuple(sorted(set(page_numbers))):
        raise ProtectedSpanError("translation context pages must be unique and ordered")

    pages: list[_PageSource] = []
    for raw, page_number, text in preliminary:
        if raw.get("truncated") is True:
            raise ProtectedSpanError("translation context page is truncated")
        expected_hash = raw.get("source_text_sha256")
        actual_hash = sha256(text.encode("utf-8")).hexdigest()
        if (
            not isinstance(expected_hash, str)
            or _HASH.fullmatch(expected_hash) is None
            or not hmac.compare_digest(expected_hash, actual_hash)
        ):
            raise ProtectedSpanError("translation context page hash does not match")
        source = raw.get("source")
        confidence = raw.get("confidence")
        if source == "native":
            if confidence is not None:
                raise ProtectedSpanError("native translation page cannot carry OCR confidence")
            source_kind: Literal["native", "ocr"] = "native"
            ocr_confidence = None
        elif source == "ocr":
            if (
                confidence is None
                or isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not 0 <= confidence <= 1
            ):
                raise ProtectedSpanError(
                    "OCR translation page requires a confidence between zero and one"
                )
            source_kind = "ocr"
            ocr_confidence = float(confidence)
        else:
            raise ProtectedSpanError("translation context page source is invalid")
        pages.append(
            _PageSource(
                page_number=page_number,
                text=text,
                source_kind=source_kind,
                ocr_confidence=ocr_confidence,
            )
        )
    return tuple(pages)


def _selection_source(
    evidence: Mapping[str, Any],
    pages: tuple[_PageSource, ...],
) -> _PageSource:
    raw = evidence.get("selection_excerpt")
    if not isinstance(raw, Mapping):
        raise ProtectedSpanError("selection scope requires frozen selection evidence")
    if raw.get("verified") is not True:
        raise ProtectedSpanError("selection evidence must be verified against its PDF page")
    page_number = raw.get("page_number")
    quote = raw.get("quote_text")
    if (
        isinstance(page_number, bool)
        or not isinstance(page_number, int)
        or page_number < 1
        or not isinstance(quote, str)
        or not quote.strip()
        or len(quote) > 50_000
    ):
        raise ProtectedSpanError("selection evidence is invalid")
    page = next((item for item in pages if item.page_number == page_number), None)
    if page is None or quote not in page.text:
        raise ProtectedSpanError("selection evidence does not match its frozen PDF page")
    return _PageSource(
        page_number=page_number,
        text=quote.strip(),
        source_kind=page.source_kind,
        ocr_confidence=page.ocr_confidence,
    )


def _bounded_chunks(text: str, *, max_characters: int) -> tuple[str, ...]:
    if len(text) <= max_characters:
        return (text,)
    chunks: list[str] = []
    start = 0
    while start < len(text):
        remaining = text[start:]
        if len(remaining) <= max_characters:
            chunk = remaining.strip()
            if chunk:
                chunks.append(chunk)
            break
        limit = start + max_characters
        sentence_cuts = [
            match.end()
            for match in _SENTENCE_BOUNDARY.finditer(text, start, limit + 1)
            if match.end() > start
        ]
        whitespace_cuts = [
            match.start()
            for match in _WHITESPACE.finditer(text, start, limit + 1)
            if match.start() > start
        ]
        cut = (
            sentence_cuts[-1]
            if sentence_cuts
            else (whitespace_cuts[-1] if whitespace_cuts else limit)
        )
        cut = _move_cut_outside_token(text, start=start, cut=cut, limit=limit)
        if cut <= start:
            raise ProtectedSpanError("a protected placeholder exceeds the segment bound")
        chunk = text[start:cut].strip()
        if chunk:
            chunks.append(chunk)
        start = cut
        while start < len(text) and text[start].isspace():
            start += 1
    if any(len(chunk) > max_characters for chunk in chunks):
        raise ProtectedSpanError("bounded segmentation exceeded max_characters")
    return tuple(chunks)


def _move_cut_outside_token(text: str, *, start: int, cut: int, limit: int) -> int:
    for match in _TOKEN.finditer(text, start):
        if match.start() >= cut:
            break
        if match.start() < cut < match.end():
            if match.start() > start:
                return match.start()
            if match.end() - start <= limit - start:
                return match.end()
            return start
    return cut


__all__ = [
    "MAX_PROTECTED_SPANS",
    "MAX_SEGMENTS",
    "MAX_SOURCE_CHARACTERS",
    "ProtectedSpan",
    "ProtectedSpanError",
    "ProtectedText",
    "SegmentationLimitError",
    "SourceSegment",
    "protect_spans",
    "restore_spans",
    "segment_context",
]
