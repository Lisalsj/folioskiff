"""Tiny PDFs generated at test time without checked-in binary fixtures."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any
from zlib import compress


def _pymupdf() -> Any:
    return import_module("pymupdf")


def _white_pixel_png(pymupdf: Any) -> bytes:
    pixmap = pymupdf.Pixmap(
        pymupdf.csRGB,
        pymupdf.IRect(0, 0, 8, 8),
        False,
    )
    pixmap.clear_with(255)
    return pixmap.tobytes("png")


def write_text_pdf(path: Path, pages: tuple[str, ...]) -> Path:
    pymupdf = _pymupdf()
    document = pymupdf.open()
    try:
        for text in pages:
            page = document.new_page(width=420, height=595)
            font_name = "china-s" if any(ord(character) > 127 for character in text) else "helv"
            inserted = page.insert_textbox(
                pymupdf.Rect(48, 48, 372, 547),
                text,
                fontsize=12,
                fontname=font_name,
            )
            if inserted < 0:
                raise AssertionError("fixture text did not fit on its page")
        document.save(path)
    finally:
        document.close()
    return path


def write_text_and_image_pdf(path: Path, pages: tuple[str | None, ...]) -> Path:
    """Write text pages while using ``None`` for a scanned-image-only page."""

    pymupdf = _pymupdf()
    document = pymupdf.open()
    try:
        image = _white_pixel_png(pymupdf)
        for text in pages:
            page = document.new_page(width=420, height=595)
            if text is None:
                page.insert_image(page.rect, stream=image)
                continue
            font_name = "china-s" if any(ord(character) > 127 for character in text) else "helv"
            inserted = page.insert_textbox(
                pymupdf.Rect(48, 48, 372, 547),
                text,
                fontsize=12,
                fontname=font_name,
            )
            if inserted < 0:
                raise AssertionError("fixture text did not fit on its page")
        document.save(path)
    finally:
        document.close()
    return path


def write_image_only_pdf(path: Path) -> Path:
    pymupdf = _pymupdf()
    document = pymupdf.open()
    try:
        page = document.new_page(width=420, height=595)
        page.insert_image(page.rect, stream=_white_pixel_png(pymupdf))
        document.save(path)
    finally:
        document.close()
    return path


def write_mixed_pdf(path: Path) -> Path:
    pymupdf = _pymupdf()
    document = pymupdf.open()
    try:
        text_page = document.new_page(width=420, height=595)
        text_page.insert_text((48, 72), "Native evidence page", fontsize=12)
        image_page = document.new_page(width=420, height=595)
        image_page.insert_image(image_page.rect, stream=_white_pixel_png(pymupdf))
        document.save(path)
    finally:
        document.close()
    return path


def write_encrypted_pdf(path: Path, *, password: str = "reader-secret") -> Path:
    pymupdf = _pymupdf()
    document = pymupdf.open()
    try:
        page = document.new_page(width=420, height=595)
        page.insert_text((48, 72), "Encrypted evidence", fontsize=12)
        document.save(
            path,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner-secret",
            user_pw=password,
        )
    finally:
        document.close()
    return path


def write_malformed_pdf(path: Path) -> Path:
    path.write_bytes(b"%PDF-1.7\nthis is not a PDF object graph\n%%EOF\n")
    return path


def write_truncated_pdf(path: Path) -> Path:
    complete = write_text_pdf(path.with_suffix(".complete.pdf"), ("Truncated",))
    payload = complete.read_bytes()
    path.write_bytes(payload[: max(32, len(payload) // 3)])
    complete.unlink()
    return path


def write_compressed_text_bomb_pdf(
    path: Path,
    *,
    characters: int = 100_000,
) -> Path:
    """Write a tiny deflated stream that expands into one oversized text block."""

    if characters <= 0:
        raise ValueError("characters must be positive")
    commands = [b"BT /F1 1 Tf\n"]
    remaining = characters
    line_number = 0
    while remaining:
        line_size = min(100, remaining)
        y_position = 20 + (line_number % 100) * 5
        commands.append(
            f"1 0 0 1 10 {y_position} Tm (".encode("ascii")
            + (b"S" * line_size)
            + b") Tj\n"
        )
        line_number += 1
        remaining -= line_size
    commands.append(b"ET")
    content = b"".join(commands)
    compressed = compress(content, level=9)
    return _write_object_pdf(
        path,
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 595] "
                b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            ),
            (
                b"<< /Length "
                + str(len(compressed)).encode("ascii")
                + b" /Filter /FlateDecode >>\nstream\n"
                + compressed
                + b"\nendstream"
            ),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ),
    )


def write_giant_mediabox_pdf(
    path: Path,
    *,
    width: int = 50_000,
    height: int = 50_000,
) -> Path:
    if width <= 0 or height <= 0:
        raise ValueError("page dimensions must be positive")
    media_box = f"[0 0 {width} {height}]".encode("ascii")
    return _write_object_pdf(
        path,
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox "
            + media_box
            + b" /Contents 4 0 R >>",
            b"<< /Length 0 >>\nstream\n\nendstream",
        ),
    )


def write_recursive_page_tree_pdf(path: Path) -> Path:
    """Write a valid xref around an invalid self-referential page tree."""

    return _write_object_pdf(
        path,
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [2 0 R] /Count 1 >>",
        ),
    )


def _write_object_pdf(path: Path, object_bodies: tuple[bytes, ...]) -> Path:
    payload = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(object_bodies, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii"))
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(object_bodies) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(object_bodies) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(payload)
    return path
