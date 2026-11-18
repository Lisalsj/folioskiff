from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.agents.contracts import (
    AgentCapability,
    AgentFragment,
    AgentLocation,
    AgentModel,
    AgentProbeCode,
    AgentRequest,
    AgentResult,
    ModelCatalog,
)
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.settings import AppPaths


class ComparisonApiBackend:
    def __init__(self) -> None:
        self.capability = AgentCapability(
            backend_id="codex_cli",
            display_name="Codex CLI",
            location=AgentLocation.CLOUD,
            probe_code=AgentProbeCode.READY,
            installed=True,
            authenticated=True,
            available=True,
            supports_schema=True,
            supports_streaming=True,
            supports_vision=False,
            supports_external_research=False,
        )
        self.calls = 0

    async def discover_models(self) -> ModelCatalog:
        return ModelCatalog(
            models=(AgentModel(model_id="fixture-model", display_name="Fixture"),),
            supports_manual_model_id=True,
            supports_default_model=True,
        )

    async def generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: Callable[[AgentFragment], Awaitable[None]],
        cancelled: Callable[[], bool],
    ) -> AgentResult:
        self.calls += 1
        await on_fragment(AgentFragment(index=0, kind="text", text="comparison-fragment"))
        work_ids = list(dict.fromkeys(re.findall(r'work_id="([^"]+)"', request.prompt)))
        references = [
            {
                "work_id": work_id,
                "evidence_id": "abstract:work",
                "kind": "abstract",
                "page_number": None,
                "locator": None,
                "url": None,
                "external_title": None,
            }
            for work_id in work_ids[:2]
        ]
        statement = {"text": "Grounded comparison.", "references": references}
        return AgentResult(
            raw_output="{}",
            structured_output={
                "title": "<script>Comparison</script>",
                "overview": statement,
                "axes": [{"name": "Method", "findings": [statement]}],
                "agreements": [statement],
                "differences": [statement],
                "tradeoffs": [statement],
                "limitations": [statement],
                "recommendations": [statement],
            },
            resolved_model_id="fixture-model",
        )


def _paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )


def _seed(app: Any) -> None:
    from paper_reader.discovery.models import WorkRecord

    with app.state.sessions() as session:
        session.add_all(
            [
                WorkRecord(
                    id=f"work-comparison-api-{index}",
                    canonical_title=f"Comparison API paper {index}",
                    normalized_title=f"comparison api paper {index}",
                    authors=["Alice"],
                    abstract=f"Stable local abstract {index}.",
                    published_at=datetime(2026, 7, index, tzinfo=UTC),
                )
                for index in (1, 2)
            ]
        )
        session.commit()


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


async def _wait(client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    for _ in range(100):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in {"succeeded", "failed", "cancelled"}:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError("comparison Job did not finish")


@pytest.mark.asyncio
async def test_comparison_api_is_click_only_private_idempotent_and_exportable(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    backend = ComparisonApiBackend()
    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((backend,)),
    )
    _seed(app)
    selection = {
        "items": [
            {"work_id": "work-comparison-api-2", "document_version_id": None},
            {"work_id": "work-comparison-api-1", "document_version_id": None},
        ],
        "model_id": "fixture-model",
        "user_instruction": "Compare reliability.",
    }

    async with local_client(app) as client:
        unauthorized = await client.post(
            "/api/v1/comparisons/preflight",
            json=selection,
        )
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        empty = await client.get("/api/v1/comparisons/reports")
        preflight = await client.post(
            "/api/v1/comparisons/preflight",
            json=selection,
        )
        assert backend.calls == 0
        command = {
            "selection": selection,
            "preflight_fingerprint": preflight.json()["preflight_fingerprint"],
        }
        origin = {"Origin": "http://127.0.0.1"}
        headers = {
            **origin,
            "X-CSRF-Token": client.cookies["paper_reader_csrf"],
            "Idempotency-Key": "comparison-api",
        }
        missing_csrf = await client.post(
            "/api/v1/comparisons/reports",
            json=command,
            headers={**origin, "Idempotency-Key": "comparison-no-csrf"},
        )
        created = await client.post(
            "/api/v1/comparisons/reports",
            json=command,
            headers=headers,
        )
        repeated = await client.post(
            "/api/v1/comparisons/reports",
            json=command,
            headers=headers,
        )
        terminal = await _wait(client, created.json()["id"])
        report_id = terminal["result"]["report_id"]
        history = await client.get("/api/v1/comparisons/reports")
        report = await client.get(f"/api/v1/comparisons/reports/{report_id}")
        markdown = await client.get(
            f"/api/v1/comparisons/reports/{report_id}/markdown"
        )
        fragments = await client.get(
            f"/api/v1/comparisons/jobs/{created.json()['id']}/fragments"
        )
        paper_evidence = await client.get(
            f"/api/v1/comparisons/jobs/{created.json()['id']}/paper-evidence"
        )

    assert unauthorized.status_code == 401
    assert empty.status_code == 200 and empty.json() == []
    assert preflight.status_code == 200
    assert preflight.json()["mode"] == "direct"
    assert [item["work_id"] for item in preflight.json()["items"]] == [
        "work-comparison-api-2",
        "work-comparison-api-1",
    ]
    assert missing_csrf.status_code == 403
    assert created.status_code == 202
    assert repeated.status_code == 202
    assert repeated.json()["id"] == created.json()["id"]
    assert terminal["status"] == "succeeded"
    assert backend.calls == 1
    assert history.status_code == 200 and len(history.json()) == 1
    assert report.status_code == 200
    assert [item["work_id"] for item in report.json()["items"]] == [
        "work-comparison-api-2",
        "work-comparison-api-1",
    ]
    assert report.headers["cache-control"] == "private, no-store"
    assert markdown.status_code == 200
    assert "&lt;script&gt;Comparison&lt;/script&gt;" in markdown.text
    assert markdown.headers["x-content-type-options"] == "nosniff"
    assert fragments.status_code == 200 and fragments.json()[0]["sequence"] == 0
    assert paper_evidence.status_code == 200 and paper_evidence.json() == []
