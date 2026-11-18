from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentCapability,
    AgentErrorCode,
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
from paper_reader.settings import AppPaths


class ApiTranslationBackend:
    def __init__(self) -> None:
        self.capability = AgentCapability(
            backend_id="codex_cli",
            display_name="Translation API fixture",
            location=AgentLocation.LOCAL,
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
        self.fail_on_call: int | None = 2
        self.request_segment_ids: list[tuple[str, ...]] = []

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
        source = request.prompt.split("<UNTRUSTED_TRANSLATION_SOURCE_BEGIN>\n", 1)[1].split(
            "\n<UNTRUSTED_TRANSLATION_SOURCE_END>", 1
        )[0]
        segments = json.loads(source)
        ids = tuple(item["segment_id"] for item in segments)
        self.request_segment_ids.append(ids)
        if self.fail_on_call == self.calls:
            raise AgentBackendError(
                AgentErrorCode.UNAVAILABLE,
                "Fixture interruption after a committed batch",
                retryable=True,
            )
        assert cancelled() is False
        await on_fragment(AgentFragment(index=0, kind="text", text="discarded"))
        return AgentResult(
            raw_output='{"segments":[]}',
            structured_output={
                "segments": [
                    {
                        "segment_id": item["segment_id"],
                        "translated_text": "Chinese translation: " + item["protected_text"],
                        "uncertain_terms": [],
                    }
                    for item in segments
                ]
            },
            resolved_model_id=request.model_id or "fixture-model",
            usage=AgentUsage(input_tokens=100, output_tokens=50),
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
        (b"%PDF-1.7\ntranslation API fixture\n%%EOF\n",),
        media_type="application/pdf",
        max_bytes=2_048,
    )
    paragraphs = [
        f"Section {index}. " + ("Evidence remains bounded and reproducible. " * 180)
        for index in range(1, 5)
    ]
    text = "\n\n".join(paragraphs)
    with app.state.sessions() as session:
        session.add(
            WorkRecord(
                id="work-translation-api",
                canonical_title="Translation API <Safety> Fixture",
                normalized_title="translation api safety fixture",
                authors=["Alice <Researcher>"],
                abstract="A restart-safe translation fixture.",
                published_at=datetime(2026, 7, 1, tzinfo=UTC),
            )
        )
        session.flush()
        session.add(
            DocumentVersionRecord(
                id="document-translation-api",
                work_id="work-translation-api",
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
                document_version_id="document-translation-api",
                page_number=1,
                page_width=420,
                page_height=595,
                native_text=text,
                native_blocks=[],
                native_character_count=len(text),
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
    raise AssertionError("Translation Job did not reach a terminal state")


def _command() -> dict[str, Any]:
    return {
        "context": {
            "document_version_id": "document-translation-api",
            "scope": "current_page",
            "page_number": 1,
        },
        "source_language": "en",
        "target_language": "zh-CN",
        "agent": {
            "backend_id": "codex_cli",
            "model_id": "fixture-model",
            "reasoning_effort": "medium",
            "service_tier": "flex",
        },
        "user_terminology": [
            {"source_term": "Evidence", "target_term": "evidence-zh", "locked": True}
        ],
        "network_policy": "local_only",
    }


@pytest.mark.asyncio
async def test_resume_skips_committed_segments_after_restart_and_exports(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    backend = ApiTranslationBackend()
    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((backend,)),
    )
    _seed(app)

    async with local_client(app) as client:
        unauthorized = await client.get("/api/v1/translations/missing")
        assert unauthorized.status_code == 401
        mutation = await _bootstrap(client)
        missing_csrf = await client.post(
            "/api/v1/translations",
            json=_command(),
            headers={
                "Origin": "http://127.0.0.1",
                "Idempotency-Key": "translation-api-first",
            },
        )
        assert missing_csrf.status_code == 403

        created = await client.post(
            "/api/v1/translations",
            json=_command(),
            headers={**mutation, "Idempotency-Key": "translation-api-first"},
        )
        assert created.status_code == 202
        failed = await _wait_for_terminal(client, created.json()["job_id"])
        assert failed["status"] == "failed"
        artifact_id = created.json()["artifact_id"]

        detail = await client.get(f"/api/v1/translations/{artifact_id}")
        assert detail.status_code == 200
        assert detail.headers["cache-control"] == "no-store"
        assert detail.json()["status"] == "partial"
        segments = (await client.get(f"/api/v1/translations/{artifact_id}/segments")).json()
        committed = {item["segment_id"] for item in segments if item["status"] == "succeeded"}
        pending = {item["segment_id"] for item in segments if item["status"] != "succeeded"}
        assert committed
        assert pending

        blocked_export = await client.get(f"/api/v1/translations/{artifact_id}/export.md")
        assert blocked_export.status_code == 409
        draft_export = await client.get(f"/api/v1/translations/{artifact_id}/export.md?draft=true")
        assert draft_export.status_code == 200
        assert "-draft.md" in draft_export.headers["content-disposition"]

        backend.fail_on_call = None
        before_resume = len(backend.request_segment_ids)
        resumed = await client.post(
            f"/api/v1/translations/{artifact_id}/resume",
            headers={**mutation, "Idempotency-Key": "translation-resume-1"},
        )
        assert resumed.status_code == 202
        assert resumed.json()["artifact_id"] == artifact_id
        completed = await _wait_for_terminal(client, resumed.json()["job_id"])
        assert completed["status"] == "succeeded"
        resumed_ids = {
            segment_id
            for request_ids in backend.request_segment_ids[before_resume:]
            for segment_id in request_ids
        }
        assert resumed_ids == pending
        assert resumed_ids.isdisjoint(committed)

        markdown = await client.get(f"/api/v1/translations/{artifact_id}/export.md")
        html = await client.get(f"/api/v1/translations/{artifact_id}/export.html")
        printable = await client.get(f"/api/v1/translations/{artifact_id}/print")
        assert markdown.status_code == html.status_code == printable.status_code == 200
        assert "source-sha256:" in markdown.text
        assert "default-src 'none'" in html.text
        assert "window.print" not in printable.text

        completed_detail = await client.get(f"/api/v1/translations/{artifact_id}")
        user_term = next(
            item for item in completed_detail.json()["terms"] if item["source_term"] == "Evidence"
        )
        missing_term_key = await client.put(
            f"/api/v1/translations/{artifact_id}/terms/{user_term['id']}",
            json={"target_term": "evidence-zh", "locked": True},
            headers=mutation,
        )
        assert missing_term_key.status_code == 422
        replayed_term = await client.put(
            f"/api/v1/translations/{artifact_id}/terms/{user_term['id']}",
            json={"target_term": "evidence-zh", "locked": True},
            headers={**mutation, "Idempotency-Key": "translation-term-replay"},
        )
        assert replayed_term.status_code == 200
        forbidden_rewrite = await client.put(
            f"/api/v1/translations/{artifact_id}/terms/{user_term['id']}",
            json={"target_term": "changed", "locked": True},
            headers={**mutation, "Idempotency-Key": "translation-term-change"},
        )
        assert forbidden_rewrite.status_code == 409
