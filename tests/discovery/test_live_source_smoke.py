from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from paper_reader.settings import SourceCredentials

FIXTURES = Path(__file__).parents[1] / "fixtures" / "connectors"


@pytest.mark.asyncio
async def test_live_source_smoke_returns_minimal_public_evidence() -> None:
    from scripts.smoke_sources import run_live_smoke

    payload = json.loads(
        (FIXTURES / "europe_pmc_response.json").read_text(encoding="utf-8")
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_live_smoke(
            source="europe_pmc",
            month="2026-07",
            term="cancer",
            limit=2,
            credentials=SourceCredentials(),
            client=client,
        )

    assert result["schema_version"] == "paper-reader-source-live-smoke-v1"
    assert result["ok"] is True
    assert result["source"] == "europe_pmc"
    assert result["month"] == "2026-07"
    assert result["term"] == "cancer"
    assert result["estimate_total"] == 3
    assert result["local_filter_applied"] is True
    assert result["remote_record_count"] == 2
    assert result["record_count"] == 1
    assert result["records"] == [
        {
            "external_id": "MED/12345678",
            "doi": "10.1000/cancer.agent.1",
            "updated_at": "2026-07-08T00:00:00+00:00",
        }
    ]
    assert len(requests) == 2
    assert requests[1].url.params["pageSize"] == "50"
    assert "UPDATE_DATE:[2026-07-01 TO 2026-07-31]" in requests[0].url.params[
        "query"
    ]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "abstractText" not in serialized
    assert "fullTextUrlList" not in serialized


@pytest.mark.asyncio
async def test_live_source_smoke_never_exposes_configured_key() -> None:
    from scripts.smoke_sources import run_live_smoke

    secret = "ieee-live-secret"
    payload = json.loads(
        (FIXTURES / "ieee_response.json").read_text(encoding="utf-8")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["apikey"] == secret
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_live_smoke(
            source="ieee",
            month="2026-07",
            term="agent",
            limit=1,
            credentials=SourceCredentials(ieee_api_key=secret),
            client=client,
        )

    assert result["ok"] is True
    assert secret not in json.dumps(result)


@pytest.mark.asyncio
async def test_live_source_smoke_fails_before_network_without_required_key() -> None:
    from scripts.smoke_sources import LiveSmokeFailure, run_live_smoke

    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LiveSmokeFailure) as captured:
            await run_live_smoke(
                source="springer_nature",
                month="2026-06",
                term="cancer",
                limit=1,
                credentials=SourceCredentials(),
                client=client,
            )

    assert captured.value.code == "missing_api_key"
    assert captured.value.retryable is False
    assert called is False


@pytest.mark.asyncio
async def test_live_source_smoke_requires_a_nonempty_official_response() -> None:
    from scripts.smoke_sources import LiveSmokeFailure, run_live_smoke

    payload = {
        "hitCount": 0,
        "nextCursorMark": "*",
        "resultList": {"result": []},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LiveSmokeFailure) as captured:
            await run_live_smoke(
                source="europe_pmc",
                month="2026-06",
                term="definitely-empty",
                limit=1,
                credentials=SourceCredentials(),
                client=client,
            )

    assert captured.value.code == "empty_result"
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_live_source_smoke_rejects_remote_candidates_outside_local_rules() -> None:
    from scripts.smoke_sources import LiveSmokeFailure, run_live_smoke

    payload = json.loads(
        (FIXTURES / "europe_pmc_response.json").read_text(encoding="utf-8")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LiveSmokeFailure) as captured:
            await run_live_smoke(
                source="europe_pmc",
                month="2026-06",
                term="cancer",
                limit=2,
                credentials=SourceCredentials(),
                client=client,
            )

    assert captured.value.code == "empty_local_match"
    assert captured.value.retryable is False


def test_live_source_smoke_cli_redacts_unexpected_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import smoke_sources

    async def fail(arguments: object) -> dict[str, object]:
        raise RuntimeError(
            "IEEE_XPLORE_API_KEY=do-not-print "
            "https://example.invalid/?api_key=do-not-print"
        )

    monkeypatch.setattr(smoke_sources, "_run_from_arguments", fail)

    exit_code = smoke_sources.main(
        (
            "--source",
            "ieee",
            "--month",
            "2026-06",
            "--term",
            "agent",
        )
    )
    evidence = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert evidence == {
        "error_code": "unexpected_failure",
        "month": "2026-06",
        "ok": False,
        "retryable": False,
        "schema_version": "paper-reader-source-live-smoke-v1",
        "source": "ieee",
    }
    assert "do-not-print" not in json.dumps(evidence)
