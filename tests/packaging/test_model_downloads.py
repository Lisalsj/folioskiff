from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest


class FixtureNetworkPolicy:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.responses: list[str] = []

    async def validate_request(self, url: str) -> object:
        self.requests.append(url)
        return {"fixture-address"}

    async def validate_response(
        self,
        response: httpx.Response,
        target: object,
    ) -> None:
        assert target == {"fixture-address"}
        self.responses.append(str(response.request.url))


class BrokenStream(httpx.AsyncByteStream):
    def __init__(self, first: bytes) -> None:
        self.first = first

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.first
        raise httpx.ReadError("fixture disconnected")

    async def aclose(self) -> None:
        return None


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


class BlockingAfterChunkStream(httpx.AsyncByteStream):
    def __init__(self, first: bytes) -> None:
        self.first = first
        self.waiting = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.first
        self.waiting.set()
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        return None


def tiny_manifest(payload: bytes, *, revision: str = "a" * 40) -> Any:
    from paper_reader.assets.manifest import (
        ModelFile,
        ModelLicense,
        ModelManifest,
    )

    return ModelManifest(
        capability="semantic",
        bundle_id="fixture-e5",
        display_name="Fixture E5",
        purpose="Local semantic search",
        version="fixture-v1",
        revision=revision,
        source_url="https://models.example.test/fixture/tree/" + revision,
        license=ModelLicense(
            name="MIT",
            url="https://models.example.test/license",
            copyright_notice="Fixture",
        ),
        files=(
            ModelFile(
                relative_path="onnx/model.onnx",
                url="https://models.example.test/fixture/model.onnx",
                size_bytes=len(payload),
                sha256=sha256(payload).hexdigest(),
                role=None,
            ),
        ),
        runtime={
            "model_id": "fixture/e5",
            "dimension": 3,
            "pooling": "mean",
            "normalized": True,
        },
    )


@pytest.mark.asyncio
async def test_download_resumes_only_a_verified_etag_bound_partial_and_publishes_atomically(
    tmp_path: Path,
) -> None:
    from paper_reader.assets.downloader import AssetDownloader, AssetDownloadError

    payload = b"verified-model-payload"
    manifest = tiny_manifest(payload)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                headers={"Content-Length": str(len(payload)), "ETag": '"fixture-v1"'},
                stream=BrokenStream(payload[:7]),
            )
        assert request.headers["Range"] == "bytes=7-"
        assert request.headers["If-Range"] == '"fixture-v1"'
        return httpx.Response(
            206,
            headers={
                "Content-Length": str(len(payload) - 7),
                "Content-Range": f"bytes 7-{len(payload) - 1}/{len(payload)}",
                "ETag": '"fixture-v1"',
            },
            content=payload[7:],
        )

    policy = FixtureNetworkPolicy()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = AssetDownloader(tmp_path, client=client, network_policy=policy)
        with pytest.raises(AssetDownloadError) as first:
            await downloader.download(manifest)
        assert first.value.code == "network"
        destination = manifest.file_path(tmp_path, manifest.files[0])
        partial = destination.with_name(destination.name + ".part")
        checkpoint = destination.with_name(destination.name + ".part.json")
        assert partial.read_bytes() == payload[:7]
        assert checkpoint.is_file()
        assert destination.exists() is False

        await downloader.download(manifest)

    assert destination.read_bytes() == payload
    assert partial.exists() is False
    assert checkpoint.exists() is False
    assert sha256(destination.read_bytes()).hexdigest() == manifest.files[0].sha256
    assert policy.requests == [manifest.files[0].url, manifest.files[0].url]
    assert len(policy.responses) == 2


