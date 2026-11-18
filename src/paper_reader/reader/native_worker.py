"""Private JSON worker used by the frozen executable for native document calls."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from secrets import token_hex
from typing import Any, cast

from paper_reader.reader.native_process import (
    MAX_NATIVE_REQUEST_BYTES,
    MAX_NATIVE_RESULT_BYTES,
    NATIVE_PROTOCOL_VERSION,
)


def run_native_worker(work_directory: Path | None = None) -> int:
    """Read one request from stdin, write one bounded result, and exit."""

    root = Path.cwd() if work_directory is None else Path(work_directory)
    try:
        request = _read_request()
        result_file = request["result_file"]
        if result_file != "result.json":
            raise ValueError("result filename")
        payload = _execute(
            cast(str, request["operation"]),
            cast(dict[str, Any], request["payload"]),
        )
        encoded = _encoded_result(payload)
        if len(encoded) > MAX_NATIVE_RESULT_BYTES:
            encoded = _encoded_result(
                {
                    "ok": False,
                    "error": {
                        "code": "result_limit",
                        "retryable": False,
                    },
                }
            )
        destination = root / result_file
        if destination.exists() or destination.is_symlink():
            raise ValueError("result collision")
        temporary = root / f".result-{token_hex(8)}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        _emit(
            {
                "type": "result",
                "byte_size": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
        return 0
    except Exception:
        return 2


def _read_request() -> dict[str, Any]:
    encoded = sys.stdin.buffer.read(MAX_NATIVE_REQUEST_BYTES + 1)
    if len(encoded) > MAX_NATIVE_REQUEST_BYTES:
        raise ValueError("request limit")
    value = json.loads(encoded)
    if not isinstance(value, dict) or set(value) != {
        "protocol_version",
        "operation",
        "payload",
        "result_file",
    }:
        raise ValueError("request envelope")
    if value.get("protocol_version") != NATIVE_PROTOCOL_VERSION:
        raise ValueError("protocol version")
    if not isinstance(value.get("operation"), str) or not isinstance(value.get("payload"), dict):
        raise ValueError("request payload")
    if not isinstance(value.get("result_file"), str):
        raise ValueError("result file")
    return cast(dict[str, Any], value)


def _execute(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    if operation == "pdf_extract":
        return _extract_pdf(payload)
    if operation == "ocr_document":
        return _recognize_document(payload)
    return {
        "ok": False,
        "error": {"code": "unsupported_operation", "retryable": False},
    }


def _recognize_document(payload: dict[str, Any]) -> dict[str, Any]:
    import math
    from importlib import import_module

    from paper_reader.reader.ocr_contracts import (
        OCR_RENDER_SCALE,
        OcrError,
        OcrModelRole,
    )
    from paper_reader.reader.ocr_runtime import RapidOcrBackend

    if set(payload) != {"document", "pages", "model_paths", "limits"}:
        raise ValueError("OCR payload")
    document = payload.get("document")
    pages = payload.get("pages")
    model_paths = payload.get("model_paths")
    limits = payload.get("limits")
    if (
        not isinstance(document, str)
        or not isinstance(pages, list)
        or not isinstance(model_paths, dict)
        or set(model_paths) != {"det", "cls", "rec"}
        or not all(isinstance(value, str) for value in model_paths.values())
        or not isinstance(limits, dict)
        or set(limits)
        != {
            "max_render_pixels",
            "max_image_bytes",
            "max_lines_per_page",
            "max_lines_total",
            "max_characters_per_page",
            "max_characters_total",
        }
    ):
        raise ValueError("OCR payload")
    try:
        roles: tuple[OcrModelRole, ...] = ("det", "cls", "rec")
        backend = RapidOcrBackend(
            model_paths={role: Path(cast(str, model_paths[role])) for role in roles}
        )
        pymupdf: Any = import_module("pymupdf")
        opened: Any = pymupdf.open(Path(document))
        output_pages: list[dict[str, Any]] = []
        character_total = 0
        line_total = 0
        with opened as pdf:
            if bool(pdf.needs_pass) or bool(getattr(pdf, "is_repaired", False)):
                raise OcrError(
                    "document_damaged",
                    "The local PDF cannot be rendered safely for OCR",
                    retryable=False,
                )
            page_count = int(pdf.page_count)
            for index, expected in enumerate(pages, start=1):
                if not isinstance(expected, dict) or set(expected) != {
                    "page_number",
                    "page_width",
                    "page_height",
                }:
                    raise ValueError("OCR page payload")
                page_number = expected.get("page_number")
                expected_width = expected.get("page_width")
                expected_height = expected.get("page_height")
                if (
                    isinstance(page_number, bool)
                    or not isinstance(page_number, int)
                    or not 1 <= page_number <= page_count
                    or isinstance(expected_width, bool)
                    or not isinstance(expected_width, (int, float))
                    or isinstance(expected_height, bool)
                    or not isinstance(expected_height, (int, float))
                ):
                    raise ValueError("OCR page payload")
                page = pdf.load_page(page_number - 1)
                width = float(page.rect.width)
                height = float(page.rect.height)
                if not math.isclose(width, float(expected_width), abs_tol=0.5) or not math.isclose(
                    height,
                    float(expected_height),
                    abs_tol=0.5,
                ):
                    raise OcrError(
                        "stale_pages",
                        "The extracted page evidence no longer matches the PDF",
                        retryable=True,
                    )
                projected_pixels = width * height * OCR_RENDER_SCALE * OCR_RENDER_SCALE
                if not math.isfinite(projected_pixels) or projected_pixels > int(
                    limits["max_render_pixels"]
                ):
                    raise OcrError(
                        "page_too_large",
                        "The scanned page is too large to render safely for OCR",
                        retryable=False,
                    )
                pixmap = page.get_pixmap(
                    matrix=pymupdf.Matrix(OCR_RENDER_SCALE, OCR_RENDER_SCALE),
                    alpha=False,
                )
                rendered_width = int(pixmap.width)
                rendered_height = int(pixmap.height)
                if (
                    rendered_width <= 0
                    or rendered_height <= 0
                    or rendered_width * rendered_height > int(limits["max_render_pixels"])
                ):
                    raise OcrError(
                        "page_too_large",
                        "The scanned page is too large to render safely for OCR",
                        retryable=False,
                    )
                image = cast(bytes, pixmap.tobytes("png"))
                if len(image) > int(limits["max_image_bytes"]):
                    raise OcrError(
                        "image_too_large",
                        "The rendered page image exceeds the safe OCR input limit",
                        retryable=False,
                    )
                lines = tuple(backend.recognize(image))
                if len(lines) > int(limits["max_lines_per_page"]):
                    raise OcrError(
                        "too_many_lines",
                        "The OCR page contains too many lines to process safely",
                        retryable=False,
                    )
                line_total += len(lines)
                if line_total > int(limits["max_lines_total"]):
                    raise OcrError(
                        "too_many_lines",
                        "The OCR document contains too many lines to process safely",
                        retryable=False,
                    )
                page_characters = sum(len(line.text) for line in lines)
                if page_characters > int(limits["max_characters_per_page"]):
                    raise OcrError(
                        "too_much_text",
                        "The OCR output contains too much text to process safely",
                        retryable=False,
                    )
                character_total += page_characters
                if character_total > int(limits["max_characters_total"]):
                    raise OcrError(
                        "too_much_text",
                        "The OCR output contains too much text to process safely",
                        retryable=False,
                    )
                output_pages.append(
                    {
                        "page_number": page_number,
                        "rendered_width": rendered_width,
                        "rendered_height": rendered_height,
                        "lines": [
                            {
                                "text": line.text,
                                "confidence": line.confidence,
                                "polygon": [list(point) for point in line.polygon],
                            }
                            for line in lines
                        ],
                    }
                )
                _emit({"type": "progress", "completed": index, "total": len(pages)})
        version = backend.version.strip()
        if not version or len(version) > 80:
            raise OcrError(
                "invalid_output",
                "The OCR backend version is invalid",
                retryable=False,
            )
        return {
            "ok": True,
            "result": {"engine_version": version, "pages": output_pages},
        }
    except OcrError as error:
        return {
            "ok": False,
            "error": {"code": error.code, "retryable": error.retryable},
        }
    except Exception:
        return {
            "ok": False,
            "error": {"code": "document_damaged", "retryable": False},
        }


def _extract_pdf(payload: dict[str, Any]) -> dict[str, Any]:
    from paper_reader.reader.extraction import ExtractionError, PdfTextExtractor

    if set(payload) != {"document", "limits"}:
        raise ValueError("PDF extraction payload")
    document = payload.get("document")
    limits = payload.get("limits")
    if not isinstance(document, str) or not isinstance(limits, dict):
        raise ValueError("PDF extraction payload")
    expected_limits = {
        "max_pages",
        "max_page_dimension_points",
        "max_page_area_points",
        "max_blocks_per_page",
        "max_blocks_total",
        "max_images_per_page",
        "max_characters_per_page",
        "max_characters_total",
        "max_seconds",
    }
    if set(limits) != expected_limits:
        raise ValueError("PDF extraction limits")
    try:
        extractor = PdfTextExtractor(**limits)
        result = extractor.extract(
            Path(document),
            cancelled=lambda: False,
            progress=lambda completed, total: _emit(
                {"type": "progress", "completed": completed, "total": total}
            ),
        )
    except ExtractionError as error:
        return {
            "ok": False,
            "error": {"code": error.code, "retryable": error.retryable},
        }
    return {
        "ok": True,
        "result": {
            "pages": [
                {
                    "page_number": page.page_number,
                    "page_width": page.page_width,
                    "page_height": page.page_height,
                    "text": page.text,
                    "blocks": [
                        {
                            "x0": block.x0,
                            "y0": block.y0,
                            "x1": block.x1,
                            "y1": block.y1,
                            "text": block.text,
                            "block_number": block.block_number,
                            "confidence": block.confidence,
                        }
                        for block in page.blocks
                    ],
                    "native_character_count": page.native_character_count,
                    "likely_scanned": page.likely_scanned,
                    "ocr_state": page.ocr_state,
                }
                for page in result.pages
            ],
            "page_count": result.page_count,
            "native_character_count": result.native_character_count,
            "scanned_page_count": result.scanned_page_count,
            "extractor_version": result.extractor_version,
        },
    }


def _encoded_result(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    return run_native_worker()


if __name__ == "__main__":
    raise SystemExit(main())
