from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import inspect, text

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
from paper_reader.settings import AppPaths


class ApiConversationBackend:
    def __init__(self) -> None:
        self.capability = AgentCapability(
            backend_id="codex_cli",
            display_name="Conversation API fixture",
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
        await on_fragment(AgentFragment(index=0, kind="text", text="persisted answer"))
        await asyncio.sleep(0.02)
        return AgentResult(
            raw_output='{"answer":"API answer"}',
            structured_output={
                "answer": "The API answer is grounded in page one.",
                "citations": [
                    {
                        "evidence_id": "page:1:native",
                        "kind": "page",
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
        (b"%PDF-1.7\nconversation API fixture\n%%EOF\n",),
        media_type="application/pdf",
        max_bytes=2_048,
    )
    with app.state.sessions() as session:
        work = WorkRecord(
            id="work-conversation-api",
            canonical_title="Conversation API fixture",
            normalized_title="conversation api fixture",
            authors=["Alice"],
            abstract="Conversation API abstract.",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        alias = WorkRecord(
            id="work-conversation-api-alias",
            canonical_title="Conversation API alias",
            normalized_title="conversation api alias",
            authors=["Alice"],
            abstract="Alias.",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
            status="merged",
            merged_into_work_id=work.id,
        )
        session.add_all([work, alias])
        session.flush()
        document = DocumentVersionRecord(
            id="document-conversation-api",
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
                native_text="Stable conversation API evidence.",
                native_blocks=[],
                native_character_count=33,
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
    raise AssertionError("conversation Job did not reach a terminal state")


@pytest.mark.asyncio
async def test_legacy_conversation_routes_delegate_without_legacy_storage(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    backend = ApiConversationBackend()
    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((backend,)),
    )
    _seed(app)
    openapi_document = app.openapi()
    compatibility_operations = (
        ("/api/v1/conversations/works/{work_id}", "get"),
        ("/api/v1/conversations/works/{work_id}/replies", "post"),
        ("/api/v1/conversations/{conversation_id}", "get"),
        ("/api/v1/conversations/jobs/{job_id}/fragments", "get"),
    )
    for path, method in compatibility_operations:
        assert (
            openapi_document["paths"][path][method][
                "x-paper-reader-compatibility-adapter"
            ]
            is True
        )
    command = {
        "message": "What does the selected evidence establish?",
        "selection": {
            "document_version_id": "document-conversation-api",
            "model_id": "fixture-model",
        },
    }

    async with local_client(app) as client:
        unauthorized = await client.get(
            "/api/v1/conversations/works/work-conversation-api"
        )
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        empty = await client.get(
            "/api/v1/conversations/works/work-conversation-api"
        )
        invalid_work = await client.get(
            f"/api/v1/conversations/works/{'x' * 81}"
        )
        invalid_conversation = await client.get(
            f"/api/v1/conversations/{'x' * 81}"
        )
        invalid_fragments = await client.get(
            f"/api/v1/conversations/jobs/{'x' * 81}/fragments"
        )
        assert empty.status_code == 200
        assert empty.json() is None
        assert invalid_work.status_code == 404
        assert invalid_conversation.status_code == 404
        assert invalid_fragments.status_code == 200
        assert invalid_fragments.json() == []
        assert backend.calls == 0

        origin = {"Origin": "http://127.0.0.1"}
        missing_csrf = await client.post(
            "/api/v1/conversations/works/work-conversation-api-alias/replies",
            json=command,
            headers={**origin, "Idempotency-Key": "conversation-api"},
        )
        headers = {
            **origin,
            "X-CSRF-Token": client.cookies["paper_reader_csrf"],
            "Idempotency-Key": "conversation-api",
        }
        created = await client.post(
            "/api/v1/conversations/works/work-conversation-api-alias/replies",
            json=command,
            headers=headers,
        )
        repeated = await client.post(
            "/api/v1/conversations/works/work-conversation-api/replies",
            json=command,
            headers=headers,
        )
        assert created.status_code == 202
        assert repeated.status_code == 202
        assert repeated.json()["id"] == created.json()["id"]

        committed = await client.get(
            "/api/v1/conversations/works/work-conversation-api"
        )
        assert committed.status_code == 200
        committed_payload = committed.json()
        assert committed_payload["work_id"] == "work-conversation-api"
        assert committed_payload["messages"][0]["role"] == "user"
        assert committed_payload["messages"][0]["status"] == "committed"
        assert committed_payload["messages"][0]["job_id"] == created.json()["id"]
        assert committed_payload["messages"][0]["usage"] == {}
        assert committed_payload["messages"][0]["resolved_model_id"] is None

        terminal = await _wait_for_terminal(client, created.json()["id"])
        assert terminal["status"] == "succeeded"
        conversation = await client.get(
            "/api/v1/conversations/works/work-conversation-api-alias"
        )
        payload = conversation.json()
        assert [item["role"] for item in payload["messages"]] == ["user", "assistant"]
        assistant = payload["messages"][1]
        assert assistant["citations"][0]["evidence_id"] == "page:1:native"
        assert assistant["evidence_manifest"]["pages"][0]["evidence_id"] == (
            "page:1:native"
        )
        assert "text" not in assistant["evidence_manifest"]["pages"][0]
        assert "work" not in assistant["evidence_manifest"]
        assert assistant["usage"] == {"input_tokens": 80, "output_tokens": 20}
        assert payload["checkpoints"] == []

        by_id = await client.get(f"/api/v1/conversations/{payload['id']}")
        fragments = await client.get(
            f"/api/v1/conversations/jobs/{created.json()['id']}/fragments"
        )
        events = await client.get("/api/v1/events/poll?after_event_id=0")
        fragment_events = [
            item
            for item in events.json()
            if item["type"] == "assistant.fragment"
        ]
        assert by_id.status_code == 200
        assert fragments.json()[0]["text"] == "persisted answer"
        assert fragment_events
        resumed = await client.get(
            f"/api/v1/events/poll?after_event_id={fragment_events[-1]['event_id']}"
        )
        assert all(
            item["event_id"] > fragment_events[-1]["event_id"]
            for item in resumed.json()
        )
        assert unauthorized.status_code == 401
        assert missing_csrf.status_code == 403
        assert conversation.headers["cache-control"] == "private, no-store"
        assert backend.calls == 1

    with app.state.sessions() as session:
        database = inspect(session.connection())
        assert not database.has_table("paper_conversations")
        assert not database.has_table("conversation_messages")
        assert not database.has_table("conversation_fragments")
        assert not database.has_table("conversation_checkpoints")
        assert session.execute(text("SELECT COUNT(*) FROM assistant_sessions")).scalar_one() == 1
