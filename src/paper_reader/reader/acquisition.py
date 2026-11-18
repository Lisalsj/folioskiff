"""Safe, user-triggered acquisition of lawful open PDF documents."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol, cast
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import (
    DocumentVersionRecord,
    SourceRecord,
    WorkRecord,
)
from paper_reader.jobs.models import JobRecord, utc_now
from paper_reader.jobs.repository import JobRepository
from paper_reader.reader.imports import ReferenceImportError, _normalize_public_url
from paper_reader.reader.models import LibraryEntryRecord
from paper_reader.reader.storage import (
    ContentStore,
    DocumentTooLargeError,
    InvalidPdfError,
    StorageCancelledError,
    StoredObject,
)

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]
ProgressCallback = Callable[[int, int | None], None]
ACQUISITION_JOB_KIND = "reader.acquire_document"


class AcquisitionReason(StrEnum):
    OPEN = "open"
    SAVE = "save"
    ANALYZE = "analyze"


@dataclass(frozen=True, slots=True)
class AcquisitionRequest:
    work_id: str
    source_record_id: str | None
    url: str
    access_status: Literal["open"]
    reason: AcquisitionReason


class PdfDownloadError(RuntimeError):
    """Categorized failure from public PDF acquisition."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class SafeUrl:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


async def resolve_host_addresses(host: str, port: int) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    answers = await loop.getaddrinfo(
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    addresses: list[str] = []
    seen: set[str] = set()
    for answer in answers:
        address = str(answer[4][0])
        if address not in seen:
            addresses.append(address)
            seen.add(address)
    return tuple(addresses)


class PublicUrlPolicy:
    """Validate URL syntax and every resolved address before a request."""

    def __init__(self, *, resolver: Resolver = resolve_host_addresses) -> None:
        self._resolver = resolver

    async def validate(self, value: str) -> SafeUrl:
        try:
            normalized_url, _ = _normalize_public_url(value)
            parsed = urlsplit(normalized_url)
            host = parsed.hostname
            if host is None:  # pragma: no cover - URL normalizer requires a host
                raise ReferenceImportError("URL requires a host")
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except (ReferenceImportError, ValueError) as error:
            raise PdfDownloadError(
                "unsafe_url",
                "The document URL is not a safe public HTTP destination",
                retryable=False,
            ) from error
        try:
            answers = await self._resolver(host, port)
        except OSError as error:
            raise PdfDownloadError(
                "dns_failure",
                "The public document host could not be resolved",
                retryable=True,
            ) from error

        addresses: list[str] = []
        seen: set[str] = set()
        for answer in answers:
            try:
                address = ipaddress.ip_address(answer)
            except ValueError as error:
                raise PdfDownloadError(
                    "unsafe_url",
                    "The document host resolved to an invalid address",
                    retryable=False,
                ) from error
            if not address.is_global:
                raise PdfDownloadError(
                    "unsafe_url",
                    "The document host resolved outside the public Internet",
                    retryable=False,
                )
            rendered = str(address)
            if rendered not in seen:
                addresses.append(rendered)
                seen.add(rendered)
        if not addresses:
            raise PdfDownloadError(
                "unsafe_url",
                "The document host did not resolve to a public address",
                retryable=False,
            )
        return SafeUrl(
            url=normalized_url,
            host=host,
            port=port,
            addresses=tuple(addresses),
        )


class OpenPdfDownloader:
    """Stream a validated open PDF through the content-addressed store."""

    _REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
    _RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
    _ACCEPTED_MEDIA_TYPES = frozenset(
        {"application/pdf", "application/octet-stream"}
    )

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        store: ContentStore,
        policy: PublicUrlPolicy,
        max_bytes: int = 100 * 1024 * 1024,
        max_redirects: int = 5,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if max_redirects < 0:
            raise ValueError("max_redirects cannot be negative")
        self._client = client
        self._store = store
        self._policy = policy
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects

    async def download(
        self,
        url: str,
        *,
        cancelled: Callable[[], bool],
        progress: ProgressCallback = lambda _received, _total: None,
    ) -> StoredObject:
        if cancelled():
            raise PdfDownloadError(
                "cancelled", "The document download was cancelled", retryable=False
            )

        current_url = url
        redirects = 0
        try:
            while True:
                safe_url = await self._policy.validate(current_url)
                async with self._client.stream(
                    "GET",
                    safe_url.url,
                    headers={
                        "Accept": "application/pdf, application/octet-stream;q=0.8",
                        "Accept-Encoding": "identity",
                    },
                    follow_redirects=False,
                ) as response:
                    self._validate_connected_peer(response, safe_url)
                    if response.status_code in self._REDIRECT_STATUSES:
                        if redirects >= self._max_redirects:
                            raise PdfDownloadError(
                                "redirect",
                                "The document URL exceeded the redirect limit",
                                retryable=False,
                            )
                        location = response.headers.get("Location")
                        if not location:
                            raise PdfDownloadError(
                                "redirect",
                                "The document server returned an invalid redirect",
                                retryable=False,
                            )
                        current_url = urljoin(safe_url.url, location)
                        redirects += 1
                        continue

                    self._validate_status(response.status_code)
                    total = self._validate_headers(response.headers)
                    with self._store.stage(
                        media_type="application/pdf",
                        max_bytes=self._max_bytes,
                        cancelled=cancelled,
                    ) as stage:
                        async for chunk in response.aiter_raw():
                            stage.write(chunk)
                            progress(stage.byte_size, total)
                        if total is not None and stage.byte_size != total:
                            raise PdfDownloadError(
                                "truncated",
                                "The document transfer ended before its declared size",
                                retryable=True,
                            )
                        return stage.commit()
        except PdfDownloadError:
            raise
        except DocumentTooLargeError as error:
            raise PdfDownloadError(
                "too_large",
                "The PDF exceeds the configured download limit",
                retryable=False,
            ) from error
        except InvalidPdfError as error:
            raise PdfDownloadError(
                "not_pdf",
                "The response is not a valid PDF document",
                retryable=False,
            ) from error
        except StorageCancelledError as error:
            raise PdfDownloadError(
                "cancelled", "The document download was cancelled", retryable=False
            ) from error
        except httpx.HTTPError as error:
            raise PdfDownloadError(
                "network",
                "The document could not be downloaded from the public source",
                retryable=True,
            ) from error

    def _validate_headers(self, headers: httpx.Headers) -> int | None:
        content_encoding = headers.get("Content-Encoding", "identity").casefold()
        if content_encoding not in {"", "identity"}:
            raise PdfDownloadError(
                "network",
                "The document server ignored the identity encoding request",
                retryable=True,
            )
        media_type = headers.get("Content-Type", "").partition(";")[0].strip()
        if media_type.casefold() not in self._ACCEPTED_MEDIA_TYPES:
            raise PdfDownloadError(
                "not_pdf",
                "The response is not declared as a PDF document",
                retryable=False,
            )
        content_length = headers.get("Content-Length")
        if content_length is None:
            return None
        try:
            total = int(content_length)
        except ValueError as error:
            raise PdfDownloadError(
                "network",
                "The document server returned an invalid content length",
                retryable=True,
            ) from error
        if total < 0:
            raise PdfDownloadError(
                "network",
                "The document server returned an invalid content length",
                retryable=True,
            )
        if total > self._max_bytes:
            raise PdfDownloadError(
                "too_large",
                "The PDF exceeds the configured download limit",
                retryable=False,
            )
        return total

    def _validate_status(self, status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        raise PdfDownloadError(
            "network",
            "The public document source returned an unsuccessful response",
            retryable=status_code in self._RETRYABLE_STATUSES,
        )

    @staticmethod
    def _validate_connected_peer(
        response: httpx.Response, safe_url: SafeUrl
    ) -> None:
        network_stream = response.extensions.get("network_stream")
        get_extra_info = getattr(network_stream, "get_extra_info", None)
        if not callable(get_extra_info):
            raise PdfDownloadError(
                "unsafe_url",
                "The connected document server address could not be verified",
                retryable=False,
            )
        server_address: Any = get_extra_info("server_addr")
        if not server_address:
            raise PdfDownloadError(
                "unsafe_url",
                "The connected document server address could not be verified",
                retryable=False,
            )
        raw_address = server_address[0] if isinstance(server_address, tuple) else None
        if not isinstance(raw_address, (str, int, bytes)):
            raise PdfDownloadError(
                "unsafe_url",
                "The connected document server address is unsafe",
                retryable=False,
            )
        try:
            peer = ipaddress.ip_address(raw_address)
        except (TypeError, ValueError) as error:
            raise PdfDownloadError(
                "unsafe_url",
                "The connected document server address is unsafe",
                retryable=False,
            ) from error
        if not peer.is_global or str(peer) not in safe_url.addresses:
            raise PdfDownloadError(
                "unsafe_url",
                "The connected document server does not match validated public DNS",
                retryable=False,
            )


class PdfDownloader(Protocol):
    async def download(
        self,
        url: str,
        *,
        cancelled: Callable[[], bool],
        progress: ProgressCallback,
    ) -> StoredObject: ...


class DocumentAcquisitionService:
    """Coordinate durable acquisition jobs and document persistence."""

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        jobs: JobRepository,
        downloader: PdfDownloader,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._jobs = jobs
        self._downloader = downloader
        self._clock = clock

    def create_job(
        self,
        value: AcquisitionRequest,
        *,
        idempotency_key: str,
    ) -> JobRecord:
        key = idempotency_key.strip()
        if not key or len(key) > 160:
            raise ValueError("idempotency key must contain 1-160 characters")
        if value.access_status != "open":
            raise ValueError("Only confirmed open access documents can be acquired")
        try:
            reason = AcquisitionReason(value.reason)
        except ValueError as error:
            raise ValueError("Unsupported acquisition reason") from error
        normalized_url, _ = _normalize_public_url(value.url)
        with self._sessions() as session:
            work = self._canonical_work(session, value.work_id)
            source_record_id = self._validated_source_id(
                session,
                work_id=work.id,
                source_record_id=value.source_record_id,
            )
        payload: dict[str, Any] = {
            "work_id": work.id,
            "source_record_id": source_record_id,
            "url": normalized_url,
            "access_status": "open",
            "reason": reason.value,
        }
        job = self._jobs.create(
            kind=ACQUISITION_JOB_KIND,
            payload=payload,
            idempotency_key=key,
        )
        if job.kind != ACQUISITION_JOB_KIND or job.payload != payload:
            raise ValueError("idempotency key belongs to a different acquisition command")
        return job

    async def execute(self, job_id: str) -> None:
        job = self._require_job(job_id)
        payload = self._read_payload(job.payload)
        if job.status in {"succeeded", "cancelled"}:
            return
        checkpoint: dict[str, Any] = {
            "received_bytes": 0,
            "total_bytes": None,
        }
        if job.cancel_requested:
            self._finish_cancelled(job.id, progress=job.progress, checkpoint=checkpoint)
            return

        self._jobs.update_state(
            job.id,
            status="running",
            progress=0,
            checkpoint=checkpoint,
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="reader.acquisition.started",
            progress=0,
            payload={"work_id": payload["work_id"], "reason": payload["reason"]},
        )

        current_progress = 0

        def cancelled() -> bool:
            current = self._jobs.get(job.id)
            return current is None or current.cancel_requested

        def report_progress(received: int, total: int | None) -> None:
            nonlocal checkpoint, current_progress
            checkpoint = {
                "received_bytes": received,
                "total_bytes": total,
            }
            current_progress = self._download_progress(received, total)
            self._jobs.update_state(
                job.id,
                status="running",
                progress=current_progress,
                checkpoint=checkpoint,
            )
            self._jobs.append_event(
                job_id=job.id,
                event_type="reader.acquisition.progress",
                progress=current_progress,
                payload=dict(checkpoint),
            )

        try:
            stored = await self._downloader.download(
                payload["url"],
                cancelled=cancelled,
                progress=report_progress,
            )
            if cancelled():
                self._finish_cancelled(
                    job.id,
                    progress=current_progress,
                    checkpoint=checkpoint,
                )
                return
            document_id, created_document = self._persist_document(payload, stored)
        except PdfDownloadError as error:
            if error.code == "cancelled":
                self._finish_cancelled(
                    job.id,
                    progress=current_progress,
                    checkpoint=checkpoint,
                )
            else:
                self._finish_failed(
                    job.id,
                    progress=current_progress,
                    checkpoint=checkpoint,
                    error=error,
                )
            return
        except Exception:
            unexpected_error = PdfDownloadError(
                "internal_error",
                "The document acquisition failed unexpectedly",
                retryable=False,
            )
            self._finish_failed(
                job.id,
                progress=current_progress,
                checkpoint=checkpoint,
                error=unexpected_error,
            )
            return

        checkpoint = {
            "received_bytes": stored.byte_size,
            "total_bytes": stored.byte_size,
            "content_sha256": stored.content_sha256,
            "document_version_id": document_id,
        }
        result = {
            "work_id": payload["work_id"],
            "document_version_id": document_id,
            "content_sha256": stored.content_sha256,
            "created_document": created_document,
        }
        self._jobs.update_state(
            job.id,
            status="succeeded",
            progress=100,
            result=result,
            checkpoint=checkpoint,
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="reader.acquisition.succeeded",
            progress=100,
            payload=result,
        )

    def resumable_job_ids(self) -> tuple[str, ...]:
        return self._jobs.list_resumable_ids(kind=ACQUISITION_JOB_KIND)

    def _persist_document(
        self,
        payload: Mapping[str, Any],
        stored: StoredObject,
    ) -> tuple[str, bool]:
        observed_at = self._aware_utc(self._clock())
        work_id = cast(str, payload["work_id"])
        source_record_id = cast(str | None, payload["source_record_id"])
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            source_id = self._validated_source_id(
                session,
                work_id=work.id,
                source_record_id=source_record_id,
            )
            source = session.get(SourceRecord, source_id) if source_id else None
            document = session.scalar(
                select(DocumentVersionRecord).where(
                    DocumentVersionRecord.work_id == work.id,
                    DocumentVersionRecord.content_sha256 == stored.content_sha256,
                )
            )
            created_document = document is None
            if document is None:
                document = DocumentVersionRecord(
                    work_id=work.id,
                    source_record_id=source_id,
                    content_sha256=stored.content_sha256,
                    access_status="open",
                    media_type=stored.media_type,
                    completeness=100,
                    source_updated_at=(source.source_updated_at if source else observed_at),
                    extraction_status="pending",
                    relative_path=stored.relative_path,
                    byte_size=stored.byte_size,
                    original_filename=self._download_filename(cast(str, payload["url"])),
                    acquisition_kind="download",
                    acquisition_url=cast(str, payload["url"]),
                    last_accessed_at=observed_at,
                    created_at=observed_at,
                )
                session.add(document)
                session.flush()
            else:
                document.relative_path = stored.relative_path
                document.byte_size = stored.byte_size
                document.access_status = "open"
                document.completeness = max(document.completeness, 100)
                document.acquisition_kind = "download"
                document.acquisition_url = cast(str, payload["url"])
                document.last_accessed_at = observed_at
                if document.source_record_id is None:
                    document.source_record_id = source_id

            library_entry = session.get(LibraryEntryRecord, work.id)
            if library_entry is None:
                session.add(
                    LibraryEntryRecord(
                        work_id=work.id,
                        added_via="save",
                        created_at=observed_at,
                        updated_at=observed_at,
                    )
                )
            else:
                library_entry.updated_at = observed_at
            session.commit()
            return document.id, created_document

    def _finish_cancelled(
        self,
        job_id: str,
        *,
        progress: int,
        checkpoint: dict[str, Any],
    ) -> None:
        result = {"cancelled": True}
        self._jobs.update_state(
            job_id,
            status="cancelled",
            progress=progress,
            result=result,
            checkpoint=checkpoint,
            error_code="cancelled",
            error_message="The document acquisition was cancelled",
        )
        self._jobs.append_event(
            job_id=job_id,
            event_type="reader.acquisition.cancelled",
            progress=progress,
            payload=result,
        )

    def _finish_failed(
        self,
        job_id: str,
        *,
        progress: int,
        checkpoint: dict[str, Any],
        error: PdfDownloadError,
    ) -> None:
        result = {"retryable": error.retryable}
        self._jobs.update_state(
            job_id,
            status="failed",
            progress=progress,
            result=result,
            checkpoint=checkpoint,
            error_code=error.code,
            error_message=str(error),
        )
        self._jobs.append_event(
            job_id=job_id,
            event_type="reader.acquisition.failed",
            progress=progress,
            payload={"code": error.code, "retryable": error.retryable},
        )

    def _require_job(self, job_id: str) -> JobRecord:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.kind != ACQUISITION_JOB_KIND:
            raise ValueError("job is not a document acquisition command")
        return job

    @staticmethod
    def _read_payload(value: Mapping[str, Any]) -> dict[str, Any]:
        expected = {
            "work_id": str,
            "url": str,
            "access_status": str,
            "reason": str,
        }
        if any(not isinstance(value.get(key), kind) for key, kind in expected.items()):
            raise ValueError("document acquisition job payload is invalid")
        source_record_id = value.get("source_record_id")
        if source_record_id is not None and not isinstance(source_record_id, str):
            raise ValueError("document acquisition job payload is invalid")
        if value["access_status"] != "open" or value["reason"] not in {
            item.value for item in AcquisitionReason
        }:
            raise ValueError("document acquisition job payload is invalid")
        return {
            "work_id": value["work_id"],
            "source_record_id": source_record_id,
            "url": value["url"],
            "access_status": value["access_status"],
            "reason": value["reason"],
        }

    @staticmethod
    def _canonical_work(session: Session, work_id: str) -> WorkRecord:
        work = session.get(WorkRecord, work_id)
        if work is None:
            raise KeyError(work_id)
        visited: set[str] = set()
        while work.status == "merged":
            if work.id in visited or work.merged_into_work_id is None:
                raise RuntimeError("Invalid Work merge chain")
            visited.add(work.id)
            target = session.get(WorkRecord, work.merged_into_work_id)
            if target is None:
                raise RuntimeError("Merged Work target is missing")
            work = target
        return work

    @staticmethod
    def _validated_source_id(
        session: Session,
        *,
        work_id: str,
        source_record_id: str | None,
    ) -> str | None:
        if source_record_id is None:
            return None
        source = session.get(SourceRecord, source_record_id)
        if source is None or source.work_id != work_id:
            raise ValueError("source record does not belong to the requested Work")
        return source.id

    @staticmethod
    def _download_progress(received: int, total: int | None) -> int:
        if total and total > 0:
            return min(90, max(1, int(received * 90 / total)))
        return 5 if received else 0

    @staticmethod
    def _download_filename(url: str) -> str:
        name = PurePosixPath(urlsplit(url).path).name
        if not name or len(name) > 255:
            return "paper.pdf"
        return name

    @staticmethod
    def _aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(UTC)