@pytest.mark.asyncio
async def test_tampered_or_unbound_partial_is_discarded_instead_of_resumed(
    tmp_path: Path,
) -> None:
    from paper_reader.assets.downloader import AssetDownloader, AssetDownloadError

    payload = b"0123456789"
    manifest = tiny_manifest(payload)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                200,
                headers={"Content-Length": "10", "ETag": '"one"'},
                stream=BrokenStream(payload[:4]),
            )
        assert "Range" not in request.headers
        return httpx.Response(
            200,
            headers={"Content-Length": "10", "ETag": '"two"'},
            content=payload,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = AssetDownloader(
            tmp_path,
            client=client,
            network_policy=FixtureNetworkPolicy(),
        )
        with pytest.raises(AssetDownloadError):
            await downloader.download(manifest)
        destination = manifest.file_path(tmp_path, manifest.files[0])
        partial = destination.with_name(destination.name + ".part")
        partial.write_bytes(b"evil")
        await downloader.download(manifest)

    assert destination.read_bytes() == payload


@pytest.mark.asyncio
async def test_redirects_are_bounded_and_every_hop_revalidates_network_identity(
    tmp_path: Path,
) -> None:
    from paper_reader.assets.downloader import AssetDownloader

    payload = b"redirected"
    manifest = tiny_manifest(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "models.example.test":
            return httpx.Response(
                302,
                headers={"Location": "https://cdn.example.test/model.onnx"},
            )
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(payload)), "ETag": '"cdn"'},
            content=payload,
        )

    policy = FixtureNetworkPolicy()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await AssetDownloader(
            tmp_path,
            client=client,
            network_policy=policy,
        ).download(manifest)

    assert policy.requests == [
        "https://models.example.test/fixture/model.onnx",
        "https://cdn.example.test/model.onnx",
    ]
    assert policy.responses == policy.requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "code", "keeps_partial"),
    [
        (
            httpx.Response(503, headers={"Content-Length": "0"}),
            "network",
            False,
        ),
        (
            httpx.Response(
                206,
                headers={
                    "Content-Length": "5",
                    "Content-Range": "bytes 3-7/9",
                    "ETag": '"bad-range"',
                },
                content=b"45678",
            ),
            "invalid_range",
            False,
        ),
        (
            httpx.Response(
                200,
                headers={"Content-Length": "11", "ETag": '"long"'},
                content=b"01234567890",
            ),
            "size_mismatch",
            False,
        ),
    ],
)
async def test_download_failures_never_publish_incomplete_or_oversized_content(
    tmp_path: Path,
    response: httpx.Response,
    code: str,
    keeps_partial: bool,
) -> None:
    from paper_reader.assets.downloader import AssetDownloader, AssetDownloadError

    payload = b"0123456789"
    manifest = tiny_manifest(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = AssetDownloader(
            tmp_path,
            client=client,
            network_policy=FixtureNetworkPolicy(),
        )
        with pytest.raises(AssetDownloadError) as captured:
            await downloader.download(manifest)

    destination = manifest.file_path(tmp_path, manifest.files[0])
    assert captured.value.code == code
    assert destination.exists() is False
    partial = destination.with_name(destination.name + ".part")
    if keeps_partial:
        assert partial.is_file()
    else:
        assert partial.exists() is False


@pytest.mark.asyncio
async def test_pause_preserves_verified_bytes_resume_reuses_them_and_cancel_removes_them(
    tmp_path: Path,
) -> None:
    from paper_reader.assets.downloader import (
        AssetDownloadCancelled,
        AssetDownloader,
        AssetDownloadPaused,
    )

    payload = b"abcdefghij"
    manifest = tiny_manifest(payload)
    directive = "run"
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        offset = int(request.headers.get("Range", "bytes=0-")[6:-1])
        headers = {
            "Content-Length": str(len(payload) - offset),
            "ETag": '"pause-v1"',
        }
        status = 200
        if offset:
            status = 206
            headers["Content-Range"] = f"bytes {offset}-9/10"
        return httpx.Response(
            status,
            headers=headers,
            stream=ChunkStream(payload[offset : offset + 4], payload[offset + 4 :]),
        )

    def control() -> str:
        return directive

    def pause_after_first(received: int, total: int) -> None:
        nonlocal directive
        assert total == 10
        if received == 4:
            directive = "pause"

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = AssetDownloader(
            tmp_path,
            client=client,
            network_policy=FixtureNetworkPolicy(),
        )
        with pytest.raises(AssetDownloadPaused):
            await downloader.download(manifest, control=control, progress=pause_after_first)

        directive = "run"
        await downloader.download(manifest, control=control)
        assert calls[1].headers["Range"] == "bytes=4-"

        destination = manifest.file_path(tmp_path, manifest.files[0])
        destination.unlink()
        directive = "cancel"
        with pytest.raises(AssetDownloadCancelled):
            await downloader.download(manifest, control=control)

    assert destination.exists() is False
    assert destination.with_name(destination.name + ".part").exists() is False
    assert destination.with_name(destination.name + ".part.json").exists() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("directive", ["pause", "cancel"])
async def test_pause_and_cancel_interrupt_a_stalled_network_read_within_two_seconds(
    tmp_path: Path,
    directive: str,
) -> None:
    from paper_reader.assets.downloader import (
        AssetDownloadCancelled,
        AssetDownloader,
        AssetDownloadPaused,
    )

    payload = b"0123456789"
    manifest = tiny_manifest(payload)
    stream = BlockingAfterChunkStream(payload[:3])
    state = "run"

    def control() -> str:
        return state

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": "10", "ETag": '"stalled"'},
            stream=stream,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = AssetDownloader(
            tmp_path,
            client=client,
            network_policy=FixtureNetworkPolicy(),
        )
        task = asyncio.create_task(downloader.download(manifest, control=control))
        await asyncio.wait_for(stream.waiting.wait(), timeout=1)
        state = directive
        expected = AssetDownloadPaused if directive == "pause" else AssetDownloadCancelled
        with pytest.raises(expected):
            await asyncio.wait_for(task, timeout=2)

    destination = manifest.file_path(tmp_path, manifest.files[0])
    partial = destination.with_name(destination.name + ".part")
    checkpoint = destination.with_name(destination.name + ".part.json")
    if directive == "pause":
        assert partial.read_bytes() == payload[:3]
        assert checkpoint.is_file()
    else:
        assert partial.exists() is False
        assert checkpoint.exists() is False


@pytest.mark.asyncio
async def test_header_budget_and_untrusted_peer_fail_before_writing(tmp_path: Path) -> None:
    from paper_reader.assets.downloader import AssetDownloader, AssetDownloadError

    payload = b"small"
    manifest = tiny_manifest(payload)

    class RejectPeer(FixtureNetworkPolicy):
        async def validate_response(self, response: httpx.Response, target: object) -> None:
            raise AssetDownloadError("unsafe_peer", "unsafe peer", retryable=False)

    response = httpx.Response(
        200,
        headers={"Content-Length": "5", "X-Large": "x" * 70_000},
        content=payload,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(AssetDownloadError) as captured:
            await AssetDownloader(
                tmp_path,
                client=client,
                network_policy=RejectPeer(),
            ).download(manifest)

    assert captured.value.code == "unsafe_peer"
    assert manifest.file_path(tmp_path, manifest.files[0]).exists() is False


@pytest.mark.asyncio
async def test_response_header_and_body_hard_limits_are_enforced(tmp_path: Path) -> None:
    from paper_reader.assets.downloader import AssetDownloader, AssetDownloadError

    payload = b"small"
    manifest = tiny_manifest(payload)
    responses = iter(
        (
            httpx.Response(
                200,
                headers={"Content-Length": "5", "X-Large": "x" * 70_000},
                content=payload,
            ),
            httpx.Response(
                200,
                headers={"Content-Length": "5", "ETag": '"body"'},
                stream=ChunkStream(payload, b"overflow"),
            ),
        )
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: next(responses))
    ) as client:
        downloader = AssetDownloader(
            tmp_path,
            client=client,
            network_policy=FixtureNetworkPolicy(),
        )
        with pytest.raises(AssetDownloadError) as header_error:
            await downloader.download(manifest)
        assert header_error.value.code == "header_too_large"
        with pytest.raises(AssetDownloadError) as body_error:
            await downloader.download(manifest)
        assert body_error.value.code == "size_mismatch"

    destination = manifest.file_path(tmp_path, manifest.files[0])
    assert destination.exists() is False
    assert destination.with_name(destination.name + ".part").exists() is False


@pytest.mark.asyncio
async def test_disk_budget_is_checked_before_network_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil
    from types import SimpleNamespace

    from paper_reader.assets.downloader import AssetDownloader, AssetDownloadError

    manifest = tiny_manifest(b"asset")
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    monkeypatch.setattr(shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = AssetDownloader(
            tmp_path,
            client=client,
            network_policy=FixtureNetworkPolicy(),
        )
        with pytest.raises(AssetDownloadError) as captured:
            await downloader.download(manifest)

    assert captured.value.code == "disk_full"
    assert called is False


@pytest.mark.asyncio
async def test_existing_link_inside_the_model_root_cannot_alias_a_version_directory(
    tmp_path: Path,
) -> None:
    from paper_reader.assets.downloader import AssetDownloader, AssetDownloadError

    manifest = tiny_manifest(b"asset")
    version_parent = tmp_path / "fixture--e5"
    target = tmp_path / "other-version"
    version_parent.mkdir()
    target.mkdir()
    try:
        (version_parent / manifest.revision).symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory links are unavailable: {error}")

    downloader = AssetDownloader(tmp_path, network_policy=FixtureNetworkPolicy())
    try:
        with pytest.raises(AssetDownloadError) as captured:
            downloader.inspect(manifest)
    finally:
        await downloader.aclose()

    assert captured.value.code == "unsafe_path"


@pytest.mark.asyncio
async def test_saved_checkpoint_survives_short_read_and_5xx_then_truncates_crash_tail(
    tmp_path: Path,
) -> None:
    from paper_reader.assets.downloader import AssetDownloader, AssetDownloadError

    payload = b"0123456789"
    manifest = tiny_manifest(payload)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                200,
                headers={"Content-Length": "10", "ETag": '"stable"'},
                stream=ChunkStream(payload[:4]),
            )
        if len(calls) == 2:
            return httpx.Response(503, headers={"Content-Length": "0"})
        assert request.headers["Range"] == "bytes=4-"
        return httpx.Response(
            206,
            headers={
                "Content-Length": "6",
                "Content-Range": "bytes 4-9/10",
                "ETag": '"stable"',
            },
            content=payload[4:],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = AssetDownloader(
            tmp_path,
            client=client,
            network_policy=FixtureNetworkPolicy(),
        )
        with pytest.raises(AssetDownloadError, match="ended before"):
            await downloader.download(manifest)
        destination = manifest.file_path(tmp_path, manifest.files[0])
        partial = destination.with_name(destination.name + ".part")
        partial.write_bytes(partial.read_bytes() + b"uncheckpointed-crash-tail")
        with pytest.raises(AssetDownloadError) as unavailable:
            await downloader.download(manifest)
        assert unavailable.value.code == "network"
        assert partial.read_bytes() == payload[:4]
        await downloader.download(manifest)

    assert destination.read_bytes() == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_mode", ["etag-change", "ignored-range", "bad-range"])
async def test_resume_restarts_safely_when_remote_range_identity_is_untrustworthy(
    tmp_path: Path,
    resume_mode: str,
) -> None:
    from paper_reader.assets.downloader import AssetDownloader, AssetDownloadError

    payload = b"abcdefghij"
    manifest = tiny_manifest(payload)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                200,
                headers={"Content-Length": "10", "ETag": '"old"'},
                stream=BrokenStream(payload[:3]),
            )
        if len(calls) == 2 and resume_mode == "ignored-range":
            return httpx.Response(
                200,
                headers={"Content-Length": "10", "ETag": '"new"'},
                content=payload,
            )
        if len(calls) == 2:
            return httpx.Response(
                206,
                headers={
                    "Content-Length": "7",
                    "Content-Range": (
                        "bytes 2-8/10" if resume_mode == "bad-range" else "bytes 3-9/10"
                    ),
                    "ETag": '"new"' if resume_mode == "etag-change" else '"old"',
                },
                content=payload[3:],
            )
        assert "Range" not in request.headers
        return httpx.Response(
            200,
            headers={"Content-Length": "10", "ETag": '"new"'},
            content=payload,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = AssetDownloader(
            tmp_path,
            client=client,
            network_policy=FixtureNetworkPolicy(),
        )
        with pytest.raises(AssetDownloadError):
            await downloader.download(manifest)
        if resume_mode == "ignored-range":
            await downloader.download(manifest)
        else:
            with pytest.raises(AssetDownloadError) as unsafe:
                await downloader.download(manifest)
            assert unsafe.value.code in {"etag_changed", "invalid_range"}
            await downloader.download(manifest)

    assert manifest.file_path(tmp_path, manifest.files[0]).read_bytes() == payload


@pytest.mark.asyncio
async def test_default_transport_disables_environment_proxies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.assets.downloader import AssetDownloader

    captured: dict[str, Any] = {}

    class Client:
        async def aclose(self) -> None:
            captured["closed"] = True

    def create_client(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return Client()

    monkeypatch.setattr(httpx, "AsyncClient", create_client)
    downloader = AssetDownloader(tmp_path)
    await downloader.aclose()

    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False
    assert captured["closed"] is True


@pytest.mark.asyncio
async def test_public_network_policy_rejects_private_dns_and_peer_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    from paper_reader.assets.downloader import (
        AssetDownloadError,
        PublicHttpsNetworkPolicy,
    )

    policy = PublicHttpsNetworkPolicy()
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(AssetDownloadError) as private:
        await policy.validate_request("https://models.example.test/file")
    assert private.value.code == "unsafe_dns"

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    target = await policy.validate_request("https://models.example.test/file")

    class Stream:
        def get_extra_info(self, name: str) -> object:
            return ("1.1.1.1", 443) if name == "server_addr" else None

    response = httpx.Response(200, extensions={"network_stream": Stream()})
    with pytest.raises(AssetDownloadError) as rebound:
        await policy.validate_response(response, target)
    assert rebound.value.code == "unsafe_peer"
