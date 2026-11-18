from __future__ import annotations

import importlib
import json
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
RELEASE_VERSION = "3.2.0"
RELEASE_DATE = "2026-11-18"


def _project_version_from_uv_lock() -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    matches = [
        package["version"]
        for package in lock["package"]
        if package.get("name") == "paper-reader" and package.get("source") == {"editable": "."}
    ]
    assert len(matches) == 1
    return str(matches[0])


def test_release_version_is_one_cross_file_fact() -> None:
    package = importlib.import_module("paper_reader")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    npm_package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    npm_lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    openapi = json.loads((ROOT / "openapi" / "openapi.json").read_text(encoding="utf-8"))
    windows_version = (ROOT / "packaging" / "windows" / "version-info.txt").read_text(
        encoding="utf-8"
    )
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    bug_report = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(
        encoding="utf-8"
    )
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert package.__version__ == RELEASE_VERSION
    assert pyproject["project"]["version"] == RELEASE_VERSION
    assert npm_package["version"] == RELEASE_VERSION
    assert npm_lock["version"] == RELEASE_VERSION
    assert npm_lock["packages"][""]["version"] == RELEASE_VERSION
    assert _project_version_from_uv_lock() == RELEASE_VERSION
    assert openapi["info"]["version"] == RELEASE_VERSION

    numeric_version = tuple(int(part) for part in RELEASE_VERSION.split(".")) + (0,)
    assert f"filevers={numeric_version}" in windows_version
    assert f"prodvers={numeric_version}" in windows_version
    assert windows_version.count(f"'{RELEASE_VERSION}.0'") == 2

    assert f"version: {RELEASE_VERSION}" in citation
    assert f"date-released: {RELEASE_DATE}" in citation
    assert f'placeholder: "{RELEASE_VERSION}"' in bug_report
    supported_line = RELEASE_VERSION.rsplit(".", 1)[0]
    assert f"| `{supported_line}.x` | Current supported release line |" in security

    assert f"## [{RELEASE_VERSION}] - {RELEASE_DATE}" in changelog
    assert (
        f"[Unreleased]: https://github.com/Lisalsj/folioskiff/compare/"
        f"v{RELEASE_VERSION}...HEAD"
    ) in changelog
    assert (
        f"[{RELEASE_VERSION}]: https://github.com/Lisalsj/folioskiff/releases/tag/"
        f"v{RELEASE_VERSION}"
    ) in changelog


def test_cli_reports_the_same_release_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from paper_reader.cli import build_parser

    with pytest.raises(SystemExit) as stopped:
        build_parser().parse_args(["--version"])

    assert stopped.value.code == 0
    assert capsys.readouterr().out == f"paper-reader {RELEASE_VERSION}\n"
