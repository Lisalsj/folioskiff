"""Official Europe PMC REST connector with UPDATE_DATE filtering."""

from __future__ import annotations

from typing import Any

import httpx

from paper_reader.discovery.connectors import (
    ConnectorCapabilities,
    ConnectorEstimate,
    ConnectorRequestError,
    SourcePage,
    SourceQuery,
)
from paper_reader.discovery.identity import SourcePaper
from paper_reader.discovery.sources.http import (
    SOURCE_USER_AGENT,
    RequestPacer,
    get_response,
    json_object,
)
from paper_reader.discovery.sources.utils import clean_text, month_bounds, parse_datetime


class EuropePmcConnector:
    endpoint = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    capabilities = ConnectorCapabilities(
        connector_id="europe_pmc",
        display_name="Europe PMC",
        credential_requirement="none",
        supports_exact_updated_date=True,
        setup_url="https://europepmc.org/RestfulWebService",
    )

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        min_interval_seconds: float = 0,
    ) -> None:
        self._client = client
        self._pacer = RequestPacer(min_interval_seconds)

    async def estimate(self, query: SourceQuery) -> ConnectorEstimate:
        page = await self._fetch(query, cursor="*", limit=1)
        return ConnectorEstimate(total=page.total, exact=True)

    async def fetch_page(
        self,
        query: SourceQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> SourcePage:
        return await self._fetch(query, cursor=cursor or "*", limit=limit)

    async def _fetch(self, query: SourceQuery, *, cursor: str, limit: int) -> SourcePage:
        await self._pacer.wait()
        response = await get_response(
            self._client,
            self.endpoint,
            params={
                "query": self._search_query(query),
                "format": "json",
                "resultType": "core",
                "cursorMark": cursor,
                "pageSize": min(max(1, limit), 1_000),
            },
            headers={"User-Agent": SOURCE_USER_AGENT},
        )
        payload = json_object(response)
        result_list = payload.get("resultList")
        results = result_list.get("result") if isinstance(result_list, dict) else None
        if not isinstance(results, list):
            raise ConnectorRequestError(
                "response_incompatible",
                "Europe PMC resultList.result must be a list",
                retryable=False,
            )
        try:
            hit_count = payload["hitCount"]
            if isinstance(hit_count, bool):
                raise TypeError
            total = int(hit_count)
        except (TypeError, ValueError) as error:
            raise ConnectorRequestError(
                "response_incompatible",
                "Europe PMC hitCount must be an integer",
                retryable=False,
            ) from error
        except KeyError as error:
            raise ConnectorRequestError(
                "response_incompatible",
                "Europe PMC hitCount is required",
                retryable=False,
            ) from error
        records = tuple(self._paper(item) for item in results if isinstance(item, dict))
        next_mark = payload.get("nextCursorMark")
        next_cursor = (
            next_mark
            if isinstance(next_mark, str) and next_mark and next_mark != cursor and records
            else None
        )
        return SourcePage(records=records, next_cursor=next_cursor, total=total)

    @staticmethod
    def _search_query(query: SourceQuery) -> str:
        start, end = month_bounds(query.months)
        expression = query.compiled_keywords.boolean_expression
        clauses = [f"({expression})", f"UPDATE_DATE:[{start} TO {end}]"]
        if query.filters.get("open_access") is True:
            clauses.append("OPEN_ACCESS:Y")
        source = query.filters.get("source")
        if isinstance(source, str) and source:
            clauses.append(f"SRC:{source}")
        return " AND ".join(clauses)

    @classmethod
    def _paper(cls, item: dict[str, Any]) -> SourcePaper:
        source = clean_text(item.get("source"))
        identifier = clean_text(item.get("id") or item.get("extId"))
        title = clean_text(item.get("title"))
        if source is None or identifier is None or title is None:
            raise ConnectorRequestError(
                "response_incompatible",
                "Europe PMC record is missing source, id, or title",
                retryable=False,
            )
        authors = cls._authors(item)
        open_access = str(item.get("isOpenAccess", "N")).upper() == "Y"
        return SourcePaper(
            connector_id="europe_pmc",
            external_id=f"{source}/{identifier}",
            title=title,
            authors=authors,
            abstract=clean_text(item.get("abstractText")),
            doi=clean_text(item.get("doi")),
            published_at=parse_datetime(item.get("firstPublicationDate")),
            updated_at=parse_datetime(item.get("dateOfRevision") or item.get("firstIndexDate")),
            landing_url=f"https://europepmc.org/article/{source}/{identifier}",
            field_provenance={
                "title": "europe_pmc:title",
                "authors": "europe_pmc:authorList",
                "abstract": "europe_pmc:abstractText",
                "published_at": "europe_pmc:firstPublicationDate",
                "updated_at": (
                    "europe_pmc:dateOfRevision"
                    if item.get("dateOfRevision")
                    else "europe_pmc:firstIndexDate"
                ),
                "doi": "europe_pmc:doi",
            },
            raw_payload={
                "pmid": item.get("pmid"),
                "pmcid": item.get("pmcid"),
                "is_open_access": open_access,
                "full_text_urls": item.get("fullTextUrlList"),
                "version_list": item.get("versionList"),
            },
        )

    @staticmethod
    def _authors(item: dict[str, Any]) -> tuple[str, ...]:
        author_list = item.get("authorList", {})
        values = author_list.get("author", []) if isinstance(author_list, dict) else []
        if isinstance(values, list):
            names = tuple(
                name
                for value in values
                if isinstance(value, dict)
                and (name := clean_text(value.get("fullName"))) is not None
            )
            if names:
                return names
        author_string = clean_text(item.get("authorString"))
        return tuple(part.strip() for part in author_string.split(";")) if author_string else ()
