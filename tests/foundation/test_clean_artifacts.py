from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
ROOT = Path(__file__).parents[2]
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required")


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    scripts = workspace / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/clean-artifacts.ps1", scripts / "clean-artifacts.ps1")
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    return workspace


def _run(workspace: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL is not None
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(workspace / "scripts/clean-artifacts.ps1"),
            *arguments,
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_cleanup_removes_only_generated_artifacts_and_can_preview(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    for relative in (
        ".tmp/result.txt",
        "src/module/__pycache__/test.pyc",
        "src/keep.py",
        "src/paper_reader/static/index.html",
        ".venv/keep",
        "node_modules/keep",
    ):
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    preview = _run(workspace, "-WhatIf")
    assert preview.returncode == 0, preview.stderr
    assert (workspace / ".tmp/result.txt").is_file()
    result = _run(workspace)
    assert result.returncode == 0, result.stderr
    assert not (workspace / ".tmp").exists()
    assert not (workspace / "src/module/__pycache__").exists()
    for relative in (
        "src/keep.py",
        "src/paper_reader/static/index.html",
        ".venv/keep",
        "node_modules/keep",
    ):
        assert (workspace / relative).is_file()


def test_cleanup_preserves_tracked_files_under_an_artifact_directory(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    path = workspace / ".tmp/keep.txt"
    path.parent.mkdir()
    path.write_text("tracked fixture", encoding="utf-8")
    subprocess.run(["git", "add", ".tmp/keep.txt"], cwd=workspace, check=True)
    result = _run(workspace)
    assert result.returncode == 0, result.stderr
    assert "tracked files" in result.stdout
    assert path.read_text(encoding="utf-8") == "tracked fixture"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction safety")
def test_cleanup_rejects_junctions_to_directories_outside_the_workspace(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    keep = outside / "keep.txt"
    keep.write_text("external fixture", encoding="utf-8")
    artifact = workspace / ".tmp"
    artifact.mkdir()
    disposable = artifact / "generated.txt"
    disposable.write_text("disposable", encoding="utf-8")

    def quote(path: Path) -> str:
        return "'" + str(path).replace("'", "''") + "'"

    assert POWERSHELL is not None
    subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"New-Item -ItemType Junction -Path {quote(artifact / 'linked')} "
            f"-Target {quote(outside)} | Out-Null",
        ],
        check=True,
        capture_output=True,
    )
    result = _run(workspace)
    assert result.returncode == 0, result.stderr
    assert "link or junction" in result.stdout
    assert keep.read_text(encoding="utf-8") == "external fixture"
    assert not disposable.exists()
