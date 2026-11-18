"""Fail-closed Windows plugin process isolation primitives.

The generic launcher owns all stream, wall-clock, cancellation and output limits. A
platform backend may only return a process after it has established both an AppContainer
token and a Job Object. There is deliberately no ordinary-subprocess fallback.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wintypes
import hashlib
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast

DEFAULT_PLUGIN_MEMORY_BYTES = 256 * 1024 * 1024
DEFAULT_PLUGIN_OUTPUT_BYTES = 16 * 1024 * 1024
DEFAULT_PLUGIN_LINE_BYTES = 1024 * 1024
_MINIMUM_MEMORY_BYTES = 16 * 1024 * 1024
_MAXIMUM_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
_WORK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_ALLOWED_ENVIRONMENT = frozenset(
    {
        "PATH",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "SYSTEMROOT",
        "WINDIR",
    }
)

_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_NO_WINDOW = 0x08000000
_STARTF_USESTDHANDLES = 0x00000100
_HANDLE_FLAG_INHERIT = 0x00000001
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_UI_RESTRICTIONS = 4
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_UI_LIMITS = 0x000000FF
_ERROR_ALREADY_EXISTS_HRESULT = 0x800700B7
_PROFILE_NOT_FOUND_HRESULTS = frozenset({0x80070002, 0x80070490})
_ERROR_INSUFFICIENT_BUFFER = 122
_WAIT_OBJECT_0 = 0
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _StartupInfoW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoExW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _StartupInfoW),
        ("lpAttributeList", wintypes.LPVOID),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [
        ("Sid", wintypes.LPVOID),
        ("Attributes", wintypes.DWORD),
    ]


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", wintypes.LPVOID),
        ("Capabilities", ctypes.POINTER(_SidAndAttributes)),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


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


class _JobObjectBasicUiRestrictions(ctypes.Structure):
    _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]


class SandboxError(RuntimeError):
    """Stable sandbox failure without raw platform or worker diagnostics."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class SandboxProbe:
    available: bool
    code: str | None
    app_container: bool
    job_object: bool

    def __post_init__(self) -> None:
        if self.available:
            if self.code is not None or not self.app_container or not self.job_object:
                raise ValueError("An available sandbox requires AppContainer and Job Object")
        elif not self.code:
            raise ValueError("An unavailable sandbox requires a stable reason code")


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    wall_clock_seconds: float = 60.0
    memory_bytes: int = DEFAULT_PLUGIN_MEMORY_BYTES
    output_bytes: int = DEFAULT_PLUGIN_OUTPUT_BYTES
    max_line_bytes: int = DEFAULT_PLUGIN_LINE_BYTES

    def __post_init__(self) -> None:
        if self.wall_clock_seconds <= 0 or self.wall_clock_seconds > 3600:
            raise ValueError("wall_clock_seconds must be between 0 and 3600")
        if not _MINIMUM_MEMORY_BYTES <= self.memory_bytes <= _MAXIMUM_MEMORY_BYTES:
            raise ValueError("memory_bytes must be between 16 MiB and 2 GiB")
        if self.output_bytes <= 0 or self.output_bytes > DEFAULT_PLUGIN_OUTPUT_BYTES:
            raise ValueError("output_bytes must be between 1 byte and 16 MiB")
        if self.max_line_bytes < 64 or self.max_line_bytes > DEFAULT_PLUGIN_LINE_BYTES:
            raise ValueError("max_line_bytes must be between 64 bytes and 1 MiB")


@dataclass(frozen=True, slots=True)
class SandboxProcessSpec:
    executable: Path
    arguments: tuple[str, ...]
    working_directory: Path
    environment: Mapping[str, str]
    limits: SandboxLimits

    def __post_init__(self) -> None:
        executable = Path(self.executable)
        working_directory = Path(self.working_directory)
        if not executable.is_file():
            raise ValueError("executable must name an existing file")
        if not working_directory.is_dir():
            raise ValueError("working_directory must name an existing directory")
        if any(working_directory.iterdir()):
            raise ValueError("Plugin working_directory must be empty")
        if any("\x00" in argument for argument in self.arguments):
            raise ValueError("Plugin arguments cannot contain NUL")
        environment = dict(self.environment)
        if set(environment) - _ALLOWED_ENVIRONMENT:
            raise ValueError("Plugin environment contains a non-allowlisted variable")
        if any(
            "\x00" in key or "\x00" in value for key, value in environment.items()
        ):
            raise ValueError("Plugin environment cannot contain NUL")
        object.__setattr__(self, "executable", executable.resolve())
        object.__setattr__(self, "working_directory", working_directory.resolve())
        object.__setattr__(self, "environment", environment)


