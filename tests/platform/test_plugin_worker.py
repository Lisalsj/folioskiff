from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any
from uuid import uuid4

import pytest

from paper_reader.plugins.contracts import PluginManifest
from paper_reader.plugins.sdk import (
    JsonlCodec,
    PluginApplication,
    PluginContext,
    PluginEvent,
    PluginOperationError,
    PluginRequest,
    PluginResponse,
)
from paper_reader.plugins.worker import run_application_once


def manifest() -> PluginManifest:
    return PluginManifest.model_validate(
        {
            "manifest_version": "1",
            "plugin_id": "fixture.worker",
            "display_name": "Fixture Worker",
            "distribution_name": "paper-reader-fixture-worker",
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
                    "capability_id": "fixture.worker-source",
                    "kind": "source_connector",
                    "methods": ["probe", "estimate", "fetch_page"],
                    "permissions": [],
                    "timeout_seconds": 30,
                }
            ],
        }
    )


def request(
    *,
    capability: str = "fixture.worker-source",
    method: str = "estimate",
    protocol_version: str = "1.0",
    deadline: datetime | None = None,
    payload: dict[str, Any] | None = None,
) -> PluginRequest:
    return PluginRequest(
        protocol_version=protocol_version,
        request_id=str(uuid4()),
        capability=capability,
        method=method,
        payload=(payload if payload is not None else {"query": {"months": ["2026-07"]}}),
        deadline=deadline or datetime.now(UTC) + timedelta(minutes=1),
    )


async def execute(
    application: PluginApplication,
    plugin_request: PluginRequest,
    *,
    now: datetime | None = None,
) -> tuple[int, tuple[PluginEvent | PluginResponse, ...]]:
    codec = JsonlCodec()
    output = BytesIO()
    exit_code = await run_application_once(
        application,
        input_stream=BytesIO(codec.encode(plugin_request)),
        output_stream=output,
        clock=lambda: now or datetime.now(UTC),
    )
    frames = tuple(
        codec.decode_frame(line)
        for line in output.getvalue().splitlines(keepends=True)
    )
    return exit_code, frames


@pytest.mark.asyncio
async def test_worker_reserved_manifest_probe_never_calls_plugin_handler() -> None:
    calls: list[str] = []

    async def estimate(payload: dict[str, Any], context: PluginContext) -> dict[str, Any]:
        calls.append(context.request.method)
        return {"total": 1, "exact": True, "warnings": []}

    application = PluginApplication(
        manifest=manifest(),
        handlers={("fixture.worker-source", "estimate"): estimate},
    )
    plugin_request = request(capability="plugin.manifest", method="probe", payload={})

    exit_code, frames = await execute(application, plugin_request)

    assert exit_code == 0
    assert calls == []
    assert len(frames) == 1
    terminal = frames[0]
    assert isinstance(terminal, PluginResponse)
    assert terminal.ok is True
    assert terminal.result == {"manifest": manifest().model_dump(mode="json")}


@pytest.mark.asyncio
async def test_worker_dispatches_declared_handler_and_streams_contiguous_events() -> None:
    async def estimate(payload: dict[str, Any], context: PluginContext) -> dict[str, Any]:
        await context.emit("progress", {"completed": 1, "total": 1})
        assert payload == {"query": {"months": ["2026-07"]}}
        return {"total": 3, "exact": True, "warnings": []}

    application = PluginApplication(
        manifest=manifest(),
        handlers={("fixture.worker-source", "estimate"): estimate},
    )
    plugin_request = request()

    exit_code, frames = await execute(application, plugin_request)

    assert exit_code == 0
    assert len(frames) == 2
    assert isinstance(frames[0], PluginEvent)
    assert frames[0].sequence == 1
    assert frames[0].event == "progress"
    assert isinstance(frames[1], PluginResponse)
    assert frames[1].sequence == 2
    assert frames[1].ok is True
    assert frames[1].result == {"total": 3, "exact": True, "warnings": []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plugin_request", "error_code"),
    [
        (
            request(deadline=datetime(2026, 7, 14, 7, tzinfo=UTC)),
            "deadline_exceeded",
        ),
        (request(protocol_version="9.0"), "protocol_mismatch"),
        (request(method="fetch_page"), "handler_unavailable"),
        (request(capability="fixture.unknown"), "unknown_capability"),
    ],
)
async def test_worker_rejects_expired_protocol_and_undeclared_dispatch_before_handler(
    plugin_request: PluginRequest,
    error_code: str,
) -> None:
    calls: list[str] = []

    async def estimate(payload: dict[str, Any], context: PluginContext) -> dict[str, Any]:
        calls.append(context.request.method)
        return {"total": 1, "exact": True, "warnings": []}

    application = PluginApplication(
        manifest=manifest(),
        handlers={("fixture.worker-source", "estimate"): estimate},
    )

    exit_code, frames = await execute(
        application,
        plugin_request,
        now=datetime(2026, 7, 14, 8, tzinfo=UTC),
    )

    assert exit_code == 0
    assert calls == []
    assert len(frames) == 1
    terminal = frames[0]
    assert isinstance(terminal, PluginResponse)
    assert terminal.ok is False
    assert terminal.error is not None
    assert terminal.error.code == error_code


@pytest.mark.asyncio
async def test_worker_preserves_safe_transient_errors_but_redacts_unhandled_exceptions() -> None:
    private_canary = "C:\\Users\\reader\\private-api-key.txt"

    async def temporary(payload: dict[str, Any], context: PluginContext) -> dict[str, Any]:
        raise PluginOperationError(
            "temporary_fixture_error",
            "Fixture source is temporarily unavailable",
            retryable=True,
        )

    async def exploded(payload: dict[str, Any], context: PluginContext) -> dict[str, Any]:
        raise RuntimeError(private_canary)

    transient_application = PluginApplication(
        manifest=manifest(),
        handlers={("fixture.worker-source", "estimate"): temporary},
    )
    exploded_application = PluginApplication(
        manifest=manifest(),
        handlers={("fixture.worker-source", "estimate"): exploded},
    )

    _, transient_frames = await execute(transient_application, request())
    _, exploded_frames = await execute(exploded_application, request())

    transient = transient_frames[0]
    exploded_result = exploded_frames[0]
    assert isinstance(transient, PluginResponse)
    assert transient.error is not None
    assert transient.error.code == "temporary_fixture_error"
    assert transient.error.retryable is True
    assert isinstance(exploded_result, PluginResponse)
    assert exploded_result.error is not None
    assert exploded_result.error.code == "plugin_failure"
    assert private_canary not in exploded_result.model_dump_json()


def test_application_rejects_handlers_not_declared_by_manifest() -> None:
    async def handler(payload: dict[str, Any], context: PluginContext) -> dict[str, Any]:
        return {}

    with pytest.raises(ValueError, match="declared"):
        PluginApplication(
            manifest=manifest(),
            handlers={("fixture.worker-source", "delete_everything"): handler},
        )
