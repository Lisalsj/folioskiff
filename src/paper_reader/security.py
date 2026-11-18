"""Loopback session protection for the browser API."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from secrets import compare_digest, token_urlsafe
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

SESSION_COOKIE = "paper_reader_session"
CSRF_COOKIE = "paper_reader_csrf"
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True, slots=True)
class IssuedBootstrapToken:
    token: str
    expires_at: datetime


class BootstrapTokenStore:
    """Bounded, short-lived bootstrap grants consumed exactly once."""

    def __init__(
        self,
        *,
        lifetime: timedelta = timedelta(minutes=5),
        maximum_tokens: int = 16,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if lifetime <= timedelta(0):
            raise ValueError("bootstrap lifetime must be positive")
        if not 1 <= maximum_tokens <= 64:
            raise ValueError("bootstrap token capacity is invalid")
        self._lifetime = lifetime
        self._maximum_tokens = maximum_tokens
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tokens: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def issue(self, *, token: str | None = None) -> IssuedBootstrapToken:
        value = token or token_urlsafe(32)
        if _TOKEN_PATTERN.fullmatch(value) is None:
            raise ValueError("bootstrap token format is invalid")
        with self._lock:
            now = self._clock()
            self._remove_expired(now)
            self._tokens.pop(value, None)
            while len(self._tokens) >= self._maximum_tokens:
                oldest = next(iter(self._tokens))
                self._tokens.pop(oldest, None)
            expires_at = now + self._lifetime
            self._tokens[value] = expires_at
            return IssuedBootstrapToken(token=value, expires_at=expires_at)

    def consume(self, supplied: str | None) -> bool:
        if supplied is None:
            return False
        with self._lock:
            now = self._clock()
            self._remove_expired(now)
            match: str | None = None
            for candidate in self._tokens:
                if compare_digest(candidate, supplied):
                    match = candidate
            if match is None:
                return False
            self._tokens.pop(match, None)
            return True

    @property
    def lifetime_seconds(self) -> int:
        return int(self._lifetime.total_seconds())

    def _remove_expired(self, now: datetime) -> None:
        for token, expires_at in tuple(self._tokens.items()):
            if expires_at <= now:
                self._tokens.pop(token, None)


class LocalSessionStore:
    """Bounded server-side sessions for the loopback browser client."""

    def __init__(
        self,
        *,
        lifetime: timedelta = timedelta(hours=12),
        maximum_sessions: int = 64,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if lifetime < timedelta(0):
            raise ValueError("local session lifetime cannot be negative")
        if not 1 <= maximum_sessions <= 256:
            raise ValueError("local session capacity is invalid")
        self._lifetime = lifetime
        self._maximum_sessions = maximum_sessions
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sessions: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        token = token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._remove_expired(now)
            while len(self._sessions) >= self._maximum_sessions:
                oldest = next(iter(self._sessions))
                self._sessions.pop(oldest, None)
            self._sessions[token] = now + self._lifetime
        return token

    def contains(self, token: str | None) -> bool:
        if token is None:
            return False
        with self._lock:
            self._remove_expired(self._clock())
            matched = False
            for candidate in self._sessions:
                matched = compare_digest(candidate, token) | matched
            return matched

    @property
    def lifetime_seconds(self) -> int:
        return int(self._lifetime.total_seconds())

    def _remove_expired(self, now: datetime) -> None:
        for token, expires_at in tuple(self._sessions.items()):
            if expires_at <= now:
                self._sessions.pop(token, None)


def require_local_session(request: Request) -> None:
    session_token = request.cookies.get(SESSION_COOKIE)
    if not request.app.state.local_sessions.contains(session_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Local session required",
        )


def is_loopback_origin(origin: str | None) -> bool:
    if origin is None:
        return False
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
        and (port is None or 1 <= port <= 65535)
    )


def is_loopback_address(host: str | None) -> bool:
    if host is None:
        return False
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def require_local_mutation(request: Request) -> None:
    require_local_session(request)
    csrf_cookie = request.cookies.get(CSRF_COOKIE)
    csrf_header = request.headers.get("X-CSRF-Token")
    valid_csrf = (
        csrf_cookie is not None
        and csrf_header is not None
        and compare_digest(csrf_cookie, csrf_header)
    )
    if not is_loopback_origin(request.headers.get("Origin")) or not valid_csrf:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin and CSRF proof required",
        )
