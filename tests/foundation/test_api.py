from __future__ import annotations

import mimetypes
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from paper_reader import __version__
from paper_reader.settings import AppPaths


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1",
        ) as client,
    ):
        yield client


@pytest.mark.asyncio
async def test_application_lifespan_disposes_database_engine(tmp_path: Path) -> None:
    from paper_reader.app import create_app

    app = create_app(paths=make_paths(tmp_path), bootstrap_token="one-time-secret")

    with patch.object(app.state.engine, "dispose", wraps=app.state.engine.dispose) as dispose:
        async with app.router.lifespan_context(app):
            pass

    dispose.assert_called_once_with()


@pytest.mark.asyncio
async def test_health_endpoint_identifies_local_application(tmp_path: Path) -> None:
    from paper_reader.app import create_app
    from paper_reader.migrations.versions import FROZEN_DATABASE_HEAD
    from paper_reader.openapi import (
        API_CONTRACT_VERSION,
        OPENAPI_CONTRACT_SHA256,
        openapi_contract_sha256,
    )
    from paper_reader.plugins.contracts import PLUGIN_PROTOCOL_VERSION

    app = create_app(paths=make_paths(tmp_path), bootstrap_token="one-time-secret")

    async with local_client(app) as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "name": "paper-reader",
        "version": __version__,
        "status": "ok",
        "api_version": API_CONTRACT_VERSION,
        "openapi_sha256": OPENAPI_CONTRACT_SHA256,
        "database_schema_revision": FROZEN_DATABASE_HEAD,
        "plugin_protocol_version": PLUGIN_PROTOCOL_VERSION,
    }
    assert response.json()["openapi_sha256"] == openapi_contract_sha256(app.openapi())


@pytest.mark.asyncio
async def test_jobs_route_requires_local_session(tmp_path: Path) -> None:
    from paper_reader.app import create_app

    app = create_app(paths=make_paths(tmp_path), bootstrap_token="one-time-secret")

    async with local_client(app) as client:
        response = await client.get("/api/v1/jobs")

    assert response.status_code == 401
    assert response.json()["detail"] == "Local session required"


@pytest.mark.asyncio
async def test_retrieval_capability_explains_optional_semantic_degradation(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    app = create_app(paths=make_paths(tmp_path), bootstrap_token="one-time-secret")

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "one-time-secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        response = await client.get("/api/v1/settings/retrieval")

    assert response.status_code == 200
    payload = response.json()
    assert payload["lexical_enabled"] is True
    assert payload["semantic_enabled"] is False
    assert payload["model_state"] == "missing"
    assert "embedding_model_not_installed" in payload["degradation_reasons"]
    assert payload["model"] == {
        "model_id": "intfloat/multilingual-e5-small",
        "revision": "0e60b8d9d2166d80387f86e3b48ec9ced55f4d15",
        "dimension": 384,
        "download_size_bytes": 492421556,
        "license_id": "MIT",
        "source_url": (
            "https://huggingface.co/intfloat/multilingual-e5-small/tree/"
            "0e60b8d9d2166d80387f86e3b48ec9ced55f4d15"
        ),
    }
    assert app.state.semantic_index is None


@pytest.mark.asyncio
async def test_bootstrap_token_creates_http_only_local_session(tmp_path: Path) -> None:
    from paper_reader.app import create_app

    app = create_app(paths=make_paths(tmp_path), bootstrap_token="one-time-secret")

    async with local_client(app) as client:
        bootstrap = await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "one-time-secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        jobs = await client.get("/api/v1/jobs")

    cookies = bootstrap.headers.get_list("set-cookie")
    session_cookie = next(
        cookie for cookie in cookies if cookie.startswith("paper_reader_session=")
    )
    csrf_cookie = next(cookie for cookie in cookies if cookie.startswith("paper_reader_csrf="))
    assert bootstrap.status_code == 204
    assert "httponly" in session_cookie.lower()
    assert "samesite=strict" in session_cookie.lower()
    assert "Path=/api/v1" in session_cookie
    assert "Max-Age=43200" in session_cookie
    assert "httponly" not in csrf_cookie.lower()
    assert "Path=/" in csrf_cookie
    assert "Max-Age=43200" in csrf_cookie
    assert jobs.status_code == 200


@pytest.mark.asyncio
async def test_bootstrap_rejects_foreign_web_origin(tmp_path: Path) -> None:
    from paper_reader.app import create_app

    app = create_app(paths=make_paths(tmp_path), bootstrap_token="one-time-secret")

    async with local_client(app) as client:
        response = await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "one-time-secret"},
            headers={"Origin": "https://example.com"},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bootstrap_token_can_only_be_used_once(tmp_path: Path) -> None:
    from paper_reader.app import create_app

    app = create_app(paths=make_paths(tmp_path), bootstrap_token="one-time-secret")
    request = {
        "json": {"token": "one-time-secret"},
        "headers": {"Origin": "http://127.0.0.1"},
    }

    async with local_client(app) as client:
        first = await client.post("/api/v1/session/bootstrap", **request)
        second = await client.post("/api/v1/session/bootstrap", **request)

    assert first.status_code == 204
    assert second.status_code == 401


