"""Fail-closed validation and bounded reachability checks for public knowledge URLs."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from urllib.parse import parse_qsl, quote, urljoin, urlsplit, urlunsplit

MAX_PUBLIC_URL_CHARACTERS = 2_048
MAX_PUBLIC_RESPONSE_BYTES = 64 * 1024
MAX_PUBLIC_REDIRECTS = 5
MAX_PUBLIC_DNS_ANSWERS = 32
MAX_PUBLIC_HEADER_BYTES = 64 * 1024
MAX_PUBLIC_HEADERS = 200

_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")
_HEADER_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_HEAD_UNSUPPORTED_STATUSES = frozenset({405, 501})
_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_ALLOWED_REQUEST_HEADERS = frozenset({"accept", "range"})
_DEFAULT_ACCEPT = "text/html,application/xhtml+xml,application/pdf;q=0.8,*/*;q=0.1"
_SENSITIVE_QUERY_NAMES = frozenset(
    {
        "access_token",
        "api-key",
        "api_key",
        "apikey",
        "auth_token",
        "authorization",
        "client_secret",
        "jwt",
        "password",
        "secret",
        "session_token",
        "sig",
        "signature",
        "token",
        "x-amz-credential",
        "x-amz-security-token",
        "x-amz-signature",
        "x-goog-signature",
    }
)

PublicMethod = Literal["HEAD", "GET"]
Resolver = Callable[[str, int], Awaitable[tuple[str, ...]]]
StreamOpener = Callable[..., Awaitable[tuple[Any, Any]]]
MonotonicClock = Callable[[], float]


class PublicUrlRejected(RuntimeError):
    """A public URL or its reachability proof failed a closed security rule."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ValidatedPublicUrl:
    url: str
    domain: str
    resolved_addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OutboundPublicRequest:
    url: str
    method: PublicMethod
    headers: Mapping[str, str] = field(default_factory=dict)
    resolved_addresses: tuple[str, ...] = ()
    max_response_bytes: int = MAX_PUBLIC_RESPONSE_BYTES

    def __post_init__(self) -> None:
        if self.method not in {"HEAD", "GET"}:
            raise ValueError("public verification supports only HEAD and GET")
        if not self.resolved_addresses:
            raise ValueError("public verification requires pinned DNS addresses")
        if not 1 <= self.max_response_bytes <= MAX_PUBLIC_RESPONSE_BYTES:
            raise ValueError("public response limit must be between 1 byte and 64 KiB")
        for raw_name, raw_value in self.headers.items():
            name = raw_name.casefold()
            if name not in _ALLOWED_REQUEST_HEADERS or not _HEADER_NAME.fullmatch(raw_name):
                raise ValueError("public verification request header is not allowed")
            if not raw_value or _HEADER_CONTROL.search(raw_value):
                raise ValueError("public verification request header is unsafe")


@dataclass(frozen=True, slots=True)
class PublicHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    peer_address: str


@dataclass(frozen=True, slots=True)
class VerifiedPublicUrl:
    canonical_url: str
    final_url: str
    domain: str
    http_status: int
    accessed_at: datetime

    def __post_init__(self) -> None:
        if self.accessed_at.utcoffset() is None:
            raise ValueError("accessed_at must include a timezone")


class PublicHttpTransport(Protocol):
    async def send(self, request: OutboundPublicRequest) -> PublicHttpResponse: ...


async def resolve_public_host(host: str, port: int) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    answers = await loop.getaddrinfo(host, port, type=1)
    return tuple(dict.fromkeys(str(answer[4][0]) for answer in answers))


