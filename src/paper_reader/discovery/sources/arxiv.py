"""Official arXiv Atom API connector."""

from __future__ import annotations

from xml.etree import ElementTree

import httpx
from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

from paper_reader.discovery.connectors import (
    ConnectorCapabilities,
    ConnectorEstimate,
    ConnectorRequestError,
    SourcePage,
    SourceQuery,
)
from paper_reader.discovery.identity import SourcePaper
from paper_reader.discovery.sources.http import SOURCE_USER_AGENT, RequestPacer, get_response
from paper_reader.discovery.sources.utils import boolean_query, clean_text, parse_datetime

ATOM = "{http://www.w3.org/2005/Atom}"
OPEN_SEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
ARXIV = "{http://arxiv.org/schemas/atom}"


class ArxivConnector:
    endpoint = "https://export.arxiv.org/api/query"
    capabilities = ConnectorCapabilities(
        connector_id="arxiv",
        display_name="arXiv",
        credential_requirement="none",
        supports_exact_updated_date=False,
        setup_url="https://info.arxiv.org/help/api/",
    )

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        min_interval_seconds: float = 3,
    ) -> None:
        self._client = client
        self._pacer = RequestPacer(min_interval_seconds)

    async def estimate(self, query: SourceQuery) -> ConnectorEstimate:
        page = await self._fetch(query, cursor="0", limit=1)
        return ConnectorEstimate(
            total=page.total,
            exact=False,
            warnings=("estimate_before_local_updated_filter",),
        )

    async def fetch_page(
        self,
        query: SourceQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> SourcePage:
        return await self._fetch(query, cursor=cursor or "0", limit=limit)

    async def _fetch(self, query: SourceQuery, *, cursor: str, limit: int) -> SourcePage:
        try:
            start = max(0, int(cursor))
        except ValueError as error:
            raise ConnectorRequestError(
                "permanent_request",
                "arXiv cursor must be an integer",
                retryable=False,
            ) from error
        page_size = min(max(1, limit), 2_000)
        await self._pacer.wait()
        response = await get_response(
            self._client,
            self.endpoint,
            params={
                "search_query": self._search_query(query),
                "start": start,
                "max_results": page_size,
                "sortBy": "lastUpdatedDate",
                "sortOrder": "descending",
            },
            headers={"User-Agent": SOURCE_USER_AGENT},
        )
        try:
            root = DefusedElementTree.fromstring(response.content)
        except (ElementTree.ParseError, DefusedXmlException) as error:
            raise ConnectorRequestError(
                "response_incompatible",
                "arXiv returned invalid Atom XML",
                retryable=False,
            ) from error
        total = self._integer_text(root.find(f"{OPEN_SEARCH}totalResults"))
        records = tuple(self._paper(entry) for entry in root.findall(f"{ATOM}entry"))
        next_start = start + len(records)
        next_cursor = str(next_start) if records and next_start < total else None
        return SourcePage(records=records, next_cursor=next_cursor, total=total)

    @staticmethod
    def _search_query(query: SourceQuery) -> str:
        expression = boolean_query(
            query.compiled_keywords,
            format_term=lambda term: f'all:"{term.replace(chr(34), "")}"',
            exclude_operator="ANDNOT",
        )
        categories = query.filters.get("categories", [])
        if isinstance(categories, list) and categories:
            safe_categories = [item for item in categories if isinstance(item, str) and item]
            if safe_categories:
                category_query = " OR ".join(f"cat:{item}" for item in safe_categories)
                expression += f" AND ({category_query})"
        return expression

    @classmethod
    def _paper(cls, entry: ElementTree.Element) -> SourcePaper:
        identifier_url = cls._text(entry.find(f"{ATOM}id"))
        external_id = identifier_url.rsplit("/abs/", 1)[-1]
        title = clean_text(cls._text(entry.find(f"{ATOM}title")))
        if not external_id or title is None:
            raise ConnectorRequestError(
                "response_incompatible",
                "arXiv entry is missing id or title",
                retryable=False,
            )
        authors = tuple(
            name
            for author in entry.findall(f"{ATOM}author")
            if (name := clean_text(cls._text(author.find(f"{ATOM}name")))) is not None
        )
        links = [
            {
                "href": link.attrib.get("href"),
                "rel": link.attrib.get("rel"),
                "type": link.attrib.get("type"),
            }
            for link in entry.findall(f"{ATOM}link")
        ]
        landing_url = next(
            (str(link["href"]) for link in links if link["rel"] == "alternate" and link["href"]),
            identifier_url,
        )
        categories = [
            item.attrib["term"]
            for item in entry.findall(f"{ATOM}category")
            if item.attrib.get("term")
        ]
        primary = entry.find(f"{ARXIV}primary_category")
        primary_category = primary.attrib.get("term") if primary is not None else None
        return SourcePaper(
            connector_id="arxiv",
            external_id=external_id,
            title=title,
            authors=authors,
            abstract=clean_text(cls._text(entry.find(f"{ATOM}summary"))),
            doi=clean_text(cls._text(entry.find(f"{ARXIV}doi"))),
            published_at=parse_datetime(cls._text(entry.find(f"{ATOM}published"))),
            updated_at=parse_datetime(cls._text(entry.find(f"{ATOM}updated"))),
            landing_url=landing_url,
            field_provenance={
                "title": "arxiv:title",
                "authors": "arxiv:author",
                "abstract": "arxiv:summary",
                "published_at": "arxiv:published",
                "updated_at": "arxiv:updated",
                "doi": "arxiv:doi",
            },
            raw_payload={
                "categories": categories,
                "primary_category": primary_category,
                "links": links,
            },
        )

    @staticmethod
    def _text(element: ElementTree.Element | None) -> str:
        return element.text if element is not None and element.text is not None else ""

    @classmethod
    def _integer_text(cls, element: ElementTree.Element | None) -> int:
        try:
            return int(cls._text(element).strip())
        except ValueError as error:
            raise ConnectorRequestError(
                "response_incompatible",
                "arXiv response is missing totalResults",
                retryable=False,
            ) from error
