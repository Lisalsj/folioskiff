"""Metadata-only plugin discovery and manifest-versioned installation records."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from typing import Literal, Protocol, cast
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.plugins.contracts import (
    PLUGIN_PROTOCOL_VERSION,
    PermissionKind,
    PermissionRequest,
    PluginManifest,
    is_protocol_compatible,
)

ENTRY_POINT_GROUP = "paper_reader.plugins"
_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,15}$")
_ENTRY_POINT_VALUE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
    r":[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
_SAFE_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,79}$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_DISTRIBUTION_NORMALIZER = re.compile(r"[-_.]+")


class _DistributionLike(Protocol):
    @property
    def metadata(self) -> Mapping[str, str]: ...

    @property
    def version(self) -> str: ...


class _EntryPointLike(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def value(self) -> str: ...

    @property
    def dist(self) -> _DistributionLike | None: ...


@dataclass(frozen=True, slots=True)
class DiscoveredEntryPoint:
    plugin_id: str
    entry_point_value: str
    distribution_name: str
    distribution_version: str

    def __post_init__(self) -> None:
        if not _PLUGIN_ID.fullmatch(self.plugin_id):
            raise ValueError("Plugin ID must be a stable lowercase identifier")
        if not _ENTRY_POINT_VALUE.fullmatch(self.entry_point_value):
            raise ValueError("Plugin entry point must use module:attribute syntax")
        if (
            not self.distribution_name.strip()
            or len(self.distribution_name) > 160
            or _CONTROL_CHARACTER.search(self.distribution_name)
        ):
            raise ValueError("Plugin distribution name is invalid")
        if not _SAFE_VERSION.fullmatch(self.distribution_version):
            raise ValueError("Plugin distribution version is invalid")


@dataclass(frozen=True, slots=True)
class DiscoveryIssue:
    plugin_id: str | None
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    entries: tuple[DiscoveredEntryPoint, ...]
    issues: tuple[DiscoveryIssue, ...]


@dataclass(frozen=True, slots=True)
class PluginInstallation:
    plugin_id: str
    entry_point_value: str
    distribution_name: str
    distribution_version: str
    manifest: PluginManifest
    manifest_sha256: str
    compatible: bool
    compatibility_reason: str | None
    enabled: bool
    consecutive_failures: int
    circuit_open_reason: str | None
    last_discovered_at: datetime
    last_called_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    sequence: int
    id: str
    plugin_id: str
    distribution_version: str
    manifest_sha256: str
    permission_kind: PermissionKind
    resource: str
    decision: Literal["grant", "revoke"]
    decided_at: datetime


def discover_plugins() -> DiscoveryResult:
    selected = metadata.entry_points(group=ENTRY_POINT_GROUP)
    return discover_entry_points(cast(Iterable[_EntryPointLike], selected))


def discover_entry_points(entry_points: Iterable[_EntryPointLike]) -> DiscoveryResult:
    candidates = tuple(entry_points)
    counts = Counter(
        candidate.name for candidate in candidates if isinstance(candidate.name, str)
    )
    duplicate_ids = {
        plugin_id
        for plugin_id, count in counts.items()
        if count > 1 and _PLUGIN_ID.fullmatch(plugin_id)
    }
    issues: list[DiscoveryIssue] = [
        DiscoveryIssue(
            plugin_id=plugin_id,
            code="duplicate_plugin_id",
            message="Multiple installed entry points declare the same plugin ID",
        )
        for plugin_id in duplicate_ids
    ]
    entries: list[DiscoveredEntryPoint] = []
    ordered = sorted(candidates, key=_entry_point_sort_key)
    for candidate in ordered:
        plugin_id = candidate.name if isinstance(candidate.name, str) else None
        if plugin_id in duplicate_ids:
            continue
        if plugin_id is None or not _PLUGIN_ID.fullmatch(plugin_id):
            issues.append(
                DiscoveryIssue(
                    plugin_id=None,
                    code="invalid_plugin_id",
                    message="An installed plugin entry point has an invalid ID",
                )
            )
            continue
        value = candidate.value
        if not isinstance(value, str) or not _ENTRY_POINT_VALUE.fullmatch(value):
            issues.append(
                DiscoveryIssue(
                    plugin_id=plugin_id,
                    code="invalid_entry_point",
                    message="Plugin entry point must use safe module:attribute syntax",
                )
            )
            continue
        distribution = candidate.dist
        if distribution is None:
            issues.append(_missing_distribution(plugin_id))
            continue
        try:
            distribution_name = distribution.metadata.get("Name")
            distribution_version = distribution.version
        except (AttributeError, KeyError, TypeError):
            issues.append(_missing_distribution(plugin_id))
            continue
        if (
            not isinstance(distribution_name, str)
            or not distribution_name.strip()
            or len(distribution_name) > 160
            or _CONTROL_CHARACTER.search(distribution_name)
            or not isinstance(distribution_version, str)
            or not _SAFE_VERSION.fullmatch(distribution_version)
        ):
            issues.append(_missing_distribution(plugin_id))
            continue
        entries.append(
            DiscoveredEntryPoint(
                plugin_id=plugin_id,
                entry_point_value=value,
                distribution_name=distribution_name.strip(),
                distribution_version=distribution_version,
            )
        )
    return DiscoveryResult(
        entries=tuple(sorted(entries, key=lambda item: item.plugin_id)),
        issues=tuple(sorted(issues, key=lambda item: (item.plugin_id or "", item.code))),
    )


def validate_manifest_identity(
    discovered: DiscoveredEntryPoint,
    manifest: PluginManifest,
) -> None:
    if discovered.plugin_id != manifest.plugin_id:
        raise ValueError("Worker manifest plugin ID does not match its entry point")
    if _normalized_distribution(discovered.distribution_name) != _normalized_distribution(
        manifest.distribution_name
    ):
        raise ValueError("Worker manifest distribution does not match installed metadata")
    if discovered.distribution_version != manifest.distribution_version:
        raise ValueError("Worker manifest version does not match installed distribution version")


class PluginInstallationRegistry:
    """Persist discovery identity while keeping permission history manifest-scoped."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def upsert(
        self,
        discovered: DiscoveredEntryPoint,
        manifest: PluginManifest,
        *,
        discovered_at: datetime,
    ) -> PluginInstallation:
        validate_manifest_identity(discovered, manifest)
        timestamp = _utc_datetime(discovered_at)
        manifest_json = manifest.canonical_bytes().decode("utf-8")
        manifest_hash = manifest.canonical_sha256()
        compatible = is_protocol_compatible(
            manifest.protocol,
            host_version=PLUGIN_PROTOCOL_VERSION,
        )
        reason = None if compatible else "protocol_incompatible"
        with self._sessions.begin() as session:
            existing = session.execute(
                text("SELECT * FROM plugin_installations WHERE plugin_id = :plugin_id"),
                {"plugin_id": discovered.plugin_id},
            ).mappings().first()
            if existing is None:
                session.execute(
                    text(
                        "INSERT INTO plugin_installations (plugin_id, entry_point_value, "
                        "distribution_name, distribution_version, manifest_json, "
                        "manifest_sha256, protocol_minimum, protocol_maximum, compatible, "
                        "compatibility_reason, enabled, consecutive_failures, "
                        "circuit_open_reason, last_discovered_at, last_called_at, created_at, "
                        "updated_at) VALUES (:plugin_id, :entry_point_value, "
                        ":distribution_name, :distribution_version, :manifest_json, "
                        ":manifest_sha256, :protocol_minimum, :protocol_maximum, :compatible, "
                        ":compatibility_reason, 0, 0, NULL, :last_discovered_at, NULL, "
                        ":created_at, :updated_at)"
                    ),
                    {
                        **_installation_parameters(discovered, manifest, manifest_json),
                        "manifest_sha256": manifest_hash,
                        "compatible": compatible,
                        "compatibility_reason": reason,
                        "last_discovered_at": timestamp,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    },
                )
            else:
                identity_changed = any(
                    (
                        existing["entry_point_value"] != discovered.entry_point_value,
                        _normalized_distribution(str(existing["distribution_name"]))
                        != _normalized_distribution(discovered.distribution_name),
                        existing["distribution_version"] != discovered.distribution_version,
                        existing["manifest_sha256"] != manifest_hash,
                    )
                )
                enabled = bool(existing["enabled"]) and compatible and not identity_changed
                session.execute(
                    text(
                        "UPDATE plugin_installations SET entry_point_value = :entry_point_value, "
                        "distribution_name = :distribution_name, "
                        "distribution_version = :distribution_version, "
                        "manifest_json = :manifest_json, manifest_sha256 = :manifest_sha256, "
                        "protocol_minimum = :protocol_minimum, "
                        "protocol_maximum = :protocol_maximum, "
                        "compatible = :compatible, compatibility_reason = :compatibility_reason, "
                        "enabled = :enabled, consecutive_failures = :consecutive_failures, "
                        "circuit_open_reason = :circuit_open_reason, "
                        "last_discovered_at = :last_discovered_at, updated_at = :updated_at "
                        "WHERE plugin_id = :plugin_id"
                    ),
                    {
                        **_installation_parameters(discovered, manifest, manifest_json),
                        "manifest_sha256": manifest_hash,
                        "compatible": compatible,
                        "compatibility_reason": reason,
                        "enabled": enabled,
                        "consecutive_failures": (
                            0 if identity_changed else int(existing["consecutive_failures"])
                        ),
                        "circuit_open_reason": (
                            None if identity_changed else existing["circuit_open_reason"]
                        ),
                        "last_discovered_at": timestamp,
                        "updated_at": timestamp,
                    },
                )
            stored = session.execute(
                text("SELECT * FROM plugin_installations WHERE plugin_id = :plugin_id"),
                {"plugin_id": discovered.plugin_id},
            ).mappings().one()
            return _installation_from_row(stored)

    def require(self, plugin_id: str) -> PluginInstallation:
        with self._sessions() as session:
            row = session.execute(
                text("SELECT * FROM plugin_installations WHERE plugin_id = :plugin_id"),
                {"plugin_id": plugin_id},
            ).mappings().first()
            if row is None:
                raise KeyError(f"Unknown plugin installation: {plugin_id}")
            return _installation_from_row(row)

    def list_all(self) -> tuple[PluginInstallation, ...]:
        """Return metadata-only installation records in deterministic ID order."""

        with self._sessions() as session:
            rows = session.execute(
                text("SELECT * FROM plugin_installations ORDER BY plugin_id")
            ).mappings()
            return tuple(_installation_from_row(row) for row in rows)

    def quarantine_unverified(
        self,
        plugin_id: str,
        *,
        reason: Literal["plugin_not_installed", "plugin_not_verified"],
        changed_at: datetime,
    ) -> PluginInstallation:
        """Disable stale code and revoke current grants while preserving audit history."""

        timestamp = _utc_datetime(changed_at)
        current = self.require(plugin_id)
        for permission in self.effective_permissions(plugin_id):
            self.record_permission_decision(
                plugin_id,
                permission,
                decision="revoke",
                decided_at=timestamp,
            )
        with self._sessions.begin() as session:
            session.execute(
                text(
                    "UPDATE plugin_installations SET compatible = 0, "
                    "compatibility_reason = :reason, enabled = 0, "
                    "consecutive_failures = 0, circuit_open_reason = NULL, "
                    "updated_at = :updated_at WHERE plugin_id = :plugin_id"
                ),
                {
                    "reason": reason,
                    "updated_at": timestamp,
                    "plugin_id": current.plugin_id,
                },
            )
        return self.require(plugin_id)

    def set_enabled(self, plugin_id: str, enabled: bool) -> PluginInstallation:
        current = self.require(plugin_id)
        if enabled and not current.compatible:
            raise ValueError("An incompatible plugin cannot be enabled")
        changed_at = datetime.now(UTC)
        with self._sessions.begin() as session:
            session.execute(
                text(
                    "UPDATE plugin_installations SET enabled = :enabled, updated_at = :updated_at "
                    "WHERE plugin_id = :plugin_id"
                ),
                {"enabled": enabled, "updated_at": changed_at, "plugin_id": plugin_id},
            )
        return self.require(plugin_id)

    def record_permission_decision(
        self,
        plugin_id: str,
        permission: PermissionRequest,
        *,
        decision: Literal["grant", "revoke"],
        decided_at: datetime,
    ) -> PermissionDecision:
        if decision not in {"grant", "revoke"}:
            raise ValueError("Plugin permission decision must be grant or revoke")
        current = self.require(plugin_id)
        declared = {
            (item.kind, item.resource): item for item in current.manifest.requested_permissions()
        }
        if (permission.kind, permission.resource) not in declared:
            raise ValueError("Permission was not declared by the current plugin manifest")
        decision_id = str(uuid4())
        timestamp = _utc_datetime(decided_at)
        with self._sessions.begin() as session:
            session.execute(
                text(
                    "INSERT INTO plugin_permission_decisions (id, plugin_id, "
                    "distribution_version, manifest_sha256, permission_kind, resource, "
                    "decision, decided_at) VALUES (:id, :plugin_id, :distribution_version, "
                    ":manifest_sha256, :permission_kind, :resource, :decision, :decided_at)"
                ),
                {
                    "id": decision_id,
                    "plugin_id": plugin_id,
                    "distribution_version": current.distribution_version,
                    "manifest_sha256": current.manifest_sha256,
                    "permission_kind": permission.kind.value,
                    "resource": permission.resource,
                    "decision": decision,
                    "decided_at": timestamp,
                },
            )
            stored = session.execute(
                text("SELECT * FROM plugin_permission_decisions WHERE id = :id"),
                {"id": decision_id},
            ).mappings().one()
            return _permission_from_row(stored)

    def permission_history(self, plugin_id: str) -> tuple[PermissionDecision, ...]:
        self.require(plugin_id)
        with self._sessions() as session:
            rows = session.execute(
                text(
                    "SELECT * FROM plugin_permission_decisions WHERE plugin_id = :plugin_id "
                    "ORDER BY sequence"
                ),
                {"plugin_id": plugin_id},
            ).mappings()
            return tuple(_permission_from_row(row) for row in rows)

    def effective_permissions(self, plugin_id: str) -> tuple[PermissionRequest, ...]:
        current = self.require(plugin_id)
        declared = {
            (item.kind, item.resource): item for item in current.manifest.requested_permissions()
        }
        relevant = tuple(
            decision
            for decision in self.permission_history(plugin_id)
            if decision.distribution_version == current.distribution_version
            and decision.manifest_sha256 == current.manifest_sha256
        )
        latest: dict[tuple[PermissionKind, str], PermissionDecision] = {}
        for decision in relevant:
            latest[(decision.permission_kind, decision.resource)] = decision
        granted = [
            declared[key]
            for key, decision in latest.items()
            if decision.decision == "grant" and key in declared
        ]
        return tuple(sorted(granted, key=lambda item: (item.kind.value, item.resource)))


