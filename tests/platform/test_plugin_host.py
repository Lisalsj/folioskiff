from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.database import create_session_factory, create_sqlite_engine
from paper_reader.plugins.broker import (
    BrokerCall,
    CredentialBroker,
    FileBroker,
    NetworkBroker,
    PermissionBroker,
    SecretRedactor,
)
from paper_reader.plugins.contracts import PLUGIN_PROTOCOL_VERSION, PluginManifest
from paper_reader.plugins.discovery import (
    DiscoveredEntryPoint,
    PluginInstallationRegistry,
)
from paper_reader.plugins.host import (
    PluginHost,
    PluginHostError,
    PluginInvocationResult,
)
from paper_reader.plugins.repository import PluginHealthRepository
from paper_reader.plugins.sandbox import (
    SandboxError,
    SandboxExecution,
    SandboxProbe,
    SandboxProcessSpec,
)
from paper_reader.plugins.sdk import (
    JsonlCodec,
    PluginError,
    PluginEvent,
    PluginRequest,
    PluginResponse,
)

Script = Callable[[PluginRequest], tuple[tuple[bytes, ...], int, bytes]]


def manifest_payload(
    *,
    plugin_id: str = "fixture.source",
    distribution_name: str = "paper-reader-fixture",
) -> dict[str, object]:
    return {
        "manifest_version": "1",
        "plugin_id": plugin_id,
        "display_name": "Fixture Source",
        "distribution_name": distribution_name,
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
                "capability_id": plugin_id,
                "kind": "source_connector",
                "methods": ["probe", "estimate", "fetch_page"],
                "permissions": [],
                "timeout_seconds": 30,
            }
        ],
    }


def upgrade_database(path: Path) -> None:
    from paper_reader.migrations import upgrade_database as run_upgrade

    run_upgrade(path)


def repositories(
    tmp_path: Path,
) -> tuple[PluginInstallationRegistry, PluginHealthRepository, sessionmaker[Session]]:
    database = tmp_path / "plugins.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    return (
        PluginInstallationRegistry(sessions),
        PluginHealthRepository(sessions),
        sessions,
    )


def install(
    registry: PluginInstallationRegistry,
    *,
    plugin_id: str = "fixture.source",
    distribution_name: str = "paper-reader-fixture",
    enabled: bool = True,
) -> PluginManifest:
    manifest = PluginManifest.model_validate(
        manifest_payload(plugin_id=plugin_id, distribution_name=distribution_name)
    )
    registry.upsert(
        DiscoveredEntryPoint(
            plugin_id=plugin_id,
            entry_point_value=f"{plugin_id.replace('.', '_')}.plugin:plugin",
            distribution_name=distribution_name,
            distribution_version="1.0.0",
        ),
        manifest,
        discovered_at=datetime(2026, 7, 14, tzinfo=UTC),
    )
    if enabled:
        registry.set_enabled(plugin_id, True)
    return manifest


class ScriptedLauncher:
    def __init__(
        self,
        scripts: list[Script | SandboxError],
        *,
        probe: SandboxProbe | None = None,
    ) -> None:
        self.scripts = scripts
        self.probe_result = probe or SandboxProbe(
            available=True,
            code=None,
            app_container=True,
            job_object=True,
        )
        self.calls: list[tuple[SandboxProcessSpec, PluginRequest]] = []
        self.replies: list[bytes] = []

    def probe(self) -> SandboxProbe:
        return self.probe_result

    async def run(
        self,
        spec: SandboxProcessSpec,
        *,
        initial_stdin: bytes,
        cancelled: Callable[[], bool],
        on_stdout_line: Callable[[bytes], Any],
    ) -> SandboxExecution:
        request = JsonlCodec().decode_request(initial_stdin)
        self.calls.append((spec, request))
        script = self.scripts.pop(0)
        if isinstance(script, SandboxError):
            raise script
        lines, exit_code, stderr = script(request)
        for line in lines:
            reply = await on_stdout_line(line)
            if reply is not None:
                self.replies.append(reply)
        return SandboxExecution(exit_code=exit_code, stderr=stderr)