@dataclass(frozen=True, slots=True)
class SandboxNativePolicy:
    identity: str
    executable: Path
    arguments: tuple[str, ...]
    working_directory: Path
    environment: Mapping[str, str]
    shell: bool
    hidden_window: bool
    app_container: bool
    network_capabilities: tuple[str, ...]
    inherited_handles: tuple[str, ...]
    job_active_process_limit: int
    job_kill_on_close: bool
    job_memory_limit_bytes: int

    @classmethod
    def from_spec(cls, value: SandboxProcessSpec) -> SandboxNativePolicy:
        call_identity = (
            f"{os.getpid()}:{os.path.normcase(str(value.working_directory.resolve()))}"
        )
        return cls(
            identity=(
                # Windows 11 can reject AppContainer names longer than 50 characters.
                "paper-reader-" + hashlib.sha256(call_identity.encode("utf-8")).hexdigest()[:32]
            ),
            executable=value.executable,
            arguments=value.arguments,
            working_directory=value.working_directory,
            environment=value.environment,
            shell=False,
            hidden_window=True,
            app_container=True,
            network_capabilities=(),
            inherited_handles=("stdin", "stdout", "stderr"),
            job_active_process_limit=1,
            job_kill_on_close=True,
            job_memory_limit_bytes=value.limits.memory_bytes,
        )


@dataclass(frozen=True, slots=True)
class SandboxExecution:
    exit_code: int
    stderr: bytes


class SandboxProcess(Protocol):
    async def write_stdin(self, content: bytes) -> None: ...

    async def close_stdin(self) -> None: ...

    async def read_stdout(self, size: int) -> bytes: ...

    async def read_stderr(self, size: int) -> bytes: ...

    async def wait(self) -> int: ...

    async def terminate_tree(self) -> None: ...

    async def close(self) -> None: ...


class SandboxBackend(Protocol):
    def probe(self) -> SandboxProbe: ...

    async def start(self, policy: SandboxNativePolicy) -> SandboxProcess: ...


LineHandler = Callable[[bytes], Awaitable[bytes | None]]


class SandboxLauncher(Protocol):
    def probe(self) -> SandboxProbe: ...

    async def run(
        self,
        spec: SandboxProcessSpec,
        *,
        initial_stdin: bytes,
        cancelled: Callable[[], bool],
        on_stdout_line: LineHandler,
    ) -> SandboxExecution: ...