class PublicUrlPolicy:
    """Canonicalize an HTTPS URL and resolve every address before any request."""

    def __init__(
        self,
        resolver: Resolver = resolve_public_host,
        *,
        dns_timeout_seconds: float = 5.0,
    ) -> None:
        if dns_timeout_seconds <= 0 or dns_timeout_seconds > 30:
            raise ValueError("DNS timeout must be between 0 and 30 seconds")
        self._resolver = resolver
        self._dns_timeout_seconds = dns_timeout_seconds

    async def validate(self, value: str) -> ValidatedPublicUrl:
        canonical, domain = _canonical_public_url(value)
        try:
            raw_answers = await asyncio.wait_for(
                self._resolver(domain, 443),
                timeout=self._dns_timeout_seconds,
            )
        except TimeoutError as error:
            raise PublicUrlRejected(
                "dns_timeout",
                "Public resource DNS resolution timed out",
                retryable=True,
            ) from error
        except Exception as error:
            raise PublicUrlRejected(
                "dns_failure",
                "Public resource DNS resolution failed",
                retryable=True,
            ) from error
        if isinstance(raw_answers, (str, bytes)):
            raise PublicUrlRejected("unsafe_dns", "Public resource DNS answer is invalid")
        answers = tuple(raw_answers)
        if not answers or len(answers) > MAX_PUBLIC_DNS_ANSWERS:
            raise PublicUrlRejected(
                "unsafe_dns",
                "Public resource must resolve to a bounded public address set",
            )
        rendered: list[str] = []
        for raw_answer in answers:
            try:
                address = ipaddress.ip_address(raw_answer)
            except ValueError as error:
                raise PublicUrlRejected(
                    "unsafe_dns",
                    "Public resource DNS answer is not an IP address",
                ) from error
            if not address.is_global:
                raise PublicUrlRejected(
                    "unsafe_dns",
                    "Public resource resolved outside the public Internet",
                )
            normalized = str(address)
            if normalized not in rendered:
                rendered.append(normalized)
        return ValidatedPublicUrl(
            url=canonical,
            domain=domain,
            resolved_addresses=tuple(rendered),
        )