def success_script(
    result: dict[str, Any] | None = None,
    *,
    with_event: bool = False,
) -> Script:
    codec = JsonlCodec()

    def run(request: PluginRequest) -> tuple[tuple[bytes, ...], int, bytes]:
        frames: list[bytes] = []
        sequence = 1
        if with_event:
            frames.append(
                codec.encode(
                    PluginEvent(
                        protocol_version=request.protocol_version,
                        request_id=request.request_id,
                        sequence=sequence,
                        event="progress",
                        payload={"completed": 1},
                    )
                )
            )
            sequence += 1
        frames.append(
            codec.encode(
                PluginResponse(
                    protocol_version=request.protocol_version,
                    request_id=request.request_id,
                    sequence=sequence,
                    ok=True,
                    result=result or {"healthy": True},
                )
            )
        )
        return tuple(frames), 0, b"worker diagnostic is not user-visible"

    return run


def host_for(
    tmp_path: Path,
    registry: PluginInstallationRegistry,
    health: PluginHealthRepository,
    launcher: ScriptedLauncher,
    *,
    broker_factory: Callable[[Any, Any, str], PermissionBroker] | None = None,
) -> PluginHost:
    executable = tmp_path / "python.exe"
    executable.write_bytes(b"fixture executable")
    return PluginHost(
        registry=registry,
        health=health,
        launcher=launcher,
        executable=executable,
        work_root=tmp_path / "plugin-calls",
        environment_source={
            "PATH": str(tmp_path),
            "SYSTEMROOT": r"C:\\Windows",
            "OPENAI_API_KEY": "must-not-leak",
            "HTTP_PROXY": "http://127.0.0.1:9",
        },
        broker_factory=broker_factory,
    )


@pytest.mark.asyncio
async def test_host_invokes_enabled_capability_with_correlated_protocol_and_cleans_cwd(
    tmp_path: Path,
) -> None:
    registry, health, _ = repositories(tmp_path)
    install(registry)
    launcher = ScriptedLauncher([success_script({"estimated": 12}, with_event=True)])
    host = host_for(tmp_path, registry, health, launcher)

    result = await host.invoke(
        "fixture.source",
        capability_id="fixture.source",
        method="estimate",
        payload={"month": "2026-06"},
        deadline=datetime.now(UTC) + timedelta(seconds=10),
        cancelled=lambda: False,
    )

    assert result == PluginInvocationResult(
        result={"estimated": 12},
        events=(
            PluginEvent(
                protocol_version="1.0",
                request_id=result.events[0].request_id,
                sequence=1,
                event="progress",
                payload={"completed": 1},
            ),
        ),
    )
    spec, request = launcher.calls[0]
    assert request.capability == "fixture.source"
    assert request.method == "estimate"
    assert request.payload == {"month": "2026-06"}
    assert spec.arguments == (
        "-I",
        "-m",
        "paper_reader.plugins.worker",
        "--entry-point",
        "fixture_source.plugin:plugin",
    )
    assert spec.working_directory.parent == (tmp_path / "plugin-calls").resolve()
    assert not spec.working_directory.exists()
    assert "OPENAI_API_KEY" not in spec.environment
    assert "HTTP_PROXY" not in spec.environment
    assert health.require_callable("fixture.source").consecutive_failures == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "probe", "capability_id", "method", "expected_code"),
    [
        (False, None, "fixture.source", "probe", "plugin_disabled"),
        (
            True,
            SandboxProbe(
                available=False,
                code="appcontainer_unavailable",
                app_container=False,
                job_object=True,
            ),
            "fixture.source",
            "probe",
            "sandbox_unavailable",
        ),
        (True, None, "fixture.missing", "probe", "unknown_capability"),
        (True, None, "fixture.source", "delete_everything", "unknown_method"),
    ],
)
async def test_host_fails_closed_before_launch(
    tmp_path: Path,
    enabled: bool,
    probe: SandboxProbe | None,
    capability_id: str,
    method: str,
    expected_code: str,
) -> None:
    registry, health, _ = repositories(tmp_path)
    install(registry, enabled=enabled)
    launcher = ScriptedLauncher([success_script()], probe=probe)
    host = host_for(tmp_path, registry, health, launcher)

    with pytest.raises(PluginHostError) as captured:
        await host.invoke(
            "fixture.source",
            capability_id=capability_id,
            method=method,
            payload={},
            deadline=datetime.now(UTC) + timedelta(seconds=5),
            cancelled=lambda: False,
        )

    assert captured.value.code == expected_code
    assert launcher.calls == []
    assert not (tmp_path / "plugin-calls").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("script", "expected_code"),
    [
        (SandboxError("timeout", "Sandbox timed out", retryable=True), "timeout"),
        (SandboxError("output_limit", "Sandbox output exceeded the limit"), "output_limit"),
        (lambda _request: ((), 7, b"raw crash details"), "plugin_crash"),
    ],
)
async def test_runtime_failures_increment_only_the_target_plugin(
    tmp_path: Path,
    script: Script | SandboxError,
    expected_code: str,
) -> None:
    registry, health, _ = repositories(tmp_path)
    install(registry)
    install(
        registry,
        plugin_id="fixture.other",
        distribution_name="paper-reader-other",
    )
    launcher = ScriptedLauncher([script])
    host = host_for(tmp_path, registry, health, launcher)

    with pytest.raises(PluginHostError) as captured:
        await host.invoke(
            "fixture.source",
            capability_id="fixture.source",
            method="probe",
            payload={},
            deadline=datetime.now(UTC) + timedelta(seconds=5),
            cancelled=lambda: False,
        )

    assert captured.value.code == expected_code
    assert "raw crash details" not in str(captured.value)
    assert registry.require("fixture.source").consecutive_failures == 1
    assert registry.require("fixture.other").consecutive_failures == 0


