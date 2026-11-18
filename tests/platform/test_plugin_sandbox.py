from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from paper_reader.plugins.sandbox import (
    SandboxError,
    SandboxExecution,
    SandboxLimits,
    SandboxNativePolicy,
    SandboxProbe,
    SandboxProcessSpec,
    WindowsSandboxLauncher,
    build_plugin_environment,
    cleanup_working_directory,
    create_empty_working_directory,
    probe_windows_isolation,
)


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: tuple[bytes, ...] = (),
        stderr: tuple[bytes, ...] = (),
        exit_code: int = 0,
        block: bool = False,
    ) -> None:
        self._stdout = list(stdout)
        self._stderr = list(stderr)
        self._exit_code = exit_code
        self._block = block
        self.stdin: list[bytes] = []
        self.stdin_closed = False
        self.terminated = False
        self.closed = False
        self._terminated = asyncio.Event()

    async def write_stdin(self, content: bytes) -> None:
        self.stdin.append(content)

    async def close_stdin(self) -> None:
        self.stdin_closed = True

    async def read_stdout(self, _size: int) -> bytes:
        if self._stdout:
            return self._stdout.pop(0)
        if self._block and not self.terminated:
            await asyncio.Event().wait()
        return b""

    async def read_stderr(self, _size: int) -> bytes:
        if self._stderr:
            return self._stderr.pop(0)
        if self._block and not self.terminated:
            await asyncio.Event().wait()
        return b""

    async def wait(self) -> int:
        if self._block and not self.terminated:
            await self._terminated.wait()
        return self._exit_code

    async def terminate_tree(self) -> None:
        self.terminated = True
        self._block = False
        self._terminated.set()

    async def close(self) -> None:
        self.closed = True


class FakeBackend:
    def __init__(
        self,
        process: FakeProcess,
        *,
        probe: SandboxProbe | None = None,
    ) -> None:
        self.process = process
        self.probe_result = probe or SandboxProbe(
            available=True,
            code=None,
            app_container=True,
            job_object=True,
        )
        self.policies: list[SandboxNativePolicy] = []

    def probe(self) -> SandboxProbe:
        return self.probe_result

    async def start(self, policy: SandboxNativePolicy) -> FakeProcess:
        self.policies.append(policy)
        return self.process


