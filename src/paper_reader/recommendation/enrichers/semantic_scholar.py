"""Official Semantic Scholar Academic Graph paper enricher."""

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


class SemanticScholarEnricher:
    endpoint = "https://api.semanticscholar.org/graph/v1/paper"
    selected_fields = (
        "paperId",
        "externalIds",
        "url",
        "venue",
        "publicationVenue",
        "publicationTypes",
        "year",
        "referenceCount",
        "citationCount",
        "influentialCitationCount",
        "isOpenAccess",
        "openAccessPdf",
        "fieldsOfStudy",
        "s2FieldsOfStudy",
        "authors",
    )

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str | None,
        min_interval_seconds: float = 1,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._client = client
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._pacer = RequestPacer(min_interval_seconds)
        self._clock = clock
        self.capabilities = EnricherCapabilities(
            enricher_id="semantic_scholar",
            display_name="Semantic Scholar",
            version="graph-v1",
            enabled=True,
            disabled_reason=None,
            credential_requirement="optional_api_key",
            credential_configured=self._api_key is not None,
            setup_url="https://www.semanticscholar.org/product/api",
            default_requests_per_second=1 if self._api_key is not None else None,
            max_concurrency=1,
        )

    async def enrich(self, work: WorkLookup) -> EnrichmentResult:
        identifier, matched_by = self._identifier(work)
        if identifier is None or matched_by is None:
            return empty_result(
                work,
                self.capabilities,
                status=EnrichmentStatus.NOT_APPLICABLE,
                warning="missing_supported_identifier",
                clock=self._clock,
            )
        headers = {"User-Agent": "paper-reader/0.3"}
        if self._api_key is not None:
            headers["x-api-key"] = self._api_key
        await self._pacer.wait()
        payload, response = await request_json_object(
            self._client,
            f"{self.endpoint}/{quote(identifier, safe=':')}",
            params={"fields": ",".join(self.selected_fields)},
            headers=headers,
        )
        if payload is None:
            return empty_result(
                work,
                self.capabilities,
                status=EnrichmentStatus.NOT_FOUND,
                warning="paper_not_found",
                clock=self._clock,
            )
        external_id = payload.get("paperId")
        if not isinstance(external_id, str) or not external_id:
            raise EnricherRequestError(
                "response_incompatible",
                "Semantic Scholar paper is missing paperId",
                retryable=False,
            )
        fields, provenance = self._fields(payload)
        return observed_result(
            work,
            self.capabilities,
            external_id=external_id,
            matched_by=matched_by,
            fields=fields,
            field_provenance=provenance,
            payload=payload,
            rate_limit=rate_limit_observation(response),
            clock=self._clock,
        )

    @staticmethod
    def _identifier(work: WorkLookup) -> tuple[str | None, str | None]:
        if work.doi is not None:
            return f"DOI:{work.doi}", "doi"
        arxiv_id = work.source_ids.get("arxiv")
        if arxiv_id:
            return f"ARXIV:{arxiv_id}", "source_id:arxiv"
        return None, None

    @staticmethod
    def _fields(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        fields: dict[str, Any] = {}
        provenance: dict[str, str] = {}

        def add(key: str, value: Any, path: str) -> None:
            if value is not None:
                fields[key] = value
                provenance[key] = f"semantic_scholar:{path}"

        add("external_ids", payload.get("externalIds"), "externalIds")
        add("landing_url", payload.get("url"), "url")
        add("publication_types", payload.get("publicationTypes"), "publicationTypes")
        add("publication_year", payload.get("year"), "year")
        add("reference_count", payload.get("referenceCount"), "referenceCount")
        add("citation_count", payload.get("citationCount"), "citationCount")
        add(
            "influential_citation_count",
            payload.get("influentialCitationCount"),
            "influentialCitationCount",
        )
        add("is_open_access", payload.get("isOpenAccess"), "isOpenAccess")
        add("open_access_pdf", payload.get("openAccessPdf"), "openAccessPdf")
        add("fields_of_study", payload.get("fieldsOfStudy"), "fieldsOfStudy")
        add("s2_fields_of_study", payload.get("s2FieldsOfStudy"), "s2FieldsOfStudy")

        publication_venue = payload.get("publicationVenue")
        if isinstance(publication_venue, dict):
            add(
                "venue",
                {
                    "id": publication_venue.get("id"),
                    "name": publication_venue.get("name"),
                    "type": publication_venue.get("type"),
                    "alternate_names": publication_venue.get("alternate_names"),
                    "url": publication_venue.get("url"),
                },
                "publicationVenue",
            )
        else:
            add("venue", payload.get("venue"), "venue")

        authors = payload.get("authors")
        if isinstance(authors, list):
            add(
                "authors",
                [
                    {"id": item.get("authorId"), "name": item.get("name")}
                    for item in authors
                    if isinstance(item, dict)
                ],
                "authors",
            )
        return fields, provenance
