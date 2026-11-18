from __future__ import annotations

import base64
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from paper_reader.plugins.broker import (
    DEFAULT_BROKER_REQUEST_BYTES,
    DEFAULT_BROKER_RESPONSE_BYTES,
    DEFAULT_FILE_READ_BYTES,
    BrokerCall,
    BrokerError,
    BrokerHttpResponse,
    BrokerReply,
    CredentialBroker,
    FileBroker,
    NetworkBroker,
    NetworkFetchRequest,
    OutboundHttpRequest,
    PermissionBroker,
    SecretRedactor,
)
from paper_reader.plugins.contracts import PermissionRequest
from paper_reader.plugins.sandbox import DEFAULT_PLUGIN_LINE_BYTES
from paper_reader.plugins.sdk import JsonlCodec, PluginEvent


class RecordingResolver:
    def __init__(self, answers: dict[str, tuple[str, ...]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        answer = self.answers[host]
        if isinstance(answer, BaseException):  # pragma: no cover - typing guard for fixtures
            raise answer
        return answer


class RecordingTransport:
    def __init__(self, responses: list[BrokerHttpResponse]) -> None:
        self.responses = responses
        self.requests: list[OutboundHttpRequest] = []

    async def send(self, request: OutboundHttpRequest) -> BrokerHttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def response(
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    chunks: tuple[bytes, ...] = (b'{"ok":true}',),
    peer: str = "93.184.216.34",
) -> BrokerHttpResponse:
    return BrokerHttpResponse(
        status_code=status,
        headers=headers or {"content-type": "application/json"},
        body_chunks=chunks,
        peer_address=peer,
    )


@pytest.mark.asyncio
async def test_network_broker_revalidates_every_https_redirect_and_strips_unsafe_headers() -> None:
    resolver = RecordingResolver(
        {
            "api.example.org": ("93.184.216.34",),
            "mirror.example.org": ("93.184.216.35",),
        }
    )
    transport = RecordingTransport(
        [
            response(
                status=302,
                headers={"location": "https://mirror.example.org/v2"},
            ),
            response(
                headers={
                    "content-type": "application/json",
                    "etag": '"abc"',
                    "set-cookie": "session=secret",
                    "authorization": "Bearer secret",
                    "x-internal-path": r"C:\\private",
                },
                chunks=(b'{"items":', b"[]}"),
                peer="93.184.216.35",
            ),
        ]
    )
    broker = NetworkBroker(resolver=resolver, transport=transport)

    result = await broker.fetch(
        approved_domains=frozenset({"api.example.org", "mirror.example.org"}),
        request=NetworkFetchRequest(
            url="https://api.example.org/v1?q=paper",
            method="GET",
            headers={"accept": "application/json"},
        ),
    )

    assert resolver.calls == [
        ("api.example.org", 443),
        ("mirror.example.org", 443),
    ]
    assert len(transport.requests) == 2
    for outbound in transport.requests:
        assert outbound.follow_redirects is False
        assert outbound.use_environment_proxy is False
        assert outbound.accept_encoding == "identity"
        assert outbound.url.startswith("https://")
    assert result.status_code == 200
    assert result.headers == {"content-type": "application/json", "etag": '"abc"'}
    assert base64.b64decode(result.body_base64) == b'{"items":[]}'
    assert result.byte_size == 12
    assert "secret" not in result.model_dump_json()
    assert r"C:\\private" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_largest_network_broker_body_always_fits_one_sandbox_jsonl_frame() -> None:
    body = b"x" * DEFAULT_BROKER_RESPONSE_BYTES
    broker = NetworkBroker(
        resolver=RecordingResolver({"api.example.org": ("93.184.216.34",)}),
        transport=RecordingTransport(
            [
                response(
                    headers={"content-length": str(len(body))},
                    chunks=(body,),
                )
            ]
        ),
    )
    result = await broker.fetch(
        approved_domains=frozenset({"api.example.org"}),
        request=NetworkFetchRequest(url="https://api.example.org/papers"),
    )
    encoded = JsonlCodec().encode(
        BrokerReply(
            protocol_version="1.0",
            request_id=str(uuid4()),
            broker_request_id=str(uuid4()),
            ok=True,
            result=result.model_dump(mode="json"),
        )
    )

    assert len(encoded) <= DEFAULT_PLUGIN_LINE_BYTES


def test_largest_network_request_body_always_fits_one_plugin_jsonl_frame() -> None:
    request = NetworkFetchRequest(
        url="https://api.example.org/papers",
        method="POST",
        body_base64=base64.b64encode(b"x" * DEFAULT_BROKER_REQUEST_BYTES).decode("ascii"),
    )
    call = BrokerCall(
        broker_request_id=str(uuid4()),
        operation="network.fetch",
        resource="api.example.org",
        payload=request.model_dump(mode="json", exclude_none=True),
    )
    encoded = JsonlCodec().encode(
        PluginEvent(
            protocol_version="1.0",
            request_id=str(uuid4()),
            sequence=1,
            event="broker.request",
            payload=call.model_dump(mode="json"),
        )
    )

    assert len(encoded) <= DEFAULT_PLUGIN_LINE_BYTES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "answers", "expected_code"),
    [
        ("http://api.example.org/v1", {"api.example.org": ("93.184.216.34",)}, "unsafe_url"),
        (
            "https://user:pass@api.example.org/v1",
            {"api.example.org": ("93.184.216.34",)},
            "unsafe_url",
        ),
        ("https://127.0.0.1/v1", {}, "unsafe_url"),
        ("https://localhost/v1", {}, "unsafe_url"),
        ("https://service.local/v1", {}, "unsafe_url"),
        (
            "https://other.example.org/v1",
            {"other.example.org": ("93.184.216.34",)},
            "permission_denied",
        ),
        ("https://api.example.org/v1", {"api.example.org": ("127.0.0.1",)}, "unsafe_url"),
        ("https://api.example.org/v1", {"api.example.org": ("10.0.0.1",)}, "unsafe_url"),
    ],
)
async def test_network_broker_rejects_credentials_non_https_private_and_unapproved_hosts(
    url: str,
    answers: dict[str, tuple[str, ...]],
    expected_code: str,
) -> None:
    resolver = RecordingResolver(answers)
    transport = RecordingTransport([response()])
    broker = NetworkBroker(resolver=resolver, transport=transport)

    with pytest.raises(BrokerError) as captured:
        await broker.fetch(
            approved_domains=frozenset({"api.example.org"}),
            request=NetworkFetchRequest(url=url, method="GET", headers={}),
        )

    assert captured.value.code == expected_code
    assert transport.requests == []