class WindowsSandboxLauncher:
    """Run one protocol request only after a backend attests both Windows boundaries."""

    def __init__(self, *, backend: SandboxBackend | None = None) -> None:
        self._backend = backend or Win32AppContainerBackend()

    def probe(self) -> SandboxProbe:
        return self._backend.probe()

    async def run(
        self,
        spec: SandboxProcessSpec,
        *,
        initial_stdin: bytes,
        cancelled: Callable[[], bool],
        on_stdout_line: LineHandler,
    ) -> SandboxExecution:
        probe = self.probe()
        if not probe.available:
            raise SandboxError(
                "sandbox_unavailable",
                f"Windows plugin sandbox is unavailable ({probe.code})",
            )
        if cancelled():
            raise SandboxError(
                "cancelled",
                "Plugin execution was cancelled before launch",
                retryable=True,
            )
        if len(initial_stdin) > spec.limits.max_line_bytes:
            raise SandboxError("input_limit", "Plugin request exceeds the input limit")

        policy = SandboxNativePolicy.from_spec(spec)
        try:
            process = await self._backend.start(policy)
        except SandboxError:
            raise
        except (OSError, RuntimeError) as error:
            raise SandboxError(
                "sandbox_unavailable",
                "Windows could not establish the plugin sandbox",
            ) from error

        output_budget = [0]
        stderr_chunks: list[bytes] = []
        stdout_task = asyncio.create_task(
            self._read_stdout(
                process,
                spec.limits,
                output_budget,
                on_stdout_line,
            )
        )
        stderr_task = asyncio.create_task(
            self._read_stderr(process, spec.limits, output_budget, stderr_chunks)
        )
        wait_task = asyncio.create_task(process.wait())
        tasks = (stdout_task, stderr_task, wait_task)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + spec.limits.wall_clock_seconds
        failure: SandboxError | None = None
        try:
            await process.write_stdin(initial_stdin)
            while True:
                for task in tasks:
                    if not task.done() or task.cancelled():
                        continue
                    task_error = task.exception()
                    if isinstance(task_error, SandboxError):
                        raise task_error
                    if task_error is not None:
                        raise SandboxError(
                            "process_io",
                            "Plugin sandbox stream failed",
                        ) from task_error
                if all(task.done() for task in tasks):
                    break
                if cancelled():
                    raise SandboxError(
                        "cancelled",
                        "Plugin execution was cancelled",
                        retryable=True,
                    )
                if loop.time() >= deadline:
                    raise SandboxError(
                        "timeout",
                        "Plugin sandbox exceeded its wall-clock limit",
                        retryable=True,
                    )
                await asyncio.sleep(min(0.01, max(0.0, deadline - loop.time())))
        except SandboxError as error:
            failure = error
            await process.terminate_tree()
            if not wait_task.done():
                with suppress(TimeoutError, OSError, SandboxError):
                    await asyncio.wait_for(asyncio.shield(wait_task), timeout=2)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await process.close()

        if failure is not None:
            raise failure
        return SandboxExecution(
            exit_code=wait_task.result(),
            stderr=b"".join(stderr_chunks),
        )

    async def _read_stdout(
        self,
        process: SandboxProcess,
        limits: SandboxLimits,
        output_budget: list[int],
        on_line: LineHandler,
    ) -> None:
        pending = bytearray()
        try:
            while True:
                chunk = await process.read_stdout(64 * 1024)
                if not chunk:
                    if pending:
                        await on_line(bytes(pending))
                    return
                _charge_output(output_budget, len(chunk), limits.output_bytes)
                pending.extend(chunk)
                if len(pending) > limits.max_line_bytes and b"\n" not in pending:
                    raise SandboxError(
                        "line_limit",
                        "Plugin output exceeded the JSONL line limit",
                    )
                while True:
                    newline = pending.find(b"\n")
                    if newline < 0:
                        break
                    line = bytes(pending[: newline + 1])
                    del pending[: newline + 1]
                    if len(line) > limits.max_line_bytes:
                        raise SandboxError(
                            "line_limit",
                            "Plugin output exceeded the JSONL line limit",
                        )
                    try:
                        reply = await on_line(line)
                    except SandboxError:
                        raise
                    except Exception as error:
                        raise SandboxError(
                            "protocol_error",
                            "Plugin output violated the isolated protocol",
                        ) from error
                    if reply is not None:
                        if len(reply) > limits.max_line_bytes:
                            raise SandboxError(
                                "input_limit",
                                "Broker response exceeded the JSONL line limit",
                            )
                        await process.write_stdin(reply)
        finally:
            await process.close_stdin()

    @staticmethod
    async def _read_stderr(
        process: SandboxProcess,
        limits: SandboxLimits,
        output_budget: list[int],
        chunks: list[bytes],
    ) -> None:
        while True:
            chunk = await process.read_stderr(64 * 1024)
            if not chunk:
                return
            _charge_output(output_budget, len(chunk), limits.output_bytes)
            chunks.append(chunk)


