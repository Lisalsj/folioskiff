from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

NOW = datetime(2026, 7, 14, 9, 30, tzinfo=UTC)
LATEST_ENDPOINT = "https://api.github.com/repos/Lisalsj/folioskiff/releases/latest"


def github_release(
    version: str = "0.8.0",
    *,
    tag: str | None = None,
    draft: bool = False,
    prerelease: bool = False,
    html_url: str | None = None,
    manifest_version: str | None = None,
    manifest: bool = True,
    signed: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_tag = tag or f"v{version}"
    asset_names = [
        f"paper-reader-{version}-windows-x64.exe",
        f"paper-reader-{version}-windows-x64.zip",
        "release-manifest.json",
        "SHA256SUMS.txt",
    ]
    summary = {
        "schema_version": "paper-reader-update-v1",
        "version": manifest_version or version,
        "changelog": "新增只读更新检查。\n修复离线状态提示。",
        "migration_notes": "升级前建议创建便携备份；数据库将在启动时自动迁移。",
        "capability_matrix": {
            "windows_x64": "supported",
            "installer": "supported",
            "portable": "supported",
        },
        "required_assets": asset_names,
        "signed": signed,
    }
    body = (
        "Release notes for people.\n\n"
        "<!-- paper-reader-update-manifest:v1\n"
        f"{json.dumps(summary, ensure_ascii=False, separators=(',', ':'))}\n"
        "-->"
        if manifest
        else "Release notes without an update manifest."
    )
    payload: dict[str, Any] = {
        "tag_name": resolved_tag,
        "name": f"paper-reader {version}",
        "draft": draft,
        "prerelease": prerelease,
        "published_at": "2026-07-14T08:00:00Z",
        "html_url": html_url
        or f"https://github.com/Lisalsj/folioskiff/releases/tag/{resolved_tag}",
        "body": body,
        "assets": [
            {
                "name": name,
                "size": index + 100,
                "digest": f"sha256:{str(index + 1) * 64}"[:71],
                "browser_download_url": (
                    "https://github.com/Lisalsj/folioskiff/releases/download/"
                    f"{resolved_tag}/{name}"
                ),
            }
            for index, name in enumerate(asset_names)
        ],
    }
    payload.update(extra or {})
    return payload


def database(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine

    engine = create_sqlite_engine(tmp_path / "updates.sqlite3")
    Base.metadata.create_all(engine)
    return engine, create_session_factory(engine)


def service_for(
    tmp_path: Path,
    handler: Any,
    *,
    current_version: str = "0.6.0",
    clock: Any = lambda: NOW,
):  # type: ignore[no-untyped-def]
    from paper_reader.updates.service import UpdateService

    engine, sessions = database(tmp_path)
    service = UpdateService(
        sessions=sessions,
        current_version=current_version,
        transport=httpx.MockTransport(handler),
        clock=clock,
    )
    return engine, service


@pytest.mark.asyncio
async def test_release_check_uses_only_fixed_unauthenticated_github_endpoint(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=github_release(extra={"unknown_future_field": True}))

    engine, service = service_for(tmp_path, handler)
    result = await service.check(manual=True)

    assert result.status == "update_available"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == LATEST_ENDPOINT
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
    assert request.headers["User-Agent"] == "paper-reader/0.6.0"
    assert request.headers["Accept-Encoding"] == "identity"
    assert "authorization" not in request.headers
    assert "cookie" not in request.headers
    assert request.content == b""
    assert result.release is not None
    assert result.release.version == "0.8.0"
    assert result.release.changelog.startswith("新增只读更新检查")
    assert result.release.unsigned is True
    assert {item.name for item in result.release.assets} >= {"release-manifest.json"}
    await service.aclose()
    engine.dispose()


@pytest.mark.asyncio
async def test_public_endpoint_cookies_are_never_echoed_on_later_checks(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert "cookie" not in request.headers
        return httpx.Response(
            200,
            json=github_release(),
            headers={"Set-Cookie": "tracking=not-accepted; Secure; HttpOnly"},
        )

    engine, service = service_for(tmp_path, handler)
    await service.check(manual=True)
    await service.check(manual=True)
    assert calls == 2
    await service.aclose()
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current", "remote", "payload_changes", "expected"),
    [
        ("0.8.0", "0.8.0", {}, "up_to_date"),
        ("0.8.0", "0.8.1", {}, "update_available"),
        ("0.8.0", "0.7.9", {}, "server_older"),
        ("0.8.0", "0.9.0-beta.1", {"prerelease": True}, "prerelease"),
        ("0.8.0", "0.9.0", {"draft": True}, "draft"),
    ],
)
async def test_release_versions_have_deterministic_states(
    tmp_path: Path,
    current: str,
    remote: str,
    payload_changes: dict[str, Any],
    expected: str,
) -> None:
    engine, service = service_for(
        tmp_path,
        lambda _request: httpx.Response(
            200,
            json=github_release(remote, **payload_changes),
        ),
        current_version=current,
    )
    result = await service.check(manual=True)
    assert result.status == expected
    assert result.release is not None
    await service.aclose()
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_factory", "expected"),
    [
        (
            lambda request: httpx.Response(
                403,
                headers={"X-RateLimit-Remaining": "0"},
                json={"message": "rate limit"},
                request=request,
            ),
            "rate_limited",
        ),
        (
            lambda request: httpx.Response(
                502,
                json={"message": "upstream unavailable"},
                request=request,
            ),
            "service_unavailable",
        ),
        (
            lambda request: httpx.Response(
                200,
                content=b"{broken",
                headers={"Content-Type": "application/json"},
                request=request,
            ),
            "invalid_response",
        ),
    ],
)
async def test_remote_failures_have_stable_non_throwing_states(
    tmp_path: Path,
    response_factory: Any,
    expected: str,
) -> None:
    engine, service = service_for(tmp_path, response_factory)
    result = await service.check(manual=True)
    assert result.status == expected
    assert result.release is None
    assert result.last_checked_at == NOW
    await service.aclose()
    engine.dispose()


@pytest.mark.asyncio
async def test_offline_is_reported_without_leaking_transport_details(tmp_path: Path) -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("C:/Users/private/papers/network-token", request=request)

    engine, service = service_for(tmp_path, offline)
    result = await service.check(manual=True)
    assert result.status == "offline"
    assert result.error_code == "offline"
    assert "private" not in json.dumps(result.model_dump(mode="json"))
    await service.aclose()
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        github_release(html_url="https://evil.example/releases/tag/v0.8.0"),
        github_release(manifest_version="9.9.9"),
        github_release(manifest=False),
        github_release(tag="v0.8.0+unexpected", version="0.8.0"),
    ],
)
async def test_untrusted_or_inconsistent_release_metadata_is_never_updatable(
    tmp_path: Path,
    payload: dict[str, Any],
) -> None:
    engine, service = service_for(
        tmp_path,
        lambda _request: httpx.Response(200, json=payload),
    )
    result = await service.check(manual=True)
    assert result.status == "invalid_response"
    assert result.release is None
    await service.aclose()
    engine.dispose()