class PinnedHttpsTransport:
    """Send a minimal HTTP/1.1 probe to a validated IP while preserving TLS SNI."""

    def __init__(
        self,
        *,
        opener: StreamOpener = asyncio.open_connection,
        timeout_seconds: float = 10.0,
        ssl_context: ssl.SSLContext | None = None,
        monotonic: MonotonicClock = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("public HTTP timeout must be between 0 and 60 seconds")
        if ssl_context is not None and (
            ssl_context.verify_mode != ssl.CERT_REQUIRED or not ssl_context.check_hostname
        ):
            raise ValueError("public HTTPS verification requires certificate validation")
        self._opener = opener
        self._timeout_seconds = timeout_seconds
        self._ssl_context = ssl_context
        self._monotonic = monotonic

    async def send(self, request: OutboundPublicRequest) -> PublicHttpResponse:
        canonical, host = _canonical_public_url(request.url)
        parsed = urlsplit(canonical)
        target = _request_target(parsed.path or "/", parsed.query)
        addresses = _validated_pinned_addresses(request.resolved_addresses)
        ssl_context = self._ssl_context or ssl.create_default_context()
        last_network_error: BaseException | None = None
        deadline = self._monotonic() + self._timeout_seconds

        for address in addresses:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                break
            writer: Any | None = None
            try:
                reader, connected_writer = await asyncio.wait_for(
                    self._opener(
                        address,
                        443,
                        ssl=ssl_context,
                        server_hostname=host,
                    ),
                    timeout=remaining,
                )
                writer = connected_writer
                peer = _writer_peer(connected_writer)
                if peer != address or peer not in addresses:
                    raise PublicUrlRejected(
                        "unsafe_peer",
                        "Public resource peer does not match the validated DNS address",
                    )
                payload = _request_bytes(
                    method=request.method,
                    target=target,
                    host=host,
                    headers=request.headers,
                )
                connected_writer.write(payload)
                remaining = _remaining(deadline, self._monotonic)
                await asyncio.wait_for(
                    connected_writer.drain(),
                    timeout=remaining,
                )
                remaining = _remaining(deadline, self._monotonic)
                response = await asyncio.wait_for(
                    _read_response(
                        reader,
                        method=request.method,
                        peer=peer,
                        maximum=request.max_response_bytes,
                        timeout=remaining,
                    ),
                    timeout=remaining,
                )
                return response
            except PublicUrlRejected:
                raise
            except (OSError, TimeoutError, asyncio.IncompleteReadError) as error:
                last_network_error = error
            finally:
                if writer is not None:
                    writer.close()
                    with suppress(Exception):
                        await writer.wait_closed()
        raise PublicUrlRejected(
            "network_error",
            "Public resource could not be reached through its validated address",
            retryable=True,
        ) from last_network_error


class PublicUrlVerifier:
    """Verify reachability without downloading arbitrary page bodies."""

    def __init__(
        self,
        policy: PublicUrlPolicy,
        *,
        transport: PublicHttpTransport | None = None,
        max_redirects: int = MAX_PUBLIC_REDIRECTS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 0 <= max_redirects <= MAX_PUBLIC_REDIRECTS:
            raise ValueError("public redirect limit must be between 0 and 5")
        self._policy = policy
        self._transport = transport or PinnedHttpsTransport()
        self._max_redirects = max_redirects
        self._clock = clock or (lambda: datetime.now(UTC))

    async def verify(self, value: str) -> VerifiedPublicUrl:
        current = await self._policy.validate(value)
        canonical_url = current.url
        method: PublicMethod = "HEAD"
        redirects = 0
        while True:
            headers = {"Accept": _DEFAULT_ACCEPT}
            if method == "GET":
                headers["Range"] = f"bytes=0-{MAX_PUBLIC_RESPONSE_BYTES - 1}"
            response = await self._transport.send(
                OutboundPublicRequest(
                    url=current.url,
                    method=method,
                    headers=headers,
                    resolved_addresses=current.resolved_addresses,
                    max_response_bytes=MAX_PUBLIC_RESPONSE_BYTES,
                )
            )
            _validate_response_peer(response.peer_address, current.resolved_addresses)
            if len(response.body) > MAX_PUBLIC_RESPONSE_BYTES:
                raise PublicUrlRejected(
                    "response_too_large",
                    "Public resource verification response exceeded 64 KiB",
                )
            if response.status_code in _REDIRECT_STATUSES:
                if redirects >= self._max_redirects:
                    raise PublicUrlRejected(
                        "redirect_limit",
                        "Public resource exceeded the redirect limit",
                    )
                location = _header(response.headers, "location")
                if location is None:
                    raise PublicUrlRejected(
                        "invalid_redirect",
                        "Public resource redirect did not include a location",
                    )
                current = await self._policy.validate(urljoin(current.url, location))
                redirects += 1
                continue
            if method == "HEAD" and response.status_code in _HEAD_UNSUPPORTED_STATUSES:
                method = "GET"
                continue
            if not 200 <= response.status_code < 300:
                raise PublicUrlRejected(
                    "http_status",
                    "Public resource returned an unsuccessful HTTP status",
                    retryable=response.status_code in _RETRYABLE_STATUSES,
                )
            accessed_at = self._clock()
            if accessed_at.utcoffset() is None:
                raise ValueError("public URL verifier clock must return an aware datetime")
            return VerifiedPublicUrl(
                canonical_url=canonical_url,
                final_url=current.url,
                domain=current.domain,
                http_status=response.status_code,
                accessed_at=accessed_at.astimezone(UTC),
            )


def _canonical_public_url(value: str) -> tuple[str, str]:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PUBLIC_URL_CHARACTERS
        or value != value.strip()
        or _CONTROL_OR_SPACE.search(value)
        or "\\" in value
    ):
        raise PublicUrlRejected("unsafe_url", "Public resource URL is not a safe HTTPS URL")
    try:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() != "https":
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.port not in {None, 443}:
            raise ValueError
        raw_host = parsed.hostname
        if raw_host is None:
            raise ValueError
        domain = raw_host.encode("idna").decode("ascii").casefold().rstrip(".")
        if not _valid_dns_name(domain):
            raise ValueError
        query_fields = parse_qsl(
            parsed.query.replace(";", "&"),
            keep_blank_values=True,
            max_num_fields=100,
        )
        if any(name.casefold() in _SENSITIVE_QUERY_NAMES for name, _ in query_fields):
            raise ValueError
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            raise ValueError
    except (UnicodeError, ValueError) as error:
        raise PublicUrlRejected(
            "unsafe_url",
            "Public resource URL is not a safe HTTPS destination",
        ) from error
    path = parsed.path or "/"
    canonical = urlunsplit(("https", domain, path, parsed.query, ""))
    if len(canonical) > MAX_PUBLIC_URL_CHARACTERS:
        raise PublicUrlRejected("unsafe_url", "Public resource URL is too long")
    return canonical, domain


def _valid_dns_name(domain: str) -> bool:
    if (
        not domain
        or len(domain) > 253
        or "." not in domain
        or domain == "localhost"
        or domain.endswith(".localhost")
        or domain.endswith(".local")
    ):
        return False
    labels = domain.split(".")
    return all(_HOST_LABEL.fullmatch(label) is not None for label in labels)


def _validated_pinned_addresses(values: tuple[str, ...]) -> tuple[str, ...]:
    if not values or len(values) > MAX_PUBLIC_DNS_ANSWERS:
        raise PublicUrlRejected("unsafe_dns", "Pinned public address set is invalid")
    addresses: list[str] = []
    for raw_value in values:
        try:
            address = ipaddress.ip_address(raw_value)
        except ValueError as error:
            raise PublicUrlRejected("unsafe_dns", "Pinned public address is invalid") from error
        if not address.is_global:
            raise PublicUrlRejected("unsafe_dns", "Pinned address is not public")
        rendered = str(address)
        if rendered not in addresses:
            addresses.append(rendered)
    return tuple(addresses)


def _remaining(deadline: float, clock: MonotonicClock) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise TimeoutError("public HTTP verification deadline expired")
    return remaining


def _writer_peer(writer: Any) -> str:
    peer = writer.get_extra_info("peername")
    if not isinstance(peer, tuple) or not peer:
        raise PublicUrlRejected("unsafe_peer", "Public resource peer could not be verified")
    raw_address = peer[0]
    if not isinstance(raw_address, (str, bytes, int)):
        raise PublicUrlRejected("unsafe_peer", "Public resource peer could not be verified")
    try:
        address = ipaddress.ip_address(raw_address)
    except (TypeError, ValueError) as error:
        raise PublicUrlRejected(
            "unsafe_peer",
            "Public resource peer is not a valid IP address",
        ) from error
    if not address.is_global:
        raise PublicUrlRejected("unsafe_peer", "Public resource peer is not public")
    return str(address)


def _validate_response_peer(peer: str, addresses: tuple[str, ...]) -> None:
    try:
        address = ipaddress.ip_address(peer)
    except ValueError as error:
        raise PublicUrlRejected("unsafe_peer", "Public resource peer is invalid") from error
    normalized = str(address)
    if not address.is_global or normalized not in addresses:
        raise PublicUrlRejected(
            "unsafe_peer",
            "Public resource peer does not match validated public DNS",
        )


def _request_target(path: str, query: str) -> str:
    encoded_path = quote(path, safe="/%:@!$&'()*+,;=-._~")
    if not encoded_path.startswith("/"):
        encoded_path = "/" + encoded_path
    if not query:
        return encoded_path
    encoded_query = quote(query, safe="=&?/:;+,%@-._~!$'()*")
    return f"{encoded_path}?{encoded_query}"


def _request_bytes(
    *,
    method: PublicMethod,
    target: str,
    host: str,
    headers: Mapping[str, str],
) -> bytes:
    rendered = [
        f"{method} {target} HTTP/1.1",
        f"Host: {host}",
        "User-Agent: paper-reader/1 public-resource-verifier",
        "Accept-Encoding: identity",
        "Connection: close",
    ]
    for raw_name, raw_value in headers.items():
        name = raw_name.casefold()
        if name not in _ALLOWED_REQUEST_HEADERS or not _HEADER_NAME.fullmatch(raw_name):
            raise PublicUrlRejected("unsafe_header", "Public verification header is not allowed")
        if not raw_value or any(character in raw_value for character in ("\r", "\n", "\x00")):
            raise PublicUrlRejected("unsafe_header", "Public verification header is unsafe")
        rendered.append(f"{raw_name}: {raw_value}")
    return ("\r\n".join(rendered) + "\r\n\r\n").encode("ascii")


async def _read_response(
    reader: Any,
    *,
    method: PublicMethod,
    peer: str,
    maximum: int,
    timeout: float,
) -> PublicHttpResponse:
    status_line = await _readline(reader, timeout=timeout)
    if len(status_line) > 8_192:
        raise PublicUrlRejected("invalid_response", "Public HTTP status line is too large")
    match = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})(?: [^\r\n]*)?\r?\n", status_line)
    if match is None:
        raise PublicUrlRejected("invalid_response", "Public HTTP status line is invalid")
    status_code = int(match.group(1))
    header_bytes = len(status_line)
    headers: dict[str, str] = {}
    for _ in range(MAX_PUBLIC_HEADERS):
        line = await _readline(reader, timeout=timeout)
        header_bytes += len(line)
        if header_bytes > MAX_PUBLIC_HEADER_BYTES or len(line) > 8_192:
            raise PublicUrlRejected("invalid_response", "Public HTTP headers are too large")
        if line in {b"\r\n", b"\n"}:
            break
        if line[:1] in {b" ", b"\t"} or b":" not in line:
            raise PublicUrlRejected("invalid_response", "Public HTTP header is invalid")
        raw_name, raw_value = line.rstrip(b"\r\n").split(b":", 1)
        try:
            name = raw_name.decode("ascii").casefold()
            value = raw_value.decode("iso-8859-1").strip()
        except UnicodeError as error:
            raise PublicUrlRejected("invalid_response", "Public HTTP header is invalid") from error
        if not _HEADER_NAME.fullmatch(name) or any(
            character in value for character in ("\r", "\n", "\x00")
        ):
            raise PublicUrlRejected("invalid_response", "Public HTTP header is invalid")
        if name in headers:
            headers[name] = f"{headers[name]}, {value}"
        else:
            headers[name] = value
    else:
        raise PublicUrlRejected("invalid_response", "Public HTTP response has too many headers")

    body = b""
    if method == "GET" and status_code not in _REDIRECT_STATUSES and status_code not in {204, 304}:
        body = await _read_bounded_body(reader, headers, maximum=maximum, timeout=timeout)
    return PublicHttpResponse(
        status_code=status_code,
        headers=headers,
        body=body,
        peer_address=peer,
    )


