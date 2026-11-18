"""Small Windows launcher helpers kept independent from the web application."""

from __future__ import annotations

import ctypes
import json
import os
import threading
import webbrowser
from contextlib import suppress
from pathlib import Path
from secrets import token_hex
from typing import Any, Protocol

from paper_reader.instance import restrict_file_to_current_user

INSTALLER_APP_ID = "{381B261E-B659-4B56-855E-ADDE80CC5BD2}"
INSTALLER_RUN_MARKER = r"Local\paper-reader-installer-running-v1"


class InstallerRunMarkerError(RuntimeError):
    """The non-owning installer liveness marker could not be established."""


class InstallerRunMarker:
    """Hold an Inno-visible marker after the profile file lock is owned."""

    def __init__(self) -> None:
        self._handle: int | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("installer run marker is already held")
        if os.name != "nt":
            return
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, INSTALLER_RUN_MARKER)
        if not handle:
            raise InstallerRunMarkerError("installer_run_marker_failed")
        self._handle = int(handle)

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None or os.name != "nt":
            return
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        kernel32.CloseHandle(handle)

    def __enter__(self) -> InstallerRunMarker:
        self.acquire()
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


def is_installer_run_marker_present() -> bool:
    """Probe the installer-only marker; it never decides application ownership."""

    if os.name != "nt":
        return False
    synchronize = 0x00100000
    kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.OpenMutexW.restype = ctypes.c_void_p
    handle = kernel32.OpenMutexW(synchronize, False, INSTALLER_RUN_MARKER)
    if not handle:
        return False
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    kernel32.CloseHandle(handle)
    return True


class _ServerState(Protocol):
    started: bool
    should_exit: bool


class _InstancePublisher(Protocol):
    def publish(self, *, port: int, activation_secret: str) -> object: ...


class LauncherMonitorError(RuntimeError):
    """The local server could not safely publish its ready state."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def open_browser_safely(url: str) -> bool:
    """Best-effort browser activation; browser integration cannot stop the server."""

    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


class LauncherMonitor:
    """Publish activation state only after Uvicorn reports successful startup."""

    def __init__(
        self,
        *,
        server: _ServerState,
        instance_lock: _InstancePublisher,
        port: int,
        activation_secret: str,
        initial_url: str,
        open_browser: bool,
        ready_file: Path | None,
        shutdown_after: float | None,
    ) -> None:
        self._server = server
        self._instance_lock = instance_lock
        self._port = port
        self._activation_secret = activation_secret
        self._initial_url = initial_url
        self._open_browser = open_browser
        self._ready_file = Path(ready_file).absolute() if ready_file is not None else None
        self._shutdown_after = shutdown_after
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready_bytes: bytes | None = None
        self.error: LauncherMonitorError | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("launcher monitor has already started")
        self._thread = threading.Thread(
            target=self._run,
            name="paper-reader-launcher-monitor",
            daemon=True,
        )
        self._thread.start()

    def wait_until_ready(self, *, timeout: float) -> bool:
        return self._ready.wait(timeout) and self.error is None

    @property
    def was_ready(self) -> bool:
        return self._ready.is_set() and self.error is None

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        if self._ready_file is not None and self._ready_bytes is not None:
            _unlink_if_exact(self._ready_file, self._ready_bytes)
            self._ready_bytes = None

    def _run(self) -> None:
        try:
            while not bool(self._server.started):
                if self._stop.wait(0.02) or bool(self._server.should_exit):
                    return
            if self._stop.is_set():
                return
            self._instance_lock.publish(
                port=self._port,
                activation_secret=self._activation_secret,
            )
            if self._ready_file is not None:
                self._ready_bytes = _write_ready_file(self._ready_file, port=self._port)
            if self._open_browser:
                with suppress(Exception):
                    open_browser_safely(self._initial_url)
            self._ready.set()
            if self._shutdown_after is not None and not self._stop.wait(self._shutdown_after):
                self._server.should_exit = True
        except Exception:
            self.error = LauncherMonitorError(
                "launcher_ready_failed",
                "paper-reader could not publish its local ready state",
            )
            self._server.should_exit = True
            self._ready.set()


def _write_ready_file(path: Path, *, port: int) -> bytes:
    parent = path.parent
    if _has_reparse_ancestor(parent):
        raise LauncherMonitorError("ready_file_unsafe", "the smoke ready file is unsafe")
    parent.mkdir(parents=True, exist_ok=True)
    if _has_reparse_ancestor(parent) or (path.exists() and _is_link_or_reparse(path)):
        raise LauncherMonitorError("ready_file_unsafe", "the smoke ready file is unsafe")
    payload = {
        "schema_version": "paper-reader-ready-v1",
        "pid": os.getpid(),
        "port": port,
    }
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        os.set_inheritable(descriptor, False)
        restrict_file_to_current_user(temporary)
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        return encoded
    except OSError as error:
        raise LauncherMonitorError(
            "ready_file_failed",
            "the smoke ready file could not be written",
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()


def _is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        return bool(checker()) if checker is not None else False
    except OSError:
        return True


def _has_reparse_ancestor(path: Path) -> bool:
    current = path
    while True:
        if _is_link_or_reparse(current):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _unlink_if_exact(path: Path, expected: bytes) -> None:
    try:
        if _has_reparse_ancestor(path) or not path.is_file():
            return
        if path.read_bytes() == expected:
            path.unlink()
    except OSError:
        return
