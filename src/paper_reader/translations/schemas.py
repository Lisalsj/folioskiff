"""Closed structured-output schema for one bounded translation batch."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SEGMENT_ID = re.compile(r"^segment-[0-9]{6}$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class _StrictOutput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class TranslatedSegmentOutput(_StrictOutput):
    segment_id: str
    translated_text: str = Field(min_length=1, max_length=50_000)
    uncertain_terms: tuple[str, ...] = Field(max_length=50)

    @field_validator("segment_id")
    @classmethod
    def _valid_segment_id(cls, value: str) -> str:
        if _SEGMENT_ID.fullmatch(value) is None:
            raise ValueError("segment_id must use the canonical segment-NNNNNN form")
        return value

    @field_validator("translated_text")
    @classmethod
    def _nonblank_translation(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("translated_text cannot be blank")
        if _CONTROL_CHARACTER.search(value):
            raise ValueError("translated_text contains unsupported control characters")
        return value

    @field_validator("uncertain_terms")
    @classmethod
    def _bounded_uncertain_terms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for term in value:
            item = term.strip()
            if not item or len(item) > 500 or _CONTROL_CHARACTER.search(item):
                raise ValueError("uncertain terms must be non-blank and at most 500 characters")
            normalized.append(item)
        if len(normalized) != len(set(normalized)):
            raise ValueError("uncertain terms must be unique")
        return tuple(normalized)


class TranslationBatchOutput(_StrictOutput):
    segments: tuple[TranslatedSegmentOutput, ...] = Field(min_length=1, max_length=40)


def translation_batch_json_schema() -> dict[str, Any]:
    """Return the validation schema supplied verbatim to Agent backends."""

    return TranslationBatchOutput.model_json_schema(mode="validation")


__all__ = [
    "TranslatedSegmentOutput",
    "TranslationBatchOutput",
    "translation_batch_json_schema",
]
