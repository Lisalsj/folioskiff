"""Bounded, no-shell subprocess execution for local Agent backends."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from paper_reader.agents.contracts import AgentBackendError, AgentErrorCode
from paper_reader.logging import redact_secrets

_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_ALLOWED_ENVIRONMENT = {
    "appdata",
    "codex_home",
    "comspec",
    "homedrive",
    "homepath",
    "lang",
    "lc_all",
    "localappdata",
    "no_color",
    "path",
    "pathext",
    "systemdrive",
    "systemroot",
    "temp",
    "term",
    "tmp",
    "userprofile",
    "windir",
}
_SENSITIVE_FLAG = {
    "--api-key",
    "--authorization",
    "--credential",
    "--password",
    "--secret",
    "--token",
}
_INLINE_SECRET = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+\S+|bearer\s+\S+|sk-[A-Za-z0-9_-]+)"
)


@dataclass(frozen=True, slots=True)
class SecureProcessPolicy:
    executable: Path
    arguments: tuple[str, ...]
    environment: Mapping[str, str]
    working_directory: Path
    timeout_seconds: float
    output_limit_bytes: int

    def __post_init__(self) -> None:
        executable = Path(self.executable)
        working_directory = Path(self.working_directory)
        if not executable.is_file():
            raise ValueError("executable must name an existing file")
        if not working_directory.is_dir():
            raise ValueError("working_directory must name an existing directory")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 7200:
            raise ValueError("timeout_seconds must be between 0 and 7200")
        if self.output_limit_bytes <= 0 or self.output_limit_bytes > 50 * 1024 * 1024:
            raise ValueError("output_limit_bytes must be between 1 and 52428800")
        if any("\x00" in argument for argument in self.arguments):
            raise ValueError("Process arguments cannot contain NUL")
        if any("\x00" in key or "\x00" in value for key, value in self.environment.items()):
            raise ValueError("Process environment cannot contain NUL")
        object.__setattr__(self, "executable", executable.resolve())
        object.__setattr__(self, "working_directory", working_directory.resolve())
        object.__setattr__(self, "environment", dict(self.environment))


@dataclass(frozen=True, slots=True)
class SecureProcessResult:
    exit_code: int
    stdout: str
    stderr: str


class _OutputLimitExceeded(RuntimeError):
    pass


def build_allowlisted_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    inherited = os.environ if source is None else source
    return {
        key: value
        for key, value in inherited.items()
        if key.casefold() in _ALLOWED_ENVIRONMENT and "\x00" not in key and "\x00" not in value
    }


def resolve_executable(
    name: str,
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> Path:
    resolved = which(name)
    if resolved is None:
        raise AgentBackendError(
            AgentErrorCode.UNAVAILABLE,
            f"{name} is not installed or is not available on PATH",
        )
    return Path(resolved)


def create_task_directory(cache_root: Path, task_id: str) -> Path:
    if not _TASK_ID.fullmatch(task_id):
        raise ValueError("task_id must be a safe identifier")
    root = (Path(cache_root) / "agent-jobs").resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / task_id
    if target.is_symlink():
        raise ValueError("Agent task path is outside the Agent task root")
    if target.exists():
        resolved = target.resolve()
        _require_child(root, resolved)
    else:
        target.mkdir()
        resolved = target.resolve()
        _require_child(root, resolved)
    return resolved


def cleanup_task_directory(cache_root: Path, task_directory: Path) -> None:
    root = (Path(cache_root) / "agent-jobs").resolve()
    target = Path(task_directory).resolve()
    _require_child(root, target)
    if target.exists():
        shutil.rmtree(target, onexc=_remove_read_only)


def redact_command(arguments: Sequence[str]) -> str:
    rendered: list[str] = []
    redact_next = False
    for argument in arguments:
        if redact_next:
            rendered.append("<redacted>")
            redact_next = False
            continue
        flag = argument.casefold()
        if flag in _SENSITIVE_FLAG:
            rendered.append(argument)
            redact_next = True
        elif any(flag.startswith(f"{item}=") for item in _SENSITIVE_FLAG):
            rendered.append(f"{argument.split('=', 1)[0]}=<redacted>")
        elif _INLINE_SECRET.search(argument):
            rendered.append("<redacted>")
        else:
            rendered.append(argument)
    return " ".join(rendered)


def redact_diagnostic(value: str, *, limit: int = 4000) -> str:
    """Bound diagnostics and remove common bearer/API-key material."""
    return redact_secrets(value)[:limit]


async def run_secure_process(
    policy: SecureProcessPolicy,
    *,
    stdin: str,
    cancelled: Callable[[], bool],
    on_stdout_line: Callable[[str], Awaitable[None]] | None = None,
) -> SecureProcessResult:
    if cancelled():
        raise AgentBackendError(
            AgentErrorCode.CANCELLED,
            "Agent process was cancelled before launch",
            retryable=True,
        )

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(__import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        process = await asyncio.create_subprocess_exec(
            str(policy.executable),
            *policy.arguments,
            cwd=str(policy.working_directory),
            env=dict(policy.environment),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
    except (FileNotFoundError, PermissionError, OSError) as error:
        raise AgentBackendError(
            AgentErrorCode.UNAVAILABLE,
            "Agent process could not be started",
            diagnostic=type(error).__name__,
        ) from error

    budget = [0]
    stdout_task = asyncio.create_task(
        _read_bounded(
            process.stdout,
            policy.output_limit_bytes,
            budget,
            on_line=on_stdout_line,
        )
    )
    stderr_task = asyncio.create_task(
        _read_bounded(process.stderr, policy.output_limit_bytes, budget)
    )
    writer_task = asyncio.create_task(_write_stdin(process, stdin.encode("utf-8")))
    wait_task = asyncio.create_task(process.wait())
    tasks = (stdout_task, stderr_task, writer_task, wait_task)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + policy.timeout_seconds
    failure: AgentBackendError | None = None
    try:
        while True:
            for task in tasks:
                if task.done() and not task.cancelled():
                    exception = task.exception()
                    if isinstance(exception, _OutputLimitExceeded):
                        raise AgentBackendError(
                            AgentErrorCode.OUTPUT_LIMIT,
                            "Agent process output exceeded the configured limit",
                        ) from exception
                    if isinstance(exception, AgentBackendError):
                        raise exception
                    if exception is not None:
                        raise AgentBackendError(
                            AgentErrorCode.PROCESS,
                            "Agent process stream failed",
                            diagnostic=type(exception).__name__,
                        ) from exception
            if all(task.done() for task in tasks):
                break
            if cancelled():
                raise AgentBackendError(
                    AgentErrorCode.CANCELLED,
                    "Agent process was cancelled",
                    retryable=True,
                )
            if loop.time() >= deadline:
                raise AgentBackendError(
                    AgentErrorCode.TIMEOUT,
                    "Agent process timed out",
                    retryable=True,
                )
            await asyncio.sleep(min(0.02, max(0.0, deadline - loop.time())))
    except AgentBackendError as error:
        failure = error
        await _terminate_process_tree(process)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    if failure is not None:
        raise failure
    return SecureProcessResult(
        exit_code=wait_task.result(),
        stdout=stdout_task.result().decode("utf-8", errors="replace"),
        stderr=stderr_task.result().decode("utf-8", errors="replace"),
    )


async def _read_bounded(
    stream: asyncio.StreamReader | None,
    limit: int,
    budget: list[int],
    *,
    on_line: Callable[[str], Awaitable[None]] | None = None,
) -> bytes:
    if stream is None:  # pragma: no cover - PIPE is always configured
        return b""
    chunks: list[bytes] = []
    pending_line = bytearray()
    while True:
        chunk = await stream.read(64 * 1024)
        if not chunk:
            if on_line is not None and pending_line:
                await on_line(bytes(pending_line).decode("utf-8", errors="replace"))
            return b"".join(chunks)
        budget[0] += len(chunk)
        if budget[0] > limit:
            raise _OutputLimitExceeded
        chunks.append(chunk)
        if on_line is not None:
            pending_line.extend(chunk)
            while True:
                newline = pending_line.find(b"\n")
                if newline < 0:
                    break
                line = bytes(pending_line[:newline])
                del pending_line[: newline + 1]
                await on_line(line.rstrip(b"\r").decode("utf-8", errors="replace"))


async def _write_stdin(process: asyncio.subprocess.Process, content: bytes) -> None:
    if process.stdin is None:  # pragma: no cover - PIPE is always configured
        return
    try:
        process.stdin.write(content)
        await process.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        process.stdin.close()


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=3)
        except (FileNotFoundError, OSError, TimeoutError):
            process.kill()
    else:  # pragma: no cover - v1.0 targets Windows
        process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except TimeoutError:
        process.kill()
        await process.wait()


def _require_child(root: Path, target: Path) -> None:
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError("Agent task path is outside the Agent task root") from error
    if target == root:
        raise ValueError("Agent task path is outside the Agent task root")


def _remove_read_only(
    operation: Callable[[str], object],
    path: str,
    error: BaseException,
) -> None:
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IWRITE)
    operation(path)
