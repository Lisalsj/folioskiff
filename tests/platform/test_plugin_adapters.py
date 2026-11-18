from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentFragment,
    AgentRequest,
    AgentTaskKind,
    ReasoningEffort,
    ServiceTier,
)
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.discovery.connectors import (
    ConnectorRegistry,
    ConnectorRequestError,
    SourceQuery,
)
from paper_reader.discovery.keywords import CompiledKeywordQuery
from paper_reader.plugins.adapters import (
    PluginAdapterBundle,
    PluginAdapterError,
    PluginAdapterManager,
    PluginAgentBackend,
    PluginMetadataEnricher,
    PluginPublisher,
    PluginScoreDimension,
    PluginSourceConnector,
    PublisherRegistry,
    PublisherRequest,
    ScoreDimensionRegistry,
    ScoreEvidence,
    build_plugin_adapters,
    register_plugin_adapters,
)
from paper_reader.plugins.contracts import CapabilityKind, PluginCapability, PluginManifest
from paper_reader.plugins.discovery import PluginInstallation
from paper_reader.plugins.host import PluginHostError, PluginInvocationResult
from paper_reader.plugins.sdk import PluginEvent
from paper_reader.recommendation.enrichers.registry import EnricherRegistry
from paper_reader.recommendation.enrichment import (
    EnricherRequestError,
    EnrichmentStatus,
    WorkLookup,
)
from paper_reader.recommendation.relevance import EvidenceStatus


def capability(
    kind: CapabilityKind,
    *,
    capability_id: str | None = None,
    methods: tuple[str, ...] | None = None,
    permissions: tuple[dict[str, str], ...] = (),
) -> PluginCapability:
    defaults = {
        CapabilityKind.SOURCE: ("probe", "estimate", "fetch_page"),
        CapabilityKind.METADATA: ("probe", "enrich"),
        CapabilityKind.SCORE: ("probe", "evaluate"),
        CapabilityKind.AGENT: ("probe", "models", "generate"),
        CapabilityKind.PUBLISHER: ("probe", "publish"),
    }
    return PluginCapability.model_validate(
        {
            "capability_id": capability_id or f"fixture.{kind.value}",
            "kind": kind.value,
            "methods": list(methods or defaults[kind]),
            "permissions": list(permissions),
            "timeout_seconds": 90,
        }
    )


def manifest(
    *,
    plugin_id: str = "fixture.research",
    capabilities: tuple[PluginCapability, ...] | None = None,
) -> PluginManifest:
    selected = capabilities or tuple(capability(kind) for kind in CapabilityKind)
    return PluginManifest.model_validate(
        {
            "manifest_version": "1",
            "plugin_id": plugin_id,
            "display_name": "Fixture Research Tools",
            "distribution_name": "paper-reader-fixture-research",
            "distribution_version": "1.4.0",
            "protocol": {"minimum": "1.0", "maximum": "1.0"},
            "capabilities": [item.model_dump(mode="json") for item in selected],
            "config_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "license": "Apache-2.0",
        }
    )


def installation(
    plugin_manifest: PluginManifest,
    *,
    enabled: bool = True,
    compatible: bool = True,
    circuit_open_reason: str | None = None,
) -> PluginInstallation:
    now = datetime(2026, 7, 14, 8, tzinfo=UTC)
    return PluginInstallation(
        plugin_id=plugin_manifest.plugin_id,
        entry_point_value="fixture_research:plugin",
        distribution_name=plugin_manifest.distribution_name,
        distribution_version=plugin_manifest.distribution_version,
        manifest=plugin_manifest,
        manifest_sha256=plugin_manifest.canonical_sha256(),
        compatible=compatible,
        compatibility_reason=None if compatible else "protocol_incompatible",
        enabled=enabled,
        consecutive_failures=3 if circuit_open_reason else 0,
        circuit_open_reason=circuit_open_reason,
        last_discovered_at=now,
        last_called_at=None,
        created_at=now,
        updated_at=now,
    )


@dataclass(frozen=True, slots=True)
class HostCall:
    plugin_id: str
    capability_id: str
    method: str
    payload: dict[str, Any]


