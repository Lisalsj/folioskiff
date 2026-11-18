"""Child-process-only JSONL runtime for an isolated plugin application."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import re
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import ModuleType
from typing import Any, BinaryIO, cast
from uuid import uuid4

from pydantic import ValidationError

from paper_reader.plugins.broker import BrokerCall, BrokerReply
from paper_reader.plugins.contracts import PLUGIN_PROTOCOL_VERSION
from paper_reader.plugins.sdk import (
    BrokerOperation,
    JsonlCodec,
    PluginApplication,
    PluginContext,
    PluginError,
    PluginEvent,
    PluginOperationError,
    PluginResponse,
    ProtocolError,
)

_ENTRY_POINT_VALUE = re.compile(
    r"^(?P<module>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)"
    r":(?P<attribute>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)$"
)


def resolve_entry_point(
    value: str,
    *,
    importer: Callable[[str], ModuleType] = importlib.import_module,
) -> object:
    """Resolve validated plugin code only after the host has created a sandbox."""

    matched = _ENTRY_POINT_VALUE.fullmatch(value)
    if matched is None:
        raise ValueError("Plugin entry point must use safe module:attribute syntax")
    target: object = importer(matched.group("module"))
    for attribute in matched.group("attribute").split("."):
        try:
            target = getattr(target, attribute)
        except AttributeError as error:
            raise ValueError("Plugin entry point attribute is unavailable") from error
    return target


async def run_application_once(
    application: PluginApplication,
    *,
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Handle exactly one request and produce one correlated terminal response."""

    codec = JsonlCodec()
    request_line = input_stream.readline(codec.max_line_bytes + 1)
    try:
        request = codec.decode_request(request_line)
    except ProtocolError:
        return 2
    sequence = [1]

    def write_frame(frame: PluginEvent | PluginResponse) -> None:
        output_stream.write(codec.encode(frame))
        output_stream.flush()

    async def emit(event: str, payload: dict[str, Any]) -> None:
        frame = PluginEvent(
            protocol_version=request.protocol_version,
            request_id=request.request_id,
            sequence=sequence[0],
            event=event,
            payload=payload,
        )
        write_frame(frame)
        sequence[0] += 1

    async def broker(
        operation: BrokerOperation,
        resource: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        call = BrokerCall(
            broker_request_id=str(uuid4()),
            operation=operation,
            resource=resource,
            payload=payload,
        )
        await emit("broker.request", call.model_dump(mode="json"))
        reply_line = input_stream.readline(codec.max_line_bytes + 1)
        try:
            reply = BrokerReply.model_validate(codec.decode_object(reply_line))
        except (ProtocolError, ValidationError) as error:
            raise PluginOperationError(
                "broker_protocol",
                "The host broker returned an invalid response",
            ) from error
        if (
            reply.protocol_version != request.protocol_version
            or reply.request_id != request.request_id
            or reply.broker_request_id != call.broker_request_id
        ):
            raise PluginOperationError(
                "broker_protocol",
                "The host broker response did not match this request",
            )
        if not reply.ok:
            if reply.error is None:  # pragma: no cover - schema enforces this
                raise PluginOperationError("broker_protocol", "The host broker response is invalid")
            raise PluginOperationError(
                reply.error.code,
                reply.error.message,
                retryable=reply.error.retryable,
                details=reply.error.details,
            )
        if reply.result is None:  # pragma: no cover - schema enforces this
            raise PluginOperationError("broker_protocol", "The host broker response is invalid")
        return reply.result

    def terminal(*, result: dict[str, Any] | None = None, error: PluginError | None = None) -> None:
        write_frame(
            PluginResponse(
                protocol_version=request.protocol_version,
                request_id=request.request_id,
                sequence=sequence[0],
                ok=error is None,
                result=result,
                error=error,
            )
        )

    try:
        now = clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise RuntimeError("Worker clock must be timezone-aware")
        request.ensure_not_expired(now=now)
        if request.protocol_version != PLUGIN_PROTOCOL_VERSION:
            raise PluginOperationError(
                "protocol_mismatch",
                "The plugin does not support this host protocol version",
            )
        if request.capability == "plugin.manifest":
            if request.method != "probe" or request.payload:
                raise PluginOperationError(
                    "invalid_manifest_probe",
                    "The reserved manifest probe is invalid",
                )
            terminal(result={"manifest": application.manifest.model_dump(mode="json")})
            return 0
        declared = next(
            (
                capability
                for capability in application.manifest.capabilities
                if capability.capability_id == request.capability
            ),
            None,
        )
        if declared is None:
            raise PluginOperationError(
                "unknown_capability",
                "The requested capability is not declared",
            )
        if request.method not in declared.methods:
            raise PluginOperationError(
                "unknown_method",
                "The requested method is not declared",
            )
        handler = application.handler(request.capability, request.method)
        if handler is None:
            raise PluginOperationError(
                "handler_unavailable",
                "The declared plugin handler is unavailable",
            )
        context = PluginContext(request, emit=emit, broker=broker)
        outcome = handler(dict(request.payload), context)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        if not isinstance(outcome, dict):
            if not isinstance(outcome, Mapping):
                raise PluginOperationError(
                    "invalid_result",
                    "Plugin handlers must return a JSON object",
                )
            outcome = dict(outcome)
        terminal(result=cast(dict[str, Any], outcome))
        return 0
    except ProtocolError as error:
        terminal(
            error=PluginError(
                code=error.code,
                message=str(error),
                retryable=error.code == "deadline_exceeded",
            )
        )
        return 0
    except PluginOperationError as error:
        terminal(error=error.error)
        return 0
    except Exception:
        terminal(
            error=PluginError(
                code="plugin_failure",
                message="The isolated plugin handler failed",
                retryable=False,
            )
        )
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paper-reader-plugin-worker")
    parser.add_argument("--entry-point", required=True)
    args = parser.parse_args(argv)
    resolved = resolve_entry_point(args.entry_point)
    if not isinstance(resolved, PluginApplication):
        return 2
    return asyncio.run(
        run_application_once(
            resolved,
            input_stream=cast(BinaryIO, sys.stdin.buffer),
            output_stream=cast(BinaryIO, sys.stdout.buffer),
        )
    )


if __name__ == "__main__":  # pragma: no cover - exercised by isolated integration tests
    raise SystemExit(main())
