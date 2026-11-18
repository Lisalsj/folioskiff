"""Durable backup commands, integrity-gated downloads, and weekly policy."""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from typing import BinaryIO, Protocol, TypedDict
from uuid import uuid4

from paper_reader.backups.archive import (
    BackupArchiveBuilder,
    BackupArchiveResult,
    BackupCancelledError,
    BackupError,
    BackupInsufficientSpaceError,
    BackupIntegrityError,
    UnsafeBackupPathError,
)
from paper_reader.backups.crypto import (
    BackupCryptoError,
    PasswordLeaseStore,
    PasswordMaterial,
    encrypt_backup,
    is_encrypted_backup,
)
from paper_reader.backups.manifest import utc_text
from paper_reader.backups.models import BackupArchiveRecord
from paper_reader.backups.repository import BackupRepository
from paper_reader.backups.restore import (
    MAX_PACKAGE_BYTES,
    PendingRestoreError,
    RestorePackageValidator,
    RestoreValidationError,
    ValidatedRestorePackage,
    pending_restore_path,
    restore_journal_path,
    schedule_pending_restore,
    staged_restore_fingerprint,
)
from paper_reader.jobs.models import JobRecord, utc_now
from paper_reader.jobs.repository import JobRepository
from paper_reader.settings import AppPaths

BACKUP_JOB_KIND = "backup.create"
RESTORE_VALIDATION_JOB_KIND = "backup.restore.validate"
RESTORE_CONFIRMATION_JOB_KIND = "backup.restore.confirm"
AUTOMATIC_INTERVAL = timedelta(days=7)
SCHEDULER_CHECK_INTERVAL = timedelta(hours=6)
AUTOMATIC_RETENTION = 4
RESTORE_PREVIEW_TTL = timedelta(minutes=10)
_READ_SIZE = 1024 * 1024


