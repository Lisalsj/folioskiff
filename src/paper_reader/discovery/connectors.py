"""Stable source-connector contract shared by preview and resumable plan runs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

from paper_reader.discovery.identity import SourcePaper
from paper_reader.discovery.keywords import CompiledKeywordQuery

CredentialRequirement = Literal["none", "api_key", "optional_account"]


@dataclass(frozen=True, slots=True)
class ConnectorCapabilities:
    connector_id: str
    display_name: str
    credential_requirement: CredentialRequirement
    supports_exact_updated_date: bool
    enabled: bool = True
    disabled_reason: str | None = None
    credential_configured: bool = False
    setup_url: str | None = None
    protocol_version: str = "1"


@dataclass(frozen=True, slots=True)
class ConnectorEstimate:
    total: int | None
    exact: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceQuery:
    connector_id: str
    months: tuple[str, ...]
    compiled_keywords: CompiledKeywordQuery
    filters: dict[str, object]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class SourcePage:
    records: tuple[SourcePaper, ...]
    next_cursor: str | None
    total: int | None = None


class ConnectorRequestError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class SourceConnector(Protocol):
    capabilities: ConnectorCapabilities

    async def estimate(self, query: SourceQuery) -> ConnectorEstimate: ...

    async def fetch_page(
        self,
        query: SourceQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> SourcePage: ...


class ConnectorRegistry:
    def __init__(self, connectors: Iterable[SourceConnector] = ()) -> None:
        self._connectors: dict[str, SourceConnector] = {}
        for connector in connectors:
            self.register(connector)
        self._builtin_connectors = dict(self._connectors)

    def register(self, connector: SourceConnector) -> None:
        connector_id = connector.capabilities.connector_id
        if connector_id in self._connectors:
            raise ValueError(f"Duplicate connector ID: {connector_id}")
        self._connectors[connector_id] = connector

    def replace_extensions(
        self,
        connectors: Iterable[SourceConnector],
    ) -> tuple[str, ...]:
        extensions: dict[str, SourceConnector] = {}
        conflicts: list[str] = []
        for connector in connectors:
            connector_id = connector.capabilities.connector_id
            if connector_id in self._builtin_connectors or connector_id in extensions:
                conflicts.append(connector_id)
                continue
            extensions[connector_id] = connector
        self._connectors = {**self._builtin_connectors, **extensions}
        return tuple(sorted(conflicts))

    def replace_builtins(self, connectors: Iterable[SourceConnector]) -> tuple[str, ...]:
        replacements: dict[str, SourceConnector] = {}
        for connector in connectors:
            connector_id = connector.capabilities.connector_id
            if connector_id in replacements:
                raise ValueError(f"Duplicate connector ID: {connector_id}")
            replacements[connector_id] = connector
        extensions = {
            connector_id: connector
            for connector_id, connector in self._connectors.items()
            if connector_id not in self._builtin_connectors
        }
        conflicts = tuple(sorted(set(replacements) & set(extensions)))
        self._builtin_connectors = replacements
        self._connectors = {
            **replacements,
            **{key: value for key, value in extensions.items() if key not in replacements},
        }
        return conflicts

    def get(self, connector_id: str) -> SourceConnector | None:
        return self._connectors.get(connector_id)

    def all(self) -> tuple[SourceConnector, ...]:
        return tuple(self._connectors.values())
