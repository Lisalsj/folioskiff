"""Strict, canonical plugin manifest contracts evaluated before execution."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Self

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PLUGIN_PROTOCOL_VERSION = "1.0"
PLUGIN_PROTOCOL_MAJOR = 1
MAX_CONFIG_SCHEMA_BYTES = 64 * 1024
MAX_SCHEMA_DEPTH = 32
MAX_SCHEMA_NODES = 4096

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,15}$")
_ABSTRACT_RESOURCE = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")
_VERSION_TEXT = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,79}$")
_PROTOCOL_VERSION = re.compile(r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")

_SCHEMA_KEYWORDS = frozenset(
    {
        "$anchor",
        "$comment",
        "$defs",
        "$dynamicAnchor",
        "$dynamicRef",
        "$id",
        "$ref",
        "$schema",
        "additionalProperties",
        "allOf",
        "anyOf",
        "const",
        "contains",
        "contentEncoding",
        "contentMediaType",
        "deprecated",
        "dependentRequired",
        "dependentSchemas",
        "description",
        "else",
        "enum",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "if",
        "items",
        "maxContains",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minContains",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "not",
        "oneOf",
        "pattern",
        "patternProperties",
        "prefixItems",
        "properties",
        "propertyNames",
        "readOnly",
        "required",
        "then",
        "title",
        "type",
        "unevaluatedItems",
        "unevaluatedProperties",
        "uniqueItems",
        "writeOnly",
    }
)
_SCHEMA_MAPS = frozenset(
    {"$defs", "dependentSchemas", "definitions", "patternProperties", "properties"}
)
_SCHEMA_LISTS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_VALUES = frozenset(
    {
        "additionalProperties",
        "contains",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
_ALLOWED_FORMATS = frozenset(
    {"date", "date-time", "email", "hostname", "ipv4", "ipv6", "time", "uri", "uuid"}
)


class CapabilityKind(StrEnum):
    SOURCE = "source_connector"
    METADATA = "metadata_enricher"
    SCORE = "score_dimension"
    AGENT = "agent_backend"
    PUBLISHER = "publisher"


class PermissionKind(StrEnum):
    NETWORK = "network"
    FILE = "file"
    CREDENTIAL = "credential"


PLUGIN_CAPABILITY_METHODS: Mapping[CapabilityKind, frozenset[str]] = MappingProxyType(
    {
        CapabilityKind.SOURCE: frozenset({"estimate", "fetch_page", "probe"}),
        CapabilityKind.METADATA: frozenset({"enrich", "probe"}),
        CapabilityKind.SCORE: frozenset({"evaluate", "probe"}),
        CapabilityKind.AGENT: frozenset({"generate", "models", "probe"}),
        CapabilityKind.PUBLISHER: frozenset({"probe", "publish"}),
    }
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProtocolRange(_FrozenModel):
    minimum: str
    maximum: str

    @field_validator("minimum", "maximum")
    @classmethod
    def validate_version(cls, value: str) -> str:
        _parse_protocol_version(value)
        return value

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if _parse_protocol_version(self.minimum) > _parse_protocol_version(self.maximum):
            raise ValueError("Protocol minimum cannot exceed maximum")
        return self


class PermissionRequest(_FrozenModel):
    kind: PermissionKind
    resource: str = Field(min_length=2, max_length=253)
    purpose: str = Field(min_length=3, max_length=300)

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, value: str) -> str:
        if _CONTROL_CHARACTER.search(value):
            raise ValueError("Permission purpose cannot contain control characters")
        normalized = value.strip()
        if not normalized:
            raise ValueError("Permission purpose cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_resource(self) -> Self:
        normalized = self.resource.strip().casefold()
        if self.kind is PermissionKind.NETWORK:
            try:
                ipaddress.ip_address(normalized)
            except ValueError:
                pass
            else:
                raise ValueError("Network permission resource must be a public DNS hostname")
            if (
                not _HOSTNAME.fullmatch(normalized)
                or normalized.endswith(".local")
                or "*" in normalized
            ):
                raise ValueError("Network permission resource must be a canonical DNS hostname")
        elif not _ABSTRACT_RESOURCE.fullmatch(normalized) or ".." in normalized:
            raise ValueError(
                "File and credential permission resource must be an abstract broker identifier"
            )
        object.__setattr__(self, "resource", normalized)
        return self


class PluginCapability(_FrozenModel):
    capability_id: str = Field(min_length=3, max_length=80)
    kind: CapabilityKind
    methods: tuple[str, ...] = Field(min_length=1)
    permissions: tuple[PermissionRequest, ...] = ()
    timeout_seconds: int = Field(default=60, ge=1, le=3600)

    @field_validator("capability_id")
    @classmethod
    def validate_capability_id(cls, value: str) -> str:
        return _validated_identifier(value, "capability_id")

    @model_validator(mode="after")
    def validate_methods_and_permissions(self) -> Self:
        methods = tuple(sorted(self.methods))
        if len(methods) != len(set(methods)):
            raise ValueError("Capability methods cannot contain duplicates")
        invalid = set(methods) - PLUGIN_CAPABILITY_METHODS[self.kind]
        if invalid:
            raise ValueError(f"Unknown method for {self.kind.value}: {', '.join(sorted(invalid))}")
        permissions = tuple(
            sorted(self.permissions, key=lambda item: (item.kind.value, item.resource))
        )
        permission_keys = [(item.kind, item.resource) for item in permissions]
        if len(permission_keys) != len(set(permission_keys)):
            raise ValueError("Capability permissions cannot contain duplicate resources")
        object.__setattr__(self, "methods", methods)
        object.__setattr__(self, "permissions", permissions)
        return self


class PluginManifest(_FrozenModel):
    manifest_version: str
    plugin_id: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    distribution_name: str = Field(min_length=1, max_length=160)
    distribution_version: str = Field(min_length=1, max_length=80)
    protocol: ProtocolRange
    capabilities: tuple[PluginCapability, ...] = Field(min_length=1, max_length=40)
    config_schema: dict[str, Any]
    license: str = Field(min_length=1, max_length=120)

    @field_validator("manifest_version")
    @classmethod
    def validate_manifest_version(cls, value: str) -> str:
        if value != "1":
            raise ValueError("Only plugin manifest version 1 is supported")
        return value

    @field_validator("plugin_id")
    @classmethod
    def validate_plugin_id(cls, value: str) -> str:
        return _validated_identifier(value, "plugin_id")

    @field_validator("display_name", "distribution_name", "license")
    @classmethod
    def validate_display_text(cls, value: str) -> str:
        if _CONTROL_CHARACTER.search(value):
            raise ValueError("Manifest text fields cannot be blank or contain controls")
        normalized = value.strip()
        if not normalized:
            raise ValueError("Manifest text fields cannot be blank or contain controls")
        return normalized

    @field_validator("distribution_version")
    @classmethod
    def validate_distribution_version(cls, value: str) -> str:
        if not _VERSION_TEXT.fullmatch(value):
            raise ValueError("Distribution version must be a bounded version identifier")
        return value

    @field_validator("config_schema")
    @classmethod
    def validate_config_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_config_schema(value)
        return value

    @model_validator(mode="after")
    def normalize_capabilities(self) -> Self:
        capabilities = tuple(sorted(self.capabilities, key=lambda item: item.capability_id))
        identifiers = [item.capability_id for item in capabilities]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Duplicate capability ID in plugin manifest")
        object.__setattr__(self, "capabilities", capabilities)
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.model_dump(mode="json"))

    def canonical_sha256(self) -> str:
        return sha256(self.canonical_bytes()).hexdigest()

    def requested_permissions(self) -> tuple[PermissionRequest, ...]:
        permissions = {
            (permission.kind, permission.resource): permission
            for capability in self.capabilities
            for permission in capability.permissions
        }
        return tuple(
            permissions[key]
            for key in sorted(permissions, key=lambda item: (item[0].value, item[1]))
        )


def is_protocol_compatible(protocol: ProtocolRange, *, host_version: str) -> bool:
    host = _parse_protocol_version(host_version)
    minimum = _parse_protocol_version(protocol.minimum)
    maximum = _parse_protocol_version(protocol.maximum)
    return host[0] == PLUGIN_PROTOCOL_MAJOR and minimum <= host <= maximum


def _validated_identifier(value: str, field_name: str) -> str:
    normalized = value.strip().casefold()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a stable lowercase identifier")
    return normalized


def _parse_protocol_version(value: str) -> tuple[int, int]:
    matched = _PROTOCOL_VERSION.fullmatch(value)
    if matched is None:
        raise ValueError("Protocol version must use major.minor integers")
    return int(matched.group(1)), int(matched.group(2))


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _validate_config_schema(schema: dict[str, Any]) -> None:
    try:
        encoded = _canonical_json_bytes(schema)
    except (TypeError, ValueError) as error:
        raise ValueError("Configuration JSON Schema must contain JSON-compatible values") from error
    if len(encoded) > MAX_CONFIG_SCHEMA_BYTES:
        raise ValueError("Configuration JSON Schema exceeds the 64 KiB limit")
    if schema.get("type") != "object":
        raise ValueError("Configuration JSON Schema root type must be object")
    if schema.get("additionalProperties") is not False:
        raise ValueError("Configuration JSON Schema must reject unknown configuration fields")
    declared_draft = schema.get("$schema")
    if declared_draft not in (None, "https://json-schema.org/draft/2020-12/schema"):
        raise ValueError("Configuration JSON Schema must use draft 2020-12")
    node_counter = [0]
    _validate_schema_node(schema, path="$", depth=0, node_counter=node_counter)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ValueError("Configuration JSON Schema is invalid") from error


def _validate_schema_node(
    schema: Any,
    *,
    path: str,
    depth: int,
    node_counter: list[int],
) -> None:
    node_counter[0] += 1
    if node_counter[0] > MAX_SCHEMA_NODES:
        raise ValueError("Configuration JSON Schema has too many nodes")
    if depth > MAX_SCHEMA_DEPTH:
        raise ValueError("Configuration JSON Schema is too deeply nested")
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise ValueError(f"Configuration JSON Schema node {path} must be an object or boolean")
    unknown = set(schema) - _SCHEMA_KEYWORDS
    if unknown:
        raise ValueError(f"Unknown JSON Schema keyword at {path}: {sorted(unknown)[0]}")
    for keyword in ("$ref", "$dynamicRef"):
        reference = schema.get(keyword)
        if reference is not None and (
            not isinstance(reference, str) or not (reference == "#" or reference.startswith("#/"))
        ):
            raise ValueError("remote JSON Schema references are forbidden")
    schema_format = schema.get("format")
    if schema_format is not None and schema_format not in _ALLOWED_FORMATS:
        raise ValueError(f"Unknown JSON Schema format: {schema_format}")

    for keyword in _SCHEMA_MAPS:
        nested = schema.get(keyword)
        if nested is None:
            continue
        if not isinstance(nested, dict):
            raise ValueError(f"JSON Schema keyword {keyword} must be an object")
        for name, child in nested.items():
            _validate_schema_node(
                child,
                path=f"{path}/{keyword}/{name}",
                depth=depth + 1,
                node_counter=node_counter,
            )
    for keyword in _SCHEMA_LISTS:
        nested_list = schema.get(keyword)
        if nested_list is None:
            continue
        if not isinstance(nested_list, list):
            raise ValueError(f"JSON Schema keyword {keyword} must be an array")
        for index, child in enumerate(nested_list):
            _validate_schema_node(
                child,
                path=f"{path}/{keyword}/{index}",
                depth=depth + 1,
                node_counter=node_counter,
            )
    for keyword in _SCHEMA_VALUES:
        child = schema.get(keyword)
        if child is None or not isinstance(child, (dict, bool)):
            continue
        _validate_schema_node(
            child,
            path=f"{path}/{keyword}",
            depth=depth + 1,
            node_counter=node_counter,
        )
