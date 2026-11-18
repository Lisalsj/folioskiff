"""Deterministic, offline SourceConnector fixture for SDK conformance tests."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from paper_reader.plugins.contracts import PluginManifest
from paper_reader.plugins.sdk import PluginApplication, PluginContext, PluginOperationError

_MANIFEST = PluginManifest.model_validate(
    {
        "manifest_version": "1",
        "plugin_id": "fixture.source",
        "display_name": "Offline fixture source",
        "distribution_name": "paper-reader-fixture-source",
        "distribution_version": "1.0.0",
        "protocol": {"minimum": "1.0", "maximum": "1.0"},
        "license": "Apache-2.0",
        "config_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "capabilities": [
            {
                "capability_id": "fixture.source",
                "kind": "source_connector",
                "methods": ["probe", "estimate", "fetch_page"],
                "permissions": [],
                "timeout_seconds": 15,
            }
        ],
    }
)


def _papers() -> list[dict[str, Any]]:
    resource = files(__package__).joinpath("papers.json")
    value = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(value, list):  # pragma: no cover - packaged fixture is fixed
        raise RuntimeError("Fixture paper data must be a list")
    return value


def _query(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("query")
    if not isinstance(value, dict):
        raise PluginOperationError("invalid_query", "Fixture query must be an object")
    filters = value.get("filters", {})
    if isinstance(filters, dict) and filters.get("simulate") == "temporary_error":
        raise PluginOperationError(
            "temporary_fixture_error",
            "The fixture source is temporarily unavailable",
            retryable=True,
        )
    return value


def _matching(payload: dict[str, Any]) -> list[dict[str, Any]]:
    query = _query(payload)
    months = query.get("months")
    if not isinstance(months, list) or not all(isinstance(month, str) for month in months):
        raise PluginOperationError("invalid_query", "Fixture months must be a string list")
    selected = set(months)
    return [paper for paper in _papers() if str(paper["updated_at"])[:7] in selected]


async def probe(payload: dict[str, Any], context: PluginContext) -> dict[str, Any]:
    return {
        "available": True,
        "source": "packaged_fixture",
        "protocol_version": context.request.protocol_version,
    }


async def estimate(payload: dict[str, Any], context: PluginContext) -> dict[str, Any]:
    papers = _matching(payload)
    await context.emit("progress", {"completed": 1, "total": 1})
    return {"total": len(papers), "exact": True, "warnings": []}


async def fetch_page(payload: dict[str, Any], context: PluginContext) -> dict[str, Any]:
    papers = _matching(payload)
    limit = payload.get("limit")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise PluginOperationError("invalid_limit", "Fixture page limit must be between 1 and 50")
    cursor = payload.get("cursor")
    if cursor is None:
        offset = 0
    elif isinstance(cursor, str) and cursor.startswith("offset:"):
        try:
            offset = int(cursor.removeprefix("offset:"))
        except ValueError as error:
            raise PluginOperationError("invalid_cursor", "Fixture cursor is invalid") from error
        if offset < 0:
            raise PluginOperationError("invalid_cursor", "Fixture cursor is invalid")
    else:
        raise PluginOperationError("invalid_cursor", "Fixture cursor is invalid")
    page = papers[offset : offset + limit]
    next_offset = offset + len(page)
    next_cursor = f"offset:{next_offset}" if next_offset < len(papers) else None
    await context.emit(
        "progress",
        {"completed": next_offset, "total": len(papers)},
    )
    return {
        "records": page,
        "next_cursor": next_cursor,
        "total": len(papers),
    }


plugin = PluginApplication(
    manifest=_MANIFEST,
    handlers={
        ("fixture.source", "probe"): probe,
        ("fixture.source", "estimate"): estimate,
        ("fixture.source", "fetch_page"): fetch_page,
    },
)

__all__ = ["plugin"]
