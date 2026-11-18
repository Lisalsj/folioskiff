from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace

import httpx
import pytest

from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentErrorCode,
    AgentFragment,
    AgentProbeCode,
    AgentRequest,
    AgentTaskKind,
    ReasoningEffort,
    ServiceTier,
)
from paper_reader.agents.ollama import OllamaBackend


def _request(
    *,
    model_id: str | None = "fixture-model",
    reasoning: ReasoningEffort = ReasoningEffort.HIGH,
) -> AgentRequest:
    return AgentRequest(
        task_id="ollama-job-1",
        task_kind=AgentTaskKind.REPORT,
        prompt="Analyze the untrusted paper evidence.",
        output_schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
        model_id=model_id,
        reasoning_effort=reasoning,
        service_tier=ServiceTier.FLEX,
        timeout_seconds=120,
    )


def _transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    def with_metadata(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/show"):
            return httpx.Response(200, json={"thinking": {"values": [False, True]}})
        return handler(request)

    return httpx.MockTransport(with_metadata)


@pytest.mark.asyncio
async def test_local_model_discovery_is_dynamic_deduplicated_and_unauthenticated() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "zeta:latest", "size": 10},
                    {
                        "model": "alpha:7b",
                        "size": 20,
                        "details": {"context_length": 32768},
                    },
                    {"name": "alpha:7b", "size": 20},
                ]
            },
        )

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        backend = OllamaBackend.local(client=client)
        catalog = await backend.discover_models()

    assert [item.model_id for item in catalog.models] == ["alpha:7b", "zeta:latest"]
    assert catalog.supports_manual_model_id is True
    assert catalog.supports_default_model is True
    assert catalog.models[0].context_window == 32768
    assert requests[0].url == httpx.URL("http://127.0.0.1:11434/api/tags")
    assert "Authorization" not in requests[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(503, text="offline"), AgentErrorCode.UNAVAILABLE),
        (httpx.Response(200, text="not-json"), AgentErrorCode.PROTOCOL),
        (httpx.Response(200, json={"models": "bad"}), AgentErrorCode.PROTOCOL),
    ],
)
async def test_model_discovery_classifies_http_and_protocol_failures(
    response: httpx.Response,
    expected: AgentErrorCode,
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
        backend = OllamaBackend.local(client=client)
        with pytest.raises(AgentBackendError) as captured:
            await backend.discover_models()

    assert captured.value.code is expected


@pytest.mark.asyncio
async def test_model_discovery_classifies_transport_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("fixture timeout", request=request)

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        backend = OllamaBackend.local(client=client)
        with pytest.raises(AgentBackendError) as captured:
            await backend.discover_models()

    assert captured.value.code is AgentErrorCode.TIMEOUT
    assert captured.value.retryable is True


@pytest.mark.asyncio
async def test_probe_updates_health_without_generation_and_missing_cloud_key_is_offline() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"models": []})

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        local = OllamaBackend.local(client=client)
        local_capability = await local.probe()
        cloud = OllamaBackend.cloud(client=client, api_key=None)
        cloud_capability = await cloud.probe()

    assert local_capability.installed is True
    assert local_capability.authenticated is True
    assert local_capability.available is True
    assert local_capability.probe_code is AgentProbeCode.READY
    assert local_capability.degradation_reasons == ()
    assert cloud_capability.available is False
    assert cloud_capability.authenticated is False
    assert cloud_capability.probe_code is AgentProbeCode.NOT_AUTHENTICATED
    assert cloud_capability.degradation_reasons == ("missing_credential",)
    assert calls == 1


@pytest.mark.asyncio
async def test_stopped_ollama_service_has_specific_probe_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused at a private path", request=request)

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        capability = await OllamaBackend.local(client=client).probe()

    assert capability.probe_code is AgentProbeCode.SERVICE_NOT_RUNNING
    assert capability.available is False
    assert capability.authenticated is True


@pytest.mark.asyncio
async def test_ollama_cloud_rejected_key_is_not_authenticated() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="private"))
    ) as client:
        capability = await OllamaBackend.cloud(
            client=client,
            api_key="invalid-secret",
        ).probe()

    assert capability.probe_code is AgentProbeCode.NOT_AUTHENTICATED
    assert capability.authenticated is False


