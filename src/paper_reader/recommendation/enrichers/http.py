"""HTTP normalization shared by metadata enrichers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from paper_reader.recommendation.enrichment import (
    EnricherRequestError,
    RateLimitObservation,
)


def _seconds(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.strip().casefold()
    try:
        if cleaned.endswith("ms"):
            return max(0.0, float(cleaned[:-2]) / 1000)
        if cleaned.endswith("s"):
            return max(0.0, float(cleaned[:-1]))
        return max(0.0, float(cleaned))
    except ValueError:
        return None


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    seconds = _seconds(value)
    if seconds is not None:
        return seconds
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (parsed - datetime.now(UTC)).total_seconds())


def _integer(headers: httpx.Headers, *names: str) -> int | None:
    for name in names:
        value = headers.get(name)
        if value is not None:
            try:
                return int(value)
            except ValueError:
                continue
    return None


def rate_limit_observation(response: httpx.Response) -> RateLimitObservation | None:
    headers = response.headers
    limit = _integer(headers, "X-RateLimit-Limit", "X-Rate-Limit-Limit")
    remaining = _integer(headers, "X-RateLimit-Remaining", "X-Rate-Limit-Remaining")
    reset = _seconds(headers.get("X-RateLimit-Reset"))
    interval = _seconds(headers.get("X-Rate-Limit-Interval"))
    if all(value is None for value in (limit, remaining, reset, interval)):
        return None
    return RateLimitObservation(
        limit=limit,
        remaining=remaining,
        reset_after_seconds=reset,
        interval_seconds=interval,
    )


async def request_json_object(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    rate_limit_statuses: frozenset[int] = frozenset({429}),
) -> tuple[dict[str, Any] | None, httpx.Response]:
    try:
        response = await client.get(url, params=params, headers=headers)
    except httpx.TimeoutException as error:
        raise EnricherRequestError(
            "temporary_network",
            "Metadata request timed out",
            retryable=True,
        ) from error
    except httpx.NetworkError as error:
        raise EnricherRequestError(
            "temporary_network",
            "Metadata request failed",
            retryable=True,
        ) from error

    if response.status_code == 404:
        return None, response
    if response.status_code in rate_limit_statuses:
        raise EnricherRequestError(
            "rate_limited",
            "Metadata source rate limit reached",
            retryable=True,
            retry_after_seconds=_retry_after(response),
        )
    if response.status_code in {401, 403}:
        raise EnricherRequestError(
            "authentication",
            "Metadata source authentication failed",
            retryable=False,
        )
    if response.status_code >= 500:
        raise EnricherRequestError(
            "remote_5xx",
            f"Metadata source returned HTTP {response.status_code}",
            retryable=True,
        )
    if response.status_code >= 400:
        raise EnricherRequestError(
            "permanent_request",
            f"Metadata source rejected the request with HTTP {response.status_code}",
            retryable=False,
        )
    try:
        payload = response.json()
    except ValueError as error:
        raise EnricherRequestError(
            "response_incompatible",
            "Metadata source returned invalid JSON",
            retryable=False,
        ) from error
    if not isinstance(payload, dict):
        raise EnricherRequestError(
            "response_incompatible",
            "Metadata source JSON root must be an object",
            retryable=False,
        )
    return payload, response
