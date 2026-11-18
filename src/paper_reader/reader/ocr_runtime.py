"""OCR manifest acquisition and isolated native runtime adapters."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from functools import cache
from hashlib import sha256
from importlib import import_module, util
from pathlib import Path
from threading import Lock
from types import ModuleType
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from paper_reader.reader.native_process import NativeProcessError, NativeProcessRunner
from paper_reader.reader.ocr_contracts import (
    _BUNDLE_ID_PATTERN,
    _CONTENT_RANGE_PATTERN,
    _MODEL_ROLES,
    _REVISION_PATTERN,
    _SHA256_PATTERN,
    IsolatedOcrDocument,
    IsolatedOcrPage,
    OcrCapability,
    OcrError,
    OcrLine,
    OcrManifestError,
    OcrModelBundle,
    OcrModelDownloadError,
    OcrModelFile,
    OcrModelFileStatus,
    OcrModelIntegrity,
    OcrModelRole,
    OcrProgress,
    RecognitionProgress,
    RuntimeProbe,
    _invalid_output_error,
    _ocr_text_limit_error,
    _PageSnapshot,
    _recognition_cancelled_error,
)


def load_ocr_manifest(path: Path | None = None) -> OcrModelBundle:
    """Load and strictly validate the immutable packaged OCR model manifest."""

    if path is None:
        from paper_reader.assets.manifest import load_packaged_model_manifests

        common = load_packaged_model_manifests()[1]
        return OcrModelBundle(
            bundle_id=common.bundle_id,
            display_name=common.display_name,
            version=common.version,
            source_revision=common.revision,
            source_url=common.source_url,
            license_name=common.license.name,
            license_url=common.license.url,
            copyright_notice=common.license.copyright_notice,
            files=tuple(
                OcrModelFile(
                    role=cast(OcrModelRole, item.role),
                    filename=item.relative_path,
                    url=item.url,
                    size=item.size_bytes,
                    sha256=item.sha256,
                )
                for item in common.files
            ),
        )

    try:
        text = Path(path).read_text(encoding="utf-8")
        raw = cast(Any, json.loads(text))
    except (OSError, UnicodeError, ValueError) as error:
        raise OcrManifestError("OCR manifest cannot be read as JSON") from error
    if not isinstance(raw, dict):
        raise OcrManifestError("OCR manifest root must be an object")

    required = {
        "bundle_id",
        "display_name",
        "version",
        "source_revision",
        "source_url",
        "license",
        "files",
    }
    if set(raw) != required:
        raise OcrManifestError("OCR manifest fields are incomplete or unknown")
    bundle_id = _required_string(raw, "bundle_id", maximum=120)
    if _BUNDLE_ID_PATTERN.fullmatch(bundle_id) is None:
        raise OcrManifestError("OCR manifest bundle_id is unsafe")
    source_revision = _required_string(raw, "source_revision", maximum=40).casefold()
    if _REVISION_PATTERN.fullmatch(source_revision) is None:
        raise OcrManifestError("OCR manifest source revision must be a Git hash")
    source_url = _validated_https_url(
        _required_string(raw, "source_url", maximum=2_048),
        field="source URL",
    )

    license_value = raw.get("license")
    if not isinstance(license_value, dict) or set(license_value) != {
        "name",
        "url",
        "copyright_notice",
    }:
        raise OcrManifestError("OCR manifest license is incomplete or unknown")
    license_name = _required_string(license_value, "name", maximum=120)
    license_url = _validated_https_url(
        _required_string(license_value, "url", maximum=2_048),
        field="license URL",
    )
    copyright_notice = _required_string(
        license_value,
        "copyright_notice",
        maximum=1_000,
    )

    raw_files = raw.get("files")
    if not isinstance(raw_files, list) or len(raw_files) != len(_MODEL_ROLES):
        raise OcrManifestError("OCR manifest must contain det, cls, and rec files")
    models: list[OcrModelFile] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict) or set(raw_file) != {
            "role",
            "filename",
            "url",
            "size",
            "sha256",
        }:
            raise OcrManifestError("OCR manifest model fields are incomplete or unknown")
        role_value = _required_string(raw_file, "role", maximum=3)
        if role_value not in _MODEL_ROLES:
            raise OcrManifestError("OCR manifest model role is invalid")
        filename = _safe_filename(_required_string(raw_file, "filename", maximum=255))
        url = _validated_https_url(
            _required_string(raw_file, "url", maximum=2_048),
            field="model URL",
        )
        size_value = raw_file.get("size")
        if isinstance(size_value, bool) or not isinstance(size_value, int) or size_value <= 0:
            raise OcrManifestError("OCR manifest model size must be positive")
        digest = _required_string(raw_file, "sha256", maximum=64).casefold()
        if _SHA256_PATTERN.fullmatch(digest) is None:
            raise OcrManifestError("OCR manifest model SHA-256 is invalid")
        models.append(
            OcrModelFile(
                role=role_value,
                filename=filename,
                url=url,
                size=size_value,
                sha256=digest,
            )
        )
    if tuple(model.role for model in models) != _MODEL_ROLES:
        raise OcrManifestError("OCR manifest model roles must be det, cls, and rec")
    if len({model.filename.casefold() for model in models}) != len(models):
        raise OcrManifestError("OCR manifest model filenames must be unique")

    return OcrModelBundle(
        bundle_id=bundle_id,
        display_name=_required_string(raw, "display_name", maximum=200),
        version=_required_string(raw, "version", maximum=120),
        source_revision=source_revision,
        source_url=source_url,
        license_name=license_name,
        license_url=license_url,
        copyright_notice=copyright_notice,
        files=tuple(models),
    )


class OcrModelInstaller:
    """Inspect and explicitly install one hash-pinned model bundle."""

    def __init__(
        self,
        models_root: Path,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._root = Path(models_root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._client = client

    def model_paths(self, bundle: OcrModelBundle) -> dict[OcrModelRole, Path]:
        bundle_root = self._bundle_root(bundle)
        result: dict[OcrModelRole, Path] = {}
        for model in bundle.files:
            if model.role not in _MODEL_ROLES:
                raise OcrManifestError("OCR model role is invalid")
            filename = _safe_filename(model.filename)
            destination = (bundle_root / filename).resolve()
            try:
                destination.relative_to(bundle_root)
            except ValueError as error:
                raise OcrManifestError("OCR model filename escapes the model root") from error
            result[model.role] = destination
        if tuple(result) != _MODEL_ROLES:
            raise OcrManifestError("OCR bundle must contain det, cls, and rec files")
        return result

    def inspect(self, bundle: OcrModelBundle) -> tuple[OcrModelFileStatus, ...]:
        paths = self.model_paths(bundle)
        statuses: list[OcrModelFileStatus] = []
        for model in bundle.files:
            path = paths[model.role]
            if not path.is_file():
                integrity: OcrModelIntegrity = "missing"
            elif path.stat().st_size != model.size or _file_sha256(path) != model.sha256:
                integrity = "hash_mismatch"
            else:
                integrity = "verified"
            statuses.append(
                OcrModelFileStatus(
                    role=model.role,
                    filename=model.filename,
                    size=model.size,
                    sha256=model.sha256,
                    installed=integrity == "verified",
                    integrity=integrity,
                )
            )
        return tuple(statuses)

    async def install(
        self,
        bundle: OcrModelBundle,
        *,
        cancelled: Callable[[], bool],
        progress: OcrProgress = lambda _received, _total: None,
    ) -> None:
        if self._client is None:
            raise OcrModelDownloadError(
                "network",
                "The OCR model download client is unavailable",
                retryable=True,
            )
        paths = self.model_paths(bundle)
        completed = 0
        for model in bundle.files:
            if _safe_cancelled(cancelled):
                raise _cancelled_download_error()
            destination = paths[model.role]
            if _verified_file(destination, model):
                completed += model.size
                _safe_progress(progress, completed, bundle.total_size)
                continue
            if destination.exists():
                destination.unlink()
            await self._download_file(
                model,
                destination,
                completed_before=completed,
                total=bundle.total_size,
                cancelled=cancelled,
                progress=progress,
            )
            completed += model.size
        _safe_progress(progress, bundle.total_size, bundle.total_size)

    async def _download_file(
        self,
        model: OcrModelFile,
        destination: Path,
        *,
        completed_before: int,
        total: int,
        cancelled: Callable[[], bool],
        progress: OcrProgress,
    ) -> None:
        client = self._client
        if client is None:  # pragma: no cover - guarded by install
            raise RuntimeError("OCR model client is unavailable")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(f"{destination.suffix}.part")
        if partial.exists() and partial.stat().st_size > model.size:
            partial.unlink()
        if partial.exists() and partial.stat().st_size == model.size:
            if _file_sha256(partial) == model.sha256:
                os.replace(partial, destination)
                _safe_progress(progress, completed_before + model.size, total)
                return
            partial.unlink()

        offset = partial.stat().st_size if partial.exists() else 0
        headers = {
            "Accept": "application/octet-stream",
            "Accept-Encoding": "identity",
        }
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            async with client.stream(
                "GET",
                model.url,
                headers=headers,
                follow_redirects=False,
            ) as response:
                mode = "ab"
                if offset:
                    if response.status_code == 206:
                        _validate_content_range(response, offset=offset, size=model.size)
                    elif response.status_code == 200:
                        offset = 0
                        mode = "wb"
                    else:
                        raise _network_download_error()
                elif response.status_code == 200:
                    mode = "wb"
                else:
                    raise _network_download_error()
                if response.headers.get("Content-Encoding", "identity").casefold() not in {
                    "",
                    "identity",
                }:
                    raise _network_download_error()

                written = offset
                with partial.open(mode) as stream:
                    async for chunk in response.aiter_bytes():
                        if _safe_cancelled(cancelled):
                            raise _cancelled_download_error()
                        if written + len(chunk) > model.size:
                            stream.close()
                            partial.unlink(missing_ok=True)
                            raise OcrModelDownloadError(
                                "hash_mismatch",
                                "The OCR model download exceeded its signed size",
                                retryable=False,
                            )
                        stream.write(chunk)
                        written += len(chunk)
                        _safe_progress(progress, completed_before + written, total)
                    stream.flush()
                    os.fsync(stream.fileno())
        except OcrModelDownloadError:
            raise
        except httpx.HTTPError as error:
            raise _network_download_error() from error
        except OSError as error:
            raise OcrModelDownloadError(
                "storage",
                "The OCR model could not be written to the local model cache",
                retryable=True,
            ) from error

        if not partial.is_file() or partial.stat().st_size != model.size:
            raise OcrModelDownloadError(
                "network",
                "The OCR model download ended before its declared size",
                retryable=True,
            )
        if _file_sha256(partial) != model.sha256:
            partial.unlink(missing_ok=True)
            raise OcrModelDownloadError(
                "hash_mismatch",
                "The OCR model failed SHA-256 verification",
                retryable=False,
            )
        os.replace(partial, destination)

    def _bundle_root(self, bundle: OcrModelBundle) -> Path:
        if _BUNDLE_ID_PATTERN.fullmatch(bundle.bundle_id) is None:
            raise OcrManifestError("OCR bundle_id is unsafe")
        root = (self._root / bundle.bundle_id / bundle.source_revision).resolve()
        try:
            root.relative_to(self._root)
        except ValueError as error:
            raise OcrManifestError("OCR bundle escapes the model root") from error
        return root


def inspect_ocr_capability(
    *,
    bundle: OcrModelBundle,
    installer: OcrModelInstaller,
    runtime_probe: RuntimeProbe | None = None,
) -> OcrCapability:
    runtime_available = bool(probe_ocr_runtime() if runtime_probe is None else runtime_probe())
    files = installer.inspect(bundle)
    models_installed = all(model.installed for model in files)
    reasons: list[str] = []
    if not runtime_available:
        reasons.append("runtime_unavailable")
    if any(model.integrity == "hash_mismatch" for model in files):
        reasons.append("model_hash_mismatch")
    elif not models_installed:
        reasons.append("models_missing")
    return OcrCapability(
        available=runtime_available and models_installed,
        runtime_available=runtime_available,
        models_installed=models_installed,
        bundle_id=bundle.bundle_id,
        display_name=bundle.display_name,
        version=bundle.version,
        total_size=bundle.total_size,
        license_name=bundle.license_name,
        license_url=bundle.license_url,
        copyright_notice=bundle.copyright_notice,
        source_url=bundle.source_url,
        source_revision=bundle.source_revision,
        files=files,
        degradation_reasons=tuple(reasons),
    )


@cache
def probe_ocr_runtime(
    finder: Callable[[str], object | None] = util.find_spec,
    runner: Callable[..., Any] = subprocess.run,
    executable: str = sys.executable,
) -> bool:
    try:
        installed = finder("rapidocr") is not None and finder("onnxruntime") is not None
    except (ImportError, ValueError):
        return False
    if not installed:
        return False
    try:
        result = runner(
            [
                executable,
                "-c",
                (
                    "import importlib; "
                    "importlib.import_module('onnxruntime'); "
                    "importlib.import_module('rapidocr')"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(result.returncode == 0)


class RapidOcrBackend:
    """Lazy RapidOCR adapter configured exclusively with verified local models."""

    def __init__(
        self,
        *,
        model_paths: Mapping[OcrModelRole, Path],
        module_loader: Callable[[], ModuleType | Any] = lambda: import_module("rapidocr"),
    ) -> None:
        if tuple(model_paths) != _MODEL_ROLES:
            raise ValueError("RapidOCR requires det, cls, and rec model paths")
        self._paths = {role: Path(model_paths[role]) for role in _MODEL_ROLES}
        self._module_loader = module_loader
        self._engine: Any | None = None
        self._version = "rapidocr-unloaded"
        self._lock = Lock()

    @property
    def version(self) -> str:
        return self._version

    def recognize(self, image: bytes) -> tuple[OcrLine, ...]:
        engine = self._get_engine()
        try:
            result = engine(image)
        except Exception as error:
            raise OcrError(
                "runtime_failure",
                "The local OCR runtime failed to recognize this page",
                retryable=True,
            ) from error
        boxes = getattr(result, "boxes", None)
        texts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        if boxes is None and texts is None and scores is None:
            return ()
        if boxes is None or texts is None or scores is None:
            raise _invalid_output_error()
        try:
            if not (len(boxes) == len(texts) == len(scores)):
                raise _invalid_output_error()
            lines: list[OcrLine] = []
            for box, text_value, score_value in zip(boxes, texts, scores, strict=True):
                text = unicodedata.normalize("NFC", str(text_value)).strip()
                if not text:
                    continue
                score = float(score_value)
                if not math.isfinite(score) or not 0 <= score <= 1:
                    raise _invalid_output_error()
                if not isinstance(box, Sequence) or len(box) != 4:
                    raise _invalid_output_error()
                points: list[tuple[float, float]] = []
                for point in box:
                    if not isinstance(point, Sequence) or len(point) != 2:
                        raise _invalid_output_error()
                    x, y = float(point[0]), float(point[1])
                    if not math.isfinite(x) or not math.isfinite(y):
                        raise _invalid_output_error()
                    points.append((x, y))
                lines.append(
                    OcrLine(
                        text=text,
                        confidence=score,
                        polygon=cast(
                            tuple[
                                tuple[float, float],
                                tuple[float, float],
                                tuple[float, float],
                                tuple[float, float],
                            ],
                            tuple(points),
                        ),
                    )
                )
            return tuple(lines)
        except OcrError:
            raise
        except (TypeError, ValueError, OverflowError) as error:
            raise _invalid_output_error() from error

    def _get_engine(self) -> Any:
        with self._lock:
            if self._engine is not None:
                return self._engine
            try:
                module = self._module_loader()
                rapid_ocr = module.RapidOCR
                self._engine = rapid_ocr(params=self._parameters())
                version = str(getattr(module, "__version__", "unknown"))
                self._version = f"rapidocr-{version}"[:80]
                return self._engine
            except (ImportError, AttributeError, OSError) as error:
                raise OcrError(
                    "runtime_unavailable",
                    "The optional local OCR runtime is unavailable",
                    retryable=True,
                ) from error
            except Exception as error:
                raise OcrError(
                    "runtime_failure",
                    "The local OCR runtime could not load the verified models",
                    retryable=True,
                ) from error

    def _parameters(self) -> dict[str, Any]:
        return {
            "Global.log_level": "critical",
            "Global.use_det": True,
            "Global.use_cls": True,
            "Global.use_rec": True,
            "Det.engine_type": "onnxruntime",
            "Det.lang_type": "ch",
            "Det.model_type": "mobile",
            "Det.ocr_version": "PP-OCRv5",
            "Det.model_path": str(self._paths["det"]),
            "Cls.engine_type": "onnxruntime",
            "Cls.lang_type": "ch",
            "Cls.model_type": "mobile",
            "Cls.ocr_version": "PP-OCRv5",
            "Cls.model_path": str(self._paths["cls"]),
            "Rec.engine_type": "onnxruntime",
            "Rec.lang_type": "ch",
            "Rec.model_type": "mobile",
            "Rec.ocr_version": "PP-OCRv5",
            "Rec.model_path": str(self._paths["rec"]),
        }


class IsolatedOcrDocumentProcessor:
    """Render and recognize all scanned pages in one hard-bounded process."""

    def __init__(
        self,
        *,
        model_paths: Mapping[OcrModelRole, Path],
        work_root: Path,
        runner: NativeProcessRunner | None = None,
    ) -> None:
        if set(model_paths) != set(_MODEL_ROLES):
            raise ValueError("RapidOCR requires det, cls, and rec model paths")
        self._model_paths = {role: Path(model_paths[role]).resolve() for role in _MODEL_ROLES}
        self._runner = runner or NativeProcessRunner(work_root=work_root)

    def process(
        self,
        *,
        document: Path,
        pages: Sequence[_PageSnapshot],
        max_render_pixels: int,
        max_image_bytes: int,
        max_lines_per_page: int,
        max_lines_total: int,
        max_characters_per_page: int,
        max_characters_total: int,
        timeout_seconds: float,
        cancelled: Callable[[], bool],
        progress: RecognitionProgress,
    ) -> IsolatedOcrDocument:
        try:
            envelope = self._runner.run(
                operation="ocr_document",
                payload={
                    "document": str(Path(document).resolve()),
                    "pages": [
                        {
                            "page_number": page.page_number,
                            "page_width": page.page_width,
                            "page_height": page.page_height,
                        }
                        for page in pages
                    ],
                    "model_paths": {role: str(self._model_paths[role]) for role in _MODEL_ROLES},
                    "limits": {
                        "max_render_pixels": max_render_pixels,
                        "max_image_bytes": max_image_bytes,
                        "max_lines_per_page": max_lines_per_page,
                        "max_lines_total": max_lines_total,
                        "max_characters_per_page": max_characters_per_page,
                        "max_characters_total": max_characters_total,
                    },
                },
                timeout_seconds=timeout_seconds,
                cancelled=cancelled,
                progress=progress,
            )
        except NativeProcessError as error:
            if error.code == "cancelled":
                raise _recognition_cancelled_error() from error
            if error.code == "time_limit":
                raise OcrError(
                    "time_limit",
                    "The document exceeded the safe OCR time limit",
                    retryable=False,
                ) from error
            if error.code == "worker_unavailable":
                raise OcrError(
                    "runtime_unavailable",
                    "The optional local OCR runtime is unavailable",
                    retryable=True,
                ) from error
            raise OcrError(
                "runtime_failure",
                "The isolated OCR worker failed safely",
                retryable=True,
            ) from error
        return _isolated_ocr_document(
            envelope,
            pages=pages,
            max_render_pixels=max_render_pixels,
            max_lines_per_page=max_lines_per_page,
            max_lines_total=max_lines_total,
            max_characters_per_page=max_characters_per_page,
            max_characters_total=max_characters_total,
        )


def _isolated_ocr_document(
    envelope: Mapping[str, Any],
    *,
    pages: Sequence[_PageSnapshot],
    max_render_pixels: int,
    max_lines_per_page: int,
    max_lines_total: int,
    max_characters_per_page: int,
    max_characters_total: int,
) -> IsolatedOcrDocument:
    if envelope.get("ok") is False:
        error = envelope.get("error")
        if set(envelope) != {"ok", "error"} or not isinstance(error, dict):
            raise _isolated_ocr_protocol_error()
        code = error.get("code")
        policy: dict[str, tuple[str, str, bool]] = {
            "runtime_unavailable": (
                "runtime_unavailable",
                "The optional local OCR runtime is unavailable",
                True,
            ),
            "runtime_failure": (
                "runtime_failure",
                "The local OCR runtime failed safely",
                True,
            ),
            "invalid_output": (
                "invalid_output",
                "The OCR backend returned invalid output",
                False,
            ),
            "document_damaged": (
                "document_damaged",
                "The local PDF cannot be rendered safely for OCR",
                False,
            ),
            "stale_pages": (
                "stale_pages",
                "The extracted page evidence no longer matches the PDF",
                True,
            ),
            "page_too_large": (
                "page_too_large",
                "The scanned page is too large to render safely for OCR",
                False,
            ),
            "image_too_large": (
                "image_too_large",
                "The rendered page image exceeds the safe OCR input limit",
                False,
            ),
            "too_many_lines": (
                "too_many_lines",
                "The OCR page contains too many lines to process safely",
                False,
            ),
            "too_much_text": (
                "too_much_text",
                "The OCR output contains too much text to process safely",
                False,
            ),
            "time_limit": (
                "time_limit",
                "The document exceeded the safe OCR time limit",
                False,
            ),
            "cancelled": ("cancelled", "The scanned-page OCR was cancelled", True),
            "result_limit": (
                "invalid_output",
                "The OCR result exceeded its safe output limit",
                False,
            ),
        }
        selected = policy.get(code) if isinstance(code, str) else None
        if selected is None:
            raise _isolated_ocr_protocol_error()
        raise OcrError(selected[0], selected[1], retryable=selected[2])
    result = envelope.get("result")
    if (
        set(envelope) != {"ok", "result"}
        or envelope.get("ok") is not True
        or not isinstance(result, dict)
        or set(result) != {"engine_version", "pages"}
    ):
        raise _isolated_ocr_protocol_error()
    engine_version = result.get("engine_version")
    raw_pages = result.get("pages")
    if (
        not isinstance(engine_version, str)
        or not 1 <= len(engine_version) <= 80
        or any(ord(character) < 32 for character in engine_version)
        or not isinstance(raw_pages, list)
        or len(raw_pages) != len(pages)
    ):
        raise _isolated_ocr_protocol_error()
    parsed_pages: list[IsolatedOcrPage] = []
    character_total = 0
    line_total = 0
    for expected, raw_page in zip(pages, raw_pages, strict=True):
        if not isinstance(raw_page, dict) or set(raw_page) != {
            "page_number",
            "rendered_width",
            "rendered_height",
            "lines",
        }:
            raise _isolated_ocr_protocol_error()
        page_number = raw_page.get("page_number")
        rendered_width = raw_page.get("rendered_width")
        rendered_height = raw_page.get("rendered_height")
        raw_lines = raw_page.get("lines")
        if (
            page_number != expected.page_number
            or isinstance(rendered_width, bool)
            or not isinstance(rendered_width, int)
            or isinstance(rendered_height, bool)
            or not isinstance(rendered_height, int)
            or rendered_width <= 0
            or rendered_height <= 0
            or rendered_width * rendered_height > max_render_pixels
            or not isinstance(raw_lines, list)
            or len(raw_lines) > max_lines_per_page
        ):
            raise _isolated_ocr_protocol_error()
        line_total += len(raw_lines)
        if line_total > max_lines_total:
            raise _isolated_ocr_protocol_error()
        lines: list[OcrLine] = []
        page_characters = 0
        for raw_line in raw_lines:
            if not isinstance(raw_line, dict) or set(raw_line) != {
                "text",
                "confidence",
                "polygon",
            }:
                raise _isolated_ocr_protocol_error()
            text = raw_line.get("text")
            confidence = _isolated_ocr_number(raw_line.get("confidence"))
            raw_polygon = raw_line.get("polygon")
            if (
                not isinstance(text, str)
                or not text
                or len(text) > max_characters_per_page
                or not 0 <= confidence <= 1
                or not isinstance(raw_polygon, list)
                or len(raw_polygon) != 4
            ):
                raise _isolated_ocr_protocol_error()
            points: list[tuple[float, float]] = []
            for raw_point in raw_polygon:
                if not isinstance(raw_point, list) or len(raw_point) != 2:
                    raise _isolated_ocr_protocol_error()
                points.append(
                    (
                        _isolated_ocr_number(raw_point[0]),
                        _isolated_ocr_number(raw_point[1]),
                    )
                )
            page_characters += len(text)
            if page_characters > max_characters_per_page:
                raise _ocr_text_limit_error()
            lines.append(
                OcrLine(
                    text=text,
                    confidence=confidence,
                    polygon=cast(
                        tuple[
                            tuple[float, float],
                            tuple[float, float],
                            tuple[float, float],
                            tuple[float, float],
                        ],
                        tuple(points),
                    ),
                )
            )
        character_total += page_characters
        if character_total > max_characters_total:
            raise _ocr_text_limit_error()
        parsed_pages.append(
            IsolatedOcrPage(
                page_number=expected.page_number,
                rendered_width=rendered_width,
                rendered_height=rendered_height,
                lines=tuple(lines),
            )
        )
    return IsolatedOcrDocument(
        pages=tuple(parsed_pages),
        engine_version=engine_version,
    )


def _isolated_ocr_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _isolated_ocr_protocol_error()
    number = float(value)
    if not math.isfinite(number):
        raise _isolated_ocr_protocol_error()
    return number


def _isolated_ocr_protocol_error() -> OcrError:
    return OcrError(
        "invalid_output",
        "The isolated OCR worker returned an invalid result",
        retryable=False,
    )


def _required_string(value: Mapping[str, Any], key: str, *, maximum: int) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip() or len(raw) > maximum:
        raise OcrManifestError(f"OCR manifest {key} must be a non-empty string")
    return raw.strip()


def _validated_https_url(value: str, *, field: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise OcrManifestError(f"OCR manifest {field} must be an HTTPS URL")
    return value


def _safe_filename(value: str) -> str:
    if (
        Path(value).name != value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
        or not value.casefold().endswith(".onnx")
    ):
        raise OcrManifestError("OCR manifest model filename is unsafe")
    return value


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_file(path: Path, model: OcrModelFile) -> bool:
    return (
        path.is_file() and path.stat().st_size == model.size and _file_sha256(path) == model.sha256
    )


def _validate_content_range(
    response: httpx.Response,
    *,
    offset: int,
    size: int,
) -> None:
    value = response.headers.get("Content-Range", "")
    matched = _CONTENT_RANGE_PATTERN.fullmatch(value)
    if matched is None:
        raise _network_download_error()
    start, end, total = (int(part) for part in matched.groups())
    if start != offset or end != size - 1 or total != size:
        raise _network_download_error()


def _safe_cancelled(callback: Callable[[], bool]) -> bool:
    try:
        return bool(callback())
    except Exception as error:
        raise OcrModelDownloadError(
            "internal_error",
            "The OCR model cancellation check failed",
            retryable=False,
        ) from error


def _safe_progress(callback: OcrProgress, received: int, total: int) -> None:
    try:
        callback(received, total)
    except Exception as error:
        raise OcrModelDownloadError(
            "internal_error",
            "The OCR model progress callback failed",
            retryable=False,
        ) from error


def _network_download_error() -> OcrModelDownloadError:
    return OcrModelDownloadError(
        "network",
        "The OCR model could not be downloaded from its pinned source",
        retryable=True,
    )


def _cancelled_download_error() -> OcrModelDownloadError:
    return OcrModelDownloadError(
        "cancelled",
        "The OCR model installation was cancelled",
        retryable=False,
    )
