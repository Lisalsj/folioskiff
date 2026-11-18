"""Verified, versioned optional runtime assets."""

from paper_reader.assets.manifest import (
    ModelFile,
    ModelLicense,
    ModelManifest,
    ModelManifestError,
    load_model_manifest,
    load_packaged_model_manifests,
)

__all__ = [
    "ModelFile",
    "ModelLicense",
    "ModelManifest",
    "ModelManifestError",
    "load_model_manifest",
    "load_packaged_model_manifests",
]
