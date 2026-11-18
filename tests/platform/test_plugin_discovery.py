from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.database import create_session_factory, create_sqlite_engine
from paper_reader.plugins.contracts import PermissionRequest, PluginManifest
from paper_reader.plugins.discovery import (
    DiscoveredEntryPoint,
    PluginInstallationRegistry,
    discover_entry_points,
    discover_plugins,
    validate_manifest_identity,
)
from paper_reader.plugins.worker import resolve_entry_point


@dataclass
class FakeDistribution:
    name: str | None
    version: str

    @property
    def metadata(self) -> dict[str, str]:
        return {} if self.name is None else {"Name": self.name}


class FakeEntryPoint:
    def __init__(
        self,
        name: str,
        value: str,
        *,
        distribution: FakeDistribution | None,
    ) -> None:
        self.name = name
        self.value = value
        self.dist = distribution
        self.load_calls = 0

    def load(self) -> object:
        self.load_calls += 1
        raise AssertionError("The main process must never load plugin code")


def manifest_payload(
    *,
    plugin_id: str = "fixture.source",
    distribution_name: str = "paper-reader-fixture",
    distribution_version: str = "1.0.0",
    purpose: str = "Read public metadata",
) -> dict[str, object]:
    return {
        "manifest_version": "1",
        "plugin_id": plugin_id,
        "display_name": "Fixture Source",
        "distribution_name": distribution_name,
        "distribution_version": distribution_version,
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
                "permissions": [
                    {
                        "kind": "network",
                        "resource": "api.example.org",
                        "purpose": purpose,
                    }
                ],
                "timeout_seconds": 30,
            }
        ],
    }


def upgrade_database(path: Path) -> None:
    from paper_reader.migrations import upgrade_database as run_upgrade

    run_upgrade(path)


def registry_for(path: Path) -> tuple[PluginInstallationRegistry, sessionmaker[Session]]:
    upgrade_database(path)
    engine = create_sqlite_engine(path)
    sessions = create_session_factory(engine)
    return PluginInstallationRegistry(sessions), sessions


def entry(
    plugin_id: str = "fixture.source",
    *,
    value: str = "paper_reader_fixture.plugin:plugin",
    distribution_name: str = "paper-reader-fixture",
    distribution_version: str = "1.0.0",
) -> DiscoveredEntryPoint:
    return DiscoveredEntryPoint(
        plugin_id=plugin_id,
        entry_point_value=value,
        distribution_name=distribution_name,
        distribution_version=distribution_version,
    )


def test_discovery_reads_only_entry_point_metadata_and_never_calls_load() -> None:
    alpha = FakeEntryPoint(
        "fixture.alpha",
        "fixture_alpha.plugin:plugin",
        distribution=FakeDistribution("paper-reader-alpha", "2.0.0"),
    )
    beta = FakeEntryPoint(
        "fixture.beta",
        "fixture_beta.plugin:plugin",
        distribution=FakeDistribution("paper-reader-beta", "1.4.0"),
    )

    first = discover_entry_points([beta, alpha])
    second = discover_entry_points([alpha, beta])

    assert first == second
    assert [item.plugin_id for item in first.entries] == ["fixture.alpha", "fixture.beta"]
    assert first.entries[0].distribution_name == "paper-reader-alpha"
    assert first.issues == ()
    assert alpha.load_calls == beta.load_calls == 0


def test_default_discovery_uses_only_the_named_entry_point_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = FakeEntryPoint(
        "fixture.grouped",
        "fixture_grouped.plugin:plugin",
        distribution=FakeDistribution("paper-reader-grouped", "1.0.0"),
    )
    selected_groups: list[str] = []

    def entry_points(*, group: str) -> list[FakeEntryPoint]:
        selected_groups.append(group)
        return [candidate]

    monkeypatch.setattr("paper_reader.plugins.discovery.metadata.entry_points", entry_points)

    result = discover_plugins()

    assert selected_groups == ["paper_reader.plugins"]
    assert [item.plugin_id for item in result.entries] == ["fixture.grouped"]
    assert candidate.load_calls == 0


def test_duplicate_ids_are_all_quarantined_without_order_dependence() -> None:
    first = FakeEntryPoint(
        "fixture.duplicate",
        "fixture_one.plugin:plugin",
        distribution=FakeDistribution("fixture-one", "1.0.0"),
    )
    second = FakeEntryPoint(
        "fixture.duplicate",
        "fixture_two.plugin:plugin",
        distribution=FakeDistribution("fixture-two", "2.0.0"),
    )

    result = discover_entry_points([second, first])

    assert result.entries == ()
    assert [(issue.plugin_id, issue.code) for issue in result.issues] == [
        ("fixture.duplicate", "duplicate_plugin_id")
    ]
    assert first.load_calls == second.load_calls == 0


