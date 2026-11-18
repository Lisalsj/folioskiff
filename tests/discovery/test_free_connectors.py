from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from paper_reader import __version__
from paper_reader.discovery.connectors import SourceQuery

FIXTURES = Path(__file__).parents[1] / "fixtures" / "connectors"
EXPECTED_USER_AGENT = f"paper-reader/{__version__} (local research application)"


def source_query() -> SourceQuery:
    from paper_reader.discovery.keywords import CompiledKeywordQuery

    return SourceQuery(
        connector_id="fixture",
        months=("2026-06", "2026-07"),
        compiled_keywords=CompiledKeywordQuery(
            version_id="keywords-1",
            boolean_expression='("AI agent" OR "智能体") AND NOT ("survey" OR "综述")',
            required_groups=(("AI agent", "智能体"),),
            boost_terms=("tool use", "工具调用"),
            excluded_groups=(("survey", "综述"),),
        ),
        filters={},
        fingerprint="f" * 64,
    )


@pytest.mark.asyncio
async def test_arxiv_atom_contract_query_paging_and_version_metadata() -> None:
    from paper_reader.discovery.sources.arxiv import ArxivConnector

    xml = (FIXTURES / "arxiv_response.xml").read_bytes()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=xml, headers={"Content-Type": "application/atom+xml"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = ArxivConnector(client=client, min_interval_seconds=0)
        query = source_query()
        query = replace(
            query,
            connector_id="arxiv",
            filters={"categories": ["cs.AI", "cs.MA"]},
        )
        estimate = await connector.estimate(query)
        first = await connector.fetch_page(query, cursor=None, limit=2)
        last = await connector.fetch_page(query, cursor="2", limit=2)

    assert estimate.total == 3
    assert estimate.exact is False
    assert "estimate_before_local_updated_filter" in estimate.warnings
    estimate_params = requests[0].url.params
    assert estimate_params["max_results"] == "1"
    assert 'all:"AI agent"' in estimate_params["search_query"]
    assert 'all:"智能体"' in estimate_params["search_query"]
    assert "ANDNOT" in estimate_params["search_query"]
    assert 'all:"综述"' in estimate_params["search_query"]
    assert "???" not in estimate_params["search_query"]
    assert "cat:cs.AI" in estimate_params["search_query"]
    assert requests[1].url.params["start"] == "0"
    assert requests[2].url.params["start"] == "2"
    assert requests[1].headers["User-Agent"] == EXPECTED_USER_AGENT
    assert first.next_cursor == "2"
    assert last.next_cursor is None
    paper = first.records[0]
    assert paper.external_id == "2607.00001v2"
    assert paper.doi == "10.5555/Agent.2026.1"
    assert paper.authors == ("Alice Researcher", "Bob Scientist")
    assert paper.field_provenance["updated_at"] == "arxiv:updated"
    assert paper.raw_payload["primary_category"] == "cs.AI"


@pytest.mark.asyncio
async def test_arxiv_rejects_atom_dtd_and_entity_expansion() -> None:
    from paper_reader.discovery.connectors import ConnectorRequestError
    from paper_reader.discovery.sources.arxiv import ArxivConnector

    hostile_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE feed [
  <!ENTITY total "3">
]>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>&total;</opensearch:totalResults>
</feed>
"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=hostile_xml,
            headers={"Content-Type": "application/atom+xml"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = ArxivConnector(client=client, min_interval_seconds=0)
        with pytest.raises(ConnectorRequestError) as captured:
            await connector.estimate(replace(source_query(), connector_id="arxiv"))

    assert captured.value.code == "response_incompatible"
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_openreview_v2_contract_uses_public_forum_search_and_offset() -> None:
    from paper_reader.discovery.sources.openreview import OpenReviewConnector

    payload = json.loads((FIXTURES / "openreview_response.json").read_text(encoding="utf-8"))
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OpenReviewConnector(client=client)
        query = replace(source_query(), connector_id="openreview")
        estimate = await connector.estimate(query)
        page = await connector.fetch_page(query, cursor="2", limit=2)

    assert estimate.total == 3
    assert estimate.exact is False
    first_body = json.loads(requests[0].content)
    page_body = json.loads(requests[1].content)
    assert requests[0].url.path == "/notes/search"
    assert requests[0].headers["User-Agent"] == EXPECTED_USER_AGENT
    assert first_body["source"] == "forum"
    assert first_body["count"] is True
    assert "query" not in first_body
    assert first_body["terms"] == ["AI agent", "智能体"]
    assert first_body["type"] == "terms"
    assert page_body["offset"] == 2
    assert page.next_cursor is None
    paper = page.records[0]
    assert paper.external_id == "note-agent-1"
    assert paper.title == "Reliable Tool-Using Agents"
    assert paper.authors == ("Alice Researcher", "Bob Scientist")
    assert paper.doi == "10.5555/openreview.agent.1"
    assert paper.updated_at is not None
    assert paper.landing_url == "https://openreview.net/forum?id=note-agent-1"


@pytest.mark.asyncio
async def test_openreview_balances_concepts_and_resumes_pagination() -> None:
    from paper_reader.discovery.sources.openreview import OpenReviewConnector

    groups = (
        ("智能体", "agent"),
        ("reinforcement learning", "强化学习"),
        ("具身智能", "embodied intelligence"),
    )
    query = source_query()
    query = replace(
        query, compiled_keywords=replace(query.compiled_keywords, required_groups=groups)
    )
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "query" not in body
        assert body["type"] == "terms"
        requests.append(body)
        group = groups.index(tuple(body["terms"]))
        offset = body["offset"]
        # The eligible paper is only ranked highly for the third concept.
        title = (
            "Agent reinforcement learning for embodied intelligence"
            if group == 2
            else "Agent planning"
        )
        return httpx.Response(
            200,
            json={
                "count": 2,
                "notes": [
                    {
                        "id": f"note-{group}-{offset}",
                        "content": {"title": {"value": title}},
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OpenReviewConnector(client=client)
        first = await connector.fetch_page(query, cursor=None, limit=1)
        assert first.next_cursor is not None
        # A fresh connector can continue the persisted cursor.
        resumed = OpenReviewConnector(client=client)
        pages = [first]
        while pages[-1].next_cursor is not None:
            pages.append(await resumed.fetch_page(query, cursor=pages[-1].next_cursor, limit=1))

    assert [(body["terms"], body["offset"]) for body in requests] == [
        (list(group), offset) for offset in (0, 1) for group in groups
    ]
    matches = [
        paper
        for page in pages
        for paper in page.records
        if query.compiled_keywords.matches(title=paper.title, abstract=paper.abstract)
    ]
    assert [paper.external_id for paper in matches] == ["note-2-0", "note-2-1"]
    assert pages[-1].next_cursor is None


@pytest.mark.asyncio
async def test_openreview_skips_empty_concept_searches() -> None:
    from paper_reader.discovery.sources.openreview import OpenReviewConnector

    query = source_query()
    query = replace(
        query,
        compiled_keywords=replace(
            query.compiled_keywords,
            required_groups=(("智能体", "agent"), ("embodied intelligence", "具身智能")),
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "agent" in body["terms"]:
            return httpx.Response(200, json={"count": 0, "notes": []})
        return httpx.Response(
            200,
            json={
                "count": 1,
                "notes": [
                    {
                        "id": "eligible",
                        "content": {"title": {"value": "Agent embodied intelligence"}},
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OpenReviewConnector(client=client)
        estimate = await connector.estimate(query)
        page = await connector.fetch_page(query, cursor=None, limit=50)
    assert estimate.total == 1
    assert [paper.external_id for paper in page.records] == ["eligible"]
    assert page.next_cursor is None


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"count": 0}, {"count": 1, "notes": ["invalid"]}])
async def test_openreview_rejects_incompatible_response_instead_of_empty_success(payload) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.connectors import ConnectorRequestError
    from paper_reader.discovery.sources.openreview import OpenReviewConnector

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        connector = OpenReviewConnector(client=client)
        with pytest.raises(ConnectorRequestError, match="list of objects"):
            await connector.fetch_page(source_query(), cursor=None, limit=50)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor", ["bad", '{"v":2,"group":1,"offsets":[0]}', '{"v":2,"group":0,"offsets":[true]}']
)
async def test_openreview_rejects_invalid_cursor_without_network_request(cursor: str) -> None:
    from paper_reader.discovery.connectors import ConnectorRequestError
    from paper_reader.discovery.sources.openreview import OpenReviewConnector

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid checkpoints must not reach the source")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ConnectorRequestError, match="cursor is invalid"):
            await OpenReviewConnector(client=client).fetch_page(
                source_query(), cursor=cursor, limit=50
            )


@pytest.mark.asyncio
async def test_europe_pmc_contract_uses_update_date_and_cursor_mark() -> None:
    from paper_reader.discovery.sources.europe_pmc import EuropePmcConnector

    payload = json.loads((FIXTURES / "europe_pmc_response.json").read_text(encoding="utf-8"))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = EuropePmcConnector(client=client)
        query = replace(source_query(), connector_id="europe_pmc")
        estimate = await connector.estimate(query)
        page = await connector.fetch_page(query, cursor="cursor-1", limit=2)

    assert estimate.total == 3
    assert estimate.exact is True
    params = requests[0].url.params
    assert "UPDATE_DATE:[2026-06-01 TO 2026-07-31]" in params["query"]
    assert params["resultType"] == "core"
    assert "sort" not in params
    assert requests[1].url.params["cursorMark"] == "cursor-1"
    assert requests[1].headers["User-Agent"] == EXPECTED_USER_AGENT
    assert page.next_cursor == "AoIIP4rKpCg1NTY0"
    paper = page.records[0]
    assert paper.external_id == "MED/12345678"
    assert paper.doi == "10.1000/cancer.agent.1"
    assert paper.updated_at is not None
    assert paper.raw_payload["is_open_access"] is True
    assert paper.landing_url == "https://europepmc.org/article/MED/12345678"


@pytest.mark.asyncio
async def test_europe_pmc_rejects_a_version_only_error_shell() -> None:
    from paper_reader.discovery.connectors import ConnectorRequestError
    from paper_reader.discovery.sources.europe_pmc import EuropePmcConnector

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "6.9"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = EuropePmcConnector(client=client)
        with pytest.raises(ConnectorRequestError) as captured:
            await connector.estimate(source_query())

    assert captured.value.code == "response_incompatible"
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_connector_http_errors_are_structured_and_retryable() -> None:
    from paper_reader.discovery.connectors import ConnectorRequestError
    from paper_reader.discovery.sources.europe_pmc import EuropePmcConnector

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"}, json={"error": "slow down"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = EuropePmcConnector(client=client)
        with pytest.raises(ConnectorRequestError) as captured:
            await connector.estimate(source_query())

    assert captured.value.code == "rate_limited"
    assert captured.value.retryable is True
    assert captured.value.retry_after_seconds == 7
