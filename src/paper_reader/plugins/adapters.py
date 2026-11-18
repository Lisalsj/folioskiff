"""Strict domain adapters for approved, isolated plugin capabilities.

Plugin code never receives these objects.  Each adapter serializes a bounded
domain request, asks :class:`PluginHost` to cross the process boundary, and
validates the complete untrusted result before constructing a domain object.
"""

from __future__ import annotations

import base64
import json
import math
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Protocol, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)

from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentCapability,
    AgentErrorCode,
    AgentFragment,
    AgentLocation,
    AgentModel,
    AgentProbeCode,
    AgentRequest,
    AgentResult,
    AgentUsage,
    ModelCatalog,
)
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.discovery.connectors import (
    ConnectorCapabilities,
    ConnectorEstimate,
    ConnectorRegistry,
    ConnectorRequestError,
    SourcePage,
    SourceQuery,
)
from paper_reader.discovery.identity import SourcePaper
from paper_reader.plugins.contracts import (
    PLUGIN_PROTOCOL_VERSION,
    CapabilityKind,
    PermissionRequest,
    PluginCapability,
)
from paper_reader.plugins.discovery import PluginInstallation
from paper_reader.plugins.host import PluginHostError, PluginInvocationResult
from paper_reader.recommendation.enrichers.registry import EnricherRegistry
from paper_reader.recommendation.enrichment import (
    EnricherCapabilities,
    EnricherRequestError,
    EnrichmentResult,
    EnrichmentStatus,
    RateLimitObservation,
    WorkLookup,
    canonical_response_sha256,
)
from paper_reader.recommendation.relevance import EvidenceStatus, MetricEvidence

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_MAX_ADAPTER_JSON_BYTES = 768 * 1024
_MAX_INLINE_ATTACHMENT_BYTES = 512 * 1024


class PluginAdapterError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class PluginInvoker(Protocol):
    async def invoke(
        self,
        plugin_id: str,
        *,
        capability_id: str,
        method: str,
        payload: dict[str, Any],
        deadline: datetime,
        cancelled: Callable[[], bool],
    ) -> PluginInvocationResult: ...

    def record_contract_failure(self, plugin_id: str) -> None: ...


class PermissionView(Protocol):
    def effective_permissions(self, plugin_id: str) -> tuple[PermissionRequest, ...]: ...


class _StrictResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _SourceEstimateResult(_StrictResult):
    total: int | None = Field(default=None, ge=0)
    exact: bool
    warnings: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("warnings")
    @classmethod
    def bounded_warnings(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_bounded_text(value, field_name="warning", maximum=500) for value in values)


