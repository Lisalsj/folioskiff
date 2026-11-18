from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class PeerStream:
    def __init__(self, address: str) -> None:
        self.address = address

    def get_extra_info(self, key: str) -> Any:
        return (self.address, 443) if key == "server_addr" else None


async def public_resolver(_: str, __: int) -> Sequence[str]:
    return ("93.184.216.34",)


def test_acquisition_public_contract_is_available() -> None:
    from paper_reader.reader.acquisition import (
        AcquisitionReason,
        AcquisitionRequest,
        DocumentAcquisitionService,
        OpenPdfDownloader,
        PdfDownloadError,
        PublicUrlPolicy,
    )

    assert AcquisitionReason.OPEN.value == "open"
    assert AcquisitionReason.SAVE.value == "save"
    assert AcquisitionReason.ANALYZE.value == "analyze"
    assert AcquisitionRequest.__name__ == "AcquisitionRequest"
    assert DocumentAcquisitionService.__name__ == "DocumentAcquisitionService"
    assert OpenPdfDownloader.__name__ == "OpenPdfDownloader"
    assert PdfDownloadError.__name__ == "PdfDownloadError"
    assert PublicUrlPolicy.__name__ == "PublicUrlPolicy"


@pytest.mark.asyncio
async def test_default_resolver_deduplicates_socket_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.reader import acquisition

    class FakeLoop:
        async def getaddrinfo(
            self,
            host: str,
            port: int,
            *,
            family: int,
            type: int,
            proto: int,
        ):  # type: ignore[no-untyped-def]
            assert (host, port) == ("example.org", 443)
            assert family == acquisition.socket.AF_UNSPEC
            assert type == acquisition.socket.SOCK_STREAM
            assert proto == acquisition.socket.IPPROTO_TCP
            return (
                (family, type, proto, "", ("93.184.216.34", port)),
                (family, type, proto, "", ("93.184.216.34", port)),
                (family, type, proto, "", ("2606:4700:4700::1111", port, 0, 0)),
            )

    monkeypatch.setattr(acquisition.asyncio, "get_running_loop", lambda: FakeLoop())

    assert await acquisition.resolve_host_addresses("example.org", 443) == (
        "93.184.216.34",
        "2606:4700:4700::1111",
    )


@pytest.mark.asyncio
async def test_public_url_policy_normalizes_and_resolves_every_destination() -> None:
    from paper_reader.reader.acquisition import PublicUrlPolicy

    calls: list[tuple[str, int]] = []

    async def resolver(host: str, port: int) -> Sequence[str]:
        calls.append((host, port))
        return ("93.184.216.34", "2606:4700:4700::1111")

    safe = await PublicUrlPolicy(resolver=resolver).validate(
        "HTTPS://EXAMPLE.ORG:443/papers/reader.pdf#page=2"
    )

    assert safe.url == "https://example.org/papers/reader.pdf"
    assert safe.host == "example.org"
    assert safe.port == 443
    assert safe.addresses == ("93.184.216.34", "2606:4700:4700::1111")
    assert calls == [("example.org", 443)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    (
        (),
        ("127.0.0.1",),
        ("93.184.216.34", "10.0.0.2"),
        ("::1",),
    ),
)
async def test_public_url_policy_rejects_empty_private_or_mixed_dns_answers(
    addresses: tuple[str, ...],
) -> None:
    from paper_reader.reader.acquisition import PdfDownloadError, PublicUrlPolicy

    async def resolver(_: str, __: int) -> Sequence[str]:
        return addresses

    with pytest.raises(PdfDownloadError) as captured:
        await PublicUrlPolicy(resolver=resolver).validate(
            "https://example.org/paper.pdf"
        )

    assert captured.value.code == "unsafe_url"
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_public_url_policy_classifies_dns_failure_as_retryable() -> None:
    from paper_reader.reader.acquisition import PdfDownloadError, PublicUrlPolicy

    async def resolver(_: str, __: int) -> Sequence[str]:
        raise OSError("temporary DNS failure")

    with pytest.raises(PdfDownloadError) as captured:
        await PublicUrlPolicy(resolver=resolver).validate(
            "https://example.org/paper.pdf"
        )

    assert captured.value.code == "dns_failure"
    assert captured.value.retryable is True
    assert "temporary DNS failure" not in str(captured.value)


