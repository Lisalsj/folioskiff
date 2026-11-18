from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentCapability,
    AgentErrorCode,
    AgentLocation,
    AgentModel,
    AgentProbeCode,
    AgentRequest,
    AgentResult,
    AgentUsage,
    ModelCatalog,
    ReasoningEffort,
)
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.discovery.agent_backend import AgentKeywordSuggestionBackend
from paper_reader.discovery.suggestions import (
    KeywordSuggestionContext,
    KeywordSuggestionGenerationError,
)


def _capability(backend_id: str) -> AgentCapability:
    return AgentCapability(
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
        degradation_reasons=(),
    )


@dataclass
class FixtureBackend:
    output: dict[str, Any]
    backend_id: str = "ollama_local"

    def __post_init__(self) -> None:
        self.capability = _capability(self.backend_id)
        self.cached_model_catalog = ModelCatalog(
            models=(AgentModel("z-model", "Z"), AgentModel("a-model", "A")),
        )
        self.requests: list[AgentRequest] = []

    async def probe(self) -> AgentCapability:
        return self.capability

    async def discover_models(self) -> ModelCatalog:
        return self.cached_model_catalog

    async def generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: Any,
        cancelled: Any,
    ) -> AgentResult:
        self.requests.append(request)
        return AgentResult(
            raw_output="fixture",
            structured_output=self.output,
            resolved_model_id=request.model_id,
            usage=AgentUsage(input_tokens=4, output_tokens=5),
        )


def _definition() -> dict[str, Any]:
    terms = [
        "robot",
        "机器人",
        "robotics",
        "机器人学",
        "autonomous robot",
        "自主机器人",
        "robot control",
        "机器人控制",
        "robot perception",
        "机器人感知",
        "robot navigation",
        "机器人导航",
    ]
    return {
        "required": [{"key": "robot", "label": "机器人", "terms": terms}],
        "boost": [],
        "excluded": [],
    }


@pytest.mark.asyncio
async def test_keyword_adapter_uses_default_backend_model_and_strict_request() -> None:
    backend = FixtureBackend(_definition())
    registry = AgentBackendRegistry((backend,))
    preferences = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            default_agent_backend="ollama_local",
            default_agent_model_id=None,
            default_reasoning_effort="medium",
        )
    )
    adapter = AgentKeywordSuggestionBackend(registry=registry, preferences=preferences)

    result = await adapter.suggest(
        KeywordSuggestionContext("robot", 12, None, (), ()),
    )

    assert result.required[0].key == "robot"
    assert backend.requests[0].task_kind.value == "keywords"
    assert backend.requests[0].model_id == "a-model"
    assert backend.requests[0].output_schema["additionalProperties"] is False
    assert adapter.invocation_info == {
        "call_mode": "model",
        "task_kind": "keywords",
        "backend_id": "ollama_local",
        "requested_model_id": None,
        "resolved_model_id": "a-model",
        "reasoning_effort": "medium",
        "service_tier": "fast",
        "usage": {"input_tokens": 4, "output_tokens": 5},
    }


@pytest.mark.asyncio
async def test_keyword_adapter_rejects_model_output_outside_bilingual_count() -> None:
    invalid = {
        "required": [{"key": "robot", "label": "机器人", "terms": ["robot", "机器人"]}],
        "boost": [],
        "excluded": [],
    }
    backend = FixtureBackend(invalid)
    registry = AgentBackendRegistry((backend,))
    preferences = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            default_agent_backend="ollama_local",
            default_agent_model_id="a-model",
            default_reasoning_effort="medium",
        )
    )
    adapter = AgentKeywordSuggestionBackend(registry=registry, preferences=preferences)

    with pytest.raises(KeywordSuggestionGenerationError) as error:
        await adapter.suggest(KeywordSuggestionContext("robot", 12, None, (), ()))

    assert error.value.code == "invalid_output"
    assert error.value.backend_id == "ollama_local"


@pytest.mark.asyncio
async def test_keyword_adapter_rejects_missing_ollama_model_before_generation() -> None:
    backend = FixtureBackend(_definition())
    registry = AgentBackendRegistry((backend,))
    preferences = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            default_agent_backend="ollama_local",
            default_agent_model_id="missing-model",
            default_reasoning_effort="medium",
        )
    )
    adapter = AgentKeywordSuggestionBackend(registry=registry, preferences=preferences)

    with pytest.raises(KeywordSuggestionGenerationError) as error:
        await adapter.suggest(KeywordSuggestionContext("robot", 12, None, (), ()))

    assert error.value.code == "model_missing"
    assert backend.requests == []


@pytest.mark.asyncio
async def test_keyword_adapter_accepts_per_request_agent_selection() -> None:
    backend = FixtureBackend(_definition(), backend_id="codex_cli")
    registry = AgentBackendRegistry((backend,))
    preferences = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            default_agent_backend="ollama_local",
            default_agent_model_id="default-model",
            default_reasoning_effort="low",
        )
    )
    adapter = AgentKeywordSuggestionBackend(
        registry=registry,
        preferences=preferences,
        backend_id="codex_cli",
        model_id="z-model",
        reasoning_effort=ReasoningEffort.HIGH,
    )

    await adapter.suggest(KeywordSuggestionContext("robot", 12, None, (), ()))

    request = backend.requests[0]
    assert request.model_id == "z-model"
    assert request.reasoning_effort is ReasoningEffort.HIGH
    assert request.context_window == 32_768
    assert request.max_output_tokens == 16_384


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_backend", ["ollama_local", "codex_cli"])
async def test_keyword_auto_selection_does_not_inherit_another_backends_model(
    selected_backend: str,
) -> None:
    backend = FixtureBackend(_definition(), backend_id=selected_backend)
    preferences = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            default_agent_backend="codex_cli",
            default_agent_model_id="saved-openai-model",
            default_reasoning_effort="medium",
        )
    )
    adapter = AgentKeywordSuggestionBackend(
        registry=AgentBackendRegistry((backend,)),
        preferences=preferences,
        backend_id=selected_backend,
        use_default_model=False,
    )
    await adapter.suggest(KeywordSuggestionContext("robot", 12, None, (), ()))
    assert backend.requests[0].model_id == (
        "a-model" if selected_backend == "ollama_local" else None
    )


@pytest.mark.asyncio
async def test_keyword_cloud_schema_failure_is_not_retried_by_both_layers() -> None:
    class InvalidBackend(FixtureBackend):
        async def generate(
            self, request: AgentRequest, *, on_fragment: Any, cancelled: Any
        ) -> AgentResult:
            self.requests.append(request)
            raise AgentBackendError(AgentErrorCode.INVALID_OUTPUT, "fixture failure")

    backend = InvalidBackend(_definition(), backend_id="ollama_cloud")
    preferences = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            default_agent_backend="ollama_cloud",
            default_agent_model_id=None,
            default_reasoning_effort="medium",
        )
    )
    adapter = AgentKeywordSuggestionBackend(
        registry=AgentBackendRegistry((backend,)),
        preferences=preferences,
    )
    with pytest.raises(KeywordSuggestionGenerationError) as failure:
        await adapter.suggest(KeywordSuggestionContext("robot", 12, None, (), ()))
    assert failure.value.code == "invalid_output"
    assert len(backend.requests) == 1