@pytest.mark.asyncio
async def test_three_protocol_failures_open_one_circuit_until_explicit_reset(
    tmp_path: Path,
) -> None:
    registry, health, _ = repositories(tmp_path)
    install(registry)
    install(
        registry,
        plugin_id="fixture.other",
        distribution_name="paper-reader-other",
    )
    codec = JsonlCodec()

    def wrong_request(request: PluginRequest) -> tuple[tuple[bytes, ...], int, bytes]:
        return (
            (
                codec.encode(
                    PluginResponse(
                        protocol_version="1.0",
                        request_id=str(uuid4()),
                        sequence=1,
                        ok=True,
                        result={},
                    )
                ),
            ),
            0,
            b"",
        )

    def wrong_protocol(request: PluginRequest) -> tuple[tuple[bytes, ...], int, bytes]:
        return (
            (
                codec.encode(
                    PluginResponse(
                        protocol_version="1.1",
                        request_id=request.request_id,
                        sequence=1,
                        ok=True,
                        result={},
                    )
                ),
            ),
            0,
            b"",
        )

    launcher = ScriptedLauncher(
        [
            lambda _request: ((b"not-json\n",), 0, b""),
            wrong_request,
            wrong_protocol,
            success_script({"other": True}),
            success_script({"recovered": True}),
        ]
    )
    host = host_for(tmp_path, registry, health, launcher)

    for expected_count in (1, 2, 3):
        with pytest.raises(PluginHostError) as captured:
            await host.invoke(
                "fixture.source",
                capability_id="fixture.source",
                method="probe",
                payload={},
                deadline=datetime.now(UTC) + timedelta(seconds=5),
                cancelled=lambda: False,
            )
        assert captured.value.code == "protocol_error"
        assert registry.require("fixture.source").consecutive_failures == expected_count

    opened = registry.require("fixture.source")
    assert opened.circuit_open_reason == "protocol_error"
    with pytest.raises(PluginHostError) as open_circuit:
        await host.invoke(
            "fixture.source",
            capability_id="fixture.source",
            method="probe",
            payload={},
            deadline=datetime.now(UTC) + timedelta(seconds=5),
            cancelled=lambda: False,
        )
    assert open_circuit.value.code == "circuit_open"
    assert len(launcher.calls) == 3

    other = await host.invoke(
        "fixture.other",
        capability_id="fixture.other",
        method="probe",
        payload={},
        deadline=datetime.now(UTC) + timedelta(seconds=5),
        cancelled=lambda: False,
    )
    assert other.result == {"other": True}

    health.reset_circuit("fixture.source", reset_at=datetime.now(UTC))
    recovered = await host.invoke(
        "fixture.source",
        capability_id="fixture.source",
        method="probe",
        payload={},
        deadline=datetime.now(UTC) + timedelta(seconds=5),
        cancelled=lambda: False,
    )
    assert recovered.result == {"recovered": True}
    assert registry.require("fixture.source").consecutive_failures == 0
    assert registry.require("fixture.source").circuit_open_reason is None


