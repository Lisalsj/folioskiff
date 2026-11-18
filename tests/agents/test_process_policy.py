from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from paper_reader.agents.contracts import AgentBackendError, AgentErrorCode
from paper_reader.agents.process_policy import (
    SecureProcessPolicy,
    build_allowlisted_environment,
    cleanup_task_directory,
    create_task_directory,
    redact_command,
    resolve_executable,
    run_secure_process,
)


def test_allowlisted_environment_keeps_runtime_context_but_drops_credentials() -> None:
    source = {
        "PATH": "safe-path",
        "PATHEXT": ".EXE",
        "SystemRoot": r"C:\Windows",
        "USERPROFILE": r"C:\Users\Reader",
        "LOCALAPPDATA": r"C:\Users\Reader\AppData\Local",
        "TEMP": r"C:\Temp",
        "OPENAI_API_KEY": "sk-secret",
        "IEEE_XPLORE_API_KEY": "source-secret",
        "CUSTOM_TOKEN": "token-secret",
        "DATABASE_PASSWORD": "password-secret",
        "UNRELATED": "do-not-inherit",
    }

    environment = build_allowlisted_environment(source)

    assert environment["PATH"] == "safe-path"
    assert environment["USERPROFILE"] == r"C:\Users\Reader"
    assert environment["LOCALAPPDATA"] == r"C:\Users\Reader\AppData\Local"
    assert "OPENAI_API_KEY" not in environment
    assert "IEEE_XPLORE_API_KEY" not in environment
    assert "CUSTOM_TOKEN" not in environment
    assert "DATABASE_PASSWORD" not in environment
    assert "UNRELATED" not in environment


def test_task_directory_is_contained_and_cleanup_is_scoped(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    task = create_task_directory(cache_root, "job-123")
    evidence = task / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")

    assert task == (cache_root / "agent-jobs" / "job-123").resolve()
    assert task.is_dir()
    cleanup_task_directory(cache_root, task)
    assert not task.exists()
    assert cache_root.exists()

    for unsafe in ("../escape", r"..\escape", "C:/escape", "", "."):
        with pytest.raises(ValueError, match="task_id"):
            create_task_directory(cache_root, unsafe)

    with pytest.raises(ValueError, match="outside the Agent task root"):
        cleanup_task_directory(cache_root, tmp_path)


def test_task_directory_rejects_a_symlink_escape_when_supported(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    task_root = cache_root / "agent-jobs"
    task_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = task_root / "linked-job"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create symlinks")

    with pytest.raises(ValueError, match="outside the Agent task root"):
        create_task_directory(cache_root, "linked-job")


def test_executable_resolution_and_command_redaction() -> None:
    assert resolve_executable("codex", which=lambda _: r"C:\Tools\codex.exe") == Path(
        r"C:\Tools\codex.exe"
    )
    with pytest.raises(AgentBackendError) as captured:
        resolve_executable("missing", which=lambda _: None)
    assert captured.value.code is AgentErrorCode.UNAVAILABLE

    rendered = redact_command(
        (
            "codex",
            "--api-key",
            "sk-live-secret",
            "--header=Authorization: Bearer cloud-secret",
            "safe-value",
        )
    )
    assert "sk-live-secret" not in rendered
    assert "cloud-secret" not in rendered
    assert "safe-value" in rendered
    assert rendered.count("<redacted>") >= 2


@pytest.mark.asyncio
async def test_secure_process_uses_stdin_and_allowlisted_environment(tmp_path: Path) -> None:
    task = create_task_directory(tmp_path / "cache", "success-job")
    environment = build_allowlisted_environment(os.environ)
    environment["PAPER_READER_TEST_SAFE"] = "not-allowlisted"
    policy = SecureProcessPolicy(
        executable=Path(sys.executable),
        arguments=(
            "-c",
            "import os,sys; data=sys.stdin.read(); "
            "print(data.upper()); print(os.getenv('OPENAI_API_KEY','absent'))",
        ),
        environment=build_allowlisted_environment(environment),
        working_directory=task,
        timeout_seconds=10,
        output_limit_bytes=4096,
    )

    lines: list[str] = []

    async def receive_line(value: str) -> None:
        lines.append(value)

    result = await run_secure_process(
        policy,
        stdin="paper evidence",
        cancelled=lambda: False,
        on_stdout_line=receive_line,
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["PAPER EVIDENCE", "absent"]
    assert result.stderr == ""
    assert lines == ["PAPER EVIDENCE", "absent"]


@pytest.mark.asyncio
async def test_secure_process_classifies_timeout_cancellation_and_output_limit(
    tmp_path: Path,
) -> None:
    task = create_task_directory(tmp_path / "cache", "failure-job")
    base = {
        "executable": Path(sys.executable),
        "environment": build_allowlisted_environment(os.environ),
        "working_directory": task,
        "output_limit_bytes": 1024,
    }

    timeout_policy = SecureProcessPolicy(
        **base,
        arguments=("-c", "import time; time.sleep(5)"),
        timeout_seconds=0.1,
    )
    with pytest.raises(AgentBackendError) as timeout:
        await run_secure_process(timeout_policy, stdin="", cancelled=lambda: False)
    assert timeout.value.code is AgentErrorCode.TIMEOUT

    cancelled_policy = SecureProcessPolicy(
        **base,
        arguments=("-c", "print('must not run')"),
        timeout_seconds=10,
    )
    with pytest.raises(AgentBackendError) as cancelled:
        await run_secure_process(cancelled_policy, stdin="", cancelled=lambda: True)
    assert cancelled.value.code is AgentErrorCode.CANCELLED

    output_policy = SecureProcessPolicy(
        **base,
        arguments=("-c", "print('x' * 100000)"),
        timeout_seconds=10,
    )
    with pytest.raises(AgentBackendError) as output:
        await run_secure_process(output_policy, stdin="", cancelled=lambda: False)
    assert output.value.code is AgentErrorCode.OUTPUT_LIMIT


def test_secure_process_policy_rejects_invalid_limits_and_working_directory(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="working_directory"):
        SecureProcessPolicy(
            executable=Path(sys.executable),
            arguments=(),
            environment={},
            working_directory=missing,
            timeout_seconds=10,
            output_limit_bytes=1024,
        )
    with pytest.raises(ValueError, match="timeout_seconds"):
        SecureProcessPolicy(
            executable=Path(sys.executable),
            arguments=(),
            environment={},
            working_directory=tmp_path,
            timeout_seconds=0,
            output_limit_bytes=1024,
        )
    with pytest.raises(ValueError, match="output_limit_bytes"):
        SecureProcessPolicy(
            executable=Path(sys.executable),
            arguments=(),
            environment={},
            working_directory=tmp_path,
            timeout_seconds=10,
            output_limit_bytes=0,
        )