def test_invalid_plugin_id_and_distribution_version_are_quarantined() -> None:
    invalid_id = FakeEntryPoint(
        "Invalid/Plugin",
        "fixture.plugin:plugin",
        distribution=FakeDistribution("fixture-invalid", "1.0.0"),
    )
    invalid_version = FakeEntryPoint(
        "fixture.invalid-version",
        "fixture.plugin:plugin",
        distribution=FakeDistribution("fixture-invalid", "1.0 unsafe"),
    )

    result = discover_entry_points([invalid_version, invalid_id])

    assert result.entries == ()
    assert [issue.code for issue in result.issues] == [
        "invalid_plugin_id",
        "missing_distribution",
    ]
    assert invalid_id.load_calls == invalid_version.load_calls == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"plugin_id": "Invalid/Plugin"},
        {"entry_point_value": "fixture.module"},
        {"distribution_name": "bad\nname"},
        {"distribution_version": "1.0 unsafe"},
    ],
)
def test_discovered_entry_point_rejects_unstable_direct_construction(
    kwargs: dict[str, str],
) -> None:
    values = {
        "plugin_id": "fixture.source",
        "entry_point_value": "fixture.plugin:plugin",
        "distribution_name": "fixture-dist",
        "distribution_version": "1.0.0",
        **kwargs,
    }
    with pytest.raises(ValueError):
        DiscoveredEntryPoint(**values)


@pytest.mark.parametrize(
    ("candidate", "code"),
    [
        (
            FakeEntryPoint(
                "fixture.invalid-value",
                "os:system --dangerous",
                distribution=FakeDistribution("fixture-invalid", "1.0.0"),
            ),
            "invalid_entry_point",
        ),
        (
            FakeEntryPoint(
                "fixture.no-attribute",
                "fixture.module",
                distribution=FakeDistribution("fixture-invalid", "1.0.0"),
            ),
            "invalid_entry_point",
        ),
        (
            FakeEntryPoint(
                "fixture.missing-dist",
                "fixture.module:plugin",
                distribution=None,
            ),
            "missing_distribution",
        ),
        (
            FakeEntryPoint(
                "fixture.missing-name",
                "fixture.module:plugin",
                distribution=FakeDistribution(None, "1.0.0"),
            ),
            "missing_distribution",
        ),
    ],
)
def test_invalid_entry_points_fail_closed_without_loading(
    candidate: FakeEntryPoint,
    code: str,
) -> None:
    result = discover_entry_points([candidate])

    assert result.entries == ()
    assert [issue.code for issue in result.issues] == [code]
    assert candidate.load_calls == 0


def test_manifest_identity_must_match_discovered_distribution() -> None:
    discovered = entry()
    valid = PluginManifest.model_validate(manifest_payload())
    validate_manifest_identity(discovered, valid)

    mismatched_plugin = PluginManifest.model_validate(
        manifest_payload(plugin_id="fixture.another")
    )
    with pytest.raises(ValueError, match="plugin ID"):
        validate_manifest_identity(discovered, mismatched_plugin)

    mismatched_distribution = PluginManifest.model_validate(
        manifest_payload(distribution_name="another-distribution")
    )
    with pytest.raises(ValueError, match="distribution"):
        validate_manifest_identity(discovered, mismatched_distribution)

    mismatched_version = PluginManifest.model_validate(
        manifest_payload(distribution_version="9.0.0")
    )
    with pytest.raises(ValueError, match="version"):
        validate_manifest_identity(discovered, mismatched_version)


def test_worker_resolves_the_module_attribute_only_inside_injected_importer() -> None:
    marker = object()
    imports: list[str] = []

    class Module:
        plugin = marker

    def importer(module_name: str) -> Any:
        imports.append(module_name)
        return Module

    assert resolve_entry_point("fixture.safe_module:plugin", importer=importer) is marker
    assert imports == ["fixture.safe_module"]
    with pytest.raises(ValueError, match="entry point"):
        resolve_entry_point("os:system --unsafe", importer=importer)
    assert imports == ["fixture.safe_module"]

    with pytest.raises(ValueError, match="attribute"):
        resolve_entry_point("fixture.safe_module:missing", importer=importer)


def test_installation_upsert_is_disabled_until_explicitly_enabled(tmp_path: Path) -> None:
    registry, _ = registry_for(tmp_path / "plugins.sqlite3")
    manifest = PluginManifest.model_validate(manifest_payload())
    installed = registry.upsert(entry(), manifest, discovered_at=datetime(2026, 7, 14, tzinfo=UTC))

    assert installed.plugin_id == "fixture.source"
    assert installed.enabled is False
    assert installed.compatible is True
    assert installed.manifest_sha256 == manifest.canonical_sha256()

    same = registry.upsert(
        entry(),
        manifest,
        discovered_at=datetime(2026, 7, 14, 1, tzinfo=UTC),
    )
    assert same.enabled is False


