from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.settings import AppPaths


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


@pytest.mark.asyncio
async def test_keyword_workbench_requires_confirmation_before_compilation(tmp_path: Path) -> None:
    from paper_reader.app import create_app

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    app = create_app(paths=paths, bootstrap_token="secret")
    payload = {
        "research_intent": "研究扩散模型在癌症影像中的应用",
        "definition": {
            "required": [
                {
                    "key": "method",
                    "label": "扩散模型",
                    "terms": ["diffusion model", "扩散模型"],
                }
            ],
            "boost": [],
            "excluded": [],
        },
    }

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        csrf = client.cookies["paper_reader_csrf"]
        mutation_headers = {
            "Origin": "http://127.0.0.1",
            "X-CSRF-Token": csrf,
        }
        created = await client.post(
            "/api/v1/keywords/versions",
            json=payload,
            headers=mutation_headers,
        )
        version_id = created.json()["id"]
        rejected = await client.get(f"/api/v1/keywords/versions/{version_id}/compiled")
        confirmed = await client.post(
            f"/api/v1/keywords/versions/{version_id}/confirm",
            headers=mutation_headers,
        )
        compiled = await client.get(f"/api/v1/keywords/versions/{version_id}/compiled")

    assert created.status_code == 201
    assert created.json()["confirmed_at"] is None
    assert rejected.status_code == 409
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmed_at"] is not None
    assert compiled.status_code == 200
    assert compiled.json()["boolean_expression"] == '("diffusion model" OR "扩散模型")'


@pytest.mark.asyncio
async def test_keyword_suggestion_endpoint_uses_safe_local_fallback(tmp_path: Path) -> None:
    from paper_reader.app import create_app

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    app = create_app(paths=paths, bootstrap_token="secret")

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        csrf = client.cookies["paper_reader_csrf"]
        response = await client.post(
            "/api/v1/keywords/suggestions",
            json={
                "research_intent": "diffusion model for cancer imaging",
                "mode": "manual",
                "target_count": 18,
            },
            headers={
                "Origin": "http://127.0.0.1",
                "X-CSRF-Token": csrf,
            },
        )

    body = response.json()
    term_count = sum(
        len(group["terms"])
        for section in ("required", "boost", "excluded")
        for group in body["definition"][section]
    )
    assert response.status_code == 201
    assert 12 <= term_count <= 24
    assert body["confirmed_at"] is None
    assert body["generator_info"]["mode"] == "local_fallback"


@pytest.mark.asyncio
async def test_keyword_suggestion_endpoint_directs_unknown_intent_to_manual_mode(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    app = create_app(paths=paths, bootstrap_token="secret")

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        csrf = client.cookies["paper_reader_csrf"]
        response = await client.post(
            "/api/v1/keywords/suggestions",
            json={"research_intent": "一个完全未知的新方向甲乙丙", "mode": "manual"},
            headers={
                "Origin": "http://127.0.0.1",
                "X-CSRF-Token": csrf,
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "manual_configuration_required"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected_status"),
    [("timeout", 504), ("output_limit", 502), ("invalid_output", 502), ("unavailable", 503)],
)
async def test_keyword_failures_preserve_category_and_actionable_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    expected_status: int,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.discovery.agent_backend import AgentKeywordSuggestionBackend
    from paper_reader.discovery.suggestions import KeywordSuggestionGenerationError

    async def fail(self: Any, context: Any) -> Any:
        raise KeywordSuggestionGenerationError(
            code,
            "模型未能完成答案，请降低推理强度后重试。",
            backend_id="ollama_local",
            requested_model_id="fixture-model",
            diagnostic="private response or credential",
        )

    monkeypatch.setattr(AgentKeywordSuggestionBackend, "suggest", fail)
    app = create_app(
        paths=AppPaths.from_overrides(
            state_root=tmp_path / "state",
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "library",
        ),
        bootstrap_token="secret",
    )
    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        response = await client.post(
            "/api/v1/keywords/suggestions",
            json={"research_intent": "robot", "agent_backend_id": "ollama_local"},
            headers={
                "Origin": "http://127.0.0.1",
                "X-CSRF-Token": client.cookies["paper_reader_csrf"],
            },
        )
    assert response.status_code == expected_status
    assert response.json()["detail"] == {
        "code": code,
        "message": "模型未能完成答案，请降低推理强度后重试。",
        "backend_id": "ollama_local",
        "requested_model_id": "fixture-model",
    }
    assert "private" not in response.text
