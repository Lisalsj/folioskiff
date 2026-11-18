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
    ModelCatalog,
)
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.settings import AppPaths


def _structured_output(valid: bool = True) -> dict[str, object]:
    if not valid:
        return {"invalid": "missing fixed report sections"}
    reference = {
        "evidence_id": "page:1:native",
        "kind": "page",
        "page_number": 1,
        "locator": None,
        "url": None,
        "external_title": None,
    }
    statement = {"text": "API evidence supports this.", "references": [reference]}
    return {
        "title": "<script>Generated report</script>",
        "overview": statement,
        "research_question": statement,
        "author_claims": [statement],
        "contributions": [statement],
        "method": [statement],
        "assumptions": [statement],
        "data_and_experiments": [statement],
        "findings": [statement],
        "limitations": [statement],
        "reproducibility": [statement],
        "relation_to_user_intent": [statement],
        "agent_judgments": [statement],
    }


class ApiBackend:
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
            supports_vision=True,
            supports_external_research=True,
        )
        self.valid = True
        self.generation_calls = 0

    async def discover_models(self) -> ModelCatalog:
        return ModelCatalog(
            models=(AgentModel(model_id="fixture-model", display_name="Fixture model"),),
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
        self.generation_calls += 1
        await on_fragment(AgentFragment(index=0, kind="text", text="fragment"))
        return AgentResult(
            raw_output='{"api":true}',
            structured_output=_structured_output(self.valid),
            resolved_model_id=request.model_id or "backend-default",
        )


def _paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )


