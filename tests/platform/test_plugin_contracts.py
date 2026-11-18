from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from paper_reader.plugins.contracts import (
    CapabilityKind,
    PermissionKind,
    PluginManifest,
    is_protocol_compatible,
)
from paper_reader.plugins.sdk import (
    JsonlCodec,
    PluginError,
    PluginEvent,
    PluginRequest,
    PluginResponse,
    ProtocolError,
    ProtocolSession,
)


def manifest_payload() -> dict[str, object]:
    return {
        "manifest_version": "1",
        "plugin_id": "fixture.research-tools",
        "display_name": "Fixture Research Tools",
        "distribution_name": "paper-reader-fixture",
        "distribution_version": "1.2.3",
        "protocol": {"minimum": "1.0", "maximum": "1.2"},
        "license": "Apache-2.0",
        "config_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["zh", "en"]},
                "contact": {"type": "string", "format": "email"},
            },
            "additionalProperties": False,
        },
        "capabilities": [
            {
                "capability_id": "fixture.source",
                "kind": "source_connector",
                "methods": ["fetch_page", "estimate", "probe"],
                "permissions": [
                    {
                        "kind": "network",
                        "resource": "api.example.org",
                        "purpose": "Read public fixture metadata",
                    },
                    {
                        "kind": "credential",
                        "resource": "fixture_api_key",
                        "purpose": "Authenticate requests through the host broker",
                    },
                ],
                "timeout_seconds": 30,
            },
            {
                "capability_id": "fixture.metadata",
                "kind": "metadata_enricher",
                "methods": ["probe", "enrich"],
                "permissions": [],
                "timeout_seconds": 20,
            },
            {
                "capability_id": "fixture.score",
                "kind": "score_dimension",
                "methods": ["probe", "evaluate"],
                "permissions": [],
                "timeout_seconds": 20,
            },
            {
                "capability_id": "fixture.agent",
                "kind": "agent_backend",
                "methods": ["probe", "models", "generate"],
                "permissions": [],
                "timeout_seconds": 120,
            },
            {
                "capability_id": "fixture.publisher",
                "kind": "publisher",
                "methods": ["probe", "publish"],
                "permissions": [
                    {
                        "kind": "file",
                        "resource": "published_snapshot",
                        "purpose": "Read the host-provided public snapshot",
                    }
                ],
                "timeout_seconds": 60,
            },
        ],
    }


def request(*, deadline: datetime | None = None) -> PluginRequest:
    return PluginRequest(
        protocol_version="1.0",
        request_id=str(uuid4()),
        capability="fixture.source",
        method="fetch_page",
        payload={"query": "扩散模型", "cursor": None},
        deadline=deadline or datetime.now(UTC) + timedelta(minutes=1),
    )


def test_manifest_normalizes_all_five_capabilities_and_hashes_canonical_json() -> None:
    first = PluginManifest.model_validate(manifest_payload())
    reordered = manifest_payload()
    reordered["capabilities"] = list(reversed(reordered["capabilities"]))  # type: ignore[arg-type]
    second = PluginManifest.model_validate(reordered)

    assert {item.kind for item in first.capabilities} == set(CapabilityKind)
    assert first.capabilities[0].capability_id == "fixture.agent"
    source = next(item for item in first.capabilities if item.kind is CapabilityKind.SOURCE)
    assert source.methods == ("estimate", "fetch_page", "probe")
    assert source.permissions[0].kind is PermissionKind.CREDENTIAL
    assert source.permissions[1].resource == "api.example.org"
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.canonical_sha256() == second.canonical_sha256()
    assert len(first.canonical_sha256()) == 64


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update({"surprise": True}), "extra"),
        (
            lambda value: value["capabilities"][0].update({"methods": ["run_shell"]}),
            "method",
        ),
        (
            lambda value: value["config_schema"].update(
                {"properties": {"remote": {"$ref": "https://evil.invalid/schema.json"}}}
            ),
            "remote",
        ),
        (
            lambda value: value["config_schema"].update({"execute": "calc.exe"}),
            "keyword",
        ),
        (
            lambda value: value["config_schema"].update(
                {"properties": {"value": {"type": "string", "format": "python-code"}}}
            ),
            "format",
        ),
    ],
)
def test_manifest_rejects_unknown_fields_methods_and_unsafe_schema(
    mutation: object,
    message: str,
) -> None:
    payload = manifest_payload()
    assert callable(mutation)
    mutation(payload)  # type: ignore[operator]
    with pytest.raises((ValidationError, ValueError), match=message):
        PluginManifest.model_validate(payload)


