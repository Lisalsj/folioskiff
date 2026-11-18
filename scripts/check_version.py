"""Validate that release metadata names one application version."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _capture(pattern: str, value: str, *, label: str) -> str:
    match = re.search(pattern, value, re.MULTILINE)
    if match is None:
        raise SystemExit(f"missing release metadata: {label}")
    return match.group(1)


def main() -> int:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    npm_lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    uv_lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    openapi = json.loads((ROOT / "openapi/openapi.json").read_text(encoding="utf-8"))
    versions = {
        "package.json": str(package["version"]),
        "package-lock.json": str(npm_lock["version"]),
        "package-lock.json root": str(npm_lock["packages"][""]["version"]),
        "pyproject.toml": str(pyproject["project"]["version"]),
        "openapi/openapi.json": str(openapi["info"]["version"]),
    }
    editable = [
        item["version"]
        for item in uv_lock["package"]
        if item.get("name") == "paper-reader" and item.get("source") == {"editable": "."}
    ]
    if len(editable) != 1:
        raise SystemExit("uv.lock must contain exactly one editable paper-reader package")
    versions["uv.lock"] = str(editable[0])
    init = (ROOT / "src/paper_reader/__init__.py").read_text(encoding="utf-8")
    versions["src/paper_reader/__init__.py"] = _capture(
        r'__version__\s*=\s*"([^"]+)"', init, label="__version__"
    )
    windows = (ROOT / "packaging/windows/version-info.txt").read_text(encoding="utf-8")
    versions["packaging/windows/version-info.txt"] = _capture(
        r"StringStruct\('ProductVersion', '([^']+)'\)",
        windows,
        label="ProductVersion",
    ).removesuffix(".0")
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    versions["CITATION.cff"] = _capture(
        r"^version:\s*(\S+)$", citation, label="citation version"
    )
    values = set(versions.values())
    if len(values) != 1 or not VERSION_RE.fullmatch(next(iter(values))):
        details = ", ".join(f"{name}={value}" for name, value in versions.items())
        raise SystemExit(f"release version mismatch: {details}")
    print(next(iter(values)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
