"""Persistent per-plugin health and circuit-breaker state."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.plugins.discovery import (
    PluginInstallation,
    PluginInstallationRegistry,
)


class PluginStateError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PluginHealthRepository:
    """Keep failures isolated to one installation and require explicit circuit reset."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        failure_threshold: int = 3,
    ) -> None:
        if failure_threshold < 1 or failure_threshold > 20:
            raise ValueError("failure_threshold must be between 1 and 20")
        self._sessions = sessions
        self._installations = PluginInstallationRegistry(sessions)
        self.failure_threshold = failure_threshold

    def require_callable(self, plugin_id: str) -> PluginInstallation:
        installation = self._installations.require(plugin_id)
        if not installation.compatible:
            raise PluginStateError("plugin_incompatible", "Plugin protocol is incompatible")
        if not installation.enabled:
            raise PluginStateError("plugin_disabled", "Plugin is disabled")
        if installation.circuit_open_reason is not None:
            raise PluginStateError("circuit_open", "Plugin circuit is open")
        return installation

    def record_success(self, plugin_id: str, *, called_at: datetime) -> PluginInstallation:
        timestamp = _utc_datetime(called_at)
        self._installations.require(plugin_id)
        with self._sessions.begin() as session:
            session.execute(
                text(
                    "UPDATE plugin_installations SET consecutive_failures = 0, "
                    "circuit_open_reason = NULL, last_called_at = :called_at, "
                    "updated_at = :called_at WHERE plugin_id = :plugin_id"
                ),
                {"called_at": timestamp, "plugin_id": plugin_id},
            )
        return self._installations.require(plugin_id)

    def record_failure(
        self,
        plugin_id: str,
        *,
        reason: str,
        called_at: datetime,
    ) -> PluginInstallation:
        if not reason or len(reason) > 100:
            raise ValueError("Plugin failure reason must be a bounded stable code")
        timestamp = _utc_datetime(called_at)
        with self._sessions.begin() as session:
            updated_plugin_id = session.execute(
                text(
                    "UPDATE plugin_installations SET "
                    "consecutive_failures = consecutive_failures + 1, "
                    "circuit_open_reason = CASE "
                    "WHEN consecutive_failures + 1 >= :failure_threshold "
                    "THEN :reason ELSE NULL END, last_called_at = :called_at, "
                    "updated_at = :called_at WHERE plugin_id = :plugin_id "
                    "RETURNING plugin_id"
                ),
                {
                    "failure_threshold": self.failure_threshold,
                    "reason": reason,
                    "called_at": timestamp,
                    "plugin_id": plugin_id,
                },
            ).scalar_one_or_none()
            if updated_plugin_id is None:
                raise KeyError(f"Unknown plugin installation: {plugin_id}")
        return self._installations.require(plugin_id)

    def reset_circuit(self, plugin_id: str, *, reset_at: datetime) -> PluginInstallation:
        timestamp = _utc_datetime(reset_at)
        self._installations.require(plugin_id)
        with self._sessions.begin() as session:
            session.execute(
                text(
                    "UPDATE plugin_installations SET consecutive_failures = 0, "
                    "circuit_open_reason = NULL, updated_at = :reset_at "
                    "WHERE plugin_id = :plugin_id"
                ),
                {"reset_at": timestamp, "plugin_id": plugin_id},
            )
        return self._installations.require(plugin_id)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Plugin health timestamps must include a timezone")
    return value.astimezone(UTC)