def test_manifest_rejects_remote_refs_oversize_schema_and_duplicate_capability_ids() -> None:
    oversized = manifest_payload()
    oversized["config_schema"] = {
        "type": "object",
        "description": "x" * 70_000,
        "additionalProperties": False,
    }
    with pytest.raises(ValidationError, match="64 KiB"):
        PluginManifest.model_validate(oversized)

    duplicate = manifest_payload()
    capabilities = duplicate["capabilities"]
    assert isinstance(capabilities, list)
    capabilities.append(dict(capabilities[0]))
    with pytest.raises(ValidationError, match="Duplicate capability"):
        PluginManifest.model_validate(duplicate)


@pytest.mark.parametrize(
    ("kind", "resource"),
    [
        ("network", "https://api.example.org/path"),
        ("network", "*.example.org"),
        ("network", "127.0.0.1"),
        ("file", "C:\\Users\\reader\\paper.pdf"),
        ("file", "../paper.pdf"),
        ("credential", "secret/value"),
    ],
)
def test_manifest_permissions_are_abstract_broker_resources(kind: str, resource: str) -> None:
    payload = manifest_payload()
    capabilities = payload["capabilities"]
    assert isinstance(capabilities, list)
    capabilities[0]["permissions"] = [  # type: ignore[index]
        {"kind": kind, "resource": resource, "purpose": "Fixture permission"}
    ]
    with pytest.raises(ValidationError, match="resource"):
        PluginManifest.model_validate(payload)


def test_protocol_compatibility_requires_overlapping_major_one_range() -> None:
    manifest = PluginManifest.model_validate(manifest_payload())

    assert is_protocol_compatible(manifest.protocol, host_version="1.0") is True
    assert is_protocol_compatible(manifest.protocol, host_version="1.2") is True
    assert is_protocol_compatible(manifest.protocol, host_version="1.3") is False
    assert is_protocol_compatible(manifest.protocol, host_version="2.0") is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("manifest_version", "2", "manifest version"),
        ("plugin_id", "invalid/id", "stable lowercase"),
        ("display_name", "bad\nname", "controls"),
        ("distribution_version", "1.0 unsafe", "version"),
    ],
)
def test_manifest_rejects_unstable_identity_fields(field: str, value: str, message: str) -> None:
    payload = manifest_payload()
    payload[field] = value
    with pytest.raises(ValidationError, match=message):
        PluginManifest.model_validate(payload)


def test_manifest_rejects_invalid_protocol_ranges_and_duplicate_declarations() -> None:
    reversed_range = manifest_payload()
    reversed_range["protocol"] = {"minimum": "1.2", "maximum": "1.0"}
    with pytest.raises(ValidationError, match="minimum"):
        PluginManifest.model_validate(reversed_range)

    invalid_version = manifest_payload()
    invalid_version["protocol"] = {"minimum": "v1", "maximum": "1.0"}
    with pytest.raises(ValidationError, match="major.minor"):
        PluginManifest.model_validate(invalid_version)

    duplicate_method = manifest_payload()
    capabilities = duplicate_method["capabilities"]
    assert isinstance(capabilities, list)
    capabilities[0]["methods"] = ["probe", "probe"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="duplicates"):
        PluginManifest.model_validate(duplicate_method)

    duplicate_permission = manifest_payload()
    capabilities = duplicate_permission["capabilities"]
    assert isinstance(capabilities, list)
    permissions = capabilities[0]["permissions"]  # type: ignore[index]
    assert isinstance(permissions, list)
    permissions.append(dict(permissions[0]))
    with pytest.raises(ValidationError, match="duplicate resources"):
        PluginManifest.model_validate(duplicate_permission)


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        ({"type": "array", "additionalProperties": False}, "root type"),
        ({"type": "object", "additionalProperties": True}, "unknown configuration"),
        (
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "additionalProperties": False,
            },
            "draft 2020-12",
        ),
        (
            {"type": "object", "properties": [], "additionalProperties": False},
            "properties",
        ),
        (
            {"type": "object", "allOf": {}, "additionalProperties": False},
            "allOf",
        ),
        (
            {"type": "object", "properties": {"x": 12}, "additionalProperties": False},
            "object or boolean",
        ),
    ],
)
def test_config_schema_requires_a_bounded_closed_draft_2020_shape(
    schema: dict[str, object],
    message: str,
) -> None:
    payload = manifest_payload()
    payload["config_schema"] = schema
    with pytest.raises(ValidationError, match=message):
        PluginManifest.model_validate(payload)


