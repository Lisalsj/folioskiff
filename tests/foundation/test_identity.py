from __future__ import annotations

import importlib
import json
import tomllib
from pathlib import Path


def test_paper_reader_package_exposes_project_identity() -> None:
    package = importlib.import_module("paper_reader")

    assert package.__version__ == "3.2.0"
    assert package.PROJECT_NAME == "paper-reader"


def test_python_project_uses_paper_reader_name_and_cli() -> None:
    project_file = Path(__file__).parents[2] / "pyproject.toml"
    project = tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]

    assert project["name"] == "paper-reader"
    assert project["scripts"]["paper-reader"] == "paper_reader.cli:main"


def test_pytest_explicitly_imports_repository_test_helpers() -> None:
    project_file = Path(__file__).parents[2] / "pyproject.toml"
    project = tomllib.loads(project_file.read_text(encoding="utf-8"))
    pytest_options = project["tool"]["pytest"]["ini_options"]
    dev_dependencies = " ".join(project["project"]["optional-dependencies"]["dev"])

    assert pytest_options["pythonpath"] == ["."]
    assert pytest_options["timeout"] == 300
    assert pytest_options["timeout_method"] == "thread"
    assert "pytest-timeout" in dev_dependencies


def test_frontend_project_uses_vite_and_paper_reader_identity() -> None:
    project_file = Path(__file__).parents[2] / "package.json"
    package = json.loads(project_file.read_text(encoding="utf-8"))

    assert package["name"] == "paper-reader"
    assert package["scripts"]["dev"] == "vite"
    assert package["scripts"]["build"] == "vue-tsc -b && vite build"


def test_legacy_runtime_and_generated_site_are_absent() -> None:
    root = Path(__file__).parents[2]
    forbidden = [
        "src/daily_paper_reader",
        "site",
        "archive",
        "agent.md",
        "function.md",
        "todo.md",
        "CLAUDE.md",
        "PRODUCT_REDESIGN_PLAN.md",
        "PRODUCT_REDESIGN_IMPLEMENTATION_PLAN.md",
    ]

    assert [path for path in forbidden if (root / path).exists()] == []
    assert (root / "docs/product-requirements.md").is_file()
    assert (root / "docs/superpowers/plans/2026-07-13-v0.1-foundation.md").is_file()