def _entry_point_sort_key(candidate: _EntryPointLike) -> tuple[str, str, str, str]:
    name = candidate.name if isinstance(candidate.name, str) else ""
    value = candidate.value if isinstance(candidate.value, str) else ""
    distribution = candidate.dist
    if distribution is None:
        return name, value, "", ""
    try:
        distribution_name = distribution.metadata.get("Name", "")
        version = distribution.version
    except (AttributeError, KeyError, TypeError):
        return name, value, "", ""
    return name, value, str(distribution_name), str(version)


def _missing_distribution(plugin_id: str) -> DiscoveryIssue:
    return DiscoveryIssue(
        plugin_id=plugin_id,
        code="missing_distribution",
        message="Plugin entry point is missing trustworthy distribution metadata",
    )


def _normalized_distribution(value: str) -> str:
    return _DISTRIBUTION_NORMALIZER.sub("-", value).casefold()


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Plugin audit timestamps must include a timezone")
    return value.astimezone(UTC)


def _installation_parameters(
    discovered: DiscoveredEntryPoint,
    manifest: PluginManifest,
    manifest_json: str,
) -> dict[str, object]:
    return {
        "plugin_id": discovered.plugin_id,
        "entry_point_value": discovered.entry_point_value,
        "distribution_name": discovered.distribution_name,
        "distribution_version": discovered.distribution_version,
        "manifest_json": manifest_json,
        "protocol_minimum": manifest.protocol.minimum,
        "protocol_maximum": manifest.protocol.maximum,
    }


