from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_release_layout_is_a_closed_model_free_onedir_contract() -> None:
    from paper_reader.packaging.resources import load_release_layout

    root = Path(__file__).parents[2]
    layout = load_release_layout(root / "packaging" / "release-layout.json")

    assert layout.schema_version == "paper-reader-release-layout-v1"
    assert layout.application == "paper-reader"
    assert layout.platform == "windows"
    assert layout.architecture == "x86_64"
    assert layout.bundle_mode == "onedir"
    assert layout.entrypoint == "paper-reader.exe"
    assert layout.internal_directory == "_internal"
    assert layout.api_version == "v1"
    assert layout.database_schema_revision == "0038_remove_legacy_conversations"
    assert layout.openapi_sha256 == (
        "5c69bd4bfe08e2f3403cf15875c0b71d7df4ec6d4ea4ea7b8dffec7c33c1df8a"
    )
    assert layout.plugin_protocol_version == "1.0"
    assert layout.required_top_level_files == (
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "paper-reader.exe",
        "release-manifest.json",
    )
    assert ".onnx" in layout.forbidden_suffixes
    assert ".safetensors" in layout.forbidden_suffixes
    assert ".git" in layout.forbidden_names
    assert "paper-reader.sqlite3" in layout.forbidden_names


def test_release_layout_rejects_unknown_fields_duplicates_and_unsafe_names(
    tmp_path: Path,
) -> None:
    from paper_reader.packaging.resources import ReleaseLayoutError, load_release_layout

    source = Path(__file__).parents[2] / "packaging" / "release-layout.json"
    payload = json.loads(source.read_text(encoding="utf-8"))

    payload["unexpected"] = True
    unknown = tmp_path / "unknown.json"
    unknown.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReleaseLayoutError, match="incomplete or unknown"):
        load_release_layout(unknown)

    payload.pop("unexpected")
    payload["required_top_level_files"] = ["LICENSE", "LICENSE"]
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReleaseLayoutError, match="unique"):
        load_release_layout(duplicate)

    payload["required_top_level_files"] = ["../LICENSE"]
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReleaseLayoutError, match="unsafe"):
        load_release_layout(unsafe)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("identity", "identity is unsupported"),
        ("missing_entrypoint", "entrypoint must be required"),
        ("forbidden_internal", "internal directory is forbidden"),
        ("invalid_scalar", "application is invalid"),
        ("invalid_names", "forbidden_names is invalid"),
        ("invalid_suffix", "unsafe suffix"),
        ("duplicate_suffix", "values must be unique"),
        ("invalid_contract", "compatibility contract is invalid"),
    ],
)
def test_release_layout_fails_closed_for_ambiguous_contracts(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    from paper_reader.packaging.resources import ReleaseLayoutError, load_release_layout

    source = Path(__file__).parents[2] / "packaging" / "release-layout.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    if case == "identity":
        payload["architecture"] = "any"
    elif case == "missing_entrypoint":
        payload["required_top_level_files"].remove("paper-reader.exe")
    elif case == "forbidden_internal":
        payload["forbidden_names"].append("_internal")
    elif case == "invalid_scalar":
        payload["application"] = ""
    elif case == "invalid_names":
        payload["forbidden_names"] = []
    elif case == "invalid_suffix":
        payload["forbidden_suffixes"] = ["onnx"]
    elif case == "duplicate_suffix":
        payload["forbidden_suffixes"] = [".onnx", ".ONNX"]
    elif case == "invalid_contract":
        payload["openapi_sha256"] = "not-a-sha256"
    else:  # pragma: no cover - parametrization invariant
        raise AssertionError(case)

    candidate = tmp_path / f"{case}.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReleaseLayoutError, match=message):
        load_release_layout(candidate)


def test_release_layout_rejects_missing_or_invalid_json(tmp_path: Path) -> None:
    from paper_reader.packaging.resources import ReleaseLayoutError, load_release_layout

    with pytest.raises(ReleaseLayoutError, match="cannot be read as JSON"):
        load_release_layout(tmp_path / "missing.json")

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(ReleaseLayoutError, match="cannot be read as JSON"):
        load_release_layout(invalid)