@pytest.mark.asyncio
async def test_public_url_policy_maps_unsafe_url_syntax_to_a_safe_error() -> None:
    from paper_reader.reader.acquisition import PdfDownloadError, PublicUrlPolicy

    async def resolver(_: str, __: int) -> Sequence[str]:
        raise AssertionError("Unsafe syntax must fail before DNS")

    with pytest.raises(PdfDownloadError) as captured:
        await PublicUrlPolicy(resolver=resolver).validate(
            "http://127.0.0.1/private.pdf"
        )

    assert captured.value.code == "unsafe_url"
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_public_url_policy_rejects_malformed_dns_answers() -> None:
    from paper_reader.reader.acquisition import PdfDownloadError, PublicUrlPolicy

    async def resolver(_: str, __: int) -> Sequence[str]:
        return ("not-an-ip-address",)

    with pytest.raises(PdfDownloadError) as captured:
        await PublicUrlPolicy(resolver=resolver).validate(
            "https://example.org/paper.pdf"
        )

    assert captured.value.code == "unsafe_url"


@pytest.mark.asyncio
async def test_downloader_validates_each_redirect_streams_and_reports_progress(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.acquisition import OpenPdfDownloader, PublicUrlPolicy
    from paper_reader.reader.storage import ContentStore

    requests: list[httpx.Request] = []
    stream = ChunkStream((PDF_BYTES[:12], PDF_BYTES[12:]))

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/start":
            return httpx.Response(
                302,
                headers={"Location": "/paper.pdf"},
                extensions={"network_stream": PeerStream("93.184.216.34")},
            )
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/pdf; charset=binary",
                "Content-Length": str(len(PDF_BYTES)),
            },
            stream=stream,
            extensions={"network_stream": PeerStream("93.184.216.34")},
        )

    progress: list[tuple[int, int | None]] = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        result = await OpenPdfDownloader(
            client=client,
            store=ContentStore(tmp_path / "library"),
            policy=PublicUrlPolicy(resolver=public_resolver),
            max_bytes=1024,
        ).download(
            "https://example.org/start",
            cancelled=lambda: False,
            progress=lambda received, total: progress.append((received, total)),
        )

    assert result.byte_size == len(PDF_BYTES)
    assert result.media_type == "application/pdf"
    assert len(requests) == 2
    assert requests[0].headers["Accept-Encoding"] == "identity"
    assert "application/pdf" in requests[0].headers["Accept"]
    assert progress[-1] == (len(PDF_BYTES), len(PDF_BYTES))
    assert stream.closed is True


@pytest.mark.asyncio
async def test_downloader_hashes_64k_chunks_and_reuses_the_atomic_object(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.acquisition import OpenPdfDownloader, PublicUrlPolicy
    from paper_reader.reader.storage import ContentStore

    payload = PDF_BYTES + b"x" * (2 * 64 * 1024)
    chunks = tuple(
        payload[offset : offset + 64 * 1024]
        for offset in range(0, len(payload), 64 * 1024)
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": str(len(payload)),
            },
            stream=ChunkStream(chunks),
            extensions={"network_stream": PeerStream("93.184.216.34")},
        )

    store = ContentStore(tmp_path / "library")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = OpenPdfDownloader(
            client=client,
            store=store,
            policy=PublicUrlPolicy(resolver=public_resolver),
            max_bytes=len(payload),
        )
        first = await downloader.download(
            "https://example.org/paper.pdf", cancelled=lambda: False
        )
        second = await downloader.download(
            "https://example.org/paper.pdf", cancelled=lambda: False
        )

    assert first == second
    assert first.content_sha256 == sha256(payload).hexdigest()
    assert store.resolve(first.relative_path).read_bytes() == payload
    assert len(tuple((tmp_path / "library").rglob("*.pdf"))) == 1
    assert not tuple((tmp_path / "library").rglob("*.part"))


