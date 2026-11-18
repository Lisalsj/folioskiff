"""Closed resource contracts shared by source, wheel, and frozen runtimes."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, cast

_SAFE_TOP_LEVEL_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_DATABASE_REVISION = re.compile(r"^[a-z0-9][a-z0-9_]{0,79}$")
_PROTOCOL_VERSION = re.compile(r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FrozenResourceError(RuntimeError):
    """A required read-only application resource is absent from the distribution."""


class ReleaseLayoutError(ValueError):
    """The checked-in distribution layout is unsafe or ambiguous."""


@dataclass(frozen=True, slots=True)
class RuntimeResources:
    package_root: Path
    static_root: Path
    migrations_root: Path
    ocr_manifest: Path
    e5_manifest: Path
    pages_workflow: Path
    app_license: Path
    third_party_notices: Path


@dataclass(frozen=True, slots=True)
class ReleaseLayout:
    schema_version: str
    application: str
    platform: str
    architecture: str
    bundle_mode: str
    entrypoint: str
    internal_directory: str
    api_version: str
    database_schema_revision: str
    openapi_sha256: str
    plugin_protocol_version: str
    required_top_level_files: tuple[str, ...]
    forbidden_suffixes: tuple[str, ...]
    forbidden_names: tuple[str, ...]


def discover_runtime_resources(*, package_root: Path | None = None) -> RuntimeResources:
    """Resolve immutable resources without consulting the process working directory."""

    if package_root is None:
        root = Path(str(resources.files("paper_reader"))).resolve()
    else:
        root = Path(package_root).resolve()
    if not root.is_dir():
        raise FrozenResourceError("packaged_resource_missing:paper_reader")

    required = {
        "static_root": "static/index.html",
        "migrations_env": "migrations/env.py",
        "migrations_template": "migrations/script.py.mako",
        "ocr_manifest": "data/rapidocr-manifest.json",
        "e5_manifest": "data/e5-manifest.json",
        "pages_workflow": "publishing/assets/pages-workflow.yml",
        "app_license": "data/app-license.txt",
        "third_party_notices": "data/third-party-notices.md",
    }
    resolved: dict[str, Path] = {}
    for key, relative in required.items():
        candidate = root.joinpath(*relative.split("/")).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise FrozenResourceError(f"packaged_resource_unsafe:{relative}") from error
        if not candidate.is_file():
            raise FrozenResourceError(f"packaged_resource_missing:{relative}")
        resolved[key] = candidate

    return RuntimeResources(
        package_root=root,
        static_root=resolved["static_root"].parent,
        migrations_root=resolved["migrations_env"].parent,
        ocr_manifest=resolved["ocr_manifest"],
        e5_manifest=resolved["e5_manifest"],
        pages_workflow=resolved["pages_workflow"],
        app_license=resolved["app_license"],
        third_party_notices=resolved["third_party_notices"],
    )


def load_release_layout(path: Path) -> ReleaseLayout:
    """Load the model-free onedir allowlist used by build-time scanners."""

    try:
        raw = cast(Any, json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError) as error:
        raise ReleaseLayoutError("release layout cannot be read as JSON") from error
    required = {
        "schema_version",
        "application",
        "platform",
        "architecture",
        "bundle_mode",
        "entrypoint",
        "internal_directory",
        "api_version",
        "database_schema_revision",
        "openapi_sha256",
        "plugin_protocol_version",
        "required_top_level_files",
        "forbidden_suffixes",
        "forbidden_names",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise ReleaseLayoutError("release layout fields are incomplete or unknown")

    identity_values = {
        key: _required_text(raw, key)
        for key in (
            "schema_version",
            "application",
            "platform",
            "architecture",
            "bundle_mode",
            "entrypoint",
            "internal_directory",
        )
    }
    if identity_values != {
        "schema_version": "paper-reader-release-layout-v1",
        "application": "paper-reader",
        "platform": "windows",
        "architecture": "x86_64",
        "bundle_mode": "onedir",
        "entrypoint": "paper-reader.exe",
        "internal_directory": "_internal",
    }:
        raise ReleaseLayoutError("release layout identity is unsupported")

    contract_values = {
        key: _required_text(raw, key)
        for key in (
            "api_version",
            "database_schema_revision",
            "openapi_sha256",
            "plugin_protocol_version",
        )
    }
    if (
        contract_values["api_version"] != "v1"
        or _DATABASE_REVISION.fullmatch(contract_values["database_schema_revision"]) is None
        or _SHA256.fullmatch(contract_values["openapi_sha256"]) is None
        or _PROTOCOL_VERSION.fullmatch(contract_values["plugin_protocol_version"]) is None
    ):
        raise ReleaseLayoutError("release layout compatibility contract is invalid")

    required_files = _safe_name_list(raw, "required_top_level_files")
    forbidden_names = _safe_name_list(raw, "forbidden_names")
    forbidden_suffixes = _suffix_list(raw, "forbidden_suffixes")
    if identity_values["entrypoint"] not in required_files:
        raise ReleaseLayoutError("release layout entrypoint must be required")
    if identity_values["internal_directory"] in forbidden_names:
        raise ReleaseLayoutError("release layout internal directory is forbidden")

    return ReleaseLayout(
        **identity_values,
        **contract_values,
        required_top_level_files=required_files,
        forbidden_suffixes=forbidden_suffixes,
        forbidden_names=forbidden_names,
    )


def _required_text(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item or len(item) > 128:
        raise ReleaseLayoutError(f"release layout {key} is invalid")
    return item


def _safe_name_list(value: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = value.get(key)
    if not isinstance(raw, list) or not raw or len(raw) > 128:
        raise ReleaseLayoutError(f"release layout {key} is invalid")
    items: list[str] = []
    folded: set[str] = set()
    for item in raw:
        valid_name = isinstance(item, str) and (
            _SAFE_TOP_LEVEL_NAME.fullmatch(item) is not None
            or (
                key == "forbidden_names"
                and item.startswith(".")
                and _SAFE_TOP_LEVEL_NAME.fullmatch(f"x{item}") is not None
            )
        )
        if not valid_name:
            raise ReleaseLayoutError(f"release layout {key} contains an unsafe name")
        normalized = item.casefold()
        if normalized in folded:
            raise ReleaseLayoutError(f"release layout {key} values must be unique")
        folded.add(normalized)
        items.append(item)
    return tuple(items)


def _suffix_list(value: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = value.get(key)
    if not isinstance(raw, list) or not raw or len(raw) > 64:
        raise ReleaseLayoutError(f"release layout {key} is invalid")
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if (
            not isinstance(item, str)
            or not item.startswith(".")
            or len(item) > 32
            or _SAFE_TOP_LEVEL_NAME.fullmatch(f"x{item}") is None
        ):
            raise ReleaseLayoutError(f"release layout {key} contains an unsafe suffix")
        normalized = item.casefold()
        if normalized in seen:
            raise ReleaseLayoutError(f"release layout {key} values must be unique")
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)