def test_incompatible_installation_cannot_be_enabled_and_unknown_ids_fail_closed(
    tmp_path: Path,
) -> None:
    registry, _ = registry_for(tmp_path / "incompatible.sqlite3")
    payload = manifest_payload()
    payload["protocol"] = {"minimum": "2.0", "maximum": "2.1"}
    manifest = PluginManifest.model_validate(payload)
    installed = registry.upsert(
        entry(),
        manifest,
        discovered_at=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert installed.compatible is False
    assert installed.compatibility_reason == "protocol_incompatible"
    with pytest.raises(ValueError, match="incompatible"):
        registry.set_enabled("fixture.source", True)
    with pytest.raises(KeyError, match="Unknown plugin"):
        registry.require("fixture.missing")
    with pytest.raises(ValueError, match="timezone"):
        registry.upsert(entry(), manifest, discovered_at=datetime(2026, 7, 14))


def test_manifest_or_version_change_invalidates_enablement_and_current_grants(
    tmp_path: Path,
) -> None:
    registry, _ = registry_for(tmp_path / "permissions.sqlite3")
    original = PluginManifest.model_validate(manifest_payload())
    registry.upsert(entry(), original, discovered_at=datetime(2026, 7, 14, tzinfo=UTC))
    permission = original.capabilities[0].permissions[0]
    decision = registry.record_permission_decision(
        "fixture.source",
        permission,
        decision="grant",
        decided_at=datetime(2026, 7, 14, 1, tzinfo=UTC),
    )
    registry.set_enabled("fixture.source", True)

    assert registry.effective_permissions("fixture.source") == (permission,)
    assert registry.require("fixture.source").enabled is True
    assert decision.resource == "api.example.org"

    updated_entry = entry(distribution_version="1.1.0")
    updated_manifest = PluginManifest.model_validate(
        manifest_payload(distribution_version="1.1.0", purpose="Read updated metadata")
    )
    updated = registry.upsert(
        updated_entry,
        updated_manifest,
        discovered_at=datetime(2026, 7, 14, 2, tzinfo=UTC),
    )

    assert updated.enabled is False
    assert registry.effective_permissions("fixture.source") == ()
    history = registry.permission_history("fixture.source")
    assert len(history) == 1
    assert history[0].manifest_sha256 == original.canonical_sha256()

    updated_permission = updated_manifest.capabilities[0].permissions[0]
    registry.record_permission_decision(
        "fixture.source",
        updated_permission,
        decision="grant",
        decided_at=datetime(2026, 7, 14, 3, tzinfo=UTC),
    )
    assert registry.effective_permissions("fixture.source") == (updated_permission,)
    registry.record_permission_decision(
        "fixture.source",
        updated_permission,
        decision="revoke",
        decided_at=datetime(2026, 7, 14, 4, tzinfo=UTC),
    )
    assert registry.effective_permissions("fixture.source") == ()
    assert len(registry.permission_history("fixture.source")) == 3
    assert [item.sequence for item in registry.permission_history("fixture.source")] == [1, 2, 3]


def test_permission_decisions_are_append_only_and_store_aliases_not_secret_values(
    tmp_path: Path,
) -> None:
    database = tmp_path / "append-only.sqlite3"
    registry, _ = registry_for(database)
    manifest = PluginManifest.model_validate(manifest_payload())
    registry.upsert(entry(), manifest, discovered_at=datetime(2026, 7, 14, tzinfo=UTC))
    permission = manifest.capabilities[0].permissions[0]
    recorded = registry.record_permission_decision(
        "fixture.source",
        permission,
        decision="grant",
        decided_at=datetime(2026, 7, 14, 1, tzinfo=UTC),
    )

    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(plugin_permission_decisions)")
        }
        row = connection.execute(
            "SELECT resource, decision FROM plugin_permission_decisions WHERE id = ?",
            (recorded.id,),
        ).fetchone()
        assert "secret" not in columns
        assert "value" not in columns
        assert row == ("api.example.org", "grant")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE plugin_permission_decisions SET decision = 'revoke' WHERE id = ?",
                (recorded.id,),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM plugin_permission_decisions WHERE id = ?",
                (recorded.id,),
            )

    with pytest.raises(ValueError, match="declared"):
        registry.record_permission_decision(
            "fixture.source",
            PermissionRequest(
                kind="network",
                resource="undeclared.example.org",
                purpose="Not declared by the installed manifest",
            ),
            decision="grant",
            decided_at=datetime.now(UTC) + timedelta(seconds=1),
        )

    with pytest.raises(ValueError, match="grant or revoke"):
        registry.record_permission_decision(
            "fixture.source",
            permission,
            decision="allow",  # type: ignore[arg-type]
            decided_at=datetime.now(UTC) + timedelta(seconds=2),
        )


def test_registry_rejects_a_tampered_stored_manifest_hash(tmp_path: Path) -> None:
    database = tmp_path / "tampered-registry.sqlite3"
    registry, _ = registry_for(database)
    manifest = PluginManifest.model_validate(manifest_payload())
    registry.upsert(entry(), manifest, discovered_at=datetime(2026, 7, 14, tzinfo=UTC))

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE plugin_installations SET manifest_sha256 = ? WHERE plugin_id = ?",
            ("f" * 64, "fixture.source"),
        )
        connection.commit()

    with pytest.raises(ValueError, match="hash"):
        registry.require("fixture.source")