class FakeHost:
    def __init__(self, results: list[PluginInvocationResult | PluginHostError]) -> None:
        self.results = deque(results)
        self.calls: list[HostCall] = []
        self.contract_failures: list[str] = []

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
        assert deadline.tzinfo is not None
        assert cancelled() is False
        self.calls.append(HostCall(plugin_id, capability_id, method, payload))
        outcome = self.results.popleft()
        if isinstance(outcome, PluginHostError):
            raise outcome
        return outcome

    def record_contract_failure(self, plugin_id: str) -> None:
        self.contract_failures.append(plugin_id)


class PermissionView:
    def __init__(self, granted: Mapping[str, tuple[object, ...]]) -> None:
        self.granted = dict(granted)

    def effective_permissions(self, plugin_id: str) -> tuple[object, ...]:
        return self.granted.get(plugin_id, ())


def success(
    result: dict[str, Any],
    *,
    events: tuple[PluginEvent, ...] = (),
) -> PluginInvocationResult:
    return PluginInvocationResult(result=result, events=events)


def source_query(connector_id: str = "fixture.source_connector") -> SourceQuery:
    return SourceQuery(
        connector_id=connector_id,
        months=("2026-05", "2026-06"),
        compiled_keywords=CompiledKeywordQuery(
            version_id="keywords-v1",
            boolean_expression='("diffusion" OR "扩散模型") AND NOT ("survey")',
            required_groups=(("diffusion", "扩散模型"),),
            boost_terms=("controllable generation",),
            excluded_groups=(("survey",),),
        ),
        filters={"category": "cs.LG"},
        fingerprint="f" * 64,
    )


@pytest.mark.asyncio
async def test_source_adapter_maps_estimate_records_and_opaque_page_cursor() -> None:
    source_capability = capability(
        CapabilityKind.SOURCE,
        capability_id="fixture.source_connector",
    )
    host = FakeHost(
        [
            success({"total": 27, "exact": False, "warnings": ["sampled"]}),
            success(
                {
                    "records": [
                        {
                            "external_id": "paper-27",
                            "title": "Controllable Diffusion",
                            "authors": ["Ada Researcher", "林同学"],
                            "abstract": "A bounded fixture abstract.",
                            "doi": "10.1234/FIXTURE.27",
                            "published_at": "2026-05-03T08:00:00Z",
                            "updated_at": "2026-06-04T09:30:00Z",
                            "landing_url": "https://example.org/paper-27",
                            "field_provenance": {"title": "fixture.title"},
                            "raw_payload": {"fixture": 27},
                        }
                    ],
                    "next_cursor": "opaque:page:2",
                    "total": 27,
                }
            ),
        ]
    )
    adapter = PluginSourceConnector(
        plugin_id="fixture.research",
        display_name="Fixture Research Tools",
        capability=source_capability,
        host=host,
    )
    query = source_query()

    estimate = await adapter.estimate(query)
    page = await adapter.fetch_page(query, cursor=None, limit=20)

    assert estimate.total == 27
    assert estimate.exact is False
    assert estimate.warnings == ("sampled",)
    assert page.next_cursor == "opaque:page:2"
    assert page.total == 27
    assert page.records[0].connector_id == "fixture.source_connector"
    assert page.records[0].external_id == "paper-27"
    assert page.records[0].doi == "10.1234/FIXTURE.27"
    assert page.records[0].updated_at == datetime(2026, 6, 4, 9, 30, tzinfo=UTC)
    assert host.calls[0].method == "estimate"
    assert host.calls[0].payload == {
        "query": {
            "months": ["2026-05", "2026-06"],
            "keywords": {
                "version_id": "keywords-v1",
                "boolean_expression": '("diffusion" OR "扩散模型") AND NOT ("survey")',
                "required_groups": [["diffusion", "扩散模型"]],
                "boost_terms": ["controllable generation"],
                "excluded_groups": [["survey"]],
            },
            "filters": {"category": "cs.LG"},
            "fingerprint": "f" * 64,
        }
    }
    assert host.calls[1].payload["cursor"] is None
    assert host.calls[1].payload["limit"] == 20


