from __future__ import annotations

import asyncio
import ssl
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from paper_reader.related_resources.web_policy import (
    MAX_PUBLIC_RESPONSE_BYTES,
    OutboundPublicRequest,
    PinnedHttpsTransport,
    PublicHttpResponse,
    PublicUrlPolicy,
    PublicUrlRejected,
    PublicUrlVerifier,
)

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


def public_dns(
    answers: Mapping[str, tuple[str, ...]] | None = None,
) -> Callable[[str, int], Awaitable[tuple[str, ...]]]:
    configured = dict(answers or {})

    async def resolve(host: str, port: int) -> tuple[str, ...]:
        assert port == 443
        return configured.get(host, (PUBLIC_V4,))

    return resolve


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/secret",
        "https://127.0.0.1/private",
        "https://localhost/admin",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pass@example.org/",
        "file:///C:/private.txt",
        "https://printer.local/status",
        "https://api.localhost/status",
        "https://intranet/path",
        "https://example.org:8443/path",
        "https://example.org/resource?access_token=do-not-store",
        "https://example.org/resource?X-Amz-Signature=do-not-store",
        "https://example.org/resource?public=yes;token=do-not-store",
        " https://example.org/path",
    ],
)
async def test_public_url_policy_rejects_local_or_credentialed_targets(url: str) -> None:
    with pytest.raises(PublicUrlRejected):
        await PublicUrlPolicy(public_dns()).validate(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answers",
    [
        (),
        ("127.0.0.1",),
        ("10.0.0.5",),
        ("169.254.169.254",),
        ("::1",),
        (PUBLIC_V4, "192.168.1.5"),
        ("not-an-address",),
    ],
)
async def test_public_url_policy_requires_every_dns_answer_to_be_public(
    answers: tuple[str, ...],
) -> None:
    policy = PublicUrlPolicy(public_dns({"example.org": answers}))

    with pytest.raises(PublicUrlRejected):
        await policy.validate("https://example.org/guide")


@pytest.mark.asyncio
async def test_public_url_policy_canonicalizes_host_port_and_fragment() -> None:
    validated = await PublicUrlPolicy(
        public_dns({"example.org": (PUBLIC_V6, PUBLIC_V4, PUBLIC_V4)})
    ).validate("https://EXAMPLE.org:443/Guide?q=one#section")

    assert validated.url == "https://example.org/Guide?q=one"
    assert validated.domain == "example.org"
    assert validated.resolved_addresses == (PUBLIC_V6, PUBLIC_V4)


@pytest.mark.asyncio
async def test_public_url_policy_redacts_unexpected_resolver_failures() -> None:
    async def broken_resolver(_host: str, _port: int) -> tuple[str, ...]:
        raise RuntimeError("resolver implementation detail")

    with pytest.raises(PublicUrlRejected, match="DNS resolution failed") as captured:
        await PublicUrlPolicy(broken_resolver).validate("https://example.org/guide")

    assert captured.value.code == "dns_failure"
    assert "implementation detail" not in str(captured.value)


@dataclass(slots=True)
class RecordingTransport:
    responses: list[PublicHttpResponse]
    requests: list[OutboundPublicRequest] = field(default_factory=list)

    async def send(self, request: OutboundPublicRequest) -> PublicHttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_verifier_revalidates_every_redirect_and_returns_final_identity() -> None:
    resolver_calls: list[str] = []

    async def resolver(host: str, port: int) -> tuple[str, ...]:
        assert port == 443
        resolver_calls.append(host)
        return {
            "docs.example.org": ("93.184.216.34",),
            "university.example.edu": ("142.250.72.14",),
        }[host]

    transport = RecordingTransport(
        responses=[
            PublicHttpResponse(
                status_code=302,
                headers={"location": "https://university.example.edu/course"},
                body=b"",
                peer_address="93.184.216.34",
            ),
            PublicHttpResponse(
                status_code=204,
                headers={},
                body=b"",
                peer_address="142.250.72.14",
            ),
        ]
    )
    verifier = PublicUrlVerifier(PublicUrlPolicy(resolver), transport=transport)

    verified = await verifier.verify("https://docs.example.org/start")

    assert resolver_calls == ["docs.example.org", "university.example.edu"]
    assert [request.method for request in transport.requests] == ["HEAD", "HEAD"]
    assert verified.canonical_url == "https://docs.example.org/start"
    assert verified.final_url == "https://university.example.edu/course"
    assert verified.domain == "university.example.edu"
    assert verified.http_status == 204


@pytest.mark.asyncio
async def test_verifier_rejects_dns_rebinding_peer_before_accepting_response() -> None:
    transport = RecordingTransport(
        responses=[
            PublicHttpResponse(
                status_code=200,
                headers={},
                body=b"",
                peer_address="127.0.0.1",
            )
        ]
    )
    verifier = PublicUrlVerifier(PublicUrlPolicy(public_dns()), transport=transport)

    with pytest.raises(PublicUrlRejected, match="peer"):
        await verifier.verify("https://example.org/resource")


