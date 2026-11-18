"""In-process workers for backup commands and the app-open weekly schedule."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial

from paper_reader.backups.service import BackupService, WeeklyBackupScheduler
from paper_reader.jobs.manager import BackgroundTaskManager


async def resume_backup(service: BackupService, job_id: str) -> None:
    await service.execute(job_id)


async def run_backup_scheduler(
    *,
    scheduler: WeeklyBackupScheduler,
    service: BackupService,
    tasks: BackgroundTaskManager,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    while True:
        try:
            job = scheduler.check()
        except Exception:
            job = None
        if job is not None and job.status in {"queued", "running"}:
            tasks.start(
                f"backup:{job.id}",
                partial(resume_backup, service, job.id),
            )
        await sleep(scheduler.check_interval.total_seconds())
