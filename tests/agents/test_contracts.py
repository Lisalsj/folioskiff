from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import fields
from typing import Any

import pytest

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
    AgentTaskKind,
    AgentUsage,
    EvidenceAttachment,
    ModelCatalog,
    ReasoningEffort,
    ServiceTier,
)
from paper_reader.agents.registry import AgentBackendRegistry


class FakeBackend:
    def __init__(self, backend_id: str = "fake") -> None:
        self.capability = AgentCapability(
            backend_id=backend_id,
            display_name="Fixture Agent",
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
        self.discovery_calls = 0
        self.generation_calls = 0

    async def discover_models(self) -> ModelCatalog:
        self.discovery_calls += 1
        return ModelCatalog(
            models=(AgentModel(model_id="fixture-small", display_name="Fixture small"),),
            supports_manual_model_id=True,
            supports_default_model=False,
        )

    async def generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: Callable[[AgentFragment], Awaitable[None]],
        cancelled: Callable[[], bool],
    ) -> AgentResult:
        self.generation_calls += 1
        if cancelled():
            raise AgentBackendError(
                AgentErrorCode.CANCELLED,
                "Agent generation was cancelled",
                retryable=True,
            )
        await on_fragment(AgentFragment(index=0, kind="text", text="fixture"))
        return AgentResult(
            raw_output='{"summary":"fixture"}',
            structured_output={"summary": "fixture"},
            resolved_model_id=request.model_id or "fixture-default",
            usage=AgentUsage(input_tokens=12, output_tokens=4),
        )


def _request() -> AgentRequest:
    return AgentRequest(
        task_id="job-123",
        task_kind=AgentTaskKind.REPORT,
        prompt="Analyze the delimited paper evidence.",
        output_schema={"type": "object"},
        model_id="fixture-small",
        reasoning_effort=ReasoningEffort.HIGH,
        service_tier=ServiceTier.FLEX,
        external_research=False,
        attachments=(
            EvidenceAttachment(
                name="evidence.json",
                media_type="application/json",
                content=b"{}",
            ),
        ),
        timeout_seconds=120,
    )


def test_agent_request_has_no_score_preference_credential_or_path_capability() -> None:
    names = {item.name for item in fields(AgentRequest)}

    assert {
        "task_id",
        "task_kind",
        "prompt",
        "output_schema",
        "model_id",
        "reasoning_effort",
        "service_tier",
        "external_research",
        "attachments",
        "timeout_seconds",
        "context_window",
        "max_output_tokens",
    } == names
    assert not names.intersection(
        {
            "score_repository",
            "ranking_service",
            "preference_repository",
            "credential_store",
            "database_session",
            "working_directory",
            "file_path",
        }
    )


@pytest.mark.asyncio
async def test_invocation_failures_log_category_and_counts_without_private_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailedBackend(FakeBackend):
        async def generate(self, request: AgentRequest, **kwargs: Any) -> AgentResult:
            raise AgentBackendError(
                AgentErrorCode.OUTPUT_LIMIT,
                "private content sk-fixture-token",
                diagnostic="private prompt or credential",
                usage=AgentUsage(input_tokens=500, output_tokens=2048),
            )

    registry = AgentBackendRegistry((FailedBackend(),))
    with pytest.raises(AgentBackendError):
        await registry.generate(
            "fake", _request(), on_fragment=lambda _: None, cancelled=lambda: False
        )
    assert "error_code=output_limit" in caplog.text
    assert "input_tokens=500 output_tokens=2048" in caplog.text
    assert "requested_model_id=fixture-small" in caplog.text
    assert "private" not in caplog.text
    assert "sk-fixture" not in caplog.text