@pytest.mark.asyncio
async def test_local_generation_streams_schema_output_without_authorization() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert payload["model"] == "fixture-model"
        assert payload["stream"] is True
        assert payload["format"] == _request().output_schema
        assert payload["think"] is True
        assert payload["messages"] == [
            {"role": "user", "content": "Analyze the untrusted paper evidence."}
        ]
        body = "\n".join(
            (
                json.dumps(
                    {
                        "message": {"role": "assistant", "content": '{"summary":'},
                        "done": False,
                    }
                ),
                json.dumps(
                    {
                        "message": {"role": "assistant", "content": '"fixture"}'},
                        "done": False,
                    }
                ),
                json.dumps(
                    {
                        "message": {"role": "assistant", "content": ""},
                        "done": True,
                        "prompt_eval_count": 20,
                        "eval_count": 5,
                    }
                ),
            )
        )
        return httpx.Response(200, content=f"{body}\n".encode())

    fragments: list[AgentFragment] = []
    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        backend = OllamaBackend.local(client=client)
        result = await backend.generate(
            _request(),
            on_fragment=_append(fragments),
            cancelled=lambda: False,
        )

    assert result.structured_output == {"summary": "fixture"}
    assert result.raw_output == '{"summary":"fixture"}'
    assert result.resolved_model_id == "fixture-model"
    assert result.usage.input_tokens == 20
    assert result.usage.output_tokens == 5
    assert [item.text for item in fragments] == ['{"summary":', '"fixture"}']
    assert "Authorization" not in requests[0].headers


@pytest.mark.asyncio
async def test_generation_sends_bounded_context_and_output_options() -> None:
    observed: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        observed.append(payload)
        return _stream_response('{"summary":"bounded"}')

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        backend = OllamaBackend.local(client=client)
        await backend.generate(
            replace(_request(), context_window=16_384, max_output_tokens=2_048),
            on_fragment=_append([]),
            cancelled=lambda: False,
        )

    assert observed[0]["options"] == {"num_ctx": 16_384, "num_predict": 2_048}


@pytest.mark.asyncio
async def test_thinking_only_chunks_are_streamed_as_reasoning() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = "\n".join(
            (
                json.dumps({"message": {"thinking": "先检查输出格式"}, "done": False}),
                json.dumps({"message": {"content": '{"summary":"ok"}'}, "done": False}),
                json.dumps(
                    {
                        "message": {"content": ""},
                        "done": True,
                        "prompt_eval_count": 3,
                        "eval_count": 4,
                    }
                ),
            )
        )
        return httpx.Response(200, content=f"{body}\n".encode())

    fragments: list[AgentFragment] = []
    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        backend = OllamaBackend.local(client=client)
        result = await backend.generate(
            _request(),
            on_fragment=_append(fragments),
            cancelled=lambda: False,
        )

    assert result.structured_output == {"summary": "ok"}
    assert [(item.kind, item.text) for item in fragments] == [
        ("reasoning", "先检查输出格式"),
        ("text", '{"summary":"ok"}'),
    ]


@pytest.mark.asyncio
async def test_reasoning_effort_maps_to_supported_ollama_think_values() -> None:
    observed: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(json.loads(request.content)["think"])
        return _stream_response('{"summary":"ok"}')

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        backend = OllamaBackend.local(client=client)
        for effort in ReasoningEffort:
            await backend.generate(
                _request(reasoning=effort),
                on_fragment=_append([]),
                cancelled=lambda: False,
            )

    assert observed == [False, True, True, True, True]


@pytest.mark.asyncio
async def test_cloud_uses_bearer_auth_omits_format_and_repairs_once() -> None:
    payloads: list[dict[str, object]] = []
    authorizations: list[str | None] = []
    cloud_key = "ollama-cloud-secret-123"

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        authorizations.append(request.headers.get("Authorization"))
        if len(payloads) == 1:
            return _stream_response('{"wrong":1}')
        return _stream_response(
            '{"summary":"repaired"}',
            input_tokens=7,
            output_tokens=3,
        )

    fragments: list[AgentFragment] = []
    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        backend = OllamaBackend.cloud(client=client, api_key=cloud_key)
        result = await backend.generate(
            _request(),
            on_fragment=_append(fragments),
            cancelled=lambda: False,
        )

    assert result.structured_output == {"summary": "repaired"}
    assert len(payloads) == 2
    assert all("format" not in payload for payload in payloads)
    assert authorizations == [f"Bearer {cloud_key}", f"Bearer {cloud_key}"]
    repair_messages = payloads[1]["messages"]
    assert isinstance(repair_messages, list)
    assert repair_messages[-2] == {"role": "assistant", "content": '{"wrong":1}'}
    assert repair_messages[-1]["role"] == "user"
    assert "Repair" in str(repair_messages[-1]["content"])
    assert cloud_key not in result.raw_output
    assert [item.index for item in fragments] == [0, 1]


