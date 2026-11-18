from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from paper_reader import __version__

FIXTURES = Path(__file__).parents[1] / "fixtures" / "connectors"
EXPECTED_USER_AGENT = f"paper-reader/{__version__} (local research application)"


def source_query():  # type: ignore[no-untyped-def]
    from paper_reader.discovery.connectors import SourceQuery
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
@pytest.mark.parametrize(
    ("module_name", "class_name"),
    (
        ("paper_reader.discovery.sources.ieee", "IeeeConnector"),
        ("paper_reader.discovery.sources.springer_nature", "SpringerNatureConnector"),
    ),
)
async def test_keyed_connector_is_disabled_and_makes_no_request_without_key(
    module_name: str,
    class_name: str,
) -> None:
    import importlib

    from paper_reader.discovery.connectors import ConnectorRequestError

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    connector_class = getattr(importlib.import_module(module_name), class_name)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = connector_class(client=client, api_key=None)
        assert connector.capabilities.enabled is False
        assert connector.capabilities.disabled_reason == "missing_api_key"
        assert connector.capabilities.setup_url is not None
        with pytest.raises(ConnectorRequestError) as captured:
            await connector.estimate(source_query())

    assert captured.value.code == "configuration_missing"
    assert captured.value.retryable is False
    assert calls == 0


@pytest.mark.asyncio
async def test_ieee_contract_uses_insert_update_range_and_one_based_paging() -> None:
    from paper_reader.discovery.sources.ieee import IeeeConnector

    payload = json.loads((FIXTURES / "ieee_response.json").read_text(encoding="utf-8"))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = IeeeConnector(client=client, api_key="ieee-secret")
        query = replace(
            source_query(),
            connector_id="ieee",
            filters={"content_type": "Journals", "open_access": True},
        )
        estimate = await connector.estimate(query)
        page = await connector.fetch_page(query, cursor=None, limit=2)

    assert connector.capabilities.enabled is True
    assert connector.capabilities.supports_exact_updated_date is True
    assert estimate.total == 3
    assert estimate.exact is True
    params = requests[0].url.params
    assert params["apikey"] == "ieee-secret"
    assert params["start_date"] == "20260601"
    assert params["end_date"] == "20260731"
    assert params["start_record"] == "1"
    assert params["max_records"] == "1"
    assert params["content_type"] == "Journals"
    assert params["open_access"] == "True"
    assert requests[1].url.params["max_records"] == "2"
    assert requests[1].headers["User-Agent"] == EXPECTED_USER_AGENT
    assert page.next_cursor == "3"
    paper = page.records[0]
    assert paper.external_id == "11000001"
    assert paper.doi == "10.1109/AGENT.2026.11000001"
    assert paper.updated_at is not None
    assert paper.field_provenance["updated_at"] == "ieee:insert_date"
    assert paper.raw_payload["access_type"] == "Open Access"


@pytest.mark.asyncio
async def test_springer_meta_v2_contract_marks_online_date_as_approximate() -> None:
    from paper_reader.discovery.sources.springer_nature import SpringerNatureConnector

    payload = json.loads((FIXTURES / "springer_nature_response.json").read_text(encoding="utf-8"))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = SpringerNatureConnector(client=client, api_key="springer-secret")
        query = replace(
            source_query(),
            connector_id="springer_nature",
            filters={"open_access": True, "content_type": "Journal"},
        )
        estimate = await connector.estimate(query)
        page = await connector.fetch_page(query, cursor=None, limit=2)

    assert connector.capabilities.enabled is True
    assert connector.capabilities.supports_exact_updated_date is False
    assert estimate.total == 3
    assert estimate.exact is False
    assert "online_date_is_not_revision_date" in estimate.warnings
    params = requests[0].url.params
    assert params["api_key"] == "springer-secret"
    assert params["s"] == "1"
    assert params["p"] == "1"
    assert "onlinedatefrom:2026-06-01" in params["q"]
    assert "onlinedateto:2026-07-31" in params["q"]
    assert "openaccess:true" in params["q"]
    assert requests[1].headers["User-Agent"] == EXPECTED_USER_AGENT
    assert page.next_cursor == "3"
    paper = page.records[0]
    assert paper.external_id == "10.1007/s00100-026-00001-1"
    assert paper.doi == "10.1007/s00100-026-00001-1"
    assert paper.authors == ("Researcher, Alice", "Scientist, Bob")
    assert paper.updated_at is not None
    assert paper.field_provenance["updated_at"] == "springer_nature:onlineDate"
    assert paper.raw_payload["open_access"] is True
