"""Scanned-page snapshot selection and OCR evidence normalization."""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Sequence
from hashlib import sha256
from typing import cast

from paper_reader.reader.extraction import TextBlock
from paper_reader.reader.models import DocumentPageTextRecord
from paper_reader.reader.ocr_contracts import (
    OCR_MAX_CHARACTERS_PER_PAGE,
    OcrLine,
    OcrPageResult,
    _invalid_output_error,
    _ocr_text_limit_error,
    _PageSnapshot,
)


def _stable_coordinate(value: float) -> float:
    return round(value, 3)


def _normalized_page_result(
    page: _PageSnapshot,
    *,
    rendered_width: int,
    rendered_height: int,
    lines: Sequence[OcrLine],
    max_characters: int = OCR_MAX_CHARACTERS_PER_PAGE,
) -> OcrPageResult:
    if rendered_width <= 0 or rendered_height <= 0:
        raise _invalid_output_error()
    blocks: list[TextBlock] = []
    weighted_confidence = 0.0
    character_count = 0
    for block_number, line in enumerate(lines):
        if len(line.text) > max_characters:
            raise _ocr_text_limit_error()
        text = unicodedata.normalize("NFC", line.text).strip()
        if not text:
            continue
        confidence = float(line.confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise _invalid_output_error()
        try:
            xs = tuple(float(point[0]) for point in line.polygon)
            ys = tuple(float(point[1]) for point in line.polygon)
        except (IndexError, TypeError, ValueError) as error:
            raise _invalid_output_error() from error
        if len(xs) != 4 or len(ys) != 4 or not all(math.isfinite(value) for value in (*xs, *ys)):
            raise _invalid_output_error()
        x0 = _stable_coordinate(
            max(0.0, min(page.page_width, min(xs) * page.page_width / rendered_width))
        )
        y0 = _stable_coordinate(
            max(
                0.0,
                min(page.page_height, min(ys) * page.page_height / rendered_height),
            )
        )
        x1 = _stable_coordinate(
            max(0.0, min(page.page_width, max(xs) * page.page_width / rendered_width))
        )
        y1 = _stable_coordinate(
            max(
                0.0,
                min(page.page_height, max(ys) * page.page_height / rendered_height),
            )
        )
        if x0 >= x1 or y0 >= y1:
            raise _invalid_output_error()
        blocks.append(
            TextBlock(
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                text=text,
                block_number=block_number,
                confidence=confidence,
            )
        )
        line_characters = sum(not character.isspace() for character in text)
        if character_count + line_characters > max_characters:
            raise _ocr_text_limit_error()
        weighted_confidence += confidence * line_characters
        character_count += line_characters
    text = "".join(f"{block.text}\n" for block in blocks)
    confidence = 0.0 if character_count == 0 else round(weighted_confidence / character_count, 6)
    return OcrPageResult(
        page_number=page.page_number,
        text=text,
        blocks=tuple(blocks),
        confidence=confidence,
        line_count=len(blocks),
    )


def _aggregate_page_confidence(pages: Sequence[OcrPageResult]) -> float:
    weighted = 0.0
    character_count = 0
    for page in pages:
        page_characters = sum(not character.isspace() for character in page.text)
        weighted += page.confidence * page_characters
        character_count += page_characters
    return 0.0 if character_count == 0 else round(weighted / character_count, 6)


def _page_snapshot(row: DocumentPageTextRecord) -> _PageSnapshot:
    evidence = json.dumps(
        {
            "text": row.native_text,
            "blocks": row.native_blocks,
            "characters": row.native_character_count,
            "extractor": row.extractor_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _PageSnapshot(
        page_number=row.page_number,
        page_width=row.page_width,
        page_height=row.page_height,
        native_evidence_hash=sha256(evidence).hexdigest(),
    )


def select_scanned_page_rows(
    rows: Sequence[DocumentPageTextRecord],
) -> tuple[DocumentPageTextRecord, ...]:
    """Select scanned pages deterministically without coupling to a query layer."""

    return tuple(
        sorted(
            (row for row in rows if row.likely_scanned),
            key=lambda row: row.page_number,
        )
    )


def scanned_page_snapshots(
    rows: Sequence[DocumentPageTextRecord],
) -> tuple[_PageSnapshot, ...]:
    return tuple(_page_snapshot(row) for row in select_scanned_page_rows(rows))


def _ocr_block_payload(block: TextBlock) -> dict[str, object]:
    return {
        "x0": block.x0,
        "y0": block.y0,
        "x1": block.x1,
        "y1": block.y1,
        "text": block.text,
        "block_number": block.block_number,
        "confidence": cast(float, block.confidence),
    }