def test_request_and_attachment_reject_unsafe_unbounded_values() -> None:
    with pytest.raises(ValueError, match="relative file name"):
        EvidenceAttachment(name="../paper.pdf", media_type="application/pdf", content=b"pdf")
    with pytest.raises(ValueError, match="media type"):
        EvidenceAttachment(name="paper.pdf", media_type="", content=b"pdf")
    with pytest.raises(ValueError, match="visual media type"):
        EvidenceAttachment(name="paper.svg", media_type="image/svg+xml", content=b"<svg/>")
    with pytest.raises(ValueError, match="extension"):
        EvidenceAttachment(name="paper.json", media_type="image/png", content=b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match="content signature"):
        EvidenceAttachment(name="paper.png", media_type="image/png", content=b"not-png")
    with pytest.raises(ValueError, match="task_id"):
        AgentRequest(
            task_id="../escape",
            task_kind=AgentTaskKind.REPORT,
            prompt="prompt",
            output_schema={},
            reasoning_effort=ReasoningEffort.HIGH,
            service_tier=ServiceTier.FLEX,
        )
    with pytest.raises(ValueError, match="timeout_seconds"):
        AgentRequest(
            task_id="job-safe",
            task_kind=AgentTaskKind.REPORT,
            prompt="prompt",
            output_schema={},
            reasoning_effort=ReasoningEffort.HIGH,
            service_tier=ServiceTier.FLEX,
            timeout_seconds=5,
        )
    with pytest.raises(ValueError, match="model_id"):
        AgentRequest(
            task_id="job-safe",
            task_kind=AgentTaskKind.REPORT,
            prompt="prompt",
            output_schema={},
            model_id="bad\nmodel",
            reasoning_effort=ReasoningEffort.HIGH,
            service_tier=ServiceTier.FLEX,
        )

    visual = lambda index: EvidenceAttachment(  # noqa: E731 - compact bounded fixture
        name=f"visual-{index}.png",
        media_type="image/png",
        content=b"\x89PNG\r\n\x1a\nfixture",
    )
    with pytest.raises(ValueError, match="six visual"):
        AgentRequest(
            task_id="job-visual-count",
            task_kind=AgentTaskKind.REPORT,
            prompt="prompt",
            output_schema={},
            reasoning_effort=ReasoningEffort.HIGH,
            service_tier=ServiceTier.FLEX,
            attachments=tuple(visual(index) for index in range(7)),
        )


def test_capability_inspection_does_not_discover_models_or_generate() -> None:
    backend = FakeBackend()
    registry = AgentBackendRegistry((backend,))

    capabilities = registry.capabilities()

    assert capabilities == (backend.capability,)
    assert backend.discovery_calls == 0
    assert backend.generation_calls == 0


@pytest.mark.asyncio
async def test_registry_discovers_and_generates_through_the_selected_backend() -> None:
    backend = FakeBackend()
    registry = AgentBackendRegistry((backend,))
    fragments: list[AgentFragment] = []

    catalog = await registry.discover_models("fake")
    result = await registry.generate(
        "fake",
        _request(),
        on_fragment=_append(fragments),
        cancelled=lambda: False,
    )

    assert [model.model_id for model in catalog.models] == ["fixture-small"]
    assert fragments == [AgentFragment(index=0, kind="text", text="fixture")]
    assert result.structured_output == {"summary": "fixture"}
    assert result.usage == AgentUsage(input_tokens=12, output_tokens=4)
    assert backend.discovery_calls == 1
    assert backend.generation_calls == 1


@pytest.mark.asyncio
async def test_registry_preserves_typed_cancellation_errors() -> None:
    registry = AgentBackendRegistry((FakeBackend(),))

    with pytest.raises(AgentBackendError) as captured:
        await registry.generate(
            "fake",
            _request(),
            on_fragment=_append([]),
            cancelled=lambda: True,
        )

    assert captured.value.code is AgentErrorCode.CANCELLED
    assert captured.value.retryable is True


def test_registry_rejects_duplicate_or_unknown_backends() -> None:
    with pytest.raises(ValueError, match="Duplicate Agent backend"):
        AgentBackendRegistry((FakeBackend(), FakeBackend()))

    registry = AgentBackendRegistry((FakeBackend(),))
    with pytest.raises(KeyError, match="Unknown Agent backend"):
        registry.require("missing")


def test_catalog_rejects_duplicate_model_ids_and_sorts_stably() -> None:
    catalog = ModelCatalog(
        models=(
            AgentModel(model_id="zeta", display_name="Zeta"),
            AgentModel(model_id="alpha", display_name="Alpha"),
        ),
        supports_manual_model_id=True,
        supports_default_model=True,
    )
    assert [model.model_id for model in catalog.models] == ["alpha", "zeta"]

    with pytest.raises(ValueError, match="Duplicate model ID"):
        ModelCatalog(
            models=(
                AgentModel(model_id="same", display_name="One"),
                AgentModel(model_id="same", display_name="Two"),
            )
        )


def _append(target: list[AgentFragment]) -> Callable[[AgentFragment], Awaitable[None]]:
    async def append(value: AgentFragment) -> None:
        target.append(value)

    return append
