"""Official Crossref REST API work enricher."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from paper_reader.discovery.identity import normalize_doi
from paper_reader.discovery.sources.http import RequestPacer
from paper_reader.recommendation.enrichers.http import (
    rate_limit_observation,
    request_json_object,
)
from paper_reader.recommendation.enrichment import (
    EnricherCapabilities,
    EnricherRequestError,
    EnrichmentResult,
    EnrichmentStatus,
    WorkLookup,
    empty_result,
    observed_result,
    utc_now,
)


class CrossrefEnricher:
    endpoint = "https://api.crossref.org/works"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        contact_email: str | None = None,
        plus_api_token: str | None = None,
        min_interval_seconds: float | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._client = client
        self._contact_email = self._clean(contact_email)
        self._plus_api_token = self._clean(plus_api_token)
        requests_per_second = 10 if self._contact_email is not None else 5
        interval = 1 / requests_per_second if min_interval_seconds is None else min_interval_seconds
        self._pacer = RequestPacer(interval)
        self._clock = clock
        self.capabilities = EnricherCapabilities(
            enricher_id="crossref",
            display_name="Crossref",
            version="rest-v1",
            enabled=True,
            disabled_reason=None,
            credential_requirement="optional_plus_token",
            credential_configured=self._plus_api_token is not None,
            setup_url=(
                "https://www.crossref.org/documentation/retrieve-metadata/rest-api/"
                "access-and-authentication/"
            ),
            default_requests_per_second=float(requests_per_second),
            max_concurrency=3 if self._contact_email is not None else 1,
        )

    async def enrich(self, work: WorkLookup) -> EnrichmentResult:
        if work.doi is None:
            return empty_result(
                work,
                self.capabilities,
                status=EnrichmentStatus.NOT_APPLICABLE,
                warning="missing_doi",
                clock=self._clock,
            )
        params: dict[str, str] = {}
        if self._contact_email is not None:
            params["mailto"] = self._contact_email
        user_agent = "paper-reader/0.3"
        if self._contact_email is not None:
            user_agent = f"{user_agent} (mailto:{self._contact_email})"
        headers = {"User-Agent": user_agent}
        if self._plus_api_token is not None:
            headers["Crossref-Plus-API-Token"] = f"Bearer {self._plus_api_token}"
        await self._pacer.wait()
        payload, response = await request_json_object(
            self._client,
            f"{self.endpoint}/{quote(work.doi, safe='')}",
            params=params,
            headers=headers,
        )
        if payload is None:
            return empty_result(
                work,
                self.capabilities,
                status=EnrichmentStatus.NOT_FOUND,
                warning="work_not_found",
                clock=self._clock,
            )
        message = payload.get("message")
        if not isinstance(message, dict):
            raise EnricherRequestError(
                "response_incompatible",
                "Crossref response is missing the work message",
                retryable=False,
            )
        external_id = normalize_doi(
            message.get("DOI") if isinstance(message.get("DOI"), str) else None
        )
        if external_id is None:
            raise EnricherRequestError(
                "response_incompatible",
                "Crossref work is missing its DOI",
                retryable=False,
            )
        fields, provenance = self._fields(message)
        return observed_result(
            work,
            self.capabilities,
            external_id=external_id,
            matched_by="doi",
            fields=fields,
            field_provenance=provenance,
            payload=payload,
            rate_limit=rate_limit_observation(response),
            clock=self._clock,
        )

    @staticmethod
    def _fields(message: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        fields: dict[str, Any] = {}
        provenance: dict[str, str] = {}

        def add(key: str, value: Any, path: str) -> None:
            if value is not None:
                fields[key] = value
                provenance[key] = f"crossref:{path}"

        add("work_type", message.get("type"), "type")
        titles = message.get("title")
        if isinstance(titles, list) and titles:
            add("title", titles[0], "title[0]")
        containers = message.get("container-title")
        if isinstance(containers, list) and containers:
            add("venue", containers[0], "container-title[0]")
        add("publisher", message.get("publisher"), "publisher")
        add("issn", message.get("ISSN"), "ISSN")
        add("reference_count", message.get("reference-count"), "reference-count")
        add(
            "citation_count",
            message.get("is-referenced-by-count"),
            "is-referenced-by-count",
        )
        add("licenses", message.get("license"), "license")
        add("funders", message.get("funder"), "funder")
        add("relations", message.get("relation"), "relation")
        add("updates", message.get("update-to"), "update-to")
        add("published", message.get("published"), "published")
        add("source_updated_at", message.get("indexed"), "indexed")
        return fields, provenance

    @staticmethod
    def _clean(value: str | None) -> str | None:
        cleaned = value.strip() if value is not None else ""
        return cleaned or None