@pytest.mark.asyncio
async def test_source_adapter_isolates_host_and_untrusted_schema_failures() -> None:
    source_capability = capability(CapabilityKind.SOURCE)
    host = FakeHost(
        [
            PluginHostError(
                "plugin_error",
                "safe host message",
                retryable=True,
                plugin_error_code="temporary_upstream",
            ),
            success({"records": [{"title": "missing identity"}], "next_cursor": None}),
        ]
    )
    adapter = PluginSourceConnector(
        plugin_id="fixture.research",
        display_name="Fixture Research Tools",
        capability=source_capability,
        host=host,
    )

    with pytest.raises(ConnectorRequestError) as host_error:
        await adapter.estimate(source_query("fixture.source_connector"))
    assert host_error.value.code == "temporary_upstream"
    assert host_error.value.retryable is True

    with pytest.raises(ConnectorRequestError) as schema_error:
        await adapter.fetch_page(source_query("fixture.source_connector"), cursor=None, limit=10)
    assert schema_error.value.code == "invalid_plugin_result"
    assert schema_error.value.retryable is False
    assert host.contract_failures == ["fixture.research"]


@pytest.mark.asyncio
async def test_metadata_adapter_binds_work_identity_and_computes_auditable_hash() -> None:
    metadata_capability = capability(CapabilityKind.METADATA)
    host = FakeHost(
        [
            success(
                {
                    "status": "observed",
                    "external_id": "W2741809807",
                    "matched_by": "doi",
                    "fields": {"cited_by_count": 41, "fwci": 2.3},
                    "field_provenance": {
                        "cited_by_count": "fixture.cited_by_count",
                        "fwci": "fixture.fwci",
                    },
                    "warnings": [],
                    "rate_limit": {"remaining": 99, "limit": 100},
                }
            )
        ]
    )
    adapter = PluginMetadataEnricher(
        plugin_id="fixture.research",
        display_name="Fixture Research Tools",
        distribution_version="1.4.0",
        capability=metadata_capability,
        host=host,
        clock=lambda: datetime(2026, 7, 14, 9, tzinfo=UTC),
    )
    lookup = WorkLookup(
        work_id="work-1",
        doi="10.1234/example",
        title="A paper",
        authors=("Author One",),
        source_ids={"arxiv": "2607.00001"},
    )

    result = await adapter.enrich(lookup)

    assert result.work_id == "work-1"
    assert result.enricher_id == metadata_capability.capability_id
    assert result.enricher_version == "1.4.0"
    assert result.status is EnrichmentStatus.OBSERVED
    assert result.fields == {"cited_by_count": 41, "fwci": 2.3}
    assert result.response_sha256 is not None and len(result.response_sha256) == 64
    assert result.fetched_at == datetime(2026, 7, 14, 9, tzinfo=UTC)
    assert host.calls[0].payload["work"] == {
        "work_id": "work-1",
        "doi": "10.1234/example",
        "title": "A paper",
        "authors": ["Author One"],
        "source_ids": {"arxiv": "2607.00001"},
    }


@pytest.mark.asyncio
async def test_metadata_adapter_maps_plugin_failures_without_exposing_diagnostics() -> None:
    host = FakeHost(
        [
            PluginHostError(
                "plugin_error",
                "Plugin returned a structured operation error",
                retryable=True,
                plugin_error_code="rate_limited",
            )
        ]
    )
    adapter = PluginMetadataEnricher(
        plugin_id="fixture.research",
        display_name="Fixture Research Tools",
        distribution_version="1.4.0",
        capability=capability(CapabilityKind.METADATA),
        host=host,
    )

    with pytest.raises(EnricherRequestError) as captured:
        await adapter.enrich(
            WorkLookup(work_id="work-1", doi=None, title="A paper", authors=())
        )

    assert captured.value.code == "rate_limited"
    assert captured.value.retryable is True
    assert "diagnostic" not in str(captured.value).casefold()


