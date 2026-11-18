from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


def test_local_session_expires_and_is_removed() -> None:
    from paper_reader.security import LocalSessionStore

    sessions = LocalSessionStore(lifetime=timedelta(0))
    token = sessions.issue()

    assert sessions.contains(token) is False
    assert sessions.contains(token) is False
    assert sessions.contains(None) is False


def test_local_sessions_are_bounded_and_prune_expired_tokens() -> None:
    from paper_reader.security import LocalSessionStore

    current = [datetime(2026, 7, 15, tzinfo=UTC)]
    sessions = LocalSessionStore(
        lifetime=timedelta(seconds=10),
        maximum_sessions=2,
        clock=lambda: current[0],
    )
    first = sessions.issue()
    second = sessions.issue()
    third = sessions.issue()

    assert sessions.contains(first) is False
    assert sessions.contains(second) is True
    assert sessions.contains(third) is True
    current[0] += timedelta(seconds=11)
    assert sessions.contains(second) is False
    assert sessions.contains(third) is False
    assert sessions.lifetime_seconds == 10


def test_local_session_membership_checks_every_live_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_reader.security as security

    sessions = security.LocalSessionStore()
    first = sessions.issue()
    sessions.issue()
    sessions.issue()
    comparisons: list[tuple[str, str]] = []

    def record_comparison(candidate: str, presented: str) -> bool:
        comparisons.append((candidate, presented))
        return candidate == presented

    monkeypatch.setattr(security, "compare_digest", record_comparison)

    assert sessions.contains(first) is True
    assert len(comparisons) == 3


def test_bootstrap_tokens_are_short_lived_one_time_and_bounded() -> None:
    from paper_reader.security import BootstrapTokenStore

    now = datetime(2026, 7, 14, tzinfo=UTC)
    store = BootstrapTokenStore(
        lifetime=timedelta(seconds=30),
        clock=lambda: now,
        maximum_tokens=2,
    )
    first = store.issue(token="a" * 43)
    second = store.issue(token="b" * 43)

    assert first.token == "a" * 43
    assert first.expires_at == now + timedelta(seconds=30)
    assert store.consume("wrong") is False
    assert store.consume(first.token) is True
    assert store.consume(first.token) is False

    store.issue(token="c" * 43)
    store.issue(token="d" * 43)
    assert store.consume(second.token) is False


def test_bootstrap_token_expiry_is_enforced_without_revealing_membership() -> None:
    from paper_reader.security import BootstrapTokenStore

    current = [datetime(2026, 7, 14, tzinfo=UTC)]
    store = BootstrapTokenStore(
        lifetime=timedelta(seconds=1),
        clock=lambda: current[0],
    )
    issued = store.issue(token="e" * 43)
    current[0] += timedelta(seconds=2)

    assert store.consume(issued.token) is False
    assert store.consume("x" * 43) is False


@pytest.mark.parametrize(
    "origin",
    [
        "https://127.0.0.1",
        "http://evil.example",
        "http://evil@127.0.0.1",
        "http://127.0.0.1/path",
        "http://127.0.0.1?query=yes",
        "http://127.0.0.1#fragment",
        "http://127.0.0.1:not-a-port",
        None,
    ],
)
def test_loopback_origin_rejects_ambiguous_or_non_origin_urls(origin: str | None) -> None:
    from paper_reader.security import is_loopback_origin

    assert is_loopback_origin(origin) is False


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1",
        "http://127.0.0.1:45678",
        "http://localhost:45678",
        "http://[::1]:45678",
    ],
)
def test_loopback_origin_accepts_only_plain_http_origins(origin: str) -> None:
    from paper_reader.security import is_loopback_origin

    assert is_loopback_origin(origin) is True