def test_manifest_rejects_control_purpose_and_non_json_schema_values() -> None:
    control = manifest_payload()
    capabilities = control["capabilities"]
    assert isinstance(capabilities, list)
    capabilities[0]["permissions"][0]["purpose"] = "bad\npurpose"  # type: ignore[index]
    with pytest.raises(ValidationError, match="control"):
        PluginManifest.model_validate(control)

    blank_purpose = manifest_payload()
    capabilities = blank_purpose["capabilities"]
    assert isinstance(capabilities, list)
    capabilities[0]["permissions"][0]["purpose"] = "   "  # type: ignore[index]
    with pytest.raises(ValidationError, match="blank"):
        PluginManifest.model_validate(blank_purpose)

    non_json = manifest_payload()
    schema = non_json["config_schema"]
    assert isinstance(schema, dict)
    schema["description"] = object()
    with pytest.raises(ValidationError, match="JSON-compatible"):
        PluginManifest.model_validate(non_json)


def test_jsonl_request_round_trip_preserves_unicode_and_checks_deadline() -> None:
    codec = JsonlCodec(max_line_bytes=4096)
    original = request()
    encoded = codec.encode(original)
    decoded = codec.decode_request(encoded)

    assert encoded.endswith(b"\n")
    assert decoded == original
    assert decoded.payload["query"] == "扩散模型"
    decoded.ensure_not_expired(now=datetime.now(UTC))
    with pytest.raises(ProtocolError, match="deadline"):
        request(deadline=datetime.now(UTC) - timedelta(seconds=1)).ensure_not_expired(
            now=datetime.now(UTC)
        )


