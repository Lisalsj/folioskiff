from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_session_cookie_replay_is_bounded_by_server_expiry(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.security import LocalSessionStore
    from tests.foundation.test_api import local_client, make_paths

    current = [datetime(2026, 7, 15, tzinfo=UTC)]
    app = create_app(paths=make_paths(tmp_path), bootstrap_token="release-secret")
    app.state.local_sessions = LocalSessionStore(
        lifetime=timedelta(seconds=10),
        maximum_sessions=4,
        clock=lambda: current[0],
    )

    async with local_client(app) as client:
        bootstrap = await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "release-secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        first = await client.get("/api/v1/jobs")
        replay_inside_lifetime = await client.get("/api/v1/jobs")
        current[0] += timedelta(seconds=11)
        replay_after_expiry = await client.get("/api/v1/jobs")

    cookies = bootstrap.headers.get_list("set-cookie")
    assert bootstrap.status_code == 204
    assert all("Max-Age=10" in cookie for cookie in cookies)
    assert first.status_code == replay_inside_lifetime.status_code == 200
    assert replay_after_expiry.status_code == 401


def test_clean_smoke_has_no_firewall_skip_success_path() -> None:
    source = (Path(__file__).parents[2] / "packaging" / "windows" / "clean-smoke.ps1").read_text(
        encoding="utf-8"
    )

    assert "outbound_firewall_blocked = $true" in source
    assert "firewall_requires_administrator" in source
    assert "firewall_service_unavailable" in source
    assert "firewall_permission_denied" in source
    assert "firewall_skipped" not in source
    assert "diagnostic_skip" not in source


def test_persisted_agent_diagnostics_redact_prefixed_credentials_and_controls() -> None:
    from paper_reader.agents.process_policy import redact_diagnostic

    diagnostic = redact_diagnostic(
        "OLLAMA_API_KEY=ollama-secret AWS_SECRET_ACCESS_KEY=aws-secret\r\n"
        "client_secret=client-secret\u2028\x1b[31mFORGED "
        '{"api_key": "quoted secret value"}'
    )

    for secret in (
        "ollama-secret",
        "aws-secret",
        "client-secret",
        "quoted secret value",
    ):
        assert secret not in diagnostic
    for control in ("\r", "\n", "\u2028", "\x1b"):
        assert control not in diagnostic
    assert "[REDACTED]" in diagnostic


@pytest.mark.asyncio
async def test_external_resource_transport_rejects_unvalidated_peer_before_writing() -> None:
    from paper_reader.related_resources.web_policy import (
        OutboundPublicRequest,
        PinnedHttpsTransport,
        PublicUrlRejected,
    )

    class Reader:
        async def readline(self) -> bytes:
            raise AssertionError("no response should be read from an unvalidated peer")

    class Writer:
        wrote = False

        def get_extra_info(self, name: str):  # type: ignore[no-untyped-def]
            return ("127.0.0.1", 443) if name == "peername" else None

        def write(self, _value: bytes) -> None:
            self.wrote = True

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    writer = Writer()

    async def opener(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return Reader(), writer

    transport = PinnedHttpsTransport(opener=opener)
    with pytest.raises(PublicUrlRejected, match="peer"):
        await transport.send(
            OutboundPublicRequest(
                url="https://example.org/",
                method="HEAD",
                headers={},
                resolved_addresses=("93.184.216.34",),
            )
        )

    assert writer.wrote is False
