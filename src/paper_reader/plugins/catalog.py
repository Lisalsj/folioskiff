"""Explicit plugin discovery with manifest inspection inside the Windows sandbox."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError

from paper_reader.plugins.contracts import PLUGIN_PROTOCOL_VERSION, PluginManifest
from paper_reader.plugins.discovery import (
    DiscoveredEntryPoint,
    DiscoveryResult,
    PluginInstallationRegistry,
    discover_plugins,
    validate_manifest_identity,
)
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


class PluginInspectionError(RuntimeError):
    """Stable inspection failure that intentionally excludes worker diagnostics."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PluginCatalogIssue:
    plugin_id: str | None
    code: str


@dataclass(frozen=True, slots=True)
class PluginCatalogRefresh:
    discovered: int
    registered: int
    issues: tuple[PluginCatalogIssue, ...]


class ManifestInspector(Protocol):
    async def inspect(self, discovered: DiscoveredEntryPoint) -> PluginManifest: ...


class PluginManifestInspector:
    """Load a reserved manifest probe without granting a plugin any capability."""

    def __init__(
        self,
        *,
        launcher: SandboxLauncher,
        executable: Path,
        work_root: Path,
        environment_source: Mapping[str, str] | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        executable_path = Path(executable)
        if not executable_path.is_file():
            raise ValueError("Plugin worker executable must exist")
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("Manifest timeout must be between 0 and 60 seconds")
        self._launcher = launcher
        self._executable = executable_path.resolve()
        self._work_root = Path(work_root).resolve()
        self._environment_source = dict(environment_source or {})
        self._timeout_seconds = timeout_seconds

    async def inspect(self, discovered: DiscoveredEntryPoint) -> PluginManifest:
        probe = self._launcher.probe()
        if not probe.available:
            raise PluginInspectionError(
                "sandbox_unavailable",
                "The Windows plugin sandbox is unavailable",
            )

        request = PluginRequest(
            protocol_version=PLUGIN_PROTOCOL_VERSION,
            request_id=str(uuid4()),
            capability="plugin.manifest",
            method="probe",
            payload={},
            deadline=datetime.now(UTC) + timedelta(seconds=self._timeout_seconds),
        )
        codec = JsonlCodec()
        protocol = ProtocolSession(request, codec=codec)
        terminal: PluginResponse | None = None

        async def on_stdout_line(line: bytes) -> bytes | None:
            nonlocal terminal
            frame = protocol.accept_line(line)
            if isinstance(frame, PluginEvent):
                raise ProtocolError(
                    "unexpected_event",
                    "Manifest probes cannot emit events or broker requests",
                )
            terminal = frame
            return None

        call_directory = create_empty_working_directory(
            self._work_root,
            request.request_id,
        )
        spec = SandboxProcessSpec(
            executable=self._executable,
            arguments=(
                "-I",
                "-m",
                "paper_reader.plugins.worker",
                "--entry-point",
                discovered.entry_point_value,
            ),
            working_directory=call_directory,
            environment=build_plugin_environment(
                self._executable.parent,
                source=self._environment_source,
            ),
            limits=SandboxLimits(wall_clock_seconds=self._timeout_seconds),
        )
        try:
            try:
                execution = await self._launcher.run(
                    spec,
                    initial_stdin=codec.encode(request),
                    cancelled=lambda: False,
                    on_stdout_line=on_stdout_line,
                )
            except ProtocolError as error:
                raise PluginInspectionError(
                    "manifest_protocol_error",
                    "The plugin manifest response violated the protocol",
                ) from error
            except SandboxError as error:
                raise PluginInspectionError(
                    "manifest_sandbox_failed",
                    "The plugin manifest sandbox failed",
                ) from error

            if execution.exit_code != 0:
                raise PluginInspectionError(
                    "manifest_worker_failed",
                    "The plugin manifest worker exited before completing",
                )
            if not protocol.finished or terminal is None:
                raise PluginInspectionError(
                    "manifest_protocol_error",
                    "The plugin did not return one manifest response",
                )
            if not terminal.ok:
                raise PluginInspectionError(
                    "manifest_rejected",
                    "The plugin rejected its reserved manifest probe",
                )
            result = terminal.result
            if result is None or set(result) != {"manifest"}:
                raise PluginInspectionError(
                    "manifest_invalid",
                    "The plugin returned an invalid manifest envelope",
                )
            try:
                manifest = PluginManifest.model_validate(result["manifest"])
            except ValidationError as error:
                raise PluginInspectionError(
                    "manifest_invalid",
                    "The plugin manifest does not match the host contract",
                ) from error
            try:
                validate_manifest_identity(discovered, manifest)
            except ValueError as error:
                raise PluginInspectionError(
                    "manifest_identity_mismatch",
                    "The plugin manifest identity does not match installed metadata",
                ) from error
            return manifest
        finally:
            cleanup_working_directory(self._work_root, call_directory)


class PluginCatalog:
    """Refresh installation records while quarantining failures per plugin."""

    def __init__(
        self,
        *,
        registry: PluginInstallationRegistry,
        inspector: ManifestInspector,
        discover: Callable[[], DiscoveryResult] = discover_plugins,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._registry = registry
        self._inspector = inspector
        self._discover = discover
        self._clock = clock

    async def refresh(self) -> PluginCatalogRefresh:
        try:
            discovery = self._discover()
        except Exception:
            timestamp = self._clock()
            for installation in self._registry.list_all():
                self._registry.quarantine_unverified(
                    installation.plugin_id,
                    reason="plugin_not_verified",
                    changed_at=timestamp,
                )
            return PluginCatalogRefresh(
                discovered=0,
                registered=0,
                issues=(PluginCatalogIssue(plugin_id=None, code="discovery_failed"),),
            )

        issues = [
            PluginCatalogIssue(plugin_id=issue.plugin_id, code=issue.code)
            for issue in discovery.issues
        ]
        registered = 0
        verified: set[str] = set()
        discovered_ids = {entry.plugin_id for entry in discovery.entries} | {
            issue.plugin_id for issue in discovery.issues if issue.plugin_id is not None
        }
        for entry in discovery.entries:
            try:
                manifest = await self._inspector.inspect(entry)
                self._registry.upsert(entry, manifest, discovered_at=self._clock())
            except PluginInspectionError as error:
                issues.append(PluginCatalogIssue(plugin_id=entry.plugin_id, code=error.code))
                continue
            except ValueError:
                issues.append(
                    PluginCatalogIssue(
                        plugin_id=entry.plugin_id,
                        code="manifest_registration_failed",
                    )
                )
                continue
            registered += 1
            verified.add(entry.plugin_id)

        timestamp = self._clock()
        for installation in self._registry.list_all():
            if installation.plugin_id in verified:
                continue
            self._registry.quarantine_unverified(
                installation.plugin_id,
                reason=(
                    "plugin_not_verified"
                    if installation.plugin_id in discovered_ids
                    else "plugin_not_installed"
                ),
                changed_at=timestamp,
            )

        return PluginCatalogRefresh(
            discovered=len(discovery.entries),
            registered=registered,
            issues=tuple(sorted(issues, key=lambda issue: (issue.plugin_id or "", issue.code))),
        )
