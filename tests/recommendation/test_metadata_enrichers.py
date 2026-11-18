from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parents[1] / "fixtures" / "enrichers"
FETCHED_AT = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)


def lookup():  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.enrichment import WorkLookup

    return WorkLookup(
        work_id="work-1",
        doi="https://doi.org/10.5555/AGENT.2026.1",
        title="Reliable Paper Reading Agents",
        authors=("Alice Researcher",),
        source_ids={"arxiv": "2607.00001"},
    )


@pytest.mark.asyncio
async def test_openalex_requires_key_and_never_requests_when_disabled() -> None:
    from paper_reader.recommendation.enrichers.openalex import OpenAlexEnricher

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        enricher = OpenAlexEnricher(client=client, api_key=None)
        result = await enricher.enrich(lookup())

    assert enricher.capabilities.enabled is False
    assert enricher.capabilities.disabled_reason == "missing_api_key"
    assert result.status == "disabled"
    assert result.warnings == ("missing_api_key",)
    assert calls == 0


@pytest.mark.asyncio
async def test_openalex_official_work_contract_preserves_normalized_field_provenance() -> None:
    from paper_reader.recommendation.enrichers.openalex import OpenAlexEnricher

    payload = json.loads((FIXTURES / "openalex_work.json").read_text(encoding="utf-8"))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=payload,
            headers={
                "X-RateLimit-Limit": "10000",
                "X-RateLimit-Remaining": "9999",
                "X-RateLimit-Reset": "43200",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAlexEnricher(
            client=client,
            api_key="openalex-secret",
            min_interval_seconds=0,
            clock=lambda: FETCHED_AT,
        ).enrich(lookup())

    assert len(requests) == 1
    assert requests[0].url.params["api_key"] == "openalex-secret"
    assert "citation_normalized_percentile" in requests[0].url.params["select"]
    assert result.status == "observed"
    assert result.external_id == "W4400000001"
    assert result.matched_by == "doi"
    assert result.fields["citation_count"] == 42
    assert result.fields["fwci"] == 3.25
    assert result.fields["citation_normalized_percentile"]["value"] == 0.94
    assert result.fields["venue"]["name"] == "Journal of Reliable Agents"
    assert result.fields["authors"][0]["institutions"][0]["name"] == (
        "Institute for Reliable AI"
    )
    assert result.field_provenance["citation_count"] == "openalex:cited_by_count"
    assert result.field_provenance["authors"] == "openalex:authorships"
    assert result.rate_limit is not None
    assert result.rate_limit.remaining == 9999
    assert result.fetched_at == FETCHED_AT
    assert len(result.response_sha256 or "") == 64


@pytest.mark.asyncio
async def test_semantic_scholar_uses_optional_key_and_official_paper_fields() -> None:
    from paper_reader.recommendation.enrichers.semantic_scholar import (
        SemanticScholarEnricher,
    )

    payload = json.loads(
        (FIXTURES / "semantic_scholar_paper.json").read_text(encoding="utf-8")
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        enricher = SemanticScholarEnricher(
            client=client,
            api_key="s2-secret",
            min_interval_seconds=0,
            clock=lambda: FETCHED_AT,
        )
        result = await enricher.enrich(lookup())

    assert enricher.capabilities.enabled is True
    assert requests[0].headers["x-api-key"] == "s2-secret"
    assert requests[0].url.raw_path.split(b"?", 1)[0].endswith(
        b"/paper/DOI:10.5555%2Fagent.2026.1"
    )
    assert "influentialCitationCount" in requests[0].url.params["fields"]
    assert result.external_id == "0123456789abcdef0123456789abcdef01234567"
    assert result.fields["citation_count"] == 40
    assert result.fields["influential_citation_count"] == 7
    assert result.fields["venue"]["type"] == "journal"
    assert result.field_provenance["influential_citation_count"] == (
        "semantic_scholar:influentialCitationCount"
    )


@pytest.mark.asyncio
async def test_semantic_scholar_anonymous_mode_omits_private_header() -> None:
    from paper_reader.recommendation.enrichers.semantic_scholar import (
        SemanticScholarEnricher,
    )

    payload = json.loads(
        (FIXTURES / "semantic_scholar_paper.json").read_text(encoding="utf-8")
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        enricher = SemanticScholarEnricher(client=client, api_key=None, min_interval_seconds=0)
        await enricher.enrich(lookup())

    assert enricher.capabilities.enabled is True
    assert enricher.capabilities.credential_configured is False
    assert "x-api-key" not in requests[0].headers


@pytest.mark.asyncio
async def test_crossref_polite_contract_identifies_client_and_preserves_relations() -> None:
    from paper_reader.recommendation.enrichers.crossref import CrossrefEnricher

    payload = json.loads((FIXTURES / "crossref_work.json").read_text(encoding="utf-8"))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=payload,
            headers={"X-Rate-Limit-Limit": "10", "X-Rate-Limit-Interval": "1s"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await CrossrefEnricher(
            client=client,
            contact_email="reader@example.org",
            min_interval_seconds=0,
            clock=lambda: FETCHED_AT,
        ).enrich(lookup())

    assert requests[0].url.params["mailto"] == "reader@example.org"
    assert "mailto:reader@example.org" in requests[0].headers["user-agent"]
    assert requests[0].url.raw_path.split(b"?", 1)[0].endswith(
        b"/works/10.5555%2Fagent.2026.1"
    )
    assert result.status == "observed"
    assert result.external_id == "10.5555/agent.2026.1"
    assert result.fields["citation_count"] == 41
    assert result.fields["venue"] == "Journal of Reliable Agents"
    assert result.fields["relations"]["is-preprint-of"][0]["id"] == (
        "10.5555/agent.2026.2"
    )
    assert result.field_provenance["relations"] == "crossref:relation"
    assert result.rate_limit is not None
    assert result.rate_limit.limit == 10
    assert result.rate_limit.interval_seconds == 1


@pytest.mark.asyncio
async def test_enrichers_do_not_request_when_no_supported_identifier_exists() -> None:
    from paper_reader.recommendation.enrichers.crossref import CrossrefEnricher
    from paper_reader.recommendation.enrichment import WorkLookup

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await CrossrefEnricher(client=client).enrich(
            WorkLookup(
                work_id="work-2",
                doi=None,
                title="Paper without DOI",
                authors=("Bob",),
            )
        )

    assert result.status == "not_applicable"
    assert result.warnings == ("missing_doi",)
    assert calls == 0


@pytest.mark.asyncio
async def test_rate_limit_errors_keep_retry_hint_without_leaking_credentials() -> None:
    from paper_reader.recommendation.enrichers.openalex import OpenAlexEnricher
    from paper_reader.recommendation.enrichment import EnricherRequestError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EnricherRequestError) as captured:
            await OpenAlexEnricher(
                client=client,
                api_key="do-not-leak",
                min_interval_seconds=0,
            ).enrich(lookup())

    assert captured.value.code == "rate_limited"
    assert captured.value.retryable is True
    assert captured.value.retry_after_seconds == 7
    assert "do-not-leak" not in str(captured.value)
