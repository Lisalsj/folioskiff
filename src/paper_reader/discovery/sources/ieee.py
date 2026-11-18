"""Official IEEE Xplore Metadata API connector."""

from __future__ import annotations

from datetime import UTC, datetime
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
from paper_reader.discovery.sources.utils import clean_text, month_bounds


class IeeeConnector:
    endpoint = "https://ieeexploreapi.ieee.org/api/v1/search/articles"

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
            connector_id="ieee",
            display_name="IEEE Xplore",
            credential_requirement="api_key",
            supports_exact_updated_date=True,
            enabled=self._api_key is not None,
            disabled_reason="missing_api_key" if self._api_key is None else None,
            credential_configured=self._api_key is not None,
            setup_url="https://developer.ieee.org/getting_started",
        )

    async def estimate(self, query: SourceQuery) -> ConnectorEstimate:
        page = await self._fetch(query, start_record=1, limit=1)
        return ConnectorEstimate(total=page.total, exact=True)

    async def fetch_page(
        self,
        query: SourceQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> SourcePage:
        try:
            start_record = max(1, int(cursor or "1"))
        except ValueError as error:
            raise ConnectorRequestError(
                "permanent_request",
                "IEEE cursor must be an integer",
                retryable=False,
            ) from error
        return await self._fetch(query, start_record=start_record, limit=limit)

    async def _fetch(
        self,
        query: SourceQuery,
        *,
        start_record: int,
        limit: int,
    ) -> SourcePage:
        api_key = self._require_key()
        start_date, end_date = month_bounds(query.months)
        params: dict[str, Any] = {
            "apikey": api_key,
            "format": "json",
            "querytext": query.compiled_keywords.boolean_expression,
            "start_record": start_record,
            "max_records": min(max(1, limit), 200),
            "start_date": start_date.replace("-", ""),
            "end_date": end_date.replace("-", ""),
        }
        for key in ("content_type", "publisher", "publication_number"):
            value = query.filters.get(key)
            if isinstance(value, str) and value:
                params[key] = value
        if isinstance(query.filters.get("open_access"), bool):
            params["open_access"] = str(query.filters["open_access"])
        await self._pacer.wait()
        payload = json_object(
            await get_response(
                self._client,
                self.endpoint,
                params=params,
                headers={"User-Agent": SOURCE_USER_AGENT},
            )
        )
        articles = payload.get("articles", [])
        if not isinstance(articles, list):
            raise ConnectorRequestError(
                "response_incompatible",
                "IEEE articles must be a list",
                retryable=False,
            )
        raw_total = payload.get("total_records", payload.get("totalfound", len(articles)))
        try:
            total = int(raw_total)
        except (TypeError, ValueError) as error:
            raise ConnectorRequestError(
                "response_incompatible",
                "IEEE total record count must be an integer",
                retryable=False,
            ) from error
        records = tuple(self._paper(item) for item in articles if isinstance(item, dict))
        next_start = start_record + len(records)
        next_cursor = str(next_start) if records and next_start <= total else None
        return SourcePage(records=records, next_cursor=next_cursor, total=total)

    def _require_key(self) -> str:
        if self._api_key is None:
            raise ConnectorRequestError(
                "configuration_missing",
                "IEEE Xplore API key is not configured",
                retryable=False,
            )
        return self._api_key

    @classmethod
    def _paper(cls, item: dict[str, Any]) -> SourcePaper:
        article_number = clean_text(str(item.get("article_number", "")))
        title = clean_text(item.get("title"))
        if article_number is None or title is None:
            raise ConnectorRequestError(
                "response_incompatible",
                "IEEE article is missing article_number or title",
                retryable=False,
            )
        authors_container = item.get("authors", {})
        author_values = (
            authors_container.get("authors", []) if isinstance(authors_container, dict) else []
        )
        authors = tuple(
            name
            for author in author_values
            if isinstance(author, dict)
            and (name := clean_text(author.get("full_name"))) is not None
        )
        access_type = clean_text(item.get("access_type") or item.get("accessType"))
        return SourcePaper(
            connector_id="ieee",
            external_id=article_number,
            title=title,
            authors=authors,
            abstract=clean_text(item.get("abstract")),
            doi=clean_text(item.get("doi")),
            published_at=cls._publication_date(item),
            updated_at=cls._compact_date(item.get("insert_date")),
            landing_url=(
                clean_text(item.get("abstract_url"))
                or clean_text(item.get("html_url"))
                or f"https://ieeexplore.ieee.org/document/{article_number}"
            ),
            field_provenance={
                "title": "ieee:title",
                "authors": "ieee:authors.authors",
                "abstract": "ieee:abstract",
                "published_at": "ieee:publication_date",
                "updated_at": "ieee:insert_date",
                "doi": "ieee:doi",
            },
            raw_payload={
                "publication_title": item.get("publication_title"),
                "content_type": item.get("content_type"),
                "access_type": access_type,
                "pdf_url": item.get("pdf_url"),
                "index_terms": item.get("index_terms"),
                "affiliations": [
                    author.get("affiliation")
                    for author in author_values
                    if isinstance(author, dict) and author.get("affiliation")
                ],
            },
        )

    @staticmethod
    def _compact_date(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.strptime(value, "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            return None

    @staticmethod
    def _publication_date(item: dict[str, Any]) -> datetime | None:
        value = clean_text(item.get("publication_date"))
        if value:
            for pattern in ("%Y-%m-%d", "%d %B %Y", "%B %Y", "%Y"):
                try:
                    return datetime.strptime(value, pattern).replace(tzinfo=UTC)
                except ValueError:
                    continue
        year = clean_text(item.get("publication_year"))
        if year:
            try:
                return datetime(int(year), 1, 1, tzinfo=UTC)
            except ValueError:
                pass
        return None
