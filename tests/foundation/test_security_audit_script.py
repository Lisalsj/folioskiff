from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_security_audit_is_closed_and_non_mutating() -> None:
    source = (ROOT / "scripts/security_audit.ps1").read_text(encoding="utf-8")
    for command in (
        "uv lock --check",
        "uv sync --locked --extra dev --extra models --extra ocr --extra packaging",
        "uv pip check",
        "uv export --locked --all-extras --no-emit-project",
        "uv run pip-audit --strict --requirement",
        "--require-hashes --disable-pip",
        "npm run audit",
        "uv run bandit -r src scripts -ll",
    ):
        assert command in source

    for forbidden in (
        "--fix",
        "audit fix",
        "pip install",
        "npm install",
        "Get-ChildItem Env:",
        "Get-Content",
        "--skip-editable",
    ):
        assert forbidden.casefold() not in source.casefold()

    assert "ConvertFrom-Json" in source
    assert "known vulnerabilities" in source
    assert "medium/high findings" in source


def test_security_audit_tools_and_package_entry_are_locked() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev_dependencies = project["project"]["optional-dependencies"]["dev"]
    assert any(item.startswith("bandit>=") for item in dev_dependencies)
    assert any(item.startswith("pip-audit>=") for item in dev_dependencies)

    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["audit:security"] == (
        "powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        "-File scripts/security_audit.ps1"
    )


def test_windows_ci_reuses_the_locked_audit_environment() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    backend = workflow.split("  frontend:", maxsplit=1)[0]

    install = "uv sync --locked --extra dev --extra models --extra ocr --extra packaging"
    audit = "npm run audit:security"
    parallel_tests = "- name: Test parallel-safe suite with coverage"
    serial_tests = "- name: Test resource-sensitive Windows contracts"
    assert backend.count(install) == 1
    assert backend.count(audit) == 1
    assert (
        backend.index(install)
        < backend.index(audit)
        < backend.index(parallel_tests)
        < backend.index(serial_tests)
    )
    assert workflow.count(audit) == 1