def test_protocol_models_reject_noncanonical_identity_and_control_diagnostics() -> None:
    base = request().model_dump()
    for field, value in (
        ("protocol_version", "v1"),
        ("request_id", "not-a-uuid"),
        ("request_id", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
        ("capability", "unsafe/capability"),
        ("deadline", datetime(2026, 7, 14)),
    ):
        candidate = {**base, field: value}
        with pytest.raises(ValidationError):
            PluginRequest.model_validate(candidate)

    with pytest.raises(ValueError, match="timezone"):
        request().ensure_not_expired(now=datetime(2026, 7, 14))
    with pytest.raises(ValidationError, match="error code"):
        PluginError(code="BAD-CODE", message="safe")
    with pytest.raises(ValidationError, match="control"):
        PluginError(code="bad_output", message="unsafe\nmessage")
    with pytest.raises(ValidationError, match="blank"):
        PluginError(code="bad_output", message="   ")
    with pytest.raises(ValidationError, match="event"):
        PluginEvent(
            protocol_version="1.0",
            request_id=str(uuid4()),
            sequence=1,
            event="unsafe/event",
            payload={},
        )


@pytest.mark.parametrize(
    "line",
    [
        b'\xff\n',
        b'{"request_id":"bad\x00value"}\n',
        b'{"request_id":"one"}\n{"request_id":"two"}\n',
        b'[]\n',
        b'{"request_id":"missing-newline"}',
    ],
)
def test_jsonl_codec_rejects_invalid_utf8_controls_and_non_object_lines(line: bytes) -> None:
    with pytest.raises(ProtocolError):
        JsonlCodec(max_line_bytes=1024).decode_object(line)


def test_jsonl_codec_applies_line_limit_before_json_parsing() -> None:
    codec = JsonlCodec(max_line_bytes=128)
    oversized = json.dumps({"payload": "x" * 200}).encode() + b"\n"
    with pytest.raises(ProtocolError, match="line limit"):
        codec.decode_object(oversized)


def test_jsonl_codec_rejects_bom_bad_json_unknown_frames_and_non_json_values() -> None:
    codec = JsonlCodec(max_line_bytes=1024)
    with pytest.raises(ProtocolError, match="BOM"):
        codec.decode_object(b'\xef\xbb\xbf{"type":"event"}\n')
    with pytest.raises(ProtocolError, match="valid JSON"):
        codec.decode_object(b'{broken}\n')
    with pytest.raises(ProtocolError, match="unknown type"):
        codec.decode_frame(b'{"type":"mystery"}\n')
    with pytest.raises(ProtocolError, match="envelope"):
        codec.decode_frame(b'{"type":"event"}\n')
    with pytest.raises(ProtocolError, match="valid JSON"):
        codec.encode({"not_json": object()})
    with pytest.raises(ProtocolError, match="line limit"):
        JsonlCodec(max_line_bytes=64).encode({"payload": "x" * 100})


def test_protocol_limits_must_be_sensible_and_session_tracks_accepted_bytes() -> None:
    with pytest.raises(ValueError, match="line limit"):
        JsonlCodec(max_line_bytes=63)
    with pytest.raises(ValueError, match="positive"):
        ProtocolSession(request(), max_total_bytes=0)

    original = request()
    codec = JsonlCodec(max_line_bytes=1024)
    event = PluginEvent(
        protocol_version="1.0",
        request_id=original.request_id,
        sequence=1,
        event="page",
        payload={},
    )
    line = codec.encode(event)
    session = ProtocolSession(original, codec=codec)
    session.accept_line(line)
    assert session.total_bytes == len(line)


def test_protocol_session_enforces_correlation_sequence_and_single_terminal() -> None:
    original = request()
    codec = JsonlCodec(max_line_bytes=4096)
    session = ProtocolSession(original, codec=codec, max_total_bytes=8192)
    event = PluginEvent(
        protocol_version="1.0",
        request_id=original.request_id,
        sequence=1,
        event="page",
        payload={"records": 2},
    )
    terminal = PluginResponse(
        protocol_version="1.0",
        request_id=original.request_id,
        sequence=2,
        ok=True,
        result={"next_cursor": None},
    )

    assert session.accept_line(codec.encode(event)) == event
    assert session.accept_line(codec.encode(terminal)) == terminal
    assert session.finished is True
    with pytest.raises(ProtocolError, match="terminal"):
        session.accept_line(codec.encode(terminal))

    wrong_request = terminal.model_copy(update={"request_id": str(uuid4())})
    with pytest.raises(ProtocolError, match="request_id"):
        ProtocolSession(original, codec=codec).accept_line(codec.encode(wrong_request))

    wrong_sequence = event.model_copy(update={"sequence": 2})
    with pytest.raises(ProtocolError, match="sequence"):
        ProtocolSession(original, codec=codec).accept_line(codec.encode(wrong_sequence))

    wrong_protocol = event.model_copy(update={"protocol_version": "1.1"})
    with pytest.raises(ProtocolError, match="protocol_version"):
        ProtocolSession(original, codec=codec).accept_line(codec.encode(wrong_protocol))


def test_protocol_session_accepts_structured_error_and_limits_total_output() -> None:
    original = request()
    codec = JsonlCodec(max_line_bytes=4096)
    error = PluginResponse(
        protocol_version="1.0",
        request_id=original.request_id,
        sequence=1,
        ok=False,
        error=PluginError(code="rate_limited", message="Try later", retryable=True),
    )
    encoded = codec.encode(error)

    accepted = ProtocolSession(original, codec=codec).accept_line(encoded)
    assert isinstance(accepted, PluginResponse)
    assert accepted.error is not None and accepted.error.retryable is True

    with pytest.raises(ProtocolError, match="total output"):
        ProtocolSession(
            original,
            codec=codec,
            max_total_bytes=len(encoded) - 1,
        ).accept_line(encoded)


def test_response_requires_exactly_one_result_or_structured_error() -> None:
    common = {
        "protocol_version": "1.0",
        "request_id": str(uuid4()),
        "sequence": 1,
    }
    with pytest.raises(ValidationError, match="result"):
        PluginResponse(**common, ok=True)
    with pytest.raises(ValidationError, match="error"):
        PluginResponse(**common, ok=False, result={"unexpected": True})
