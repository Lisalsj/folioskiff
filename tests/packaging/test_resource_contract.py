from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest


def _write_runtime_tree(root: Path) -> None:
    (root / "static" / "assets").mkdir(parents=True)
    (root / "static" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (root / "migrations" / "versions").mkdir(parents=True)
    (root / "migrations" / "env.py").write_text("# migration fixture", encoding="utf-8")
    (root / "migrations" / "script.py.mako").write_text("fixture", encoding="utf-8")
    (root / "data").mkdir(parents=True)
    (root / "data" / "rapidocr-manifest.json").write_text("{}", encoding="utf-8")
    (root / "data" / "e5-manifest.json").write_text("{}", encoding="utf-8")
    (root / "data" / "app-license.txt").write_text("Apache-2.0", encoding="utf-8")
    (root / "data" / "third-party-notices.md").write_text("# Notices", encoding="utf-8")
    (root / "publishing" / "assets").mkdir(parents=True)
    (root / "publishing" / "assets" / "pages-workflow.yml").write_text(
        "name: Pages",
        encoding="utf-8",
    )


def test_packaged_runtime_resources_are_complete_and_cwd_independent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from paper_reader.packaging.resources import discover_runtime_resources

    monkeypatch.chdir(tmp_path)
    runtime = discover_runtime_resources()

    assert runtime.package_root.name == "paper_reader"
    assert runtime.static_root.joinpath("index.html").is_file()
    assert runtime.migrations_root.joinpath("env.py").is_file()
    assert runtime.migrations_root.joinpath("script.py.mako").is_file()
    assert runtime.ocr_manifest.name == "rapidocr-manifest.json"
    assert runtime.e5_manifest.name == "e5-manifest.json"
    assert runtime.pages_workflow.name == "pages-workflow.yml"
    repository_license = Path(__file__).parents[2] / "LICENSE"
    assert runtime.app_license.read_bytes() == repository_license.read_bytes()
    assert runtime.third_party_notices.read_text(encoding="utf-8").startswith(
        "# FolioSkiff third-party notices"
    )


def test_simulated_frozen_package_root_uses_only_declared_read_only_resources(
    tmp_path: Path,
) -> None:
    from paper_reader.packaging.resources import discover_runtime_resources

    target_package_root_length = 200
    unicode_prefix = "只读程序-"
    unpadded_root = tmp_path / unicode_prefix / "_internal" / "paper_reader"
    padding = target_package_root_length - len(str(unpadded_root))
    assert padding >= 0, "pytest temporary root is too deep for the Windows path fixture"
    long_unicode_segment = unicode_prefix + ("a" * padding)
    package_root = tmp_path / long_unicode_segment / "_internal" / "paper_reader"
    deepest_resource = package_root / "publishing" / "assets" / "pages-workflow.yml"
    assert len(str(package_root)) == target_package_root_length
    assert len(str(deepest_resource)) < 240
    _write_runtime_tree(package_root)
    packaged_files = tuple(path for path in package_root.rglob("*") if path.is_file())
    for path in packaged_files:
        path.chmod(stat.S_IREAD)

    try:
        runtime = discover_runtime_resources(package_root=package_root)

        assert len(str(runtime.package_root)) == target_package_root_length
        assert runtime.package_root == package_root.resolve()
        assert runtime.static_root == package_root.joinpath("static").resolve()
        assert runtime.e5_manifest.read_text(encoding="utf-8") == "{}"
        assert runtime.app_license.read_text(encoding="utf-8") == "Apache-2.0"
    finally:
        for path in packaged_files:
            path.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_runtime_resource_contract_rejects_a_missing_package_root(tmp_path: Path) -> None:
    from paper_reader.packaging.resources import FrozenResourceError, discover_runtime_resources

    with pytest.raises(FrozenResourceError, match="packaged_resource_missing:paper_reader"):
        discover_runtime_resources(package_root=tmp_path / "missing")


@pytest.mark.parametrize(
    "missing",
    [
        "static/index.html",
        "migrations/env.py",
        "migrations/script.py.mako",
        "data/rapidocr-manifest.json",
        "data/e5-manifest.json",
        "data/app-license.txt",
        "data/third-party-notices.md",
        "publishing/assets/pages-workflow.yml",
    ],
)
def test_runtime_resource_contract_fails_closed_when_one_file_is_missing(
    tmp_path: Path,
    missing: str,
) -> None:
    from paper_reader.packaging.resources import FrozenResourceError, discover_runtime_resources

    package_root = tmp_path / "paper_reader"
    _write_runtime_tree(package_root)
    package_root.joinpath(*missing.split("/")).unlink()

    with pytest.raises(FrozenResourceError, match="packaged_resource_missing"):
        discover_runtime_resources(package_root=package_root)


def test_e5_manifest_is_packaged_closed_and_download_ready(tmp_path: Path) -> None:
    from paper_reader.recommendation.semantic import (
        DEFAULT_E5_MANIFEST,
        EmbeddingManifestError,
        load_embedding_manifest,
    )

    manifest = load_embedding_manifest()
    assert manifest == DEFAULT_E5_MANIFEST
    assert manifest.model_id == "intfloat/multilingual-e5-small"
    assert manifest.revision == "0e60b8d9d2166d80387f86e3b48ec9ced55f4d15"
    assert manifest.download_size_bytes == 492_421_556
    assert manifest.license_id == "MIT"
    assert manifest.license_url == "https://opensource.org/license/mit"
    assert manifest.copyright_notice
    assert all(asset.url.startswith("https://huggingface.co/") for asset in manifest.assets)
    assert all(f"/{manifest.revision}/" in asset.url for asset in manifest.assets)

    packaged = json.loads(
        Path(__file__)
        .parents[2]
        .joinpath("src/paper_reader/data/e5-manifest.json")
        .read_text(encoding="utf-8")
    )
    packaged["unknown"] = True
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text(json.dumps(packaged), encoding="utf-8")
    with pytest.raises(EmbeddingManifestError, match="incomplete or unknown"):
        load_embedding_manifest(unsafe)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("revision", "revision must be a Git hash"),
        ("source_host", "source URL is not revision pinned"),
        ("asset_path", "asset path is unsafe"),
        ("asset_host", "asset URL is not revision pinned"),
        ("duplicate", "paths must be unique"),
        ("size", "asset size is invalid"),
        ("sha256", "asset SHA-256 is invalid"),
        ("license", "license is incomplete or unknown"),
    ],
)
def test_e5_manifest_rejects_unpinned_or_ambiguous_assets(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    from paper_reader.recommendation.semantic import EmbeddingManifestError, load_embedding_manifest

    source = Path(__file__).parents[2] / "src/paper_reader/data/e5-manifest.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    if case == "revision":
        payload["revision"] = "main"
    elif case == "source_host":
        payload["source_url"] = payload["source_url"].replace(
            "huggingface.co",
            "models.example.test",
        )
    elif case == "asset_path":
        payload["assets"][0]["relative_path"] = "../config.json"
    elif case == "asset_host":
        payload["assets"][0]["url"] = payload["assets"][0]["url"].replace(
            "huggingface.co",
            "models.example.test",
        )
    elif case == "duplicate":
        payload["assets"][1]["relative_path"] = payload["assets"][0]["relative_path"]
    elif case == "size":
        payload["assets"][0]["size_bytes"] = 0
    elif case == "sha256":
        payload["assets"][0]["sha256"] = "0" * 63
    elif case == "license":
        payload["license"]["unknown"] = True
    else:  # pragma: no cover - parametrization invariant
        raise AssertionError(case)

    manifest = tmp_path / f"{case}.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EmbeddingManifestError, match=message):
        load_embedding_manifest(manifest)
