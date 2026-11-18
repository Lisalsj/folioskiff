"""HTTP error normalization and polite request pacing for built-in connectors."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from paper_reader import __version__
from paper_reader.discovery.connectors import ConnectorRequestError

SOURCE_USER_AGENT = f"paper-reader/{__version__} (local research application)"


class RequestPacer:
    def __init__(self, minimum_interval_seconds: float = 0) -> None:
        if minimum_interval_seconds < 0:
            raise ValueError("minimum_interval_seconds cannot be negative")
        self._minimum_interval = minimum_interval_seconds
        self._last_request_at: float | None = None
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            if self._last_request_at is not None:
                remaining = self._minimum_interval - (now - self._last_request_at)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_request_at = loop.time()


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed - datetime.now(UTC)).total_seconds())


def checked_response(response: httpx.Response) -> httpx.Response:
    status = response.status_code
    if status < 400:
        return response
    if status in {401, 403}:
        raise ConnectorRequestError(
            "authentication",
            "Source authentication failed",
            retryable=False,
        )
    if status == 429:
        raise ConnectorRequestError(
            "rate_limited",
            "Source rate limit reached",
            retryable=True,
            retry_after_seconds=_retry_after(response),
        )
    if status >= 500:
        raise ConnectorRequestError(
            "remote_5xx",
            f"Source returned HTTP {status}",
            retryable=True,
        )
    raise ConnectorRequestError(
        "permanent_request",
        f"Source rejected the request with HTTP {status}",
        retryable=False,
    )


async def get_response(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    try:
        response = await client.get(url, params=params, headers=headers)
    except httpx.TimeoutException as error:
        raise ConnectorRequestError(
            "temporary_network",
            "Source request timed out",
            retryable=True,
        ) from error
    except httpx.NetworkError as error:
        raise ConnectorRequestError(
            "temporary_network",
            "Source network request failed",
            retryable=True,
        ) from error
    return checked_response(response)


async def post_json_response(
    client: httpx.AsyncClient,
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    try:
        response = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as error:
        raise ConnectorRequestError(
            "temporary_network",
            "Source request timed out",
            retryable=True,
        ) from error
    except httpx.NetworkError as error:
        raise ConnectorRequestError(
            "temporary_network",
            "Source network request failed",
            retryable=True,
        ) from error
    return checked_response(response)


def json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as error:
        raise ConnectorRequestError(
            "response_incompatible",
            "Source returned invalid JSON",
            retryable=False,
        ) from error
    if not isinstance(payload, dict):
        raise ConnectorRequestError(
            "response_incompatible",
            "Source JSON root must be an object",
            retryable=False,
        )
    return payload
