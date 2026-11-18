"""In-process lifecycle for durable jobs executed while the local app is open."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any


class BackgroundTaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, key: str, work: Callable[[], Coroutine[Any, Any, None]]) -> bool:
        existing = self._tasks.get(key)
        if existing is not None and not existing.done():
            return False
        task: asyncio.Task[None] = asyncio.create_task(
            work(),
            name=f"paper-reader:{key}",
        )
        self._tasks[key] = task
        task.add_done_callback(lambda completed: self._completed(key, completed))
        return True

    def _completed(self, key: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(key) is task:
            self._tasks.pop(key, None)
        if not task.cancelled():
            task.exception()

    async def shutdown(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