def executable_and_workspace(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    executable = tmp_path / "worker.exe"
    executable.write_bytes(b"fixture")
    workspace = create_empty_working_directory(tmp_path / "calls", "call-1")
    return executable, workspace


def process_spec(tmp_path: Path, *, limits: SandboxLimits | None = None) -> SandboxProcessSpec:
    executable, workspace = executable_and_workspace(tmp_path)
    return SandboxProcessSpec(
        executable=executable,
        arguments=("-I", "-m", "paper_reader.plugins.worker"),
        working_directory=workspace,
        environment={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        limits=limits or SandboxLimits(),
    )


def test_environment_is_minimal_and_never_inherits_credentials(tmp_path: Path) -> None:
    source = {
        "PATH": str(tmp_path),
        "SYSTEMROOT": r"C:\\Windows",
        "WINDIR": r"C:\\Windows",
        "TEMP": str(tmp_path / "unsafe-temp"),
        "OPENAI_API_KEY": "secret",
        "HTTP_PROXY": "http://127.0.0.1:8080",
        "PYTHONPATH": str(tmp_path / "injected"),
        "HOME": str(tmp_path / "home"),
    }

    environment = build_plugin_environment(tmp_path, source=source)

    assert environment == {
        "PATH": str(tmp_path.resolve()),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "SYSTEMROOT": r"C:\\Windows",
        "WINDIR": r"C:\\Windows",
    }
    assert all("KEY" not in key and "PROXY" not in key for key in environment)


def test_working_directory_is_new_empty_bounded_and_cleaned(tmp_path: Path) -> None:
    root = tmp_path / "calls"
    target = create_empty_working_directory(root, "5ed9aa7e-674a-4d38-b3d6-09ddf9f21b77")

    assert target.parent == root.resolve()
    assert list(target.iterdir()) == []
    with pytest.raises(FileExistsError):
        create_empty_working_directory(root, target.name)
    with pytest.raises(ValueError, match="identifier"):
        create_empty_working_directory(root, "../escape")

    cleanup_working_directory(root, target)
    assert not target.exists()
    with pytest.raises(ValueError, match="outside"):
        cleanup_working_directory(root, tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("wall_clock_seconds", 0),
        ("wall_clock_seconds", 3601),
        ("memory_bytes", 15 * 1024 * 1024),
        ("memory_bytes", 3 * 1024 * 1024 * 1024),
        ("output_bytes", 0),
        ("output_bytes", 17 * 1024 * 1024),
        ("max_line_bytes", 63),
    ],
)
def test_limits_reject_unbounded_or_unusable_values(field: str, value: int | float) -> None:
    values: dict[str, int | float] = {
        "wall_clock_seconds": 30,
        "memory_bytes": 256 * 1024 * 1024,
        "output_bytes": 16 * 1024 * 1024,
        "max_line_bytes": 1024 * 1024,
    }
    values[field] = value
    with pytest.raises(ValueError):
        SandboxLimits(**values)  # type: ignore[arg-type]


def test_process_spec_requires_real_executable_empty_cwd_and_safe_environment(
    tmp_path: Path,
) -> None:
    executable, workspace = executable_and_workspace(tmp_path)
    (workspace / "unexpected.txt").write_text("not empty", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        SandboxProcessSpec(
            executable=executable,
            arguments=(),
            working_directory=workspace,
            environment={},
            limits=SandboxLimits(),
        )
    (workspace / "unexpected.txt").unlink()

    with pytest.raises(ValueError, match="environment"):
        SandboxProcessSpec(
            executable=executable,
            arguments=(),
            working_directory=workspace,
            environment={"OPENAI_API_KEY": "secret"},
            limits=SandboxLimits(),
        )
    with pytest.raises(ValueError, match="NUL"):
        SandboxProcessSpec(
            executable=executable,
            arguments=("bad\x00argument",),
            working_directory=workspace,
            environment={},
            limits=SandboxLimits(),
        )


@pytest.mark.asyncio
async def test_launcher_passes_only_appcontainer_job_and_stdio_policy(tmp_path: Path) -> None:
    process = FakeProcess(stdout=(b'{"ok":true}\n',), stderr=(b"diagnostic",))
    backend = FakeBackend(process)
    launcher = WindowsSandboxLauncher(backend=backend)
    seen: list[bytes] = []

    async def on_line(line: bytes) -> bytes | None:
        seen.append(line)
        return b'{"type":"broker_response"}\n'

    result = await launcher.run(
        process_spec(tmp_path),
        initial_stdin=b'{"request":true}\n',
        cancelled=lambda: False,
        on_stdout_line=on_line,
    )

    assert result == SandboxExecution(exit_code=0, stderr=b"diagnostic")
    assert seen == [b'{"ok":true}\n']
    assert process.stdin == [b'{"request":true}\n', b'{"type":"broker_response"}\n']
    assert process.stdin_closed is True
    assert process.closed is True
    policy = backend.policies[0]
    assert policy.shell is False
    assert policy.hidden_window is True
    assert policy.app_container is True
    assert policy.network_capabilities == ()
    assert policy.inherited_handles == ("stdin", "stdout", "stderr")
    assert policy.job_active_process_limit == 1
    assert policy.job_kill_on_close is True
    assert policy.job_memory_limit_bytes == 256 * 1024 * 1024


def test_native_identity_is_unique_for_equal_call_names_in_different_roots(
    tmp_path: Path,
) -> None:
    first = SandboxNativePolicy.from_spec(process_spec(tmp_path / "first"))
    second = SandboxNativePolicy.from_spec(process_spec(tmp_path / "second"))

    assert first.identity != second.identity
    assert first.identity.startswith("paper-reader-")
    assert len(first.identity) <= 50


@pytest.mark.asyncio
async def test_unavailable_probe_and_prelaunch_cancel_execute_zero_processes(
    tmp_path: Path,
) -> None:
    unavailable = FakeBackend(
        FakeProcess(),
        probe=SandboxProbe(
            available=False,
            code="appcontainer_unavailable",
            app_container=False,
            job_object=True,
        ),
    )
    launcher = WindowsSandboxLauncher(backend=unavailable)
    with pytest.raises(SandboxError, match="unavailable") as captured:
        await launcher.run(
            process_spec(tmp_path),
            initial_stdin=b"{}\n",
            cancelled=lambda: False,
            on_stdout_line=_ignore_line,
        )
    assert captured.value.code == "sandbox_unavailable"
    assert unavailable.policies == []

    available = FakeBackend(FakeProcess())
    with pytest.raises(SandboxError, match="cancelled") as cancelled:
        await WindowsSandboxLauncher(backend=available).run(
            process_spec(tmp_path / "cancelled"),
            initial_stdin=b"{}\n",
            cancelled=lambda: True,
            on_stdout_line=_ignore_line,
        )
    assert cancelled.value.code == "cancelled"
    assert available.policies == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limits", "stdout", "expected_code"),
    [
        (
            SandboxLimits(output_bytes=8, max_line_bytes=64),
            (b"123456789",),
            "output_limit",
        ),
        (
            SandboxLimits(output_bytes=1024, max_line_bytes=64),
            (b"x" * 65,),
            "line_limit",
        ),
    ],
)
async def test_output_and_line_limits_kill_the_entire_job(
    tmp_path: Path,
    limits: SandboxLimits,
    stdout: tuple[bytes, ...],
    expected_code: str,
) -> None:
    process = FakeProcess(stdout=stdout, block=True)
    launcher = WindowsSandboxLauncher(backend=FakeBackend(process))

    with pytest.raises(SandboxError) as captured:
        await launcher.run(
            process_spec(tmp_path, limits=limits),
            initial_stdin=b"{}\n",
            cancelled=lambda: False,
            on_stdout_line=_ignore_line,
        )

    assert captured.value.code == expected_code
    assert process.terminated is True
    assert process.closed is True


@pytest.mark.asyncio
async def test_wall_clock_timeout_and_live_cancel_kill_the_entire_job(tmp_path: Path) -> None:
    timeout_process = FakeProcess(block=True)
    timeout_launcher = WindowsSandboxLauncher(backend=FakeBackend(timeout_process))
    with pytest.raises(SandboxError) as timed_out:
        await timeout_launcher.run(
            process_spec(
                tmp_path / "timeout",
                limits=SandboxLimits(wall_clock_seconds=0.05),
            ),
            initial_stdin=b"{}\n",
            cancelled=lambda: False,
            on_stdout_line=_ignore_line,
        )
    assert timed_out.value.code == "timeout"
    assert timeout_process.terminated is True

    cancel_process = FakeProcess(block=True)
    cancel_launcher = WindowsSandboxLauncher(backend=FakeBackend(cancel_process))
    should_cancel = False

    async def request_cancel() -> None:
        nonlocal should_cancel
        await asyncio.sleep(0.02)
        should_cancel = True

    cancel_task = asyncio.create_task(request_cancel())
    with pytest.raises(SandboxError) as cancelled:
        await cancel_launcher.run(
            process_spec(tmp_path / "live-cancel"),
            initial_stdin=b"{}\n",
            cancelled=lambda: should_cancel,
            on_stdout_line=_ignore_line,
        )
    await cancel_task
    assert cancelled.value.code == "cancelled"
    assert cancel_process.terminated is True


@pytest.mark.asyncio
async def test_callback_protocol_failure_is_classified_and_process_is_cleaned(
    tmp_path: Path,
) -> None:
    process = FakeProcess(stdout=(b"malformed-protocol-frame\n",))
    launcher = WindowsSandboxLauncher(backend=FakeBackend(process))

    async def reject_frame(_line: bytes) -> bytes | None:
        raise ValueError("raw parser diagnostic")

    with pytest.raises(SandboxError) as captured:
        await launcher.run(
            process_spec(tmp_path),
            initial_stdin=b"{}\n",
            cancelled=lambda: False,
            on_stdout_line=reject_frame,
        )

    assert captured.value.code == "protocol_error"
    assert "raw parser diagnostic" not in str(captured.value)
    assert process.terminated is True
    assert process.closed is True


def test_windows_capability_probe_is_structured_and_never_claims_partial_safety() -> None:
    probe = probe_windows_isolation()

    assert isinstance(probe.available, bool)
    if probe.available:
        assert probe.code is None
        assert probe.app_container is True
        assert probe.job_object is True
    else:
        assert probe.code
        assert not (probe.app_container and probe.job_object)


@pytest.mark.asyncio
async def test_default_backend_executes_stdio_only_after_native_boundaries_validate(
    tmp_path: Path,
) -> None:
    launcher = WindowsSandboxLauncher()
    probe = launcher.probe()
    if not probe.available:
        assert probe.code is not None
        return

    command_shell = Path(os.environ["COMSPEC"]).resolve()
    workspace = create_empty_working_directory(tmp_path / "native-calls", "stdio-canary")
    seen: list[bytes] = []

    async def capture(line: bytes) -> bytes | None:
        seen.append(line)
        return None

    execution = await launcher.run(
        SandboxProcessSpec(
            executable=command_shell,
            arguments=("/d", "/q", "/v:on", "/c", "set /p line= & echo !line!"),
            working_directory=workspace,
            environment=build_plugin_environment(command_shell.parent),
            limits=SandboxLimits(wall_clock_seconds=10),
        ),
        initial_stdin=b"native-canary\n",
        cancelled=lambda: False,
        on_stdout_line=capture,
    )

    assert execution.exit_code == 0
    assert execution.stderr == b""
    assert b"".join(seen).strip().endswith(b"native-canary")


@pytest.mark.asyncio
async def test_native_job_and_appcontainer_block_direct_host_access_and_child_processes(
    tmp_path: Path,
) -> None:
    launcher = WindowsSandboxLauncher()
    if not launcher.probe().available:
        return

    secret = tmp_path / "host-secret.txt"
    secret.write_text("NEVER_EXPOSE_THIS_CANARY", encoding="utf-8")
    escaped = tmp_path / "escaped.txt"
    command_shell = Path(os.environ["COMSPEC"]).resolve()
    workspace = create_empty_working_directory(tmp_path / "malicious-calls", "attack-canary")
    command = " & ".join(
        (
            f'(type "{secret}" 2>nul || echo FILE_READ_BLOCKED)',
            (
                f'(echo escaped>"{escaped}" 2>nul '
                "&& echo FILE_WRITE_ALLOWED || echo FILE_WRITE_BLOCKED)"
            ),
            "(cmd.exe /d /c exit 0 2>nul && echo CHILD_ALLOWED || echo CHILD_BLOCKED)",
            "(if defined OPENAI_API_KEY (echo CREDENTIAL_ALLOWED) else echo CREDENTIAL_BLOCKED)",
        )
    )
    seen: list[bytes] = []

    async def capture(line: bytes) -> bytes | None:
        seen.append(line)
        return None

    execution = await launcher.run(
        SandboxProcessSpec(
            executable=command_shell,
            arguments=("/d", "/q", "/c", command),
            working_directory=workspace,
            environment=build_plugin_environment(command_shell.parent),
            limits=SandboxLimits(wall_clock_seconds=10),
        ),
        initial_stdin=b"{}\n",
        cancelled=lambda: False,
        on_stdout_line=capture,
    )
    output = b"".join(seen)

    assert execution.exit_code == 0
    assert b"NEVER_EXPOSE_THIS_CANARY" not in output
    assert b"FILE_READ_BLOCKED" in output
    assert b"FILE_WRITE_BLOCKED" in output
    assert b"CHILD_BLOCKED" in output
    assert b"CREDENTIAL_BLOCKED" in output
    assert not escaped.exists()


@pytest.mark.asyncio
async def test_malicious_native_fixture_cannot_reach_host_network_or_loader(
    tmp_path: Path,
) -> None:
    launcher = WindowsSandboxLauncher()
    if not launcher.probe().available:
        return

    executable, host_library = _compile_malicious_fixture(tmp_path)
    secret = tmp_path / "database-and-credential-canary.txt"
    secret.write_text("HOST_DATABASE_SECRET_CANARY", encoding="utf-8")
    escaped = tmp_path / "fixture-escaped.txt"
    server = await asyncio.start_server(lambda _reader, writer: writer.close(), "127.0.0.1", 0)
    assert server.sockets
    loopback_port = int(server.sockets[0].getsockname()[1])
    workspace = create_empty_working_directory(tmp_path / "fixture-calls", "malicious-fixture")
    seen: list[bytes] = []

    async def capture(line: bytes) -> bytes | None:
        seen.append(line)
        return None

    try:
        execution = await launcher.run(
            SandboxProcessSpec(
                executable=executable,
                arguments=(
                    str(secret),
                    str(escaped),
                    str(loopback_port),
                    str(host_library),
                ),
                working_directory=workspace,
                environment=build_plugin_environment(executable.parent),
                limits=SandboxLimits(wall_clock_seconds=15),
            ),
            initial_stdin=b"{}\n",
            cancelled=lambda: False,
            on_stdout_line=capture,
        )
    finally:
        server.close()
        await server.wait_closed()

    output = b"".join(seen)
    assert execution.exit_code == 0
    assert b"HOST_DATABASE_SECRET_CANARY" not in output
    assert str(executable.parent).encode("utf-8") not in output
    assert b"PATH_VALUE=" in output
    for boundary in (
        b"FILE_READ_BLOCKED",
        b"FILE_WRITE_BLOCKED",
        b"CREDENTIAL_BLOCKED",
        b"LOOPBACK_BLOCKED",
        b"PUBLIC_NETWORK_BLOCKED",
        b"CHILD_BLOCKED",
        b"DLL_LOAD_BLOCKED",
    ):
        assert boundary in output
    assert not escaped.exists()


@pytest.mark.asyncio
async def test_native_fixture_invalid_and_unbounded_output_is_killed_without_poisoning_host(
    tmp_path: Path,
) -> None:
    launcher = WindowsSandboxLauncher()
    if not launcher.probe().available:
        return
    executable, _host_library = _compile_malicious_fixture(tmp_path)

    async def require_utf8(line: bytes) -> bytes | None:
        line.decode("utf-8")
        return None

    with pytest.raises(SandboxError) as invalid:
        await launcher.run(
            SandboxProcessSpec(
                executable=executable,
                arguments=("INVALID_OUTPUT",),
                working_directory=create_empty_working_directory(
                    tmp_path / "output-calls",
                    "invalid-output",
                ),
                environment=build_plugin_environment(executable.parent),
                limits=SandboxLimits(wall_clock_seconds=10),
            ),
            initial_stdin=b"{}\n",
            cancelled=lambda: False,
            on_stdout_line=require_utf8,
        )
    assert invalid.value.code == "protocol_error"

    with pytest.raises(SandboxError) as unbounded:
        await launcher.run(
            SandboxProcessSpec(
                executable=executable,
                arguments=("UNBOUNDED_OUTPUT",),
                working_directory=create_empty_working_directory(
                    tmp_path / "output-calls",
                    "unbounded-output",
                ),
                environment=build_plugin_environment(executable.parent),
                limits=SandboxLimits(
                    wall_clock_seconds=10,
                    output_bytes=2048,
                ),
            ),
            initial_stdin=b"{}\n",
            cancelled=lambda: False,
            on_stdout_line=_ignore_line,
        )
    assert unbounded.value.code == "output_limit"

    seen: list[bytes] = []

    async def capture(line: bytes) -> bytes | None:
        seen.append(line)
        return None

    execution = await launcher.run(
        SandboxProcessSpec(
            executable=executable,
            arguments=("SAFE_OUTPUT",),
            working_directory=create_empty_working_directory(
                tmp_path / "output-calls",
                "safe-after-attack",
            ),
            environment=build_plugin_environment(executable.parent),
            limits=SandboxLimits(wall_clock_seconds=10),
        ),
        initial_stdin=b"{}\n",
        cancelled=lambda: False,
        on_stdout_line=capture,
    )
    assert execution.exit_code == 0
    assert b"".join(seen).strip() == b"SAFE_OUTPUT"


@pytest.mark.asyncio
async def test_native_job_enforces_process_memory_limit(tmp_path: Path) -> None:
    launcher = WindowsSandboxLauncher()
    if not launcher.probe().available:
        return
    executable, _host_library = _compile_malicious_fixture(tmp_path)
    seen: list[bytes] = []

    async def capture(line: bytes) -> bytes | None:
        seen.append(line)
        return None

    execution = await launcher.run(
        SandboxProcessSpec(
            executable=executable,
            arguments=("UNBOUNDED_MEMORY",),
            working_directory=create_empty_working_directory(
                tmp_path / "memory-calls",
                "unbounded-memory",
            ),
            environment=build_plugin_environment(executable.parent),
            limits=SandboxLimits(
                wall_clock_seconds=15,
                memory_bytes=96 * 1024 * 1024,
            ),
        ),
        initial_stdin=b"{}\n",
        cancelled=lambda: False,
        on_stdout_line=capture,
    )

    assert b"MEMORY_STARTED" in b"".join(seen)
    assert execution.exit_code != 0


def _compile_malicious_fixture(tmp_path: Path) -> tuple[Path, Path]:
    compiler, compiler_environment = _native_compiler_environment()
    source = tmp_path / "malicious_plugin.c"
    executable = tmp_path / "malicious_plugin.exe"
    library_source = tmp_path / "host_only_library.c"
    host_library = tmp_path / "host_only_library.dll"
    source.write_text(
        r"""
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

static void status(const char *name, int allowed) {
    printf("%s_%s\n", name, allowed ? "ALLOWED" : "BLOCKED");
}

static int can_read(const wchar_t *path) {
    HANDLE file = CreateFileW(
        path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL, NULL
    );
    if (file == INVALID_HANDLE_VALUE) return 0;
    CloseHandle(file);
    return 1;
}

static int can_write(const wchar_t *path) {
    HANDLE file = CreateFileW(
        path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL, NULL
    );
    if (file == INVALID_HANDLE_VALUE) return 0;
    CloseHandle(file);
    return 1;
}

static int can_connect(const char *address, unsigned short port) {
    WSADATA data;
    SOCKET socket_handle;
    struct sockaddr_in endpoint;
    u_long nonblocking = 1;
    fd_set writable;
    fd_set failed;
    struct timeval timeout;
    int error = 0;
    int error_size = sizeof(error);
    int selected;

    if (WSAStartup(MAKEWORD(2, 2), &data) != 0) return 0;
    socket_handle = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (socket_handle == INVALID_SOCKET) {
        WSACleanup();
        return 0;
    }
    ioctlsocket(socket_handle, FIONBIO, &nonblocking);
    ZeroMemory(&endpoint, sizeof(endpoint));
    endpoint.sin_family = AF_INET;
    endpoint.sin_port = htons(port);
    InetPtonA(AF_INET, address, &endpoint.sin_addr);
    if (connect(socket_handle, (struct sockaddr *)&endpoint, sizeof(endpoint)) == 0) {
        closesocket(socket_handle);
        WSACleanup();
        return 1;
    }
    if (WSAGetLastError() != WSAEWOULDBLOCK) {
        closesocket(socket_handle);
        WSACleanup();
        return 0;
    }
    FD_ZERO(&writable);
    FD_ZERO(&failed);
    FD_SET(socket_handle, &writable);
    FD_SET(socket_handle, &failed);
    timeout.tv_sec = 1;
    timeout.tv_usec = 500000;
    selected = select(0, NULL, &writable, &failed, &timeout);
    if (selected <= 0 || getsockopt(
        socket_handle, SOL_SOCKET, SO_ERROR, (char *)&error, &error_size
    ) != 0) {
        error = 1;
    }
    closesocket(socket_handle);
    WSACleanup();
    return selected > 0 && error == 0;
}

static int can_start_child(void) {
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    wchar_t command[] = L"cmd.exe /d /c exit 0";
    BOOL created;
    ZeroMemory(&startup, sizeof(startup));
    ZeroMemory(&process, sizeof(process));
    startup.cb = sizeof(startup);
    created = CreateProcessW(
        NULL, command, NULL, NULL, FALSE, CREATE_NO_WINDOW,
        NULL, NULL, &startup, &process
    );
    if (!created) return 0;
    WaitForSingleObject(process.hProcess, 2000);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 1;
}

static void consume_memory(void) {
    const size_t size = 8 * 1024 * 1024;
    puts("MEMORY_STARTED");
    fflush(stdout);
    for (;;) {
        volatile unsigned char *block = (volatile unsigned char *)malloc(size);
        size_t offset;
        if (block == NULL) ExitProcess(91);
        for (offset = 0; offset < size; offset += 4096) block[offset] = 1;
    }
}

int wmain(int argc, wchar_t **argv) {
    wchar_t path_value[32768];
    HMODULE library;
    setvbuf(stdout, NULL, _IONBF, 0);

    if (argc == 2 && wcscmp(argv[1], L"UNBOUNDED_MEMORY") == 0) consume_memory();
    if (argc == 2 && wcscmp(argv[1], L"INVALID_OUTPUT") == 0) {
        const unsigned char invalid[] = {255, 254, 10};
        fwrite(invalid, 1, sizeof(invalid), stdout);
        return 0;
    }
    if (argc == 2 && wcscmp(argv[1], L"UNBOUNDED_OUTPUT") == 0) {
        char output[4096];
        memset(output, 'x', sizeof(output));
        for (;;) fwrite(output, 1, sizeof(output), stdout);
    }
    if (argc == 2 && wcscmp(argv[1], L"SAFE_OUTPUT") == 0) {
        puts("SAFE_OUTPUT");
        return 0;
    }
    if (argc != 5) return 64;

    status("FILE_READ", can_read(argv[1]));
    status("FILE_WRITE", can_write(argv[2]));
    status("CREDENTIAL", GetEnvironmentVariableW(L"OPENAI_API_KEY", NULL, 0) > 0);
    if (GetEnvironmentVariableW(L"PATH", path_value, 32768) > 0) {
        printf("PATH_VALUE=%ls\n", path_value);
    }
    status("LOOPBACK", can_connect("127.0.0.1", (unsigned short)_wtoi(argv[3])));
    status("PUBLIC_NETWORK", can_connect("1.1.1.1", 443));
    status("CHILD", can_start_child());
    library = LoadLibraryW(argv[4]);
    status("DLL_LOAD", library != NULL);
    if (library != NULL) FreeLibrary(library);
    return 0;
}
""".strip(),
        encoding="utf-8",
    )
    library_source.write_text(
        r"""
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

__declspec(dllexport) int host_only_value(void) { return 1; }
BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    (void)instance;
    (void)reason;
    (void)reserved;
    return TRUE;
}
""".strip(),
        encoding="utf-8",
    )
    subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/O1",
            "/MT",
            "/utf-8",
            f"/Fe:{executable}",
            str(source),
            "/link",
            "ws2_32.lib",
        ],
        check=True,
        capture_output=True,
        cwd=tmp_path,
        env=compiler_environment,
    )
    subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/LD",
            "/O1",
            "/MT",
            "/utf-8",
            f"/Fe:{host_library}",
            str(library_source),
        ],
        check=True,
        capture_output=True,
        cwd=tmp_path,
        env=compiler_environment,
    )
    assert executable.is_file()
    assert host_library.is_file()
    return executable, host_library