@pytest.mark.asyncio
async def test_plugin_structured_error_is_not_a_host_crash_or_circuit_failure(
    tmp_path: Path,
) -> None:
    registry, health, _ = repositories(tmp_path)
    install(registry)
    codec = JsonlCodec()

    def rejected(request: PluginRequest) -> tuple[tuple[bytes, ...], int, bytes]:
        return (
            (
                codec.encode(
                    PluginResponse(
                        protocol_version=request.protocol_version,
                        request_id=request.request_id,
                        sequence=1,
                        ok=False,
                        error=PluginError(
                            code="rate_limited",
                            message="The source asked the plugin to slow down",
                            retryable=True,
                        ),
                    )
                ),
            ),
            0,
            b"arbitrary stderr",
        )

    host = host_for(tmp_path, registry, health, ScriptedLauncher([rejected]))
    with pytest.raises(PluginHostError) as captured:
        await host.invoke(
            "fixture.source",
            capability_id="fixture.source",
            method="fetch_page",
            payload={},
            deadline=datetime.now(UTC) + timedelta(seconds=5),
            cancelled=lambda: False,
        )

    assert captured.value.code == "plugin_error"
    assert captured.value.plugin_error_code == "rate_limited"
    assert captured.value.retryable is True
    assert "arbitrary stderr" not in str(captured.value)
    assert registry.require("fixture.source").consecutive_failures == 0


@pytest.mark.asyncio
async def test_expired_deadline_and_prelaunch_cancel_never_start_worker(tmp_path: Path) -> None:
    registry, health, _ = repositories(tmp_path)
    install(registry)
    launcher = ScriptedLauncher([success_script(), success_script()])
    host = host_for(tmp_path, registry, health, launcher)

    with pytest.raises(PluginHostError) as expired:
        await host.invoke(
            "fixture.source",
            capability_id="fixture.source",
            method="probe",
            payload={},
            deadline=datetime.now(UTC) - timedelta(seconds=1),
            cancelled=lambda: False,
        )
    assert expired.value.code == "deadline_exceeded"

    with pytest.raises(PluginHostError) as cancelled:
        await host.invoke(
            "fixture.source",
            capability_id="fixture.source",
            method="probe",
            payload={},
            deadline=datetime.now(UTC) + timedelta(seconds=5),
            cancelled=lambda: True,
        )
    assert cancelled.value.code == "cancelled"
    assert launcher.calls == []


@pytest.mark.asyncio
async def test_oversized_host_request_is_rejected_without_blame_or_worker_launch(
    tmp_path: Path,
) -> None:
    registry, health, _ = repositories(tmp_path)
    install(registry)
    launcher = ScriptedLauncher([success_script()])
    host = host_for(tmp_path, registry, health, launcher)

    with pytest.raises(PluginHostError) as captured:
        await host.invoke(
            "fixture.source",
            capability_id="fixture.source",
            method="probe",
            payload={"oversized": "x" * (2 * 1024 * 1024)},
            deadline=datetime.now(UTC) + timedelta(seconds=5),
            cancelled=lambda: False,
        )

    assert captured.value.code == "invalid_request"
    assert launcher.calls == []
    assert registry.require("fixture.source").consecutive_failures == 0
    assert not (tmp_path / "plugin-calls").exists()


@pytest.mark.asyncio
async def test_default_broker_scopes_effective_grants_to_the_invoked_capability(
    tmp_path: Path,
) -> None:
    registry, health, _ = repositories(tmp_path)
    payload = manifest_payload()
    payload["capabilities"] = [
        {
            "capability_id": "fixture.source",
            "kind": "source_connector",
            "methods": ["probe"],
            "permissions": [
                {
                    "kind": "network",
                    "resource": "api.example.org",
                    "purpose": "Read public paper metadata",
                }
            ],
            "timeout_seconds": 30,
        },
        {
            "capability_id": "fixture.metadata",
            "kind": "metadata_enricher",
            "methods": ["probe"],
            "permissions": [
                {
                    "kind": "credential",
                    "resource": "account.token",
                    "purpose": "Authenticate metadata requests",
                }
            ],
            "timeout_seconds": 30,
        },
    ]
    manifest = PluginManifest.model_validate(payload)
    registry.upsert(
        DiscoveredEntryPoint(
            plugin_id="fixture.source",
            entry_point_value="fixture_source.plugin:plugin",
            distribution_name="paper-reader-fixture",
            distribution_version="1.0.0",
        ),
        manifest,
        discovered_at=datetime.now(UTC),
    )
    registry.set_enabled("fixture.source", True)
    for capability in manifest.capabilities:
        for permission in capability.permissions:
            registry.record_permission_decision(
                "fixture.source",
                permission,
                decision="grant",
                decided_at=datetime.now(UTC),
            )

    launcher = ScriptedLauncher([success_script()])
    host = host_for(tmp_path, registry, health, launcher)
    result = await host.invoke(
        "fixture.source",
        capability_id="fixture.source",
        method="probe",
        payload={},
        deadline=datetime.now(UTC) + timedelta(seconds=5),
        cancelled=lambda: False,
    )

    assert result.result == {"healthy": True}
    assert len(launcher.calls) == 1


