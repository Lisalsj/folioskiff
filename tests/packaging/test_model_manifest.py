from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_packaged_manifests_share_one_closed_public_contract(tmp_path: Path) -> None:
    from paper_reader.assets.manifest import load_packaged_model_manifests

    manifests = load_packaged_model_manifests()

    assert [item.capability for item in manifests] == ["semantic", "ocr"]
    assert [item.bundle_id for item in manifests] == [
        "multilingual-e5-small",
        "rapidocr-ppocrv5-ch-en-mobile",
    ]
    assert all(len(item.revision) == 40 for item in manifests)
    assert all(
        item.total_size_bytes == sum(file.size_bytes for file in item.files) for item in manifests
    )
    assert all(item.license.name and item.license.url.startswith("https://") for item in manifests)
    assert all(item.purpose for item in manifests)

    semantic, ocr = manifests
    assert semantic.version_root(tmp_path) == (
        tmp_path / "intfloat--multilingual-e5-small" / semantic.revision
    )
    assert ocr.version_root(tmp_path) == (tmp_path / "rapidocr" / ocr.bundle_id / ocr.revision)
    assert semantic.version_root(tmp_path) != semantic.with_revision("f" * 40).version_root(
        tmp_path
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.__setitem__("unknown", True), "unknown"),
        (
            lambda value: value["assets"][0].__setitem__("relative_path", "../escape"),
            "path",
        ),
        (
            lambda value: value["assets"][0].__setitem__("url", "http://huggingface.co/model"),
            "HTTPS",
        ),
        (lambda value: value.__setitem__("revision", "main"), "revision"),
        (
            lambda value: value["assets"][0].__setitem__("size_bytes", 2**31 + 1),
            "size",
        ),
    ],
)
def test_embedding_manifest_rejects_open_or_unsafe_input(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    from paper_reader.assets.manifest import ModelManifestError, load_model_manifest
    from paper_reader.packaging.resources import discover_runtime_resources

    raw = json.loads(discover_runtime_resources().e5_manifest.read_text(encoding="utf-8"))
    mutation(raw)  # type: ignore[operator]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ModelManifestError, match=message):
        load_model_manifest(path, capability="semantic")


def test_ocr_and_embedding_adapters_use_the_generic_manifest_parser() -> None:
    from paper_reader.assets.manifest import load_packaged_model_manifests
    from paper_reader.reader import load_ocr_manifest
    from paper_reader.recommendation.semantic import load_embedding_manifest

    semantic, ocr = load_packaged_model_manifests()
    embedding = load_embedding_manifest()
    legacy_ocr = load_ocr_manifest()

    assert embedding.bundle_id == semantic.bundle_id
    assert embedding.revision == semantic.revision
    assert embedding.download_size_bytes == semantic.total_size_bytes
    assert legacy_ocr.bundle_id == ocr.bundle_id
    assert legacy_ocr.source_revision == ocr.revision
    assert legacy_ocr.total_size == ocr.total_size_bytes


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.__setitem__("unknown", True), "unknown"),
        (lambda value: value["files"][0].__setitem__("filename", "../escape"), "path"),
        (
            lambda value: value["files"][0].__setitem__("url", "http://example.test/model"),
            "HTTPS",
        ),
        (lambda value: value.__setitem__("source_revision", "main"), "revision"),
        (lambda value: value["files"][0].__setitem__("size", 2**31 + 1), "size"),
        (
            lambda value: value["files"][0].__setitem__("filename", "det.onnx:stream"),
            "path",
        ),
        (lambda value: value["files"][0].__setitem__("filename", "CON"), "path"),
        (lambda value: value["files"][0].__setitem__("filename", "det.onnx."), "path"),
    ],
)
def test_ocr_manifest_rejects_the_same_open_or_unsafe_input_classes(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    from paper_reader.assets.manifest import ModelManifestError, load_model_manifest
    from paper_reader.packaging.resources import discover_runtime_resources

    raw = json.loads(discover_runtime_resources().ocr_manifest.read_text(encoding="utf-8"))
    mutation(raw)  # type: ignore[operator]
    path = tmp_path / "ocr-manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ModelManifestError, match=message):
        load_model_manifest(path, capability="ocr")