def _seed(app: Any) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
    from paper_reader.reader.models import DocumentPageTextRecord
    from paper_reader.reader.storage import ContentStore

    store = ContentStore(app.state.paths.library_root)
    stored = store.ingest(
        (b"%PDF-1.7\nanalysis API fixture\n%%EOF\n",),
        media_type="application/pdf",
        max_bytes=1_024,
    )
    with app.state.sessions() as session:
        work = WorkRecord(
            id="work-analysis-api",
            canonical_title="Analysis API fixture",
            normalized_title="analysis api fixture",
            authors=["Alice"],
            abstract="Fallback abstract.",
            published_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        session.add(work)
        session.flush()
        document = DocumentVersionRecord(
            id="document-analysis-api",
            work_id=work.id,
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
        session.add(document)
        session.flush()
        session.add(
            DocumentPageTextRecord(
                document_version_id=document.id,
                page_number=1,
                page_width=420,
                page_height=595,
                native_text="Stable API analysis evidence.",
                native_blocks=[],
                native_character_count=29,
                likely_scanned=False,
                ocr_state="not_needed",
                ocr_text=None,
                ocr_blocks=[],
                ocr_confidence=None,
                ocr_engine_version=None,
                ocr_model_bundle_id=None,
                extractor_version="fixture",
            )
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


async def _wait_for_terminal(client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    for _ in range(100):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in {"succeeded", "failed", "cancelled"}:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError("analysis Job did not reach a terminal state")


@pytest.mark.asyncio
async def test_click_only_api_security_idempotency_report_listing_and_exports(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    backend = ApiBackend()
    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((backend,)),
    )
    _seed(app)
    selection = {
        "document_version_id": "document-analysis-api",
        "model_id": "fixture-model",
    }

    async with local_client(app) as client:
        unauthorized = await client.post(
            "/api/v1/analysis/works/work-analysis-api/preflight",
            json=selection,
        )
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        empty_reports = await client.get(
            "/api/v1/analysis/works/work-analysis-api/reports"
        )
        preflight = await client.post(
            "/api/v1/analysis/works/work-analysis-api/preflight",
            json=selection,
        )
        jobs_before = await client.get("/api/v1/jobs")
        assert backend.generation_calls == 0
        command = {
            "selection": selection,
            "preflight_fingerprint": preflight.json()["preflight_fingerprint"],
        }
        origin = {"Origin": "http://127.0.0.1"}
        mutation_headers = {
            **origin,
            "X-CSRF-Token": client.cookies["paper_reader_csrf"],
            "Idempotency-Key": "analysis-api-success",
        }
        missing_csrf = await client.post(
            "/api/v1/analysis/works/work-analysis-api/reports",
            json=command,
            headers={**origin, "Idempotency-Key": "missing-csrf"},
        )
        missing_idempotency = await client.post(
            "/api/v1/analysis/works/work-analysis-api/reports",
            json=command,
            headers={
                **origin,
                "X-CSRF-Token": client.cookies["paper_reader_csrf"],
            },
        )
        bad_fingerprint = await client.post(
            "/api/v1/analysis/works/work-analysis-api/reports",
            json={**command, "preflight_fingerprint": "0" * 64},
            headers={**mutation_headers, "Idempotency-Key": "analysis-api-stale"},
        )
        created = await client.post(
            "/api/v1/analysis/works/work-analysis-api/reports",
            json=command,
            headers=mutation_headers,
        )
        repeated = await client.post(
            "/api/v1/analysis/works/work-analysis-api/reports",
            json=command,
            headers=mutation_headers,
        )
        terminal = await _wait_for_terminal(client, created.json()["id"])
        report_id = terminal["result"]["report_id"]
        reports = await client.get(
            "/api/v1/analysis/works/work-analysis-api/reports"
        )
        report = await client.get(f"/api/v1/analysis/reports/{report_id}")
        markdown = await client.get(
            f"/api/v1/analysis/reports/{report_id}/markdown"
        )
        html = await client.get(f"/api/v1/analysis/reports/{report_id}/html")
        printable = await client.get(
            f"/api/v1/analysis/reports/{report_id}/print"
        )

    assert unauthorized.status_code == 401
    assert empty_reports.status_code == 200 and empty_reports.json() == []
    assert preflight.status_code == 200
    assert preflight.json()["ready"] is True
    assert preflight.json()["cloud_backend"] is True
    assert jobs_before.json() == []
    assert missing_csrf.status_code == 403
    assert missing_idempotency.status_code == 422
    assert bad_fingerprint.status_code == 409
    assert created.status_code == 202
    assert repeated.status_code == 202
    assert created.json()["id"] == repeated.json()["id"]
    assert terminal["status"] == "succeeded"
    assert backend.generation_calls == 1
    assert reports.status_code == 200
    assert len(reports.json()) == 1
    assert reports.json()[0]["id"] == report_id
    assert report.status_code == 200
    assert report.json()["content"]["title"] == "<script>Generated report</script>"
    assert report.headers["cache-control"] == "private, no-store"
    assert markdown.status_code == 200
    assert "text/markdown" in markdown.headers["content-type"]
    assert "&lt;script&gt;Generated report&lt;/script&gt;" in markdown.text
    assert markdown.headers["cache-control"] == "private, no-store"
    assert markdown.headers["x-content-type-options"] == "nosniff"
    assert html.status_code == 200
    assert "<script>Generated report</script>" not in html.text
    assert "<style>" in html.text
    assert html.headers["cache-control"] == "private, no-store"
    assert "default-src 'none'" in html.headers["content-security-policy"]
    assert printable.status_code == 200
    assert "@media print" in printable.text
    assert "data-print-ready=\"true\"" in printable.text
    assert printable.headers["cache-control"] == "private, no-store"
    assert "frame-ancestors 'none'" in printable.headers[
        "content-security-policy"
    ]


@pytest.mark.asyncio
async def test_invalid_output_exposes_failure_draft_without_replacing_formal_report(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    backend = ApiBackend()
    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((backend,)),
    )
    _seed(app)
    selection = {"document_version_id": "document-analysis-api"}

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        headers = {
            "Origin": "http://127.0.0.1",
            "X-CSRF-Token": client.cookies["paper_reader_csrf"],
        }

        async def create(key: str) -> dict[str, Any]:
            preflight = await client.post(
                "/api/v1/analysis/works/work-analysis-api/preflight",
                json=selection,
            )
            response = await client.post(
                "/api/v1/analysis/works/work-analysis-api/reports",
                json={
                    "selection": selection,
                    "preflight_fingerprint": preflight.json()[
                        "preflight_fingerprint"
                    ],
                },
                headers={**headers, "Idempotency-Key": key},
            )
            assert response.status_code == 202
            return await _wait_for_terminal(client, response.json()["id"])

        formal = await create("analysis-api-formal")
        backend.valid = False
        failed = await create("analysis-api-failure")
        failure = await client.get(
            f"/api/v1/analysis/jobs/{failed['id']}/failure"
        )
        reports = await client.get(
            "/api/v1/analysis/works/work-analysis-api/reports"
        )
        fragments = await client.get(
            f"/api/v1/analysis/jobs/{failed['id']}/fragments"
        )

    assert formal["status"] == "succeeded"
    assert failed["status"] == "failed"
    assert failed["result"] is None
    assert failure.status_code == 200
    assert failure.json()["error_category"] == "invalid_output"
    assert failure.json()["validation_errors"]
    assert [item["job_id"] for item in reports.json()] == [formal["id"]]
    assert fragments.status_code == 200
    assert fragments.json()[0]["sequence"] == 0
    assert backend.generation_calls == 2


@pytest.mark.asyncio
async def test_restart_resumes_only_a_previously_explicit_analysis_job(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.selections import (
        AnalysisJobCommand,
        AnalysisSelection,
    )
    from paper_reader.app import create_app

    backend = ApiBackend()
    paths = _paths(tmp_path)
    first_app = create_app(
        paths=paths,
        bootstrap_token="first-secret",
        agent_registry=AgentBackendRegistry((backend,)),
    )
    _seed(first_app)

    async with first_app.router.lifespan_context(first_app):
        assert backend.generation_calls == 0
        service = first_app.state.analysis_service
        selection = AnalysisSelection(document_version_id="document-analysis-api")
        preflight = await service.preflight("work-analysis-api", selection)
        queued = await service.create_job(
            "work-analysis-api",
            AnalysisJobCommand(
                selection=selection,
                preflight_fingerprint=preflight.preflight_fingerprint,
            ),
            idempotency_key="analysis-restart-explicit",
        )
        assert service.jobs.get(queued.id).status == "queued"
        assert backend.generation_calls == 0

    restarted_app = create_app(
        paths=paths,
        bootstrap_token="second-secret",
        agent_registry=AgentBackendRegistry((backend,)),
    )
    async with restarted_app.router.lifespan_context(restarted_app):
        for _ in range(100):
            resumed = restarted_app.state.analysis_service.jobs.get(queued.id)
            if resumed is not None and resumed.status in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("explicit analysis Job was not resumed")

    assert resumed.status == "succeeded"
    assert backend.generation_calls == 1