@pytest.mark.asyncio
async def test_credential_values_are_redacted_from_plugin_events_and_terminal_result(
    tmp_path: Path,
) -> None:
    registry, health, _ = repositories(tmp_path)
    payload = manifest_payload()
    payload["capabilities"] = [
        {
            "capability_id": "fixture.source",
            "kind": "source_connector",
            "methods": ["probe"],
            "permissions": [
                {
                    "kind": "credential",
                    "resource": "fixture_api_key",
                    "purpose": "Authenticate one fixture request",
                }
            ],
            "timeout_seconds": 30,
        }
    ]
    manifest = PluginManifest.model_validate(payload)
    registry.upsert(
        DiscoveredEntryPoint(
            plugin_id="fixture.source",
            entry_point_value="fixture_source.plugin:plugin",
            distribution_name="paper-reader-fixture",
            distribution_version="1.0.0",
        ),
        manifest,
        discovered_at=datetime.now(UTC),
    )
    registry.set_enabled("fixture.source", True)
    permission = manifest.capabilities[0].permissions[0]
    registry.record_permission_decision(
        "fixture.source",
        permission,
        decision="grant",
        decided_at=datetime.now(UTC),
    )

    secret = "fixture-super-secret-value"

    def broker_factory(
        _installation: Any,
        capability: Any,
        request_id: str,
    ) -> PermissionBroker:
        redactor = SecretRedactor()
        return PermissionBroker(
            protocol_version=PLUGIN_PROTOCOL_VERSION,
            request_id=request_id,
            declared=capability.permissions,
            granted=capability.permissions,
            network=NetworkBroker(),
            files=FileBroker(resources={}),
            credentials=CredentialBroker(
                provider=lambda _resource: secret,
                redactor=redactor,
            ),
            redactor=redactor,
        )

    codec = JsonlCodec()

    def credential_echo(request: PluginRequest) -> tuple[tuple[bytes, ...], int, bytes]:
        call = BrokerCall(
            broker_request_id=str(uuid4()),
            operation="credential.read",
            resource="fixture_api_key",
            payload={},
        )
        return (
            (
                codec.encode(
                    PluginEvent(
                        protocol_version=request.protocol_version,
                        request_id=request.request_id,
                        sequence=1,
                        event="broker.request",
                        payload=call.model_dump(mode="json"),
                    )
                ),
                codec.encode(
                    PluginEvent(
                        protocol_version=request.protocol_version,
                        request_id=request.request_id,
                        sequence=2,
                        event="progress",
                        payload={"unsafe_echo": secret},
                    )
                ),
                codec.encode(
                    PluginResponse(
                        protocol_version=request.protocol_version,
                        request_id=request.request_id,
                        sequence=3,
                        ok=True,
                        result={"unsafe_echo": secret},
                    )
                ),
            ),
            0,
            secret.encode("utf-8"),
        )

    launcher = ScriptedLauncher([credential_echo])
    host = host_for(
        tmp_path,
        registry,
        health,
        launcher,
        broker_factory=broker_factory,
    )
    result = await host.invoke(
        "fixture.source",
        capability_id="fixture.source",
        method="probe",
        payload={},
        deadline=datetime.now(UTC) + timedelta(seconds=5),
        cancelled=lambda: False,
    )

    assert result.events[0].payload == {"unsafe_echo": "<redacted>"}
    assert result.result == {"unsafe_echo": "<redacted>"}
    assert secret not in repr(result)