@pytest.mark.asyncio
async def test_score_adapter_accepts_only_bounded_evidence_and_returns_metric_evidence() -> None:
    score_capability = capability(CapabilityKind.SCORE)
    host = FakeHost(
        [
            success(
                {
                    "status": "observed",
                    "value": 83.5,
                    "confidence": 74.0,
                    "reason": None,
                    "evidence": {"normalized_percentile": 0.835},
                }
            )
        ]
    )
    adapter = PluginScoreDimension(
        plugin_id="fixture.research",
        display_name="Fixture Research Tools",
        distribution_version="1.4.0",
        capability=score_capability,
        host=host,
    )

    metric = await adapter.evaluate(
        ScoreEvidence(
            work_id="work-1",
            title="A paper",
            fields={"citation_percentile": 0.82, "year": 2025},
        )
    )

    assert metric.status is EvidenceStatus.OBSERVED
    assert metric.value == 83.5
    assert metric.confidence == 74
    assert metric.evidence == {"normalized_percentile": 0.835}
    assert host.calls[0].payload == {
        "evidence": {
            "work_id": "work-1",
            "title": "A paper",
            "fields": {"citation_percentile": 0.82, "year": 2025},
        }
    }


@pytest.mark.asyncio
async def test_agent_adapter_maps_models_fragments_and_structured_result() -> None:
    agent_capability = capability(CapabilityKind.AGENT)
    event = PluginEvent(
        protocol_version="1.0",
        request_id="15c2ab69-67bd-4726-978b-f7a9cf0878c7",
        sequence=1,
        event="agent.fragment",
        payload={"index": 0, "kind": "text", "text": "第一段"},
    )
    host = FakeHost(
        [
            success(
                {
                    "models": [
                        {
                            "model_id": "fixture-model",
                            "display_name": "Fixture Model",
                            "supports_vision": False,
                            "context_window": 32768,
                        }
                    ],
                    "supports_manual_model_id": True,
                    "supports_default_model": False,
                    "degradation_reasons": [],
                }
            ),
            success(
                {
                    "raw_output": '{"summary":"ok"}',
                    "structured_output": {"summary": "ok"},
                    "resolved_model_id": "fixture-model",
                    "usage": {"input_tokens": 120, "output_tokens": 32},
                },
                events=(event,),
            ),
        ]
    )
    adapter = PluginAgentBackend(
        plugin_id="fixture.research",
        display_name="Fixture Research Tools",
        capability=agent_capability,
        host=host,
    )
    fragments: list[AgentFragment] = []

    catalog = await adapter.discover_models()
    result = await adapter.generate(
        AgentRequest(
            task_id="report-1",
            task_kind=AgentTaskKind.REPORT,
            prompt="Analyze only the supplied evidence.",
            output_schema={"type": "object"},
            reasoning_effort=ReasoningEffort.HIGH,
            service_tier=ServiceTier.FLEX,
            model_id="fixture-model",
            timeout_seconds=120,
        ),
        on_fragment=lambda fragment: _append_fragment(fragments, fragment),
        cancelled=lambda: False,
    )

    assert catalog.models[0].model_id == "fixture-model"
    assert adapter.capability.backend_id == agent_capability.capability_id
    assert adapter.capability.supports_streaming is True
    assert fragments == [AgentFragment(index=0, kind="text", text="第一段")]
    assert result.structured_output == {"summary": "ok"}
    assert result.usage.input_tokens == 120
    generate_payload = host.calls[1].payload["request"]
    assert generate_payload["prompt"] == "Analyze only the supplied evidence."
    assert generate_payload["reasoning_effort"] == "high"
    assert generate_payload["service_tier"] == "flex"
    assert generate_payload["attachments"] == []


async def _append_fragment(target: list[AgentFragment], fragment: AgentFragment) -> None:
    target.append(fragment)


@pytest.mark.asyncio
async def test_agent_adapter_rejects_malformed_fragment_as_invalid_output() -> None:
    event = PluginEvent(
        protocol_version="1.0",
        request_id="15c2ab69-67bd-4726-978b-f7a9cf0878c7",
        sequence=1,
        event="agent.fragment",
        payload={"index": -1, "kind": "text", "text": "bad"},
    )
    host = FakeHost([success({"raw_output": "{}", "structured_output": {}}, events=(event,))])
    adapter = PluginAgentBackend(
        plugin_id="fixture.research",
        display_name="Fixture Research Tools",
        capability=capability(CapabilityKind.AGENT),
        host=host,
    )

    with pytest.raises(AgentBackendError) as captured:
        await adapter.generate(
            AgentRequest(
                task_id="report-1",
                task_kind=AgentTaskKind.REPORT,
                prompt="Analyze.",
                output_schema={},
                reasoning_effort=ReasoningEffort.MEDIUM,
                service_tier=ServiceTier.FLEX,
                timeout_seconds=60,
            ),
            on_fragment=lambda fragment: _append_fragment([], fragment),
            cancelled=lambda: False,
        )

    assert captured.value.code.value == "invalid_output"
    assert host.contract_failures == ["fixture.research"]