class _SourceRecordResult(_StrictResult):
    external_id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=20_000)
    authors: tuple[str, ...] = Field(default=(), max_length=500)
    abstract: str | None = Field(default=None, max_length=2_000_000)
    doi: str | None = Field(default=None, max_length=500)
    published_at: datetime | None = None
    updated_at: datetime | None = None
    landing_url: HttpUrl | None = None
    field_provenance: dict[str, str] = Field(default_factory=dict)
    raw_payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("external_id", "title")
    @classmethod
    def nonblank_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source identity fields cannot be blank")
        return normalized

    @field_validator("authors")
    @classmethod
    def bounded_authors(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_bounded_text(value, field_name="author", maximum=500) for value in values)

    @field_validator("published_at", "updated_at")
    @classmethod
    def aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("plugin paper timestamps must include a timezone")
        return value.astimezone(UTC)

    @field_validator("field_provenance")
    @classmethod
    def bounded_provenance(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 256:
            raise ValueError("source provenance contains too many fields")
        return {
            _bounded_text(key, field_name="provenance field", maximum=160): _bounded_text(
                path, field_name="provenance path", maximum=500
            )
            for key, path in value.items()
        }


class _SourcePageResult(_StrictResult):
    records: tuple[_SourceRecordResult, ...] = Field(max_length=500)
    next_cursor: str | None = Field(default=None, max_length=2000)
    total: int | None = Field(default=None, ge=0)


class _RateLimitResult(_StrictResult):
    limit: int | None = Field(default=None, ge=0)
    remaining: int | None = Field(default=None, ge=0)
    reset_after_seconds: float | None = Field(default=None, ge=0)
    interval_seconds: float | None = Field(default=None, ge=0)


class _EnrichmentResult(_StrictResult):
    status: EnrichmentStatus
    external_id: str | None = Field(default=None, max_length=500)
    matched_by: str | None = Field(default=None, max_length=160)
    fields: dict[str, JsonValue]
    field_provenance: dict[str, str]
    warnings: tuple[str, ...] = Field(default=(), max_length=32)
    rate_limit: _RateLimitResult | None = None

    @model_validator(mode="after")
    def valid_status_payload(self) -> _EnrichmentResult:
        if set(self.fields) != set(self.field_provenance):
            raise ValueError("every enrichment field requires one provenance path")
        if self.status is EnrichmentStatus.OBSERVED:
            if not self.external_id or not self.matched_by:
                raise ValueError("observed enrichment requires external identity")
        elif self.external_id is not None or self.matched_by is not None or self.fields:
            raise ValueError("non-observed enrichment cannot claim observed fields")
        return self

    @field_validator("warnings")
    @classmethod
    def bounded_warnings(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_bounded_text(value, field_name="warning", maximum=500) for value in values)

    @field_validator("field_provenance")
    @classmethod
    def bounded_provenance(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 512:
            raise ValueError("enrichment provenance contains too many fields")
        return {
            _bounded_text(key, field_name="field", maximum=160): _bounded_text(
                path, field_name="provenance path", maximum=500
            )
            for key, path in value.items()
        }


class _ScoreResult(_StrictResult):
    status: EvidenceStatus
    value: float | None = None
    confidence: float = Field(default=100, ge=0, le=100)
    reason: str | None = Field(default=None, max_length=500)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_metric(self) -> _ScoreResult:
        if self.status is EvidenceStatus.OBSERVED:
            if self.value is None or not math.isfinite(self.value) or not 0 <= self.value <= 100:
                raise ValueError("observed plugin score must be between 0 and 100")
        elif self.value is not None:
            raise ValueError("unknown or not-applicable score cannot contain a value")
        if self.status is not EvidenceStatus.OBSERVED and not self.reason:
            raise ValueError("non-observed plugin score requires a reason")
        return self


class _AgentModelResult(_StrictResult):
    model_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    supports_vision: bool | None = None
    context_window: int | None = Field(default=None, ge=1)


class _ModelCatalogResult(_StrictResult):
    models: tuple[_AgentModelResult, ...] = Field(default=(), max_length=500)
    supports_manual_model_id: bool = True
    supports_default_model: bool = False
    degradation_reasons: tuple[str, ...] = Field(default=(), max_length=32)


class _AgentUsageResult(_StrictResult):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class _AgentGenerationResult(_StrictResult):
    raw_output: str = Field(max_length=4_000_000)
    structured_output: dict[str, JsonValue]
    resolved_model_id: str | None = Field(default=None, max_length=200)
    usage: _AgentUsageResult = _AgentUsageResult()


class _AgentFragmentResult(_StrictResult):
    index: int = Field(ge=0)
    kind: str
    text: str = Field(max_length=1_000_000)

    @field_validator("kind")
    @classmethod
    def known_kind(cls, value: str) -> str:
        if value not in {"text", "reasoning", "diagnostic"}:
            raise ValueError("unknown Agent fragment kind")
        return value


class _PublisherResult(_StrictResult):
    publication_id: str = Field(min_length=1, max_length=200)
    public_url: HttpUrl | None = None
    provenance: dict[str, JsonValue] = Field(default_factory=dict)


class _AdapterBase:
    __slots__ = ("_capability", "_clock", "_display_name", "_host", "_plugin_id")

    required_methods: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        plugin_id: str,
        display_name: str,
        capability: PluginCapability,
        host: PluginInvoker,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        missing = self.required_methods - set(capability.methods)
        if missing:
            raise PluginAdapterError(
                "missing_required_method",
                f"Plugin capability is missing required method: {sorted(missing)[0]}",
            )
        self._plugin_id = plugin_id
        self._display_name = display_name
        self._capability = capability
        self._host = host
        self._clock = clock

    async def _invoke(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        cancelled: Callable[[], bool] = lambda: False,
        timeout_seconds: float | None = None,
    ) -> PluginInvocationResult:
        _bounded_json(payload)
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("plugin adapter clock must return a timezone-aware datetime")
        timeout = min(float(self._capability.timeout_seconds), timeout_seconds or math.inf)
        return await self._host.invoke(
            self._plugin_id,
            capability_id=self._capability.capability_id,
            method=method,
            payload=payload,
            deadline=now.astimezone(UTC) + timedelta(seconds=timeout),
            cancelled=cancelled,
        )

    def _invalid_result(self) -> None:
        self._host.record_contract_failure(self._plugin_id)


class PluginSourceConnector(_AdapterBase):
    __slots__ = ("capabilities",)
    required_methods = frozenset({"estimate", "fetch_page"})

    def __init__(
        self,
        *,
        plugin_id: str,
        display_name: str,
        capability: PluginCapability,
        host: PluginInvoker,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if capability.kind is not CapabilityKind.SOURCE:
            raise PluginAdapterError("capability_kind", "Expected a source connector capability")
        super().__init__(
            plugin_id=plugin_id,
            display_name=display_name,
            capability=capability,
            host=host,
            clock=clock,
        )
        self.capabilities = ConnectorCapabilities(
            connector_id=capability.capability_id,
            display_name=display_name,
            credential_requirement="none",
            supports_exact_updated_date=False,
            enabled=True,
            credential_configured=bool(capability.permissions),
            protocol_version=PLUGIN_PROTOCOL_VERSION,
        )

    async def estimate(self, query: SourceQuery) -> ConnectorEstimate:
        self._require_query_identity(query)
        try:
            invoked = await self._invoke("estimate", {"query": _source_query_payload(query)})
            parsed = _SourceEstimateResult.model_validate(invoked.result)
            return ConnectorEstimate(
                total=parsed.total,
                exact=parsed.exact,
                warnings=parsed.warnings,
            )
        except PluginHostError as error:
            raise _connector_error(error) from error
        except (ValidationError, ValueError, TypeError) as error:
            self._invalid_result()
            raise ConnectorRequestError(
                "invalid_plugin_result",
                "Plugin source returned an invalid estimate",
                retryable=False,
            ) from error

    async def fetch_page(
        self,
        query: SourceQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> SourcePage:
        self._require_query_identity(query)
        if not 1 <= limit <= 500:
            raise ValueError("plugin source page limit must be between 1 and 500")
        if cursor is not None and len(cursor) > 2000:
            raise ValueError("plugin source cursor exceeds the host limit")
        try:
            invoked = await self._invoke(
                "fetch_page",
                {
                    "query": _source_query_payload(query),
                    "cursor": cursor,
                    "limit": limit,
                },
            )
            parsed = _SourcePageResult.model_validate(invoked.result)
            return SourcePage(
                records=tuple(
                    SourcePaper(
                        connector_id=self.capabilities.connector_id,
                        external_id=record.external_id,
                        title=record.title,
                        authors=record.authors,
                        abstract=record.abstract,
                        doi=record.doi,
                        published_at=record.published_at,
                        updated_at=record.updated_at,
                        landing_url=(str(record.landing_url) if record.landing_url else None),
                        field_provenance=dict(record.field_provenance),
                        raw_payload=dict(record.raw_payload),
                    )
                    for record in parsed.records
                ),
                next_cursor=parsed.next_cursor,
                total=parsed.total,
            )
        except PluginHostError as error:
            raise _connector_error(error) from error
        except (ValidationError, ValueError, TypeError) as error:
            self._invalid_result()
            raise ConnectorRequestError(
                "invalid_plugin_result",
                "Plugin source returned an invalid page",
                retryable=False,
            ) from error

    def _require_query_identity(self, query: SourceQuery) -> None:
        if query.connector_id != self.capabilities.connector_id:
            raise ValueError("source query connector does not match plugin capability")


class PluginMetadataEnricher(_AdapterBase):
    __slots__ = ("capabilities", "_distribution_version")
    required_methods = frozenset({"enrich"})

    def __init__(
        self,
        *,
        plugin_id: str,
        display_name: str,
        distribution_version: str,
        capability: PluginCapability,
        host: PluginInvoker,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if capability.kind is not CapabilityKind.METADATA:
            raise PluginAdapterError("capability_kind", "Expected a metadata enricher capability")
        super().__init__(
            plugin_id=plugin_id,
            display_name=display_name,
            capability=capability,
            host=host,
            clock=clock,
        )
        self._distribution_version = distribution_version
        self.capabilities = EnricherCapabilities(
            enricher_id=capability.capability_id,
            display_name=display_name,
            version=distribution_version,
            enabled=True,
            disabled_reason=None,
            credential_requirement="host_broker" if capability.permissions else "none",
            credential_configured=True,
            setup_url="",
            default_requests_per_second=None,
            max_concurrency=1,
        )

    async def enrich(self, work: WorkLookup) -> EnrichmentResult:
        try:
            invoked = await self._invoke(
                "enrich",
                {
                    "work": {
                        "work_id": work.work_id,
                        "doi": work.doi,
                        "title": work.title,
                        "authors": list(work.authors),
                        "source_ids": dict(work.source_ids),
                    }
                },
            )
            parsed = _EnrichmentResult.model_validate(invoked.result)
            rate_limit = (
                RateLimitObservation(**parsed.rate_limit.model_dump())
                if parsed.rate_limit is not None
                else None
            )
            response_hash = (
                canonical_response_sha256(parsed.model_dump(mode="json"))
                if parsed.status is EnrichmentStatus.OBSERVED
                else None
            )
            return EnrichmentResult(
                work_id=work.work_id,
                enricher_id=self.capabilities.enricher_id,
                enricher_version=self._distribution_version,
                status=parsed.status,
                external_id=parsed.external_id,
                matched_by=parsed.matched_by,
                fields=dict(parsed.fields),
                field_provenance=dict(parsed.field_provenance),
                warnings=parsed.warnings,
                fetched_at=self._clock(),
                response_sha256=response_hash,
                rate_limit=rate_limit,
            )
        except PluginHostError as error:
            code = error.plugin_error_code or error.code
            raise EnricherRequestError(
                code,
                "Plugin metadata enricher request failed",
                retryable=error.retryable,
            ) from error
        except (ValidationError, ValueError, TypeError) as error:
            self._invalid_result()
            raise EnricherRequestError(
                "invalid_plugin_result",
                "Plugin metadata enricher returned invalid evidence",
                retryable=False,
            ) from error


@dataclass(frozen=True, slots=True)
class ScoreEvidence:
    work_id: str
    title: str
    fields: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_id", _bounded_text(self.work_id, "work_id", 160))
        object.__setattr__(self, "title", _bounded_text(self.title, "title", 20_000))
        copied = _bounded_json(dict(self.fields))
        object.__setattr__(self, "fields", MappingProxyType(copied))


@dataclass(frozen=True, slots=True)
class ScoreDimensionCapabilities:
    dimension_id: str
    display_name: str
    version: str


class PluginScoreDimension(_AdapterBase):
    __slots__ = ("capabilities",)
    required_methods = frozenset({"evaluate"})

    def __init__(
        self,
        *,
        plugin_id: str,
        display_name: str,
        distribution_version: str,
        capability: PluginCapability,
        host: PluginInvoker,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if capability.kind is not CapabilityKind.SCORE:
            raise PluginAdapterError("capability_kind", "Expected a score dimension capability")
        super().__init__(
            plugin_id=plugin_id,
            display_name=display_name,
            capability=capability,
            host=host,
            clock=clock,
        )
        self.capabilities = ScoreDimensionCapabilities(
            dimension_id=capability.capability_id,
            display_name=display_name,
            version=distribution_version,
        )

    async def evaluate(self, evidence: ScoreEvidence) -> MetricEvidence:
        try:
            invoked = await self._invoke(
                "evaluate",
                {
                    "evidence": {
                        "work_id": evidence.work_id,
                        "title": evidence.title,
                        "fields": dict(evidence.fields),
                    }
                },
            )
            parsed = _ScoreResult.model_validate(invoked.result)
            return MetricEvidence(
                status=parsed.status,
                value=parsed.value,
                confidence=parsed.confidence,
                reason=parsed.reason,
                evidence=dict(parsed.evidence),
            )
        except PluginHostError as error:
            raise PluginAdapterError(
                error.plugin_error_code or error.code,
                "Plugin score dimension request failed",
                retryable=error.retryable,
            ) from error
        except (ValidationError, ValueError, TypeError) as error:
            self._invalid_result()
            raise PluginAdapterError(
                "invalid_plugin_result",
                "Plugin score dimension returned invalid evidence",
            ) from error


class ScoreDimensionRegistry:
    def __init__(self, dimensions: Iterable[PluginScoreDimension] = ()) -> None:
        self._items: dict[str, PluginScoreDimension] = {}
        for dimension in dimensions:
            self.register(dimension)
        self._builtin_items = dict(self._items)

    def register(self, dimension: PluginScoreDimension) -> None:
        dimension_id = dimension.capabilities.dimension_id
        if dimension_id in self._items:
            raise ValueError(f"Duplicate score dimension: {dimension_id}")
        self._items[dimension_id] = dimension

    def replace_extensions(
        self,
        dimensions: Iterable[PluginScoreDimension],
    ) -> tuple[str, ...]:
        extensions: dict[str, PluginScoreDimension] = {}
        conflicts: list[str] = []
        for dimension in dimensions:
            dimension_id = dimension.capabilities.dimension_id
            if dimension_id in self._builtin_items or dimension_id in extensions:
                conflicts.append(dimension_id)
                continue
            extensions[dimension_id] = dimension
        self._items = {**self._builtin_items, **extensions}
        return tuple(sorted(conflicts))

    def get(self, dimension_id: str) -> PluginScoreDimension | None:
        return self._items.get(dimension_id)

    def all(self) -> tuple[PluginScoreDimension, ...]:
        return tuple(self._items[key] for key in sorted(self._items))


class PluginAgentBackend(_AdapterBase):
    __slots__ = ("capability",)
    required_methods = frozenset({"models", "generate"})

    def __init__(
        self,
        *,
        plugin_id: str,
        display_name: str,
        capability: PluginCapability,
        host: PluginInvoker,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if capability.kind is not CapabilityKind.AGENT:
            raise PluginAdapterError("capability_kind", "Expected an Agent backend capability")
        super().__init__(
            plugin_id=plugin_id,
            display_name=display_name,
            capability=capability,
            host=host,
            clock=clock,
        )
        self.capability = AgentCapability(
            backend_id=capability.capability_id,
            display_name=display_name,
            location=AgentLocation.LOCAL,
            probe_code=AgentProbeCode.READY,
            installed=True,
            authenticated=True,
            available=True,
            supports_schema=True,
            supports_streaming=True,
            supports_vision=False,
            supports_external_research=False,
        )

    async def discover_models(self) -> ModelCatalog:
        try:
            invoked = await self._invoke("models", {})
            parsed = _ModelCatalogResult.model_validate(invoked.result)
            return ModelCatalog(
                models=tuple(
                    AgentModel(
                        model_id=model.model_id,
                        display_name=model.display_name,
                        supports_vision=model.supports_vision,
                        context_window=model.context_window,
                    )
                    for model in parsed.models
                ),
                supports_manual_model_id=parsed.supports_manual_model_id,
                supports_default_model=parsed.supports_default_model,
                degradation_reasons=parsed.degradation_reasons,
            )
        except PluginHostError as error:
            raise _agent_error(error) from error
        except (ValidationError, ValueError, TypeError) as error:
            self._invalid_result()
            raise AgentBackendError(
                AgentErrorCode.INVALID_OUTPUT,
                "Plugin Agent returned an invalid model catalog",
            ) from error

    async def generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: Callable[[AgentFragment], Awaitable[None]],
        cancelled: Callable[[], bool],
    ) -> AgentResult:
        attachments: list[dict[str, JsonValue]] = []
        for attachment in request.attachments:
            if len(attachment.content) > _MAX_INLINE_ATTACHMENT_BYTES:
                raise AgentBackendError(
                    AgentErrorCode.OUTPUT_LIMIT,
                    "Plugin Agent evidence exceeds the isolated request limit",
                )
            attachments.append(
                {
                    "name": attachment.name,
                    "media_type": attachment.media_type,
                    "content_base64": base64.b64encode(attachment.content).decode("ascii"),
                }
            )
        try:
            invoked = await self._invoke(
                "generate",
                {
                    "request": {
                        "task_id": request.task_id,
                        "task_kind": request.task_kind.value,
                        "prompt": request.prompt,
                        "output_schema": dict(request.output_schema),
                        "reasoning_effort": request.reasoning_effort.value,
                        "service_tier": request.service_tier.value,
                        "model_id": request.model_id,
                        "external_research": request.external_research,
                        "attachments": attachments,
                    }
                },
                cancelled=cancelled,
                timeout_seconds=request.timeout_seconds,
            )
            next_index = 0
            for event in invoked.events:
                if event.event != "agent.fragment":
                    continue
                fragment_result = _AgentFragmentResult.model_validate(event.payload)
                if fragment_result.index != next_index:
                    raise ValueError("Plugin Agent fragment indexes must be contiguous")
                fragment = AgentFragment(
                    index=fragment_result.index,
                    kind=fragment_result.kind,
                    text=fragment_result.text,
                )
                await on_fragment(fragment)
                next_index += 1
            parsed = _AgentGenerationResult.model_validate(invoked.result)
            return AgentResult(
                raw_output=parsed.raw_output,
                structured_output=dict(parsed.structured_output),
                resolved_model_id=parsed.resolved_model_id,
                usage=AgentUsage(
                    input_tokens=parsed.usage.input_tokens,
                    output_tokens=parsed.usage.output_tokens,
                ),
            )
        except PluginHostError as error:
            raise _agent_error(error) from error
        except (ValidationError, ValueError, TypeError) as error:
            self._invalid_result()
            raise AgentBackendError(
                AgentErrorCode.INVALID_OUTPUT,
                "Plugin Agent returned invalid structured output",
            ) from error


@dataclass(frozen=True, slots=True)
class PublisherRequest:
    snapshot_id: str
    payload: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.snapshot_id):
            raise ValueError("snapshot_id must be a bounded stable identifier")
        copied = _bounded_json(dict(self.payload))
        object.__setattr__(self, "payload", MappingProxyType(copied))


@dataclass(frozen=True, slots=True)
class PublisherResult:
    publication_id: str
    public_url: str | None
    provenance: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


@dataclass(frozen=True, slots=True)
class PublisherCapabilities:
    publisher_id: str
    display_name: str
    version: str


class PluginPublisher(_AdapterBase):
    __slots__ = ("capabilities",)
    required_methods = frozenset({"publish"})

    def __init__(
        self,
        *,
        plugin_id: str,
        display_name: str,
        distribution_version: str,
        capability: PluginCapability,
        host: PluginInvoker,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if capability.kind is not CapabilityKind.PUBLISHER:
            raise PluginAdapterError("capability_kind", "Expected a publisher capability")
        super().__init__(
            plugin_id=plugin_id,
            display_name=display_name,
            capability=capability,
            host=host,
            clock=clock,
        )
        self.capabilities = PublisherCapabilities(
            publisher_id=capability.capability_id,
            display_name=display_name,
            version=distribution_version,
        )

    async def publish(self, request: PublisherRequest) -> PublisherResult:
        try:
            invoked = await self._invoke(
                "publish",
                {
                    "snapshot": {
                        "snapshot_id": request.snapshot_id,
                        "payload": dict(request.payload),
                    }
                },
            )
            parsed = _PublisherResult.model_validate(invoked.result)
            return PublisherResult(
                publication_id=parsed.publication_id,
                public_url=str(parsed.public_url) if parsed.public_url else None,
                provenance=dict(parsed.provenance),
            )
        except PluginHostError as error:
            raise PluginAdapterError(
                error.plugin_error_code or error.code,
                "Plugin publisher request failed",
                retryable=error.retryable,
            ) from error
        except (ValidationError, ValueError, TypeError) as error:
            self._invalid_result()
            raise PluginAdapterError(
                "invalid_plugin_result",
                "Plugin publisher returned an invalid result",
            ) from error


class PublisherRegistry:
    def __init__(self, publishers: Iterable[PluginPublisher] = ()) -> None:
        self._items: dict[str, PluginPublisher] = {}
        for publisher in publishers:
            self.register(publisher)
        self._builtin_items = dict(self._items)

    def register(self, publisher: PluginPublisher) -> None:
        publisher_id = publisher.capabilities.publisher_id
        if publisher_id in self._items:
            raise ValueError(f"Duplicate publisher: {publisher_id}")
        self._items[publisher_id] = publisher

    def replace_extensions(
        self,
        publishers: Iterable[PluginPublisher],
    ) -> tuple[str, ...]:
        extensions: dict[str, PluginPublisher] = {}
        conflicts: list[str] = []
        for publisher in publishers:
            publisher_id = publisher.capabilities.publisher_id
            if publisher_id in self._builtin_items or publisher_id in extensions:
                conflicts.append(publisher_id)
                continue
            extensions[publisher_id] = publisher
        self._items = {**self._builtin_items, **extensions}
        return tuple(sorted(conflicts))

    def get(self, publisher_id: str) -> PluginPublisher | None:
        return self._items.get(publisher_id)

    def all(self) -> tuple[PluginPublisher, ...]:
        return tuple(self._items[key] for key in sorted(self._items))


@dataclass(frozen=True, slots=True)
class PluginAdapterIssue:
    plugin_id: str
    capability_id: str | None
    code: str


@dataclass(frozen=True, slots=True)
class PluginAdapterBundle:
    sources: tuple[PluginSourceConnector, ...] = ()
    metadata_enrichers: tuple[PluginMetadataEnricher, ...] = ()
    score_dimensions: tuple[PluginScoreDimension, ...] = ()
    agent_backends: tuple[PluginAgentBackend, ...] = ()
    publishers: tuple[PluginPublisher, ...] = ()
    issues: tuple[PluginAdapterIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginRegistrationReport:
    issues: tuple[PluginAdapterIssue, ...] = ()


class PluginAdapterManager:
    """Atomically refresh all five extension registries from current audit state."""

    def __init__(
        self,
        *,
        installations: Callable[[], tuple[PluginInstallation, ...]],
        permissions: PermissionView,
        host: PluginInvoker,
        sandbox_available: Callable[[], bool],
        sources: ConnectorRegistry,
        metadata_enrichers: EnricherRegistry,
        score_dimensions: ScoreDimensionRegistry,
        agent_backends: AgentBackendRegistry,
        publishers: PublisherRegistry,
    ) -> None:
        self._installations = installations
        self._permissions = permissions
        self._host = host
        self._sandbox_available = sandbox_available
        self._sources = sources
        self._metadata_enrichers = metadata_enrichers
        self._score_dimensions = score_dimensions
        self._agent_backends = agent_backends
        self._publishers = publishers
        self.last_report = PluginRegistrationReport()

    def refresh(self) -> PluginRegistrationReport:
        bundle = build_plugin_adapters(
            installations=self._installations(),
            permissions=self._permissions,
            host=self._host,
            sandbox_available=self._sandbox_available(),
        )
        conflicts: list[PluginAdapterIssue] = []
        mappings: tuple[
            tuple[
                tuple[Any, ...],
                Callable[[tuple[Any, ...]], tuple[str, ...]],
                Callable[[Any], str],
            ],
            ...,
        ] = (
            (
                bundle.sources,
                self._sources.replace_extensions,
                lambda item: item.capabilities.connector_id,
            ),
            (
                bundle.metadata_enrichers,
                self._metadata_enrichers.replace_extensions,
                lambda item: item.capabilities.enricher_id,
            ),
            (
                bundle.score_dimensions,
                self._score_dimensions.replace_extensions,
                lambda item: item.capabilities.dimension_id,
            ),
            (
                bundle.agent_backends,
                self._agent_backends.replace_extensions,
                lambda item: item.capability.backend_id,
            ),
            (
                bundle.publishers,
                self._publishers.replace_extensions,
                lambda item: item.capabilities.publisher_id,
            ),
        )
        for adapters, replace, identity in mappings:
            conflicting_ids = set(replace(adapters))
            for adapter in adapters:
                capability_id = identity(adapter)
                if capability_id in conflicting_ids:
                    conflicts.append(
                        PluginAdapterIssue(
                            adapter._plugin_id,
                            capability_id,
                            "builtin_id_conflict",
                        )
                    )
        self.last_report = PluginRegistrationReport(issues=(*bundle.issues, *conflicts))
        return self.last_report


def build_plugin_adapters(
    *,
    installations: Iterable[PluginInstallation],
    permissions: PermissionView,
    host: PluginInvoker,
    sandbox_available: bool,
) -> PluginAdapterBundle:
    sources: list[PluginSourceConnector] = []
    enrichers: list[PluginMetadataEnricher] = []
    scores: list[PluginScoreDimension] = []
    agents: list[PluginAgentBackend] = []
    publishers: list[PluginPublisher] = []
    issues: list[PluginAdapterIssue] = []
    ordered = tuple(sorted(installations, key=lambda item: item.plugin_id))
    if not sandbox_available:
        return PluginAdapterBundle(
            issues=tuple(
                PluginAdapterIssue(item.plugin_id, None, "sandbox_unavailable")
                for item in ordered
            )
        )
    for item in ordered:
        state_code = _installation_state_code(item)
        if state_code is not None:
            issues.append(PluginAdapterIssue(item.plugin_id, None, state_code))
            continue
        granted = {
            (permission.kind, permission.resource)
            for permission in permissions.effective_permissions(item.plugin_id)
        }
        for declared in item.manifest.capabilities:
            required = {
                (permission.kind, permission.resource) for permission in declared.permissions
            }
            if not required <= granted:
                issues.append(
                    PluginAdapterIssue(
                        item.plugin_id,
                        declared.capability_id,
                        "permission_denied",
                    )
                )
                continue
            try:
                adapter = _adapter_for(item, declared, host)
            except PluginAdapterError as error:
                issues.append(
                    PluginAdapterIssue(item.plugin_id, declared.capability_id, error.code)
                )
                continue
            if isinstance(adapter, PluginSourceConnector):
                sources.append(adapter)
            elif isinstance(adapter, PluginMetadataEnricher):
                enrichers.append(adapter)
            elif isinstance(adapter, PluginScoreDimension):
                scores.append(adapter)
            elif isinstance(adapter, PluginAgentBackend):
                agents.append(adapter)
            else:
                publishers.append(adapter)
    return PluginAdapterBundle(
        sources=tuple(sources),
        metadata_enrichers=tuple(enrichers),
        score_dimensions=tuple(scores),
        agent_backends=tuple(agents),
        publishers=tuple(publishers),
        issues=tuple(issues),
    )


def register_plugin_adapters(
    bundle: PluginAdapterBundle,
    *,
    sources: ConnectorRegistry,
    metadata_enrichers: EnricherRegistry,
    score_dimensions: ScoreDimensionRegistry,
    agent_backends: AgentBackendRegistry,
    publishers: PublisherRegistry,
) -> PluginRegistrationReport:
    issues = list(bundle.issues)
    registrations: tuple[tuple[Iterable[Any], Callable[[Any], None], Callable[[Any], str]], ...] = (
        (bundle.sources, sources.register, lambda item: item.capabilities.connector_id),
        (
            bundle.metadata_enrichers,
            metadata_enrichers.register,
            lambda item: item.capabilities.enricher_id,
        ),
        (
            bundle.score_dimensions,
            score_dimensions.register,
            lambda item: item.capabilities.dimension_id,
        ),
        (
            bundle.agent_backends,
            agent_backends.register,
            lambda item: item.capability.backend_id,
        ),
        (bundle.publishers, publishers.register, lambda item: item.capabilities.publisher_id),
    )
    for adapters, register, identity in registrations:
        for adapter in adapters:
            capability_id = identity(adapter)
            try:
                register(adapter)
            except ValueError:
                issues.append(
                    PluginAdapterIssue(
                        adapter._plugin_id,
                        capability_id,
                        "builtin_id_conflict",
                    )
                )
    return PluginRegistrationReport(issues=tuple(issues))


PluginAdapter = (
    PluginSourceConnector
    | PluginMetadataEnricher
    | PluginScoreDimension
    | PluginAgentBackend
    | PluginPublisher
)


def _adapter_for(
    installation: PluginInstallation,
    declared: PluginCapability,
    host: PluginInvoker,
) -> PluginAdapter:
    if declared.kind is CapabilityKind.SOURCE:
        return PluginSourceConnector(
            plugin_id=installation.plugin_id,
            display_name=installation.manifest.display_name,
            capability=declared,
            host=host,
        )
    if declared.kind is CapabilityKind.METADATA:
        return PluginMetadataEnricher(
            plugin_id=installation.plugin_id,
            display_name=installation.manifest.display_name,
            distribution_version=installation.distribution_version,
            capability=declared,
            host=host,
        )
    if declared.kind is CapabilityKind.SCORE:
        return PluginScoreDimension(
            plugin_id=installation.plugin_id,
            display_name=installation.manifest.display_name,
            distribution_version=installation.distribution_version,
            capability=declared,
            host=host,
        )
    if declared.kind is CapabilityKind.AGENT:
        return PluginAgentBackend(
            plugin_id=installation.plugin_id,
            display_name=installation.manifest.display_name,
            capability=declared,
            host=host,
        )
    return PluginPublisher(
        plugin_id=installation.plugin_id,
        display_name=installation.manifest.display_name,
        distribution_version=installation.distribution_version,
        capability=declared,
        host=host,
    )


def _installation_state_code(installation: PluginInstallation) -> str | None:
    if not installation.compatible:
        return "plugin_incompatible"
    if not installation.enabled:
        return "plugin_disabled"
    if installation.circuit_open_reason is not None:
        return "circuit_open"
    return None


def _source_query_payload(query: SourceQuery) -> dict[str, Any]:
    return {
        "months": list(query.months),
        "keywords": {
            "version_id": query.compiled_keywords.version_id,
            "boolean_expression": query.compiled_keywords.boolean_expression,
            "required_groups": [list(group) for group in query.compiled_keywords.required_groups],
            "boost_terms": list(query.compiled_keywords.boost_terms),
            "excluded_groups": [list(group) for group in query.compiled_keywords.excluded_groups],
        },
        "filters": _bounded_json(query.filters),
        "fingerprint": query.fingerprint,
    }


def _connector_error(error: PluginHostError) -> ConnectorRequestError:
    return ConnectorRequestError(
        error.plugin_error_code or error.code,
        "Plugin source request failed",
        retryable=error.retryable,
    )


def _agent_error(error: PluginHostError) -> AgentBackendError:
    if error.code == "cancelled":
        code = AgentErrorCode.CANCELLED
    elif error.code in {"deadline_exceeded", "timeout"}:
        code = AgentErrorCode.TIMEOUT
    elif error.code in {"output_limit", "line_limit", "total_output_limit", "input_limit"}:
        code = AgentErrorCode.OUTPUT_LIMIT
    elif error.code in {"protocol_error", "invalid_request"}:
        code = AgentErrorCode.PROTOCOL
    elif error.code in {"plugin_crash", "process_io"}:
        code = AgentErrorCode.PROCESS
    elif error.code in {"sandbox_unavailable", "plugin_disabled", "plugin_incompatible"}:
        code = AgentErrorCode.UNAVAILABLE
    else:
        code = AgentErrorCode.TRANSPORT
    return AgentBackendError(code, "Plugin Agent request failed", retryable=error.retryable)


def _bounded_text(value: str, field_name: str, maximum: int) -> str:
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field_name} must contain between 1 and {maximum} characters")
    return normalized


def _bounded_json(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("plugin adapter payload must contain only JSON values") from error
    if len(encoded) > _MAX_ADAPTER_JSON_BYTES:
        raise ValueError("plugin adapter payload exceeds the isolated request budget")
    return cast(dict[str, Any], json.loads(encoded))
