from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _locked_versions(package_name: str) -> tuple[tuple[int, ...], ...]:
    payload = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    suffix = f"node_modules/{package_name}"
    return tuple(
        _version_tuple(str(item["version"]))
        for path, item in payload["packages"].items()
        if path == suffix or path.endswith(f"/{suffix}")
    )


def test_node_toolchain_stays_on_node_24_compatible_majors() -> None:
    payload = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert payload["engines"]["node"] == ">=24"
    assert payload["devDependencies"]["typescript"] == "^5.9.3"
    assert payload["devDependencies"]["jsdom"] == "^27.4.0"
    assert payload["devDependencies"]["@types/node"] == "^24.13.3"


def test_node_lock_excludes_known_denial_of_service_versions() -> None:
    brace_versions = _locked_versions("brace-expansion")
    nanoid_versions = _locked_versions("nanoid")
    yaml_versions = _locked_versions("js-yaml")
    source_map_versions = _locked_versions("source-map-js")
    renderer_versions = _locked_versions("@vue/server-renderer")

    assert brace_versions
    assert nanoid_versions
    assert yaml_versions
    assert source_map_versions
    assert renderer_versions
    assert all(version >= (5, 0, 12) for version in brace_versions)
    assert all(version >= (3, 3, 18) for version in nanoid_versions)
    assert all(version >= (4, 3, 0) for version in yaml_versions)
    assert all(version >= (1, 2, 2) for version in source_map_versions)
    assert all(version >= (3, 5, 42) for version in renderer_versions)


def test_python_lock_excludes_vulnerable_urllib3_versions() -> None:
    payload = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = tuple(
        _version_tuple(item["version"]) for item in payload["package"] if item["name"] == "urllib3"
    )

    assert versions
    assert all(version >= (2, 8, 0) for version in versions)
