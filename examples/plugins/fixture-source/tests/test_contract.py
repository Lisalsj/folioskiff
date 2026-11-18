from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib import metadata
from io import BytesIO
from uuid import uuid4

import pytest
from paper_reader_fixture_source import plugin

from paper_reader.plugins.discovery import discover_entry_points
from paper_reader.plugins.sdk import JsonlCodec, PluginRequest, PluginResponse
from paper_reader.plugins.worker import run_application_once


def request(method: str, payload: dict[str, object]) -> PluginRequest:
    return PluginRequest(
        protocol_version="1.0",
        request_id=str(uuid4()),
        capability="fixture.source",
        method=method,
        payload=payload,
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )


async def invoke(plugin_request: PluginRequest) -> PluginResponse:
    codec = JsonlCodec()
    output = BytesIO()
    exit_code = await run_application_once(
        plugin,
        input_stream=BytesIO(codec.encode(plugin_request)),
        output_stream=output,
    )
    assert exit_code == 0
    frames = [
        codec.decode_frame(line)
        for line in output.getvalue().splitlines(keepends=True)
    ]
    terminal = frames[-1]
    assert isinstance(terminal, PluginResponse)
    return terminal


def test_independent_distribution_is_discovered_by_entry_point_metadata() -> None:
    entry_points = [
        entry
        for entry in metadata.entry_points(group="paper_reader.plugins")
        if entry.name == "fixture.source"
    ]

    discovered = discover_entry_points(entry_points)

    assert discovered.issues == ()
    assert len(discovered.entries) == 1
    assert discovered.entries[0].plugin_id == "fixture.source"
    assert discovered.entries[0].entry_point_value == "paper_reader_fixture_source:plugin"
    assert discovered.entries[0].distribution_name == "paper-reader-fixture-source"
    assert discovered.entries[0].distribution_version == "1.0.0"
    assert plugin.manifest.requested_permissions() == ()


@pytest.mark.asyncio
async def test_fixture_source_filters_months_and_preserves_opaque_pagination() -> None:
    query = {
        "months": ["2026-06"],
        "keywords": {"required_groups": [["agent"]]},
        "filters": {},
        "fingerprint": "f" * 64,
    }

    estimate = await invoke(request("estimate", {"query": query}))
    first = await invoke(
        request("fetch_page", {"query": query, "cursor": None, "limit": 2})
    )
    second = await invoke(
        request("fetch_page", {"query": query, "cursor": "offset:2", "limit": 2})
    )

    assert estimate.ok is True
    assert estimate.result == {"total": 3, "exact": True, "warnings": []}
    assert first.result is not None
    assert [record["external_id"] for record in first.result["records"]] == [
        "fixture-002",
        "fixture-003",
    ]
    assert first.result["next_cursor"] == "offset:2"
    assert second.result is not None
    assert [record["external_id"] for record in second.result["records"]] == [
        "fixture-004"
    ]
    assert second.result["next_cursor"] is None


@pytest.mark.asyncio
async def test_fixture_source_reports_transient_error_and_worker_enforces_protocol() -> None:
    temporary = await invoke(
        request(
            "estimate",
            {
                "query": {
                    "months": ["2026-06"],
                    "filters": {"simulate": "temporary_error"},
                }
            },
        )
    )
    unsupported = request("estimate", {"query": {"months": ["2026-06"]}}).model_copy(
        update={"protocol_version": "9.0"}
    )
    protocol = await invoke(unsupported)

    assert temporary.ok is False
    assert temporary.error is not None
    assert temporary.error.code == "temporary_fixture_error"
    assert temporary.error.retryable is True
    assert protocol.ok is False
    assert protocol.error is not None
    assert protocol.error.code == "protocol_mismatch"
