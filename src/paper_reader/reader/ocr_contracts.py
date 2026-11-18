"""Immutable OCR contracts, limits, and stable public errors."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from paper_reader.jobs.models import JobRecord
from paper_reader.reader.extraction import TextBlock

OcrModelRole = Literal["det", "cls", "rec"]
OcrModelIntegrity = Literal["missing", "verified", "hash_mismatch"]
OcrProgress = Callable[[int, int], None]
RuntimeProbe = Callable[[], bool]
RecognitionProgress = Callable[[int, int], None]

_MODEL_ROLES: tuple[OcrModelRole, ...] = ("det", "cls", "rec")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_BUNDLE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{0,119}$")
_CONTENT_RANGE_PATTERN = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
OCR_MODEL_INSTALL_JOB_KIND = "reader.install_ocr_models"
OCR_RECOGNITION_JOB_KIND = "reader.ocr_document"
OCR_RENDER_SCALE = 2.0
OCR_MAX_RENDER_PIXELS = 25_000_000
OCR_MAX_SCANNED_PAGES = 500
OCR_MAX_IMAGE_BYTES = 64 * 1024 * 1024
OCR_MAX_LINES_PER_PAGE = 10_000
OCR_MAX_LINES_TOTAL = 200_000
OCR_MAX_CHARACTERS_PER_PAGE = 1_000_000
OCR_MAX_CHARACTERS_TOTAL = 10_000_000
OCR_MAX_SECONDS = 1_800.0
LEGAL_DOCUMENT_ACCESS = frozenset({"open", "user_provided"})


class OcrManifestError(ValueError):
    """The packaged or supplied OCR manifest is unsafe or inconsistent."""


class OcrError(RuntimeError):
    """Stable, user-safe OCR recognition failure."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class OcrModelDownloadError(OcrError):
    """Stable failure while acquiring one immutable OCR model bundle."""


@dataclass(frozen=True, slots=True)
class OcrModelFile:
    role: OcrModelRole
    filename: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class OcrModelBundle:
    bundle_id: str
    display_name: str
    version: str
    source_revision: str
    source_url: str
    license_name: str
    license_url: str
    copyright_notice: str
    files: tuple[OcrModelFile, ...]

    @property
    def total_size(self) -> int:
        return sum(model.size for model in self.files)


@dataclass(frozen=True, slots=True)
class OcrModelFileStatus:
    role: OcrModelRole
    filename: str
    size: int
    sha256: str
    installed: bool
    integrity: OcrModelIntegrity


@dataclass(frozen=True, slots=True)
class OcrCapability:
    available: bool
    runtime_available: bool
    models_installed: bool
    bundle_id: str
    display_name: str
    version: str
    total_size: int
    license_name: str
    license_url: str
    copyright_notice: str
    source_url: str
    source_revision: str
    files: tuple[OcrModelFileStatus, ...]
    degradation_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    confidence: float
    polygon: tuple[
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
    ]


@dataclass(frozen=True, slots=True)
class OcrPageResult:
    page_number: int
    text: str
    blocks: tuple[TextBlock, ...]
    confidence: float
    line_count: int


@dataclass(frozen=True, slots=True)
class OcrResult:
    document_version_id: str
    pages: tuple[OcrPageResult, ...]
    page_count: int
    line_count: int
    aggregate_confidence: float
    engine_version: str
    model_bundle_id: str


@dataclass(frozen=True, slots=True)
class _PageSnapshot:
    page_number: int
    page_width: float
    page_height: float
    native_evidence_hash: str


@dataclass(frozen=True, slots=True)
class _DocumentSnapshot:
    work_id: str
    document_version_id: str
    content_sha256: str
    path: Path
    pages: tuple[_PageSnapshot, ...]


@dataclass(frozen=True, slots=True)
class IsolatedOcrPage:
    page_number: int
    rendered_width: int
    rendered_height: int
    lines: tuple[OcrLine, ...]


@dataclass(frozen=True, slots=True)
class IsolatedOcrDocument:
    pages: tuple[IsolatedOcrPage, ...]
    engine_version: str


class OcrBackend(Protocol):
    @property
    def version(self) -> str: ...

    def recognize(self, image: bytes) -> Sequence[OcrLine]: ...


class OcrDocumentProcessor(Protocol):
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
    ) -> IsolatedOcrDocument: ...


class OcrAssetLifecycle(Protocol):
    def create_install_job(self, *, bundle_id: str, idempotency_key: str) -> JobRecord: ...

    async def install(self, job_id: str) -> None: ...

    def resumable_job_ids(self) -> tuple[str, ...]: ...

    def is_active(self, bundle_id: str, revision: str) -> bool: ...

    def reference_job(self, bundle_id: str, revision: str, job_id: str) -> None: ...

    def release_job(self, bundle_id: str, revision: str, job_id: str) -> None: ...


def _recognition_cancelled_error() -> OcrError:
    return OcrError(
        "cancelled",
        "The scanned-page OCR was cancelled",
        retryable=False,
    )


def _ocr_line_limit_error() -> OcrError:
    return OcrError(
        "too_many_lines",
        "The OCR document contains too many lines to process safely",
        retryable=False,
    )


def _ocr_text_limit_error() -> OcrError:
    return OcrError(
        "too_much_text",
        "The OCR output contains too much text to process safely",
        retryable=False,
    )


def _invalid_output_error() -> OcrError:
    return OcrError(
        "invalid_output",
        "The local OCR runtime returned invalid output",
        retryable=False,
    )