@pytest.mark.asyncio
async def test_cloud_failed_repair_and_local_invalid_output_are_formal_failures() -> None:
    cloud_calls = 0

    def cloud_handler(_: httpx.Request) -> httpx.Response:
        nonlocal cloud_calls
        cloud_calls += 1
        return _stream_response('{"wrong":1}')

    async with httpx.AsyncClient(transport=_transport(cloud_handler)) as client:
        backend = OllamaBackend.cloud(client=client, api_key="cloud-secret")
        with pytest.raises(AgentBackendError) as cloud_failure:
            await backend.generate(
                _request(),
                on_fragment=_append([]),
                cancelled=lambda: False,
            )
    assert cloud_failure.value.code is AgentErrorCode.INVALID_OUTPUT
    assert cloud_calls == 2

    local_calls = 0

    def local_handler(_: httpx.Request) -> httpx.Response:
        nonlocal local_calls
        local_calls += 1
        return _stream_response('{"wrong":1}')

    async with httpx.AsyncClient(transport=_transport(local_handler)) as client:
        backend = OllamaBackend.local(client=client)
        with pytest.raises(AgentBackendError) as local_failure:
            await backend.generate(
                _request(),
                on_fragment=_append([]),
                cancelled=lambda: False,
            )
    assert local_failure.value.code is AgentErrorCode.INVALID_OUTPUT
    assert local_calls == 1


@pytest.mark.asyncio
async def test_generation_requires_model_and_cloud_credential() -> None:
    transport = httpx.MockTransport(lambda _: _stream_response("{}"))
    async with httpx.AsyncClient(transport=transport) as client:
        local = OllamaBackend.local(client=client)
        with pytest.raises(AgentBackendError) as missing_model:
            await local.generate(
                _request(model_id=None),
                on_fragment=_append([]),
                cancelled=lambda: False,
            )
        cloud = OllamaBackend.cloud(client=client, api_key=None)
        with pytest.raises(AgentBackendError) as missing_key:
            await cloud.generate(
                _request(),
                on_fragment=_append([]),
                cancelled=lambda: False,
            )

    assert missing_model.value.code is AgentErrorCode.PROTOCOL
    assert missing_key.value.code is AgentErrorCode.AUTHENTICATION


@pytest.mark.asyncio
async def test_generation_cancels_before_request_and_during_stream() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _stream_response('{"summary":"unused"}')

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        backend = OllamaBackend.local(client=client)
        with pytest.raises(AgentBackendError) as before:
            await backend.generate(
                _request(),
                on_fragment=_append([]),
                cancelled=lambda: True,
            )
        checks = iter((False, False, True, True))
        with pytest.raises(AgentBackendError) as during:
            await backend.generate(
                _request(),
                on_fragment=_append([]),
                cancelled=lambda: next(checks, True),
            )

    assert before.value.code is AgentErrorCode.CANCELLED
    assert during.value.code is AgentErrorCode.CANCELLED
    assert calls == 1


