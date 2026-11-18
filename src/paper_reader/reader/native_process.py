"""Hard-bounded subprocess protocol for untrusted native document runtimes."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from ctypes import wintypes
from pathlib import Path
from secrets import token_hex
from typing import Any, BinaryIO, cast

NATIVE_PROTOCOL_VERSION = "paper-reader-native-v1"
MAX_NATIVE_REQUEST_BYTES = 1024 * 1024
MAX_NATIVE_STATUS_LINE_BYTES = 64 * 1024
MAX_NATIVE_STATUS_EVENTS = 4_096
MAX_NATIVE_RESULT_BYTES = 192 * 1024 * 1024
DEFAULT_NATIVE_MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_RESULT_FILE = "result.json"
_ALLOWED_ENVIRONMENT = frozenset(
    {
        "allusersprofile",
        "appdata",
        "comspec",
        "homedrive",
        "homepath",
        "lang",
        "lc_all",
        "localappdata",
        "number_of_processors",
        "path",
        "pathext",
        "processor_architecture",
        "programdata",
        "systemdrive",
        "systemroot",
        "temp",
        "tmp",
        "userdomain",
        "username",
        "userprofile",
        "windir",
    }
)


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class NativeProcessError(RuntimeError):
    """Stable failure from the native worker supervision boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _WindowsJob:
    """Own a kill-on-close, one-process, memory-bounded Windows Job Object."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        memory_limit_bytes: int,
        active_process_limit: int,
    ) -> None:
        kernel32 = cast(Any, ctypes.WinDLL("kernel32.dll", use_last_error=True))
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError("native Job Object could not be created")
        self._kernel32 = kernel32
        self._handle = int(handle)
        try:
            limits = _JobObjectExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
                | _JOB_OBJECT_LIMIT_JOB_MEMORY
                | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            limits.BasicLimitInformation.ActiveProcessLimit = active_process_limit
            limits.ProcessMemoryLimit = memory_limit_bytes
            limits.JobMemoryLimit = memory_limit_bytes
            if not kernel32.SetInformationJobObject(
                wintypes.HANDLE(self._handle),
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise OSError("native Job Object limits could not be established")
            process_handle = getattr(process, "_handle", None)
            if not isinstance(process_handle, int) or not kernel32.AssignProcessToJobObject(
                wintypes.HANDLE(self._handle),
                wintypes.HANDLE(process_handle),
            ):
                raise OSError("native worker could not enter its Job Object")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        handle = self._handle
        if handle:
            self._handle = 0
            self._kernel32.CloseHandle(wintypes.HANDLE(handle))


def _attach_windows_job(
    process: subprocess.Popen[bytes],
    *,
    memory_limit_bytes: int,
    active_process_limit: int,
) -> _WindowsJob | None:
    if os.name != "nt":
        return None
    return _WindowsJob(
        process,
        memory_limit_bytes=memory_limit_bytes,
        active_process_limit=active_process_limit,
    )


class NativeProcessRunner:
    """Run one native operation with a clean environment and hard termination."""

    def __init__(
        self,
        *,
        work_root: Path,
        command: Sequence[str] | None = None,
        environment_source: Mapping[str, str] | None = None,
        memory_limit_bytes: int = DEFAULT_NATIVE_MEMORY_LIMIT_BYTES,
        active_process_limit: int | None = None,
    ) -> None:
        if command is not None and (not command or any("\x00" in item for item in command)):
            raise ValueError("native worker command is invalid")
        root = Path(work_root)
        if root.exists() and _is_reparse(root):
            raise ValueError("native worker root cannot be a reparse point")
        if not 128 * 1024 * 1024 <= memory_limit_bytes <= 4 * 1024 * 1024 * 1024:
            raise ValueError("native worker memory limit is invalid")
        selected_process_limit = (
            (1 if bool(getattr(sys, "frozen", False)) else 2)
            if active_process_limit is None
            else active_process_limit
        )
        if selected_process_limit not in {1, 2}:
            raise ValueError("native worker process limit is invalid")
        self._work_root = root
        self._command = tuple(command) if command is not None else None
        self._environment_source = environment_source
        self._memory_limit_bytes = memory_limit_bytes
        self._active_process_limit = selected_process_limit

    def run(
        self,
        *,
        operation: str,
        payload: Mapping[str, Any],
        timeout_seconds: float,
        cancelled: Callable[[], bool],
        progress: Callable[[int, int], None] = lambda _completed, _total: None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", operation):
            raise ValueError("native operation is invalid")
        if not 0 < timeout_seconds <= 7200:
            raise ValueError("native timeout must be between 0 and 7200 seconds")
        if _safe_cancelled(cancelled):
            raise NativeProcessError("cancelled", "The native operation was cancelled")

        task = self._create_task_directory()
        process: subprocess.Popen[bytes] | None = None
        windows_job: _WindowsJob | None = None
        reader: threading.Thread | None = None
        writer: threading.Thread | None = None
        events: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=256)
        writer_events: queue.Queue[BaseException | None] = queue.Queue(maxsize=1)
        try:
            request = {
                "protocol_version": NATIVE_PROTOCOL_VERSION,
                "operation": operation,
                "payload": dict(payload),
                "result_file": _RESULT_FILE,
            }
            try:
                encoded_request = json.dumps(
                    request,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError) as error:
                raise ValueError("native request must contain finite JSON values") from error
            if len(encoded_request) > MAX_NATIVE_REQUEST_BYTES:
                raise NativeProcessError(
                    "request_limit",
                    "The native operation request exceeded its safe size",
                )
            deadline = time.monotonic() + timeout_seconds

            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                    subprocess,
                    "CREATE_NEW_PROCESS_GROUP",
                    0,
                )
            try:
                process = subprocess.Popen(
                    self._resolved_command(),
                    cwd=task,
                    env=_native_environment(self._environment_source),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    creationflags=creationflags,
                )
            except (FileNotFoundError, PermissionError, OSError) as error:
                raise NativeProcessError(
                    "worker_unavailable",
                    "The isolated native worker could not be started",
                ) from error
            try:
                windows_job = _attach_windows_job(
                    process,
                    memory_limit_bytes=self._memory_limit_bytes,
                    active_process_limit=self._active_process_limit,
                )
            except OSError as error:
                raise NativeProcessError(
                    "worker_unavailable",
                    "The isolated native worker policy could not be established",
                ) from error
            assert process.stdin is not None
            assert process.stdout is not None
            reader = threading.Thread(
                target=_read_status_lines,
                args=(process.stdout, events),
                name="paper-reader-native-status",
                daemon=True,
            )
            reader.start()
            writer = threading.Thread(
                target=_write_request,
                args=(process.stdin, encoded_request, writer_events),
                name="paper-reader-native-request",
                daemon=True,
            )
            writer.start()
            reader_finished = False
            status_event_count = 0
            while True:
                if _safe_cancelled(cancelled):
                    raise NativeProcessError(
                        "cancelled",
                        "The native operation was cancelled",
                    )
                if time.monotonic() >= deadline:
                    raise NativeProcessError(
                        "time_limit",
                        "The native operation exceeded its hard time limit",
                    )
                try:
                    writer_state = writer_events.get_nowait()
                except queue.Empty:
                    writer_state = None
                if isinstance(writer_state, BaseException):
                    raise NativeProcessError(
                        "worker_failed",
                        "The isolated native worker stopped before accepting its request",
                    ) from writer_state
                try:
                    item = events.get(timeout=min(0.02, max(0.001, deadline - time.monotonic())))
                except queue.Empty:
                    if process.poll() is not None and reader_finished:
                        raise NativeProcessError(
                            "worker_failed",
                            "The isolated native worker stopped without a result",
                        ) from None
                    continue
                if item is None:
                    reader_finished = True
                    if process.poll() is not None and events.empty():
                        raise NativeProcessError(
                            "worker_failed",
                            "The isolated native worker stopped without a result",
                        )
                    continue
                if isinstance(item, BaseException):
                    raise NativeProcessError(
                        "protocol_error",
                        "The isolated native worker emitted an invalid status stream",
                    ) from item
                status_event_count += 1
                if status_event_count > MAX_NATIVE_STATUS_EVENTS:
                    raise NativeProcessError(
                        "protocol_error",
                        "The isolated native worker exceeded its status event budget",
                    )
                event = _decode_status(item)
                event_type = event.get("type")
                if event_type == "progress":
                    completed = event.get("completed")
                    total = event.get("total")
                    if (
                        set(event) != {"type", "completed", "total"}
                        or isinstance(completed, bool)
                        or isinstance(total, bool)
                        or not isinstance(completed, int)
                        or not isinstance(total, int)
                        or not 0 <= completed <= total <= 2_000
                    ):
                        raise NativeProcessError(
                            "protocol_error",
                            "The isolated native worker emitted invalid progress",
                        )
                    progress(completed, total)
                    continue
                if event_type == "result":
                    return self._read_result(
                        task,
                        process,
                        event,
                        deadline=deadline,
                        cancelled=cancelled,
                    )
                raise NativeProcessError(
                    "protocol_error",
                    "The isolated native worker emitted an unknown status",
                )
        finally:
            if process is not None and process.poll() is None:
                _kill_process_tree(process)
            if windows_job is not None:
                windows_job.close()
                windows_job = None
            if process is not None:
                if writer is not None:
                    writer.join(timeout=1)
                if (
                    process.stdin is not None
                    and not process.stdin.closed
                    and (writer is None or not writer.is_alive())
                ):
                    process.stdin.close()
                if process.stdout is not None:
                    process.stdout.close()
            if reader is not None:
                reader.join(timeout=1)
            self._remove_task_directory(task)

    def _create_task_directory(self) -> Path:
        if self._work_root.exists() and _is_reparse(self._work_root):
            raise NativeProcessError(
                "unsafe_work_root",
                "The native worker root is unsafe",
            )
        self._work_root.mkdir(parents=True, exist_ok=True)
        root = self._work_root.resolve()
        task = root / f"call-{token_hex(16)}"
        task.mkdir(exist_ok=False)
        if _is_reparse(task) or task.parent != root:
            raise NativeProcessError("unsafe_work_root", "The native worker path is unsafe")
        return task

    def _remove_task_directory(self, task: Path) -> None:
        root = self._work_root.resolve()
        resolved = task.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as error:  # pragma: no cover - created internally
            raise RuntimeError("native worker cleanup escaped its root") from error
        if resolved == root or _is_reparse(task):
            raise RuntimeError("native worker cleanup target is unsafe")
        if task.exists():
            shutil.rmtree(task)

    def _resolved_command(self) -> tuple[str, ...]:
        if self._command is not None:
            return self._command
        if bool(getattr(sys, "frozen", False)):
            return (sys.executable, "native-worker")
        return (sys.executable, "-m", "paper_reader.reader.native_worker")

    @staticmethod
    def _read_result(
        task: Path,
        process: subprocess.Popen[bytes],
        event: Mapping[str, Any],
        *,
        deadline: float,
        cancelled: Callable[[], bool],
    ) -> dict[str, Any]:
        byte_size = event.get("byte_size")
        digest = event.get("sha256")
        if (
            set(event) != {"type", "byte_size", "sha256"}
            or isinstance(byte_size, bool)
            or not isinstance(byte_size, int)
            or not 0 <= byte_size <= MAX_NATIVE_RESULT_BYTES
            or not isinstance(digest, str)
            or _SHA256_PATTERN.fullmatch(digest) is None
        ):
            raise NativeProcessError(
                "protocol_error",
                "The isolated native worker result receipt is invalid",
            )
        while process.poll() is None:
            if _safe_cancelled(cancelled):
                raise NativeProcessError(
                    "cancelled",
                    "The native operation was cancelled",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NativeProcessError(
                    "time_limit",
                    "The native operation exceeded its hard time limit",
                )
            time.sleep(min(0.02, remaining))
        return_code = process.returncode
        if return_code != 0:
            raise NativeProcessError(
                "worker_failed",
                "The isolated native worker failed after reporting a result",
            )
        result_path = task / _RESULT_FILE
        if not result_path.is_file() or _is_reparse(result_path):
            raise NativeProcessError(
                "protocol_error",
                "The isolated native worker result file is unsafe",
            )
        with result_path.open("rb") as stream:
            encoded = stream.read(MAX_NATIVE_RESULT_BYTES + 1)
        if (
            len(encoded) != byte_size
            or len(encoded) > MAX_NATIVE_RESULT_BYTES
            or hashlib.sha256(encoded).hexdigest() != digest
        ):
            raise NativeProcessError(
                "protocol_error",
                "The isolated native worker result receipt does not match its bytes",
            )
        try:
            value = json.loads(encoded)
        except (UnicodeError, ValueError) as error:
            raise NativeProcessError(
                "protocol_error",
                "The isolated native worker result is not valid JSON",
            ) from error
        if not isinstance(value, dict):
            raise NativeProcessError(
                "protocol_error",
                "The isolated native worker result must be an object",
            )
        return cast(dict[str, Any], value)


def _native_environment(source: Mapping[str, str] | None) -> dict[str, str]:
    inherited = os.environ if source is None else source
    result = {
        key: value
        for key, value in inherited.items()
        if key.casefold() in _ALLOWED_ENVIRONMENT and "\x00" not in key and "\x00" not in value
    }
    result.update(
        {
            "NO_COLOR": "1",
            "PATH": os.pathsep.join(_trusted_native_paths(inherited)),
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": "",
        }
    )
    return result


def _trusted_native_paths(source: Mapping[str, str]) -> tuple[str, ...]:
    candidates = [Path(sys.executable).resolve().parent, Path(sys.base_prefix).resolve()]
    base_dlls = Path(sys.base_prefix).resolve() / "DLLs"
    candidates.append(base_dlls)
    frozen_root = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen_root, str) and frozen_root:
        candidates.append(Path(frozen_root).resolve())
    system_root = next(
        (value for key, value in source.items() if key.casefold() == "systemroot"),
        None,
    )
    if system_root:
        candidates.append(Path(system_root).resolve() / "System32")

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.is_absolute() or not candidate.is_dir():
            continue
        rendered = str(candidate)
        identity = os.path.normcase(rendered)
        if identity not in seen:
            seen.add(identity)
            result.append(rendered)
    return tuple(result)


def _write_request(
    stdin: BinaryIO,
    encoded_request: bytes,
    events: queue.Queue[BaseException | None],
) -> None:
    try:
        stdin.write(encoded_request)
        stdin.close()
    except BaseException as error:  # pragma: no cover - exact pipe error is OS-specific
        events.put(error)
        return
    events.put(None)


def _read_status_lines(
    stdout: BinaryIO,
    events: queue.Queue[bytes | BaseException | None],
) -> None:
    try:
        while True:
            line = stdout.readline(MAX_NATIVE_STATUS_LINE_BYTES + 1)
            if not line:
                events.put(None)
                return
            if len(line) > MAX_NATIVE_STATUS_LINE_BYTES or not line.endswith(b"\n"):
                events.put(ValueError("native status line limit"))
                return
            events.put(line)
    except BaseException as error:  # pragma: no cover - OS pipe failure
        events.put(error)


def _decode_status(line: bytes) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except (UnicodeError, ValueError) as error:
        raise NativeProcessError(
            "protocol_error",
            "The isolated native worker emitted invalid JSON",
        ) from error
    if not isinstance(value, dict):
        raise NativeProcessError(
            "protocol_error",
            "The isolated native worker status must be an object",
        )
    return cast(dict[str, Any], value)


def _safe_cancelled(callback: Callable[[], bool]) -> bool:
    try:
        return bool(callback())
    except Exception:
        return True


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    # Popen.kill maps to TerminateProcess on Windows, so native threads cannot
    # delay cancellation; closing the surrounding Job also kills launcher members.
    process.kill()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:  # pragma: no cover - process.kill is definitive
        process.kill()
        process.wait(timeout=1)


def _is_reparse(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True
