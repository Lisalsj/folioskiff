"""Bounded UTF-8 JSON Lines protocol primitives shared with plugin authors."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DEFAULT_MAX_LINE_BYTES = 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 16 * 1024 * 1024
PLUGIN_PROTOCOL_CONTRACT_SHA256 = "520cdcc36b7e5bcede23a9b6ae891ec9e7896ca41020f628fae76b757973bae5"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,15}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_PROTOCOL_VERSION = re.compile(r"^[0-9]{1,4}\.[0-9]{1,4}$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


class ProtocolError(RuntimeError):
    """A stable protocol violation that is safe to surface without worker output."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _CorrelatedFrame(_ProtocolModel):
    protocol_version: str
    request_id: str

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol_version(cls, value: str) -> str:
        if not _PROTOCOL_VERSION.fullmatch(value):
            raise ValueError("protocol_version must use major.minor integers")
        return value

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        try:
            normalized = str(UUID(value))
        except ValueError as error:
            raise ValueError("request_id must be a canonical UUID") from error
        if normalized != value:
            raise ValueError("request_id must be a canonical UUID")
        return normalized


class PluginRequest(_CorrelatedFrame):
    capability: str = Field(min_length=3, max_length=80)
    method: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any]
    deadline: datetime

    @field_validator("capability", "method")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValueError("Plugin capability and method must use stable identifiers")
        return normalized

    @field_validator("deadline")
    @classmethod
    def validate_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Plugin request deadline must include a timezone")
        return value.astimezone(UTC)

    def ensure_not_expired(self, *, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Deadline comparison time must include a timezone")
        if self.deadline <= now.astimezone(UTC):
            raise ProtocolError("deadline_exceeded", "Plugin request deadline has expired")


class PluginError(_ProtocolModel):
    code: str = Field(min_length=2, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False
    details: dict[str, Any] | None = None

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        if not _ERROR_CODE.fullmatch(value):
            raise ValueError("Plugin error code must be a stable identifier")
        return value

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if _CONTROL_CHARACTER.search(value):
            raise ValueError("Plugin error message cannot contain control characters")
        normalized = value.strip()
        if not normalized:
            raise ValueError("Plugin error message cannot be blank")
        return normalized


class PluginEvent(_CorrelatedFrame):
    type: Literal["event"] = "event"
    sequence: int = Field(ge=1)
    event: str = Field(min_length=2, max_length=80)
    payload: dict[str, Any]

    @field_validator("event")
    @classmethod
    def validate_event(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValueError("Plugin event must use a stable identifier")
        return normalized


class PluginResponse(_CorrelatedFrame):
    type: Literal["response"] = "response"
    sequence: int = Field(ge=1)
    ok: bool
    result: dict[str, Any] | None = None
    error: PluginError | None = None

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> Self:
        if self.ok and (self.result is None or self.error is not None):
            raise ValueError("Successful plugin response requires result and forbids error")
        if not self.ok and (self.error is None or self.result is not None):
            raise ValueError("Failed plugin response requires error and forbids result")
        return self


def plugin_protocol_contract_sha256() -> str:
    """Hash the frozen 1.x manifest, permission, method, and envelope schemas."""

    from paper_reader.plugins.contracts import (
        PLUGIN_CAPABILITY_METHODS,
        PLUGIN_PROTOCOL_VERSION,
        PluginManifest,
    )

    contract = {
        "protocol_version": PLUGIN_PROTOCOL_VERSION,
        "capability_methods": {
            kind.value: sorted(methods) for kind, methods in PLUGIN_CAPABILITY_METHODS.items()
        },
        "manifest": PluginManifest.model_json_schema(),
        "request": PluginRequest.model_json_schema(),
        "event": PluginEvent.model_json_schema(),
        "response": PluginResponse.model_json_schema(),
    }
    encoded = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


ProtocolFrame = PluginEvent | PluginResponse


class PluginOperationError(RuntimeError):
    """A bounded business error a plugin may return without counting as a crash."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        validated = PluginError(
            code=code,
            message=message,
            retryable=retryable,
            details=details,
        )
        super().__init__(validated.message)
        self.error = validated


EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]
BrokerOperation = Literal["network.fetch", "file.read", "credential.read"]
BrokerRequester = Callable[
    [BrokerOperation, str, dict[str, Any]],
    Awaitable[dict[str, Any]],
]


class PluginContext:
    """The complete runtime surface available to a plugin handler."""

    __slots__ = ("_broker", "_emit", "request")

    def __init__(
        self,
        request: PluginRequest,
        *,
        emit: EventEmitter,
        broker: BrokerRequester | None = None,
    ) -> None:
        self.request = request
        self._emit = emit
        self._broker = broker

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        await self._emit(event, payload)

    async def broker(
        self,
        operation: BrokerOperation,
        resource: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self._broker is None:
            raise PluginOperationError(
                "broker_unavailable",
                "The host broker is unavailable for this invocation",
            )
        return await self._broker(operation, resource, payload)


PluginHandlerResult = Mapping[str, Any] | Awaitable[Mapping[str, Any]]
PluginHandler = Callable[[dict[str, Any], PluginContext], PluginHandlerResult]


class PluginApplication:
    """Immutable manifest plus an explicit capability/method dispatch table."""

    __slots__ = ("handlers", "manifest")

    def __init__(
        self,
        *,
        manifest: Any,
        handlers: Mapping[tuple[str, str], PluginHandler],
    ) -> None:
        # Imported lazily so manifest contracts may depend on protocol constants
        # without making the author SDK's import graph cyclic.
        from paper_reader.plugins.contracts import PluginManifest

        validated_manifest = (
            manifest
            if isinstance(manifest, PluginManifest)
            else PluginManifest.model_validate(manifest)
        )
        declared = {
            (capability.capability_id, method)
            for capability in validated_manifest.capabilities
            for method in capability.methods
        }
        normalized: dict[tuple[str, str], PluginHandler] = {}
        for raw_key, handler in handlers.items():
            if len(raw_key) != 2:
                raise ValueError("Plugin handler keys must contain capability and method")
            key = (raw_key[0].strip().casefold(), raw_key[1].strip().casefold())
            if key not in declared:
                raise ValueError("Plugin handler must be declared by the manifest")
            if key in normalized:
                raise ValueError("Plugin handler keys cannot be duplicated")
            if not callable(handler):
                raise ValueError("Plugin handler must be callable")
            normalized[key] = handler
        self.manifest = validated_manifest
        self.handlers = MappingProxyType(normalized)

    def handler(self, capability: str, method: str) -> PluginHandler | None:
        return self.handlers.get((capability, method))


class JsonlCodec:
    def __init__(self, *, max_line_bytes: int = DEFAULT_MAX_LINE_BYTES) -> None:
        if max_line_bytes < 64:
            raise ValueError("JSONL line limit is too small")
        self.max_line_bytes = max_line_bytes

    def encode(self, frame: BaseModel | dict[str, Any]) -> bytes:
        value = (
            frame.model_dump(mode="json", exclude_none=True)
            if isinstance(frame, BaseModel)
            else frame
        )
        try:
            encoded = (
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError) as error:
            raise ProtocolError("invalid_json", "Protocol frame is not valid JSON") from error
        if len(encoded) > self.max_line_bytes:
            raise ProtocolError("line_limit", "Protocol frame exceeds the JSONL line limit")
        return encoded

    def decode_object(self, line: bytes) -> dict[str, Any]:
        if len(line) > self.max_line_bytes:
            raise ProtocolError("line_limit", "Protocol frame exceeds the JSONL line limit")
        if not line.endswith(b"\n") or line.count(b"\n") != 1 or b"\r" in line:
            raise ProtocolError(
                "framing",
                "Protocol output must contain exactly one LF-terminated line",
            )
        body = line[:-1]
        if not body or any(byte < 0x20 for byte in body) or b"\x7f" in body:
            raise ProtocolError(
                "control_character",
                "Protocol output contains raw control characters",
            )
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ProtocolError("invalid_utf8", "Protocol output is not valid UTF-8") from error
        if text.startswith("\ufeff"):
            raise ProtocolError("invalid_utf8", "Protocol output must not contain a UTF-8 BOM")
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise ProtocolError("invalid_json", "Protocol output is not valid JSON") from error
        if not isinstance(value, dict):
            raise ProtocolError("invalid_envelope", "Protocol frame must be a JSON object")
        return value

    def decode_request(self, line: bytes) -> PluginRequest:
        try:
            return PluginRequest.model_validate(self.decode_object(line))
        except ValidationError as error:
            raise ProtocolError("invalid_request", "Plugin request envelope is invalid") from error

    def decode_frame(self, line: bytes) -> ProtocolFrame:
        value = self.decode_object(line)
        frame_type = value.get("type")
        model: type[PluginEvent] | type[PluginResponse]
        if frame_type == "event":
            model = PluginEvent
        elif frame_type == "response":
            model = PluginResponse
        else:
            raise ProtocolError("invalid_envelope", "Plugin output frame has an unknown type")
        try:
            return model.model_validate(value)
        except ValidationError as error:
            raise ProtocolError("invalid_envelope", "Plugin output envelope is invalid") from error


class ProtocolSession:
    """Validate one request's bounded, monotonically sequenced worker output."""

    def __init__(
        self,
        request: PluginRequest,
        *,
        codec: JsonlCodec | None = None,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    ) -> None:
        if max_total_bytes < 1:
            raise ValueError("Protocol total output limit must be positive")
        self.request = request
        self.codec = codec or JsonlCodec()
        self.max_total_bytes = max_total_bytes
        self._total_bytes = 0
        self._next_sequence = 1
        self._finished = False

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def accept_line(self, line: bytes) -> ProtocolFrame:
        if self._finished:
            raise ProtocolError("duplicate_terminal", "Output appeared after the terminal response")
        self._total_bytes += len(line)
        if self._total_bytes > self.max_total_bytes:
            raise ProtocolError("total_output_limit", "Plugin exceeded the total output limit")
        frame = self.codec.decode_frame(line)
        if frame.protocol_version != self.request.protocol_version:
            raise ProtocolError(
                "protocol_mismatch",
                "Plugin output protocol_version does not match",
            )
        if frame.request_id != self.request.request_id:
            raise ProtocolError("request_mismatch", "Plugin output request_id does not match")
        if frame.sequence != self._next_sequence:
            raise ProtocolError(
                "sequence",
                "Plugin output sequence is not monotonically contiguous",
            )
        self._next_sequence += 1
        if isinstance(frame, PluginResponse):
            self._finished = True
        return frame