def _native_compiler_environment() -> tuple[Path, dict[str, str]]:
    installer_root = Path(os.environ["PROGRAMFILES(X86)"]) / "Microsoft Visual Studio"
    vswhere = installer_root / "Installer" / "vswhere.exe"
    assert vswhere.is_file(), "Windows CI must provide Visual Studio Build Tools"
    located = subprocess.run(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert located, "Windows CI must provide the x64 Visual C++ compiler"
    developer_environment = Path(located) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    assert developer_environment.is_file()
    configured = subprocess.run(
        [
            os.environ["COMSPEC"],
            "/d",
            "/s",
            "/c",
            "call vcvars64.bat >nul && set",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        cwd=developer_environment.parent,
    ).stdout
    configured_environment: dict[str, str] = {}
    for line in configured.splitlines():
        if "=" not in line or line.startswith("="):
            continue
        key, value = line.split("=", 1)
        configured_environment[key] = value
    configured_keys = {key.upper() for key in configured_environment}
    environment = {
        key: value for key, value in os.environ.items() if key.upper() not in configured_keys
    }
    environment.update(configured_environment)
    compiler_path = next(
        (value for key, value in configured_environment.items() if key.upper() == "PATH"),
        None,
    )
    compiler = shutil.which("cl.exe", path=compiler_path)
    assert compiler, "vcvars64.bat must expose cl.exe"
    return Path(compiler).resolve(), environment


async def _ignore_line(_line: bytes) -> bytes | None:
    return None