async def _readline(reader: Any, *, timeout: float) -> bytes:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    except TimeoutError as error:
        raise PublicUrlRejected(
            "network_timeout",
            "Public resource response timed out",
            retryable=True,
        ) from error
    if not line:
        raise PublicUrlRejected("invalid_response", "Public HTTP response ended unexpectedly")
    return bytes(line)


async def _read_bounded_body(
    reader: Any,
    headers: Mapping[str, str],
    *,
    maximum: int,
    timeout: float,
) -> bytes:
    transfer_encoding = _header(headers, "transfer-encoding")
    if transfer_encoding is not None:
        if transfer_encoding.casefold() != "chunked":
            raise PublicUrlRejected(
                "invalid_response",
                "Public HTTP transfer encoding is unsupported",
            )
        return await _read_chunked_body(reader, maximum=maximum, timeout=timeout)
    content_length = _content_length(headers)
    if content_length is not None:
        if content_length > maximum:
            raise PublicUrlRejected(
                "response_too_large",
                "Public resource verification response exceeded 64 KiB",
            )
        try:
            return await asyncio.wait_for(reader.readexactly(content_length), timeout=timeout)
        except TimeoutError as error:
            raise PublicUrlRejected(
                "network_timeout",
                "Public resource response timed out",
                retryable=True,
            ) from error
    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(min(16_384, maximum + 1 - total)), timeout)
        except TimeoutError as error:
            raise PublicUrlRejected(
                "network_timeout",
                "Public resource response timed out",
                retryable=True,
            ) from error
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > maximum:
            raise PublicUrlRejected(
                "response_too_large",
                "Public resource verification response exceeded 64 KiB",
            )
        chunks.append(chunk)


