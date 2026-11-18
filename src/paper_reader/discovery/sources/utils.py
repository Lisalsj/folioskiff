"""Query and date helpers shared by built-in connectors."""

from __future__ import annotations

import calendar
from collections.abc import Callable
from datetime import UTC, datetime

from paper_reader.discovery.keywords import CompiledKeywordQuery


def month_bounds(months: tuple[str, ...]) -> tuple[str, str]:
    if not months:
        raise ValueError("at least one month is required")
    start_year, start_month = (int(item) for item in months[0].split("-"))
    end_year, end_month = (int(item) for item in months[-1].split("-"))
    end_day = calendar.monthrange(end_year, end_month)[1]
    return (
        f"{start_year:04d}-{start_month:02d}-01",
        f"{end_year:04d}-{end_month:02d}-{end_day:02d}",
    )


def boolean_query(
    compiled: CompiledKeywordQuery,
    *,
    format_term: Callable[[str], str],
    exclude_operator: str = "AND NOT",
) -> str:
    required = [
        f"({' OR '.join(format_term(term) for term in group)})"
        for group in compiled.required_groups
    ]
    expression = " AND ".join(required)
    for group in compiled.excluded_groups:
        expression += f" {exclude_operator} ({' OR '.join(format_term(term) for term in group)})"
    return expression


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None
