"""Generated hostile-PDF regression corpus for bounded local processing."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from paper_reader.reader.extraction import ExtractionError, PdfTextExtractor
from tests.reader.pdf_fixtures import (
    write_compressed_text_bomb_pdf,
    write_encrypted_pdf,
    write_giant_mediabox_pdf,
    write_recursive_page_tree_pdf,
    write_text_pdf,
)


def _captured_error(action: Callable[[], object]) -> ExtractionError:
    with pytest.raises(ExtractionError) as captured:
        action()
    return captured.value


def test_deflated_text_bomb_is_small_on_disk_but_never_published(tmp_path: Path) -> None:
    source = write_compressed_text_bomb_pdf(
        tmp_path / "private-research-text.pdf",
        characters=100_000,
    )
    assert source.stat().st_size < 8_192

    error = _captured_error(
        lambda: PdfTextExtractor(max_characters_per_page=1_024).extract(
            source,
            cancelled=lambda: False,
        )
    )

    assert error.code == "too_much_text"
    assert str(source) not in str(error)
    assert "SSSS" not in str(error)


def test_prestart_cancel_stops_a_text_bomb_before_progress_or_decompression(
    tmp_path: Path,
) -> None:
    source = write_compressed_text_bomb_pdf(
        tmp_path / "private-cancelled-text-bomb.pdf",
        characters=100_000,
    )
    progress: list[tuple[int, int]] = []

    error = _captured_error(
        lambda: PdfTextExtractor(max_characters_per_page=1_024).extract(
            source,
            cancelled=lambda: True,
            progress=lambda completed, total: progress.append((completed, total)),
        )
    )

    assert error.code == "cancelled"
    assert error.retryable is False
    assert progress == []
    assert str(source) not in str(error)
    assert "private" not in str(error).casefold()


@pytest.mark.parametrize(
    ("factory", "options", "expected_code"),
    (
        (write_giant_mediabox_pdf, {}, "page_too_large"),
        (write_recursive_page_tree_pdf, {}, "damaged"),
        (write_encrypted_pdf, {}, "encrypted"),
        (
            lambda path: write_text_pdf(path, ("one", "two", "three")),
            {"max_pages": 2},
            "too_many_pages",
        ),
    ),
)
def test_generated_hostile_structures_have_stable_redacted_failures(
    tmp_path: Path,
    factory: Callable[[Path], Path],
    options: dict[str, int],
    expected_code: str,
) -> None:
    source = factory(tmp_path / f"private-{expected_code}.pdf")

    error = _captured_error(
        lambda: PdfTextExtractor(**options).extract(
            source,
            cancelled=lambda: False,
        )
    )

    assert error.code == expected_code
    assert str(source) not in str(error)
    assert "private" not in str(error).casefold()