@pytest.mark.asyncio
async def test_network_broker_rejects_dns_rebinding_peer_mismatch_and_unlimited_output() -> None:
    resolver = RecordingResolver({"api.example.org": ("93.184.216.34",)})

    mismatch = NetworkBroker(
        resolver=resolver,
        transport=RecordingTransport([response(peer="93.184.216.35")]),
    )
    with pytest.raises(BrokerError) as peer_error:
        await mismatch.fetch(
            approved_domains=frozenset({"api.example.org"}),
            request=NetworkFetchRequest(
                url="https://api.example.org/v1",
                method="GET",
                headers={},
            ),
        )
    assert peer_error.value.code == "unsafe_peer"

    private_peer = NetworkBroker(
        resolver=resolver,
        transport=RecordingTransport([response(peer="127.0.0.1")]),
    )
    with pytest.raises(BrokerError) as private_error:
        await private_peer.fetch(
            approved_domains=frozenset({"api.example.org"}),
            request=NetworkFetchRequest(
                url="https://api.example.org/v1",
                method="GET",
                headers={},
            ),
        )
    assert private_error.value.code == "unsafe_peer"

    oversized = NetworkBroker(
        resolver=resolver,
        transport=RecordingTransport(
            [
                response(
                    headers={"content-length": "9"},
                    chunks=(b"12345", b"6789"),
                )
            ]
        ),
        max_response_bytes=8,
    )
    with pytest.raises(BrokerError) as output_error:
        await oversized.fetch(
            approved_domains=frozenset({"api.example.org"}),
            request=NetworkFetchRequest(
                url="https://api.example.org/v1",
                method="GET",
                headers={},
            ),
        )
    assert output_error.value.code == "response_too_large"