@pytest.mark.asyncio
async def test_publisher_adapter_maps_only_snapshot_payload_and_safe_result() -> None:
    host = FakeHost(
        [
            success(
                {
                    "publication_id": "fixture-publication-1",
                    "public_url": "https://pages.example.org/reading-list/",
                    "provenance": {"revision": "abc123"},
                }
            )
        ]
    )
    adapter = PluginPublisher(
        plugin_id="fixture.research",
        display_name="Fixture Research Tools",
        distribution_version="1.4.0",
        capability=capability(CapabilityKind.PUBLISHER),
        host=host,
    )

    result = await adapter.publish(
        PublisherRequest(
            snapshot_id="snapshot-1",
            payload={"title": "Public reading list", "works": [{"id": "work-1"}]},
        )
    )

    assert result.publication_id == "fixture-publication-1"
    assert result.public_url == "https://pages.example.org/reading-list/"
    assert result.provenance == {"revision": "abc123"}
    assert host.calls[0].payload == {
        "snapshot": {
            "snapshot_id": "snapshot-1",
            "payload": {"title": "Public reading list", "works": [{"id": "work-1"}]},
        }
    }


def test_builder_only_exposes_enabled_compatible_approved_and_healthy_capabilities() -> None:
    requested = (
        {
            "kind": "network",
            "resource": "api.example.org",
            "purpose": "Read public metadata",
        },
    )
    ready_manifest = manifest(
        plugin_id="fixture.ready",
        capabilities=(
            capability(
                CapabilityKind.SOURCE,
                capability_id="fixture.ready-source",
                permissions=requested,
            ),
            capability(CapabilityKind.METADATA, capability_id="fixture.ready-metadata"),
        ),
    )
    no_grant_manifest = manifest(
        plugin_id="fixture.no-grant",
        capabilities=(
            capability(
                CapabilityKind.SOURCE,
                capability_id="fixture.no-grant-source",
                permissions=requested,
            ),
        ),
    )
    disabled_manifest = manifest(
        plugin_id="fixture.disabled",
        capabilities=(capability(CapabilityKind.SOURCE, capability_id="fixture.disabled-source"),),
    )
    incompatible_manifest = manifest(
        plugin_id="fixture.incompatible",
        capabilities=(
            capability(CapabilityKind.SOURCE, capability_id="fixture.incompatible-source"),
        ),
    )
    circuit_manifest = manifest(
        plugin_id="fixture.circuit",
        capabilities=(capability(CapabilityKind.SOURCE, capability_id="fixture.circuit-source"),),
    )
    # Capabilities are sorted by ID; find the permission-bearing source explicitly.
    granted_permission = next(
        item.permissions
        for item in ready_manifest.capabilities
        if item.kind is CapabilityKind.SOURCE
    )
    host = FakeHost([])

    bundle = build_plugin_adapters(
        installations=(
            installation(ready_manifest),
            installation(no_grant_manifest),
            installation(disabled_manifest, enabled=False),
            installation(incompatible_manifest, compatible=False),
            installation(circuit_manifest, circuit_open_reason="protocol_error"),
        ),
        permissions=PermissionView({"fixture.ready": granted_permission}),
        host=host,
        sandbox_available=True,
    )

    assert [item.capabilities.connector_id for item in bundle.sources] == [
        "fixture.ready-source"
    ]
    assert [item.capabilities.enricher_id for item in bundle.metadata_enrichers] == [
        "fixture.ready-metadata"
    ]
    assert bundle.score_dimensions == ()
    assert bundle.agent_backends == ()
    assert bundle.publishers == ()
    assert {issue.code for issue in bundle.issues} == {
        "plugin_disabled",
        "plugin_incompatible",
        "permission_denied",
        "circuit_open",
    }

    unavailable = build_plugin_adapters(
        installations=(installation(ready_manifest),),
        permissions=PermissionView({"fixture.ready": granted_permission}),
        host=host,
        sandbox_available=False,
    )
    assert unavailable.sources == ()
    assert unavailable.metadata_enrichers == ()
    assert [issue.code for issue in unavailable.issues] == ["sandbox_unavailable"]


