"""Application service for model preflight, jobs, activation, and cleanup."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from paper_reader.assets.downloader import (
    AssetDownloadCancelled,
    AssetDownloader,
    AssetDownloadError,
    AssetDownloadPaused,
)
from paper_reader.assets.manifest import (
    ModelManifest,
    ModelManifestError,
    model_manifest_fingerprint,
    model_manifest_from_payload,
)
from paper_reader.assets.repository import DeletionPreview, ModelAssetRepository
from paper_reader.jobs.models import JobRecord
from paper_reader.jobs.repository import JobRepository

MODEL_INSTALL_JOB_KIND = "models.install"
RuntimeProbe = Callable[[], bool]
ActivationHook = Callable[[ModelManifest], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class ModelAssetView:
    asset_id: str
    capability: Literal["semantic", "ocr"]
    bundle_id: str
    display_name: str
    purpose: str
    version: str
    revision: str
    current_release: bool
    download_size_bytes: int
    license_name: str
    license_url: str
    copyright_notice: str
    source_url: str
    status: str
    integrity: str
    active: bool
    runtime_available: bool
    available: bool
    degradation_reasons: tuple[str, ...]


class ModelAssetService:
    def __init__(
        self,
        *,
        manifests: Sequence[ModelManifest],
        repository: ModelAssetRepository,
        jobs: JobRepository,
        downloader: AssetDownloader,
        runtime_probes: Mapping[str, RuntimeProbe] | None = None,
        activation_hooks: Mapping[str, ActivationHook] | None = None,
    ) -> None:
        if not manifests:
            raise ValueError("at least one model manifest is required")
        self._manifests = {(item.bundle_id, item.revision): item for item in manifests}
        self._current = {item.bundle_id: item for item in manifests}
        self._repository = repository
        self._jobs = jobs
        self._downloader = downloader
        self._runtime_probes = dict(runtime_probes or {})
        self._activation_hooks = dict(activation_hooks or {})
        for manifest in manifests:
            repository.register(manifest)
        for record in repository.list_all():
            if (record.bundle_id, record.revision) in self._manifests:
                continue
            try:
                stored = model_manifest_from_payload(record.manifest_json)
                if (
                    stored.bundle_id != record.bundle_id
                    or stored.revision != record.revision
                    or model_manifest_fingerprint(stored) != record.manifest_sha256
                ):
                    continue
            except ModelManifestError:
                continue
            self._manifests[(stored.bundle_id, stored.revision)] = stored

    def list_assets(self) -> tuple[ModelAssetView, ...]:
        manifests = sorted(
            self._manifests.values(),
            key=lambda item: (item.capability, item.bundle_id, item.revision),
        )
        return tuple(self._view(item) for item in manifests)

    def latest_jobs(self) -> tuple[JobRecord, ...]:
        latest_by_asset: dict[str, JobRecord] = {}
        for job in self._jobs.list_jobs_by_kind(kind=MODEL_INSTALL_JOB_KIND):
            asset_id = job.payload.get("asset_id")
            if isinstance(asset_id, str) and asset_id not in latest_by_asset:
                latest_by_asset[asset_id] = job
        return tuple(latest_by_asset.values())

    def create_install_job(self, *, bundle_id: str, idempotency_key: str) -> JobRecord:
        if not idempotency_key.strip() or len(idempotency_key) > 160:
            raise ValueError("idempotency key is invalid")
        manifest = self._require_current(bundle_id)
        asset = self._repository.register(manifest)
        payload = {
            "asset_id": asset.id,
            "bundle_id": manifest.bundle_id,
            "revision": manifest.revision,
        }
        job = self._jobs.create(
            kind=MODEL_INSTALL_JOB_KIND,
            payload=payload,
            idempotency_key=idempotency_key.strip(),
        )
        if job.kind != MODEL_INSTALL_JOB_KIND or job.payload != payload:
            raise ValueError("idempotency key belongs to a different model command")
        if job.status not in {"succeeded", "cancelled"}:
            self._repository.add_reference(asset.id, kind="job", reference_id=job.id)
        return job

    async def install(self, job_id: str) -> None:
        job, manifest = self._install_context(job_id)
        if job.status in {"succeeded", "cancelled", "paused"}:
            return
        current_progress = max(10, job.progress)
        last_event_progress = current_progress
        if job.cancel_requested:
            self._cancel_install(job, manifest)
            return
        self._repository.update_download(
            job.payload["asset_id"],
            status="downloading",
            integrity="partial" if self._downloader.partial_bytes(manifest) else "missing",
            received_bytes=self._downloader.partial_bytes(manifest),
        )
        self._jobs.update_state(
            job.id,
            status="running",
            progress=current_progress,
            checkpoint={
                "asset_id": job.payload["asset_id"],
                "received_bytes": self._downloader.partial_bytes(manifest),
                "total_bytes": manifest.total_size_bytes,
            },
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="models.install.started",
            progress=current_progress,
            payload={"bundle_id": manifest.bundle_id, "revision": manifest.revision},
        )

        def control() -> Literal["run", "pause", "cancel"]:
            latest = self._jobs.get(job.id)
            if latest is None or latest.cancel_requested:
                return "cancel"
            if latest.pause_requested:
                return "pause"
            return "run"

        def progress(received: int, total: int) -> None:
            nonlocal current_progress, last_event_progress
            current_progress = min(75, 10 + int(received * 65 / max(total, 1)))
            checkpoint = {
                "asset_id": job.payload["asset_id"],
                "received_bytes": received,
                "total_bytes": total,
            }
            self._repository.update_download(
                job.payload["asset_id"],
                status="downloading",
                integrity="partial",
                received_bytes=received,
            )
            self._jobs.update_state(
                job.id,
                status="running",
                progress=current_progress,
                checkpoint=checkpoint,
            )
            if current_progress > last_event_progress:
                self._jobs.append_event(
                    job_id=job.id,
                    event_type="models.install.progress",
                    progress=current_progress,
                    payload=checkpoint,
                )
                last_event_progress = current_progress

        try:
            await self._downloader.download(manifest, control=control, progress=progress)
            if self._downloader.inspect(manifest) != "verified":
                raise AssetDownloadError(
                    "integrity",
                    "The installed model revision did not pass verification",
                    retryable=False,
                )
        except AssetDownloadPaused:
            received = self._downloader.partial_bytes(manifest)
            self._repository.update_download(
                job.payload["asset_id"],
                status="paused",
                integrity="partial" if received else "missing",
                received_bytes=received,
            )
            self._jobs.update_state(
                job.id,
                status="paused",
                progress=current_progress,
                checkpoint={
                    "asset_id": job.payload["asset_id"],
                    "received_bytes": received,
                    "total_bytes": manifest.total_size_bytes,
                },
            )
            self._jobs.append_event(
                job_id=job.id,
                event_type="models.install.paused",
                progress=current_progress,
                payload={"received_bytes": received},
            )
            return
        except AssetDownloadCancelled:
            self._cancel_install(job, manifest)
            return
        except AssetDownloadError as error:
            received = self._downloader.partial_bytes(manifest)
            integrity = self._downloader.inspect(manifest)
            self._repository.update_download(
                job.payload["asset_id"],
                status="failed",
                integrity=integrity if integrity in {"partial", "invalid"} else "missing",
                received_bytes=received,
                error_code=error.code,
                error_message=str(error),
            )
            self._jobs.update_state(
                job.id,
                status="failed",
                progress=current_progress,
                result={"retryable": error.retryable},
                checkpoint={
                    "asset_id": job.payload["asset_id"],
                    "received_bytes": received,
                    "total_bytes": manifest.total_size_bytes,
                },
                error_code=error.code,
                error_message=str(error),
            )
            self._jobs.append_event(
                job_id=job.id,
                event_type="models.install.failed",
                progress=current_progress,
                payload={"error_code": error.code, "retryable": error.retryable},
            )
            return

        asset = self._repository.mark_ready(job.payload["asset_id"])
        result = {
            "asset_id": asset.id,
            "bundle_id": asset.bundle_id,
            "revision": asset.revision,
            "installed_bytes": asset.total_bytes,
        }
        self._jobs.update_state(
            job.id,
            status="succeeded",
            progress=100,
            result=result,
            checkpoint={
                "asset_id": asset.id,
                "received_bytes": asset.total_bytes,
                "total_bytes": asset.total_bytes,
            },
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="models.install.succeeded",
            progress=100,
            payload=result,
        )
        self._repository.remove_reference(asset.id, kind="job", reference_id=job.id)

    def pause(self, job_id: str) -> JobRecord:
        job = self._require_install_job(job_id)
        requested = self._jobs.request_pause(job.id)
        if requested is None:  # pragma: no cover - protected by lookup
            raise KeyError(job_id)
        return requested

    def resume(self, job_id: str) -> JobRecord:
        job = self._require_install_job(job_id)
        if job.status == "failed" and job.result == {"retryable": False}:
            raise ValueError("this model install failure is not retryable")
        resumed = self._jobs.resume(job_id)
        if resumed is None:  # pragma: no cover - protected by lookup
            raise KeyError(job_id)
        return resumed

    def cancel(self, job_id: str) -> JobRecord:
        job, manifest = self._install_context(job_id)
        if job.status in {"succeeded", "cancelled"}:
            return job
        requested = self._jobs.request_cancel(job.id)
        if requested is None:  # pragma: no cover - protected by lookup
            raise KeyError(job_id)
        if requested.status in {"paused", "failed"}:
            self._cancel_install(requested, manifest)
            finished = self._jobs.get(job.id)
            if finished is None:  # pragma: no cover
                raise KeyError(job_id)
            return finished
        return requested

    async def activate(self, bundle_id: str, revision: str) -> ModelAssetView:
        manifest = self._require_manifest(bundle_id, revision)
        record = self._repository.find(bundle_id, revision)
        if record is None:
            raise KeyError(revision)
        if self._downloader.inspect(manifest) != "verified":
            raise ValueError("only a complete verified model revision can be activated")
        self._repository.mark_ready(record.id)
        hook = self._activation_hooks.get(manifest.capability)
        if hook is not None:
            try:
                result = hook(manifest)
                if inspect.isawaitable(result):
                    await result
            except Exception as error:
                raise ValueError("model runtime could not be activated") from error
        self._repository.activate(record.id)
        return self._view(manifest)

    def deletion_preview(self, bundle_id: str, revision: str) -> DeletionPreview:
        record = self._repository.find(bundle_id, revision)
        if record is None:
            raise KeyError(revision)
        return self._repository.deletion_preview(record.id)

    def delete(self, bundle_id: str, revision: str) -> DeletionPreview:
        manifest = self._require_manifest(bundle_id, revision)
        record = self._repository.find(bundle_id, revision)
        if record is None:
            raise KeyError(revision)
        preview = self._repository.deletion_preview(record.id)
        if not preview.can_delete:
            self._repository.delete(record.id)
        self._downloader.remove_version(manifest)
        self._repository.delete(record.id)
        return preview

    def resumable_job_ids(self) -> tuple[str, ...]:
        return self._jobs.list_resumable_ids(kind=MODEL_INSTALL_JOB_KIND)

    def is_active(self, bundle_id: str, revision: str) -> bool:
        record = self._repository.find(bundle_id, revision)
        return (
            record is not None
            and self._repository.active_id(bundle_id) == record.id
            and record.integrity == "verified"
        )

    def reference_job(self, bundle_id: str, revision: str, job_id: str) -> None:
        record = self._repository.find(bundle_id, revision)
        if record is None:
            raise KeyError(revision)
        self._repository.add_reference(record.id, kind="job", reference_id=job_id)

    def release_job(self, bundle_id: str, revision: str, job_id: str) -> None:
        record = self._repository.find(bundle_id, revision)
        if record is not None:
            self._repository.remove_reference(
                record.id,
                kind="job",
                reference_id=job_id,
            )

    def _view(self, manifest: ModelManifest) -> ModelAssetView:
        record = self._repository.find(manifest.bundle_id, manifest.revision)
        if record is None:  # pragma: no cover - registered at construction
            raise KeyError(manifest.bundle_id)
        integrity = self._downloader.inspect(manifest)
        if integrity == "verified" and record.integrity != "verified":
            record = self._repository.mark_ready(record.id)
        elif integrity != "verified" and record.integrity == "verified":
            record = self._repository.update_download(
                record.id,
                status="failed" if integrity == "invalid" else "missing",
                integrity=integrity,
                received_bytes=self._downloader.partial_bytes(manifest),
                error_code="model_files_changed",
                error_message="The verified model files are no longer complete",
            )
        active = self._repository.active_id(manifest.bundle_id) == record.id
        current = self._current.get(manifest.bundle_id)
        probe = self._runtime_probes.get(manifest.capability, lambda: True)
        try:
            runtime_available = bool(probe())
        except Exception:
            runtime_available = False
        reasons: list[str] = []
        if not runtime_available:
            reasons.append("runtime_unavailable")
        if integrity == "missing":
            reasons.append("model_missing")
        elif integrity == "partial":
            reasons.append("model_download_incomplete")
        elif integrity == "invalid":
            reasons.append("model_integrity_failed")
        if integrity == "verified" and not active:
            reasons.append("model_not_enabled")
        available = runtime_available and integrity == "verified" and active
        return ModelAssetView(
            asset_id=record.id,
            capability=manifest.capability,
            bundle_id=manifest.bundle_id,
            display_name=manifest.display_name,
            purpose=manifest.purpose,
            version=manifest.version,
            revision=manifest.revision,
            current_release=current is not None and current.revision == manifest.revision,
            download_size_bytes=manifest.total_size_bytes,
            license_name=manifest.license.name,
            license_url=manifest.license.url,
            copyright_notice=manifest.license.copyright_notice,
            source_url=manifest.source_url,
            status=record.status,
            integrity=integrity,
            active=active,
            runtime_available=runtime_available,
            available=available,
            degradation_reasons=tuple(reasons),
        )

    def _cancel_install(self, job: JobRecord, manifest: ModelManifest) -> None:
        self._downloader.cleanup_partial(manifest)
        asset_id = str(job.payload["asset_id"])
        integrity = self._downloader.inspect(manifest)
        received = self._downloader.partial_bytes(manifest)
        if integrity == "verified":
            self._repository.mark_ready(asset_id)
        else:
            self._repository.update_download(
                asset_id,
                status="missing" if integrity == "missing" else "failed",
                integrity=integrity,
                received_bytes=received,
            )
        self._jobs.update_state(
            job.id,
            status="cancelled",
            progress=job.progress,
            checkpoint={
                "asset_id": asset_id,
                "received_bytes": received,
                "total_bytes": manifest.total_size_bytes,
            },
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="models.install.cancelled",
            progress=job.progress,
            payload={"asset_id": asset_id},
        )
        self._repository.remove_reference(asset_id, kind="job", reference_id=job.id)

    def _install_context(self, job_id: str) -> tuple[JobRecord, ModelManifest]:
        job = self._require_install_job(job_id)
        try:
            bundle_id = str(job.payload["bundle_id"])
            revision = str(job.payload["revision"])
            asset_id = str(job.payload["asset_id"])
        except (KeyError, TypeError) as error:
            raise ValueError("model install job payload is invalid") from error
        manifest = self._require_manifest(bundle_id, revision)
        record = self._repository.find(bundle_id, revision)
        if record is None or record.id != asset_id:
            raise ValueError("model install job asset is invalid")
        return job, manifest

    def _require_install_job(self, job_id: str) -> JobRecord:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.kind != MODEL_INSTALL_JOB_KIND:
            raise ValueError("job is not a model install command")
        return job

    def _require_current(self, bundle_id: str) -> ModelManifest:
        try:
            return self._current[bundle_id]
        except KeyError as error:
            raise KeyError(bundle_id) from error

    def _require_manifest(self, bundle_id: str, revision: str) -> ModelManifest:
        try:
            return self._manifests[(bundle_id, revision)]
        except KeyError as error:
            raise KeyError(revision) from error
