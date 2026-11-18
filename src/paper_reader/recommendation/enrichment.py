"""Normalized metadata-enrichment contract with auditable field provenance."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

from paper_reader.discovery.identity import normalize_doi


class EnrichmentStatus(StrEnum):
    OBSERVED = "observed"
    NOT_FOUND = "not_found"
    NOT_APPLICABLE = "not_applicable"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class EnricherCapabilities:
    enricher_id: str
    display_name: str
    version: str
    enabled: bool
    disabled_reason: str | None
    credential_requirement: str
    credential_configured: bool
    setup_url: str
    default_requests_per_second: float | None
    max_concurrency: int


@dataclass(frozen=True, slots=True)
class RateLimitObservation:
    limit: int | None = None
    remaining: int | None = None
    reset_after_seconds: float | None = None
    interval_seconds: float | None = None

    def __post_init__(self) -> None:
        if any(
            value is not None and value < 0
            for value in (
                self.limit,
                self.remaining,
                self.reset_after_seconds,
                self.interval_seconds,
            )
        ):
            raise ValueError("rate-limit values cannot be negative")


@dataclass(frozen=True, slots=True)
class WorkLookup:
    work_id: str
    doi: str | None
    title: str
    authors: tuple[str, ...]
    source_ids: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "doi", normalize_doi(self.doi))
        object.__setattr__(self, "title", " ".join(self.title.split()))
        object.__setattr__(self, "authors", tuple(self.authors))
        object.__setattr__(self, "source_ids", MappingProxyType(dict(self.source_ids)))
        if not self.work_id.strip() or not self.title:
            raise ValueError("work_id and title are required")


@dataclass(frozen=True, slots=True)
class EnrichmentResult:
    work_id: str
    enricher_id: str
    enricher_version: str
    status: EnrichmentStatus
    external_id: str | None
    matched_by: str | None
    fields: Mapping[str, Any]
    field_provenance: Mapping[str, str]
    warnings: tuple[str, ...]
    fetched_at: datetime
    response_sha256: str | None
    rate_limit: RateLimitObservation | None = None

    def __post_init__(self) -> None:
        status = EnrichmentStatus(self.status)
        object.__setattr__(self, "status", status)
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        object.__setattr__(self, "fetched_at", self.fetched_at.astimezone(UTC))
        if status is EnrichmentStatus.OBSERVED:
            if not self.external_id or not self.matched_by or self.response_sha256 is None:
                raise ValueError("observed enrichment requires identity and response hash")
        elif self.response_sha256 is not None:
            raise ValueError("non-observed enrichment cannot contain a response hash")
        if self.response_sha256 is not None and re.fullmatch(
            r"[0-9a-f]{64}", self.response_sha256
        ) is None:
            raise ValueError("response_sha256 must be lowercase SHA-256")
        if set(self.fields) != set(self.field_provenance):
            raise ValueError("every normalized field requires exactly one provenance path")
        object.__setattr__(
            self,
            "fields",
            MappingProxyType(copy.deepcopy(dict(self.fields))),
        )
        object.__setattr__(
            self,
            "field_provenance",
            MappingProxyType(dict(self.field_provenance)),
        )


class EnricherRequestError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class MetadataEnricher(Protocol):
    capabilities: EnricherCapabilities

    async def enrich(self, work: WorkLookup) -> EnrichmentResult: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_response_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def empty_result(
    work: WorkLookup,
    capabilities: EnricherCapabilities,
    *,
    status: EnrichmentStatus,
    warning: str,
    clock: Callable[[], datetime],
) -> EnrichmentResult:
    return EnrichmentResult(
        work_id=work.work_id,
        enricher_id=capabilities.enricher_id,
        enricher_version=capabilities.version,
        status=status,
        external_id=None,
        matched_by=None,
        fields={},
        field_provenance={},
        warnings=(warning,),
        fetched_at=clock(),
        response_sha256=None,
    )


def observed_result(
    work: WorkLookup,
    capabilities: EnricherCapabilities,
    *,
    external_id: str,
    matched_by: str,
    fields: Mapping[str, Any],
    field_provenance: Mapping[str, str],
    payload: Mapping[str, Any],
    rate_limit: RateLimitObservation | None,
    clock: Callable[[], datetime],
    warnings: tuple[str, ...] = (),
) -> EnrichmentResult:
    return EnrichmentResult(
        work_id=work.work_id,
        enricher_id=capabilities.enricher_id,
        enricher_version=capabilities.version,
        status=EnrichmentStatus.OBSERVED,
        external_id=external_id,
        matched_by=matched_by,
        fields=fields,
        field_provenance=field_provenance,
        warnings=warnings,
        fetched_at=clock(),
        response_sha256=canonical_response_sha256(payload),
        rate_limit=rate_limit,
    )
