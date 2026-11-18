from __future__ import annotations

import asyncio
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
    AgentUsage,
    ModelCatalog,
)
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.discovery.connectors import (
    ConnectorCapabilities,
    ConnectorEstimate,
    ConnectorRegistry,
    SourcePage,
    SourceQuery,
)
from paper_reader.discovery.identity import SourcePaper
from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.jobs.repository import JobRepository
from paper_reader.reader.models import DocumentPageTextRecord
from paper_reader.reader.storage import ContentStore
from paper_reader.recommendation.preferences import PreferenceRepository
from paper_reader.settings import AppPaths

NOW = datetime(2026, 8, 1, 9, tzinfo=UTC)


class RelatedApiBackend:
    def __init__(self) -> None:
        self.capability = AgentCapability(
            backend_id="codex_cli",
            display_name="Related API fixture",
            location=AgentLocation.LOCAL,
            probe_code=AgentProbeCode.READY,
            installed=True,
            authenticated=True,
            available=True,
            supports_schema=True,
            supports_streaming=False,
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
        assert cancelled() is False
        await on_fragment(AgentFragment(index=0, kind="text", text="discarded"))
        return AgentResult(
            raw_output='{"summary":"refined"}',
            structured_output={
                "summary": "Diffusion planning agents with reproducible evaluation",
                "required_groups": [["diffusion model", "扩散模型"]],
                "optional_terms": ["planning agent", "规划智能体", "evaluation", "评估"],
                "excluded_terms": ["survey"],
                "recent_months": 12,
                "source_ids": ["arxiv"],
            },
            resolved_model_id=request.model_id or "fixture-model",
            usage=AgentUsage(input_tokens=100, output_tokens=40),
        )


class RelatedApiConnector:
    def __init__(self) -> None:
        self.capabilities = ConnectorCapabilities(
            connector_id="arxiv",
            display_name="arXiv fixture",
            credential_requirement="none",
            supports_exact_updated_date=False,
        )
        self.calls = 0

    async def estimate(self, query: SourceQuery) -> ConnectorEstimate:
        return ConnectorEstimate(total=1, exact=False)

    async def fetch_page(
        self,
        query: SourceQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> SourcePage:
        self.calls += 1
        assert cursor is None
        assert limit == 50
        return SourcePage(
            records=(
                SourcePaper(
                    connector_id="arxiv",
                    external_id="2608.04001v1",
                    title="Diffusion model planning agent with reproducible evaluation",
                    authors=("Verified Author",),
                    abstract="A diffusion model uses a planning agent for evaluation.",
                    doi="10.1234/related.api.1",
                    published_at=NOW,
                    updated_at=NOW,
                    landing_url="https://arxiv.org/abs/2608.04001",
                    field_provenance={
                        "title": "arxiv:title",
                        "authors": "arxiv:authors",
                        "updated_at": "arxiv:updated_at",
                    },
                ),
            ),
            next_cursor=None,
            total=1,
        )


def _paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )


def _seed_context(app: Any) -> tuple[str, str]:
    work_id = "work-related-api"
    document_id = "document-related-api"
    stored = ContentStore(app.state.paths.library_root).ingest(
        (b"%PDF-1.7\nrelated resources API fixture\n%%EOF\n",),
        media_type="application/pdf",
        max_bytes=2_048,
    )
    page_text = "Planning agents coordinate diffusion experiments with reproducible evidence."
    with app.state.sessions() as session:
        session.add(
            WorkRecord(
                id=work_id,
                canonical_title="Diffusion agents for scientific workflows",
                normalized_title="diffusion agents for scientific workflows",
                authors=["Reader Author"],
                abstract="A planning agent coordinates diffusion experiments.",
            )
        )
        session.flush()
        session.add(
            DocumentVersionRecord(
                id=document_id,
                work_id=work_id,
                source_record_id=None,
                content_sha256=stored.content_sha256,
                access_status="user_provided",
                media_type="application/pdf",
                completeness=100,
                extraction_status="complete",
                relative_path=stored.relative_path,
                byte_size=stored.byte_size,
                acquisition_kind="manual_import",
            )
        )
        session.flush()
        session.add(
            DocumentPageTextRecord(
                document_version_id=document_id,
                page_number=1,
                page_width=420,
                page_height=595,
                native_text=page_text,
                native_blocks=[],
                native_character_count=len(page_text),
                likely_scanned=False,
                ocr_state="not_needed",
                ocr_text=None,
                ocr_blocks=[],
                ocr_confidence=None,
                ocr_engine_version=None,
                ocr_model_bundle_id=None,
                extractor_version="fixture-v1",
            )
        )
        session.commit()
    return work_id, document_id


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


async def _bootstrap(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/session/bootstrap",
        json={"token": "secret"},
        headers={"Origin": "http://127.0.0.1"},
    )
    assert response.status_code == 204
    return {
        "Origin": "http://127.0.0.1",
        "X-CSRF-Token": client.cookies["paper_reader_csrf"],
    }


async def _wait_for_terminal(client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in {"succeeded", "failed", "cancelled"}:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError("Related-resource Job did not reach a terminal state")


@pytest.mark.asyncio
async def test_related_resource_api_requires_confirmation_then_runs_and_saves(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    backend = RelatedApiBackend()
    connector = RelatedApiConnector()
    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        connector_registry=ConnectorRegistry((connector,)),
        agent_registry=AgentBackendRegistry((backend,)),
    )
    work_id, document_id = _seed_context(app)
    draft_context = {
        "document_version_id": document_id,
        "scope": "current_page",
        "page_number": 1,
        "annotation_ids": [],
    }

    async with local_client(app) as client:
        initial_backend_calls = backend.calls
        initial_connector_calls = connector.calls
        unauthorized = await client.post(
            f"/api/v1/related-resources/works/{work_id}/draft",
            json={"context": draft_context},
        )
        assert unauthorized.status_code == 401
        mutation = await _bootstrap(client)
        missing_csrf = await client.post(
            f"/api/v1/related-resources/works/{work_id}/draft",
            json={"context": draft_context},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert missing_csrf.status_code == 403

        missing_context = await client.post(
            f"/api/v1/related-resources/works/{work_id}/draft",
            json={},
            headers=mutation,
        )
        assert missing_context.status_code == 422
        ambiguous_context = await client.post(
            f"/api/v1/related-resources/works/{work_id}/draft",
            json={"context_snapshot_id": "existing-snapshot", "context": draft_context},
            headers=mutation,
        )
        assert ambiguous_context.status_code == 422

        jobs_before_draft = len(JobRepository(app.state.sessions).list_jobs())
        drafted = await client.post(
            f"/api/v1/related-resources/works/{work_id}/draft",
            json={"context": draft_context},
            headers=mutation,
        )
        assert drafted.status_code == 201
        draft = drafted.json()
        assert draft["confirmed"] is False
        assert backend.calls == initial_backend_calls
        assert connector.calls == initial_connector_calls
        assert len(JobRepository(app.state.sessions).list_jobs()) == jobs_before_draft

        frozen_set = await client.get(
            f"/api/v1/related-resources/{draft['set_id']}",
        )
        assert frozen_set.status_code == 200
        legacy_draft = await client.post(
            f"/api/v1/related-resources/works/{work_id}/draft",
            json={"context_snapshot_id": frozen_set.json()["context_snapshot_id"]},
            headers=mutation,
        )
        assert legacy_draft.status_code == 201
        assert backend.calls == initial_backend_calls
        assert connector.calls == initial_connector_calls
        assert len(JobRepository(app.state.sessions).list_jobs()) == jobs_before_draft

        blocked_run = await client.post(
            f"/api/v1/related-resources/{draft['set_id']}/run",
            json={"intent_revision": draft["revision"]},
            headers={**mutation, "Idempotency-Key": "related-api-unconfirmed"},
        )
        assert blocked_run.status_code == 409
        assert backend.calls == initial_backend_calls
        assert connector.calls == initial_connector_calls

        refined = await client.post(
            f"/api/v1/related-resources/{draft['set_id']}/refine",
            json={
                "instruction": "Narrow to evaluated planning agents.",
                "agent": {
                    "backend_id": "codex_cli",
                    "model_id": "fixture-model",
                    "reasoning_effort": "medium",
                    "service_tier": "flex",
                },
            },
            headers=mutation,
        )
        assert refined.status_code == 200
        revised = refined.json()
        assert revised["revision"] == 2
        assert backend.calls == initial_backend_calls + 1
        assert connector.calls == initial_connector_calls

        confirmed = await client.put(
            f"/api/v1/related-resources/{draft['set_id']}/intent",
            json={
                "expected_revision": revised["revision"],
                "intent": revised,
                "confirm": True,
            },
            headers=mutation,
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["confirmed"] is True

        run_headers = {**mutation, "Idempotency-Key": "related-api-run"}
        started = await client.post(
            f"/api/v1/related-resources/{draft['set_id']}/run",
            json={"intent_revision": revised["revision"]},
            headers=run_headers,
        )
        assert started.status_code == 202
        replayed = await client.post(
            f"/api/v1/related-resources/{draft['set_id']}/run",
            json={"intent_revision": revised["revision"]},
            headers=run_headers,
        )
        assert replayed.status_code == 202
        assert replayed.json()["job_id"] == started.json()["job_id"]
        assert replayed.json()["replayed"] is True

        terminal = await _wait_for_terminal(client, started.json()["job_id"])
        assert terminal["status"] == "succeeded"
        detail = await client.get(f"/api/v1/related-resources/{draft['set_id']}")
        assert detail.status_code == 200
        assert detail.headers["cache-control"] == "no-store"
        payload = detail.json()
        assert payload["status"] == "succeeded"
        assert payload["coverage"] == 100
        assert len(payload["papers"]) == 1
        candidate = payload["papers"][0]
        assert candidate["field_provenance"]["title"] == "arxiv:title"

        missing_key = await client.post(
            f"/api/v1/related-resources/{draft['set_id']}/papers/{candidate['id']}/save",
            headers=mutation,
        )
        assert missing_key.status_code == 422
        saved = await client.post(
            f"/api/v1/related-resources/{draft['set_id']}/papers/{candidate['id']}/save",
            headers={**mutation, "Idempotency-Key": "related-api-save"},
        )
        assert saved.status_code == 200
        assert saved.json()["saved"] is True
        state = PreferenceRepository(app.state.sessions).get_library_state(
            candidate["linked_work_id"]
        )
        assert state.favorite is True