@pytest.mark.asyncio
async def test_downloader_default_limit_rejects_more_than_100_mib_before_streaming(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.acquisition import (
        OpenPdfDownloader,
        PdfDownloadError,
        PublicUrlPolicy,
    )
    from paper_reader.reader.storage import ContentStore

    stream = ChunkStream((PDF_BYTES,))

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": str(100 * 1024 * 1024 + 1),
            },
            stream=stream,
            extensions={"network_stream": PeerStream("93.184.216.34")},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PdfDownloadError) as captured:
            await OpenPdfDownloader(
                client=client,
                store=ContentStore(tmp_path / "library"),
                policy=PublicUrlPolicy(resolver=public_resolver),
            ).download("https://example.org/paper.pdf", cancelled=lambda: False)

    assert captured.value.code == "too_large"
    assert stream.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "chunks", "expected_code", "retryable"),
    (
        (
            {"Content-Type": "application/pdf", "Content-Length": "2048"},
            (PDF_BYTES,),
            "too_large",
            False,
        ),
        (
            {"Content-Type": "application/pdf"},
            (PDF_BYTES, b"x" * 128),
            "too_large",
            False,
        ),
        (
            {"Content-Type": "text/html"},
            (b"<html>not a paper</html>",),
            "not_pdf",
            False,
        ),
        (
            {"Content-Type": "application/pdf"},
            (b"<html>wrong magic</html>",),
            "not_pdf",
            False,
        ),
        (
            {"Content-Type": "application/pdf"},
            (),
            "not_pdf",
            False,
        ),
        (
            {"Content-Type": "application/pdf", "Content-Length": "60"},
            (PDF_BYTES,),
            "truncated",
            True,
        ),
    ),
)
async def test_downloader_rejects_size_type_magic_empty_and_truncated_responses(
    tmp_path: Path,
    headers: dict[str, str],
    chunks: tuple[bytes, ...],
    expected_code: str,
    retryable: bool,
) -> None:
    from paper_reader.reader.acquisition import (
        OpenPdfDownloader,
        PdfDownloadError,
        PublicUrlPolicy,
    )
    from paper_reader.reader.storage import ContentStore

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers=headers,
            stream=ChunkStream(chunks),
            extensions={"network_stream": PeerStream("93.184.216.34")},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = OpenPdfDownloader(
            client=client,
            store=ContentStore(tmp_path / "library"),
            policy=PublicUrlPolicy(resolver=public_resolver),
            max_bytes=64,
        )
        with pytest.raises(PdfDownloadError) as captured:
            await downloader.download(
                "https://example.org/paper.pdf",
                cancelled=lambda: False,
            )

    assert captured.value.code == expected_code
    assert captured.value.retryable is retryable
    assert not tuple((tmp_path / "library").rglob("*.pdf"))
    assert not tuple((tmp_path / "library").rglob("*.part"))


@pytest.mark.asyncio
async def test_downloader_revalidates_redirect_and_limits_redirect_count(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.acquisition import (
        OpenPdfDownloader,
        PdfDownloadError,
        PublicUrlPolicy,
    )
    from paper_reader.reader.storage import ContentStore

    request_count = 0

    async def private_redirect(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            302,
            headers={"Location": "http://127.0.0.1/private.pdf"},
            extensions={"network_stream": PeerStream("93.184.216.34")},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(private_redirect)
    ) as client:
        downloader = OpenPdfDownloader(
            client=client,
            store=ContentStore(tmp_path / "private-library"),
            policy=PublicUrlPolicy(resolver=public_resolver),
        )
        with pytest.raises(PdfDownloadError) as unsafe:
            await downloader.download(
                "https://example.org/start",
                cancelled=lambda: False,
            )
    assert unsafe.value.code == "unsafe_url"
    assert request_count == 1

    async def redirect_loop(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "/again"},
            extensions={"network_stream": PeerStream("93.184.216.34")},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect_loop)) as client:
        downloader = OpenPdfDownloader(
            client=client,
            store=ContentStore(tmp_path / "loop-library"),
            policy=PublicUrlPolicy(resolver=public_resolver),
            max_redirects=2,
        )
        with pytest.raises(PdfDownloadError) as looped:
            await downloader.download(
                "https://example.org/start",
                cancelled=lambda: False,
            )
    assert looped.value.code == "redirect"
    assert looped.value.retryable is False