@pytest.mark.asyncio
async def test_instance_activation_issues_a_short_lived_one_time_bootstrap(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    activation_secret = "a" * 43
    app = create_app(
        paths=make_paths(tmp_path),
        bootstrap_token="initial-secret",
        activation_secret=activation_secret,
    )

    async with local_client(app) as client:
        activation = await client.post(
            "/api/v1/session/activate",
            json={"activation_secret": activation_secret},
        )
        payload = activation.json()
        first = await client.post(
            "/api/v1/session/bootstrap",
            json={"token": payload["bootstrap_token"]},
            headers={"Origin": "http://127.0.0.1"},
        )
        replay = await client.post(
            "/api/v1/session/bootstrap",
            json={"token": payload["bootstrap_token"]},
            headers={"Origin": "http://127.0.0.1"},
        )

    assert activation.status_code == 200
    assert activation.headers["cache-control"] == "no-store"
    assert set(payload) == {"bootstrap_token", "expires_in_seconds", "pid"}
    assert len(payload["bootstrap_token"]) == 43
    assert payload["expires_in_seconds"] == 300
    assert payload["pid"] == os.getpid()
    assert activation_secret not in activation.text
    assert first.status_code == 204
    assert replay.status_code == 401


@pytest.mark.asyncio
async def test_instance_activation_rejects_untrusted_callers_and_payloads(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    activation_secret = "a" * 43
    app = create_app(
        paths=make_paths(tmp_path),
        bootstrap_token="initial-secret",
        activation_secret=activation_secret,
    )

    async with local_client(app) as client:
        wrong = await client.post(
            "/api/v1/session/activate",
            json={"activation_secret": "b" * 43},
        )
        foreign_origin = await client.post(
            "/api/v1/session/activate",
            json={"activation_secret": activation_secret},
            headers={"Origin": "https://example.com"},
        )
        oversized = await client.post(
            "/api/v1/session/activate",
            json={"activation_secret": "a" * 129},
        )

        async def oversized_chunks() -> AsyncIterator[bytes]:
            yield b'{"activation_secret":"'
            yield b"a" * 2048
            yield b'"}'

        oversized_chunked = await client.post(
            "/api/v1/session/activate",
            content=oversized_chunks(),
            headers={"Content-Type": "application/json"},
        )

    transport = httpx.ASGITransport(app=app, client=("192.0.2.44", 52000))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as remote,
    ):
        non_loopback = await remote.post(
            "/api/v1/session/activate",
            json={"activation_secret": activation_secret},
        )

    assert wrong.status_code == 401
    assert foreign_origin.status_code == 403
    assert non_loopback.status_code == 403
    assert oversized.status_code == 413
    assert oversized_chunked.status_code == 413
    for response in (wrong, foreign_origin, non_loopback, oversized, oversized_chunked):
        assert activation_secret not in response.text


@pytest.mark.asyncio
async def test_instance_activation_is_absent_without_a_launcher_secret(tmp_path: Path) -> None:
    from paper_reader.app import create_app

    app = create_app(paths=make_paths(tmp_path), bootstrap_token="initial-secret")

    async with local_client(app) as client:
        response = await client.post(
            "/api/v1/session/activate",
            json={"activation_secret": "a" * 43},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_jobs_route_lists_persisted_jobs(tmp_path: Path) -> None:
    from paper_reader.app import create_app
    from paper_reader.jobs.repository import JobRepository

    app = create_app(paths=make_paths(tmp_path), bootstrap_token="one-time-secret")
    created = JobRepository(app.state.sessions).create(
        kind="discovery.run",
        payload={"plan_id": "plan-1"},
        idempotency_key="run-plan-1",
    )

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "one-time-secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        response = await client.get("/api/v1/jobs")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": created.id,
            "kind": "discovery.run",
            "status": "queued",
            "progress": 0,
            "cancel_requested": False,
            "pause_requested": False,
        }
    ]