@pytest.mark.asyncio
async def test_network_broker_rejects_unsafe_request_headers_and_redirect_policy_escape() -> None:
    resolver = RecordingResolver(
        {
            "api.example.org": ("93.184.216.34",),
            "private.example.org": ("10.0.0.1",),
        }
    )
    transport = RecordingTransport(
        [response(status=302, headers={"location": "https://private.example.org/secret"})]
    )
    broker = NetworkBroker(resolver=resolver, transport=transport)

    with pytest.raises(ValidationError, match="header"):
        NetworkFetchRequest(
            url="https://api.example.org/v1",
            method="GET",
            headers={"Authorization": "Bearer secret"},
        )
    with pytest.raises(ValidationError, match="header"):
        NetworkFetchRequest(
            url="https://api.example.org/v1",
            method="GET",
            headers={"Proxy-Authorization": "secret"},
        )

    with pytest.raises(BrokerError) as redirected:
        await broker.fetch(
            approved_domains=frozenset({"api.example.org", "private.example.org"}),
            request=NetworkFetchRequest(
                url="https://api.example.org/v1",
                method="GET",
                headers={},
            ),
        )
    assert redirected.value.code == "unsafe_url"
    assert len(transport.requests) == 1


def test_file_broker_uses_only_abstract_resources_and_never_returns_a_path() -> None:
    calls: list[str] = []

    def provide(resource: str) -> bytes:
        calls.append(resource)
        return b"0123456789"

    broker = FileBroker(
        resources={"published_snapshot": provide},
        max_read_bytes=6,
    )
    result = broker.read("published_snapshot", offset=2, limit=4)

    assert base64.b64decode(result.data_base64) == b"2345"
    assert result.byte_size == 4
    assert result.eof is False
    assert "path" not in result.model_dump()
    assert calls == ["published_snapshot"]

    with pytest.raises(BrokerError) as missing:
        broker.read("database", offset=0, limit=1)
    assert missing.value.code == "resource_unavailable"
    with pytest.raises(BrokerError) as unlimited:
        broker.read("published_snapshot", offset=0, limit=7)
    assert unlimited.value.code == "read_limit"


def test_largest_file_broker_chunk_always_fits_one_sandbox_jsonl_frame() -> None:
    content = b"x" * DEFAULT_FILE_READ_BYTES
    result = FileBroker(resources={"paper_text": lambda _resource: content}).read(
        "paper_text",
        offset=0,
        limit=DEFAULT_FILE_READ_BYTES,
    )
    encoded = JsonlCodec().encode(
        BrokerReply(
            protocol_version="1.0",
            request_id=str(uuid4()),
            broker_request_id=str(uuid4()),
            ok=True,
            result=result.model_dump(mode="json"),
        )
    )

    assert len(encoded) <= DEFAULT_PLUGIN_LINE_BYTES


def test_credential_broker_fetches_only_named_secret_and_redactor_removes_exact_value() -> None:
    provider_calls: list[str] = []
    redactor = SecretRedactor()
    secret = "sk-super-secret-value\r\nFORGED-EVENT\u2028"

    def provider(resource: str) -> str | None:
        provider_calls.append(resource)
        return secret if resource == "fixture_api_key" else None

    broker = CredentialBroker(provider=provider, redactor=redactor)
    value = broker.read("fixture_api_key")

    assert value == secret
    assert provider_calls == ["fixture_api_key"]
    assert redactor.redact_text(f"token={secret}") == "token=<redacted>"
    assert redactor.redact_text("token=sk-super-secret-valu") == "token=sk-super-secret-valu"
    assert redactor.redact_value(
        {
            "nested": [secret, {"error": f"bad {secret}"}],
            "safe": 7,
        }
    ) == {
        "nested": ["<redacted>", {"error": "bad <redacted>"}],
        "safe": 7,
    }

    with pytest.raises(BrokerError) as missing:
        broker.read("missing_key")
    assert missing.value.code == "credential_unavailable"