@pytest.mark.asyncio
async def test_downloader_rejects_private_connected_peer_and_network_timeout(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.acquisition import (
        OpenPdfDownloader,
        PdfDownloadError,
        PublicUrlPolicy,
    )
    from paper_reader.reader.storage import ContentStore

    async def private_peer(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=PDF_BYTES,
            extensions={"network_stream": PeerStream("10.0.0.9")},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(private_peer)) as client:
        downloader = OpenPdfDownloader(
            client=client,
            store=ContentStore(tmp_path / "peer-library"),
            policy=PublicUrlPolicy(resolver=public_resolver),
        )
        with pytest.raises(PdfDownloadError) as unsafe:
            await downloader.download(
                "https://example.org/paper.pdf",
                cancelled=lambda: False,
            )
    assert unsafe.value.code == "unsafe_url"

    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        downloader = OpenPdfDownloader(
            client=client,
            store=ContentStore(tmp_path / "timeout-library"),
            policy=PublicUrlPolicy(resolver=public_resolver),
        )
        with pytest.raises(PdfDownloadError) as failed:
            await downloader.download(
                "https://example.org/paper.pdf",
                cancelled=lambda: False,
            )
    assert failed.value.code == "network"
    assert failed.value.retryable is True
    assert "timed out" not in str(failed.value)


@pytest.mark.asyncio
async def test_downloader_fails_closed_when_connected_peer_cannot_be_verified(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.acquisition import (
        OpenPdfDownloader,
        PdfDownloadError,
        PublicUrlPolicy,
    )
    from paper_reader.reader.storage import ContentStore

    async def no_peer(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            stream=ChunkStream((PDF_BYTES,)),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_peer)) as client:
        with pytest.raises(PdfDownloadError) as captured:
            await OpenPdfDownloader(
                client=client,
                store=ContentStore(tmp_path / "library"),
                policy=PublicUrlPolicy(resolver=public_resolver),
            ).download("https://example.org/paper.pdf", cancelled=lambda: False)

    assert captured.value.code == "unsafe_url"
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_downloader_rejects_invalid_configuration_headers_status_and_peers(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.acquisition import (
        OpenPdfDownloader,
        PdfDownloadError,
        PublicUrlPolicy,
        SafeUrl,
    )
    from paper_reader.reader.storage import ContentStore

    async with httpx.AsyncClient() as client:
        kwargs = {
            "client": client,
            "store": ContentStore(tmp_path / "library"),
            "policy": PublicUrlPolicy(resolver=public_resolver),
        }
        with pytest.raises(ValueError, match="positive"):
            OpenPdfDownloader(**kwargs, max_bytes=0)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="negative"):
            OpenPdfDownloader(**kwargs, max_redirects=-1)  # type: ignore[arg-type]

        downloader = OpenPdfDownloader(**kwargs)  # type: ignore[arg-type]
        assert downloader._validate_headers(  # noqa: SLF001
            httpx.Headers({"Content-Type": "application/octet-stream"})
        ) is None
        for headers in (
            {"Content-Type": "application/pdf", "Content-Encoding": "gzip"},
            {"Content-Type": "application/pdf", "Content-Length": "invalid"},
            {"Content-Type": "application/pdf", "Content-Length": "-1"},
        ):
            with pytest.raises(PdfDownloadError) as invalid_header:
                downloader._validate_headers(httpx.Headers(headers))  # noqa: SLF001
            assert invalid_header.value.code == "network"
        with pytest.raises(PdfDownloadError) as not_found:
            downloader._validate_status(404)  # noqa: SLF001
        assert not_found.value.retryable is False
        with pytest.raises(PdfDownloadError) as unavailable:
            downloader._validate_status(503)  # noqa: SLF001
        assert unavailable.value.retryable is True

        safe = SafeUrl(
            url="https://example.org/paper.pdf",
            host="example.org",
            port=443,
            addresses=("93.184.216.34",),
        )

        class PeerInfo:
            def __init__(self, value: object) -> None:
                self.value = value

            def get_extra_info(self, _: str) -> object:
                return self.value

        for peer in (
            object(),
            PeerInfo(None),
            PeerInfo("not-a-tuple"),
            PeerStream("not-an-ip-address"),
        ):
            response = httpx.Response(200, extensions={"network_stream": peer})
            with pytest.raises(PdfDownloadError) as invalid_peer:
                downloader._validate_connected_peer(response, safe)  # noqa: SLF001
            assert invalid_peer.value.code == "unsafe_url"


@pytest.mark.asyncio
async def test_downloader_rejects_redirect_without_location(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.acquisition import (
        OpenPdfDownloader,
        PdfDownloadError,
        PublicUrlPolicy,
    )
    from paper_reader.reader.storage import ContentStore

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            extensions={"network_stream": PeerStream("93.184.216.34")},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PdfDownloadError) as captured:
            await OpenPdfDownloader(
                client=client,
                store=ContentStore(tmp_path / "library"),
                policy=PublicUrlPolicy(resolver=public_resolver),
            ).download("https://example.org/paper.pdf", cancelled=lambda: False)

    assert captured.value.code == "redirect"


@pytest.mark.asyncio
async def test_downloader_cancels_before_request_and_between_chunks_without_artifacts(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.acquisition import (
        OpenPdfDownloader,
        PdfDownloadError,
        PublicUrlPolicy,
    )
    from paper_reader.reader.storage import ContentStore

    requests = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            stream=ChunkStream((PDF_BYTES[:12], PDF_BYTES[12:])),
            extensions={"network_stream": PeerStream("93.184.216.34")},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = OpenPdfDownloader(
            client=client,
            store=ContentStore(tmp_path / "before-library"),
            policy=PublicUrlPolicy(resolver=public_resolver),
        )
        with pytest.raises(PdfDownloadError) as before:
            await downloader.download(
                "https://example.org/paper.pdf",
                cancelled=lambda: True,
            )
    assert before.value.code == "cancelled"
    assert requests == 0

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        library = tmp_path / "between-library"
        downloader = OpenPdfDownloader(
            client=client,
            store=ContentStore(library),
            policy=PublicUrlPolicy(resolver=public_resolver),
        )
        with pytest.raises(PdfDownloadError) as between:
            await downloader.download(
                "https://example.org/paper.pdf",
                cancelled=cancelled,
            )
    assert between.value.code == "cancelled"
    assert not tuple(library.rglob("*.pdf"))
    assert not tuple(library.rglob("*.part"))

    commit_checks = 0

    def cancel_before_commit() -> bool:
        nonlocal commit_checks
        commit_checks += 1
        return commit_checks >= 3

    async def one_chunk(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            stream=ChunkStream((PDF_BYTES,)),
            extensions={"network_stream": PeerStream("93.184.216.34")},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(one_chunk)) as client:
        commit_library = tmp_path / "commit-library"
        downloader = OpenPdfDownloader(
            client=client,
            store=ContentStore(commit_library),
            policy=PublicUrlPolicy(resolver=public_resolver),
        )
        with pytest.raises(PdfDownloadError) as at_commit:
            await downloader.download(
                "https://example.org/paper.pdf",
                cancelled=cancel_before_commit,
            )
    assert at_commit.value.code == "cancelled"
    assert not tuple(commit_library.rglob("*.pdf"))
    assert not tuple(commit_library.rglob("*.part"))


class FakePdfDownloader:
    def __init__(self, store, jobs) -> None:  # type: ignore[no-untyped-def]
        self.store = store
        self.jobs = jobs
        self.calls: list[str] = []
        self.failure = None
        self.cancel_job_id: str | None = None
        self.cancel_after_download_job_id: str | None = None

    async def download(self, url, *, cancelled, progress):  # type: ignore[no-untyped-def]
        from paper_reader.reader.acquisition import PdfDownloadError

        self.calls.append(url)
        if self.failure is not None:
            raise self.failure
        progress(12, len(PDF_BYTES))
        if self.cancel_job_id is not None:
            self.jobs.request_cancel(self.cancel_job_id)
        if cancelled():
            raise PdfDownloadError(
                "cancelled", "The document download was cancelled", retryable=False
            )
        progress(len(PDF_BYTES), len(PDF_BYTES))
        stored = self.store.ingest(
            (PDF_BYTES,),
            media_type="application/pdf",
            max_bytes=1024,
        )
        if self.cancel_after_download_job_id is not None:
            self.jobs.request_cancel(self.cancel_after_download_job_id)
        return stored


@pytest.fixture
def acquisition_service_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.acquisition import DocumentAcquisitionService
    from paper_reader.reader.storage import ContentStore

    engine = create_sqlite_engine(tmp_path / "acquisition.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    ingested = IdentityRepository(sessions).ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.12345",
            title="A Durable Paper Reader",
            authors=("Alice",),
            updated_at=datetime(2026, 7, 13, tzinfo=UTC),
            landing_url="https://example.org/paper",
        )
    )
    jobs = JobRepository(sessions)
    store = ContentStore(tmp_path / "library")
    downloader = FakePdfDownloader(store, jobs)
    service = DocumentAcquisitionService(
        sessions=sessions,
        jobs=jobs,
        downloader=downloader,
        clock=lambda: datetime(2026, 7, 13, 8, 30, tzinfo=UTC),
    )
    try:
        yield service, jobs, downloader, sessions, ingested.work, ingested.source_record
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_acquisition_job_is_idempotent_and_persists_one_document(
    acquisition_service_context,
) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.acquisition import AcquisitionReason, AcquisitionRequest
    from paper_reader.reader.models import LibraryEntryRecord

    service, jobs, downloader, sessions, work, source = acquisition_service_context
    request = AcquisitionRequest(
        work_id=work.id,
        source_record_id=source.id,
        url="HTTPS://EXAMPLE.ORG:443/paper.pdf#page=2",
        access_status="open",
        reason=AcquisitionReason.SAVE,
    )
    created = service.create_job(request, idempotency_key="download-paper")
    duplicate = service.create_job(request, idempotency_key="download-paper")

    assert duplicate.id == created.id
    assert created.payload == {
        "work_id": work.id,
        "source_record_id": source.id,
        "url": "https://example.org/paper.pdf",
        "access_status": "open",
        "reason": "save",
    }
    assert service.resumable_job_ids() == (created.id,)

    await service.execute(created.id)
    finished = jobs.get(created.id)

    assert finished is not None
    assert finished.status == "succeeded"
    assert finished.progress == 100
    assert finished.error_code is None
    assert finished.result is not None
    assert finished.result["work_id"] == work.id
    assert finished.result["content_sha256"] == finished.checkpoint["content_sha256"]
    assert downloader.calls == ["https://example.org/paper.pdf"]
    assert service.resumable_job_ids() == ()
    with sessions() as session:
        documents = list(session.scalars(select(DocumentVersionRecord)))
        library_entry = session.get(LibraryEntryRecord, work.id)
    assert len(documents) == 1
    assert documents[0].source_record_id == source.id
    assert documents[0].access_status == "open"
    assert documents[0].acquisition_kind == "download"
    assert documents[0].acquisition_url == "https://example.org/paper.pdf"
    assert library_entry is not None
    assert library_entry.added_via == "save"

    events = [event for event in jobs.list_events() if event.job_id == created.id]
    assert [event.event_type for event in events] == [
        "reader.acquisition.started",
        "reader.acquisition.progress",
        "reader.acquisition.progress",
        "reader.acquisition.succeeded",
    ]
    assert events[-1].progress == 100

    await service.execute(created.id)
    assert downloader.calls == ["https://example.org/paper.pdf"]


@pytest.mark.asyncio
async def test_acquisition_reuses_a_completed_hash_across_distinct_jobs(
    acquisition_service_context,
) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import func, select

    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.acquisition import AcquisitionReason, AcquisitionRequest

    service, jobs, downloader, sessions, work, source = acquisition_service_context
    first_request = AcquisitionRequest(
        work_id=work.id,
        source_record_id=None,
        url="https://example.org/paper.pdf",
        access_status="open",
        reason=AcquisitionReason.OPEN,
    )
    second_request = AcquisitionRequest(
        work_id=work.id,
        source_record_id=source.id,
        url="https://example.org/paper.pdf",
        access_status="open",
        reason=AcquisitionReason.OPEN,
    )
    first = service.create_job(first_request, idempotency_key="open-first")
    second = service.create_job(second_request, idempotency_key="open-second")

    await service.execute(first.id)
    await service.execute(second.id)

    assert jobs.get(second.id).result["document_version_id"] == jobs.get(first.id).result[
        "document_version_id"
    ]
    assert len(downloader.calls) == 2
    with sessions() as session:
        count = session.scalar(select(func.count()).select_from(DocumentVersionRecord))
        document = session.scalar(select(DocumentVersionRecord))
    assert count == 1
    assert document is not None
    assert document.source_record_id == source.id


@pytest.mark.asyncio
async def test_acquisition_cancellation_is_terminal_and_commits_no_document(
    acquisition_service_context,
) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import func, select

    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.acquisition import AcquisitionReason, AcquisitionRequest

    service, jobs, downloader, sessions, work, source = acquisition_service_context
    request = AcquisitionRequest(
        work_id=work.id,
        source_record_id=source.id,
        url="https://example.org/paper.pdf",
        access_status="open",
        reason=AcquisitionReason.ANALYZE,
    )
    before = service.create_job(request, idempotency_key="cancel-before")
    jobs.request_cancel(before.id)
    await service.execute(before.id)
    assert jobs.get(before.id).status == "cancelled"
    assert downloader.calls == []

    during = service.create_job(request, idempotency_key="cancel-during")
    downloader.cancel_job_id = during.id
    await service.execute(during.id)
    cancelled = jobs.get(during.id)

    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.error_code == "cancelled"
    events = [event for event in jobs.list_events() if event.job_id == during.id]
    assert events[-1].event_type == "reader.acquisition.cancelled"
    with sessions() as session:
        count = session.scalar(select(func.count()).select_from(DocumentVersionRecord))
    assert count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "retryable"),
    (
        ("unsafe_url", False),
        ("too_large", False),
        ("not_pdf", False),
        ("network", True),
    ),
)
async def test_acquisition_job_preserves_safe_error_categories(
    acquisition_service_context,
    code: str,
    retryable: bool,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.reader.acquisition import (
        AcquisitionReason,
        AcquisitionRequest,
        PdfDownloadError,
    )

    service, jobs, downloader, _, work, source = acquisition_service_context
    downloader.failure = PdfDownloadError(code, "safe public message", retryable=retryable)
    created = service.create_job(
        AcquisitionRequest(
            work_id=work.id,
            source_record_id=source.id,
            url="https://example.org/paper.pdf",
            access_status="open",
            reason=AcquisitionReason.SAVE,
        ),
        idempotency_key=f"failure-{code}",
    )

    await service.execute(created.id)
    failed = jobs.get(created.id)

    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_code == code
    assert failed.error_message == "safe public message"
    assert failed.result == {"retryable": retryable}
    assert failed.checkpoint == {"received_bytes": 0, "total_bytes": None}
    events = [event for event in jobs.list_events() if event.job_id == created.id]
    assert events[-1].event_type == "reader.acquisition.failed"
    assert events[-1].payload == {"code": code, "retryable": retryable}


def test_acquisition_rejects_invalid_work_source_access_and_idempotency_collision(
    acquisition_service_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.reader.acquisition import AcquisitionReason, AcquisitionRequest

    service, jobs, _, _, work, source = acquisition_service_context
    valid = AcquisitionRequest(
        work_id=work.id,
        source_record_id=source.id,
        url="https://example.org/paper.pdf",
        access_status="open",
        reason=AcquisitionReason.SAVE,
    )
    service.create_job(valid, idempotency_key="collision")
    jobs.create(kind="another.command", payload={}, idempotency_key="other-collision")

    with pytest.raises(KeyError):
        service.create_job(
            AcquisitionRequest(
                work_id="missing-work",
                source_record_id=None,
                url=valid.url,
                access_status="open",
                reason=AcquisitionReason.SAVE,
            ),
            idempotency_key="missing",
        )
    with pytest.raises(ValueError, match="source record"):
        service.create_job(
            AcquisitionRequest(
                work_id=work.id,
                source_record_id="missing-source",
                url=valid.url,
                access_status="open",
                reason=AcquisitionReason.SAVE,
            ),
            idempotency_key="source",
        )
    with pytest.raises(ValueError, match="open access"):
        service.create_job(
            AcquisitionRequest(
                work_id=work.id,
                source_record_id=source.id,
                url=valid.url,
                access_status="restricted",  # type: ignore[arg-type]
                reason=AcquisitionReason.SAVE,
            ),
            idempotency_key="restricted",
        )
    with pytest.raises(ValueError, match="idempotency"):
        service.create_job(valid, idempotency_key="other-collision")


@pytest.mark.asyncio
async def test_acquisition_running_job_is_resumable_and_corrupt_jobs_fail_closed(
    acquisition_service_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.reader.acquisition import (
        ACQUISITION_JOB_KIND,
        AcquisitionReason,
        AcquisitionRequest,
    )

    service, jobs, _, _, work, source = acquisition_service_context
    created = service.create_job(
        AcquisitionRequest(
            work_id=work.id,
            source_record_id=source.id,
            url="https://example.org/paper.pdf",
            access_status="open",
            reason=AcquisitionReason.OPEN,
        ),
        idempotency_key="resume-me",
    )
    jobs.update_state(created.id, status="running", progress=17)
    assert service.resumable_job_ids() == (created.id,)
    await service.execute(created.id)
    assert jobs.get(created.id).status == "succeeded"

    wrong_kind = jobs.create(kind="another.command", payload={})
    with pytest.raises(ValueError, match="not a document acquisition"):
        await service.execute(wrong_kind.id)
    with pytest.raises(KeyError):
        await service.execute("missing-job")
    corrupt = jobs.create(kind=ACQUISITION_JOB_KIND, payload={"work_id": 1})
    with pytest.raises(ValueError, match="payload"):
        await service.execute(corrupt.id)


@pytest.mark.asyncio
async def test_acquisition_handles_late_cancellation_and_unexpected_failure_safely(
    acquisition_service_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.reader.acquisition import AcquisitionReason, AcquisitionRequest

    service, jobs, downloader, _, work, source = acquisition_service_context
    request = AcquisitionRequest(
        work_id=work.id,
        source_record_id=source.id,
        url="https://example.org/paper.pdf",
        access_status="open",
        reason=AcquisitionReason.OPEN,
    )
    unexpected = service.create_job(request, idempotency_key="unexpected")
    downloader.failure = RuntimeError("private-token-must-not-leak")
    await service.execute(unexpected.id)
    failed = jobs.get(unexpected.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_code == "internal_error"
    assert "private-token" not in str(failed.result) + str(failed.error_message)

    downloader.failure = None
    late = service.create_job(request, idempotency_key="late-cancel")
    downloader.cancel_after_download_job_id = late.id
    await service.execute(late.id)
    cancelled = jobs.get(late.id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"


def test_acquisition_validates_command_helpers_and_corrupt_payload_variants(
    acquisition_service_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.reader.acquisition import (
        AcquisitionReason,
        AcquisitionRequest,
        DocumentAcquisitionService,
    )

    service, _, _, _, work, source = acquisition_service_context
    valid = AcquisitionRequest(
        work_id=work.id,
        source_record_id=source.id,
        url="https://example.org/paper.pdf",
        access_status="open",
        reason=AcquisitionReason.SAVE,
    )
    for key in ("", "x" * 161):
        with pytest.raises(ValueError, match="idempotency"):
            service.create_job(valid, idempotency_key=key)
    with pytest.raises(ValueError, match="reason"):
        service.create_job(
            AcquisitionRequest(
                work_id=work.id,
                source_record_id=source.id,
                url=valid.url,
                access_status="open",
                reason="unsupported",  # type: ignore[arg-type]
            ),
            idempotency_key="invalid-reason",
        )

    for payload in (
        {
            "work_id": work.id,
            "source_record_id": 42,
            "url": valid.url,
            "access_status": "open",
            "reason": "save",
        },
        {
            "work_id": work.id,
            "source_record_id": None,
            "url": valid.url,
            "access_status": "restricted",
            "reason": "save",
        },
        {
            "work_id": work.id,
            "source_record_id": None,
            "url": valid.url,
            "access_status": "open",
            "reason": "unsupported",
        },
    ):
        with pytest.raises(ValueError, match="payload"):
            service._read_payload(payload)  # noqa: SLF001

    assert DocumentAcquisitionService._download_progress(0, None) == 0
    assert DocumentAcquisitionService._download_progress(1, None) == 5
    assert DocumentAcquisitionService._download_filename("https://example.org/") == (
        "paper.pdf"
    )
    assert DocumentAcquisitionService._download_filename(
        f"https://example.org/{'x' * 256}"
    ) == "paper.pdf"
    with pytest.raises(ValueError, match="timezone-aware"):
        DocumentAcquisitionService._aware_utc(datetime(2026, 7, 13))
