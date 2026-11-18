"""Registry for explicit Agent backend selection."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

import httpx

from paper_reader.agents.contracts import (
    AgentBackend,
    AgentBackendError,
    AgentCapability,
    AgentRequest,
    AgentResult,
    CancellationCheck,
    FragmentCallback,
    ModelCatalog,
)
from paper_reader.agents.process_policy import redact_diagnostic

logger = logging.getLogger(__name__)


class AgentBackendRegistry:
    def __init__(self, backends: Iterable[AgentBackend]) -> None:
        self._backends: dict[str, AgentBackend] = {}
        for backend in backends:
            self.register(backend)
        self._builtin_backends = dict(self._backends)

    def register(self, backend: AgentBackend) -> None:
        backend_id = backend.capability.backend_id
        if backend_id in self._backends:
            raise ValueError(f"Duplicate Agent backend: {backend_id}")
        self._backends[backend_id] = backend

    def replace_extensions(self, backends: Iterable[AgentBackend]) -> tuple[str, ...]:
        extensions: dict[str, AgentBackend] = {}
        conflicts: list[str] = []
        for backend in backends:
            backend_id = backend.capability.backend_id
            if backend_id in self._builtin_backends or backend_id in extensions:
                conflicts.append(backend_id)
                continue
            extensions[backend_id] = backend
        self._backends = {**self._builtin_backends, **extensions}
        return tuple(sorted(conflicts))

    def replace_builtins(self, backends: Iterable[AgentBackend]) -> tuple[str, ...]:
        """Refresh runtime-built backends in place so long-lived services keep one registry."""

        replacements: dict[str, AgentBackend] = {}
        for backend in backends:
            backend_id = backend.capability.backend_id
            if backend_id in replacements:
                raise ValueError(f"Duplicate Agent backend: {backend_id}")
            replacements[backend_id] = backend
        extensions = {
            backend_id: backend
            for backend_id, backend in self._backends.items()
            if backend_id not in self._builtin_backends
        }
        conflicts = tuple(sorted(set(replacements) & set(extensions)))
        self._builtin_backends = replacements
        self._backends = {
            **replacements,
            **{key: value for key, value in extensions.items() if key not in replacements},
        }
        return conflicts

    def get(self, backend_id: str) -> AgentBackend | None:
        return self._backends.get(backend_id)

    def capabilities(self) -> tuple[AgentCapability, ...]:
        return tuple(
            backend.capability
            for _, backend in sorted(self._backends.items(), key=lambda item: item[0])
        )

    def all(self) -> tuple[AgentBackend, ...]:
        return tuple(backend for _, backend in sorted(self._backends.items()))

    def require(self, backend_id: str) -> AgentBackend:
        backend = self._backends.get(backend_id)
        if backend is None:
            raise KeyError(f"Unknown Agent backend: {backend_id}")
        return backend

    async def discover_models(self, backend_id: str) -> ModelCatalog:
        return await self.require(backend_id).discover_models()

    async def generate(
        self,
        backend_id: str,
        request: AgentRequest,
        *,
        on_fragment: FragmentCallback,
        cancelled: CancellationCheck,
    ) -> AgentResult:
        logger.info(
            "agent invocation started task_kind=%s backend_id=%s requested_model_id=%s",
            request.task_kind.value,
            backend_id,
            redact_diagnostic(request.model_id or "default", limit=200),
        )
        try:
            result = await self.require(backend_id).generate(
                request,
                on_fragment=on_fragment,
                cancelled=cancelled,
            )
        except Exception as error:
            usage = error.usage if isinstance(error, AgentBackendError) else None
            logger.warning(
                "agent invocation failed task_kind=%s backend_id=%s requested_model_id=%s "
                "error_type=%s error_code=%s retryable=%s input_tokens=%s output_tokens=%s",
                request.task_kind.value,
                backend_id,
                redact_diagnostic(request.model_id or "default", limit=200),
                type(error).__name__,
                error.code.value if isinstance(error, AgentBackendError) else "unexpected",
                error.retryable if isinstance(error, AgentBackendError) else False,
                usage.input_tokens if usage else None,
                usage.output_tokens if usage else None,
            )
            raise
        logger.info(
            "agent invocation completed task_kind=%s backend_id=%s "
            "resolved_model_id=%s input_tokens=%s output_tokens=%s",
            request.task_kind.value,
            backend_id,
            redact_diagnostic(result.resolved_model_id or request.model_id or "default", limit=200),
            result.usage.input_tokens,
            result.usage.output_tokens,
        )
        return result


def build_agent_registry(
    *,
    cache_root: Path,
    client: httpx.AsyncClient,
    ollama_cloud_api_key: str | None,
) -> AgentBackendRegistry:
    from paper_reader.agents.codex_cli import CodexCliBackend
    from paper_reader.agents.ollama import OllamaBackend

    return AgentBackendRegistry(
        (
            CodexCliBackend(cache_root=cache_root),
            OllamaBackend.local(client=client),
            OllamaBackend.cloud(client=client, api_key=ollama_cloud_api_key),
        )
    )