@pytest.mark.asyncio
async def test_generation_classifies_http_timeout_and_malformed_streams() -> None:
    responses: list[httpx.Response | Exception] = [
        httpx.Response(503, text="offline"),
        httpx.ReadTimeout("fixture timeout"),
        httpx.Response(200, content=b"not-json\n"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        if isinstance(response, Exception):
            if isinstance(response, httpx.RequestError):
                response.request = request
            raise response
        return response

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        backend = OllamaBackend.local(client=client)
        expected = (
            AgentErrorCode.UNAVAILABLE,
            AgentErrorCode.TIMEOUT,
            AgentErrorCode.PROTOCOL,
        )
        for code in expected:
            with pytest.raises(AgentBackendError) as captured:
                await backend.generate(
                    _request(),
                    on_fragment=_append([]),
                    cancelled=lambda: False,
                )
            assert captured.value.code is code


def _stream_response(
    content: str,
    *,
    input_tokens: int = 2,
    output_tokens: int = 1,
) -> httpx.Response:
    body = "\n".join(
        (
            json.dumps(
                {
                    "message": {"role": "assistant", "content": content},
                    "done": False,
                }
            ),
            json.dumps(
                {
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                    "prompt_eval_count": input_tokens,
                    "eval_count": output_tokens,
                }
            ),
        )
    )
    return httpx.Response(200, content=f"{body}\n".encode())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("controls", "expected"),
    [
        ([False, True], [False, True, True, True, True]),
        (["low", "medium", "high"], ["low", "low", "medium", "high", "high"]),
        ([False], [None, None, None, None, None]),
    ],
)
async def test_thinking_controls_are_discovered_and_cached_per_model(
    controls: list[bool | str],
    expected: list[bool | str | None],
) -> None:
    observed: list[object] = []
    shows = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal shows
        if request.url.path.endswith("/show"):
            shows += 1
            return httpx.Response(200, json={"thinking": {"values": controls}})
        observed.append(json.loads(request.content).get("think"))
        return _stream_response('{"summary":"ok"}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        backend = OllamaBackend.local(client=client)
        for effort in ReasoningEffort:
            await backend.generate(
                _request(reasoning=effort), on_fragment=_append([]), cancelled=lambda: False
            )
    assert observed == expected
    assert shows == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["local", "cloud"])
async def test_exhausted_reasoning_budget_is_output_limit_without_repeated_generation(
    location: str,
) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=(
                json.dumps(
                    {
                        "message": {"thinking": "private reasoning", "content": ""},
                        "done": True,
                        "done_reason": "length",
                        "eval_count": 2048,
                        "prompt_eval_count": 500,
                    }
                )
                + "\n"
            ).encode(),
        )

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        backend = (
            OllamaBackend.local(client=client)
            if location == "local"
            else OllamaBackend.cloud(client=client, api_key="private-key")
        )
        with pytest.raises(AgentBackendError) as failure:
            await backend.generate(_request(), on_fragment=_append([]), cancelled=lambda: False)
    assert failure.value.code is AgentErrorCode.OUTPUT_LIMIT
    assert failure.value.usage is not None
    assert failure.value.usage.output_tokens == 2048
    assert failure.value.diagnostic == "done_reason=length"
    assert "private" not in str(failure.value)
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "provider_error", "expected"),
    [
        (
            200,
            "CUDA error: out of memory at /private sk-fixture-secret",
            AgentErrorCode.UNAVAILABLE,
        ),
        (404, "model 'private-model' not found", AgentErrorCode.PROTOCOL),
        (400, "model does not support thinking", AgentErrorCode.PROTOCOL),
        (400, "context length exceeded private evidence", AgentErrorCode.OUTPUT_LIMIT),
    ],
)
async def test_http_and_stream_errors_have_safe_classifications(
    status: int,
    provider_error: str,
    expected: AgentErrorCode,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        if status == 200:
            return httpx.Response(
                200, content=(json.dumps({"error": provider_error}) + "\n").encode()
            )
        return httpx.Response(status, json={"error": provider_error})

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        with pytest.raises(AgentBackendError) as failure:
            await OllamaBackend.local(client=client).generate(
                _request(),
                on_fragment=_append([]),
                cancelled=lambda: False,
            )
    assert failure.value.code is expected
    assert "private" not in str(failure.value)
    assert "sk-fixture" not in str(failure.value)
    assert provider_error not in (failure.value.diagnostic or "")


@pytest.mark.asyncio
async def test_unselected_model_uses_first_discovered_model_and_bounded_local_defaults() -> None:
    observed: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tags"):
            return httpx.Response(200, json={"models": [{"name": "zeta"}, {"name": "alpha"}]})
        observed.append(json.loads(request.content))
        return _stream_response('{"summary":"ok"}')

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        result = await OllamaBackend.local(client=client).generate(
            _request(model_id=None),
            on_fragment=_append([]),
            cancelled=lambda: False,
        )
    assert result.resolved_model_id == "alpha"
    assert observed[0]["model"] == "alpha"
    assert observed[0]["options"] == {"num_ctx": 32_768, "num_predict": 16_384}


@pytest.mark.asyncio
async def test_cloud_omits_local_context_reservation_options() -> None:
    observed: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(json.loads(request.content))
        return _stream_response('{"summary":"ok"}')

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        await OllamaBackend.cloud(client=client, api_key="fixture-key").generate(
            replace(_request(), context_window=16_384, max_output_tokens=8192),
            on_fragment=_append([]),
            cancelled=lambda: False,
        )
    assert observed[0]["options"] == {"num_predict": 8192}


@pytest.mark.asyncio
async def test_continuous_stream_respects_total_generation_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_timeout = asyncio.timeout
    monkeypatch.setattr("paper_reader.agents.ollama.asyncio.timeout", lambda _: real_timeout(0.01))

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b'{"message":{"thinking":"fixture"},"done":false}\n'
                await asyncio.sleep(0.001)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=SlowStream())

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        with pytest.raises(AgentBackendError) as failure:
            await OllamaBackend.local(client=client).generate(
                _request(),
                on_fragment=_append([]),
                cancelled=lambda: False,
            )
    assert failure.value.code is AgentErrorCode.TIMEOUT


def _append(target: list[AgentFragment]) -> Callable[[AgentFragment], Awaitable[None]]:
    async def append(value: AgentFragment) -> None:
        target.append(value)

    return append
