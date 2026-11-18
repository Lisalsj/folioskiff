"""Official OpenReview API v2 connector for public submission/forum notes."""

from __future__ import annotations

import json
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
    json_object,
    post_json_response,
)
from paper_reader.discovery.sources.utils import clean_text


class OpenReviewConnector:
    endpoint = "https://api2.openreview.net/notes/search"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        access_token: str | None = None,
        min_interval_seconds: float = 0,
    ) -> None:
        self._client = client
        self._access_token = access_token
        self._pacer = RequestPacer(min_interval_seconds)
        self.capabilities = ConnectorCapabilities(
            connector_id="openreview",
            display_name="OpenReview",
            credential_requirement="optional_account",
            supports_exact_updated_date=False,
            credential_configured=bool(access_token),
            setup_url="https://docs.openreview.net/getting-started/using-the-api",
            protocol_version="2",
        )

    async def estimate(self, query: SourceQuery) -> ConnectorEstimate:
        pages = [
            await self._fetch(query, group_index=index, offset=0, limit=1)
            for index in range(len(query.compiled_keywords.required_groups))
        ]
        return ConnectorEstimate(
            # Reported totals overlap and may be capped by the source. Keep
            # the estimate approximate before the authoritative local checks.
            total=sum(page.total or 0 for page in pages),
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
        group_count = len(query.compiled_keywords.required_groups)
        group_index, offsets = self._read_cursor(cursor, group_count)
        # Balance the bounded scan across every required concept instead of
        # spending it all on a broad first concept (or its first Chinese word).
        # An empty concept search must not hide the remaining searches.
        for _ in range(group_count):
            offset = offsets[group_index]
            if offset is not None:
                page = await self._fetch(query, group_index=group_index, offset=offset, limit=limit)
                offsets[group_index] = (
                    int(page.next_cursor) if page.next_cursor is not None else None
                )
                next_group = (group_index + 1) % group_count
                next_cursor = (
                    json.dumps({"v": 2, "group": next_group, "offsets": offsets})
                    if any(value is not None for value in offsets)
                    else None
                )
                if page.records:
                    return SourcePage(
                        records=page.records,
                        next_cursor=next_cursor,
                        total=page.total if group_count == 1 else None,
                    )
            group_index = (group_index + 1) % group_count
        return SourcePage(records=(), next_cursor=None, total=0)

    @staticmethod
    def _read_cursor(cursor: str | None, group_count: int) -> tuple[int, list[int | None]]:
        try:
            if group_count < 1:
                raise ValueError("no required concepts")
            if cursor is None:
                return 0, [0] * group_count
            # Preserve checkpoints made by the previous single-concept search.
            try:
                return 0, [max(0, int(cursor)), *([0] * (group_count - 1))]
            except ValueError:
                pass
            state = json.loads(cursor)
            if not isinstance(state, dict) or state.get("v") != 2:
                raise ValueError("unsupported cursor")
            group = state.get("group")
            offsets = state.get("offsets")
            if type(group) is not int or not 0 <= group < group_count:
                raise ValueError("invalid concept index")
            if not isinstance(offsets, list) or len(offsets) != group_count:
                raise ValueError("invalid offsets")
            if any(
                value is not None and (type(value) is not int or value < 0) for value in offsets
            ):
                raise ValueError("invalid offset")
            return group, offsets
        except (ValueError, TypeError) as error:
            raise ConnectorRequestError(
                "permanent_request", "OpenReview cursor is invalid", retryable=False
            ) from error

    async def _fetch(
        self, query: SourceQuery, *, group_index: int, offset: int, limit: int
    ) -> SourcePage:
        body: dict[str, Any] = {
            # API v2 accepts term/terms, not query. All confirmed alternatives
            # remain searchable; local matching enforces the complete AND/NOT.
            "terms": list(query.compiled_keywords.required_groups[group_index]),
            "type": "terms",
            "source": "forum",
            "count": True,
            "sort": "tmdate:desc",
            "limit": min(max(1, limit), 1_000),
            "offset": offset,
        }
        for key in ("invitation", "venue", "venueid", "domain"):
            value = query.filters.get(key)
            if isinstance(value, str) and value:
                body[key] = value
        headers = {"User-Agent": SOURCE_USER_AGENT}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        await self._pacer.wait()
        response = await post_json_response(
            self._client,
            self.endpoint,
            payload=body,
            headers=headers,
        )
        payload = json_object(response)
        notes = payload.get("notes")
        if not isinstance(notes, list) or any(not isinstance(note, dict) for note in notes):
            raise ConnectorRequestError(
                "response_incompatible",
                "OpenReview response notes must be a list of objects",
                retryable=False,
            )
        try:
            total = int(payload.get("count", len(notes)))
        except (TypeError, ValueError) as error:
            raise ConnectorRequestError(
                "response_incompatible",
                "OpenReview count must be an integer",
                retryable=False,
            ) from error
        records = tuple(self._paper(note) for note in notes)
        next_offset = offset + len(records)
        next_cursor = str(next_offset) if records and next_offset < total else None
        return SourcePage(records=records, next_cursor=next_cursor, total=total)

    @classmethod
    def _paper(cls, note: dict[str, Any]) -> SourcePaper:
        external_id = clean_text(note.get("id"))
        content = note.get("content", {})
        if not isinstance(content, dict):
            content = {}
        title = clean_text(cls._value(content.get("title")))
        if external_id is None or title is None:
            raise ConnectorRequestError(
                "response_incompatible",
                "OpenReview note is missing id or title",
                retryable=False,
            )
        author_value = cls._value(content.get("authors"))
        authors = (
            tuple(str(item) for item in author_value if isinstance(item, str))
            if isinstance(author_value, list)
            else ()
        )
        pdf = clean_text(cls._value(content.get("pdf")))
        return SourcePaper(
            connector_id="openreview",
            external_id=external_id,
            title=title,
            authors=authors,
            abstract=clean_text(cls._value(content.get("abstract"))),
            doi=clean_text(cls._value(content.get("doi"))),
            published_at=cls._milliseconds(note.get("cdate")),
            updated_at=cls._milliseconds(note.get("tmdate")),
            landing_url=f"https://openreview.net/forum?id={external_id}",
            field_provenance={
                "title": "openreview:content.title.value",
                "authors": "openreview:content.authors.value",
                "abstract": "openreview:content.abstract.value",
                "published_at": "openreview:cdate",
                "updated_at": "openreview:tmdate",
                "doi": "openreview:content.doi.value",
            },
            raw_payload={
                "forum": note.get("forum"),
                "number": note.get("number"),
                "venueid": cls._value(content.get("venueid")),
                "authorids": cls._value(content.get("authorids")),
                "pdf": (f"https://openreview.net{pdf}" if pdf and pdf.startswith("/") else pdf),
            },
        )

    @staticmethod
    def _value(value: Any) -> Any:
        return value.get("value") if isinstance(value, dict) and "value" in value else value

    @staticmethod
    def _milliseconds(value: object) -> datetime | None:
        if not isinstance(value, (int, float)):
            return None
        return datetime.fromtimestamp(value / 1_000, tz=UTC)
