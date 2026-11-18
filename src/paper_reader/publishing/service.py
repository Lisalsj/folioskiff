"""Durable publication commands binding an immutable snapshot to one Git target."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Protocol, TypedDict, cast

from pydantic import BaseModel, ConfigDict

from paper_reader.jobs.models import JobRecord
from paper_reader.jobs.repository import JobRepository
from paper_reader.publishing.git import (
    GitPublicationResult,
    GitPublishCancelled,
    GitPublishError,
    GitPublishingDiagnostic,
)
from paper_reader.publishing.models import (
    PublicationPreview,
    PublicationSelection,
    PublishedSnapshot,
)
from paper_reader.publishing.privacy import PublishingPrivacyBoundary
from paper_reader.publishing.repository import (
    PublishedSnapshotIntegrityError,
    PublishedSnapshotRepository,
)

PUBLISH_JOB_KIND = "publishing.publish"
PUBLICATION_FILES = (
    ".github/workflows/pages.yml",
    "site/data.json",
    "site/index.html",
    "site/manifest.json",
)
PUBLIC_INCLUDED_FIELDS = (
    "curated title and public introduction",
    "canonical paper title, authors, abstract, DOI and publication date",
    "latest objective quality score summary when available",
    "explicitly selected formal analysis report when available",
)
PUBLIC_EXCLUDED_FIELDS = (
    "PDF and extracted full text",
    "annotations, private notes and reading progress",
    "paper conversations and preference events",
    "credentials",
    "complete prompts, failure drafts and model diagnostics",
    "private score evidence and source raw payloads",
    "local filesystem paths",
)


class SnapshotPublisher(Protocol):
    def diagnose(self) -> GitPublishingDiagnostic: ...

    def publish(
        self,
        snapshot: PublishedSnapshot,
        *,
        operation_id: str,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> GitPublicationResult: ...


class PublishingConflictError(RuntimeError):
    pass


class PublishingNotReadyError(RuntimeError):
    pass


class PublishingPreviewEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    preview: PublicationPreview
    git: GitPublishingDiagnostic
    files: tuple[str, ...] = PUBLICATION_FILES
    included_fields: tuple[str, ...] = PUBLIC_INCLUDED_FIELDS
    excluded_fields: tuple[str, ...] = PUBLIC_EXCLUDED_FIELDS


class GitSettingsConfirmation(BaseModel):
    model_config = ConfigDict(frozen=True)

    confirmed: bool = True
    git: GitPublishingDiagnostic


class _PublishPayload(TypedDict):
    snapshot_id: str
    snapshot_sha256: str
    preview_fingerprint: str
    target_fingerprint: str
    retry_of_job_id: str | None


class PublishingService:
    def __init__(
        self,
        *,
        jobs: JobRepository,
        snapshots: PublishedSnapshotRepository,
        privacy: PublishingPrivacyBoundary,
        publisher: SnapshotPublisher,
    ) -> None:
        self._jobs = jobs
        self._snapshots = snapshots
        self._privacy = privacy
        self._publisher = publisher
        self._confirmed_target: str | None = None
        self._confirmation_lock = threading.Lock()

    def diagnose(self) -> GitPublishingDiagnostic:
        diagnostic = self._publisher.diagnose()
        with self._confirmation_lock:
            if self._confirmed_target != diagnostic.target_fingerprint:
                self._confirmed_target = None
        return diagnostic

    def preview(self, selection: PublicationSelection) -> PublishingPreviewEnvelope:
        return PublishingPreviewEnvelope(
            preview=self._privacy.preview(selection),
            git=self.diagnose(),
        )

    def confirm_settings(self, target_fingerprint: str) -> GitSettingsConfirmation:
        diagnostic = self._require_target(target_fingerprint, require_confirmation=False)
        with self._confirmation_lock:
            self._confirmed_target = target_fingerprint
        return GitSettingsConfirmation(git=diagnostic)

    def create_job(
        self,
        *,
        preview: PublicationPreview,
        target_fingerprint: str,
        idempotency_key: str,
    ) -> JobRecord:
        key = _idempotency_key(idempotency_key, maximum=139)
        namespaced = f"publishing.publish:{key}"
        existing = self._jobs.get_by_idempotency_key(namespaced)
        if existing is not None:
            if (
                existing.kind == PUBLISH_JOB_KIND
                and existing.payload.get("preview_fingerprint") == preview.fingerprint
                and existing.payload.get("target_fingerprint") == target_fingerprint
                and existing.payload.get("retry_of_job_id") is None
            ):
                return existing
            raise PublishingConflictError("idempotency key is bound to another publication command")
        self._require_target(target_fingerprint, require_confirmation=True)
        snapshot = self._snapshots.freeze(preview)
        payload: _PublishPayload = {
            "snapshot_id": snapshot.id,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "preview_fingerprint": preview.fingerprint,
            "target_fingerprint": target_fingerprint,
            "retry_of_job_id": None,
        }
        job = self._jobs.create(
            kind=PUBLISH_JOB_KIND,
            payload=cast(dict[str, object], payload),
            idempotency_key=namespaced,
        )
        if job.kind != PUBLISH_JOB_KIND or job.payload != payload:
            raise PublishingConflictError("idempotency key is bound to another publication command")
        return job

    def retry_job(
        self,
        job_id: str,
        *,
        idempotency_key: str,
    ) -> JobRecord:
        original = self._jobs.get(job_id)
        if original is None:
            raise KeyError(job_id)
        if original.kind != PUBLISH_JOB_KIND:
            raise PublishingConflictError("Job is not a publication Job")
        retryable = bool((original.result or {}).get("retryable"))
        if original.status not in {"failed", "cancelled"} or not retryable:
            raise PublishingConflictError("Publication Job is not retryable")
        original_payload = self._validated_payload(original.payload)
        key = _idempotency_key(idempotency_key, maximum=85)
        namespaced = f"publishing.retry:{original.id}:{key}"
        existing = self._jobs.get_by_idempotency_key(namespaced)
        if existing is not None:
            if (
                existing.kind == PUBLISH_JOB_KIND
                and existing.payload.get("snapshot_id") == original_payload["snapshot_id"]
                and existing.payload.get("retry_of_job_id") == original.id
            ):
                return existing
            raise PublishingConflictError("idempotency key is bound to another retry")
        diagnostic = self._require_confirmed_target()
        try:
            snapshot = self._snapshots.require(original_payload["snapshot_id"])
        except (KeyError, PublishedSnapshotIntegrityError) as error:
            raise PublishingConflictError("Frozen publication snapshot is unavailable") from error
        if snapshot.snapshot_sha256 != original_payload["snapshot_sha256"]:
            raise PublishingConflictError("Frozen publication snapshot changed")
        payload: _PublishPayload = {
            **original_payload,
            "target_fingerprint": diagnostic.target_fingerprint or "",
            "retry_of_job_id": original.id,
        }
        return self._jobs.create(
            kind=PUBLISH_JOB_KIND,
            payload=cast(dict[str, object], payload),
            idempotency_key=namespaced,
        )

    def resumable_job_ids(self) -> tuple[str, ...]:
        return self._jobs.list_resumable_ids(kind=PUBLISH_JOB_KIND)

    async def execute(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.kind != PUBLISH_JOB_KIND:
            raise ValueError("Job is not a publication Job")
        if job.status in {"succeeded", "failed", "cancelled"}:
            return
        try:
            payload = self._validated_payload(job.payload)
            snapshot = self._snapshots.require(payload["snapshot_id"])
        except (KeyError, ValueError, PublishedSnapshotIntegrityError):
            self._finish_failed(
                job.id,
                snapshot_id=str(job.payload.get("snapshot_id", "")),
                code="snapshot_unavailable",
                retryable=False,
            )
            return
        if snapshot.snapshot_sha256 != payload["snapshot_sha256"]:
            self._finish_failed(
                job.id,
                snapshot_id=snapshot.id,
                code="snapshot_integrity",
                retryable=False,
            )
            return
        diagnostic = self._publisher.diagnose()
        if not diagnostic.enabled or diagnostic.target_fingerprint != payload["target_fingerprint"]:
            self._finish_failed(
                job.id,
                snapshot_id=snapshot.id,
                code="target_changed",
                retryable=False,
            )
            return
        if job.cancel_requested:
            self._finish_cancelled(job.id, snapshot.id)
            return

        checkpoint = {"phase": "worktree", "snapshot_id": snapshot.id}
        self._jobs.update_state(
            job.id,
            status="running",
            progress=5,
            checkpoint=checkpoint,
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="publishing.started",
            progress=5,
            payload={"snapshot_id": snapshot.id, "branch": "published"},
        )
        stopping = threading.Event()

        def cancelled() -> bool:
            current = self._jobs.get(job.id)
            return stopping.is_set() or current is None or current.cancel_requested

        work = asyncio.create_task(
            asyncio.to_thread(
                self._publisher.publish,
                snapshot,
                operation_id=job.id,
                cancelled=cancelled,
            )
        )
        try:
            result = await asyncio.shield(work)
        except asyncio.CancelledError:
            stopping.set()
            try:
                completed = await asyncio.shield(work)
            except Exception:
                self._jobs.update_state(
                    job.id,
                    status="queued",
                    progress=5,
                    checkpoint={"phase": "interrupted", "snapshot_id": snapshot.id},
                )
            else:
                self._finish_success(job.id, completed)
            raise
        except GitPublishCancelled:
            self._finish_cancelled(job.id, snapshot.id)
            return
        except GitPublishError as error:
            self._finish_failed(
                job.id,
                snapshot_id=snapshot.id,
                code=error.code,
                retryable=error.retryable,
            )
            return
        except Exception:
            self._finish_failed(
                job.id,
                snapshot_id=snapshot.id,
                code="internal_error",
                retryable=False,
            )
            return
        self._finish_success(job.id, result)

    def _require_target(
        self,
        target_fingerprint: str,
        *,
        require_confirmation: bool,
    ) -> GitPublishingDiagnostic:
        diagnostic = self.diagnose()
        if not diagnostic.enabled or diagnostic.target_fingerprint is None:
            raise PublishingNotReadyError(diagnostic.reason or "Git publishing is unavailable")
        if target_fingerprint != diagnostic.target_fingerprint:
            raise PublishingConflictError("Git publishing settings changed; review them again")
        if require_confirmation:
            with self._confirmation_lock:
                if self._confirmed_target != target_fingerprint:
                    raise PublishingConflictError("Git publishing settings are not confirmed")
        return diagnostic

    def _require_confirmed_target(self) -> GitPublishingDiagnostic:
        with self._confirmation_lock:
            target = self._confirmed_target
        if target is None:
            raise PublishingConflictError("Git publishing settings are not confirmed")
        return self._require_target(target, require_confirmation=True)

    @staticmethod
    def _validated_payload(value: dict[str, object]) -> _PublishPayload:
        required = {
            "snapshot_id",
            "snapshot_sha256",
            "preview_fingerprint",
            "target_fingerprint",
            "retry_of_job_id",
        }
        if set(value) != required:
            raise ValueError("Invalid publication Job payload")
        for key in required - {"retry_of_job_id"}:
            if not isinstance(value[key], str) or not value[key]:
                raise ValueError("Invalid publication Job payload")
        retry = value["retry_of_job_id"]
        if retry is not None and not isinstance(retry, str):
            raise ValueError("Invalid publication Job payload")
        return cast(_PublishPayload, dict(value))

    def _finish_success(self, job_id: str, result: GitPublicationResult) -> None:
        public_result = result.model_dump(mode="json")
        self._jobs.update_state(
            job_id,
            status="succeeded",
            progress=100,
            result=public_result,
            checkpoint={
                "phase": "complete",
                "snapshot_id": result.snapshot_id,
                "commit_sha": result.commit_sha,
            },
        )
        self._jobs.append_event(
            job_id=job_id,
            event_type="publishing.succeeded",
            progress=100,
            payload={
                "snapshot_id": result.snapshot_id,
                "commit_sha": result.commit_sha,
                "branch": result.branch,
            },
        )

    def _finish_failed(
        self,
        job_id: str,
        *,
        snapshot_id: str,
        code: str,
        retryable: bool,
    ) -> None:
        result = {"snapshot_id": snapshot_id, "retryable": retryable}
        self._jobs.update_state(
            job_id,
            status="failed",
            progress=100,
            result=result,
            checkpoint={"phase": "failed", "snapshot_id": snapshot_id},
            error_code=code,
            error_message="Publication did not complete; the frozen snapshot was preserved.",
        )
        self._jobs.append_event(
            job_id=job_id,
            event_type="publishing.failed",
            progress=100,
            payload={"snapshot_id": snapshot_id, "code": code, "retryable": retryable},
        )

    def _finish_cancelled(self, job_id: str, snapshot_id: str) -> None:
        result = {"snapshot_id": snapshot_id, "retryable": True}
        self._jobs.update_state(
            job_id,
            status="cancelled",
            progress=100,
            result=result,
            checkpoint={"phase": "cancelled", "snapshot_id": snapshot_id},
        )
        self._jobs.append_event(
            job_id=job_id,
            event_type="publishing.cancelled",
            progress=100,
            payload=result,
        )


def _idempotency_key(value: str, *, maximum: int) -> str:
    key = value.strip()
    if not key or len(key) > maximum:
        raise ValueError(f"idempotency key must contain 1-{maximum} characters")
    return key
