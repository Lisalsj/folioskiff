"""Conservative, content-bound visual evidence for Agent requests.

PDF pages are rendered only after extracted text points at a figure, table, or
equation and the caller has confirmed that the selected model accepts images.
Rendering is in-memory so no derived page image survives a request.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from typing import Any

from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentErrorCode,
    EvidenceAttachment,
)
from paper_reader.agents.evidence import EvidenceBundle, EvidencePage

_REFERENCE = re.compile(
    r"""
    (?:
        (?P<english>fig(?:ure)?|table|eq(?:uation)?|formula)
        \s*(?:[.:#]?\s*)[\(\[]?[A-Z]?\d+(?:[A-Z])?[\)\]]?
      |
        (?P<chinese>图|表|公式|方程)
        \s*[\(\[]?[A-Z]?\d+(?:[A-Z])?[\)\]]?
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_DEPENDENCY = re.compile(
    r"(?i)\b(?:see|shown|depicted|illustrated|refer(?:red)?\s+to|according\s+to)\b"
    r"|(?:参见|见图|见表|见公式|见方程|如图|如表|如公式|所示|显示)"
)
_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_RENDER_ATTEMPTS = 10
_MIN_RENDER_SCALE = 0.2


class VisualEvidenceError(RuntimeError):
    """Visual evidence could not be proven to match the frozen document."""


@dataclass(frozen=True, slots=True)
class VisualEvidencePolicy:
    enabled: bool = True
    adjacent_pages: int = 1
    max_pages: int = 6
    max_dimension_pixels: int = 1600
    max_total_pixels: int = 10_000_000
    max_page_bytes: int = 3 * 1024 * 1024
    max_total_bytes: int = 12 * 1024 * 1024
    render_scale: float = 2.0
    minimum_sufficient_characters: int = 240

    def __post_init__(self) -> None:
        if not 0 <= self.adjacent_pages <= 2:
            raise ValueError("adjacent_pages must be between zero and two")
        if not 1 <= self.max_pages <= 6:
            raise ValueError("max_pages must be between one and six")
        if not 128 <= self.max_dimension_pixels <= 4096:
            raise ValueError("max_dimension_pixels must be between 128 and 4096")
        if self.max_total_pixels < 16_384:
            raise ValueError("max_total_pixels is too small")
        if self.max_page_bytes < 16_384 or self.max_total_bytes < self.max_page_bytes:
            raise ValueError("visual byte limits are invalid")
        if not 0.2 <= self.render_scale <= 4:
            raise ValueError("render_scale must be between 0.2 and 4")
        if self.minimum_sufficient_characters < 1:
            raise ValueError("minimum_sufficient_characters must be positive")


@dataclass(frozen=True, slots=True)
class VisualPageSelection:
    page_number: int
    evidence_id: str
    primary: bool
    reference_kinds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RenderedVisualPage:
    page_number: int
    evidence_id: str
    primary: bool
    reference_kinds: tuple[str, ...]
    document_content_sha256: str
    image_sha256: str
    width_pixels: int
    height_pixels: int
    attachment: EvidenceAttachment


@dataclass(frozen=True, slots=True)
class VisualEvidenceBatch:
    document_content_sha256: str | None
    pages: tuple[RenderedVisualPage, ...] = ()

    @property
    def attachments(self) -> tuple[EvidenceAttachment, ...]:
        return tuple(page.attachment for page in self.pages)

    @property
    def total_pixels(self) -> int:
        return sum(page.width_pixels * page.height_pixels for page in self.pages)

    @property
    def total_bytes(self) -> int:
        return sum(len(page.attachment.content) for page in self.pages)

    @property
    def manifest(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "page_number": page.page_number,
                "evidence_id": page.evidence_id,
                "primary": page.primary,
                "reference_kinds": list(page.reference_kinds),
                "document_content_sha256": page.document_content_sha256,
                "image_sha256": page.image_sha256,
                "filename": page.attachment.name,
                "media_type": page.attachment.media_type,
                "width_pixels": page.width_pixels,
                "height_pixels": page.height_pixels,
            }
            for page in self.pages
        )


def select_visual_pages(
    evidence: EvidenceBundle,
    *,
    supports_vision: bool,
    policy: VisualEvidencePolicy | None = None,
    text_extraction_sufficient: bool | None = None,
) -> tuple[VisualPageSelection, ...]:
    """Return referenced pages plus bounded context, never arbitrary pages."""

    selected_policy = policy or VisualEvidencePolicy()
    if (
        not selected_policy.enabled
        or not supports_vision
        or text_extraction_sufficient is True
        or evidence.document is None
        or evidence.document.media_type.casefold() != "application/pdf"
    ):
        return ()

    pages_by_number = {page.page_number: page for page in evidence.pages}
    primary: list[tuple[EvidencePage, tuple[str, ...]]] = []
    for page in evidence.pages:
        kinds = _reference_kinds(page.text)
        if not kinds:
            continue
        if text_extraction_sufficient is False or _needs_visual_render(
            page,
            minimum_characters=selected_policy.minimum_sufficient_characters,
        ):
            primary.append((page, kinds))
    if not primary:
        return ()

    primary = primary[: selected_policy.max_pages]
    selected: dict[int, VisualPageSelection] = {
        page.page_number: VisualPageSelection(
            page_number=page.page_number,
            evidence_id=page.evidence_id,
            primary=True,
            reference_kinds=kinds,
        )
        for page, kinds in primary
    }
    for distance in range(1, selected_policy.adjacent_pages + 1):
        for page, _ in primary:
            for page_number in (page.page_number - distance, page.page_number + distance):
                if len(selected) >= selected_policy.max_pages:
                    break
                context = pages_by_number.get(page_number)
                if context is None or page_number in selected:
                    continue
                selected[page_number] = VisualPageSelection(
                    page_number=page_number,
                    evidence_id=context.evidence_id,
                    primary=False,
                    reference_kinds=("context",),
                )
            if len(selected) >= selected_policy.max_pages:
                break
        if len(selected) >= selected_policy.max_pages:
            break
    return tuple(selected[number] for number in sorted(selected))


def render_visual_evidence(
    pdf_path: Path,
    evidence: EvidenceBundle,
    *,
    supports_vision: bool,
    policy: VisualEvidencePolicy | None = None,
    text_extraction_sufficient: bool | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> VisualEvidenceBatch:
    """Render a bounded in-memory PNG batch after verifying the PDF hash."""

    selected_policy = policy or VisualEvidencePolicy()
    selections = select_visual_pages(
        evidence,
        supports_vision=supports_vision,
        policy=selected_policy,
        text_extraction_sufficient=text_extraction_sufficient,
    )
    document = evidence.document
    if not selections or document is None:
        return VisualEvidenceBatch(
            document_content_sha256=(document.content_sha256 if document else None)
        )
    _raise_if_cancelled(cancelled)
    path = Path(pdf_path)
    if not path.is_file():
        raise VisualEvidenceError("Visual evidence PDF is unavailable")
    actual_hash = _file_sha256(path, cancelled=cancelled)
    if actual_hash != document.content_sha256.casefold():
        raise VisualEvidenceError("Visual evidence PDF content hash does not match")
    if not _HASH.fullmatch(actual_hash):  # pragma: no cover - defensive invariant
        raise VisualEvidenceError("Visual evidence document hash is invalid")
    _raise_if_cancelled(cancelled)

    pymupdf: Any = import_module("pymupdf")
    rendered: list[RenderedVisualPage] = []
    total_pixels = 0
    total_bytes = 0
    try:
        opened: Any = pymupdf.open(path)
        try:
            for selection in selections:
                _raise_if_cancelled(cancelled)
                if selection.page_number > int(opened.page_count):
                    raise VisualEvidenceError("Visual evidence page is outside the verified PDF")
                remaining_pixels = selected_policy.max_total_pixels - total_pixels
                remaining_bytes = selected_policy.max_total_bytes - total_bytes
                if remaining_pixels < 16_384 or remaining_bytes < 16_384:
                    break
                page: Any = opened.load_page(selection.page_number - 1)
                image = _render_page(
                    pymupdf,
                    page,
                    policy=selected_policy,
                    remaining_pixels=remaining_pixels,
                    remaining_bytes=remaining_bytes,
                    cancelled=cancelled,
                )
                if image is None:
                    continue
                content, width, height = image
                image_hash = sha256(content).hexdigest()
                name = f"visual-{actual_hash[:16]}-p{selection.page_number:04d}.png"
                attachment = EvidenceAttachment(
                    name=name,
                    media_type="image/png",
                    content=content,
                )
                rendered.append(
                    RenderedVisualPage(
                        page_number=selection.page_number,
                        evidence_id=selection.evidence_id,
                        primary=selection.primary,
                        reference_kinds=selection.reference_kinds,
                        document_content_sha256=actual_hash,
                        image_sha256=image_hash,
                        width_pixels=width,
                        height_pixels=height,
                        attachment=attachment,
                    )
                )
                total_pixels += width * height
                total_bytes += len(content)
        finally:
            opened.close()
    except AgentBackendError:
        raise
    except VisualEvidenceError:
        raise
    except Exception as error:
        raise VisualEvidenceError("Verified PDF page rendering failed") from error
    _raise_if_cancelled(cancelled)
    return VisualEvidenceBatch(
        document_content_sha256=actual_hash,
        pages=tuple(rendered),
    )


def _reference_kinds(text: str) -> tuple[str, ...]:
    kinds: set[str] = set()
    for match in _REFERENCE.finditer(text):
        raw = (match.group("english") or match.group("chinese") or "").casefold()
        if raw.startswith("fig") or raw == "图":
            kinds.add("figure")
        elif raw == "table" or raw == "表":
            kinds.add("table")
        else:
            kinds.add("equation")
    return tuple(sorted(kinds))


def _needs_visual_render(page: EvidencePage, *, minimum_characters: int) -> bool:
    if page.truncated or page.source == "ocr":
        return True
    if len(page.text) < minimum_characters:
        return True
    return _DEPENDENCY.search(page.text) is not None


def _render_page(
    pymupdf: Any,
    page: Any,
    *,
    policy: VisualEvidencePolicy,
    remaining_pixels: int,
    remaining_bytes: int,
    cancelled: Callable[[], bool],
) -> tuple[bytes, int, int] | None:
    width_points = float(page.rect.width)
    height_points = float(page.rect.height)
    if width_points <= 0 or height_points <= 0:
        raise VisualEvidenceError("Visual evidence page dimensions are invalid")
    scale = min(
        policy.render_scale,
        policy.max_dimension_pixels / max(width_points, height_points),
        math.sqrt(remaining_pixels / (width_points * height_points)),
    )
    for _ in range(_MAX_RENDER_ATTEMPTS):
        _raise_if_cancelled(cancelled)
        if scale < _MIN_RENDER_SCALE:
            return None
        pixmap: Any = page.get_pixmap(
            matrix=pymupdf.Matrix(scale, scale),
            colorspace=pymupdf.csRGB,
            alpha=False,
        )
        width = int(pixmap.width)
        height = int(pixmap.height)
        pixels = width * height
        content = bytes(pixmap.tobytes("png"))
        if (
            max(width, height) <= policy.max_dimension_pixels
            and pixels <= remaining_pixels
            and len(content) <= policy.max_page_bytes
            and len(content) <= remaining_bytes
        ):
            return content, width, height
        scale *= 0.75
    return None


def _file_sha256(path: Path, *, cancelled: Callable[[], bool]) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            _raise_if_cancelled(cancelled)
            digest.update(chunk)
    return digest.hexdigest()


def _raise_if_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise AgentBackendError(
            AgentErrorCode.CANCELLED,
            "Visual evidence rendering was cancelled",
            retryable=True,
        )
