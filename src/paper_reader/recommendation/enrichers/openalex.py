"""Official OpenAlex Work metadata enricher."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

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


class OpenAlexEnricher:
    endpoint = "https://api.openalex.org/works"
    selected_fields = (
        "id",
        "doi",
        "type",
        "cited_by_count",
        "is_retracted",
        "primary_location",
        "authorships",
        "fwci",
        "citation_normalized_percentile",
        "cited_by_percentile_year",
        "counts_by_year",
        "topics",
        "primary_topic",
        "keywords",
        "open_access",
        "updated_date",
    )

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str | None,
        min_interval_seconds: float = 0.1,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._client = client
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._pacer = RequestPacer(min_interval_seconds)
        self._clock = clock
        self.capabilities = EnricherCapabilities(
            enricher_id="openalex",
            display_name="OpenAlex",
            version="work-v1",
            enabled=self._api_key is not None,
            disabled_reason="missing_api_key" if self._api_key is None else None,
            credential_requirement="api_key",
            credential_configured=self._api_key is not None,
            setup_url="https://openalex.org/settings/api",
            default_requests_per_second=None,
            max_concurrency=1,
        )

    async def enrich(self, work: WorkLookup) -> EnrichmentResult:
        if self._api_key is None:
            return empty_result(
                work,
                self.capabilities,
                status=EnrichmentStatus.DISABLED,
                warning="missing_api_key",
                clock=self._clock,
            )
        if work.doi is None:
            return empty_result(
                work,
                self.capabilities,
                status=EnrichmentStatus.NOT_APPLICABLE,
                warning="missing_doi",
                clock=self._clock,
            )

        await self._pacer.wait()
        payload, response = await request_json_object(
            self._client,
            f"{self.endpoint}/{quote(f'doi:{work.doi}', safe=':')}",
            params={
                "api_key": self._api_key,
                "select": ",".join(self.selected_fields),
            },
            headers={"User-Agent": "paper-reader/0.3"},
            rate_limit_statuses=frozenset({403, 429}),
        )
        if payload is None:
            return empty_result(
                work,
                self.capabilities,
                status=EnrichmentStatus.NOT_FOUND,
                warning="work_not_found",
                clock=self._clock,
            )
        external_id = self._external_id(payload.get("id"))
        if external_id is None:
            raise EnricherRequestError(
                "response_incompatible",
                "OpenAlex Work is missing its ID",
                retryable=False,
            )
        fields, provenance = self._fields(payload)
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

    @classmethod
    def _fields(cls, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        fields: dict[str, Any] = {}
        provenance: dict[str, str] = {}

        def add(key: str, value: Any, path: str) -> None:
            if value is not None:
                fields[key] = value
                provenance[key] = f"openalex:{path}"

        add("citation_count", payload.get("cited_by_count"), "cited_by_count")
        add("fwci", payload.get("fwci"), "fwci")
        add(
            "citation_normalized_percentile",
            payload.get("citation_normalized_percentile"),
            "citation_normalized_percentile",
        )
        add(
            "citation_percentile_year",
            payload.get("cited_by_percentile_year"),
            "cited_by_percentile_year",
        )
        add("citation_counts_by_year", payload.get("counts_by_year"), "counts_by_year")
        add("work_type", payload.get("type"), "type")
        add("is_retracted", payload.get("is_retracted"), "is_retracted")
        add("topics", payload.get("topics"), "topics")
        add("primary_topic", payload.get("primary_topic"), "primary_topic")
        add("keywords", payload.get("keywords"), "keywords")
        add("open_access", payload.get("open_access"), "open_access")
        add("source_updated_at", payload.get("updated_date"), "updated_date")

        location = payload.get("primary_location")
        if isinstance(location, dict):
            source = location.get("source")
            source_data = source if isinstance(source, dict) else {}
            venue = {
                "id": cls._external_id(source_data.get("id")),
                "name": source_data.get("display_name"),
                "issn_l": source_data.get("issn_l"),
                "issn": source_data.get("issn"),
                "is_open_access": location.get("is_oa"),
                "license": location.get("license"),
                "version": location.get("version"),
            }
            add("venue", venue, "primary_location")

        authorships = payload.get("authorships")
        if isinstance(authorships, list):
            authors: list[dict[str, Any]] = []
            for authorship in authorships:
                if not isinstance(authorship, dict):
                    continue
                author = authorship.get("author")
                author_data = author if isinstance(author, dict) else {}
                institutions: list[dict[str, Any]] = []
                raw_institutions = authorship.get("institutions")
                if isinstance(raw_institutions, list):
                    for institution in raw_institutions:
                        if isinstance(institution, dict):
                            institutions.append(
                                {
                                    "id": cls._external_id(institution.get("id")),
                                    "name": institution.get("display_name"),
                                    "country_code": institution.get("country_code"),
                                    "type": institution.get("type"),
                                }
                            )
                authors.append(
                    {
                        "id": cls._external_id(author_data.get("id")),
                        "name": author_data.get("display_name"),
                        "position": authorship.get("author_position"),
                        "institutions": institutions,
                    }
                )
            add("authors", authors, "authorships")
        return fields, provenance

    @staticmethod
    def _external_id(value: object) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        return value.rstrip("/").rsplit("/", 1)[-1]