@pytest.mark.asyncio
async def test_job_cancellation_requires_origin_and_csrf_proof(tmp_path: Path) -> None:
    from paper_reader.app import create_app
    from paper_reader.jobs.repository import JobRepository

    app = create_app(paths=make_paths(tmp_path), bootstrap_token="one-time-secret")
    created = JobRepository(app.state.sessions).create(
        kind="discovery.run",
        payload={"plan_id": "plan-1"},
    )

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "one-time-secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        csrf_token = client.cookies["paper_reader_csrf"]
        missing_csrf = await client.post(
            f"/api/v1/jobs/{created.id}/cancel",
            headers={"Origin": "http://127.0.0.1"},
        )
        foreign_origin = await client.post(
            f"/api/v1/jobs/{created.id}/cancel",
            headers={"Origin": "https://example.com", "X-CSRF-Token": csrf_token},
        )
        accepted = await client.post(
            f"/api/v1/jobs/{created.id}/cancel",
            headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf_token},
        )

    assert missing_csrf.status_code == 403
    assert foreign_origin.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["cancel_requested"] is True


@pytest.mark.asyncio
async def test_application_serves_spa_and_preserves_api_404(tmp_path: Path) -> None:
    from paper_reader.app import create_app

    static_dir = tmp_path / "static"
    assets_dir = static_dir / "assets"
    assets_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text(
        "<!doctype html><title>paper-reader</title><div id='app'></div>",
        encoding="utf-8",
    )
    (assets_dir / "app.js").write_text("export const ready = true", encoding="utf-8")
    app = create_app(
        paths=make_paths(tmp_path),
        bootstrap_token="one-time-secret",
        static_dir=static_dir,
    )

    async with local_client(app) as client:
        root = await client.get("/")
        client_route = await client.get("/plans")
        asset = await client.get("/assets/app.js")
        unknown_api = await client.get("/api/v1/does-not-exist")

    assert root.status_code == 200
    assert root.text == client_route.text
    assert "paper-reader" in root.text
    assert asset.text == "export const ready = true"
    assert unknown_api.status_code == 404
    assert unknown_api.headers["content-type"].startswith("application/json")


@pytest.mark.asyncio
async def test_application_serves_mjs_assets_as_javascript_on_windows(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    previous_type = mimetypes.guess_type("worker.mjs")[0]
    mimetypes.add_type("text/plain", ".mjs", strict=True)
    try:
        static_dir = tmp_path / "static"
        assets_dir = static_dir / "assets"
        assets_dir.mkdir(parents=True)
        (static_dir / "index.html").write_text("<!doctype html>", encoding="utf-8")
        (assets_dir / "worker.mjs").write_text("export const ready = true", encoding="utf-8")
        app = create_app(
            paths=make_paths(tmp_path),
            bootstrap_token="one-time-secret",
            static_dir=static_dir,
        )

        async with local_client(app) as client:
            worker = await client.get("/assets/worker.mjs")

        assert worker.status_code == 200
        assert worker.headers["content-type"].startswith("text/javascript")
    finally:
        if previous_type is not None:
            mimetypes.add_type(previous_type, ".mjs", strict=True)


@pytest.mark.asyncio
async def test_event_polling_resumes_after_last_persisted_event(tmp_path: Path) -> None:
    from paper_reader.app import create_app
    from paper_reader.jobs.repository import JobRepository

    app = create_app(paths=make_paths(tmp_path), bootstrap_token="one-time-secret")
    repository = JobRepository(app.state.sessions)
    job = repository.create(kind="discovery.run", payload={})
    first = repository.append_event(
        job_id=job.id,
        event_type="job.started",
        progress=0,
        payload={"message": "started"},
    )

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "one-time-secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        initial = await client.get("/api/v1/events/poll")
        resumed = await client.get(
            "/api/v1/events/poll",
            params={"after_event_id": first.event_id},
        )

    assert initial.status_code == 200
    assert initial.json()[0] == {
        "event_id": first.event_id,
        "job_id": job.id,
        "sequence": 1,
        "type": "job.started",
        "timestamp": initial.json()[0]["timestamp"],
        "progress": 0,
        "payload": {"message": "started"},
    }
    assert resumed.json() == []