def build_plugin_environment(
    runtime_directory: Path,
    *,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a deterministic environment without credentials, proxies or user search paths."""

    runtime = Path(runtime_directory).resolve()
    inherited = os.environ if source is None else source
    environment = {
        "PATH": str(runtime),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    for key in ("SYSTEMROOT", "WINDIR"):
        value = inherited.get(key)
        if value and "\x00" not in value:
            environment[key] = value
    return dict(sorted(environment.items()))


def create_empty_working_directory(root: Path, call_id: str) -> Path:
    if not _WORK_ID.fullmatch(call_id):
        raise ValueError("Plugin call identifier is invalid")
    resolved_root = Path(root).resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)
    target = resolved_root / call_id
    target.mkdir(mode=0o700)
    resolved = target.resolve()
    _require_child(resolved_root, resolved)
    return resolved


def cleanup_working_directory(root: Path, target: Path) -> None:
    resolved_root = Path(root).resolve()
    resolved_target = Path(target).resolve()
    _require_child(resolved_root, resolved_target)
    if resolved_target.exists():
        shutil.rmtree(resolved_target, onexc=_remove_read_only)


def probe_windows_isolation() -> SandboxProbe:
    """Check that the required Windows APIs exist; never claim partial isolation as safe."""

    if os.name != "nt":
        return SandboxProbe(
            available=False,
            code="windows_required",
            app_container=False,
            job_object=False,
        )
    try:
        userenv = ctypes.WinDLL("userenv.dll", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        app_container = all(
            hasattr(userenv, name)
            for name in (
                "CreateAppContainerProfile",
                "DeleteAppContainerProfile",
                "DeriveAppContainerSidFromAppContainerName",
                "GetAppContainerFolderPath",
            )
        ) and all(
            hasattr(kernel32, name)
            for name in (
                "CreatePipe",
                "InitializeProcThreadAttributeList",
                "SetHandleInformation",
                "UpdateProcThreadAttribute",
                "CreateProcessW",
            )
        )
        job_object = all(
            hasattr(kernel32, name)
            for name in (
                "CreateJobObjectW",
                "SetInformationJobObject",
                "AssignProcessToJobObject",
                "TerminateJobObject",
            )
        )
    except OSError:
        app_container = False
        job_object = False
    if app_container and job_object:
        return SandboxProbe(
            available=True,
            code=None,
            app_container=True,
            job_object=True,
        )
    return SandboxProbe(
        available=False,
        code="windows_isolation_api_unavailable",
        app_container=app_container,
        job_object=job_object,
    )


class Win32AppContainerBackend:
    """Native Windows AppContainer plus Job Object backend.

    It creates a unique, disposable AppContainer profile per invocation. The worker receives
    no capability SID, so Windows denies direct network access. A suspended process is placed
    into a one-process, memory-bounded, kill-on-close Job Object before its first instruction.
    """

    def __init__(self) -> None:
        self._probe_result: SandboxProbe | None = None

    def probe(self) -> SandboxProbe:
        if self._probe_result is not None:
            return self._probe_result
        capabilities = probe_windows_isolation()
        if not capabilities.available:
            self._probe_result = capabilities
            return capabilities
        identity = f"paper-reader-probe-{os.getpid()}"
        api: _Win32Api | None = None
        job: int | None = None
        sid: int | None = None
        try:
            api = _Win32Api()
            api.delete_profile(identity, ignore_missing=True)
            job = api.create_job(DEFAULT_PLUGIN_MEMORY_BYTES)
            sid = api.create_profile(identity)
            api.profile_folder(sid)
            self._probe_result = SandboxProbe(
                available=True,
                code=None,
                app_container=True,
                job_object=True,
            )
        except (OSError, RuntimeError, ValueError):
            self._probe_result = SandboxProbe(
                available=False,
                code="windows_isolation_validation_failed",
                app_container=False,
                job_object=False,
            )
        finally:
            if api is not None:
                if sid is not None:
                    api.free_sid(sid)
                if job is not None:
                    api.close_handle(job)
                try:
                    api.delete_profile(identity, ignore_missing=True)
                except OSError:
                    self._probe_result = SandboxProbe(
                        available=False,
                        code="windows_isolation_cleanup_failed",
                        app_container=False,
                        job_object=False,
                    )
        return self._probe_result

    async def start(self, policy: SandboxNativePolicy) -> SandboxProcess:
        probe = self.probe()
        if not probe.available:
            raise SandboxError(
                "sandbox_unavailable",
                "Windows could not validate AppContainer and Job Object enforcement",
            )
        try:
            return await asyncio.to_thread(_start_win32_process, policy)
        except SandboxError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            self._probe_result = SandboxProbe(
                available=False,
                code="windows_isolation_start_failed",
                app_container=False,
                job_object=False,
            )
            raise SandboxError(
                "sandbox_unavailable",
                "Windows could not establish the plugin isolation boundary",
            ) from error


class _Win32Api:
    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows APIs are unavailable")
        self.kernel32 = cast(Any, ctypes.WinDLL("kernel32.dll", use_last_error=True))
        self.userenv = cast(Any, ctypes.WinDLL("userenv.dll", use_last_error=True))
        self.advapi32 = cast(Any, ctypes.WinDLL("advapi32.dll", use_last_error=True))
        self.ole32 = cast(Any, ctypes.WinDLL("ole32.dll", use_last_error=True))
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        self.userenv.CreateAppContainerProfile.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.POINTER(_SidAndAttributes),
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
        ]
        self.userenv.CreateAppContainerProfile.restype = ctypes.c_long
        self.userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.LPVOID),
        ]
        self.userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
        self.userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
        self.userenv.DeleteAppContainerProfile.restype = ctypes.c_long
        self.userenv.GetAppContainerFolderPath.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        self.userenv.GetAppContainerFolderPath.restype = ctypes.c_long
        self.advapi32.ConvertSidToStringSidW.argtypes = [
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        self.advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        self.advapi32.FreeSid.argtypes = [wintypes.LPVOID]
        self.advapi32.FreeSid.restype = wintypes.LPVOID
        self.kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        self.kernel32.LocalFree.restype = wintypes.HLOCAL
        self.ole32.CoTaskMemFree.argtypes = [wintypes.LPVOID]
        self.ole32.CoTaskMemFree.restype = None
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        self.kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self.kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        self.kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel32.TerminateJobObject.restype = wintypes.BOOL
        self.kernel32.CreatePipe.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(_SecurityAttributes),
            wintypes.DWORD,
        ]
        self.kernel32.CreatePipe.restype = wintypes.BOOL
        self.kernel32.SetHandleInformation.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self.kernel32.SetHandleInformation.restype = wintypes.BOOL
        self.kernel32.InitializeProcThreadAttributeList.argtypes = [
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        self.kernel32.UpdateProcThreadAttribute.argtypes = [
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.c_size_t,
            wintypes.LPVOID,
            ctypes.c_size_t,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        self.kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        self.kernel32.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
        self.kernel32.DeleteProcThreadAttributeList.restype = None
        self.kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(_StartupInfoW),
            ctypes.POINTER(_ProcessInformation),
        ]
        self.kernel32.CreateProcessW.restype = wintypes.BOOL
        self.kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        self.kernel32.ResumeThread.restype = wintypes.DWORD
        self.kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel32.TerminateProcess.restype = wintypes.BOOL
        self.kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.GetExitCodeProcess.restype = wintypes.BOOL

    def create_profile(self, identity: str) -> int:
        sid = wintypes.LPVOID()
        result = int(
            self.userenv.CreateAppContainerProfile(
                identity,
                "paper-reader isolated plugin",
                "Disposable paper-reader plugin process",
                None,
                0,
                ctypes.byref(sid),
            )
        )
        if _unsigned_hresult(result) == _ERROR_ALREADY_EXISTS_HRESULT:
            result = int(
                self.userenv.DeriveAppContainerSidFromAppContainerName(
                    identity,
                    ctypes.byref(sid),
                )
            )
        if not _hresult_succeeded(result) or not sid.value:
            raise OSError("AppContainer profile could not be created")
        return int(sid.value)

    def delete_profile(self, identity: str, *, ignore_missing: bool) -> None:
        result = int(self.userenv.DeleteAppContainerProfile(identity))
        if _hresult_succeeded(result):
            return
        unsigned = _unsigned_hresult(result)
        if ignore_missing and unsigned in _PROFILE_NOT_FOUND_HRESULTS:
            return
        raise OSError("AppContainer profile could not be deleted")

    def profile_folder(self, sid: int) -> Path:
        sid_text = wintypes.LPWSTR()
        if not self.advapi32.ConvertSidToStringSidW(
            wintypes.LPVOID(sid),
            ctypes.byref(sid_text),
        ):
            raise OSError("AppContainer SID could not be rendered")
        folder = wintypes.LPWSTR()
        try:
            result = int(
                self.userenv.GetAppContainerFolderPath(
                    sid_text.value,
                    ctypes.byref(folder),
                )
            )
            if not _hresult_succeeded(result) or not folder.value:
                raise OSError("AppContainer profile folder could not be resolved")
            return Path(folder.value).resolve()
        finally:
            if folder:
                self.ole32.CoTaskMemFree(ctypes.cast(folder, wintypes.LPVOID))
            if sid_text:
                self.kernel32.LocalFree(ctypes.cast(sid_text, wintypes.HLOCAL))

    def free_sid(self, sid: int) -> None:
        self.advapi32.FreeSid(wintypes.LPVOID(sid))

    def create_job(self, memory_bytes: int) -> int:
        handle = self.kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError("Job Object could not be created")
        job = int(handle)
        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | _JOB_OBJECT_LIMIT_JOB_MEMORY
            | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        limits.BasicLimitInformation.ActiveProcessLimit = 1
        limits.ProcessMemoryLimit = memory_bytes
        limits.JobMemoryLimit = memory_bytes
        ui = _JobObjectBasicUiRestrictions(_JOB_OBJECT_UI_LIMITS)
        if not self.kernel32.SetInformationJobObject(
            wintypes.HANDLE(job),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ) or not self.kernel32.SetInformationJobObject(
            wintypes.HANDLE(job),
            _JOB_OBJECT_BASIC_UI_RESTRICTIONS,
            ctypes.byref(ui),
            ctypes.sizeof(ui),
        ):
            self.close_handle(job)
            raise OSError("Job Object limits could not be established")
        return job

    def create_pipe(self) -> tuple[int, int]:
        read_handle = wintypes.HANDLE()
        write_handle = wintypes.HANDLE()
        attributes = _SecurityAttributes(
            nLength=ctypes.sizeof(_SecurityAttributes),
            lpSecurityDescriptor=None,
            bInheritHandle=True,
        )
        if not self.kernel32.CreatePipe(
            ctypes.byref(read_handle),
            ctypes.byref(write_handle),
            ctypes.byref(attributes),
            0,
        ):
            raise OSError("Plugin pipe could not be created")
        if read_handle.value is None or write_handle.value is None:
            raise OSError("Plugin pipe returned an invalid handle")
        return int(read_handle.value), int(write_handle.value)

    def clear_inheritance(self, handle: int) -> None:
        if not self.kernel32.SetHandleInformation(
            wintypes.HANDLE(handle),
            _HANDLE_FLAG_INHERIT,
            0,
        ):
            raise OSError("Plugin parent pipe inheritance could not be cleared")

    def close_handle(self, handle: int | None) -> None:
        if handle:
            self.kernel32.CloseHandle(wintypes.HANDLE(handle))


class _Win32SandboxProcess:
    def __init__(
        self,
        *,
        api: _Win32Api,
        process_handle: int,
        job_handle: int,
        stdin: BinaryIO,
        stdout: BinaryIO,
        stderr: BinaryIO,
        identity: str,
    ) -> None:
        self._api = api
        self._process_handle = process_handle
        self._job_handle = job_handle
        self._stdin: BinaryIO | None = stdin
        self._stdout: BinaryIO | None = stdout
        self._stderr: BinaryIO | None = stderr
        self._identity = identity
        self._exit_code: int | None = None
        self._closed = False

    async def write_stdin(self, content: bytes) -> None:
        stream = self._stdin
        if stream is None:
            raise SandboxError("process_io", "Plugin input stream is closed")
        try:
            await asyncio.to_thread(_write_binary, stream, content)
        except (BrokenPipeError, OSError) as error:
            raise SandboxError("process_io", "Plugin input stream failed") from error

    async def close_stdin(self) -> None:
        stream = self._stdin
        self._stdin = None
        if stream is not None:
            await asyncio.to_thread(_close_binary, stream)

    async def read_stdout(self, size: int) -> bytes:
        stream = self._stdout
        if stream is None:
            return b""
        return await asyncio.to_thread(_read_binary, stream, size)

    async def read_stderr(self, size: int) -> bytes:
        stream = self._stderr
        if stream is None:
            return b""
        return await asyncio.to_thread(_read_binary, stream, size)

    async def wait(self) -> int:
        if self._exit_code is None:
            self._exit_code = await asyncio.to_thread(self._wait_sync)
        return self._exit_code

    async def terminate_tree(self) -> None:
        terminated = await asyncio.to_thread(
            self._api.kernel32.TerminateJobObject,
            wintypes.HANDLE(self._job_handle),
            1,
        )
        if not terminated:
            raise SandboxError("sandbox_cleanup", "Plugin process tree could not be terminated")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.close_stdin()
        stdout, stderr = self._stdout, self._stderr
        self._stdout = None
        self._stderr = None
        for stream in (stdout, stderr):
            if stream is not None:
                await asyncio.to_thread(_close_binary, stream)
        self._api.close_handle(self._process_handle)
        self._api.close_handle(self._job_handle)
        try:
            self._api.delete_profile(self._identity, ignore_missing=True)
        except OSError as error:
            raise SandboxError(
                "sandbox_cleanup",
                "Plugin AppContainer profile could not be removed",
            ) from error

    def _wait_sync(self) -> int:
        status = int(
            self._api.kernel32.WaitForSingleObject(
                wintypes.HANDLE(self._process_handle),
                _INFINITE,
            )
        )
        if status == _WAIT_FAILED or status != _WAIT_OBJECT_0:
            raise OSError("Plugin process wait failed")
        exit_code = wintypes.DWORD()
        if not self._api.kernel32.GetExitCodeProcess(
            wintypes.HANDLE(self._process_handle),
            ctypes.byref(exit_code),
        ):
            raise OSError("Plugin process exit code could not be read")
        return int(exit_code.value)


def _start_win32_process(policy: SandboxNativePolicy) -> SandboxProcess:
    if not policy.app_container or policy.network_capabilities:
        raise SandboxError(
            "sandbox_unavailable",
            "Plugin native policy would weaken AppContainer isolation",
        )
    if (
        policy.shell
        or not policy.hidden_window
        or policy.inherited_handles != ("stdin", "stdout", "stderr")
        or policy.job_active_process_limit != 1
        or not policy.job_kill_on_close
    ):
        raise SandboxError(
            "sandbox_unavailable",
            "Plugin native policy does not meet the required restrictions",
        )

    api = _Win32Api()
    sid: int | None = None
    job: int | None = None
    process_info = _ProcessInformation()
    attribute_pointer: wintypes.LPVOID | None = None
    handles: set[int] = set()
    parent_streams: list[BinaryIO] = []
    created_process = False
    handed_off = False
    try:
        api.delete_profile(policy.identity, ignore_missing=True)
        sid = api.create_profile(policy.identity)
        profile_root = api.profile_folder(sid)
        native_local = profile_root / "LocalState"
        native_local.mkdir(parents=True, exist_ok=True)
        native_working_directory = native_local / "call"
        native_working_directory.mkdir(parents=True, exist_ok=False)
        native_temp = native_local / "Temp"
        native_temp.mkdir(parents=True, exist_ok=True)
        native_executable = _copy_worker_executable(policy.executable, profile_root)

        job = api.create_job(policy.job_memory_limit_bytes)
        child_stdin, parent_stdin = api.create_pipe()
        handles.update({child_stdin, parent_stdin})
        parent_stdout, child_stdout = api.create_pipe()
        handles.update({parent_stdout, child_stdout})
        parent_stderr, child_stderr = api.create_pipe()
        handles.update({parent_stderr, child_stderr})
        for parent_handle in (parent_stdin, parent_stdout, parent_stderr):
            api.clear_inheritance(parent_handle)

        attribute_size = ctypes.c_size_t()
        api.kernel32.InitializeProcThreadAttributeList(
            None,
            2,
            0,
            ctypes.byref(attribute_size),
        )
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or not attribute_size.value:
            raise OSError("Plugin process attribute size could not be determined")
        attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
        attribute_pointer = ctypes.cast(attribute_buffer, wintypes.LPVOID)
        if not api.kernel32.InitializeProcThreadAttributeList(
            attribute_pointer,
            2,
            0,
            ctypes.byref(attribute_size),
        ):
            raise OSError("Plugin process attribute list could not be initialized")

        security_capabilities = _SecurityCapabilities(
            AppContainerSid=wintypes.LPVOID(sid),
            Capabilities=ctypes.POINTER(_SidAndAttributes)(),
            CapabilityCount=0,
            Reserved=0,
        )
        if not api.kernel32.UpdateProcThreadAttribute(
            attribute_pointer,
            0,
            _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
            ctypes.byref(security_capabilities),
            ctypes.sizeof(security_capabilities),
            None,
            None,
        ):
            raise OSError("Plugin AppContainer process attribute could not be set")
        inherited = (wintypes.HANDLE * 3)(
            wintypes.HANDLE(child_stdin),
            wintypes.HANDLE(child_stdout),
            wintypes.HANDLE(child_stderr),
        )
        if not api.kernel32.UpdateProcThreadAttribute(
            attribute_pointer,
            0,
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.cast(inherited, wintypes.LPVOID),
            ctypes.sizeof(inherited),
            None,
            None,
        ):
            raise OSError("Plugin inherited handle list could not be restricted")

        startup = _StartupInfoExW()
        startup.StartupInfo.cb = ctypes.sizeof(_StartupInfoExW)
        startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = wintypes.HANDLE(child_stdin)
        startup.StartupInfo.hStdOutput = wintypes.HANDLE(child_stdout)
        startup.StartupInfo.hStdError = wintypes.HANDLE(child_stderr)
        startup.lpAttributeList = attribute_pointer
        command = subprocess.list2cmdline(
            [str(native_executable), *policy.arguments]
        )
        command_buffer = ctypes.create_unicode_buffer(command)
        environment = dict(policy.environment)
        environment.update(
            {
                "LOCALAPPDATA": str(native_local),
                "PATH": str(native_executable.parent),
                "TEMP": str(native_temp),
                "TMP": str(native_temp),
            }
        )
        environment_buffer = _environment_buffer(environment)
        creation_flags = (
            _CREATE_SUSPENDED
            | _CREATE_UNICODE_ENVIRONMENT
            | _EXTENDED_STARTUPINFO_PRESENT
            | _CREATE_NO_WINDOW
        )
        if not api.kernel32.CreateProcessW(
            str(native_executable),
            command_buffer,
            None,
            None,
            True,
            creation_flags,
            ctypes.cast(environment_buffer, wintypes.LPVOID),
            str(native_working_directory),
            ctypes.byref(startup.StartupInfo),
            ctypes.byref(process_info),
        ):
            raise OSError("Plugin AppContainer process could not be created")
        created_process = True
        if not api.kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(job),
            process_info.hProcess,
        ):
            raise OSError("Plugin process could not be assigned to its Job Object")
        if int(api.kernel32.ResumeThread(process_info.hThread)) == 0xFFFFFFFF:
            raise OSError("Plugin process could not be resumed")

        for child_handle in (child_stdin, child_stdout, child_stderr):
            api.close_handle(child_handle)
            handles.discard(child_handle)
        api.close_handle(int(process_info.hThread))
        process_info.hThread = None
        stdin_descriptor = _binary_descriptor(parent_stdin, write=True)
        handles.discard(parent_stdin)
        stdin_stream = _binary_stream(stdin_descriptor, write=True)
        parent_streams.append(stdin_stream)
        stdout_descriptor = _binary_descriptor(parent_stdout, write=False)
        handles.discard(parent_stdout)
        stdout_stream = _binary_stream(stdout_descriptor, write=False)
        parent_streams.append(stdout_stream)
        stderr_descriptor = _binary_descriptor(parent_stderr, write=False)
        handles.discard(parent_stderr)
        stderr_stream = _binary_stream(stderr_descriptor, write=False)
        parent_streams.append(stderr_stream)
        process = _Win32SandboxProcess(
            api=api,
            process_handle=int(process_info.hProcess),
            job_handle=job,
            stdin=stdin_stream,
            stdout=stdout_stream,
            stderr=stderr_stream,
            identity=policy.identity,
        )
        process_info.hProcess = None
        job = None
        parent_streams.clear()
        handed_off = True
        return process
    except Exception:
        if created_process and process_info.hProcess:
            if job:
                api.kernel32.TerminateJobObject(wintypes.HANDLE(job), 1)
            else:
                api.kernel32.TerminateProcess(process_info.hProcess, 1)
        raise
    finally:
        if attribute_pointer is not None:
            api.kernel32.DeleteProcThreadAttributeList(attribute_pointer)
        if process_info.hThread:
            api.close_handle(int(process_info.hThread))
        if process_info.hProcess:
            api.close_handle(int(process_info.hProcess))
        for stream in parent_streams:
            _close_binary(stream)
        for handle in handles:
            api.close_handle(handle)
        if sid is not None:
            api.free_sid(sid)
        if job is not None:
            api.close_handle(job)
        if not handed_off:
            api.delete_profile(policy.identity, ignore_missing=True)


def _environment_buffer(environment: Mapping[str, str]) -> ctypes.Array[Any]:
    entries = [f"{key}={value}" for key, value in sorted(environment.items())]
    if any("\x00" in entry for entry in entries):
        raise ValueError("Plugin native environment cannot contain NUL")
    # create_unicode_buffer appends one NUL; the explicit trailing NUL makes the block
    # double-NUL terminated as required by CreateProcessW.
    return ctypes.create_unicode_buffer("\x00".join(entries) + "\x00")


def _copy_worker_executable(source: Path, profile_root: Path) -> Path:
    runtime_directory = profile_root / "LocalState" / "runtime"
    runtime_directory.mkdir(parents=True, exist_ok=False)
    target = runtime_directory / "paper-reader-plugin-worker.exe"
    resolved_root = profile_root.resolve()
    resolved_target = target.resolve()
    _require_child(resolved_root, resolved_target)

    copied_bytes = 0
    with source.open("rb") as source_stream, target.open("xb") as target_stream:
        while chunk := source_stream.read(1024 * 1024):
            copied_bytes += len(chunk)
            target_stream.write(chunk)
        target_stream.flush()
        os.fsync(target_stream.fileno())
    if copied_bytes <= 0 or target.stat().st_size != copied_bytes:
        raise OSError("Plugin worker executable copy is invalid")
    target.chmod(stat.S_IREAD | stat.S_IEXEC)
    return resolved_target


def _binary_descriptor(handle: int, *, write: bool) -> int:
    import msvcrt

    flags = os.O_BINARY | (os.O_WRONLY if write else os.O_RDONLY)
    return msvcrt.open_osfhandle(handle, flags)


def _binary_stream(descriptor: int, *, write: bool) -> BinaryIO:
    mode = "wb" if write else "rb"
    try:
        return cast(BinaryIO, os.fdopen(descriptor, mode, buffering=0))
    except Exception:
        os.close(descriptor)
        raise


def _write_binary(stream: BinaryIO, content: bytes) -> None:
    stream.write(content)
    stream.flush()


def _read_binary(stream: BinaryIO, size: int) -> bytes:
    return stream.read(size)


def _close_binary(stream: BinaryIO) -> None:
    with suppress(OSError):
        stream.close()


def _hresult_succeeded(value: int) -> bool:
    return ctypes.c_long(value).value >= 0


def _unsigned_hresult(value: int) -> int:
    return value & 0xFFFFFFFF


def _charge_output(budget: list[int], size: int, limit: int) -> None:
    budget[0] += size
    if budget[0] > limit:
        raise SandboxError(
            "output_limit",
            "Plugin sandbox output exceeded the configured limit",
        )


def _require_child(root: Path, target: Path) -> None:
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError("Plugin working directory is outside its root") from error
    if target == root:
        raise ValueError("Plugin working directory is outside its root")


def _remove_read_only(
    operation: Callable[[str], object],
    path: str,
    error: BaseException,
) -> None:
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IWRITE)
    operation(path)