def _installation_from_row(row: RowMapping) -> PluginInstallation:
    manifest = PluginManifest.model_validate(json.loads(str(row["manifest_json"])))
    manifest_hash = str(row["manifest_sha256"])
    if manifest.canonical_sha256() != manifest_hash:
        raise ValueError("Stored plugin manifest hash does not match its canonical manifest")
    return PluginInstallation(
        plugin_id=str(row["plugin_id"]),
        entry_point_value=str(row["entry_point_value"]),
        distribution_name=str(row["distribution_name"]),
        distribution_version=str(row["distribution_version"]),
        manifest=manifest,
        manifest_sha256=manifest_hash,
        compatible=bool(row["compatible"]),
        compatibility_reason=(
            str(row["compatibility_reason"]) if row["compatibility_reason"] is not None else None
        ),
        enabled=bool(row["enabled"]),
        consecutive_failures=int(row["consecutive_failures"]),
        circuit_open_reason=(
            str(row["circuit_open_reason"]) if row["circuit_open_reason"] is not None else None
        ),
        last_discovered_at=_row_datetime(row["last_discovered_at"]),
        last_called_at=(
            _row_datetime(row["last_called_at"]) if row["last_called_at"] is not None else None
        ),
        created_at=_row_datetime(row["created_at"]),
        updated_at=_row_datetime(row["updated_at"]),
    )


def _permission_from_row(row: RowMapping) -> PermissionDecision:
    decision = str(row["decision"])
    if decision not in {"grant", "revoke"}:
        raise ValueError("Stored plugin permission decision is invalid")
    return PermissionDecision(
        sequence=int(row["sequence"]),
        id=str(row["id"]),
        plugin_id=str(row["plugin_id"]),
        distribution_version=str(row["distribution_version"]),
        manifest_sha256=str(row["manifest_sha256"]),
        permission_kind=PermissionKind(str(row["permission_kind"])),
        resource=str(row["resource"]),
        decision=cast(Literal["grant", "revoke"], decision),
        decided_at=_row_datetime(row["decided_at"]),
    )


def _row_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Stored plugin timestamp is invalid")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
