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


class ApiAssistantBackend:
    def __init__(self) -> None:
        self.capability = AgentCapability(
            backend_id="codex_cli",
            display_name="Assistant API fixture",
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
        self.fail = False
        self.release = asyncio.Event()

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
        await self.release.wait()
        if self.fail:
            raise AgentBackendError(
                AgentErrorCode.UNAVAILABLE,
                "Assistant API fixture unavailable",
                retryable=True,
            )
        if cancelled():
            raise AgentBackendError(AgentErrorCode.CANCELLED, "Cancelled")
        await on_fragment(AgentFragment(index=0, kind="text", text="persisted fragment"))
        return AgentResult(
            raw_output='{"answer":"API answer"}',
            structured_output={
                "answer": "The selected page introduces a stable method.",
                "citations": [
                    {
                        "evidence_id": "page:1:native",
                        "kind": "page",
                        "page_number": 1,
                    }
                ],
                "uncertainties": [],
                "follow_up_questions": [],
            },
            resolved_model_id=request.model_id or "fixture-model",
            usage=AgentUsage(input_tokens=80, output_tokens=20),
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
        (b"%PDF-1.7\nassistant API fixture\n%%EOF\n",),
        media_type="application/pdf",
        max_bytes=2_048,
    )
    with app.state.sessions() as session:
        work = WorkRecord(
            id="work-assistant-api",
            canonical_title="Assistant API fixture",
            normalized_title="assistant api fixture",
            authors=["Alice"],
            abstract="Assistant API abstract.",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        alias = WorkRecord(
            id="work-assistant-api-alias",
            canonical_title="Assistant API alias",
            normalized_title="assistant api alias",
            authors=["Alice"],
            abstract="Alias.",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
            status="merged",
            merged_into_work_id=work.id,
        )
        session.add_all([work, alias])
        session.flush()
        session.add(
            DocumentVersionRecord(
                id="document-assistant-api",
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
        )
        session.flush()
        text = "1 Introduction\nThe paper introduces a stable method."
        session.add(
            DocumentPageTextRecord(
                document_version_id="document-assistant-api",
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


def _command(message: str = "What does the selected evidence establish?") -> dict[str, Any]:
    return {
        "mode": "ask",
        "message": message,
        "context": {
            "document_version_id": "document-assistant-api",
            "scope": "current_page",
            "page_number": 1,
        },
        "agent": {
            "backend_id": "codex_cli",
            "model_id": "fixture-model",
            "reasoning_effort": "medium",
            "service_tier": "flex",
        },
        "network_policy": "local_only",
        "output_language": "zh-CN",
    }


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
    for _ in range(100):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in {"succeeded", "failed", "cancelled"}:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError("Assistant Job did not reach a terminal state")


def _assert_private(response: httpx.Response) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"


@pytest.mark.asyncio
async def test_assistant_api_security_idempotency_history_and_fragments(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    backend = ApiAssistantBackend()
    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((backend,)),
    )
    _seed(app)

    async with local_client(app) as client:
        unauthorized = await client.get("/api/v1/assistant/works/work-assistant-api")
        assert unauthorized.status_code == 401
        mutation = await _bootstrap(client)

        empty = await client.get("/api/v1/assistant/works/work-assistant-api-alias")
        assert empty.status_code == 200
        _assert_private(empty)
        assert empty.json() == {
            "work_id": "work-assistant-api",
            "session_id": None,
            "tasks": [],
            "messages": [],
            "latest_checkpoint": None,
        }

        missing_csrf = await client.post(
            "/api/v1/assistant/works/work-assistant-api/tasks",
            json=_command(),
            headers={
                "Origin": "http://127.0.0.1",
                "Idempotency-Key": "assistant-api-first",
            },
        )
        assert missing_csrf.status_code == 403
        missing_key = await client.post(
            "/api/v1/assistant/works/work-assistant-api/tasks",
            json=_command(),
            headers=mutation,
        )
        assert missing_key.status_code == 422

        headers = {**mutation, "Idempotency-Key": "assistant-api-first"}
        created = await client.post(
            "/api/v1/assistant/works/work-assistant-api-alias/tasks",
            json=_command(),
            headers=headers,
        )
        repeated = await client.post(
            "/api/v1/assistant/works/work-assistant-api/tasks",
            json=_command(),
            headers=headers,
        )
        assert created.status_code == 202
        assert repeated.status_code == 202
        _assert_private(created)
        assert repeated.json()["id"] == created.json()["id"]
        assert backend.calls <= 1

        work = await client.get("/api/v1/assistant/works/work-assistant-api")
        payload = work.json()
        assert payload["work_id"] == "work-assistant-api"
        assert payload["messages"][0]["role"] == "user"
        assert payload["messages"][0]["job_id"] == created.json()["id"]
        assert payload["tasks"][0]["context"]["scope"] == "current_page"
        assert payload["tasks"][0]["context"]["document_content_sha256"]
        assert "evidence" not in payload["tasks"][0]["context"]

        backend.release.set()
        terminal = await _wait_for_terminal(client, created.json()["id"])
        assert terminal["status"] == "succeeded"
        task_id = created.json()["task_id"]
        task = await client.get(f"/api/v1/assistant/tasks/{task_id}")
        assert task.status_code == 200
        _assert_private(task)
        task_payload = task.json()
        assert task_payload["status"] == "succeeded"
        assert task_payload["latest_run"]["resolved_model_id"] == "fixture-model"
        assert task_payload["messages"][-1]["citations"][0]["evidence_id"] == ("page:1:native")
        assert task_payload["retryable"] is False

        fragments = await client.get(
            f"/api/v1/assistant/tasks/{task_id}/fragments?after_sequence=-1"
        )
        assert fragments.status_code == 200
        _assert_private(fragments)
        assert fragments.json()[0]["text"] == "persisted fragment"
        after = await client.get(f"/api/v1/assistant/tasks/{task_id}/fragments?after_sequence=0")
        assert after.json() == []


@pytest.mark.asyncio
async def test_failed_assistant_task_can_retry_the_same_frozen_snapshot(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    backend = ApiAssistantBackend()
    backend.release.set()
    backend.fail = True
    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((backend,)),
    )
    _seed(app)

    async with local_client(app) as client:
        mutation = await _bootstrap(client)
        created = await client.post(
            "/api/v1/assistant/works/work-assistant-api/tasks",
            json=_command(),
            headers={**mutation, "Idempotency-Key": "assistant-api-failed"},
        )
        failed = await _wait_for_terminal(client, created.json()["id"])
        assert failed["status"] == "failed"
        task_id = created.json()["task_id"]
        failed_task = await client.get(f"/api/v1/assistant/tasks/{task_id}")
        assert failed_task.json()["retryable"] is True

        backend.fail = False
        retried = await client.post(
            f"/api/v1/assistant/tasks/{task_id}/retry",
            headers={**mutation, "Idempotency-Key": "assistant-api-retry"},
        )
        assert retried.status_code == 202
        assert retried.json()["task_id"] != task_id
        retry_terminal = await _wait_for_terminal(client, retried.json()["id"])
        assert retry_terminal["status"] == "succeeded"
        retry_task = await client.get(f"/api/v1/assistant/tasks/{retried.json()['task_id']}")
        assert retry_task.json()["retry_of_task_id"] == task_id
        assert (
            retry_task.json()["context"]["snapshot_sha256"]
            == (failed_task.json()["context"]["snapshot_sha256"])
        )
