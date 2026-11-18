from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")

FAST_COMMANDS = (
    "uv lock --check",
    "uv pip check",
    "uv run ruff check src tests scripts examples",
    "uv run python -m mypy src scripts",
    "npm run audit",
    "npm test",
    "npm run typecheck",
    "npm run build",
)


def _quality_plan(scope: str) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL is not None
    return subprocess.run(
        [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "quality.ps1"),
            "-Scope",
            scope,
            "-PlanOnly",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required on Windows")
def test_local_quality_gate_exposes_closed_scopes_and_real_commands() -> None:
    source = (ROOT / "scripts" / "quality.ps1").read_text(encoding="utf-8")
    fast = _quality_plan("fast")
    full = _quality_plan("full")
    release = _quality_plan("release")

    assert fast.returncode == 0, fast.stderr
    assert tuple(fast.stdout.splitlines()) == FAST_COMMANDS
    assert full.returncode == 0, full.stderr
    assert tuple(full.stdout.splitlines()[: len(FAST_COMMANDS)]) == FAST_COMMANDS
    parallel_suite = 'uv run pytest -n 2 --dist=loadscope -m "not resource_serial"'
    serial_suite = "uv run pytest -m resource_serial"

    assert parallel_suite in full.stdout
    assert serial_suite in full.stdout
    assert full.stdout.index(parallel_suite) < full.stdout.index(serial_suite)
    assert "--cov-append" in full.stdout
    assert "[System.IO.Directory]::CreateDirectory('.tmp')" in source
    assert "npm run test:e2e" in full.stdout
    assert release.returncode == 0, release.stderr
    assert "scripts/build_windows.py" in release.stdout
    assert "scripts/verify_distribution.py" in release.stdout
    assert "npm audit fix" not in release.stdout
    assert "git reset" not in release.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required on Windows")
def test_local_quality_gate_rejects_unknown_scope() -> None:
    result = _quality_plan("unknown")

    assert result.returncode != 0
    assert "ValidateSet" in result.stderr or "cannot validate argument" in result.stderr


def test_node_quality_aliases_use_the_windows_quality_gate() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"]["quality:fast"].endswith(
        "scripts/quality.ps1 -Scope fast"
    )
    assert package["scripts"]["quality"].endswith("scripts/quality.ps1 -Scope full")
    assert package["scripts"]["quality:release"].endswith(
        "scripts/quality.ps1 -Scope release"
    )


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required on Windows")
def test_quality_gate_does_not_override_application_update_policy(tmp_path: Path) -> None:
    command = """@echo off
if defined PAPER_READER_DISABLE_UPDATE_CHECKS (
  >>\"%QUALITY_ENV_LOG%\" echo set
) else (
  >>\"%QUALITY_ENV_LOG%\" echo unset
)
exit /b 0
"""
    for executable in ("uv.cmd", "npm.cmd"):
        (tmp_path / executable).write_text(command, encoding="utf-8")

    log = tmp_path / "environment.log"
    environment = os.environ.copy()
    environment.pop("PAPER_READER_DISABLE_UPDATE_CHECKS", None)
    environment["QUALITY_ENV_LOG"] = str(log)
    environment["PATH"] = f"{tmp_path}{os.pathsep}{environment['PATH']}"

    result = subprocess.run(
        [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "quality.ps1"),
            "-Scope",
            "fast",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == ["unset"] * 8
