"""Correlated one-call plugin host with fail-closed isolation and circuit breaking."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from paper_reader.plugins.broker import (
    BrokerError,
    CredentialBroker,
    FileBroker,
    NetworkBroker,
    PermissionBroker,
    SecretRedactor,
)
from paper_reader.plugins.contracts import (
    PLUGIN_PROTOCOL_VERSION,
    PluginCapability,
)
from paper_reader.plugins.discovery import (
    PluginInstallation,
    PluginInstallationRegistry,
)
from paper_reader.plugins.repository import PluginHealthRepository, PluginStateError
from paper_reader.plugins.sandbox import (
    SandboxError,
    SandboxLauncher,
    SandboxLimits,
    SandboxProcessSpec,
    build_plugin_environment,
    cleanup_working_directory,
    create_empty_working_directory,
)
from paper_reader.plugins.sdk import (
    JsonlCodec,
    PluginEvent,
    PluginRequest,
    PluginResponse,
    ProtocolError,
    ProtocolSession,
)

BrokerFactory = Callable[
    [PluginInstallation, PluginCapability, str],
    PermissionBroker,
]


class PluginHostError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        plugin_error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.plugin_error_code = plugin_error_code


@dataclass(frozen=True, slots=True)
class PluginInvocationResult:
    result: dict[str, Any]
    events: tuple[PluginEvent, ...]


class PluginHost:
    """Validate everything before launch and isolate failures to one installation."""

    def __init__(
        self,
        *,
        registry: PluginInstallationRegistry,
        health: PluginHealthRepository,
        launcher: SandboxLauncher,
        executable: Path,
        work_root: Path,
        environment_source: Mapping[str, str] | None = None,
        broker_factory: BrokerFactory | None = None,
    ) -> None:
        executable_path = Path(executable)
        if not executable_path.is_file():
            raise ValueError("Plugin worker executable must exist")
        self._registry = registry
        self._health = health
        self._launcher = launcher
        self._executable = executable_path.resolve()
        self._work_root = Path(work_root).resolve()
        self._environment_source = dict(environment_source or {})
        self._broker_factory = broker_factory

    async def invoke(
        self,
        plugin_id: str,
        *,
        capability_id: str,
        method: str,
        payload: dict[str, Any],
        deadline: datetime,
        cancelled: Callable[[], bool],
    ) -> PluginInvocationResult:
        now = datetime.now(UTC)
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise PluginHostError(
                "invalid_deadline",
                "Plugin deadline must include a timezone",
            )
        normalized_deadline = deadline.astimezone(UTC)
        if normalized_deadline <= now:
            raise PluginHostError("deadline_exceeded", "Plugin request deadline has expired")
        if cancelled():
            raise PluginHostError(
                "cancelled",
                "Plugin request was cancelled before launch",
                retryable=True,
            )
        try:
            installation = self._health.require_callable(plugin_id)
        except PluginStateError as error:
            raise PluginHostError(error.code, str(error)) from error
        except KeyError as error:
            raise PluginHostError("plugin_not_found", "Plugin is not installed") from error

        capability = next(
            (
                item
                for item in installation.manifest.capabilities
                if item.capability_id == capability_id
            ),
            None,
        )
        if capability is None:
            raise PluginHostError(
                "unknown_capability",
                "Plugin capability is not declared",
            )
        normalized_method = method.strip().casefold()
        if normalized_method not in capability.methods:
            raise PluginHostError("unknown_method", "Plugin method is not declared")

        effective = {
            (permission.kind, permission.resource)
            for permission in self._registry.effective_permissions(plugin_id)
        }
        required = {
            (permission.kind, permission.resource) for permission in capability.permissions
        }
        if not required <= effective:
            raise PluginHostError(
                "permission_denied",
                "Plugin capability permissions are not fully approved",
            )

        probe = self._launcher.probe()
        if not probe.available:
            raise PluginHostError(
                "sandbox_unavailable",
                "The Windows plugin sandbox is unavailable",
            )

        request_id = str(uuid4())
        request = PluginRequest(
            protocol_version=PLUGIN_PROTOCOL_VERSION,
            request_id=request_id,
            capability=capability_id,
            method=normalized_method,
            payload=payload,
            deadline=normalized_deadline,
        )
        codec = JsonlCodec()
        try:
            initial_stdin = codec.encode(request)
        except ProtocolError as error:
            raise PluginHostError(
                "invalid_request",
                "Plugin request exceeds the isolated protocol limits",
            ) from error
        protocol = ProtocolSession(request, codec=codec)
        broker = self._create_broker(installation, capability, request_id)
        events: list[PluginEvent] = []
        terminal: PluginResponse | None = None

        async def on_stdout_line(line: bytes) -> bytes | None:
            nonlocal terminal
            frame = protocol.accept_line(line)
            if isinstance(frame, PluginEvent):
                if frame.event == "broker.request":
                    try:
                        reply = await broker.handle_event(frame)
                    except BrokerError as error:
                        raise ProtocolError(
                            "broker_protocol",
                            "Plugin broker event is invalid",
                        ) from error
                    return codec.encode(reply)
                redacted_payload = broker.redactor.redact_value(frame.payload)
                if not isinstance(redacted_payload, dict):  # pragma: no cover - dict remains dict
                    raise ProtocolError("invalid_event", "Plugin event payload is invalid")
                events.append(frame.model_copy(update={"payload": redacted_payload}))
            else:
                terminal = frame
            return None

        call_id = request_id
        call_directory = create_empty_working_directory(self._work_root, call_id)
        remaining_seconds = min(
            capability.timeout_seconds,
            max(0.001, (normalized_deadline - now).total_seconds()),
        )
        spec = SandboxProcessSpec(
            executable=self._executable,
            arguments=(
                "-I",
                "-m",
                "paper_reader.plugins.worker",
                "--entry-point",
                installation.entry_point_value,
            ),
            working_directory=call_directory,
            environment=build_plugin_environment(
                self._executable.parent,
                source=self._environment_source,
            ),
            limits=SandboxLimits(wall_clock_seconds=remaining_seconds),
        )
        try:
            try:
                execution = await self._launcher.run(
                    spec,
                    initial_stdin=initial_stdin,
                    cancelled=cancelled,
                    on_stdout_line=on_stdout_line,
                )
            except ProtocolError as error:
                self._record_failure(plugin_id, "protocol_error")
                raise PluginHostError(
                    "protocol_error",
                    "Plugin output violated the isolated protocol",
                ) from error
            except SandboxError as error:
                if error.code not in {"cancelled", "sandbox_unavailable"}:
                    self._record_failure(plugin_id, error.code)
                raise PluginHostError(
                    error.code,
                    str(error),
                    retryable=error.retryable,
                ) from error

            if execution.exit_code != 0:
                self._record_failure(plugin_id, "plugin_crash")
                raise PluginHostError(
                    "plugin_crash",
                    "Plugin process exited before completing safely",
                )
            if not protocol.finished or terminal is None:
                self._record_failure(plugin_id, "protocol_error")
                raise PluginHostError(
                    "protocol_error",
                    "Plugin process did not emit one terminal response",
                )

            self._health.record_success(plugin_id, called_at=datetime.now(UTC))
            if not terminal.ok:
                terminal_error = terminal.error
                if terminal_error is None:  # pragma: no cover - response schema enforces this
                    raise PluginHostError("protocol_error", "Plugin error response is invalid")
                raise PluginHostError(
                    "plugin_error",
                    "Plugin returned a structured operation error",
                    retryable=terminal_error.retryable,
                    plugin_error_code=terminal_error.code,
                )
            result = terminal.result
            if result is None:  # pragma: no cover - response schema enforces this
                raise PluginHostError("protocol_error", "Plugin success response is invalid")
            redacted_result = broker.redactor.redact_value(result)
            if not isinstance(redacted_result, dict):  # pragma: no cover - dict remains dict
                raise PluginHostError("protocol_error", "Plugin result is invalid")
            return PluginInvocationResult(
                result=redacted_result,
                events=tuple(events),
            )
        finally:
            cleanup_working_directory(self._work_root, call_directory)

    def _create_broker(
        self,
        installation: PluginInstallation,
        capability: PluginCapability,
        request_id: str,
    ) -> PermissionBroker:
        if self._broker_factory is not None:
            return self._broker_factory(installation, capability, request_id)
        redactor = SecretRedactor()
        credentials = CredentialBroker(
            provider=lambda _resource: None,
            redactor=redactor,
        )
        capability_permissions = {
            (permission.kind, permission.resource) for permission in capability.permissions
        }
        granted = tuple(
            permission
            for permission in self._registry.effective_permissions(installation.plugin_id)
            if (permission.kind, permission.resource) in capability_permissions
        )
        return PermissionBroker(
            protocol_version=PLUGIN_PROTOCOL_VERSION,
            request_id=request_id,
            declared=capability.permissions,
            granted=granted,
            network=NetworkBroker(),
            files=FileBroker(resources={}),
            credentials=credentials,
            redactor=redactor,
        )

    def _record_failure(self, plugin_id: str, reason: str) -> None:
        self._health.record_failure(
            plugin_id,
            reason=reason,
            called_at=datetime.now(UTC),
        )

    def record_contract_failure(self, plugin_id: str) -> None:
        """Count a domain-schema violation without exposing health storage to adapters."""

        self._record_failure(plugin_id, "invalid_plugin_result")
