"""Official Springer Nature versioned Metadata API (Meta v2) connector."""

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


class SpringerNatureConnector:
    endpoint = "https://api.springernature.com/meta/v2/json"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str | None,
        min_interval_seconds: float = 0,
    ) -> None:
        self._client = client
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._pacer = RequestPacer(min_interval_seconds)
        self.capabilities = ConnectorCapabilities(
            connector_id="springer_nature",
            display_name="Springer Nature",
            credential_requirement="api_key",
            supports_exact_updated_date=False,
            enabled=self._api_key is not None,
            disabled_reason="missing_api_key" if self._api_key is None else None,
            credential_configured=self._api_key is not None,
            setup_url="https://dev.springernature.com/docs/quick-start/api-access/",
        )

    async def estimate(self, query: SourceQuery) -> ConnectorEstimate:
        page = await self._fetch(query, start=1, limit=1)
        return ConnectorEstimate(
            total=page.total,
            exact=False,
            warnings=("online_date_is_not_revision_date",),
        )

    async def fetch_page(
        self,
        query: SourceQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> SourcePage:
        try:
            start = max(1, int(cursor or "1"))
        except ValueError as error:
            raise ConnectorRequestError(
                "permanent_request",
                "Springer Nature cursor must be an integer",
                retryable=False,
            ) from error
        return await self._fetch(query, start=start, limit=limit)

    async def _fetch(self, query: SourceQuery, *, start: int, limit: int) -> SourcePage:
        api_key = self._require_key()
        await self._pacer.wait()
        payload = json_object(
            await get_response(
                self._client,
                self.endpoint,
                params={
                    "api_key": api_key,
                    "q": self._search_query(query),
                    "s": start,
                    "p": min(max(1, limit), 25),
                },
                headers={"User-Agent": SOURCE_USER_AGENT},
            )
        )
        records_value = payload.get("records", [])
        result_value = payload.get("result", [])
        if not isinstance(records_value, list) or not isinstance(result_value, list):
            raise ConnectorRequestError(
                "response_incompatible",
                "Springer Nature records and result must be lists",
                retryable=False,
            )
        metadata = result_value[0] if result_value and isinstance(result_value[0], dict) else {}
        try:
            total = int(metadata.get("total", len(records_value)))
        except (TypeError, ValueError) as error:
            raise ConnectorRequestError(
                "response_incompatible",
                "Springer Nature total must be an integer",
                retryable=False,
            ) from error
        records = tuple(self._paper(item) for item in records_value if isinstance(item, dict))
        next_start = start + len(records)
        next_cursor = str(next_start) if records and next_start <= total else None
        return SourcePage(records=records, next_cursor=next_cursor, total=total)

    def _require_key(self) -> str:
        if self._api_key is None:
            raise ConnectorRequestError(
                "configuration_missing",
                "Springer Nature API key is not configured",
                retryable=False,
            )
        return self._api_key

    @staticmethod
    def _search_query(query: SourceQuery) -> str:
        start, end = month_bounds(query.months)
        clauses = [
            f"({query.compiled_keywords.boolean_expression})",
            f"onlinedatefrom:{start}",
            f"onlinedateto:{end}",
        ]
        if query.filters.get("open_access") is True:
            clauses.append("openaccess:true")
        content_type = query.filters.get("content_type")
        if isinstance(content_type, str) and content_type:
            clauses.append(f'type:"{content_type}"')
        return " AND ".join(clauses)

    @classmethod
    def _paper(cls, item: dict[str, Any]) -> SourcePaper:
        identifier = clean_text(item.get("identifier"))
        doi = clean_text(item.get("doi"))
        if doi is None and identifier and identifier.casefold().startswith("doi:"):
            doi = identifier[4:]
        external_id = doi or identifier
        title = clean_text(item.get("title"))
        if external_id is None or title is None:
            raise ConnectorRequestError(
                "response_incompatible",
                "Springer Nature record is missing identifier or title",
                retryable=False,
            )
        urls = item.get("url", [])
        if not isinstance(urls, list):
            urls = []
        landing_url = next(
            (
                clean_text(url.get("value"))
                for url in urls
                if isinstance(url, dict) and url.get("format") == "html"
            ),
            None,
        )
        open_access = str(item.get("openaccess", "false")).casefold() == "true"
        return SourcePaper(
            connector_id="springer_nature",
            external_id=external_id,
            title=title,
            authors=cls._authors(item),
            abstract=clean_text(item.get("abstract")),
            doi=doi,
            published_at=parse_datetime(item.get("publicationDate")),
            updated_at=parse_datetime(item.get("onlineDate") or item.get("publicationDate")),
            landing_url=landing_url,
            field_provenance={
                "title": "springer_nature:title",
                "authors": "springer_nature:creators",
                "abstract": "springer_nature:abstract",
                "published_at": "springer_nature:publicationDate",
                "updated_at": "springer_nature:onlineDate",
                "doi": "springer_nature:doi|identifier",
            },
            raw_payload={
                "content_type": item.get("contentType"),
                "publication_title": item.get("journalTitle") or item.get("publicationName"),
                "publisher": item.get("publisher"),
                "open_access": open_access,
                "urls": urls,
                "language": item.get("language"),
            },
        )

    @staticmethod
    def _authors(item: dict[str, Any]) -> tuple[str, ...]:
        creators = item.get("creators", item.get("creator", []))
        if not isinstance(creators, list):
            return ()
        return tuple(
            name
            for creator in creators
            if (
                name := clean_text(creator.get("creator") if isinstance(creator, dict) else creator)
            )
            is not None
        )
