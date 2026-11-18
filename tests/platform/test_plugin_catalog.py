from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from paper_reader.database import create_session_factory, create_sqlite_engine
from paper_reader.plugins.catalog import PluginCatalog, PluginManifestInspector
from paper_reader.plugins.contracts import PluginManifest
from paper_reader.plugins.discovery import (
    DiscoveredEntryPoint,
    DiscoveryIssue,
    DiscoveryResult,
    PluginInstallationRegistry,
)
from paper_reader.plugins.sandbox import (
    SandboxExecution,
    SandboxProbe,
    SandboxProcessSpec,
)
from paper_reader.plugins.sdk import JsonlCodec, PluginRequest, PluginResponse


def manifest_payload(*, plugin_id: str = "fixture.source") -> dict[str, object]:
    return {
        "manifest_version": "1",
        "plugin_id": plugin_id,
        "display_name": "Fixture Source",
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
                "timeout_seconds": 30,
            }
        ],
    }


def discovered() -> DiscoveredEntryPoint:
    return DiscoveredEntryPoint(
        plugin_id="fixture.source",
        entry_point_value="paper_reader_fixture_source:application",
        distribution_name="paper-reader-fixture-source",
        distribution_version="1.0.0",
    )


class ManifestLauncher:
    def __init__(
        self,
        manifest: dict[str, object],
        *,
        exit_code: int = 0,
    ) -> None:
        self.manifest = manifest
        self.exit_code = exit_code
        self.calls: list[tuple[SandboxProcessSpec, PluginRequest]] = []

    def probe(self) -> SandboxProbe:
        return SandboxProbe(
            available=True,
            code=None,
            app_container=True,
            job_object=True,
        )

    async def run(
        self,
        spec: SandboxProcessSpec,
        *,
        initial_stdin: bytes,
        cancelled: Callable[[], bool],
        on_stdout_line: Callable[[bytes], Any],
    ) -> SandboxExecution:
        assert cancelled() is False
        codec = JsonlCodec()
        request = codec.decode_request(initial_stdin)
        self.calls.append((spec, request))
        await on_stdout_line(
            codec.encode(
                PluginResponse(
                    protocol_version=request.protocol_version,
                    request_id=request.request_id,
                    sequence=1,
                    ok=True,
                    result={"manifest": self.manifest},
                )
            )
        )
        return SandboxExecution(exit_code=self.exit_code, stderr=b"private worker stderr")


def registry(tmp_path: Path) -> PluginInstallationRegistry:
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "catalog.sqlite3"
    upgrade_database(database)
    return PluginInstallationRegistry(
        create_session_factory(create_sqlite_engine(database))
    )


def inspector(
    tmp_path: Path,
    launcher: ManifestLauncher,
) -> PluginManifestInspector:
    tmp_path.mkdir(parents=True, exist_ok=True)
    executable = tmp_path / "paper-reader-plugin-worker.exe"
    executable.write_bytes(b"fixture worker")
    return PluginManifestInspector(
        launcher=launcher,
        executable=executable,
        work_root=tmp_path / "manifest-probes",
        environment_source={
            "SYSTEMROOT": r"C:\Windows",
            "OPENAI_API_KEY": "must-not-leak",
            "HTTP_PROXY": "http://127.0.0.1:9",
        },
    )


@pytest.mark.asyncio
async def test_refresh_discovers_metadata_then_validates_manifest_only_inside_sandbox(
    tmp_path: Path,
) -> None:
    installations = registry(tmp_path)
    launcher = ManifestLauncher(manifest_payload())
    catalog = PluginCatalog(
        registry=installations,
        inspector=inspector(tmp_path, launcher),
        discover=lambda: DiscoveryResult(entries=(discovered(),), issues=()),
        clock=lambda: datetime(2026, 7, 14, 8, tzinfo=UTC),
    )

    result = await catalog.refresh()

    assert result.discovered == 1
    assert result.registered == 1
    assert result.issues == ()
    stored = installations.require("fixture.source")
    assert stored.enabled is False
    assert stored.manifest == PluginManifest.model_validate(manifest_payload())
    spec, request = launcher.calls[0]
    assert request.capability == "plugin.manifest"
    assert request.method == "probe"
    assert request.payload == {}
    assert spec.arguments == (
        "-I",
        "-m",
        "paper_reader.plugins.worker",
        "--entry-point",
        "paper_reader_fixture_source:application",
    )
    assert "OPENAI_API_KEY" not in spec.environment
    assert "HTTP_PROXY" not in spec.environment
    assert not spec.working_directory.exists()


