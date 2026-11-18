from __future__ import annotations

import pytest

from paper_reader.assistant.sectioning import (
    SectionBlock,
    SectionPage,
    detect_section,
)


def _block(number: int, text: str, *, height: float = 10) -> SectionBlock:
    return SectionBlock(
        block_number=number,
        text=text,
        x0=20,
        y0=number * 30,
        x1=390,
        y1=number * 30 + height,
    )


def test_numbered_heading_selects_pages_until_the_next_heading() -> None:
    pages = (
        SectionPage(
            page_number=1,
            text="1 Introduction\nBackground.",
            blocks=(_block(1, "1 Introduction", height=24), _block(2, "Background.")),
        ),
        SectionPage(
            page_number=2,
            text="2 Method\nObjective details.",
            blocks=(_block(1, "2 Method", height=24), _block(2, "Objective details.")),
        ),
        SectionPage(
            page_number=3,
            text="Optimization details.",
            blocks=(_block(1, "Optimization details."),),
        ),
        SectionPage(
            page_number=4,
            text="Ablation details.",
            blocks=(_block(1, "Ablation details."),),
        ),
        SectionPage(
            page_number=5,
            text="3 Results\nMetrics.",
            blocks=(_block(1, "3 Results", height=24), _block(2, "Metrics.")),
        ),
    )

    section = detect_section(pages, current_page=4)

    assert (section.start_page, section.end_page) == (2, 4)
    assert section.heading == "2 Method"
    assert section.confidence >= 0.65
    assert section.fallback is False


def test_large_standalone_block_can_identify_an_unnumbered_heading() -> None:
    pages = (
        SectionPage(
            page_number=1,
            text="Overview\nBody sentence.",
            blocks=(_block(1, "Overview", height=18), _block(2, "Body sentence.")),
        ),
        SectionPage(
            page_number=2,
            text="Neural Architecture\nLayer details.",
            blocks=(
                _block(1, "Neural Architecture", height=20),
                _block(2, "Layer details."),
            ),
        ),
        SectionPage(
            page_number=3,
            text="More layer details.",
            blocks=(_block(1, "More layer details."),),
        ),
    )

    section = detect_section(pages, current_page=3)

    assert (section.start_page, section.end_page) == (2, 3)
    assert section.heading == "Neural Architecture"
    assert section.fallback is False


@pytest.mark.parametrize("heading", ["方法", "III. Experiments", "References"])
def test_common_bilingual_and_roman_headings_are_recognized(heading: str) -> None:
    pages = (
        SectionPage(
            page_number=1,
            text=f"{heading}\nEvidence details.",
            blocks=(_block(1, heading), _block(2, "Evidence details.")),
        ),
    )

    section = detect_section(pages, current_page=1)

    assert section.heading == heading
    assert section.fallback is False


def test_low_confidence_or_missing_heading_falls_back_to_current_page() -> None:
    pages = (
        SectionPage(
            page_number=1,
            text="This is a complete sentence.\nMore prose follows.",
            blocks=(
                _block(1, "This is a complete sentence."),
                _block(2, "More prose follows."),
            ),
        ),
        SectionPage(
            page_number=2,
            text="Only prose is available.",
            blocks=(_block(1, "Only prose is available."),),
        ),
    )

    section = detect_section(pages, current_page=2)

    assert (section.start_page, section.end_page) == (2, 2)
    assert section.heading is None
    assert section.confidence == 0
    assert section.fallback is True


def test_section_detection_rejects_missing_current_page() -> None:
    pages = (
        SectionPage(
            page_number=1,
            text="1 Introduction",
            blocks=(_block(1, "1 Introduction"),),
        ),
    )

    with pytest.raises(ValueError, match="current page"):
        detect_section(pages, current_page=2)
