from __future__ import annotations

import asyncio
import shutil
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.jobs.repository import JobRepository
from paper_reader.publishing.git import (
    EXPECTED_GITHUB_REMOTE,
    GitPublicationResult,
    GitPublishCancelled,
    GitPublishError,
    GitPublishingDiagnostic,
)
from paper_reader.settings import AppPaths
from tests.platform._publishing_fixtures import build_publishing_fixture
from tests.platform.test_git_publisher import GIT, _git, _repository, _source_state


def _selection() -> dict[str, object]:
    return {
        "slug": "quiet-library-picks",
        "title": "安静图书馆精选",
        "public_intro": "只公开明确选择的论文与正式报告。",
        "works": [
            {"work_id": "work-b", "report_id": None},
            {"work_id": "work-alias", "report_id": "report-selected"},
        ],
    }


def _paths_with_publishing_fixture(tmp_path: Path) -> AppPaths:
    fixture = build_publishing_fixture(tmp_path / "seed")
    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    paths.state_root.mkdir(parents=True)
    shutil.copyfile(fixture.database, paths.database)
    # The privacy fixture deliberately stores a schema-invalid plugin manifest as a
    # canary.  App startup correctly validates installed plugins, so remove that
    # unrelated malformed row from this API-specific database copy.
    with sqlite3.connect(paths.database) as connection:
        connection.execute("DELETE FROM plugin_installations")
        connection.commit()
    return paths


@asynccontextmanager
async def _local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


async def _bootstrap(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/session/bootstrap",
        json={"token": "secret"},
        headers={"Origin": "http://127.0.0.1"},
    )
    assert response.status_code == 204
    return {
        "Origin": "http://127.0.0.1",
        "X-CSRF-Token": client.cookies["paper_reader_csrf"],
    }


async def _wait_for_status(
    client: httpx.AsyncClient,
    job_id: str,
    statuses: set[str] | None = None,
) -> dict[str, Any]:
    expected = statuses or {"succeeded", "failed", "cancelled"}
    for _ in range(500):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in expected:
            return body
        await asyncio.sleep(0.01)
    raise AssertionError(f"publishing job {job_id} did not reach {sorted(expected)}")