async def _read_chunked_body(reader: Any, *, maximum: int, timeout: float) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        line = await _readline(reader, timeout=timeout)
        raw_size = line.split(b";", 1)[0].strip()
        try:
            size = int(raw_size, 16)
        except ValueError as error:
            raise PublicUrlRejected(
                "invalid_response", "Public HTTP chunk size is invalid"
            ) from error
        if size < 0 or size > maximum - total:
            raise PublicUrlRejected(
                "response_too_large",
                "Public resource verification response exceeded 64 KiB",
            )
        if size == 0:
            await _read_chunk_trailers(reader, timeout=timeout)
            return b"".join(chunks)
        try:
            chunk = await asyncio.wait_for(reader.readexactly(size), timeout=timeout)
            ending = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
        except TimeoutError as error:
            raise PublicUrlRejected(
                "network_timeout",
                "Public resource response timed out",
                retryable=True,
            ) from error
        if ending != b"\r\n":
            raise PublicUrlRejected("invalid_response", "Public HTTP chunk framing is invalid")
        chunks.append(chunk)
        total += size


async def _read_chunk_trailers(reader: Any, *, timeout: float) -> None:
    total = 0
    for _ in range(MAX_PUBLIC_HEADERS):
        line = await _readline(reader, timeout=timeout)
        total += len(line)
        if total > MAX_PUBLIC_HEADER_BYTES:
            raise PublicUrlRejected("invalid_response", "Public HTTP trailers are too large")
        if line in {b"\r\n", b"\n"}:
            return
    raise PublicUrlRejected("invalid_response", "Public HTTP response has too many trailers")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    identity = name.casefold()
    for raw_name, value in headers.items():
        if raw_name.casefold() == identity:
            normalized = value.strip()
            return normalized or None
    return None


def _content_length(headers: Mapping[str, str]) -> int | None:
    value = _header(headers, "content-length")
    if value is None:
        return None
    if "," in value or not value.isascii() or not value.isdecimal():
        raise PublicUrlRejected("invalid_response", "Public HTTP content length is invalid")
    size = int(value)
    if size < 0:
        raise PublicUrlRejected("invalid_response", "Public HTTP content length is invalid")
    return size


__all__ = [
    "MAX_PUBLIC_REDIRECTS",
    "MAX_PUBLIC_RESPONSE_BYTES",
    "MAX_PUBLIC_URL_CHARACTERS",
    "OutboundPublicRequest",
    "PinnedHttpsTransport",
    "PublicHttpResponse",
    "PublicHttpTransport",
    "PublicUrlPolicy",
    "PublicUrlRejected",
    "PublicUrlVerifier",
    "ValidatedPublicUrl",
    "VerifiedPublicUrl",
    "resolve_public_host",
]
