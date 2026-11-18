"""One strict contract for optional OCR and embedding model bundles."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from paper_reader.packaging.resources import discover_runtime_resources

ModelCapability = Literal["semantic", "ocr"]

_BUNDLE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{0,119}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_OCR_ROLES = ("det", "cls", "rec")
_MAX_FILE_BYTES = 2**31
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*')
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


class ModelManifestError(ValueError):
    """A packaged or supplied model manifest is open, unsafe, or inconsistent."""


@dataclass(frozen=True, slots=True)
class ModelLicense:
    name: str
    url: str
    copyright_notice: str


@dataclass(frozen=True, slots=True)
class ModelFile:
    relative_path: str
    url: str
    size_bytes: int
    sha256: str
    role: str | None = None


@dataclass(frozen=True, slots=True)
class ModelManifest:
    capability: ModelCapability
    bundle_id: str
    display_name: str
    purpose: str
    version: str
    revision: str
    source_url: str
    license: ModelLicense
    files: tuple[ModelFile, ...]
    runtime: dict[str, str | int | bool]

    @property
    def total_size_bytes(self) -> int:
        return sum(item.size_bytes for item in self.files)

    def with_revision(self, revision: str) -> ModelManifest:
        normalized = revision.casefold()
        if _REVISION_PATTERN.fullmatch(normalized) is None:
            raise ModelManifestError("model manifest revision is invalid")
        return replace(self, revision=normalized)

    def version_root(self, models_root: Path) -> Path:
        root = Path(models_root)
        if self.capability == "semantic":
            model_id = self.runtime.get("model_id")
            if not isinstance(model_id, str) or _MODEL_ID_PATTERN.fullmatch(model_id) is None:
                raise ModelManifestError("semantic model_id is invalid")
            return root / model_id.replace("/", "--") / self.revision
        return root / "rapidocr" / self.bundle_id / self.revision

    def file_path(self, models_root: Path, asset: ModelFile) -> Path:
        relative = _relative_path(asset.relative_path)
        return self.version_root(models_root).joinpath(*PurePosixPath(relative).parts)


def load_packaged_model_manifests() -> tuple[ModelManifest, ModelManifest]:
    resources = discover_runtime_resources()
    return (
        load_model_manifest(resources.e5_manifest, capability="semantic"),
        load_model_manifest(resources.ocr_manifest, capability="ocr"),
    )


def load_model_manifest(path: Path, *, capability: ModelCapability) -> ModelManifest:
    try:
        raw = cast(Any, json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError) as error:
        raise ModelManifestError("model manifest cannot be read as JSON") from error
    if not isinstance(raw, dict):
        raise ModelManifestError("model manifest root must be an object")
    if capability == "semantic":
        return _semantic_manifest(raw)
    if capability == "ocr":
        return _ocr_manifest(raw)
    raise ModelManifestError("model manifest capability is unknown")


def model_manifest_from_payload(raw: dict[str, Any]) -> ModelManifest:
    """Revalidate the normalized manifest stored with an older installed revision."""
    _closed(
        raw,
        {
            "capability",
            "bundle_id",
            "display_name",
            "purpose",
            "version",
            "revision",
            "source_url",
            "license",
            "files",
            "runtime",
        },
        "stored model manifest",
    )
    capability = raw.get("capability")
    if capability not in {"semantic", "ocr"}:
        raise ModelManifestError("stored model manifest capability is invalid")
    license_value = raw.get("license")
    if not isinstance(license_value, dict):
        raise ModelManifestError("stored model manifest license is invalid")
    _closed(
        license_value,
        {"name", "url", "copyright_notice"},
        "stored model manifest license",
    )
    values = raw.get("files")
    if not isinstance(values, list) or not 1 <= len(values) <= 64:
        raise ModelManifestError("stored model manifest files are invalid")
    files: list[ModelFile] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise ModelManifestError("stored model manifest file is invalid")
        _closed(
            value,
            {"relative_path", "url", "size_bytes", "sha256", "role"},
            "stored model manifest file",
        )
        relative = _relative_path(_text(value, "relative_path", maximum=240))
        if relative.casefold() in seen:
            raise ModelManifestError("stored model manifest paths must be unique")
        seen.add(relative.casefold())
        role = value.get("role")
        if role is not None and (not isinstance(role, str) or role not in _OCR_ROLES):
            raise ModelManifestError("stored model manifest role is invalid")
        files.append(
            ModelFile(
                relative_path=relative,
                url=_https(_text(value, "url", maximum=2048), "asset URL"),
                size_bytes=_size(value, "size_bytes"),
                sha256=_digest(value),
                role=role,
            )
        )
    runtime = raw.get("runtime")
    if not isinstance(runtime, dict) or not all(
        isinstance(key, str) and isinstance(value, (str, int, bool))
        for key, value in runtime.items()
    ):
        raise ModelManifestError("stored model manifest runtime is invalid")
    if capability == "semantic":
        if set(runtime) != {"model_id", "dimension", "pooling", "normalized"}:
            raise ModelManifestError("stored semantic runtime is incomplete")
        model_id = runtime.get("model_id")
        if not isinstance(model_id, str) or _MODEL_ID_PATTERN.fullmatch(model_id) is None:
            raise ModelManifestError("stored semantic model_id is invalid")
    elif set(runtime) != {"roles"} or runtime.get("roles") != "det,cls,rec":
        raise ModelManifestError("stored OCR runtime is incomplete")
    return ModelManifest(
        capability=cast(ModelCapability, capability),
        bundle_id=_bundle_id(raw),
        display_name=_text(raw, "display_name", maximum=240),
        purpose=_text(raw, "purpose", maximum=1000),
        version=_text(raw, "version", maximum=120),
        revision=_revision(raw, "revision"),
        source_url=_https(_text(raw, "source_url", maximum=2048), "source URL"),
        license=ModelLicense(
            name=_text(license_value, "name", maximum=120),
            url=_https(_text(license_value, "url", maximum=2048), "license URL"),
            copyright_notice=_text(license_value, "copyright_notice", maximum=1000),
        ),
        files=tuple(files),
        runtime=cast(dict[str, str | int | bool], runtime),
    )


def model_manifest_payload(manifest: ModelManifest) -> dict[str, Any]:
    return {
        "capability": manifest.capability,
        "bundle_id": manifest.bundle_id,
        "display_name": manifest.display_name,
        "purpose": manifest.purpose,
        "version": manifest.version,
        "revision": manifest.revision,
        "source_url": manifest.source_url,
        "license": {
            "name": manifest.license.name,
            "url": manifest.license.url,
            "copyright_notice": manifest.license.copyright_notice,
        },
        "files": [
            {
                "relative_path": item.relative_path,
                "url": item.url,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "role": item.role,
            }
            for item in manifest.files
        ],
        "runtime": manifest.runtime,
    }


def model_manifest_fingerprint(manifest: ModelManifest) -> str:
    serialized = json.dumps(
        model_manifest_payload(manifest),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _semantic_manifest(raw: dict[str, Any]) -> ModelManifest:
    required = {
        "bundle_id",
        "display_name",
        "purpose",
        "version",
        "model_id",
        "revision",
        "dimension",
        "pooling",
        "normalized",
        "license",
        "source_url",
        "assets",
    }
    _closed(raw, required, "semantic manifest")
    bundle_id = _bundle_id(raw)
    model_id = _text(raw, "model_id", maximum=160)
    if _MODEL_ID_PATTERN.fullmatch(model_id) is None:
        raise ModelManifestError("semantic manifest model_id is unsafe")
    revision = _revision(raw, "revision")
    dimension = raw.get("dimension")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or not 1 <= dimension <= 4096:
        raise ModelManifestError("semantic manifest dimension is invalid")
    pooling = _text(raw, "pooling", maximum=20)
    if pooling not in {"mean", "cls"}:
        raise ModelManifestError("semantic manifest pooling is invalid")
    normalized = raw.get("normalized")
    if not isinstance(normalized, bool):
        raise ModelManifestError("semantic manifest normalized flag is invalid")
    source_url = _https(_text(raw, "source_url", maximum=2048), "source URL")
    source_parts = urlsplit(source_url)
    if (
        source_parts.hostname != "huggingface.co"
        or source_parts.path != f"/{model_id}/tree/{revision}"
        or source_parts.query
    ):
        raise ModelManifestError("semantic manifest source URL is not revision pinned")
    license_value = _license(raw, id_key="id")
    values = raw.get("assets")
    if not isinstance(values, list) or not 1 <= len(values) <= 64:
        raise ModelManifestError("semantic manifest assets are invalid")
    files: list[ModelFile] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise ModelManifestError("semantic manifest asset is invalid")
        _closed(
            value,
            {"relative_path", "url", "size_bytes", "sha256"},
            "semantic manifest asset",
        )
        relative = _relative_path(_text(value, "relative_path", maximum=240))
        if relative.casefold() in seen:
            raise ModelManifestError("semantic manifest asset paths must be unique")
        seen.add(relative.casefold())
        url = _https(_text(value, "url", maximum=2048), "asset URL")
        parts = urlsplit(url)
        if (
            parts.hostname != source_parts.hostname
            or parts.path != f"/{model_id}/resolve/{revision}/{relative}"
            or parts.query
        ):
            raise ModelManifestError("semantic manifest asset URL is not revision pinned")
        files.append(
            ModelFile(
                relative_path=relative,
                url=url,
                size_bytes=_size(value, "size_bytes"),
                sha256=_digest(value),
            )
        )
    return ModelManifest(
        capability="semantic",
        bundle_id=bundle_id,
        display_name=_text(raw, "display_name", maximum=240),
        purpose=_text(raw, "purpose", maximum=1000),
        version=_text(raw, "version", maximum=120),
        revision=revision,
        source_url=source_url,
        license=license_value,
        files=tuple(files),
        runtime={
            "model_id": model_id,
            "dimension": dimension,
            "pooling": pooling,
            "normalized": normalized,
        },
    )


def _ocr_manifest(raw: dict[str, Any]) -> ModelManifest:
    required = {
        "bundle_id",
        "display_name",
        "version",
        "source_revision",
        "source_url",
        "license",
        "files",
    }
    _closed(raw, required, "OCR manifest")
    revision = _revision(raw, "source_revision")
    source_url = _https(_text(raw, "source_url", maximum=2048), "source URL")
    values = raw.get("files")
    if not isinstance(values, list) or len(values) != 3:
        raise ModelManifestError("OCR manifest must contain det, cls, and rec files")
    files: list[ModelFile] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise ModelManifestError("OCR manifest file is invalid")
        _closed(
            value,
            {"role", "filename", "url", "size", "sha256"},
            "OCR manifest file",
        )
        role = _text(value, "role", maximum=3)
        if role not in _OCR_ROLES:
            raise ModelManifestError("OCR manifest model role is invalid")
        relative = _relative_path(_text(value, "filename", maximum=255))
        if "/" in relative or relative.casefold() in seen:
            raise ModelManifestError("OCR manifest filenames must be unique safe filenames")
        seen.add(relative.casefold())
        url = _https(_text(value, "url", maximum=2048), "asset URL")
        if f"/resolve/{revision}/" not in urlsplit(url).path:
            raise ModelManifestError("OCR manifest asset URL is not revision pinned")
        files.append(
            ModelFile(
                relative_path=relative,
                url=url,
                size_bytes=_size(value, "size"),
                sha256=_digest(value),
                role=role,
            )
        )
    if tuple(item.role for item in files) != _OCR_ROLES:
        raise ModelManifestError("OCR manifest model roles must be det, cls, and rec")
    return ModelManifest(
        capability="ocr",
        bundle_id=_bundle_id(raw),
        display_name=_text(raw, "display_name", maximum=240),
        purpose="为扫描论文页面提供按需中英文文本识别，并保留页码、坐标和置信度。",
        version=_text(raw, "version", maximum=120),
        revision=revision,
        source_url=source_url,
        license=_license(raw, id_key="name"),
        files=tuple(files),
        runtime={"roles": "det,cls,rec"},
    )


def _closed(value: dict[str, Any], required: set[str], label: str) -> None:
    unknown = set(value) - required
    missing = required - set(value)
    if unknown:
        raise ModelManifestError(f"{label} contains unknown fields")
    if missing:
        raise ModelManifestError(f"{label} fields are incomplete")


def _bundle_id(raw: dict[str, Any]) -> str:
    value = _text(raw, "bundle_id", maximum=120)
    if _BUNDLE_PATTERN.fullmatch(value) is None or not _safe_windows_component(value):
        raise ModelManifestError("model manifest bundle_id is unsafe")
    return value


def _revision(raw: dict[str, Any], key: str) -> str:
    value = _text(raw, key, maximum=40).casefold()
    if _REVISION_PATTERN.fullmatch(value) is None:
        raise ModelManifestError("model manifest revision is invalid")
    return value


def _license(raw: dict[str, Any], *, id_key: str) -> ModelLicense:
    value = raw.get("license")
    required = {id_key, "url", "copyright_notice"}
    if not isinstance(value, dict):
        raise ModelManifestError("model manifest license is invalid")
    _closed(value, required, "model manifest license")
    return ModelLicense(
        name=_text(value, id_key, maximum=120),
        url=_https(_text(value, "url", maximum=2048), "license URL"),
        copyright_notice=_text(value, "copyright_notice", maximum=1000),
    )


def _text(value: dict[str, Any], key: str, *, maximum: int) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip() or len(item) > maximum:
        raise ModelManifestError(f"model manifest {key} is invalid")
    return item.strip()


def _https(value: str, field: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ModelManifestError(f"model manifest {field} must be HTTPS") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise ModelManifestError(f"model manifest {field} must be HTTPS")
    return value


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or "\\" in value
        or not path.parts
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(not _safe_windows_component(part) for part in path.parts)
    ):
        raise ModelManifestError("model manifest asset path is unsafe")
    return value


def _safe_windows_component(value: str) -> bool:
    if value.endswith((" ", ".")):
        return False
    if any(ord(character) < 32 or character in _WINDOWS_FORBIDDEN for character in value):
        return False
    return value.split(".", 1)[0].upper() not in _WINDOWS_RESERVED


def _size(value: dict[str, Any], key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= _MAX_FILE_BYTES:
        raise ModelManifestError("model manifest asset size is invalid")
    return item


def _digest(value: dict[str, Any]) -> str:
    digest = _text(value, "sha256", maximum=64).casefold()
    if _SHA256_PATTERN.fullmatch(digest) is None:
        raise ModelManifestError("model manifest asset SHA-256 is invalid")
    return digest