def permission(kind: str, resource: str) -> PermissionRequest:
    return PermissionRequest(
        kind=kind,
        resource=resource,
        purpose=f"Use {resource} for this fixture call",
    )


def broker_call(operation: str, resource: str, payload: dict[str, Any]) -> BrokerCall:
    return BrokerCall(
        broker_request_id=str(uuid4()),
        operation=operation,
        resource=resource,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_permission_broker_requires_current_declaration_and_grant_before_side_effect() -> (
    None
):
    resolver = RecordingResolver({"api.example.org": ("93.184.216.34",)})
    transport = RecordingTransport([response()])
    credential_calls: list[str] = []
    file_calls: list[str] = []

    def credential_provider(resource: str) -> str | None:
        credential_calls.append(resource)
        return "credential-secret"

    def file_provider(resource: str) -> bytes:
        file_calls.append(resource)
        return b"safe snapshot"

    declared = (
        permission("network", "api.example.org"),
        permission("file", "published_snapshot"),
        permission("credential", "fixture_api_key"),
    )
    broker = PermissionBroker(
        protocol_version="1.0",
        request_id=str(uuid4()),
        declared=declared,
        granted=(declared[0],),
        network=NetworkBroker(resolver=resolver, transport=transport),
        files=FileBroker(resources={"published_snapshot": file_provider}),
        credentials=CredentialBroker(provider=credential_provider),
    )

    network_reply = await broker.handle(
        broker_call(
            "network.fetch",
            "api.example.org",
            {"url": "https://api.example.org/v1", "method": "GET", "headers": {}},
        )
    )
    assert network_reply.ok is True
    assert len(transport.requests) == 1

    denied_file = await broker.handle(
        broker_call(
            "file.read",
            "published_snapshot",
            {"offset": 0, "limit": 32},
        )
    )
    assert denied_file.ok is False
    assert denied_file.error is not None
    assert denied_file.error.code == "permission_denied"
    assert file_calls == []

    denied_credential = await broker.handle(broker_call("credential.read", "fixture_api_key", {}))
    assert denied_credential.ok is False
    assert credential_calls == []

    undeclared = await broker.handle(
        broker_call(
            "network.fetch",
            "other.example.org",
            {"url": "https://other.example.org/v1", "method": "GET", "headers": {}},
        )
    )
    assert undeclared.ok is False
    assert undeclared.error is not None
    assert undeclared.error.code == "permission_denied"
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_permission_broker_routes_file_and_credential_without_leaking_host_paths() -> None:
    request_id = str(uuid4())
    file_permission = permission("file", "published_snapshot")
    credential_permission = permission("credential", "fixture_api_key")
    redactor = SecretRedactor()
    broker = PermissionBroker(
        protocol_version="1.0",
        request_id=request_id,
        declared=(file_permission, credential_permission),
        granted=(file_permission, credential_permission),
        network=NetworkBroker(
            resolver=RecordingResolver({}),
            transport=RecordingTransport([]),
        ),
        files=FileBroker(resources={"published_snapshot": lambda _resource: b"snapshot"}),
        credentials=CredentialBroker(
            provider=lambda resource: (
                "super-secret-value" if resource == "fixture_api_key" else None
            ),
            redactor=redactor,
        ),
        redactor=redactor,
    )

    file_reply = await broker.handle(
        broker_call("file.read", "published_snapshot", {"offset": 0, "limit": 64})
    )
    credential_reply = await broker.handle(broker_call("credential.read", "fixture_api_key", {}))

    assert file_reply.ok is True
    assert file_reply.result is not None
    assert "path" not in file_reply.result
    assert credential_reply.ok is True
    assert credential_reply.result == {"value": "super-secret-value"}
    assert redactor.redact_value(credential_reply.result) == {"value": "<redacted>"}
    assert file_reply.protocol_version == "1.0"
    assert file_reply.request_id == request_id


@pytest.mark.asyncio
async def test_broker_request_id_is_consumed_once_without_replaying_side_effect() -> None:
    credential_calls: list[str] = []

    def provide(resource: str) -> str:
        credential_calls.append(resource)
        return "one-secret"

    credential_permission = permission("credential", "fixture_api_key")
    broker = PermissionBroker(
        protocol_version="1.0",
        request_id=str(uuid4()),
        declared=(credential_permission,),
        granted=(credential_permission,),
        network=NetworkBroker(
            resolver=RecordingResolver({}),
            transport=RecordingTransport([]),
        ),
        files=FileBroker(resources={}),
        credentials=CredentialBroker(provider=provide),
    )
    call = broker_call("credential.read", "fixture_api_key", {})

    first = await broker.handle(call)
    replay = await broker.handle(call)

    assert first.ok is True
    assert replay.ok is False
    assert replay.error is not None
    assert replay.error.code == "duplicate_broker_request"
    assert credential_calls == ["fixture_api_key"]


@pytest.mark.asyncio
async def test_broker_call_budget_caps_unique_side_effects_per_plugin_invocation() -> None:
    credential_calls: list[str] = []

    def provide(resource: str) -> str:
        credential_calls.append(resource)
        return "one-secret"

    credential_permission = permission("credential", "fixture_api_key")
    broker = PermissionBroker(
        protocol_version="1.0",
        request_id=str(uuid4()),
        declared=(credential_permission,),
        granted=(credential_permission,),
        network=NetworkBroker(
            resolver=RecordingResolver({}),
            transport=RecordingTransport([]),
        ),
        files=FileBroker(resources={}),
        credentials=CredentialBroker(provider=provide),
        max_calls=1,
    )

    first = await broker.handle(broker_call("credential.read", "fixture_api_key", {}))
    over_budget = await broker.handle(broker_call("credential.read", "fixture_api_key", {}))

    assert first.ok is True
    assert over_budget.ok is False
    assert over_budget.error is not None
    assert over_budget.error.code == "broker_call_limit"
    assert credential_calls == ["fixture_api_key"]


def test_broker_envelope_rejects_paths_process_dll_and_unknown_fields() -> None:
    base = {
        "broker_request_id": str(uuid4()),
        "operation": "file.read",
        "resource": "published_snapshot",
        "payload": {"offset": 0, "limit": 10},
    }
    with pytest.raises(ValidationError, match="operation"):
        BrokerCall.model_validate({**base, "operation": "process.spawn"})
    with pytest.raises(ValidationError, match="operation"):
        BrokerCall.model_validate({**base, "operation": "dll.load"})
    with pytest.raises(ValidationError, match="extra"):
        BrokerCall.model_validate({**base, "host_path": r"C:\\Users\\private"})
    with pytest.raises(ValidationError, match="extra"):
        BrokerCall.model_validate(
            {**base, "payload": {"offset": 0, "limit": 10, "path": r"C:\\db.sqlite3"}}
        )


@pytest.mark.asyncio
async def test_broker_event_must_be_correlated_and_well_formed() -> None:
    request_id = str(uuid4())
    file_permission = permission("file", "published_snapshot")
    broker = PermissionBroker(
        protocol_version="1.0",
        request_id=request_id,
        declared=(file_permission,),
        granted=(file_permission,),
        network=NetworkBroker(
            resolver=RecordingResolver({}),
            transport=RecordingTransport([]),
        ),
        files=FileBroker(resources={"published_snapshot": lambda _resource: b"snapshot"}),
        credentials=CredentialBroker(provider=lambda _resource: None),
    )
    call = broker_call(
        "file.read",
        "published_snapshot",
        {"offset": 0, "limit": 64},
    )
    event = PluginEvent(
        protocol_version="1.0",
        request_id=request_id,
        sequence=1,
        event="broker.request",
        payload=call.model_dump(mode="json"),
    )

    reply = await broker.handle_event(event)
    assert reply.ok is True
    assert reply.broker_request_id == call.broker_request_id

    wrong_request = event.model_copy(update={"request_id": str(uuid4())})
    with pytest.raises(BrokerError) as correlation:
        await broker.handle_event(wrong_request)
    assert correlation.value.code == "request_mismatch"

    not_broker = event.model_copy(update={"event": "progress"})
    with pytest.raises(BrokerError) as event_type:
        await broker.handle_event(not_broker)
    assert event_type.value.code == "invalid_broker_event"