@pytest.mark.asyncio
async def test_cross_host_asset_and_redirect_are_rejected_without_following(
    tmp_path: Path,
) -> None:
    payload = github_release()
    payload["assets"][0]["browser_download_url"] = "https://evil.example/payload.exe"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=payload, request=request)
        raise AssertionError("release check followed or downloaded another URL")

    engine, service = service_for(tmp_path, handler)
    result = await service.check(manual=True)
    assert result.status == "invalid_response"
    assert calls == 1
    await service.aclose()
    engine.dispose()

    redirect_calls = 0

    def redirect(request: httpx.Request) -> httpx.Response:
        nonlocal redirect_calls
        redirect_calls += 1
        return httpx.Response(302, headers={"Location": "https://evil.example"}, request=request)

    engine, service = service_for(tmp_path / "redirect", redirect)
    result = await service.check(manual=True)
    assert result.status == "invalid_response"
    assert redirect_calls == 1
    await service.aclose()
    engine.dispose()


@pytest.mark.asyncio
async def test_oversized_response_is_rejected_before_json_parsing(tmp_path: Path) -> None:
    oversized = b"{" + b" " * (512 * 1024 + 1) + b"}"
    engine, service = service_for(
        tmp_path,
        lambda request: httpx.Response(
            200,
            content=oversized,
            headers={"Content-Type": "application/json"},
            request=request,
        ),
    )
    result = await service.check(manual=True)
    assert result.status == "invalid_response"
    assert result.error_code == "response_too_large"
    await service.aclose()
    engine.dispose()


@pytest.mark.asyncio
async def test_only_explicit_checks_fetch_release_metadata_even_after_a_day(
    tmp_path: Path,
) -> None:
    current = NOW
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=github_release())

    engine, service = service_for(tmp_path, handler, clock=lambda: current)
    first = await service.check(manual=False)
    assert first.checked is False
    assert first.status == "never_checked"
    assert calls == 0
    manual = await service.check(manual=True)
    assert manual.checked is True
    assert manual.automatic_checks_enabled is False
    assert manual.next_automatic_check_at is None
    assert calls == 1

    current += timedelta(days=2)
    cached = await service.check(manual=False)
    assert cached.checked is False
    assert cached.last_checked_at == NOW
    assert cached.release == manual.release
    assert calls == 1

    cleared = service.clear_cache()
    assert cleared.last_checked_at is None
    assert cleared.release is None
    assert cleared.status == "never_checked"
    await service.aclose()
    engine.dispose()


@pytest.mark.asyncio
async def test_cached_version_relationship_is_recomputed_after_application_upgrade(
    tmp_path: Path,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=github_release())

    engine, service = service_for(tmp_path, handler, current_version="0.6.0")
    assert (await service.check(manual=True)).status == "update_available"
    await service.aclose()
    engine.dispose()

    engine, upgraded = service_for(tmp_path, handler, current_version="0.8.0")
    snapshot = upgraded.snapshot()
    assert snapshot.status == "up_to_date"
    assert snapshot.release is not None
    assert snapshot.release.version == "0.8.0"
    await upgraded.aclose()
    engine.dispose()


def test_strict_semver_rejects_non_semver_and_orders_prerelease_identifiers() -> None:
    from paper_reader.updates.service import SemanticVersion

    assert SemanticVersion.parse("1.0.0-alpha.2") < SemanticVersion.parse("1.0.0-alpha.10")
    assert SemanticVersion.parse("1.0.0-rc.1") < SemanticVersion.parse("1.0.0")
    assert SemanticVersion.parse("v1.2.3+build.5") == SemanticVersion.parse("1.2.3+other")
    for invalid in ("1.2", "01.2.3", "1.2.3.4", "1.2.3-01", "latest", ""):
        with pytest.raises(ValueError):
            SemanticVersion.parse(invalid)
