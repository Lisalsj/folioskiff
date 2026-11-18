"""Export the FastAPI contract consumed by the Vue client."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from paper_reader.app import create_app
from paper_reader.settings import AppPaths

API_CONTRACT_VERSION = "v1"
OPENAPI_CONTRACT_SHA256 = "5c69bd4bfe08e2f3403cf15875c0b71d7df4ec6d4ea4ea7b8dffec7c33c1df8a"


class _DuplicateOpenApiKey(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateOpenApiKey("OpenAPI contract contains a duplicate JSON key")
        value[key] = item
    return value


def load_openapi_document(path: Path) -> dict[str, Any]:
    """Load one closed OpenAPI JSON object without accepting duplicate facts."""

    try:
        value = cast(
            Any,
            json.loads(
                Path(path).read_text(encoding="utf-8"),
                object_pairs_hook=_unique_json_object,
            ),
        )
    except _DuplicateOpenApiKey:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError("OpenAPI contract must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError("OpenAPI contract root must be an object")
    return cast(dict[str, Any], value)


def openapi_contract_sha256(document: dict[str, Any]) -> str:
    """Hash API semantics while versioning the application identity separately."""

    normalized = deepcopy(document)
    info = normalized.get("info")
    if not isinstance(info, dict) or not isinstance(info.get("version"), str):
        raise ValueError("OpenAPI contract must declare info.version")
    info.pop("version")
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_openapi_contract(document: dict[str, Any], *, application_version: str) -> None:
    """Fail closed unless a document is the frozen API v1 contract for this build."""

    info = document.get("info")
    paths = document.get("paths")
    if (
        not isinstance(info, dict)
        or info.get("version") != application_version
        or not isinstance(paths, dict)
        or not paths
        or any(not str(path).startswith(f"/api/{API_CONTRACT_VERSION}/") for path in paths)
        or openapi_contract_sha256(document) != OPENAPI_CONTRACT_SHA256
    ):
        raise ValueError("OpenAPI document does not match the frozen API contract")


def write_openapi_schema(output_path: Path) -> None:
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix=".openapi-", dir=output.parent) as temporary_directory:
        scratch = Path(temporary_directory)
        paths = AppPaths.from_overrides(
            state_root=scratch / "state",
            cache_root=scratch / "cache",
            library_root=scratch / "library",
        )
        app = create_app(paths=paths, bootstrap_token="schema-export-only")
        document: dict[str, Any] = app.openapi()
        app.state.engine.dispose()

    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m paper_reader.openapi")
    parser.add_argument("--output", type=Path, default=Path("openapi/openapi.json"))
    arguments = parser.parse_args(argv)
    write_openapi_schema(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
