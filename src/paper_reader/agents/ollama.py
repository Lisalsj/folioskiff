"""Fixed-origin Ollama Local and Ollama Cloud Agent backends."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, cast

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

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
    CancellationCheck,
    FragmentCallback,
    ModelCatalog,
    ReasoningEffort,
    is_visual_attachment,
)
from paper_reader.agents.process_policy import redact_diagnostic

OLLAMA_LOCAL_BASE_URL = "http://127.0.0.1:11434/api"
OLLAMA_CLOUD_BASE_URL = "https://ollama.com/api"
_MAX_CATALOG_BYTES = 2 * 1024 * 1024
_MAX_STREAM_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _ChatResponse:
    content: str
    usage: AgentUsage
    next_fragment_index: int
    done_reason: str | None
    model_id: str


class OllamaBackend:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        backend_id: str,
        display_name: str,
        location: AgentLocation,
        base_url: str,
        api_key: str | None,
    ) -> None:
        self._client = client
        self._backend_id = backend_id
        self._display_name = display_name
        self._location = location
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key.strip() if api_key is not None and api_key.strip() else None
        authenticated = location is AgentLocation.LOCAL or self._api_key is not None
        self._cached_model_catalog: ModelCatalog | None = None
        self._thinking_controls: dict[str, tuple[bool | str, ...] | None] = {}
        self._capability = self._new_capability(
            probe_code=(
                AgentProbeCode.UNAVAILABLE if authenticated else AgentProbeCode.NOT_AUTHENTICATED
            ),
            installed=location is AgentLocation.CLOUD,
            authenticated=authenticated,
            available=False,
            degradation_reasons=(
                ("ollama_capability_not_probed",) if authenticated else ("missing_credential",)
            ),
        )

    @classmethod
    def local(cls, *, client: httpx.AsyncClient) -> OllamaBackend:
        return cls(
            client=client,
            backend_id="ollama_local",
            display_name="Ollama Local",
            location=AgentLocation.LOCAL,
            base_url=OLLAMA_LOCAL_BASE_URL,
            api_key=None,
        )

    @classmethod
    def cloud(
        cls,
        *,
        client: httpx.AsyncClient,
        api_key: str | None,
    ) -> OllamaBackend:
        return cls(
            client=client,
            backend_id="ollama_cloud",
            display_name="Ollama Cloud",
            location=AgentLocation.CLOUD,
            base_url=OLLAMA_CLOUD_BASE_URL,
            api_key=api_key,
        )

    @property
    def capability(self) -> AgentCapability:
        return self._capability

    @property
    def cached_model_catalog(self) -> ModelCatalog | None:
        return self._cached_model_catalog

    async def probe(self) -> AgentCapability:
        if self._location is AgentLocation.CLOUD and self._api_key is None:
            self._cached_model_catalog = ModelCatalog(degradation_reasons=("missing_credential",))
            self._capability = self._new_capability(
                probe_code=AgentProbeCode.NOT_AUTHENTICATED,
                installed=True,
                authenticated=False,
                available=False,
                degradation_reasons=("missing_credential",),
            )
            return self._capability
        try:
            await self.discover_models()
        except AgentBackendError as error:
            if error.code is AgentErrorCode.TIMEOUT:
                probe_code = AgentProbeCode.PROBE_TIMEOUT
            elif error.code is AgentErrorCode.AUTHENTICATION:
                probe_code = AgentProbeCode.NOT_AUTHENTICATED
            elif self._location is AgentLocation.LOCAL and error.code is AgentErrorCode.TRANSPORT:
                probe_code = AgentProbeCode.SERVICE_NOT_RUNNING
            else:
                probe_code = AgentProbeCode.UNAVAILABLE
            self._cached_model_catalog = ModelCatalog(
                degradation_reasons=(f"model_discovery_{error.code.value}",)
            )
            self._capability = self._new_capability(
                probe_code=probe_code,
                installed=(
                    self._location is AgentLocation.CLOUD
                    or error.code not in {AgentErrorCode.UNAVAILABLE, AgentErrorCode.TRANSPORT}
                ),
                authenticated=(
                    self._location is AgentLocation.LOCAL
                    or error.code is not AgentErrorCode.AUTHENTICATION
                ),
                available=False,
                degradation_reasons=(f"ollama_{error.code.value}",),
            )
        else:
            self._capability = self._new_capability(
                probe_code=AgentProbeCode.READY,
                installed=True,
                authenticated=True,
                available=True,
                degradation_reasons=(),
            )
        return self._capability

    async def discover_models(self) -> ModelCatalog:
        self._require_cloud_credential()
        try:
            response = await self._client.get(
                f"{self._base_url}/tags",
                headers=self._headers(ndjson=False),
                timeout=20,
            )
        except httpx.TimeoutException as error:
            raise AgentBackendError(
                AgentErrorCode.TIMEOUT,
                f"{self._display_name} model discovery timed out",
                retryable=True,
            ) from error
        except httpx.HTTPError as error:
            raise AgentBackendError(
                AgentErrorCode.TRANSPORT,
                f"{self._display_name} model discovery failed",
                retryable=True,
                diagnostic=type(error).__name__,
            ) from error
        self._raise_for_status(response, operation="model discovery")
        if len(response.content) > _MAX_CATALOG_BYTES:
            raise AgentBackendError(
                AgentErrorCode.OUTPUT_LIMIT,
                "Ollama model catalog exceeded the configured limit",
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise AgentBackendError(
                AgentErrorCode.PROTOCOL,
                "Ollama model catalog was not valid JSON",
            ) from error
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise AgentBackendError(
                AgentErrorCode.PROTOCOL,
                "Ollama model catalog had an invalid shape",
            )
        identifiers: dict[str, AgentModel] = {}
        for item in payload["models"]:
            if not isinstance(item, dict):
                raise AgentBackendError(
                    AgentErrorCode.PROTOCOL,
                    "Ollama model catalog contained an invalid item",
                )
            model_id = item.get("name") or item.get("model")
            if not isinstance(model_id, str):
                raise AgentBackendError(
                    AgentErrorCode.PROTOCOL,
                    "Ollama model catalog item omitted its model ID",
                )
            details = item.get("details")
            context_window = (
                details.get("context_length")
                if isinstance(details, dict) and isinstance(details.get("context_length"), int)
                else None
            )
            identifiers.setdefault(
                model_id,
                AgentModel(
                    model_id=model_id,
                    display_name=model_id,
                    supports_vision=_declared_vision_capability(item),
                    context_window=context_window,
                ),
            )
        catalog = ModelCatalog(
            models=tuple(identifiers.values()),
            supports_manual_model_id=True,
            supports_default_model=bool(identifiers),
        )
        self._cached_model_catalog = catalog
        self._thinking_controls.clear()
        return catalog

    async def generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: FragmentCallback,
        cancelled: CancellationCheck,
    ) -> AgentResult:
        try:
            async with asyncio.timeout(request.timeout_seconds):
                return await self._generate(
                    request,
                    on_fragment=on_fragment,
                    cancelled=cancelled,
                )
        except TimeoutError as error:
            raise AgentBackendError(
                AgentErrorCode.TIMEOUT,
                "Ollama generation timed out; try a smaller model or lower reasoning effort",
                retryable=True,
            ) from error

    async def _generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: FragmentCallback,
        cancelled: CancellationCheck,
    ) -> AgentResult:
        if cancelled():
            raise AgentBackendError(
                AgentErrorCode.CANCELLED,
                "Ollama generation was cancelled before the request",
                retryable=True,
            )
        self._require_cloud_credential()
        if request.model_id is None:
            catalog = self._cached_model_catalog or await self.discover_models()
            if not catalog.models:
                raise AgentBackendError(
                    AgentErrorCode.UNAVAILABLE,
                    "Ollama has no installed models; install or select a model first",
                )
            request = replace(request, model_id=catalog.models[0].model_id)
        if request.external_research:
            raise AgentBackendError(
                AgentErrorCode.PROTOCOL,
                "Ollama backends do not provide external web research",
            )
        validator = _validator(request.output_schema)
        model_id = cast(str, request.model_id)
        think = _think_value(
            request.reasoning_effort,
            await self._discover_thinking_controls(model_id),
            model_id=model_id,
        )
        context_window = request.context_window
        max_output_tokens = request.max_output_tokens
        if self._location is AgentLocation.LOCAL:
            max_output_tokens = max_output_tokens or (
                2_048 if request.reasoning_effort is ReasoningEffort.MINIMAL else 16_384
            )
            context_window = context_window or _context_window(request, max_output_tokens)
        initial_message: dict[str, object] = {
            "role": "user",
            "content": request.prompt,
        }
        images = [
            base64.b64encode(attachment.content).decode("ascii")
            for attachment in request.attachments
            if is_visual_attachment(attachment)
        ]
        if images:
            initial_message["images"] = images
        messages: list[dict[str, object]] = [initial_message]
        maximum_attempts = 2 if self._location is AgentLocation.CLOUD else 1
        fragment_index = 0
        input_tokens = 0
        output_tokens = 0
        validation_diagnostic = ""
        for attempt in range(maximum_attempts):
            if cancelled():
                raise AgentBackendError(
                    AgentErrorCode.CANCELLED,
                    "Ollama generation was cancelled",
                    retryable=True,
                )
            response = await self._chat(
                model_id=model_id,
                messages=messages,
                output_schema=request.output_schema,
                think=think,
                timeout_seconds=request.timeout_seconds,
                context_window=context_window,
                max_output_tokens=max_output_tokens,
                on_fragment=on_fragment,
                fragment_index=fragment_index,
                cancelled=cancelled,
            )
            raw, usage = response.content, response.usage
            fragment_index = response.next_fragment_index
            input_tokens += usage.input_tokens or 0
            output_tokens += usage.output_tokens or 0
            parsed, validation_diagnostic = _validated_output(raw, validator)
            if parsed is not None:
                return AgentResult(
                    raw_output=raw,
                    structured_output=parsed,
                    resolved_model_id=response.model_id,
                    usage=AgentUsage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    ),
                )
            if response.done_reason == "length":
                raise AgentBackendError(
                    AgentErrorCode.OUTPUT_LIMIT,
                    "Ollama exhausted the output budget before completing the answer; "
                    "lower reasoning effort or shorten the evidence",
                    diagnostic="done_reason=length",
                    usage=AgentUsage(input_tokens=input_tokens, output_tokens=output_tokens),
                )
            if attempt + 1 < maximum_attempts:
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": _repair_instruction(
                            request.output_schema,
                            validation_diagnostic,
                        ),
                    },
                ]
        raise AgentBackendError(
            AgentErrorCode.INVALID_OUTPUT,
            "Ollama did not produce output matching the required schema",
            diagnostic=redact_diagnostic(validation_diagnostic),
            usage=AgentUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        )

    async def _discover_thinking_controls(self, model_id: str) -> tuple[bool | str, ...] | None:
        if model_id not in self._thinking_controls:
            controls: tuple[bool | str, ...] | None = None
            try:
                response = await self._client.post(
                    f"{self._base_url}/show",
                    headers=self._headers(ndjson=False),
                    json={"model": model_id},
                    timeout=10,
                )
                if response.is_success and len(response.content) <= _MAX_CATALOG_BYTES:
                    metadata = response.json()
                    thinking = metadata.get("thinking") if isinstance(metadata, dict) else None
                    values = thinking.get("values") if isinstance(thinking, dict) else None
                    if (
                        isinstance(values, list)
                        and values
                        and all(
                            isinstance(value, bool) or isinstance(value, str) and value.strip()
                            for value in values
                        )
                    ):
                        controls = tuple(values)
                    elif (
                        isinstance(metadata, dict)
                        and isinstance(metadata.get("details"), dict)
                        and metadata["details"].get("family") == "gptoss"
                    ):
                        controls = ("low", "medium", "high")
                    elif (
                        isinstance(metadata, dict)
                        and isinstance(
                            metadata.get("capabilities"),
                            list,
                        )
                        and "thinking" not in metadata["capabilities"]
                    ):
                        controls = (False,)
            except (httpx.HTTPError, ValueError):
                # Older/local and cloud servers may omit /show or thinking
                # metadata. Generation still uses the compatible fallback.
                pass
            self._thinking_controls[model_id] = controls
        return self._thinking_controls[model_id]

    async def _chat(
        self,
        *,
        model_id: str,
        messages: list[dict[str, object]],
        output_schema: Mapping[str, Any],
        think: bool | str | None,
        timeout_seconds: float,
        context_window: int | None,
        max_output_tokens: int | None,
        on_fragment: FragmentCallback,
        fragment_index: int,
        cancelled: CancellationCheck,
    ) -> _ChatResponse:
        payload: dict[str, object] = {
            "model": model_id,
            "messages": messages,
            "stream": True,
        }
        if think is not None:
            payload["think"] = think
        options: dict[str, int] = {}
        if context_window is not None and self._location is AgentLocation.LOCAL:
            options["num_ctx"] = context_window
        if max_output_tokens is not None:
            options["num_predict"] = max_output_tokens
        if options:
            payload["options"] = options
        if self._location is AgentLocation.LOCAL:
            payload["format"] = output_schema
        chunks: list[str] = []
        total_bytes = 0
        input_tokens: int | None = None
        output_tokens: int | None = None
        completed = False
        done_reason: str | None = None
        resolved_model_id = model_id
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/chat",
                headers=self._headers(ndjson=True),
                json=payload,
                timeout=timeout_seconds,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    self._raise_for_status(response, operation="generation")
                async for line in response.aiter_lines():
                    if cancelled():
                        raise AgentBackendError(
                            AgentErrorCode.CANCELLED,
                            "Ollama generation was cancelled",
                            retryable=True,
                        )
                    if not line.strip():
                        continue
                    total_bytes += len(line.encode("utf-8"))
                    if total_bytes > _MAX_STREAM_BYTES:
                        raise AgentBackendError(
                            AgentErrorCode.OUTPUT_LIMIT,
                            "Ollama response exceeded the configured limit",
                        )
                    event = _json_object(line, "Ollama emitted malformed NDJSON")
                    if "error" in event:
                        self._raise_ollama_error(event["error"], operation="generation")
                    message = event.get("message")
                    if isinstance(message, dict):
                        thinking = message.get("thinking")
                        if thinking is not None and not isinstance(thinking, str):
                            raise AgentBackendError(
                                AgentErrorCode.PROTOCOL,
                                "Ollama response message contained invalid thinking text",
                            )
                        if thinking:
                            await on_fragment(
                                AgentFragment(
                                    index=fragment_index,
                                    kind="reasoning",
                                    text=thinking,
                                )
                            )
                            fragment_index += 1
                        content = message.get("content")
                        # Thinking models may emit reasoning-only chunks before
                        # the answer. Those chunks legitimately omit content.
                        if content is not None and not isinstance(content, str):
                            raise AgentBackendError(
                                AgentErrorCode.PROTOCOL,
                                "Ollama response message contained invalid text content",
                            )
                        if isinstance(content, str) and content:
                            chunks.append(content)
                            await on_fragment(
                                AgentFragment(
                                    index=fragment_index,
                                    kind="text",
                                    text=content,
                                )
                            )
                            fragment_index += 1
                    if event.get("done") is True:
                        completed = True
                        done_reason = event.get("done_reason")
                        resolved = event.get("model")
                        if (
                            isinstance(resolved, str)
                            and 0 < len(resolved) <= 200
                            and all(
                                ord(character) >= 32 and ord(character) != 127
                                for character in resolved
                            )
                        ):
                            resolved_model_id = resolved
                        input_tokens = _optional_count(event.get("prompt_eval_count"))
                        output_tokens = _optional_count(event.get("eval_count"))
                        break
        except AgentBackendError:
            raise
        except httpx.TimeoutException as error:
            raise AgentBackendError(
                AgentErrorCode.TIMEOUT,
                "Ollama generation timed out",
                retryable=True,
            ) from error
        except httpx.HTTPError as error:
            raise AgentBackendError(
                AgentErrorCode.TRANSPORT,
                "Ollama generation transport failed",
                retryable=True,
                diagnostic=type(error).__name__,
            ) from error
        if not completed:
            raise AgentBackendError(
                AgentErrorCode.PROTOCOL,
                "Ollama stream ended without a completion event",
                retryable=True,
            )
        return _ChatResponse(
            content="".join(chunks),
            usage=AgentUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            next_fragment_index=fragment_index,
            done_reason=done_reason,
            model_id=resolved_model_id,
        )

    def _headers(self, *, ndjson: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/x-ndjson" if ndjson else "application/json",
        }
        if self._location is AgentLocation.CLOUD and self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _require_cloud_credential(self) -> None:
        if self._location is AgentLocation.CLOUD and self._api_key is None:
            raise AgentBackendError(
                AgentErrorCode.AUTHENTICATION,
                "Ollama Cloud API key is not configured",
            )

    def _raise_for_status(self, response: httpx.Response, *, operation: str) -> None:
        if response.status_code < 400:
            return
        if response.status_code in {401, 403}:
            code = AgentErrorCode.AUTHENTICATION
            retryable = False
        elif response.status_code in {408, 429} or response.status_code >= 500:
            code = AgentErrorCode.UNAVAILABLE
            retryable = True
        else:
            code = AgentErrorCode.PROTOCOL
            retryable = False
        try:
            payload = response.json()
            message = payload.get("error") if isinstance(payload, dict) else None
        except ValueError:
            message = None
        self._raise_ollama_error(
            message,
            operation=operation,
            code=code,
            retryable=retryable,
            diagnostic=f"HTTP {response.status_code}",
        )

    def _raise_ollama_error(
        self,
        value: object,
        *,
        operation: str,
        code: AgentErrorCode = AgentErrorCode.UNAVAILABLE,
        retryable: bool = True,
        diagnostic: str | None = None,
    ) -> None:
        # Classify provider errors without exposing response bodies, model
        # paths, prompts, or credentials in user-visible messages.
        message = f"{self._display_name} {operation} failed"
        text = value.casefold() if isinstance(value, str) else ""
        if code is not AgentErrorCode.AUTHENTICATION:
            if "model" in text and any(part in text for part in ("not found", "does not exist")):
                code, retryable = AgentErrorCode.PROTOCOL, False
                message = "Ollama model is not installed; select an available model"
            elif any(part in text for part in ("out of memory", "requires more", "cuda error")):
                code, retryable = AgentErrorCode.UNAVAILABLE, False
                message = "Ollama could not load the model; use a smaller model or context window"
            elif "think" in text and any(part in text for part in ("not support", "unsupported")):
                code, retryable = AgentErrorCode.PROTOCOL, False
                message = "Ollama model does not support the selected thinking mode"
            elif any(part in text for part in ("context length", "context window", "too long")):
                code, retryable = AgentErrorCode.OUTPUT_LIMIT, False
                message = "Ollama context is too small; shorten the evidence"
        raise AgentBackendError(
            code,
            message,
            retryable=retryable,
            diagnostic=diagnostic,
        )

    def _new_capability(
        self,
        *,
        probe_code: AgentProbeCode,
        installed: bool,
        authenticated: bool,
        available: bool,
        degradation_reasons: tuple[str, ...],
    ) -> AgentCapability:
        return AgentCapability(
            backend_id=self._backend_id,
            display_name=self._display_name,
            location=self._location,
            probe_code=probe_code,
            installed=installed,
            authenticated=authenticated,
            available=available,
            supports_schema=True,
            supports_streaming=True,
            supports_vision=True,
            supports_external_research=False,
            degradation_reasons=degradation_reasons,
        )


def _validator(schema: Mapping[str, Any]) -> Draft202012Validator:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise AgentBackendError(
            AgentErrorCode.PROTOCOL,
            "Application supplied an invalid output schema",
            diagnostic=redact_diagnostic(str(error)),
        ) from error
    return Draft202012Validator(schema)


def _validated_output(
    value: str,
    validator: Draft202012Validator,
) -> tuple[dict[str, Any] | None, str]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        return None, f"Output is not valid JSON: {error.msg}"
    if not isinstance(payload, dict):
        return None, "Output must be a JSON object"
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
    if errors:
        summary = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '$'}: {error.validator}"
            for error in errors[:10]
        )
        return None, summary[:4000]
    return cast(dict[str, Any], payload), ""


def _repair_instruction(schema: Mapping[str, Any], diagnostic: str) -> str:
    return (
        "Repair the previous response. Return only one JSON object that validates "
        "against this schema; do not add Markdown fences or commentary.\n"
        f"Validation errors: {diagnostic}\n"
        f"JSON Schema: {json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
    )


def _json_object(value: str, message: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise AgentBackendError(AgentErrorCode.PROTOCOL, message) from error
    if not isinstance(payload, dict):
        raise AgentBackendError(AgentErrorCode.PROTOCOL, message)
    return cast(dict[str, Any], payload)


def _optional_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _think_value(
    value: ReasoningEffort,
    controls: tuple[bool | str, ...] | None,
    *,
    model_id: str,
) -> bool | str | None:
    if controls is None:
        controls = ("low", "medium", "high") if "gpt-oss" in model_id.casefold() else (False, True)
    if controls == (False,):
        return None
    level = {
        ReasoningEffort.MINIMAL: "low",
        ReasoningEffort.XHIGH: "high",
    }.get(value, value.value)
    if value is ReasoningEffort.MINIMAL and False in controls:
        return False
    if level in controls:
        return level
    if True in controls:
        return True
    levels = [item for item in controls if isinstance(item, str)]
    return levels[0] if value in {ReasoningEffort.MINIMAL, ReasoningEffort.LOW} else levels[-1]


def _context_window(request: AgentRequest, output_tokens: int) -> int:
    # A conservative UTF-8 estimate avoids inheriting a 262k global context for
    # short tasks, while allowing longer reports to reserve enough space.
    evidence_bytes = len(request.prompt.encode("utf-8")) + len(json.dumps(request.output_schema))
    visual_tokens = 4_096 * sum(is_visual_attachment(item) for item in request.attachments)
    required = evidence_bytes // 2 + output_tokens + visual_tokens + 1_024
    if required > 131_072:
        raise AgentBackendError(
            AgentErrorCode.OUTPUT_LIMIT,
            "Ollama evidence exceeds the automatic context limit; shorten the evidence",
        )
    return max(16_384, 1 << (required - 1).bit_length())


def _declared_vision_capability(item: dict[str, Any]) -> bool | None:
    capabilities = item.get("capabilities")
    if isinstance(capabilities, list) and all(isinstance(value, str) for value in capabilities):
        return any(value.casefold() == "vision" for value in capabilities)
    details = item.get("details")
    if not isinstance(details, dict):
        return None
    raw_families = details.get("families")
    if raw_families is None and isinstance(details.get("family"), str):
        raw_families = [details["family"]]
    if not isinstance(raw_families, list) or not all(
        isinstance(value, str) for value in raw_families
    ):
        return None
    visual_families = {
        "clip",
        "gemma3",
        "llava",
        "minicpm-v",
        "mllama",
        "qwen2-vl",
        "qwen2.5-vl",
    }
    return any(value.casefold() in visual_families for value in raw_families)