@pytest.mark.asyncio
async def test_verifier_uses_range_limited_get_only_when_head_is_unsupported() -> None:
    transport = RecordingTransport(
        responses=[
            PublicHttpResponse(
                status_code=405,
                headers={},
                body=b"",
                peer_address=PUBLIC_V4,
            ),
            PublicHttpResponse(
                status_code=206,
                headers={"content-length": "8"},
                body=b"verified",
                peer_address=PUBLIC_V4,
            ),
        ]
    )
    verifier = PublicUrlVerifier(PublicUrlPolicy(public_dns()), transport=transport)

    verified = await verifier.verify("https://example.org/resource")

    assert verified.http_status == 206
    assert [request.method for request in transport.requests] == ["HEAD", "GET"]
    assert transport.requests[1].headers == {
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.8,*/*;q=0.1",
        "Range": f"bytes=0-{MAX_PUBLIC_RESPONSE_BYTES - 1}",
    }
    assert all(
        request.max_response_bytes == MAX_PUBLIC_RESPONSE_BYTES for request in transport.requests
    )


@pytest.mark.asyncio
async def test_verifier_rejects_private_redirect_without_sending_second_request() -> None:
    transport = RecordingTransport(
        responses=[
            PublicHttpResponse(
                status_code=302,
                headers={"Location": "https://metadata.local/secret"},
                body=b"",
                peer_address=PUBLIC_V4,
            )
        ]
    )
    verifier = PublicUrlVerifier(PublicUrlPolicy(public_dns()), transport=transport)

    with pytest.raises(PublicUrlRejected):
        await verifier.verify("https://example.org/start")

    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_verifier_rejects_more_than_five_redirects() -> None:
    transport = RecordingTransport(
        responses=[
            PublicHttpResponse(
                status_code=302,
                headers={"location": f"/hop-{index + 1}"},
                body=b"",
                peer_address=PUBLIC_V4,
            )
            for index in range(6)
        ]
    )
    verifier = PublicUrlVerifier(PublicUrlPolicy(public_dns()), transport=transport)

    with pytest.raises(PublicUrlRejected, match="redirect limit") as captured:
        await verifier.verify("https://example.org/start")

    assert captured.value.code == "redirect_limit"
    assert len(transport.requests) == 6


class FakeWriter:
    def __init__(self, peer: str) -> None:
        self.peer = peer
        self.buffer = bytearray()
        self.closed = False

    def write(self, value: bytes) -> None:
        self.buffer.extend(value)

    async def drain(self) -> None:
        return None

    def get_extra_info(self, name: str) -> Any:
        if name == "peername":
            return (self.peer, 443)
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


@pytest.mark.asyncio
async def test_https_transport_connects_to_validated_ip_with_original_tls_name() -> None:
    calls: list[tuple[str, int, str | None]] = []
    writer = FakeWriter(PUBLIC_V4)

    async def opener(
        host: str,
        port: int,
        *,
        ssl: object,
        server_hostname: str | None,
    ) -> tuple[asyncio.StreamReader, FakeWriter]:
        del ssl
        calls.append((host, port, server_hostname))
        reader = asyncio.StreamReader()
        reader.feed_data(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
        reader.feed_eof()
        return reader, writer

    transport = PinnedHttpsTransport(opener=opener)
    response = await transport.send(
        OutboundPublicRequest(
            url="https://example.org/guide",
            method="HEAD",
            headers={},
            resolved_addresses=(PUBLIC_V4,),
            max_response_bytes=MAX_PUBLIC_RESPONSE_BYTES,
        )
    )

    assert calls == [(PUBLIC_V4, 443, "example.org")]
    assert response.peer_address == PUBLIC_V4
    assert b"Host: example.org\r\n" in bytes(writer.buffer)
    assert writer.closed is True


@pytest.mark.asyncio
async def test_https_transport_rejects_declared_body_over_64_kib() -> None:
    writer = FakeWriter(PUBLIC_V4)

    async def opener(
        _host: str,
        _port: int,
        *,
        ssl: object,
        server_hostname: str | None,
    ) -> tuple[asyncio.StreamReader, FakeWriter]:
        del ssl, server_hostname
        reader = asyncio.StreamReader()
        reader.feed_data(
            b"HTTP/1.1 200 OK\r\n"
            + f"Content-Length: {MAX_PUBLIC_RESPONSE_BYTES + 1}\r\n\r\n".encode()
        )
        reader.feed_eof()
        return reader, writer

    transport = PinnedHttpsTransport(opener=opener)
    with pytest.raises(PublicUrlRejected, match="64 KiB") as captured:
        await transport.send(
            OutboundPublicRequest(
                url="https://example.org/guide",
                method="GET",
                headers={"Range": f"bytes=0-{MAX_PUBLIC_RESPONSE_BYTES - 1}"},
                resolved_addresses=(PUBLIC_V4,),
            )
        )

    assert captured.value.code == "response_too_large"


def test_https_transport_rejects_an_insecure_tls_context() -> None:
    insecure = ssl._create_unverified_context()

    with pytest.raises(ValueError, match="certificate"):
        PinnedHttpsTransport(ssl_context=insecure)


@pytest.mark.asyncio
async def test_https_transport_uses_one_deadline_for_all_resolved_addresses() -> None:
    opener_calls: list[str] = []
    monotonic_values = iter((0.0, 0.25, 1.25))

    async def unavailable_opener(
        host: str,
        _port: int,
        *,
        ssl: object,
        server_hostname: str | None,
    ) -> tuple[asyncio.StreamReader, FakeWriter]:
        del ssl, server_hostname
        opener_calls.append(host)
        raise OSError("fixture endpoint unavailable")

    transport = PinnedHttpsTransport(
        opener=unavailable_opener,
        timeout_seconds=1,
        monotonic=lambda: next(monotonic_values),
    )

    with pytest.raises(PublicUrlRejected) as captured:
        await transport.send(
            OutboundPublicRequest(
                url="https://example.org/guide",
                method="HEAD",
                resolved_addresses=(PUBLIC_V4, "142.250.72.14"),
            )
        )

    assert captured.value.code == "network_error"
    assert opener_calls == [PUBLIC_V4]
