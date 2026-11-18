from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from paper_reader import __version__
from tests.packaging.test_update_service import github_release
from tests.reader.test_extraction_api import bootstrap, local_client

FUTURE_RELEASE_VERSION = "999.0.0"


def update_app(
    tmp_path: Path,
    handler,
    *,
    automatic_update_checks: bool = False,
):  # type: ignore[no-untyped-def]
    from paper_reader.app import create_app
    from paper_reader.settings import AppPaths

    return create_app(
        paths=AppPaths.from_overrides(
            state_root=tmp_path / "state",
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "library",
        ),
        bootstrap_token="secret",
        update_transport=httpx.MockTransport(handler),
        automatic_update_checks=automatic_update_checks,
    )


@pytest.mark.asyncio
async def test_update_api_requires_local_session_and_csrf_for_commands(tmp_path: Path) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=github_release(FUTURE_RELEASE_VERSION))

    app = update_app(tmp_path, handler)
    async with local_client(app) as client:
        assert (await client.get("/api/v1/settings/updates")).status_code == 401
        headers = await bootstrap(client)

        initial = await client.get("/api/v1/settings/updates")
        assert initial.status_code == 200
        assert initial.json()["status"] == "never_checked"
        assert initial.json()["current_version"] == __version__
        assert calls == 0

        assert (await client.post("/api/v1/settings/updates/check")).status_code == 403
        checked = await client.post(
            "/api/v1/settings/updates/check",
            headers=headers,
        )
        assert checked.status_code == 200
        body = checked.json()
        assert body["status"] == "update_available"
        assert body["release"]["html_url"].startswith(
            "https://github.com/Lisalsj/folioskiff/releases/tag/"
        )
        assert "browser_download_url" not in checked.text
        assert calls == 1

        disabled = await client.patch(
            "/api/v1/settings/updates",
            json={"automatic_checks_enabled": False},
            headers=headers,
        )
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"

        cleared = await client.delete(
            "/api/v1/settings/updates/cache",
            headers=headers,
        )
        assert cleared.status_code == 200
        assert cleared.json()["release"] is None
        assert cleared.json()["last_checked_at"] is None


@pytest.mark.asyncio
async def test_update_api_rejects_unknown_settings_fields_and_never_exposes_raw_errors(
    tmp_path: Path,
) -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("D:/private/library/secret.pdf", request=request)

    app = update_app(tmp_path, offline)
    async with local_client(app) as client:
        headers = await bootstrap(client)
        invalid = await client.patch(
            "/api/v1/settings/updates",
            json={"automatic_checks_enabled": True, "release_channel": "evil"},
            headers=headers,
        )
        assert invalid.status_code == 422

        checked = await client.post("/api/v1/settings/updates/check", headers=headers)
        assert checked.status_code == 200
        assert checked.json()["status"] == "offline"
        assert "secret.pdf" not in checked.text


@pytest.mark.asyncio
async def test_open_application_runs_one_due_automatic_check(tmp_path: Path) -> None:
    called = asyncio.Event()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        called.set()
        return httpx.Response(200, json=github_release(FUTURE_RELEASE_VERSION))

    app = update_app(tmp_path, handler, automatic_update_checks=True)
    async with local_client(app) as client:
        await asyncio.wait_for(called.wait(), timeout=2)
        headers = await bootstrap(client)
        status = await client.get("/api/v1/settings/updates", headers=headers)
        assert status.status_code == 200
        assert status.json()["status"] == "update_available"
        assert calls == 1


def test_update_routes_are_present_in_openapi_without_asset_download_commands(
    tmp_path: Path,
) -> None:
    app = update_app(tmp_path, lambda _request: httpx.Response(500))
    paths = app.openapi()["paths"]
    assert set(paths["/api/v1/settings/updates"]) == {"get", "patch"}
    assert set(paths["/api/v1/settings/updates/check"]) == {"post"}
    assert set(paths["/api/v1/settings/updates/cache"]) == {"delete"}
    assert not any(
        "download" in path for path in paths if path.startswith("/api/v1/settings/updates")
    )
