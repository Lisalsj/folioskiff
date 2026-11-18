"""Resumable, integrity-pinned downloads for optional runtime assets."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import shutil
import socket
import stat
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import urljoin, urlsplit

import httpx

from paper_reader.assets.manifest import ModelFile, ModelManifest

DownloadDirective = Literal["run", "pause", "cancel"]
DownloadControl = Callable[[], DownloadDirective]
DownloadProgress = Callable[[int, int], None]

_HEADER_BUDGET = 64 * 1024
_CHECKPOINT_BUDGET = 16 * 1024
_CHUNK_SIZE = 256 * 1024
_CHECKPOINT_INTERVAL = 8 * 1024 * 1024
_CONTROL_POLL_SECONDS = 0.2
_MAX_REDIRECTS = 5
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class AssetDownloadError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class AssetDownloadPaused(AssetDownloadError):
    def __init__(self) -> None:
        super().__init__("paused", "The model download was paused", retryable=True)


class AssetDownloadCancelled(AssetDownloadError):
    def __init__(self) -> None:
        super().__init__("cancelled", "The model download was cancelled", retryable=False)


class NetworkPolicy(Protocol):
    async def validate_request(self, url: str) -> object: ...

    async def validate_response(self, response: httpx.Response, target: object) -> None: ...


class DigestWriter(Protocol):
    def update(self, value: bytes) -> None: ...


class PublicHttpsNetworkPolicy:
    """Reject proxies, private DNS answers, rebinding, and non-HTTPS peers."""

    async def validate_request(self, url: str) -> object:
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError as error:
            raise AssetDownloadError(
                "unsafe_url",
                "The model source is not a safe HTTPS URL",
                retryable=False,
            ) from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port not in {None, 443}
        ):
            raise AssetDownloadError(
                "unsafe_url",
                "The model source is not a safe HTTPS URL",
                retryable=False,
            )
        try:
            answers = await asyncio.to_thread(
                socket.getaddrinfo,
                parsed.hostname,
                443,
                socket.AF_UNSPEC,
                socket.SOCK_STREAM,
            )
        except OSError as error:
            raise AssetDownloadError(
                "network",
                "The model source could not be resolved",
                retryable=True,
            ) from error
        addresses = frozenset(_normalize_ip(str(item[4][0])) for item in answers)
        if not addresses or any(not _public_ip(value) for value in addresses):
            raise AssetDownloadError(
                "unsafe_dns",
                "The model source resolved to a non-public address",
                retryable=False,
            )
        return addresses

    async def validate_response(self, response: httpx.Response, target: object) -> None:
        expected = cast(frozenset[str], target)
        stream = response.extensions.get("network_stream")
        peer: object | None = None
        if stream is not None and hasattr(stream, "get_extra_info"):
            peer = stream.get_extra_info("server_addr")
            if peer is None:
                sock = stream.get_extra_info("socket")
                if sock is not None and hasattr(sock, "getpeername"):
                    peer = sock.getpeername()
        address = peer[0] if isinstance(peer, tuple) and peer else peer
        if not isinstance(address, str):
            raise AssetDownloadError(
                "unsafe_peer",
                "The model source peer identity could not be verified",
                retryable=True,
            )
        normalized = _normalize_ip(address)
        if normalized not in expected or not _public_ip(normalized):
            raise AssetDownloadError(
                "unsafe_peer",
                "The model source peer did not match its validated DNS identity",
                retryable=False,
            )


class AssetDownloader:
    """Download complete immutable revisions without ever exposing partial files."""

    def __init__(
        self,
        models_root: Path,
        *,
        client: httpx.AsyncClient | None = None,
        network_policy: NetworkPolicy | None = None,
    ) -> None:
        self._root = Path(models_root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._client = client or httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(30.0, connect=15.0),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
        )
        self._owns_client = client is None
        self._network = network_policy or PublicHttpsNetworkPolicy()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def download(
        self,
        manifest: ModelManifest,
        *,
        control: DownloadControl = lambda: "run",
        progress: DownloadProgress = lambda _received, _total: None,
    ) -> None:
        self._safe_version_root(manifest).mkdir(parents=True, exist_ok=True)
        completed = 0
        for asset in manifest.files:
            directive = _directive(control)
            if directive == "pause":
                raise AssetDownloadPaused()
            if directive == "cancel":
                self.cleanup_partial(manifest)
                raise AssetDownloadCancelled()
            destination = self._safe_destination(manifest, asset)
            if _verified(destination, asset):
                completed += asset.size_bytes
                _progress(progress, completed, manifest.total_size_bytes)
                continue
            if destination.exists():
                destination.unlink()
            await self._download_file(
                manifest,
                asset,
                destination,
                completed_before=completed,
                total=manifest.total_size_bytes,
                control=control,
                progress=progress,
            )
            completed += asset.size_bytes
        _progress(progress, manifest.total_size_bytes, manifest.total_size_bytes)

    def inspect(self, manifest: ModelManifest) -> str:
        states: list[str] = []
        for asset in manifest.files:
            destination = self._safe_destination(manifest, asset)
            if _verified(destination, asset):
                states.append("verified")
            elif destination.exists():
                states.append("invalid")
            elif self._partial_path(destination).exists():
                states.append("partial")
            else:
                states.append("missing")
        if states and all(value == "verified" for value in states):
            return "verified"
        if "invalid" in states:
            return "invalid"
        if "partial" in states or "verified" in states:
            return "partial"
        return "missing"

    def partial_bytes(self, manifest: ModelManifest) -> int:
        total = 0
        for asset in manifest.files:
            destination = self._safe_destination(manifest, asset)
            if _verified(destination, asset):
                total += asset.size_bytes
            else:
                partial = self._partial_path(destination)
                if partial.is_file():
                    total += min(partial.stat().st_size, asset.size_bytes)
        return total

    def cleanup_partial(self, manifest: ModelManifest) -> None:
        for asset in manifest.files:
            destination = self._safe_destination(manifest, asset)
            self._discard_partial(destination)

    def remove_version(self, manifest: ModelManifest) -> int:
        root = self._safe_version_root(manifest)
        if not root.exists():
            return 0
        size = sum(item.stat().st_size for item in root.rglob("*") if item.is_file())
        shutil.rmtree(root)
        return size

    async def _download_file(
        self,
        manifest: ModelManifest,
        asset: ModelFile,
        destination: Path,
        *,
        completed_before: int,
        total: int,
        control: DownloadControl,
        progress: DownloadProgress,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._validate_storage_path(destination.parent, self._safe_version_root(manifest))
        partial = self._partial_path(destination)
        checkpoint_path = self._checkpoint_path(destination)
        offset, saved_etag = self._load_partial(destination, asset)
        _ensure_space(destination.parent, asset.size_bytes - offset)
        headers = {"Accept": "application/octet-stream", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            headers["If-Range"] = saved_etag

        response: httpx.Response | None = None
        etag: str | None = saved_etag
        mode = "ab" if offset else "wb"
        written = offset
        rolling_digest = sha256()
        if offset:
            _update_digest_from_file(rolling_digest, partial)
        next_checkpoint = ((offset // _CHECKPOINT_INTERVAL) + 1) * _CHECKPOINT_INTERVAL
        try:
            response = await self._request(asset.url, headers=headers)
            _validate_headers(response)
            if response.status_code >= 500:
                raise AssetDownloadError(
                    "network",
                    "The model source is temporarily unavailable",
                    retryable=True,
                )
            if offset and response.status_code == 200:
                self._discard_partial(destination)
                offset = 0
                written = 0
                mode = "wb"
                rolling_digest = sha256()
                next_checkpoint = _CHECKPOINT_INTERVAL
            elif offset and response.status_code == 206:
                _validate_range(response, offset=offset, total=asset.size_bytes)
            elif offset:
                self._discard_partial(destination)
                raise AssetDownloadError(
                    "invalid_range",
                    "The model source rejected an existing download checkpoint",
                    retryable=True,
                )
            elif response.status_code == 206:
                self._discard_partial(destination)
                raise AssetDownloadError(
                    "invalid_range",
                    "The model source returned an unsolicited byte range",
                    retryable=False,
                )
            elif response.status_code != 200:
                raise AssetDownloadError(
                    "network",
                    "The model source returned an unexpected response",
                    retryable=response.status_code in {408, 425, 429},
                )
            etag = _strong_etag(response.headers.get("ETag"))
            if offset and etag != saved_etag:
                self._discard_partial(destination)
                raise AssetDownloadError(
                    "etag_changed",
                    "The model source changed while the download was paused",
                    retryable=True,
                )
            expected_response_bytes = asset.size_bytes - offset
            _validate_length(response, expected_response_bytes)
            encoding = response.headers.get("Content-Encoding", "identity").casefold()
            if encoding not in {"", "identity"}:
                self._discard_partial(destination)
                raise AssetDownloadError(
                    "encoded_response",
                    "The model source returned encoded bytes",
                    retryable=False,
                )

            with partial.open(mode) as stream:
                async for chunk in _controlled_response_chunks(response, control):
                    if not chunk:
                        continue
                    if written + len(chunk) > asset.size_bytes:
                        raise AssetDownloadError(
                            "size_mismatch",
                            "The model download exceeded its signed size",
                            retryable=False,
                        )
                    stream.write(chunk)
                    rolling_digest.update(chunk)
                    written += len(chunk)
                    _progress(progress, completed_before + written, total)
                    if (
                        etag is not None
                        and written >= next_checkpoint
                        and written < asset.size_bytes
                    ):
                        stream.flush()
                        os.fsync(stream.fileno())
                        self._save_checkpoint(
                            destination,
                            asset,
                            etag,
                            written,
                            partial_sha256=rolling_digest.hexdigest(),
                        )
                        next_checkpoint = (
                            (written // _CHECKPOINT_INTERVAL) + 1
                        ) * _CHECKPOINT_INTERVAL
                    directive = _directive(control)
                    if directive != "run":
                        stream.flush()
                        os.fsync(stream.fileno())
                        if directive == "cancel":
                            raise AssetDownloadCancelled()
                        self._save_checkpoint(
                            destination,
                            asset,
                            etag,
                            written,
                            partial_sha256=rolling_digest.hexdigest(),
                        )
                        raise AssetDownloadPaused()
                stream.flush()
                os.fsync(stream.fileno())
        except AssetDownloadCancelled:
            self._discard_partial(destination)
            raise
        except AssetDownloadPaused:
            if partial.is_file():
                self._save_checkpoint(
                    destination,
                    asset,
                    etag,
                    partial.stat().st_size,
                    partial_sha256=rolling_digest.hexdigest(),
                )
            raise
        except asyncio.CancelledError:
            if partial.is_file():
                self._save_checkpoint(
                    destination,
                    asset,
                    etag,
                    partial.stat().st_size,
                    partial_sha256=rolling_digest.hexdigest(),
                )
            raise
        except AssetDownloadError as error:
            if error.code in {
                "size_mismatch",
                "invalid_range",
                "etag_changed",
                "encoded_response",
                "unsafe_url",
                "unsafe_dns",
                "unsafe_peer",
                "header_too_large",
            }:
                self._discard_partial(destination)
            elif partial.is_file() and etag is not None:
                self._save_checkpoint(
                    destination,
                    asset,
                    etag,
                    partial.stat().st_size,
                    partial_sha256=rolling_digest.hexdigest(),
                )
            raise
        except (httpx.HTTPError, OSError) as error:
            if partial.is_file() and etag is not None:
                self._save_checkpoint(destination, asset, etag, partial.stat().st_size)
            raise AssetDownloadError(
                "network" if isinstance(error, httpx.HTTPError) else "storage",
                "The model download was interrupted",
                retryable=True,
            ) from error
        finally:
            if response is not None:
                await response.aclose()

        if written != asset.size_bytes or partial.stat().st_size != asset.size_bytes:
            if etag is not None:
                self._save_checkpoint(
                    destination,
                    asset,
                    etag,
                    partial.stat().st_size,
                    partial_sha256=rolling_digest.hexdigest(),
                )
            raise AssetDownloadError(
                "network",
                "The model download ended before its signed size",
                retryable=True,
            )
        if _sha256_file(partial) != asset.sha256:
            self._discard_partial(destination)
            raise AssetDownloadError(
                "hash_mismatch",
                "The model download failed SHA-256 verification",
                retryable=False,
            )
        os.replace(partial, destination)
        checkpoint_path.unlink(missing_ok=True)
        _fsync_file(destination)

    async def _request(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
        current = url
        for hop in range(_MAX_REDIRECTS + 1):
            target = await self._network.validate_request(current)
            try:
                request = self._client.build_request("GET", current, headers=headers)
                response = await self._client.send(request, stream=True, follow_redirects=False)
            except httpx.HTTPError as error:
                raise AssetDownloadError(
                    "network",
                    "The model source could not be reached",
                    retryable=True,
                ) from error
            try:
                await self._network.validate_response(response, target)
            except Exception:
                await response.aclose()
                raise
            _validate_headers(response)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("Location")
            await response.aclose()
            if not location or hop == _MAX_REDIRECTS:
                raise AssetDownloadError(
                    "redirect",
                    "The model source redirected too many times",
                    retryable=False,
                )
            current = urljoin(current, location)
        raise AssertionError("unreachable redirect loop")

    def _load_partial(self, destination: Path, asset: ModelFile) -> tuple[int, str]:
        partial = self._partial_path(destination)
        checkpoint = self._checkpoint_path(destination)
        if not partial.exists() and not checkpoint.exists():
            return 0, ""
        if not partial.is_file() or not checkpoint.is_file():
            self._discard_partial(destination)
            return 0, ""
        try:
            if checkpoint.stat().st_size > _CHECKPOINT_BUDGET:
                raise ValueError("oversized checkpoint")
            value = json.loads(checkpoint.read_text(encoding="utf-8"))
            required = {
                "schema_version",
                "url",
                "size_bytes",
                "sha256",
                "etag",
                "received_bytes",
                "partial_sha256",
            }
            if not isinstance(value, dict) or set(value) != required:
                raise ValueError("invalid checkpoint")
            received = value["received_bytes"]
            etag = _strong_etag(value["etag"] if isinstance(value["etag"], str) else None)
            if (
                value["schema_version"] != 1
                or value["url"] != asset.url
                or value["size_bytes"] != asset.size_bytes
                or value["sha256"] != asset.sha256
                or etag is None
                or isinstance(received, bool)
                or not isinstance(received, int)
                or not 0 < received < asset.size_bytes
                or partial.stat().st_size < received
            ):
                raise ValueError("untrusted checkpoint")
            if partial.stat().st_size > received:
                with partial.open("r+b") as stream:
                    stream.truncate(received)
                    stream.flush()
                    os.fsync(stream.fileno())
            if _sha256_file(partial) != value["partial_sha256"]:
                raise ValueError("partial hash mismatch")
            return received, etag
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            self._discard_partial(destination)
            return 0, ""

    def _save_checkpoint(
        self,
        destination: Path,
        asset: ModelFile,
        etag: str | None,
        received: int,
        *,
        partial_sha256: str | None = None,
    ) -> None:
        partial = self._partial_path(destination)
        if etag is None or not partial.is_file() or not 0 < received < asset.size_bytes:
            self._discard_partial(destination)
            return
        _fsync_file(partial)
        if partial.stat().st_size != received:
            raise AssetDownloadError(
                "storage",
                "The model checkpoint byte count is inconsistent",
                retryable=True,
            )
        payload = {
            "schema_version": 1,
            "url": asset.url,
            "size_bytes": asset.size_bytes,
            "sha256": asset.sha256,
            "etag": etag,
            "received_bytes": received,
            "partial_sha256": partial_sha256 or _sha256_file(partial),
        }
        checkpoint = self._checkpoint_path(destination)
        temporary = checkpoint.with_name(checkpoint.name + ".tmp")
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(data) > _CHECKPOINT_BUDGET:
            raise AssetDownloadError(
                "storage",
                "The model checkpoint exceeded its safe size",
                retryable=False,
            )
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, checkpoint)

    def _safe_version_root(self, manifest: ModelManifest) -> Path:
        expected = manifest.version_root(self._root)
        self._validate_storage_path(expected, self._root)
        resolved = expected.resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as error:
            raise AssetDownloadError(
                "unsafe_path",
                "The model version path escapes the model root",
                retryable=False,
            ) from error
        self._validate_storage_path(resolved.parent, self._root)
        return resolved

    def _safe_destination(self, manifest: ModelManifest, asset: ModelFile) -> Path:
        version_root = self._safe_version_root(manifest)
        expected = manifest.file_path(self._root, asset)
        self._validate_storage_path(expected.parent, manifest.version_root(self._root))
        destination = expected.resolve()
        try:
            destination.relative_to(version_root)
        except ValueError as error:
            raise AssetDownloadError(
                "unsafe_path",
                "The model file path escapes its version directory",
                retryable=False,
            ) from error
        self._validate_storage_path(destination.parent, version_root)
        return destination

    @staticmethod
    def _validate_storage_path(path: Path, boundary: Path) -> None:
        boundary = boundary.resolve()
        current = path
        while True:
            if current.exists():
                info = current.lstat()
                attributes = getattr(info, "st_file_attributes", 0)
                if current.is_symlink() or attributes & _REPARSE_POINT:
                    raise AssetDownloadError(
                        "unsafe_path",
                        "The model storage path contains a link or reparse point",
                        retryable=False,
                    )
            if current == boundary:
                return
            if boundary not in current.parents:
                raise AssetDownloadError(
                    "unsafe_path",
                    "The model storage path escapes its boundary",
                    retryable=False,
                )
            current = current.parent

    @staticmethod
    def _partial_path(destination: Path) -> Path:
        return destination.with_name(destination.name + ".part")

    @staticmethod
    def _checkpoint_path(destination: Path) -> Path:
        return destination.with_name(destination.name + ".part.json")

    def _discard_partial(self, destination: Path) -> None:
        self._partial_path(destination).unlink(missing_ok=True)
        self._checkpoint_path(destination).unlink(missing_ok=True)
        self._checkpoint_path(destination).with_name(
            self._checkpoint_path(destination).name + ".tmp"
        ).unlink(missing_ok=True)


def _directive(callback: DownloadControl) -> DownloadDirective:
    try:
        value = callback()
    except Exception as error:
        raise AssetDownloadError(
            "control",
            "The model download control could not be read",
            retryable=True,
        ) from error
    if value not in {"run", "pause", "cancel"}:
        raise AssetDownloadError(
            "control",
            "The model download control returned an invalid state",
            retryable=False,
        )
    return value


def _progress(callback: DownloadProgress, received: int, total: int) -> None:
    try:
        callback(received, total)
    except Exception:
        return


def _validate_headers(response: httpx.Response) -> None:
    size = sum(len(name) + len(value) + 4 for name, value in response.headers.raw)
    if size > _HEADER_BUDGET:
        raise AssetDownloadError(
            "header_too_large",
            "The model source response headers exceeded the safe limit",
            retryable=False,
        )


async def _response_chunks(response: httpx.Response) -> AsyncIterator[bytes]:
    if response.is_stream_consumed:
        content = response.content
        for start in range(0, len(content), _CHUNK_SIZE):
            yield content[start : start + _CHUNK_SIZE]
        return
    async for chunk in response.aiter_raw():
        yield chunk


async def _controlled_response_chunks(
    response: httpx.Response,
    control: DownloadControl,
) -> AsyncIterator[bytes]:
    iterator = _response_chunks(response).__aiter__()
    while True:
        pending: asyncio.Future[bytes] = asyncio.ensure_future(anext(iterator))
        try:
            while not pending.done():
                await asyncio.wait({pending}, timeout=_CONTROL_POLL_SECONDS)
                if pending.done():
                    break
                directive = _directive(control)
                if directive == "pause":
                    raise AssetDownloadPaused()
                if directive == "cancel":
                    raise AssetDownloadCancelled()
            try:
                yield pending.result()
            except StopAsyncIteration:
                return
        except BaseException:
            if not pending.done():
                pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
            raise


def _validate_length(response: httpx.Response, expected: int) -> None:
    raw = response.headers.get("Content-Length")
    try:
        value = int(raw) if raw is not None else -1
    except ValueError as error:
        raise AssetDownloadError(
            "size_mismatch",
            "The model source returned an invalid byte length",
            retryable=False,
        ) from error
    if value != expected:
        raise AssetDownloadError(
            "size_mismatch",
            "The model source byte length did not match the signed manifest",
            retryable=False,
        )


def _validate_range(response: httpx.Response, *, offset: int, total: int) -> None:
    expected = f"bytes {offset}-{total - 1}/{total}"
    if response.headers.get("Content-Range") != expected:
        raise AssetDownloadError(
            "invalid_range",
            "The model source returned an invalid byte range",
            retryable=False,
        )


def _strong_etag(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if (
        len(stripped) > 512
        or stripped.startswith("W/")
        or len(stripped) < 2
        or not stripped.startswith('"')
        or not stripped.endswith('"')
    ):
        return None
    return stripped


def _public_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return False


def _normalize_ip(value: str) -> str:
    stripped = value.split("%", 1)[0]
    try:
        return ipaddress.ip_address(stripped).compressed
    except ValueError:
        return stripped


def _verified(path: Path, asset: ModelFile) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == asset.size_bytes
        and _sha256_file(path) == asset.sha256
    )


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _update_digest_from_file(digest: DigestWriter, path: Path) -> None:
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _ensure_space(path: Path, remaining: int) -> None:
    try:
        free = shutil.disk_usage(path).free
    except OSError as error:
        raise AssetDownloadError(
            "storage",
            "Available model storage could not be measured",
            retryable=True,
        ) from error
    if free < remaining + 16 * 1024 * 1024:
        raise AssetDownloadError(
            "disk_full",
            "There is not enough free space for the model download",
            retryable=True,
        )
