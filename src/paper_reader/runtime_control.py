"""Process-local control plane for coordinated application restarts."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock


class RuntimeControl:
    """Request one restart and invoke the bound server shutdown exactly once."""

    def __init__(self) -> None:
        self._guard = RLock()
        self._shutdown: Callable[[], None] | None = None
        self._shutdown_called = False
        self._restart_reason: str | None = None

    @property
    def restart_requested(self) -> bool:
        with self._guard:
            return self._restart_reason is not None

    @property
    def restart_reason(self) -> str | None:
        with self._guard:
            return self._restart_reason

    def bind_shutdown(self, shutdown: Callable[[], None]) -> None:
        if not callable(shutdown):
            raise TypeError("shutdown callback must be callable")
        callback: Callable[[], None] | None = None
        with self._guard:
            if self._shutdown is not None and self._shutdown is not shutdown:
                raise RuntimeError("runtime shutdown is already bound")
            self._shutdown = shutdown
            if self._restart_reason is not None and not self._shutdown_called:
                self._shutdown_called = True
                callback = shutdown
        if callback is not None:
            callback()

    def request_restart(self, reason: str) -> None:
        cleaned = reason.strip()
        if not cleaned or len(cleaned) > 100:
            raise ValueError("restart reason must contain 1 to 100 characters")
        callback: Callable[[], None] | None = None
        with self._guard:
            if self._restart_reason is None:
                self._restart_reason = cleaned
            if self._shutdown is not None and not self._shutdown_called:
                self._shutdown_called = True
                callback = self._shutdown
        if callback is not None:
            callback()
