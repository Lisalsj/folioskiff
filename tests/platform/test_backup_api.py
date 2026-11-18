from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.settings import AppPaths


def backup_app(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.app import create_app

    return create_app(
        paths=AppPaths.from_overrides(
            state_root=tmp_path / "state",
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "library",
        ),
        bootstrap_token="secret",
    )


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


async def bootstrap(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/session/bootstrap",
        json={"token": "secret"},
        headers={"Origin": "http://127.0.0.1"},
    )
    assert response.status_code == 204
    return {
        "Origin": "http://127.0.0.1",
        "X-CSRF-Token": client.cookies["paper_reader_csrf"],
        "Idempotency-Key": "manual-backup",
    }


async def wait_for_terminal(client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"succeeded", "failed", "cancelled"}:
            return body
        await asyncio.sleep(0.01)
    raise AssertionError("backup job did not complete")


@pytest.mark.asyncio
async def test_backup_api_requires_local_session_csrf_and_idempotency(tmp_path: Path) -> None:
    app = backup_app(tmp_path)
    async with local_client(app) as client:
        unauthorized = await client.post(
            "/api/v1/backups",
            json={"kind": "manual", "encrypted": False},
            headers={"Idempotency-Key": "unauthorized"},
        )
        headers = await bootstrap(client)
        no_csrf = await client.post(
            "/api/v1/backups",
            json={"kind": "manual", "encrypted": False},
            headers={
                "Origin": headers["Origin"],
                "Idempotency-Key": "no-csrf",
            },
        )
        missing_key = await client.post(
            "/api/v1/backups",
            json={"kind": "manual", "encrypted": False},
            headers={
                "Origin": headers["Origin"],
                "X-CSRF-Token": headers["X-CSRF-Token"],
            },
        )
        automatic = await client.post(
            "/api/v1/backups",
            json={"kind": "automatic", "encrypted": False},
            headers={**headers, "Idempotency-Key": "not-user-selectable"},
        )
        missing_crypto_password = await client.post(
            "/api/v1/backups",
            json={"kind": "manual", "encrypted": True},
            headers={**headers, "Idempotency-Key": "crypto-not-ready"},
        )

    assert unauthorized.status_code == 401
    assert no_csrf.status_code == 403
    assert missing_key.status_code == 422
    assert automatic.status_code == 422
    assert missing_crypto_password.status_code == 422


@pytest.mark.asyncio
async def test_manual_backup_api_runs_lists_and_downloads_verified_package(
    tmp_path: Path,
) -> None:
    app = backup_app(tmp_path)
    async with local_client(app) as client:
        headers = await bootstrap(client)
        queued = await client.post(
            "/api/v1/backups",
            json={"kind": "manual", "encrypted": False},
            headers=headers,
        )
        assert queued.status_code == 202
        job_id = queued.json()["id"]
        finished = await wait_for_terminal(client, job_id)
        duplicate = await client.post(
            "/api/v1/backups",
            json={"kind": "manual", "encrypted": False},
            headers=headers,
        )
        listing = await client.get("/api/v1/backups")
        policy = await client.get("/api/v1/backups/policy")
        manual = next(item for item in listing.json() if item["job_id"] == job_id)
        downloaded = await client.get(f"/api/v1/backups/{manual['id']}/download")
        events = await client.get("/api/v1/events/poll")

    assert finished["status"] == "succeeded"
    assert duplicate.status_code == 202 and duplicate.json()["id"] == job_id
    assert listing.status_code == 200
    assert "relative_path" not in listing.text
    assert policy.status_code == 200
    assert policy.json()["automatic_interval_days"] == 7
    assert policy.json()["automatic_retention"] == 4
    assert policy.json()["check_interval_hours"] == 6
    assert downloaded.status_code == 200
    assert downloaded.content
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert downloaded.headers["cache-control"] == "private, no-store"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert int(downloaded.headers["content-length"]) == len(downloaded.content)
    backup_events = [
        event["type"] for event in events.json() if event["job_id"] == job_id
    ]
    assert backup_events[0] == "backup.started"
    assert "backup.progress" in backup_events
    assert backup_events[-2:] == ["backup.archived", "backup.succeeded"]


@pytest.mark.asyncio
async def test_backup_download_returns_path_free_gone_for_missing_changed_or_unsafe(
    tmp_path: Path,
) -> None:
    app = backup_app(tmp_path)
    async with local_client(app) as client:
        headers = await bootstrap(client)
        queued = await client.post(
            "/api/v1/backups",
            json={"kind": "manual", "encrypted": False},
            headers=headers,
        )
        await wait_for_terminal(client, queued.json()["id"])
        listing = (await client.get("/api/v1/backups")).json()
        archive = next(item for item in listing if item["job_id"] == queued.json()["id"])

        from paper_reader.backups.models import BackupArchiveRecord

        with app.state.sessions() as session:
            record = session.get(BackupArchiveRecord, archive["id"])
            package = app.state.paths.backups / record.relative_path
        package.write_bytes(package.read_bytes() + b"tampered")
        changed = await client.get(f"/api/v1/backups/{archive['id']}/download")

        outside = app.state.paths.library_root / "private.txt"
        outside.write_text("never disclose", encoding="utf-8")
        with app.state.sessions() as session:
            record = session.get(BackupArchiveRecord, archive["id"])
            record.relative_path = "../private.txt"
            session.commit()
        unsafe = await client.get(f"/api/v1/backups/{archive['id']}/download")
        missing = await client.get("/api/v1/backups/00000000-0000-0000-0000-000000000000/download")

    assert changed.status_code == 410
    assert unsafe.status_code == 410
    assert missing.status_code == 410
    for response in (changed, unsafe, missing):
        assert "private" not in response.text.casefold()
        assert str(tmp_path).casefold() not in response.text.casefold()
    assert outside.read_text(encoding="utf-8") == "never disclose"


@pytest.mark.asyncio
async def test_app_startup_resumes_unencrypted_backup_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.backups.service import BackupKind, BackupService

    app = backup_app(tmp_path)
    queued = app.state.backup_service.create_job(
        kind=BackupKind.MANUAL,
        idempotency_key="restart-backup",
    )
    resumed: list[str] = []

    async def record_resume(self: BackupService, job_id: str) -> None:
        resumed.append(job_id)

    monkeypatch.setattr(BackupService, "execute", record_resume)
    async with local_client(app):
        for _ in range(100):
            if queued.id in resumed:
                break
            await asyncio.sleep(0.01)

    assert queued.id in resumed
