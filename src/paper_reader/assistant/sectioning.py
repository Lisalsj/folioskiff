"""Deterministic page-range section detection over extracted PDF blocks."""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from typing import Final

_NUMBERED_HEADING: Final = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*)|(?:[IVXLCDM]+))(?:[.)]|\s|$)",
    re.IGNORECASE,
)
_COMMON_HEADING: Final = re.compile(
    r"^(?:abstract|introduction|background|related\s+work|methods?|methodology|"
    r"approach|model|experiments?|evaluation|results?|discussion|conclusions?|"
    r"limitations?|references|acknowledg(?:e)?ments?|appendix|摘要|引言|背景|"
    r"相关工作|方法|模型|实验|评估|结果|讨论|结论|局限|参考文献|致谢|附录)\b",
    re.IGNORECASE,
)
_TERMINAL_SENTENCE_PUNCTUATION: Final = re.compile(r"[.!?。！？;；:,，：]$")
_MAX_HEADING_CHARACTERS = 160
_MIN_CONFIDENCE = 0.65


@dataclass(frozen=True, slots=True)
class SectionBlock:
    block_number: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.block_number, bool)
            or not isinstance(self.block_number, int)
            or self.block_number < 0
        ):
            raise ValueError("block_number must be a non-negative integer")
        if not isinstance(self.text, str):
            raise ValueError("block text must be a string")
        coordinates = (self.x0, self.y0, self.x1, self.y1)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in coordinates
        ):
            raise ValueError("block coordinates must be finite numbers")
        if self.x0 < 0 or self.y0 < 0 or self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("block coordinates must describe a positive rectangle")

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True, slots=True)
class SectionPage:
    page_number: int
    text: str
    blocks: tuple[SectionBlock, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.page_number, bool)
            or not isinstance(self.page_number, int)
            or not 1 <= self.page_number <= 2_000
        ):
            raise ValueError("page_number must be between 1 and 2,000")
        if not isinstance(self.text, str):
            raise ValueError("page text must be a string")
        if len(self.blocks) > 20_000:
            raise ValueError("section page contains too many blocks")
        numbers = tuple(block.block_number for block in self.blocks)
        if len(numbers) != len(set(numbers)):
            raise ValueError("section block numbers must be unique within a page")


@dataclass(frozen=True, slots=True)
class SectionRange:
    start_page: int
    end_page: int
    heading: str | None
    confidence: float
    fallback: bool

    def __post_init__(self) -> None:
        if self.start_page < 1 or self.end_page < self.start_page:
            raise ValueError("section page range is invalid")
        if not 0 <= self.confidence <= 1:
            raise ValueError("section confidence must be between zero and one")
        if self.fallback and (
            self.start_page != self.end_page
            or self.heading is not None
            or self.confidence != 0
        ):
            raise ValueError("fallback sections must represent only the current page")


@dataclass(frozen=True, slots=True)
class _HeadingCandidate:
    page_number: int
    block_number: int
    text: str
    confidence: float


def detect_section(
    pages: tuple[SectionPage, ...],
    current_page: int,
) -> SectionRange:
    """Return a deterministic page range or a labeled one-page fallback."""

    if not pages:
        raise ValueError("section detection requires extracted pages")
    page_numbers = tuple(page.page_number for page in pages)
    if page_numbers != tuple(sorted(set(page_numbers))):
        raise ValueError("section pages must be unique and sorted")
    if current_page not in set(page_numbers):
        raise ValueError("current page is not available for section detection")

    candidates = tuple(
        candidate
        for page in pages
        for candidate in _page_headings(page)
    )
    preceding = tuple(item for item in candidates if item.page_number <= current_page)
    if not preceding:
        return _fallback(current_page)
    selected = preceding[-1]
    if selected.confidence < _MIN_CONFIDENCE:
        return _fallback(current_page)

    next_page = next(
        (
            item.page_number
            for item in candidates
            if item.page_number > selected.page_number
        ),
        None,
    )
    end_page = max(page_numbers) if next_page is None else next_page - 1
    if not selected.page_number <= current_page <= end_page:
        return _fallback(current_page)
    return SectionRange(
        start_page=selected.page_number,
        end_page=end_page,
        heading=selected.text,
        confidence=selected.confidence,
        fallback=False,
    )


def _page_headings(page: SectionPage) -> tuple[_HeadingCandidate, ...]:
    nonempty = tuple(block for block in page.blocks if block.text.strip())
    if not nonempty:
        return ()
    median_height = statistics.median(block.height for block in nonempty)
    candidates: list[_HeadingCandidate] = []
    for block in sorted(nonempty, key=lambda item: (item.block_number, item.y0, item.x0)):
        text = " ".join(block.text.split())
        if (
            not text
            or len(text) > _MAX_HEADING_CHARACTERS
            or _TERMINAL_SENTENCE_PUNCTUATION.search(text)
        ):
            continue
        numbered = _NUMBERED_HEADING.search(text) is not None
        common = _COMMON_HEADING.search(text) is not None
        height_ratio = block.height / median_height if median_height > 0 else 0
        visual = height_ratio >= 1.25
        if not numbered and not common and not visual:
            continue
        confidence = 0.85 if numbered else 0.82 if common else 0.65
        if visual:
            confidence = max(confidence, min(0.92, 0.65 + (height_ratio - 1.25) * 0.2))
        if (numbered or common) and visual:
            confidence = max(confidence, 0.95)
        candidates.append(
            _HeadingCandidate(
                page_number=page.page_number,
                block_number=block.block_number,
                text=text,
                confidence=round(confidence, 4),
            )
        )
    return tuple(candidates)


def _fallback(current_page: int) -> SectionRange:
    return SectionRange(
        start_page=current_page,
        end_page=current_page,
        heading=None,
        confidence=0,
        fallback=True,
    )


__all__ = [
    "SectionBlock",
    "SectionPage",
    "SectionRange",
    "detect_section",
]