@pytest.mark.asyncio
async def test_refresh_quarantines_invalid_manifest_identity_and_keeps_safe_issue_codes(
    tmp_path: Path,
) -> None:
    installations = registry(tmp_path)
    launcher = ManifestLauncher(manifest_payload(plugin_id="fixture.impostor"))
    catalog = PluginCatalog(
        registry=installations,
        inspector=inspector(tmp_path, launcher),
        discover=lambda: DiscoveryResult(
            entries=(discovered(),),
            issues=(
                DiscoveryIssue(
                    plugin_id=None,
                    code="invalid_plugin_id",
                    message="private metadata details",
                ),
            ),
        ),
    )

    result = await catalog.refresh()

    assert result.discovered == 1
    assert result.registered == 0
    assert [(issue.plugin_id, issue.code) for issue in result.issues] == [
        (None, "invalid_plugin_id"),
        ("fixture.source", "manifest_identity_mismatch"),
    ]
    assert installations.list_all() == ()
    assert "private" not in repr(result)


@pytest.mark.asyncio
async def test_refresh_isolates_one_broken_plugin_and_registers_the_other(
    tmp_path: Path,
) -> None:
    installations = registry(tmp_path)
    good = discovered()
    broken = DiscoveredEntryPoint(
        plugin_id="fixture.broken",
        entry_point_value="fixture_broken:application",
        distribution_name="paper-reader-fixture-broken",
        distribution_version="1.0.0",
    )
    launchers = {
        good.plugin_id: ManifestLauncher(manifest_payload()),
        broken.plugin_id: ManifestLauncher(manifest_payload(), exit_code=7),
    }

    class PerPluginInspector:
        async def inspect(self, entry: DiscoveredEntryPoint) -> PluginManifest:
            return await inspector(tmp_path / entry.plugin_id, launchers[entry.plugin_id]).inspect(
                entry
            )

    catalog = PluginCatalog(
        registry=installations,
        inspector=PerPluginInspector(),
        discover=lambda: DiscoveryResult(entries=(broken, good), issues=()),
    )

    result = await catalog.refresh()

    assert result.registered == 1
    assert [(issue.plugin_id, issue.code) for issue in result.issues] == [
        ("fixture.broken", "manifest_worker_failed")
    ]
    assert [item.plugin_id for item in installations.list_all()] == ["fixture.source"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entries", "expected_reason"),
    [
        ((), "plugin_not_installed"),
        ((discovered(),), "plugin_not_verified"),
    ],
)
async def test_refresh_quarantines_previously_enabled_plugins_that_are_no_longer_verified(
    tmp_path: Path,
    entries: tuple[DiscoveredEntryPoint, ...],
    expected_reason: str,
) -> None:
    installations = registry(tmp_path)
    payload = manifest_payload()
    capabilities = payload["capabilities"]
    assert isinstance(capabilities, list)
    capability = capabilities[0]
    assert isinstance(capability, dict)
    capability["permissions"] = [
        {
            "kind": "network",
            "resource": "api.example.org",
            "purpose": "Read public fixture metadata",
        }
    ]
    original = PluginManifest.model_validate(payload)
    installations.upsert(
        discovered(),
        original,
        discovered_at=datetime(2026, 7, 14, 7, tzinfo=UTC),
    )
    installations.record_permission_decision(
        "fixture.source",
        original.requested_permissions()[0],
        decision="grant",
        decided_at=datetime(2026, 7, 14, 7, 30, tzinfo=UTC),
    )
    installations.set_enabled("fixture.source", True)
    invalid = ManifestLauncher(manifest_payload(plugin_id="fixture.impostor"))
    catalog = PluginCatalog(
        registry=installations,
        inspector=inspector(tmp_path, invalid),
        discover=lambda: DiscoveryResult(entries=entries, issues=()),
        clock=lambda: datetime(2026, 7, 14, 8, tzinfo=UTC),
    )

    await catalog.refresh()

    current = installations.require("fixture.source")
    assert current.enabled is False
    assert current.compatible is False
    assert current.compatibility_reason == expected_reason
    assert installations.effective_permissions("fixture.source") == ()
    assert [item.decision for item in installations.permission_history("fixture.source")] == [
        "grant",
        "revoke",
    ]
