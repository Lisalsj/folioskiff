from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def _database(tmp_path: Path) -> tuple[Any, Any, Any]:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.jobs.repository import JobRepository

    engine = create_sqlite_engine(tmp_path / "assets.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    return engine, sessions, JobRepository(sessions)


def test_model_asset_repository_preserves_revisions_and_guards_active_or_referenced_versions(
    tmp_path: Path,
) -> None:
    from paper_reader.assets.repository import (
        AssetInUseError,
        ModelAssetRepository,
    )
    from tests.packaging.test_model_downloads import tiny_manifest

    engine, sessions, _ = _database(tmp_path)
    repository = ModelAssetRepository(sessions)
    first = repository.register(tiny_manifest(b"one", revision="1" * 40))
    second = repository.register(tiny_manifest(b"two", revision="2" * 40))

    assert first.id != second.id
    assert [item.revision for item in repository.list_bundle("fixture-e5")] == [
        "1" * 40,
        "2" * 40,
    ]
    with pytest.raises(ValueError, match="verified"):
        repository.activate(first.id)

    repository.mark_ready(first.id)
    repository.activate(first.id)
    repository.add_reference(second.id, kind="job", reference_id="job-1")
    assert repository.deletion_preview(first.id).blocking_references == ("active",)
    assert repository.deletion_preview(second.id).blocking_references == ("job:job-1",)
    with pytest.raises(AssetInUseError):
        repository.delete(first.id)
    with pytest.raises(AssetInUseError):
        repository.delete(second.id)

    repository.remove_reference(second.id, kind="job", reference_id="job-1")
    repository.delete(second.id)
    assert repository.get(second.id) is None
    engine.dispose()


@pytest.mark.asyncio
async def test_asset_service_pause_resume_cancel_and_idempotency_share_one_job_and_record(
    tmp_path: Path,
) -> None:
    from paper_reader.assets.downloader import AssetDownloadCancelled, AssetDownloadPaused
    from paper_reader.assets.repository import ModelAssetRepository
    from paper_reader.assets.service import ModelAssetService
    from tests.packaging.test_model_downloads import tiny_manifest

    engine, sessions, jobs = _database(tmp_path)
    manifest = tiny_manifest(b"asset")

    class ControlledDownloader:
        mode = "pause"
        calls = 0

        async def download(self, _manifest: Any, *, control: Any, progress: Any) -> None:
            self.calls += 1
            progress(2, 5)
            if self.mode == "pause":
                raise AssetDownloadPaused()
            if self.mode == "cancel":
                raise AssetDownloadCancelled()
            progress(5, 5)

        def cleanup_partial(self, _manifest: Any) -> None:
            return None

        def remove_version(self, _manifest: Any) -> int:
            return 5

        def inspect(self, _manifest: Any) -> str:
            return "verified" if self.mode == "complete" else "partial"

        def partial_bytes(self, _manifest: Any) -> int:
            return 2 if self.mode == "pause" else 0

    downloader = ControlledDownloader()
    service = ModelAssetService(
        manifests=(manifest,),
        repository=ModelAssetRepository(sessions),
        jobs=jobs,
        downloader=downloader,  # type: ignore[arg-type]
    )
    first = service.create_install_job(
        bundle_id=manifest.bundle_id,
        idempotency_key="same-install",
    )
    duplicate = service.create_install_job(
        bundle_id=manifest.bundle_id,
        idempotency_key="same-install",
    )
    assert duplicate.id == first.id

    await service.install(first.id)
    paused = jobs.get(first.id)
    assert paused is not None
    assert paused.status == "paused"
    assert paused.progress == 36

    downloader.mode = "complete"
    resumed = service.resume(first.id)
    assert resumed.id == first.id
    assert resumed.status == "queued"
    await service.install(first.id)
    complete = jobs.get(first.id)
    assert complete is not None
    assert complete.status == "succeeded"
    assert service.list_assets()[0].integrity == "verified"
    assert "models.install.progress" in {
        event.event_type for event in jobs.list_events(after_event_id=0)
    }
    completed_replay = service.create_install_job(
        bundle_id=manifest.bundle_id,
        idempotency_key="same-install",
    )
    assert completed_replay.id == first.id
    asset = ModelAssetRepository(sessions).find(manifest.bundle_id, manifest.revision)
    assert asset is not None
    assert ModelAssetRepository(sessions).deletion_preview(asset.id).can_delete is True

    cancelled_job = service.create_install_job(
        bundle_id=manifest.bundle_id,
        idempotency_key="cancel-install",
    )
    downloader.mode = "cancel"
    service.cancel(cancelled_job.id)
    await service.install(cancelled_job.id)
    cancelled = jobs.get(cancelled_job.id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.result is None
    engine.dispose()


@pytest.mark.asyncio
async def test_pause_requested_before_worker_start_keeps_progress_monotonic(
    tmp_path: Path,
) -> None:
    from paper_reader.assets.downloader import AssetDownloadPaused
    from paper_reader.assets.repository import ModelAssetRepository
    from paper_reader.assets.service import ModelAssetService
    from tests.packaging.test_model_downloads import tiny_manifest

    engine, sessions, jobs = _database(tmp_path)
    manifest = tiny_manifest(b"asset")

    class ImmediatePauseDownloader:
        async def download(self, _manifest: Any, *, control: Any, progress: Any) -> None:
            assert control() == "pause"
            raise AssetDownloadPaused()

        def inspect(self, _manifest: Any) -> str:
            return "missing"

        def partial_bytes(self, _manifest: Any) -> int:
            return 0

    service = ModelAssetService(
        manifests=(manifest,),
        repository=ModelAssetRepository(sessions),
        jobs=jobs,
        downloader=ImmediatePauseDownloader(),  # type: ignore[arg-type]
    )
    job = service.create_install_job(
        bundle_id=manifest.bundle_id,
        idempotency_key="pause-before-start",
    )
    service.pause(job.id)

    await service.install(job.id)

    paused = jobs.get(job.id)
    assert paused is not None
    assert paused.status == "paused"
    assert paused.progress == 10
    engine.dispose()


@pytest.mark.asyncio
async def test_failed_model_install_can_be_cancelled_to_remove_partial_bytes_and_reference(
    tmp_path: Path,
) -> None:
    from paper_reader.assets.downloader import AssetDownloadError
    from paper_reader.assets.repository import ModelAssetRepository
    from paper_reader.assets.service import ModelAssetService
    from tests.packaging.test_model_downloads import tiny_manifest

    engine, sessions, jobs = _database(tmp_path)
    manifest = tiny_manifest(b"asset")

    class FailingDownloader:
        partial = True

        async def download(self, _manifest: Any, *, control: Any, progress: Any) -> None:
            progress(2, 5)
            raise AssetDownloadError("network", "offline", retryable=True)

        def cleanup_partial(self, _manifest: Any) -> None:
            self.partial = False

        def inspect(self, _manifest: Any) -> str:
            return "partial" if self.partial else "missing"

        def partial_bytes(self, _manifest: Any) -> int:
            return 2 if self.partial else 0

    repository = ModelAssetRepository(sessions)
    downloader = FailingDownloader()
    service = ModelAssetService(
        manifests=(manifest,),
        repository=repository,
        jobs=jobs,
        downloader=downloader,  # type: ignore[arg-type]
    )
    job = service.create_install_job(
        bundle_id=manifest.bundle_id,
        idempotency_key="failed-install",
    )
    await service.install(job.id)
    failed = jobs.get(job.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.result == {"retryable": True}

    cancelled = service.cancel(job.id)

    assert cancelled.status == "cancelled"
    assert downloader.partial is False
    asset = repository.find(manifest.bundle_id, manifest.revision)
    assert asset is not None
    assert repository.deletion_preview(asset.id).can_delete is True
    engine.dispose()


@pytest.mark.asyncio
async def test_service_restores_old_revision_switches_active_pointer_and_runs_hot_enable_hook(
    tmp_path: Path,
) -> None:
    from paper_reader.assets.repository import ModelAssetRepository
    from paper_reader.assets.service import ModelAssetService
    from tests.packaging.test_model_downloads import tiny_manifest

    engine, sessions, jobs = _database(tmp_path)
    repository = ModelAssetRepository(sessions)
    old = tiny_manifest(b"old", revision="1" * 40)
    current = tiny_manifest(b"new", revision="2" * 40)
    old_record = repository.register(old)
    states = {old.revision: "verified", current.revision: "verified"}
    activated: list[str] = []

    class VersionsDownloader:
        def inspect(self, manifest: Any) -> str:
            return states[manifest.revision]

        def partial_bytes(self, manifest: Any) -> int:
            return manifest.total_size_bytes if self.inspect(manifest) == "verified" else 0

        def remove_version(self, manifest: Any) -> int:
            states.pop(manifest.revision)
            return manifest.total_size_bytes

    service = ModelAssetService(
        manifests=(current,),
        repository=repository,
        jobs=jobs,
        downloader=VersionsDownloader(),  # type: ignore[arg-type]
        activation_hooks={"semantic": lambda manifest: activated.append(manifest.revision)},
    )
    repository.mark_ready(old_record.id)

    assert [item.revision for item in service.list_assets()] == [old.revision, current.revision]
    assert [item.current_release for item in service.list_assets()] == [False, True]
    old_view = await service.activate(old.bundle_id, old.revision)
    assert old_view.active is True
    assert activated == [old.revision]

    current_record = repository.find(current.bundle_id, current.revision)
    assert current_record is not None
    repository.mark_ready(current_record.id)
    current_view = await service.activate(current.bundle_id, current.revision)
    assert current_view.active is True
    assert service.is_active(current.bundle_id, old.revision) is False
    assert service.deletion_preview(old.bundle_id, old.revision).can_delete is True
    service.delete(old.bundle_id, old.revision)
    assert repository.get(old_record.id) is None
    engine.dispose()


@pytest.mark.asyncio
async def test_failed_hot_enable_keeps_the_previous_active_revision(
    tmp_path: Path,
) -> None:
    from paper_reader.assets.repository import ModelAssetRepository
    from paper_reader.assets.service import ModelAssetService
    from tests.packaging.test_model_downloads import tiny_manifest

    engine, sessions, jobs = _database(tmp_path)
    repository = ModelAssetRepository(sessions)
    old = tiny_manifest(b"old", revision="1" * 40)
    current = tiny_manifest(b"new", revision="2" * 40)
    old_record = repository.register(old)
    repository.mark_ready(old_record.id)
    repository.activate(old_record.id)

    class VerifiedDownloader:
        def inspect(self, _manifest: Any) -> str:
            return "verified"

        def partial_bytes(self, manifest: Any) -> int:
            return manifest.total_size_bytes

    def activate(manifest: Any) -> None:
        if manifest.revision == current.revision:
            raise RuntimeError("runtime failed")

    service = ModelAssetService(
        manifests=(current,),
        repository=repository,
        jobs=jobs,
        downloader=VerifiedDownloader(),  # type: ignore[arg-type]
        activation_hooks={"semantic": activate},
    )

    with pytest.raises(ValueError, match="runtime could not be activated"):
        await service.activate(current.bundle_id, current.revision)

    assert repository.active_id(old.bundle_id) == old_record.id
    assert service.is_active(old.bundle_id, old.revision) is True
    assert service.is_active(current.bundle_id, current.revision) is False
    engine.dispose()


@pytest.mark.asyncio
async def test_clean_base_install_runs_local_preflight_install_verify_and_enable_for_e5_and_ocr(
    tmp_path: Path,
) -> None:
    from hashlib import sha256

    import httpx

    from paper_reader.assets.downloader import AssetDownloader
    from paper_reader.assets.manifest import ModelFile, ModelLicense, ModelManifest
    from paper_reader.assets.repository import ModelAssetRepository
    from paper_reader.assets.service import ModelAssetService
    from tests.packaging.test_model_downloads import FixtureNetworkPolicy, tiny_manifest

    engine, sessions, jobs = _database(tmp_path)
    payloads = {
        "det.onnx": b"det",
        "cls.onnx": b"cls",
        "rec.onnx": b"rec",
        "model.onnx": b"e5",
    }
    semantic = tiny_manifest(payloads["model.onnx"])
    revision = "b" * 40
    ocr = ModelManifest(
        capability="ocr",
        bundle_id="fixture-ocr",
        display_name="Fixture OCR",
        purpose="Scanned page recognition",
        version="fixture-v1",
        revision=revision,
        source_url="https://models.example.test/ocr/tree/" + revision,
        license=ModelLicense(
            name="Apache-2.0",
            url="https://models.example.test/license",
            copyright_notice="Fixture",
        ),
        files=tuple(
            ModelFile(
                relative_path=name,
                url=f"https://models.example.test/ocr/{name}",
                size_bytes=len(payloads[name]),
                sha256=sha256(payloads[name]).hexdigest(),
                role=role,
            )
            for role, name in (("det", "det.onnx"), ("cls", "cls.onnx"), ("rec", "rec.onnx"))
        ),
        runtime={"roles": "det,cls,rec"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        body = payloads[name]
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(body)), "ETag": f'"{name}"'},
            content=body,
        )

    enabled: list[str] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = ModelAssetService(
            manifests=(semantic, ocr),
            repository=ModelAssetRepository(sessions),
            jobs=jobs,
            downloader=AssetDownloader(
                tmp_path / "models",
                client=client,
                network_policy=FixtureNetworkPolicy(),
            ),
            activation_hooks={
                "semantic": lambda manifest: enabled.append(manifest.bundle_id),
                "ocr": lambda manifest: enabled.append(manifest.bundle_id),
            },
        )
        assert {item.integrity for item in service.list_assets()} == {"missing"}
        for manifest in (semantic, ocr):
            job = service.create_install_job(
                bundle_id=manifest.bundle_id,
                idempotency_key=f"install-{manifest.bundle_id}",
            )
            await service.install(job.id)
            await service.activate(manifest.bundle_id, manifest.revision)

        views = service.list_assets()

    assert all(item.integrity == "verified" and item.available for item in views)
    assert enabled == [semantic.bundle_id, ocr.bundle_id]
    engine.dispose()


def test_paused_job_is_durable_and_never_implicitly_resumed(tmp_path: Path) -> None:
    engine, sessions, jobs = _database(tmp_path)
    job = jobs.create(kind="models.install", payload={"asset_id": "durable-model"})
    jobs.request_pause(job.id)
    jobs.update_state(job.id, status="paused", progress=37)
    for index in range(105):
        jobs.create(kind="reader.extract_document", payload={"index": index})

    from paper_reader.jobs.repository import JobRepository

    reopened = JobRepository(sessions)
    paused = reopened.get(job.id)
    assert paused is not None
    assert paused.status == "paused"
    assert paused.pause_requested is True
    assert paused.paused_at is not None
    assert reopened.list_resumable_ids(kind="models.install") == ()
    assert [item.id for item in reopened.list_jobs_by_kind(kind="models.install")] == [job.id]
    engine.dispose()


@pytest.mark.asyncio
async def test_global_job_cancel_delegates_model_cleanup_even_while_paused(
    tmp_path: Path,
) -> None:
    from paper_reader.jobs.repository import JobRepository
    from tests.reader.test_extraction_api import bootstrap, local_client, reader_app

    app = reader_app(tmp_path)
    repository = JobRepository(app.state.sessions)
    job = repository.create(
        kind="models.install",
        payload={"asset_id": "asset-1"},
        idempotency_key="global-cancel",
    )
    repository.update_state(job.id, status="paused", progress=25)
    calls: list[str] = []

    class CleanupService:
        def cancel(self, job_id: str) -> Any:
            calls.append(job_id)
            repository.request_cancel(job_id)
            return repository.update_state(job_id, status="cancelled", progress=25)

    async with local_client(app) as client:
        app.state.model_asset_service = CleanupService()
        headers = await bootstrap(client)
        response = await client.post(f"/api/v1/jobs/{job.id}/cancel", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert calls == [job.id]


@pytest.mark.asyncio
async def test_model_asset_api_exposes_preflight_lifecycle_without_local_paths(
    tmp_path: Path,
) -> None:
    from paper_reader.jobs.repository import JobRepository
    from tests.reader.test_extraction_api import bootstrap, local_client, reader_app

    app = reader_app(tmp_path)

    class StubAssetService:
        def __init__(self) -> None:
            self.jobs = JobRepository(app.state.sessions)
            self.calls: list[tuple[str, str]] = []

        def list_assets(self) -> tuple[Any, ...]:
            return (
                SimpleNamespace(
                    asset_id="asset-1",
                    capability="semantic",
                    bundle_id="multilingual-e5-small",
                    display_name="E5",
                    purpose="Semantic recommendations",
                    version="v1",
                    revision="a" * 40,
                    current_release=True,
                    download_size_bytes=42,
                    license_name="MIT",
                    license_url="https://example.test/license",
                    copyright_notice="Fixture",
                    source_url="https://example.test/source",
                    status="missing",
                    integrity="missing",
                    active=False,
                    runtime_available=True,
                    available=False,
                    degradation_reasons=("model_missing",),
                ),
            )

        def create_install_job(self, *, bundle_id: str, idempotency_key: str) -> Any:
            self.calls.append((bundle_id, idempotency_key))
            return self.jobs.create(
                kind="models.install",
                payload={"asset_id": "asset-1", "bundle_id": bundle_id},
                idempotency_key=idempotency_key,
            )

        def latest_jobs(self) -> tuple[Any, ...]:
            return tuple(self.jobs.list_jobs_by_kind(kind="models.install"))

        async def install(self, job_id: str) -> None:
            self.jobs.update_state(job_id, status="succeeded", progress=100)

        def pause(self, job_id: str) -> Any:
            return self.jobs.request_pause(job_id)

        def resume(self, job_id: str) -> Any:
            return self.jobs.resume(job_id)

        def cancel(self, job_id: str) -> Any:
            return self.jobs.request_cancel(job_id)

        async def activate(self, bundle_id: str, revision: str) -> Any:
            self.calls.append((bundle_id, revision))
            return self.list_assets()[0]

        def deletion_preview(self, bundle_id: str, revision: str) -> Any:
            return SimpleNamespace(
                asset_id="asset-1",
                bundle_id=bundle_id,
                revision=revision,
                reclaimable_bytes=42,
                blocking_references=(),
                can_delete=True,
            )

        def delete(self, bundle_id: str, revision: str) -> Any:
            self.calls.append((bundle_id, revision))
            return self.deletion_preview(bundle_id, revision)

    async with local_client(app) as client:
        service = StubAssetService()
        app.state.model_asset_service = service
        durable = service.jobs.create(
            kind="models.install",
            payload={"asset_id": "asset-1", "bundle_id": "multilingual-e5-small"},
            idempotency_key="durable-model-download",
        )
        service.jobs.request_pause(durable.id)
        service.jobs.update_state(durable.id, status="paused", progress=37)
        for index in range(105):
            service.jobs.create(kind="reader.extract_document", payload={"index": index})
        unauthorized = await client.get("/api/v1/settings/model-assets")
        headers = await bootstrap(client)
        listed = await client.get("/api/v1/settings/model-assets")
        model_jobs = await client.get("/api/v1/settings/model-assets/jobs")
        install = await client.post(
            "/api/v1/settings/model-assets/multilingual-e5-small/install",
            headers={**headers, "Idempotency-Key": "install-e5"},
        )
        await asyncio.sleep(0)
        preview = await client.get(
            "/api/v1/settings/model-assets/multilingual-e5-small/revisions/"
            + "a" * 40
            + "/deletion-preview"
        )

    assert unauthorized.status_code == 401
    assert listed.status_code == 200
    assert listed.json()[0]["download_size_bytes"] == 42
    assert listed.json()[0]["current_release"] is True
    assert listed.json()[0]["degradation_reasons"] == ["model_missing"]
    assert "path" not in listed.text.casefold()
    assert "models_root" not in listed.text
    assert model_jobs.status_code == 200
    assert len(model_jobs.json()) == 1
    assert model_jobs.json()[0]["id"] == durable.id
    assert model_jobs.json()[0]["status"] == "paused"
    assert model_jobs.json()[0]["asset_id"] == "asset-1"
    assert model_jobs.json()[0]["retryable"] is None
    assert "payload" not in model_jobs.json()[0]
    assert "checkpoint" not in model_jobs.json()[0]
    assert "result" not in model_jobs.json()[0]
    assert "error_message" not in model_jobs.json()[0]
    assert install.status_code == 202
    assert install.json()["kind"] == "models.install"
    assert preview.status_code == 200
    assert preview.json()["can_delete"] is True


def test_model_asset_migration_adds_pause_state_and_asset_lifecycle_tables(
    tmp_path: Path,
) -> None:
    from sqlalchemy import inspect

    from paper_reader.database import create_sqlite_engine
    from paper_reader.migrations import migration_head_revision, upgrade_database

    database = tmp_path / "migrated.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    inspector = inspect(engine)

    assert migration_head_revision() == "0038_remove_legacy_conversations"
    assert {
        "model_assets",
        "active_model_assets",
        "model_asset_references",
    }.issubset(set(inspector.get_table_names()))
    job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    assert {"pause_requested", "paused_at"}.issubset(job_columns)
    engine.dispose()