class BuiltinSource:
    def __init__(self, connector_id: str) -> None:
        from paper_reader.discovery.connectors import ConnectorCapabilities

        self.capabilities = ConnectorCapabilities(
            connector_id=connector_id,
            display_name="Built in",
            credential_requirement="none",
            supports_exact_updated_date=False,
        )

    async def estimate(self, query: SourceQuery) -> object:  # pragma: no cover - not invoked
        raise AssertionError(query)

    async def fetch_page(
        self,
        query: SourceQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> object:  # pragma: no cover - not invoked
        raise AssertionError((query, cursor, limit))


def test_registration_never_overwrites_builtin_ids_and_keeps_other_capabilities() -> None:
    host = FakeHost([])
    collision = PluginSourceConnector(
        plugin_id="fixture.research",
        display_name="Fixture Research Tools",
        capability=capability(CapabilityKind.SOURCE, capability_id="arxiv"),
        host=host,
    )
    unique = PluginSourceConnector(
        plugin_id="fixture.research",
        display_name="Fixture Research Tools",
        capability=capability(CapabilityKind.SOURCE, capability_id="fixture.source"),
        host=host,
    )
    sources = ConnectorRegistry((BuiltinSource("arxiv"),))
    enrichers = EnricherRegistry()
    agents = AgentBackendRegistry(())
    scores = ScoreDimensionRegistry()
    publishers = PublisherRegistry()

    report = register_plugin_adapters(
        PluginAdapterBundle(sources=(collision, unique)),
        sources=sources,
        metadata_enrichers=enrichers,
        score_dimensions=scores,
        agent_backends=agents,
        publishers=publishers,
    )

    assert sources.get("arxiv") is not collision
    assert sources.get("fixture.source") is unique
    assert [(issue.capability_id, issue.code) for issue in report.issues] == [
        ("arxiv", "builtin_id_conflict")
    ]


def test_adapter_manager_atomically_removes_revoked_extensions_and_preserves_builtins() -> None:
    plugin_manifest = manifest(
        plugin_id="fixture.dynamic",
        capabilities=(
            capability(CapabilityKind.SOURCE, capability_id="arxiv"),
            capability(CapabilityKind.SOURCE, capability_id="fixture.dynamic-source"),
        ),
    )
    current = [installation(plugin_manifest)]
    sources = ConnectorRegistry((BuiltinSource("arxiv"),))
    enrichers = EnricherRegistry()
    scores = ScoreDimensionRegistry()
    agents = AgentBackendRegistry(())
    publishers = PublisherRegistry()
    manager = PluginAdapterManager(
        installations=lambda: tuple(current),
        permissions=PermissionView({}),
        host=FakeHost([]),
        sandbox_available=lambda: True,
        sources=sources,
        metadata_enrichers=enrichers,
        score_dimensions=scores,
        agent_backends=agents,
        publishers=publishers,
    )

    first = manager.refresh()

    assert sources.get("arxiv") is not None
    assert sources.get("fixture.dynamic-source") is not None
    assert [(issue.capability_id, issue.code) for issue in first.issues] == [
        ("arxiv", "builtin_id_conflict")
    ]

    current[:] = [installation(plugin_manifest, enabled=False)]
    second = manager.refresh()

    assert sources.get("arxiv") is not None
    assert sources.get("fixture.dynamic-source") is None
    assert [(issue.plugin_id, issue.code) for issue in second.issues] == [
        ("fixture.dynamic", "plugin_disabled")
    ]


def test_adapter_rejects_missing_required_method_before_any_plugin_call() -> None:
    host = FakeHost([])
    incomplete = capability(
        CapabilityKind.SOURCE,
        methods=("probe", "estimate"),
    )

    with pytest.raises(PluginAdapterError, match="fetch_page"):
        PluginSourceConnector(
            plugin_id="fixture.research",
            display_name="Fixture Research Tools",
            capability=incomplete,
            host=host,
        )

    assert host.calls == []