class ControllablePublisher:
    def __init__(self, *, mode: str = "succeed") -> None:
        self.mode = mode
        self.calls: list[str] = []
        self.entered = threading.Event()
        self.target_fingerprint = "a" * 64

    def diagnose(self) -> GitPublishingDiagnostic:
        return GitPublishingDiagnostic(
            enabled=True,
            reason=None,
            git_available=True,
            repository_detected=True,
            repository_root="C:\\private\\repository",
            remote_name="origin",
            remote_url=EXPECTED_GITHUB_REMOTE,
            branch="published",
            site_root="site",
            workflow_path=".github/workflows/pages.yml",
            target_fingerprint=self.target_fingerprint,
            stale_worktree_count=0,
        )

    def publish(
        self,
        snapshot,  # type: ignore[no-untyped-def]
        *,
        operation_id: str,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> GitPublicationResult:
        self.calls.append(operation_id)
        self.entered.set()
        if self.mode == "fail":
            raise GitPublishError("push_rejected", retryable=True)
        if self.mode == "block":
            while not cancelled():
                threading.Event().wait(0.005)
            raise GitPublishCancelled()
        return GitPublicationResult(
            snapshot_id=snapshot.id,
            snapshot_sha256=snapshot.snapshot_sha256,
            remote_name="origin",
            remote_url=EXPECTED_GITHUB_REMOTE,
            branch="published",
            commit_sha="b" * 40,
            workflow_path=".github/workflows/pages.yml",
            site_root="site",
        )


def _app(tmp_path: Path, publisher: object):  # type: ignore[no-untyped-def]
    from paper_reader.app import create_app

    return create_app(
        paths=_paths_with_publishing_fixture(tmp_path),
        bootstrap_token="secret",
        publishing_git_publisher=publisher,
    )


@pytest.mark.asyncio
@pytest.mark.skipif(GIT is None, reason="Git is required for the real API publication")
async def test_git_diagnostic_preview_confirmation_and_publish_are_safe_and_idempotent(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.publishing.git import GitPublisher

    source, remote = _repository(tmp_path / "git")
    paths = _paths_with_publishing_fixture(tmp_path / "app")
    app = create_app(
        paths=paths,
        bootstrap_token="secret",
        publishing_git_publisher=GitPublisher(
            repository_root=source,
            cache_root=paths.cache_root,
            allow_url_rewrites=True,
        ),
    )
    before_source = _source_state(source)

    async with _local_client(app) as client:
        unauthorized = await client.get("/api/v1/publishing/git")
        headers = await _bootstrap(client)
        diagnostic = await client.get("/api/v1/publishing/git")
        assert diagnostic.status_code == 200
        target_fingerprint = diagnostic.json()["target_fingerprint"]

        with sqlite3.connect(paths.database) as connection:
            before_counts = connection.execute(
                "SELECT (SELECT COUNT(*) FROM published_snapshots), (SELECT COUNT(*) FROM jobs)"
            ).fetchone()
        preview = await client.post("/api/v1/publishing/previews", json=_selection())
        with sqlite3.connect(paths.database) as connection:
            after_counts = connection.execute(
                "SELECT (SELECT COUNT(*) FROM published_snapshots), (SELECT COUNT(*) FROM jobs)"
            ).fetchone()

        rejected_unconfirmed = await client.post(
            "/api/v1/publishing/jobs",
            json={
                "preview": preview.json()["preview"],
                "target_fingerprint": target_fingerprint,
            },
            headers={**headers, "Idempotency-Key": "before-confirmation"},
        )
        missing_csrf = await client.post(
            "/api/v1/publishing/settings/confirm",
            json={"target_fingerprint": target_fingerprint},
            headers={"Origin": headers["Origin"]},
        )
        stale_confirmation = await client.post(
            "/api/v1/publishing/settings/confirm",
            json={"target_fingerprint": "0" * 64},
            headers=headers,
        )
        confirmed = await client.post(
            "/api/v1/publishing/settings/confirm",
            json={"target_fingerprint": target_fingerprint},
            headers=headers,
        )
        queued = await client.post(
            "/api/v1/publishing/jobs",
            json={
                "preview": preview.json()["preview"],
                "target_fingerprint": target_fingerprint,
            },
            headers={**headers, "Idempotency-Key": "publish-once"},
        )
        duplicate = await client.post(
            "/api/v1/publishing/jobs",
            json={
                "preview": preview.json()["preview"],
                "target_fingerprint": target_fingerprint,
            },
            headers={**headers, "Idempotency-Key": "publish-once"},
        )
        finished = await _wait_for_status(client, queued.json()["id"])
        events = (await client.get("/api/v1/events/poll")).json()

    assert unauthorized.status_code == 401
    assert before_counts == after_counts == (0, 0)
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "private, no-store"
    assert preview.json()["git"]["remote_name"] == "origin"
    assert preview.json()["git"]["branch"] == "published"
    assert preview.json()["files"] == [
        ".github/workflows/pages.yml",
        "site/data.json",
        "site/index.html",
        "site/manifest.json",
    ]
    assert "credentials" in preview.json()["excluded_fields"]
    assert rejected_unconfirmed.status_code == 409
    assert missing_csrf.status_code == 403
    assert stale_confirmation.status_code == 409
    assert confirmed.status_code == 200 and confirmed.json()["confirmed"] is True
    assert queued.status_code == 202
    assert duplicate.status_code == 202 and duplicate.json()["id"] == queued.json()["id"]
    assert finished["status"] == "succeeded"
    assert finished["result"]["branch"] == "published"
    assert finished["result"]["snapshot_id"]
    assert finished["result"]["snapshot_sha256"]
    assert (
        finished["result"]["commit_sha"]
        == _git(remote, "rev-parse", "published").stdout.decode().strip()
    )
    serialized = str(finished)
    assert str(tmp_path) not in serialized
    assert "private\\repository" not in serialized
    assert _source_state(source) == before_source
    assert b"site/index.html" in _git(remote, "ls-tree", "-r", "--name-only", "published").stdout
    job_events = [item["type"] for item in events if item["job_id"] == queued.json()["id"]]
    assert job_events == [
        "publishing.started",
        "publishing.succeeded",
    ]


@pytest.mark.asyncio
async def test_push_failure_keeps_snapshot_retry_reuses_it_and_cancel_is_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.publishing.repository import PublishedSnapshotIntegrityError

    publisher = ControllablePublisher(mode="fail")
    app = _app(tmp_path, publisher)
    async with _local_client(app) as client:
        headers = await _bootstrap(client)
        preview = (await client.post("/api/v1/publishing/previews", json=_selection())).json()
        await client.post(
            "/api/v1/publishing/settings/confirm",
            json={"target_fingerprint": publisher.target_fingerprint},
            headers=headers,
        )
        failed_command = await client.post(
            "/api/v1/publishing/jobs",
            json={
                "preview": preview["preview"],
                "target_fingerprint": publisher.target_fingerprint,
            },
            headers={**headers, "Idempotency-Key": "will-fail"},
        )
        failed = await _wait_for_status(client, failed_command.json()["id"])
        snapshot_id = failed["result"]["snapshot_id"]

        publisher.mode = "succeed"
        retry = await client.post(
            f"/api/v1/publishing/jobs/{failed['id']}/retry",
            headers={**headers, "Idempotency-Key": "retry-success"},
        )
        retry_duplicate = await client.post(
            f"/api/v1/publishing/jobs/{failed['id']}/retry",
            headers={**headers, "Idempotency-Key": "retry-success"},
        )
        retried = await _wait_for_status(client, retry.json()["id"])

        require_snapshot = app.state.publishing_service._snapshots.require

        def corrupted_snapshot(_snapshot_id: str):  # type: ignore[no-untyped-def]
            raise PublishedSnapshotIntegrityError("private corruption detail")

        monkeypatch.setattr(
            app.state.publishing_service._snapshots,
            "require",
            corrupted_snapshot,
        )
        unavailable_retry = await client.post(
            f"/api/v1/publishing/jobs/{failed['id']}/retry",
            headers={**headers, "Idempotency-Key": "retry-corrupt-snapshot"},
        )
        monkeypatch.setattr(
            app.state.publishing_service._snapshots,
            "require",
            require_snapshot,
        )

        publisher.mode = "block"
        publisher.entered.clear()
        cancel_command = await client.post(
            "/api/v1/publishing/jobs",
            json={
                "preview": preview["preview"],
                "target_fingerprint": publisher.target_fingerprint,
            },
            headers={**headers, "Idempotency-Key": "cancel-me"},
        )
        for _ in range(200):
            if publisher.entered.is_set():
                break
            await asyncio.sleep(0.01)
        cancelled_request = await client.post(
            f"/api/v1/jobs/{cancel_command.json()['id']}/cancel",
            headers=headers,
        )
        cancelled = await _wait_for_status(client, cancel_command.json()["id"])

    assert failed_command.status_code == 202
    assert failed["status"] == "failed"
    assert failed["error_code"] == "push_rejected"
    assert failed["result"] == {"snapshot_id": snapshot_id, "retryable": True}
    assert retry.status_code == 202
    assert retry_duplicate.json()["id"] == retry.json()["id"]
    assert retry.json()["id"] != failed["id"]
    assert retried["status"] == "succeeded"
    assert retried["result"]["snapshot_id"] == snapshot_id
    assert unavailable_retry.status_code == 409
    assert unavailable_retry.json()["detail"] == "Frozen publication snapshot is unavailable"
    assert cancelled_request.status_code == 200
    assert cancelled["status"] == "cancelled"
    assert cancelled["result"]["snapshot_id"] == snapshot_id
    assert cancelled["result"]["retryable"] is True

    with sqlite3.connect(app.state.paths.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM published_snapshots").fetchone() == (1,)
        payloads = [row[0] for row in connection.execute("SELECT payload FROM jobs")]
    assert all("complete_prompt" not in payload for payload in payloads)
    assert all("private" not in payload.casefold() for payload in payloads)


@pytest.mark.asyncio
async def test_startup_resumes_frozen_publication_but_rejects_a_changed_target(
    tmp_path: Path,
) -> None:
    from paper_reader.publishing.models import PublicationSelection

    publisher = ControllablePublisher(mode="succeed")
    app = _app(tmp_path / "resume", publisher)
    service = app.state.publishing_service
    envelope = service.preview(PublicationSelection.model_validate(_selection()))
    service.confirm_settings(publisher.target_fingerprint)
    queued = service.create_job(
        preview=envelope.preview,
        target_fingerprint=publisher.target_fingerprint,
        idempotency_key="resume-after-restart",
    )

    async with _local_client(app) as client:
        await _bootstrap(client)
        resumed = await _wait_for_status(client, queued.id)

    assert resumed["status"] == "succeeded"
    assert publisher.calls == [queued.id]

    changed = ControllablePublisher(mode="succeed")
    second = _app(tmp_path / "target-change", changed)
    second_service = second.state.publishing_service
    second_envelope = second_service.preview(PublicationSelection.model_validate(_selection()))
    second_service.confirm_settings(changed.target_fingerprint)
    target_job = second_service.create_job(
        preview=second_envelope.preview,
        target_fingerprint=changed.target_fingerprint,
        idempotency_key="target-will-change",
    )
    changed.target_fingerprint = "c" * 64
    try:
        await second_service.execute(target_job.id)
        failed = JobRepository(second.state.sessions).get(target_job.id)
    finally:
        second.state.engine.dispose()

    assert failed is not None and failed.status == "failed"
    assert failed.error_code == "target_changed"
    assert failed.result == {"snapshot_id": target_job.payload["snapshot_id"], "retryable": False}
    assert changed.calls == []
