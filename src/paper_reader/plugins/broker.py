"""Host-owned plugin permission brokers.

Plugin processes receive no direct network, host filesystem or credential access. They can
only request one of the closed operations below over the correlated JSONL channel.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Self
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from paper_reader.plugins.contracts import PermissionKind, PermissionRequest
from paper_reader.plugins.sdk import PluginError, PluginEvent

DEFAULT_BROKER_RESPONSE_BYTES = 512 * 1024
DEFAULT_BROKER_REQUEST_BYTES = 512 * 1024
DEFAULT_FILE_READ_BYTES = 512 * 1024
DEFAULT_BROKER_CALLS = 128
_MAX_BROKER_REQUEST_BASE64_CHARS = 4 * ((DEFAULT_BROKER_REQUEST_BYTES + 2) // 3)
_BROKER_OPERATIONS = Literal["network.fetch", "file.read", "credential.read"]
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SAFE_REQUEST_HEADERS = frozenset(
    {
        "accept",
        "content-type",
        "if-modified-since",
        "if-none-match",
        "user-agent",
    }
)
_SAFE_RESPONSE_HEADERS = frozenset(
    {"cache-control", "content-language", "content-type", "etag", "last-modified"}
)
_PROTOCOL_VERSION = re.compile(r"^[0-9]{1,4}\.[0-9]{1,4}$")


class BrokerError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class _BrokerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NetworkFetchRequest(_BrokerModel):
    url: str = Field(min_length=8, max_length=4096)
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body_base64: str | None = Field(
        default=None,
        max_length=_MAX_BROKER_REQUEST_BASE64_CHARS,
    )

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_name, raw_value in value.items():
            name = raw_name.strip().casefold()
            if name not in _SAFE_REQUEST_HEADERS:
                raise ValueError("Request header is not allowed by the network broker")
            if not raw_value or "\r" in raw_value or "\n" in raw_value or "\x00" in raw_value:
                raise ValueError("Request header value is invalid")
            if len(raw_value) > 2048:
                raise ValueError("Request header value is too large")
            normalized[name] = raw_value.strip()
        return dict(sorted(normalized.items()))

    @model_validator(mode="after")
    def validate_body(self) -> Self:
        if self.method == "GET" and self.body_base64 is not None:
            raise ValueError("GET broker requests cannot include a body")
        if self.body_base64 is not None:
            try:
                body = base64.b64decode(self.body_base64, validate=True)
            except ValueError as error:
                raise ValueError("Broker request body is not canonical Base64") from error
            if len(body) > DEFAULT_BROKER_REQUEST_BYTES:
                raise ValueError("Broker request body exceeds 512 KiB")
        return self


class FileReadRequest(_BrokerModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=64 * 1024, ge=1)


class CredentialReadRequest(_BrokerModel):
    pass


class BrokerCall(_BrokerModel):
    broker_request_id: str
    operation: _BROKER_OPERATIONS
    resource: str
    payload: dict[str, Any]

    @field_validator("broker_request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        try:
            normalized = str(UUID(value))
        except ValueError as error:
            raise ValueError("broker_request_id must be a canonical UUID") from error
        if normalized != value:
            raise ValueError("broker_request_id must be a canonical UUID")
        return value

    @model_validator(mode="after")
    def validate_resource_and_payload(self) -> Self:
        kind = _permission_kind(self.operation)
        normalized_permission = PermissionRequest(
            kind=kind,
            resource=self.resource,
            purpose="Validate one broker call resource",
        )
        payload_model: BaseModel
        if self.operation == "network.fetch":
            payload_model = NetworkFetchRequest.model_validate(self.payload)
        elif self.operation == "file.read":
            payload_model = FileReadRequest.model_validate(self.payload)
        else:
            payload_model = CredentialReadRequest.model_validate(self.payload)
        object.__setattr__(self, "resource", normalized_permission.resource)
        object.__setattr__(
            self,
            "payload",
            payload_model.model_dump(mode="json", exclude_none=True),
        )
        return self


class BrokerReply(_BrokerModel):
    type: Literal["broker_response"] = "broker_response"
    protocol_version: str
    request_id: str
    broker_request_id: str
    ok: bool
    result: dict[str, Any] | None = None
    error: PluginError | None = None

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol_version(cls, value: str) -> str:
        if not _PROTOCOL_VERSION.fullmatch(value):
            raise ValueError("protocol_version must use major.minor integers")
        return value

    @field_validator("request_id", "broker_request_id")
    @classmethod
    def validate_request_ids(cls, value: str) -> str:
        try:
            normalized = str(UUID(value))
        except ValueError as error:
            raise ValueError("Broker correlation IDs must be canonical UUIDs") from error
        if normalized != value:
            raise ValueError("Broker correlation IDs must be canonical UUIDs")
        return value

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> Self:
        if self.ok and (self.result is None or self.error is not None):
            raise ValueError("Successful broker response requires result and forbids error")
        if not self.ok and (self.error is None or self.result is not None):
            raise ValueError("Failed broker response requires error and forbids result")
        return self


@dataclass(frozen=True, slots=True)
class OutboundHttpRequest:
    url: str
    method: Literal["GET", "POST"]
    headers: Mapping[str, str]
    body: bytes | None
    resolved_addresses: tuple[str, ...]
    follow_redirects: bool = False
    use_environment_proxy: bool = False
    accept_encoding: str = "identity"
    max_response_bytes: int = DEFAULT_BROKER_RESPONSE_BYTES


@dataclass(frozen=True, slots=True)
class BrokerHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body_chunks: tuple[bytes, ...]
    peer_address: str


class NetworkFetchResult(_BrokerModel):
    status_code: int
    headers: dict[str, str]
    body_base64: str
    byte_size: int
    final_url: str


class FileReadResult(_BrokerModel):
    data_base64: str
    byte_size: int
    eof: bool


Resolver = Callable[[str, int], Awaitable[tuple[str, ...]]]


class NetworkTransport(Protocol):
    async def send(self, request: OutboundHttpRequest) -> BrokerHttpResponse: ...


async def resolve_host_addresses(host: str, port: int) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    answers = await loop.getaddrinfo(host, port, type=1)
    return tuple(dict.fromkeys(str(answer[4][0]) for answer in answers))


class HttpxBrokerTransport:
    """Host transport that explicitly ignores proxy environment and redirects."""

    async def send(self, request: OutboundHttpRequest) -> BrokerHttpResponse:
        headers = dict(request.headers)
        headers["Accept-Encoding"] = "identity"
        timeout = httpx.Timeout(20.0, connect=10.0)
        async with httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=timeout,
        ) as client, client.stream(
            request.method,
            request.url,
            headers=headers,
            content=request.body,
        ) as response:
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_raw():
                    total += len(chunk)
                    if total > request.max_response_bytes:
                        raise BrokerError(
                            "response_too_large",
                            "Plugin network response exceeded the configured limit",
                        )
                    chunks.append(chunk)
                peer_address = _httpx_peer_address(response)
                return BrokerHttpResponse(
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    body_chunks=tuple(chunks),
                    peer_address=peer_address,
                )


class NetworkBroker:
    def __init__(
        self,
        *,
        resolver: Resolver = resolve_host_addresses,
        transport: NetworkTransport | None = None,
        max_response_bytes: int = DEFAULT_BROKER_RESPONSE_BYTES,
        max_redirects: int = 5,
    ) -> None:
        if max_response_bytes < 1 or max_response_bytes > DEFAULT_BROKER_RESPONSE_BYTES:
            raise ValueError("max_response_bytes must be between 1 byte and 512 KiB")
        if max_redirects < 0 or max_redirects > 10:
            raise ValueError("max_redirects must be between 0 and 10")
        self._resolver = resolver
        self._transport = transport or HttpxBrokerTransport()
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects

    async def fetch(
        self,
        *,
        approved_domains: frozenset[str],
        request: NetworkFetchRequest,
    ) -> NetworkFetchResult:
        if not approved_domains:
            raise BrokerError("permission_denied", "No network domain is approved")
        current_url = request.url
        redirects = 0
        while True:
            safe_url, addresses = await self._validate_url(current_url, approved_domains)
            body = (
                base64.b64decode(request.body_base64, validate=True)
                if request.body_base64 is not None
                else None
            )
            outbound = OutboundHttpRequest(
                url=safe_url,
                method=request.method,
                headers=request.headers,
                body=body,
                resolved_addresses=addresses,
                max_response_bytes=self._max_response_bytes,
            )
            try:
                response = await self._transport.send(outbound)
            except BrokerError:
                raise
            except (httpx.HTTPError, OSError) as error:
                raise BrokerError(
                    "network_error",
                    "The approved plugin network request failed",
                    retryable=True,
                ) from error
            self._validate_peer(response.peer_address, addresses)
            if response.status_code in _REDIRECT_STATUSES:
                if redirects >= self._max_redirects:
                    raise BrokerError("redirect_limit", "Plugin network redirect limit exceeded")
                location = _header(response.headers, "location")
                if not location:
                    raise BrokerError("invalid_redirect", "Plugin network redirect is invalid")
                current_url = urljoin(safe_url, location)
                redirects += 1
                continue
            if not 200 <= response.status_code < 300:
                raise BrokerError(
                    "http_status",
                    "The approved plugin network endpoint returned an unsuccessful status",
                    retryable=response.status_code in {408, 425, 429, 500, 502, 503, 504},
                )
            declared_size = _content_length(response.headers)
            if declared_size is not None and declared_size > self._max_response_bytes:
                raise BrokerError(
                    "response_too_large",
                    "Plugin network response exceeded the configured limit",
                )
            chunks: list[bytes] = []
            total = 0
            for chunk in response.body_chunks:
                total += len(chunk)
                if total > self._max_response_bytes:
                    raise BrokerError(
                        "response_too_large",
                        "Plugin network response exceeded the configured limit",
                    )
                chunks.append(chunk)
            if declared_size is not None and total != declared_size:
                raise BrokerError(
                    "truncated_response",
                    "Plugin network response length did not match its declaration",
                    retryable=True,
                )
            content = b"".join(chunks)
            return NetworkFetchResult(
                status_code=response.status_code,
                headers=_safe_response_headers(response.headers),
                body_base64=base64.b64encode(content).decode("ascii"),
                byte_size=total,
                final_url=safe_url,
            )

    async def _validate_url(
        self,
        value: str,
        approved_domains: frozenset[str],
    ) -> tuple[str, tuple[str, ...]]:
        try:
            parsed = urlsplit(value)
            if parsed.scheme.casefold() != "https":
                raise ValueError
            if parsed.username is not None or parsed.password is not None or parsed.fragment:
                raise ValueError
            host = parsed.hostname
            if host is None:
                raise ValueError
            normalized_host = host.encode("idna").decode("ascii").casefold().rstrip(".")
            if not normalized_host or normalized_host in {"localhost"}:
                raise ValueError
            if normalized_host.endswith(".local"):
                raise ValueError
            try:
                ipaddress.ip_address(normalized_host)
            except ValueError:
                pass
            else:
                raise ValueError
            port = parsed.port or 443
            if port != 443:
                raise ValueError
        except (UnicodeError, ValueError) as error:
            raise BrokerError(
                "unsafe_url",
                "Plugin network URL is not a safe HTTPS destination",
            ) from error
        if normalized_host not in approved_domains:
            raise BrokerError(
                "permission_denied",
                "Plugin network domain is not declared and approved",
            )
        try:
            raw_addresses = await self._resolver(normalized_host, port)
        except (OSError, KeyError) as error:
            raise BrokerError(
                "dns_failure",
                "Approved plugin network domain could not be resolved",
                retryable=True,
            ) from error
        addresses: list[str] = []
        for raw_address in raw_addresses:
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError as error:
                raise BrokerError("unsafe_url", "Plugin DNS answer is invalid") from error
            if not address.is_global:
                raise BrokerError(
                    "unsafe_url",
                    "Plugin network domain resolved outside the public Internet",
                )
            rendered = str(address)
            if rendered not in addresses:
                addresses.append(rendered)
        if not addresses:
            raise BrokerError("unsafe_url", "Plugin network domain has no public address")
        netloc = normalized_host
        path = parsed.path or "/"
        return urlunsplit(("https", netloc, path, parsed.query, "")), tuple(addresses)

    @staticmethod
    def _validate_peer(peer_address: str, addresses: tuple[str, ...]) -> None:
        try:
            peer = ipaddress.ip_address(peer_address)
        except ValueError as error:
            raise BrokerError("unsafe_peer", "Plugin network peer is invalid") from error
        if not peer.is_global or str(peer) not in addresses:
            raise BrokerError(
                "unsafe_peer",
                "Plugin network peer does not match validated public DNS",
            )


FileProvider = Callable[[str], bytes]
CredentialProvider = Callable[[str], str | None]


class FileBroker:
    def __init__(
        self,
        *,
        resources: Mapping[str, FileProvider],
        max_read_bytes: int = DEFAULT_FILE_READ_BYTES,
    ) -> None:
        if max_read_bytes < 1 or max_read_bytes > DEFAULT_FILE_READ_BYTES:
            raise ValueError("max_read_bytes must be between 1 byte and 512 KiB")
        self._resources = dict(resources)
        self._max_read_bytes = max_read_bytes

    def read(self, resource: str, *, offset: int, limit: int) -> FileReadResult:
        if limit < 1 or limit > self._max_read_bytes:
            raise BrokerError("read_limit", "Plugin file broker read exceeds the limit")
        if offset < 0:
            raise BrokerError("invalid_offset", "Plugin file broker offset is invalid")
        provider = self._resources.get(resource)
        if provider is None:
            raise BrokerError(
                "resource_unavailable",
                "The abstract plugin file resource is unavailable",
            )
        try:
            content = provider(resource)
        except (OSError, RuntimeError) as error:
            raise BrokerError(
                "resource_unavailable",
                "The abstract plugin file resource could not be read",
            ) from error
        if not isinstance(content, bytes):
            raise BrokerError("resource_invalid", "Plugin file resource is not binary data")
        selected = content[offset : offset + limit]
        return FileReadResult(
            data_base64=base64.b64encode(selected).decode("ascii"),
            byte_size=len(selected),
            eof=offset + len(selected) >= len(content),
        )


class SecretRedactor:
    """Remember only in-memory exact values used during one plugin call."""

    def __init__(self) -> None:
        self._values: set[str] = set()

    def register(self, value: str) -> None:
        if value:
            self._values.add(value)

    def redact_text(self, value: str) -> str:
        redacted = value
        for secret in sorted(self._values, key=len, reverse=True):
            redacted = redacted.replace(secret, "<redacted>")
        return redacted

    def redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, bytes):
            return self.redact_text(value.decode("utf-8", errors="replace")).encode("utf-8")
        if isinstance(value, list):
            return [self.redact_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact_value(item) for item in value)
        if isinstance(value, dict):
            return {
                self.redact_text(str(key)): self.redact_value(item)
                for key, item in value.items()
            }
        return value


class CredentialBroker:
    def __init__(
        self,
        *,
        provider: CredentialProvider,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self._provider = provider
        self.redactor = redactor or SecretRedactor()

    def read(self, resource: str) -> str:
        try:
            value = self._provider(resource)
        except (OSError, RuntimeError) as error:
            raise BrokerError(
                "credential_unavailable",
                "The approved plugin credential is unavailable",
            ) from error
        if not value:
            raise BrokerError(
                "credential_unavailable",
                "The approved plugin credential is unavailable",
            )
        if len(value.encode("utf-8")) > 64 * 1024:
            raise BrokerError("credential_invalid", "Plugin credential exceeds the limit")
        self.redactor.register(value)
        return value


class PermissionBroker:
    def __init__(
        self,
        *,
        protocol_version: str,
        request_id: str,
        declared: tuple[PermissionRequest, ...],
        granted: tuple[PermissionRequest, ...],
        network: NetworkBroker,
        files: FileBroker,
        credentials: CredentialBroker,
        redactor: SecretRedactor | None = None,
        max_calls: int = DEFAULT_BROKER_CALLS,
    ) -> None:
        if not _PROTOCOL_VERSION.fullmatch(protocol_version):
            raise ValueError("Plugin broker protocol version is invalid")
        try:
            if str(UUID(request_id)) != request_id:
                raise ValueError
        except ValueError as error:
            raise ValueError("Plugin broker request_id must be a canonical UUID") from error
        self.protocol_version = protocol_version
        self.request_id = request_id
        self._declared = {(item.kind, item.resource) for item in declared}
        self._granted = {(item.kind, item.resource) for item in granted}
        if not self._granted <= self._declared:
            raise ValueError("Granted plugin permissions must be declared")
        if max_calls < 1 or max_calls > 1024:
            raise ValueError("max_calls must be between 1 and 1024")
        self._network = network
        self._files = files
        self._credentials = credentials
        self.redactor = redactor or credentials.redactor
        self._max_calls = max_calls
        self._calls = 0
        self._seen_request_ids: set[str] = set()

    async def handle(self, call: BrokerCall) -> BrokerReply:
        if call.broker_request_id in self._seen_request_ids:
            return self._failure(
                call,
                BrokerError(
                    "duplicate_broker_request",
                    "Plugin broker request ID has already been consumed",
                ),
            )
        if self._calls >= self._max_calls:
            return self._failure(
                call,
                BrokerError(
                    "broker_call_limit",
                    "Plugin broker request count exceeded the configured limit",
                ),
            )
        self._seen_request_ids.add(call.broker_request_id)
        self._calls += 1
        kind = _permission_kind(call.operation)
        permission = (kind, call.resource)
        if permission not in self._declared or permission not in self._granted:
            return self._failure(
                call,
                BrokerError(
                    "permission_denied",
                    "Plugin permission is not declared and approved for this version",
                ),
            )
        try:
            if call.operation == "network.fetch":
                request = NetworkFetchRequest.model_validate(call.payload)
                approved_domains = frozenset(
                    resource
                    for permission_kind, resource in self._granted
                    if permission_kind is PermissionKind.NETWORK
                )
                result: dict[str, Any] = (
                    await self._network.fetch(
                        approved_domains=approved_domains,
                        request=request,
                    )
                ).model_dump(mode="json")
            elif call.operation == "file.read":
                file_request = FileReadRequest.model_validate(call.payload)
                file_result = await asyncio.to_thread(
                    self._files.read,
                    call.resource,
                    offset=file_request.offset,
                    limit=file_request.limit,
                )
                result = file_result.model_dump(mode="json")
            else:
                CredentialReadRequest.model_validate(call.payload)
                result = {
                    "value": await asyncio.to_thread(
                        self._credentials.read,
                        call.resource,
                    )
                }
        except ValidationError:
            return self._failure(
                call,
                BrokerError("invalid_broker_request", "Plugin broker request is invalid"),
            )
        except BrokerError as error:
            return self._failure(call, error)
        return BrokerReply(
            protocol_version=self.protocol_version,
            request_id=self.request_id,
            broker_request_id=call.broker_request_id,
            ok=True,
            result=result,
        )

    async def handle_event(self, event: PluginEvent) -> BrokerReply:
        if event.event != "broker.request":
            raise BrokerError("invalid_broker_event", "Plugin event is not a broker request")
        if (
            event.protocol_version != self.protocol_version
            or event.request_id != self.request_id
        ):
            raise BrokerError("request_mismatch", "Plugin broker event is not correlated")
        try:
            call = BrokerCall.model_validate(event.payload)
        except ValidationError as error:
            raise BrokerError(
                "invalid_broker_request",
                "Plugin broker event payload is invalid",
            ) from error
        return await self.handle(call)

    def _failure(self, call: BrokerCall, error: BrokerError) -> BrokerReply:
        return BrokerReply(
            protocol_version=self.protocol_version,
            request_id=self.request_id,
            broker_request_id=call.broker_request_id,
            ok=False,
            error=PluginError(
                code=error.code,
                message=str(error),
                retryable=error.retryable,
            ),
        )


def _permission_kind(operation: str) -> PermissionKind:
    if operation == "network.fetch":
        return PermissionKind.NETWORK
    if operation == "file.read":
        return PermissionKind.FILE
    if operation == "credential.read":
        return PermissionKind.CREDENTIAL
    raise ValueError("Unknown plugin broker operation")


def _safe_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        name.casefold(): value
        for name, value in headers.items()
        if name.casefold() in _SAFE_RESPONSE_HEADERS
        and "\r" not in value
        and "\n" not in value
        and "\x00" not in value
        and len(value) <= 4096
    }


def _header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.casefold()
    for key, value in headers.items():
        if key.casefold() == expected:
            return value
    return None


def _content_length(headers: Mapping[str, str]) -> int | None:
    raw = _header(headers, "content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as error:
        raise BrokerError(
            "invalid_response",
            "Plugin response content length is invalid",
        ) from error
    if value < 0:
        raise BrokerError("invalid_response", "Plugin response content length is invalid")
    return value


def _httpx_peer_address(response: httpx.Response) -> str:
    network_stream = response.extensions.get("network_stream")
    get_extra_info = getattr(network_stream, "get_extra_info", None)
    if not callable(get_extra_info):
        raise BrokerError("unsafe_peer", "Plugin network peer could not be verified")
    server_address: Any = get_extra_info("server_addr")
    if not isinstance(server_address, tuple) or not server_address:
        raise BrokerError("unsafe_peer", "Plugin network peer could not be verified")
    raw_address = server_address[0]
    if not isinstance(raw_address, (str, bytes, int)):
        raise BrokerError("unsafe_peer", "Plugin network peer could not be verified")
    try:
        return str(ipaddress.ip_address(raw_address))
    except (TypeError, ValueError) as error:
        raise BrokerError("unsafe_peer", "Plugin network peer could not be verified") from error