class BackupKind(StrEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class ArchiveBuilder(Protocol):
    def create(
        self,
        *,
        destination: Path,
        cancelled: Callable[[], bool],
        progress: Callable[[str, int, int], None],
    ) -> BackupArchiveResult: ...


class BackupJobPayload(TypedDict):
    kind: str
    encrypted: bool


class BackupUnavailableError(RuntimeError):
    """A catalog entry cannot be opened without trusting an unsafe path."""


@dataclass(frozen=True, slots=True)
class BackupPolicy:
    automatic_interval_days: int
    automatic_retention: int
    check_interval_hours: int
    latest_automatic_at: datetime | None
    next_automatic_at: datetime | None


@dataclass(slots=True)
class BackupDownload:
    stream: BinaryIO
    filename: str
    byte_size: int
    package_sha256: str
    encrypted: bool


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    """SQLite may return a naive value even for a timezone-aware column."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class BackupService:
    def __init__(
        self,
        *,
        paths: AppPaths,
        jobs: JobRepository,
        archives: BackupRepository,
        builder: ArchiveBuilder | None = None,
        passwords: PasswordLeaseStore | None = None,
        clock: Callable[[], datetime] = utc_now,
        automatic_retention: int = AUTOMATIC_RETENTION,
    ) -> None:
        if automatic_retention < 1:
            raise ValueError("automatic_retention must be at least one")
        self._paths = paths
        self._jobs = jobs
        self._archives = archives
        self._builder = builder or BackupArchiveBuilder(paths=paths, clock=clock)
        self._passwords = passwords or PasswordLeaseStore()
        self._password_lock = threading.Lock()
        self._password_leases: dict[str, str] = {}
        self._clock = clock
        self._automatic_retention = automatic_retention

    def create_job(
        self,
        *,
        kind: BackupKind,
        idempotency_key: str,
        encrypted: bool = False,
        password: PasswordMaterial | None = None,
    ) -> JobRecord:
        try:
            normalized_kind = BackupKind(kind)
        except ValueError as error:
            raise ValueError("Unsupported backup kind") from error
        key = idempotency_key.strip()
        if not key or len(key) > 146:
            raise ValueError("idempotency key must contain 1-146 characters")
        if normalized_kind is BackupKind.AUTOMATIC and encrypted:
            raise ValueError("automatic backups cannot require an in-memory password")
        payload = {"kind": normalized_kind.value, "encrypted": encrypted}
        namespaced_key = f"backup.create:{key}"
        existing = self._jobs.get_by_idempotency_key(namespaced_key)
        if existing is not None:
            if existing.kind != BACKUP_JOB_KIND or existing.payload != payload:
                raise ValueError("idempotency key belongs to a different backup command")
            if existing.status in {"queued", "running"}:
                if encrypted != (password is not None):
                    raise ValueError("a password is required only for an encrypted backup")
                if encrypted and password is not None:
                    self._replace_password_lease(existing.id, password)
            return existing
        if encrypted != (password is not None):
            raise ValueError("a password is required only for an encrypted backup")
        job = self._jobs.create(
            kind=BACKUP_JOB_KIND,
            payload=payload,
            idempotency_key=namespaced_key,
        )
        if job.kind != BACKUP_JOB_KIND or job.payload != payload:
            raise ValueError("idempotency key belongs to a different backup command")
        if encrypted and password is not None:
            self._replace_password_lease(job.id, password)
        return job

    async def execute(self, job_id: str) -> None:
        job = self._require_job(job_id)
        payload = self._read_payload(job)
        if job.status in {"succeeded", "cancelled"}:
            return
        if job.cancel_requested:
            self._finish_cancelled(job, progress=job.progress)
            return

        existing = self._archives.get_by_job(job.id)
        if existing is not None:
            try:
                verified = self._open_verified(existing)
            except BackupUnavailableError:
                self._finish_failed(job, code="integrity", retryable=False)
                return
            verified.stream.close()
            self._finish_success(job, existing, emit_archived=True)
            self._prune_after_success(job.id)
            return

        destination = self._destination(job, kind=payload["kind"])
        start_checkpoint = {"phase": "snapshot"}
        self._jobs.update_state(
            job.id,
            status="running",
            progress=5,
            checkpoint=start_checkpoint,
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="backup.started",
            progress=5,
            payload={"kind": payload["kind"], "encrypted": payload["encrypted"]},
        )
        try:
            self._require_fresh_destination(destination)
        except BackupError as error:
            code, retryable = self._classify_backup_error(error)
            self._finish_failed(job, code=code, retryable=retryable)
            return

        stopping = threading.Event()

        def cancelled() -> bool:
            current = self._jobs.get(job.id)
            return stopping.is_set() or current is None or current.cancel_requested

        current_progress = 5

        def report_progress(phase: str, completed: int, total: int) -> None:
            nonlocal current_progress
            if stopping.is_set() or total <= 0:
                return
            progress = min(90, max(6, 5 + int(85 * completed / total)))
            if progress <= current_progress:
                return
            current_progress = progress
            checkpoint: dict[str, object] = {
                "phase": "archive",
                "stage": phase,
                "completed_steps": completed,
                "total_steps": total,
            }
            self._jobs.update_state(
                job.id,
                status="running",
                progress=progress,
                checkpoint=checkpoint,
            )
            self._jobs.append_event(
                job_id=job.id,
                event_type="backup.progress",
                progress=progress,
                payload=checkpoint,
            )

        work = asyncio.create_task(
            asyncio.to_thread(
                self._create_archive,
                job,
                payload,
                destination=destination,
                cancelled=cancelled,
                progress=report_progress,
            )
        )
        try:
            result = await asyncio.shield(work)
        except asyncio.CancelledError:
            stopping.set()
            try:
                completed = await asyncio.shield(work)
            except Exception:
                self._interrupt_for_shutdown(job.id)
            else:
                try:
                    archive = self._persist_result(
                        job,
                        payload["kind"],
                        completed,
                        encrypted=payload["encrypted"],
                    )
                except Exception:
                    self._interrupt_for_shutdown(job.id)
                else:
                    self._finish_success(job, archive, emit_archived=True)
                    self._prune_after_success(job.id)
            raise
        except BackupCancelledError:
            self._finish_cancelled(job, progress=current_progress)
            return
        except BackupError as error:
            code, retryable = self._classify_backup_error(error)
            self._finish_failed(job, code=code, retryable=retryable)
            return
        except BackupCryptoError as error:
            code, retryable = self._classify_crypto_error(error)
            self._finish_failed(job, code=code, retryable=retryable)
            return
        except OSError:
            self._finish_failed(job, code="io_error", retryable=True)
            return
        except Exception:
            self._finish_failed(job, code="internal_error", retryable=False)
            return

        try:
            archive = self._persist_result(
                job,
                payload["kind"],
                result,
                encrypted=payload["encrypted"],
            )
        except BackupError as error:
            code, retryable = self._classify_backup_error(error)
            self._finish_failed(job, code=code, retryable=retryable)
            return
        except BackupCryptoError as error:
            code, retryable = self._classify_crypto_error(error)
            self._finish_failed(job, code=code, retryable=retryable)
            return
        except OSError:
            self._finish_failed(job, code="io_error", retryable=True)
            return
        except Exception:
            self._finish_failed(job, code="internal_error", retryable=False)
            return
        self._finish_success(job, archive, emit_archived=True)
        self._prune_after_success(job.id)

    def resumable_job_ids(self) -> tuple[str, ...]:
        return self._jobs.list_resumable_ids(kind=BACKUP_JOB_KIND)

    def list_archives(self) -> tuple[BackupArchiveRecord, ...]:
        return self._archives.list()

    def policy(self) -> BackupPolicy:
        latest = self._archives.latest_automatic()
        latest_at = _stored_utc(latest.created_at) if latest is not None else None
        schedule_started_at = _stored_utc(
            self._archives.schedule_started_at(default=_aware_utc(self._clock()))
        )
        next_automatic_at = (
            latest_at + AUTOMATIC_INTERVAL
            if latest_at is not None
            else schedule_started_at + AUTOMATIC_INTERVAL
        )
        return BackupPolicy(
            automatic_interval_days=AUTOMATIC_INTERVAL.days,
            automatic_retention=self._automatic_retention,
            check_interval_hours=int(SCHEDULER_CHECK_INTERVAL.total_seconds() // 3600),
            latest_automatic_at=latest_at,
            next_automatic_at=next_automatic_at,
        )

    def open_download(self, backup_id: str) -> BackupDownload:
        record = self._archives.get(backup_id)
        if record is None or record.deleted_at is not None:
            raise BackupUnavailableError("Backup is unavailable")
        return self._open_verified(record)

    def close(self) -> None:
        with self._password_lock:
            lease_ids = tuple(self._password_leases.values())
            self._password_leases.clear()
        for lease_id in lease_ids:
            self._passwords.discard(lease_id)
        self._passwords.close()

    def _create_archive(
        self,
        job: JobRecord,
        payload: BackupJobPayload,
        *,
        destination: Path,
        cancelled: Callable[[], bool],
        progress: Callable[[str, int, int], None],
    ) -> BackupArchiveResult:
        if not payload["encrypted"]:
            return self._builder.create(
                destination=destination,
                cancelled=cancelled,
                progress=progress,
            )
        with self._password_lock:
            lease_id = self._password_leases.pop(job.id, None)
        if lease_id is None:
            raise BackupCryptoError(
                "secret_lease_unavailable",
                "The encrypted backup password must be entered again",
            )
        # Keep the private plaintext name short enough for conservative Windows
        # path limits even when the user's Documents directory is deeply nested.
        plain = self._paths.backups / f".encrypt-{job.id}.plain"
        try:
            plain_result = self._builder.create(
                destination=plain,
                cancelled=cancelled,
                progress=progress,
            )
            with self._passwords.consume(lease_id) as secret:
                encrypted_result = encrypt_backup(
                    plain,
                    destination,
                    password=secret,
                    cancelled=cancelled,
                )
            if encrypted_result.plaintext_sha256 != plain_result.package_sha256:
                raise BackupCryptoError(
                    "source_changed",
                    "The backup changed during encryption",
                )
            return BackupArchiveResult(
                path=encrypted_result.path,
                package_sha256=encrypted_result.package_sha256,
                manifest_sha256=plain_result.manifest_sha256,
                manifest=plain_result.manifest,
            )
        finally:
            plain.unlink(missing_ok=True)
            self._passwords.discard(lease_id)

    def _persist_result(
        self,
        job: JobRecord,
        kind: str,
        result: BackupArchiveResult,
        *,
        encrypted: bool,
    ) -> BackupArchiveRecord:
        destination = self._destination(job, kind=kind)
        try:
            before = os.lstat(destination)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise BackupIntegrityError("Published backup is not a regular file")
            if result.path.resolve(strict=True) != destination.resolve(strict=True):
                raise BackupIntegrityError("Backup builder returned an unexpected path")
            if destination.resolve(strict=True).parent != self._paths.backups.resolve():
                raise BackupIntegrityError("Published backup escaped the backup root")
            byte_size = destination.stat().st_size
            if byte_size <= 0 or self._hash_path(destination) != result.package_sha256:
                raise BackupIntegrityError("Published backup hash does not match the result")
            if result.manifest.sha256() != result.manifest_sha256:
                raise BackupIntegrityError("Published backup manifest hash is invalid")
        except OSError as error:
            raise BackupIntegrityError("Published backup is unavailable") from error
        archive = self._archives.create(
            job_id=job.id,
            kind=kind,
            relative_path=destination.name,
            byte_size=byte_size,
            encrypted=encrypted,
            result=result,
        )
        if not self._record_matches_result(
            archive,
            result,
            byte_size=byte_size,
            encrypted=encrypted,
        ):
            raise BackupIntegrityError("Backup catalog conflicts with the published package")
        checkpoint = self._checkpoint(archive, phase="archived")
        self._jobs.update_state(
            job.id,
            status="running",
            progress=95,
            checkpoint=checkpoint,
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="backup.archived",
            progress=95,
            payload={
                "backup_id": archive.id,
                "byte_size": archive.byte_size,
                "package_sha256": archive.package_sha256,
            },
        )
        return archive

    def _finish_success(
        self,
        job: JobRecord,
        archive: BackupArchiveRecord,
        *,
        emit_archived: bool,
    ) -> None:
        self._discard_password_lease(job.id)
        if emit_archived:
            current = self._jobs.get(job.id)
            if current is None:
                raise KeyError(job.id)
            if current.checkpoint is None or current.checkpoint.get("backup_id") != archive.id:
                archived = self._checkpoint(archive, phase="archived")
                self._jobs.update_state(
                    job.id,
                    status="running",
                    progress=95,
                    checkpoint=archived,
                )
                self._jobs.append_event(
                    job_id=job.id,
                    event_type="backup.archived",
                    progress=95,
                    payload={
                        "backup_id": archive.id,
                        "byte_size": archive.byte_size,
                        "package_sha256": archive.package_sha256,
                    },
                )
        checkpoint = self._checkpoint(archive, phase="complete")
        result = {
            "backup_id": archive.id,
            "kind": archive.kind,
            "format_version": archive.format_version,
            "byte_size": archive.byte_size,
            "package_sha256": archive.package_sha256,
            "manifest_sha256": archive.manifest_sha256,
            "object_count": archive.object_count,
            "encrypted": archive.encrypted,
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
            event_type="backup.succeeded",
            progress=100,
            payload=result,
        )

    def _finish_cancelled(self, job: JobRecord, *, progress: int) -> None:
        self._discard_password_lease(job.id)
        result = {"cancelled": True}
        checkpoint = {"phase": "cancelled"}
        self._jobs.update_state(
            job.id,
            status="cancelled",
            progress=progress,
            result=result,
            checkpoint=checkpoint,
            error_code="cancelled",
            error_message="The backup was cancelled before publication",
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="backup.cancelled",
            progress=progress,
            payload=result,
        )

    def _finish_failed(self, job: JobRecord, *, code: str, retryable: bool) -> None:
        self._discard_password_lease(job.id)
        messages = {
            "insufficient_space": "Not enough free space to create and verify the backup",
            "integrity": "Backup inputs changed or failed integrity validation",
            "unsafe_path": "Backup stopped because a configured path was unsafe",
            "io_error": "Backup storage was temporarily unavailable",
            "secret_unavailable": "The encrypted backup password must be entered again",
            "encryption_failed": "The backup could not be encrypted safely",
            "internal_error": "The backup failed unexpectedly",
        }
        result = {"retryable": retryable}
        checkpoint = {"phase": "failed"}
        current = self._jobs.get(job.id)
        progress = max(5, current.progress if current is not None else job.progress)
        self._jobs.update_state(
            job.id,
            status="failed",
            progress=progress,
            result=result,
            checkpoint=checkpoint,
            error_code=code,
            error_message=messages[code],
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="backup.failed",
            progress=progress,
            payload={"code": code, "retryable": retryable},
        )

    def _interrupt_for_shutdown(self, job_id: str) -> None:
        current = self._jobs.get(job_id)
        if current is None or current.status in {"succeeded", "cancelled"}:
            return
        self._jobs.update_state(
            job_id,
            status="queued",
            progress=current.progress,
            checkpoint={"phase": "interrupted"},
        )

    def _prune_automatic_archives(self) -> None:
        deleted_at = _aware_utc(self._clock())
        for record in self._archives.prunable_automatic(keep=self._automatic_retention):
            try:
                verified = self._open_verified(record)
                candidate = Path(verified.stream.name)
                descriptor = os.fstat(verified.stream.fileno())
                verified.stream.close()
                before = os.lstat(candidate)
                if (
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ino,
                ) != (
                    descriptor.st_size,
                    descriptor.st_mtime_ns,
                    descriptor.st_ino,
                ):
                    continue
                candidate.unlink()
                self._archives.mark_deleted(record.id, deleted_at=deleted_at)
            except (BackupUnavailableError, OSError, KeyError):
                continue

    def _prune_after_success(self, job_id: str) -> None:
        try:
            self._prune_automatic_archives()
        except Exception:
            with suppress(Exception):
                self._jobs.append_event(
                    job_id=job_id,
                    event_type="backup.retention_warning",
                    progress=100,
                    payload={"code": "retention_deferred"},
                )

    def _open_verified(self, record: BackupArchiveRecord) -> BackupDownload:
        candidate = self._safe_record_path(record)
        try:
            before = os.lstat(candidate)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise BackupUnavailableError("Backup is unavailable")
            stream = candidate.open("rb")
        except (OSError, BackupUnavailableError) as error:
            raise BackupUnavailableError("Backup is unavailable") from error
        try:
            descriptor = os.fstat(stream.fileno())
            if not stat.S_ISREG(descriptor.st_mode) or descriptor.st_size != record.byte_size:
                raise BackupUnavailableError("Backup is unavailable")
            before_identity = (before.st_size, before.st_mtime_ns, before.st_ino)
            descriptor_identity = (
                descriptor.st_size,
                descriptor.st_mtime_ns,
                descriptor.st_ino,
            )
            if before_identity != descriptor_identity:
                raise BackupUnavailableError("Backup is unavailable")
            digest = sha256()
            while chunk := stream.read(_READ_SIZE):
                digest.update(chunk)
            after = os.lstat(candidate)
            after_identity = (after.st_size, after.st_mtime_ns, after.st_ino)
            if (
                before_identity != descriptor_identity
                or before_identity != after_identity
                or digest.hexdigest() != record.package_sha256
            ):
                raise BackupUnavailableError("Backup is unavailable")
            stream.seek(0)
            return BackupDownload(
                stream=stream,
                filename=record.relative_path,
                byte_size=record.byte_size,
                package_sha256=record.package_sha256,
                encrypted=record.encrypted,
            )
        except BaseException:
            stream.close()
            raise

    def _safe_record_path(self, record: BackupArchiveRecord) -> Path:
        root = self._paths.backups.resolve()
        relative = record.relative_path
        if (
            not relative
            or Path(relative).name != relative
            or "/" in relative
            or "\\" in relative
            or "\x00" in relative
        ):
            raise BackupUnavailableError("Backup is unavailable")
        candidate = root / relative
        try:
            if candidate.is_symlink() or candidate.is_junction():
                raise BackupUnavailableError("Backup is unavailable")
            resolved = candidate.resolve(strict=True)
            if resolved.parent != root or resolved != candidate.absolute():
                raise BackupUnavailableError("Backup is unavailable")
        except (OSError, ValueError) as error:
            raise BackupUnavailableError("Backup is unavailable") from error
        return candidate

    def _destination(self, job: JobRecord, *, kind: str) -> Path:
        created_at = _stored_utc(job.created_at)
        stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
        if bool(job.payload.get("encrypted")):
            # The authenticated wrapper is self-describing; a compact filename
            # leaves headroom under legacy Windows MAX_PATH configurations.
            return self._paths.backups / f"paper-reader-{kind}-{job.id}.prb.enc"
        return self._paths.backups / (
            f"paper-reader-{kind}-{stamp}-{job.id}.paper-reader-backup"
        )

    @staticmethod
    def _require_fresh_destination(destination: Path) -> None:
        # Never delete a colliding file merely because its predictable name contains
        # a Job UUID.  A future strict package validator may adopt a verified orphan;
        # until then, preserving user bytes is safer than guessing ownership.
        if os.path.lexists(destination):
            raise BackupIntegrityError("Backup destination is already occupied")

    @staticmethod
    def _hash_path(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(_READ_SIZE):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _checkpoint(record: BackupArchiveRecord, *, phase: str) -> dict[str, object]:
        return {
            "phase": phase,
            "backup_id": record.id,
            "byte_size": record.byte_size,
            "package_sha256": record.package_sha256,
            "manifest_sha256": record.manifest_sha256,
        }

    @staticmethod
    def _record_matches_result(
        record: BackupArchiveRecord,
        result: BackupArchiveResult,
        *,
        byte_size: int,
        encrypted: bool,
    ) -> bool:
        return (
            record.byte_size == byte_size
            and record.package_sha256 == result.package_sha256
            and record.manifest_sha256 == result.manifest_sha256
            and record.format_version == result.manifest.format_version
            and record.schema_revision == result.manifest.schema_revision
            and record.object_count == len(result.manifest.objects)
            and record.encrypted is encrypted
        )

    def _require_job(self, job_id: str) -> JobRecord:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.kind != BACKUP_JOB_KIND:
            raise ValueError("job is not a backup command")
        return job

    @staticmethod
    def _read_payload(job: JobRecord) -> BackupJobPayload:
        payload = job.payload
        if set(payload) != {"kind", "encrypted"}:
            raise ValueError("backup job payload is invalid")
        try:
            kind = BackupKind(payload["kind"])
        except (TypeError, ValueError) as error:
            raise ValueError("backup job payload is invalid") from error
        if type(payload["encrypted"]) is not bool:
            raise ValueError("backup job payload is invalid")
        return BackupJobPayload(kind=kind.value, encrypted=payload["encrypted"])

    @staticmethod
    def _classify_backup_error(error: BackupError) -> tuple[str, bool]:
        if isinstance(error, BackupInsufficientSpaceError):
            return "insufficient_space", True
        if isinstance(error, UnsafeBackupPathError):
            return "unsafe_path", False
        if isinstance(error, BackupIntegrityError):
            return "integrity", False
        return "internal_error", False

    @staticmethod
    def _classify_crypto_error(error: BackupCryptoError) -> tuple[str, bool]:
        if error.code == "secret_lease_unavailable":
            return "secret_unavailable", True
        if error.code in {"cancelled", "source_changed"}:
            return "integrity", False
        return "encryption_failed", False

    def _replace_password_lease(
        self,
        job_id: str,
        password: PasswordMaterial,
    ) -> None:
        lease_id = self._passwords.issue(password)
        with self._password_lock:
            previous = self._password_leases.pop(job_id, None)
            self._password_leases[job_id] = lease_id
        if previous is not None:
            self._passwords.discard(previous)

    def _discard_password_lease(self, job_id: str) -> None:
        with self._password_lock:
            lease_id = self._password_leases.pop(job_id, None)
        if lease_id is not None:
            self._passwords.discard(lease_id)


class RestorePreviewError(RuntimeError):
    """A stable, path-free preview or confirmation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RestorePreview:
    id: str
    validation_job_id: str
    fingerprint: str
    package_sha256: str
    manifest_sha256: str
    encrypted: bool
    source_format_version: str
    source_app_version: str
    source_schema_revision: str
    object_count: int
    payload_bytes: int
    included: tuple[str, ...]
    excluded: tuple[str, ...]
    created_at: datetime
    expires_at: datetime


@dataclass(slots=True)
class _RestorePreviewEntry:
    preview: RestorePreview
    package: ValidatedRestorePackage
    confirmed_idempotency_key: str | None = None
    confirmation_job_id: str | None = None


@dataclass(frozen=True, slots=True)
class _RestoreValidationInput:
    upload: Path
    password_lease_id: str | None


def _preview_error(code: str) -> RestorePreviewError:
    messages = {
        "preview_unavailable": "The restore preview is unavailable",
        "preview_expired": "The restore preview expired and must be validated again",
        "stale_preview": "The confirmed fingerprint no longer matches the restore preview",
        "already_confirmed": "This restore preview was already confirmed",
        "restore_already_pending": "Another restore is already waiting for restart",
        "invalid_password": "A password is required only for an encrypted backup",
        "upload_unavailable": "The uploaded backup is unavailable",
    }
    return RestorePreviewError(code, messages[code])


class RestoreService:
    """Validate uploads in isolation and schedule fingerprint-confirmed restores."""

    def __init__(
        self,
        *,
        paths: AppPaths,
        jobs: JobRepository,
        archives: BackupRepository,
        passwords: PasswordLeaseStore | None = None,
        clock: Callable[[], datetime] = utc_now,
        preview_ttl: timedelta = RESTORE_PREVIEW_TTL,
    ) -> None:
        if preview_ttl <= timedelta(0):
            raise ValueError("preview_ttl must be positive")
        self._paths = paths
        self._jobs = jobs
        self._archives = archives
        self._passwords = passwords or PasswordLeaseStore()
        self._clock = clock
        self._preview_ttl = preview_ttl
        self._lock = threading.RLock()
        self._inputs: dict[str, _RestoreValidationInput] = {}
        self._previews: dict[str, _RestorePreviewEntry] = {}
        self._interrupt_abandoned_validations()
        self._cleanup_abandoned_restore_files()

    def stage_upload(self, source: BinaryIO) -> Path:
        root = self._upload_root()
        destination = root / f"upload-{uuid4()}.package"
        copied = 0
        try:
            with destination.open("xb") as output:
                while chunk := source.read(_READ_SIZE):
                    copied += len(chunk)
                    if copied > MAX_PACKAGE_BYTES:
                        raise _preview_error("upload_unavailable")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if copied <= 0:
                raise _preview_error("upload_unavailable")
            return destination
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    def discard_upload(self, upload: Path) -> None:
        try:
            candidate = self._validated_upload(upload)
        except RestorePreviewError:
            return
        candidate.unlink(missing_ok=True)

    def create_validation_job(
        self,
        *,
        upload: Path,
        password: PasswordMaterial | None,
        idempotency_key: str,
    ) -> JobRecord:
        candidate = self._validated_upload(upload)
        key = idempotency_key.strip()
        if not key or len(key) > 134:
            self.discard_upload(candidate)
            raise ValueError("idempotency key must contain 1-134 characters")
        encrypted = is_encrypted_backup(candidate)
        if encrypted != (password is not None):
            self.discard_upload(candidate)
            raise _preview_error("invalid_password")
        payload = {"encrypted": encrypted}
        namespaced_key = f"backup.restore.validate:{key}"
        existing = self._jobs.get_by_idempotency_key(namespaced_key)
        if existing is not None:
            if existing.kind != RESTORE_VALIDATION_JOB_KIND or existing.payload != payload:
                raise ValueError("idempotency key belongs to a different restore command")
            self.discard_upload(candidate)
            return existing

        lease_id: str | None = None
        try:
            if password is not None:
                try:
                    lease_id = self._passwords.issue(password)
                except BackupCryptoError:
                    raise _preview_error("invalid_password") from None
            job = self._jobs.create(
                kind=RESTORE_VALIDATION_JOB_KIND,
                payload=payload,
                idempotency_key=namespaced_key,
            )
            if job.kind != RESTORE_VALIDATION_JOB_KIND or job.payload != payload:
                raise ValueError("idempotency key belongs to a different restore command")
            with self._lock:
                if job.id in self._inputs:
                    self.discard_upload(candidate)
                    if lease_id is not None:
                        self._passwords.discard(lease_id)
                else:
                    self._inputs[job.id] = _RestoreValidationInput(
                        upload=candidate,
                        password_lease_id=lease_id,
                    )
            return job
        except BaseException:
            self.discard_upload(candidate)
            if lease_id is not None:
                self._passwords.discard(lease_id)
            raise

    async def execute_validation(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.kind != RESTORE_VALIDATION_JOB_KIND:
            raise KeyError(job_id)
        if job.status in {"succeeded", "failed", "cancelled"}:
            return
        with self._lock:
            restore_input = self._inputs.pop(job.id, None)
        if restore_input is None:
            self._finish_validation_failure(
                job,
                code="upload_unavailable",
                message="The uploaded backup is unavailable",
            )
            return
        self._jobs.update_state(
            job.id,
            status="running",
            progress=10,
            checkpoint={"phase": "validating"},
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="backup.restore_validation.started",
            progress=10,
            payload={"encrypted": bool(job.payload["encrypted"])},
        )

        stopping = threading.Event()

        def cancelled() -> bool:
            current = self._jobs.get(job.id)
            return stopping.is_set() or current is None or current.cancel_requested

        work = asyncio.create_task(
            asyncio.to_thread(
                self._validate_input,
                restore_input,
                cancelled,
            )
        )
        try:
            package = await asyncio.shield(work)
            preview = self._store_preview(job.id, package)
        except asyncio.CancelledError:
            stopping.set()
            try:
                completed = await asyncio.shield(work)
            except Exception:
                pass
            else:
                completed.cleanup()
            raise
        except (RestoreValidationError, BackupCryptoError) as error:
            self._finish_validation_failure(job, code=error.code, message=str(error))
            return
        except Exception:
            self._finish_validation_failure(
                job,
                code="validation_failed",
                message="The uploaded backup could not be validated",
            )
            return
        finally:
            restore_input.upload.unlink(missing_ok=True)

        result = {
            "preview_id": preview.id,
            "encrypted": preview.encrypted,
            "object_count": preview.object_count,
            "payload_bytes": preview.payload_bytes,
            "expires_at": utc_text(preview.expires_at),
        }
        self._jobs.update_state(
            job.id,
            status="succeeded",
            progress=100,
            result=result,
            checkpoint={"phase": "preview_ready"},
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="backup.restore_validation.succeeded",
            progress=100,
            payload=result,
        )

    def _validate_input(
        self,
        restore_input: _RestoreValidationInput,
        cancelled: Callable[[], bool],
    ) -> ValidatedRestorePackage:
        validator = RestorePackageValidator(paths=self._paths)
        if restore_input.password_lease_id is None:
            return validator.validate(
                restore_input.upload,
                cancelled=cancelled,
            )
        with self._passwords.consume(restore_input.password_lease_id) as secret:
            return validator.validate(
                restore_input.upload,
                password=secret,
                cancelled=cancelled,
            )

    def get_preview(self, preview_id: str) -> RestorePreview:
        with self._lock:
            entry = self._previews.get(preview_id)
            if entry is None:
                raise _preview_error("preview_unavailable")
            if self._now() >= entry.preview.expires_at and entry.confirmation_job_id is None:
                self._previews.pop(preview_id, None)
                entry.package.cleanup()
                raise _preview_error("preview_expired")
            return entry.preview

    def confirm(
        self,
        *,
        preview_id: str,
        expected_fingerprint: str,
        idempotency_key: str,
    ) -> JobRecord:
        key = idempotency_key.strip()
        if not key or len(key) > 133:
            raise ValueError("idempotency key must contain 1-133 characters")
        namespaced_key = f"backup.restore.confirm:{key}"
        with self._lock:
            entry = self._previews.get(preview_id)
            if entry is None:
                raise _preview_error("preview_unavailable")
            if self._now() >= entry.preview.expires_at and entry.confirmation_job_id is None:
                self._previews.pop(preview_id, None)
                entry.package.cleanup()
                raise _preview_error("preview_expired")
            if entry.confirmation_job_id is not None:
                if entry.confirmed_idempotency_key == namespaced_key:
                    duplicate = self._jobs.get(entry.confirmation_job_id)
                    if duplicate is not None:
                        return duplicate
                raise _preview_error("already_confirmed")
            if not compare_digest(entry.preview.fingerprint, expected_fingerprint):
                raise _preview_error("stale_preview")
            payload = {"preview_id": preview_id}
            job = self._jobs.create(
                kind=RESTORE_CONFIRMATION_JOB_KIND,
                payload=payload,
                idempotency_key=namespaced_key,
            )
            if job.kind != RESTORE_CONFIRMATION_JOB_KIND or job.payload != payload:
                raise ValueError("idempotency key belongs to a different restore command")
            try:
                pending = schedule_pending_restore(
                    paths=self._paths,
                    package=entry.package,
                    restore_id=str(uuid4()),
                    expected_fingerprint=expected_fingerprint,
                    confirmed_at=self._now(),
                )
            except PendingRestoreError as error:
                self._jobs.update_state(
                    job.id,
                    status="failed",
                    progress=100,
                    result={"retryable": False},
                    checkpoint={"phase": "confirmation_failed"},
                    error_code=error.code,
                    error_message=str(error),
                )
                if error.code == "restore_already_pending":
                    raise _preview_error("restore_already_pending") from None
                raise _preview_error("stale_preview") from None
            entry.confirmed_idempotency_key = namespaced_key
            entry.confirmation_job_id = job.id
            result = {
                "pending_restart": True,
                "restore_id": pending.restore_id,
                "package_sha256": pending.package_sha256,
            }
            terminal = self._jobs.update_state(
                job.id,
                status="pending_restart",
                progress=100,
                result=result,
                checkpoint={"phase": "pending_restart"},
            )
            self._jobs.append_event(
                job_id=job.id,
                event_type="backup.restore.pending_restart",
                progress=100,
                payload=result,
            )
            return terminal

    def close(self) -> None:
        with self._lock:
            inputs = tuple(self._inputs.values())
            previews = tuple(self._previews.values())
            self._inputs.clear()
            self._previews.clear()
        for restore_input in inputs:
            restore_input.upload.unlink(missing_ok=True)
            if restore_input.password_lease_id is not None:
                self._passwords.discard(restore_input.password_lease_id)
        for entry in previews:
            if entry.confirmation_job_id is None:
                entry.package.cleanup()
        self._passwords.close()

    def _store_preview(
        self,
        validation_job_id: str,
        package: ValidatedRestorePackage,
    ) -> RestorePreview:
        created_at = self._now()
        preview = RestorePreview(
            id=validation_job_id,
            validation_job_id=validation_job_id,
            fingerprint=staged_restore_fingerprint(paths=self._paths, package=package),
            package_sha256=package.package_sha256,
            manifest_sha256=package.manifest_sha256,
            encrypted=package.encrypted,
            source_format_version=package.manifest.format_version,
            source_app_version=package.manifest.app_version,
            source_schema_revision=package.manifest.schema_revision,
            object_count=package.object_count,
            payload_bytes=package.payload_bytes,
            included=package.manifest.included,
            excluded=package.manifest.excluded,
            created_at=created_at,
            expires_at=created_at + self._preview_ttl,
        )
        with self._lock:
            replaced = self._previews.get(preview.id)
            if replaced is not None:
                package.cleanup()
                return replaced.preview
            self._previews[preview.id] = _RestorePreviewEntry(
                preview=preview,
                package=package,
            )
        return preview

    def _finish_validation_failure(
        self,
        job: JobRecord,
        *,
        code: str,
        message: str,
    ) -> None:
        self._jobs.update_state(
            job.id,
            status="failed",
            progress=100,
            result={"retryable": False},
            checkpoint={"phase": "validation_failed"},
            error_code=code,
            error_message=message,
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="backup.restore_validation.failed",
            progress=100,
            payload={"code": code, "retryable": False},
        )

    def _upload_root(self) -> Path:
        state_root = self._paths.state_root.resolve()
        root = state_root / ".restore-uploads"
        root.mkdir(exist_ok=True)
        try:
            if (
                root.resolve(strict=True) != root
                or root.parent != state_root
                or root.is_symlink()
                or root.is_junction()
            ):
                raise _preview_error("upload_unavailable")
        except OSError:
            raise _preview_error("upload_unavailable") from None
        return root

    def _interrupt_abandoned_validations(self) -> None:
        for job_id in self._jobs.list_resumable_ids(kind=RESTORE_VALIDATION_JOB_KIND):
            self._jobs.update_state(
                job_id,
                status="failed",
                progress=100,
                result={"retryable": True},
                checkpoint={"phase": "validation_interrupted"},
                error_code="upload_unavailable",
                error_message="The uploaded backup must be selected again",
            )

    def _cleanup_abandoned_restore_files(self) -> None:
        if pending_restore_path(self._paths).exists() or restore_journal_path(
            self._paths
        ).exists():
            return
        upload_root = self._upload_root()
        for candidate in upload_root.iterdir():
            try:
                if (
                    candidate.parent == upload_root
                    and candidate.is_file()
                    and not candidate.is_symlink()
                    and not candidate.is_junction()
                ):
                    candidate.unlink()
            except OSError:
                continue
        for root in (self._paths.state_root.resolve(), self._paths.library_root.resolve()):
            for candidate in root.glob(".restore-stage-*"):
                try:
                    if (
                        candidate.parent == root
                        and candidate.resolve(strict=True) == candidate
                        and candidate.is_dir()
                        and not candidate.is_symlink()
                        and not candidate.is_junction()
                    ):
                        shutil.rmtree(candidate)
                except OSError:
                    continue

    def _validated_upload(self, upload: Path) -> Path:
        root = self._upload_root()
        candidate = Path(upload)
        try:
            if (
                not candidate.is_absolute()
                or candidate.parent != root
                or candidate.resolve(strict=True) != candidate
                or candidate.is_symlink()
                or candidate.is_junction()
                or not candidate.is_file()
            ):
                raise _preview_error("upload_unavailable")
        except OSError:
            raise _preview_error("upload_unavailable") from None
        return candidate

    def _now(self) -> datetime:
        return _aware_utc(self._clock())


class WeeklyBackupScheduler:
    """Check a seven-day policy while the process is open; never runs as a service."""

    def __init__(
        self,
        *,
        service: BackupService,
        archives: BackupRepository,
        clock: Callable[[], datetime] = utc_now,
        automatic_interval: timedelta = AUTOMATIC_INTERVAL,
        check_interval: timedelta = SCHEDULER_CHECK_INTERVAL,
    ) -> None:
        if automatic_interval <= timedelta(0) or check_interval <= timedelta(0):
            raise ValueError("scheduler intervals must be positive")
        self._service = service
        self._archives = archives
        self._clock = clock
        self._automatic_interval = automatic_interval
        self._check_interval = check_interval
        self._last_checked: datetime | None = None
        self._lock = threading.Lock()

    @property
    def check_interval(self) -> timedelta:
        return self._check_interval

    def check(self, *, now: datetime | None = None) -> JobRecord | None:
        checked_at = _aware_utc(now or self._clock())
        with self._lock:
            if (
                self._last_checked is not None
                and checked_at < self._last_checked + self._check_interval
            ):
                return None
            self._last_checked = checked_at
        latest = self._archives.latest_automatic()
        if latest is not None:
            due_at = _stored_utc(latest.created_at) + self._automatic_interval
            if checked_at < due_at:
                return None
        else:
            started_at = _stored_utc(
                self._archives.schedule_started_at(default=checked_at)
            )
            if checked_at < started_at + self._automatic_interval:
                return None
        active = self._archives.active_automatic_job()
        if active is not None:
            return active
        slot_seconds = int(self._check_interval.total_seconds())
        slot = datetime.fromtimestamp(
            int(checked_at.timestamp()) // slot_seconds * slot_seconds,
            tz=UTC,
        )
        return self._service.create_job(
            kind=BackupKind.AUTOMATIC,
            idempotency_key=f"automatic:{slot.strftime('%Y%m%dT%H%M%SZ')}",
        )
