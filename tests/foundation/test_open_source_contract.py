from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
REQUIRED = (
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SUPPORT.md",
    "GOVERNANCE.md",
    "CITATION.cff",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
)


def test_public_repository_files_exist_and_are_nonempty() -> None:
    for relative in REQUIRED:
        path = ROOT / relative
        assert path.is_file(), relative
        assert len(path.read_text(encoding="utf-8").strip()) >= 80, relative


def test_python_and_node_metadata_point_to_the_canonical_repository() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    project = pyproject["project"]
    assert project["license"] == "Apache-2.0"
    assert project["urls"]["Repository"] == "https://github.com/Lisalsj/folioskiff"
    assert project["urls"]["Issues"].endswith("/issues")
    assert any("Microsoft :: Windows" in item for item in project["classifiers"])
    assert package["license"] == "Apache-2.0"
    assert package["repository"]["url"].endswith("Lisalsj/folioskiff.git")
    assert package["bugs"]["url"].endswith("/issues")


def test_readme_language_entries_and_security_status_are_consistent() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    assert "[简体中文](README.zh-CN.md)" in english
    assert "[English](README.md)" in chinese
    for heading in ("Features", "Quick start", "AI and network boundary", "Contributing"):
        assert f"## {heading}" in english
    for heading in ("功能", "快速开始", "AI 与联网边界", "参与贡献"):
        assert f"## {heading}" in chinese
    assert "3.2.0" in security
    assert "has not yet published a stable release" not in security
