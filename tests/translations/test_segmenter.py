from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import pytest

from paper_reader.assistant.contracts import ContextScope
from paper_reader.translations.segmenter import (
    ProtectedSpanError,
    SegmentationLimitError,
    protect_spans,
    restore_spans,
    segment_context,
)


@dataclass(frozen=True, slots=True)
class FrozenSnapshot:
    scope: ContextScope
    evidence: dict[str, Any]


def _page(
    page_number: int,
    text: str,
    *,
    source: str = "native",
    confidence: float | None = None,
) -> dict[str, object]:
    return {
        "evidence_id": f"page:{page_number}",
        "page_number": page_number,
        "source": source,
        "text": text,
        "source_text_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "page_width": 600,
        "page_height": 800,
        "confidence": confidence,
        "truncated": False,
    }


def _snapshot(
    *pages: dict[str, object],
    scope: ContextScope = ContextScope.FULL_DOCUMENT,
    selection_excerpt: dict[str, object] | None = None,
) -> FrozenSnapshot:
    return FrozenSnapshot(
        scope=scope,
        evidence={
            "evidence_level": "full_text",
            "pages": list(pages),
            "selection_excerpt": selection_excerpt,
        },
    )


def test_formula_citation_figure_url_doi_email_variable_and_code_round_trip() -> None:
    source = (
        "We minimize $L=\\sum_i p_i$ and display \\[E=mc^2\\] in Eq. (3), "
        "compare Fig. 2, Table 4, "
        "Algorithm 1 and [17], following (Smith et al., 2020). Variable x_i is fixed. "
        "Visit https://example.org/model?q=1, resolve 10.1000/xyz-123, email "
        "author@example.org, then run `python train.py`.\n\n"
        "```python\nloss = p_i.sum()\n```"
    )
    protected = protect_spans(source)

    literals = (
        "$L=\\sum_i p_i$",
        "\\[E=mc^2\\]",
        "Eq. (3)",
        "Fig. 2",
        "Table 4",
        "Algorithm 1",
        "[17]",
        "(Smith et al., 2020)",
        "x_i",
        "https://example.org/model?q=1",
        "10.1000/xyz-123",
        "author@example.org",
        "`python train.py`",
        "```python\nloss = p_i.sum()\n```",
    )
    for literal in literals:
        assert literal not in protected.text

    translated = protected.text.replace("We minimize", "我们最小化").replace("Variable", "变量")
    restored = restore_spans(translated, protected)
    for literal in literals:
        assert literal in restored


@pytest.mark.parametrize(
    "mutation",
    ("missing", "duplicated", "reordered", "altered", "invented", "invented_partial"),
)
def test_restoration_rejects_every_placeholder_integrity_failure(mutation: str) -> None:
    protected = protect_spans("Use $x_i$ with [4] and Fig. 2.")
    tokens = [span.token for span in protected.spans]
    assert len(tokens) == 3
    translated = protected.text
    if mutation == "missing":
        translated = translated.replace(tokens[0], "", 1)
    elif mutation == "duplicated":
        translated = translated.replace(tokens[0], tokens[0] + tokens[0], 1)
    elif mutation == "reordered":
        translated = translated.replace(tokens[0], "__FIRST__", 1)
        translated = translated.replace(tokens[1], tokens[0], 1)
        translated = translated.replace("__FIRST__", tokens[1], 1)
    elif mutation == "altered":
        translated = translated.replace(tokens[0][-10:-2], "deadbeef", 1)
    elif mutation == "invented":
        translated += " ⟦PR:999999:deadbeef⟧"
    else:
        translated += " ⟦PR:999999:deadbeef"

    with pytest.raises(ProtectedSpanError, match="placeholder"):
        restore_spans(translated, protected)


@pytest.mark.parametrize(
    "injected",
    ("⟦PR:000001:deadbeef⟧", "⟦PR:000001:deadbeef"),
)
def test_source_cannot_inject_reserved_placeholder_tokens(injected: str) -> None:
    with pytest.raises(ProtectedSpanError, match="reserved"):
        protect_spans(f"Untrusted paper text {injected}")


def test_segmenter_never_splits_a_protected_span_or_crosses_pages() -> None:
    page_one = (
        "First paragraph explains the objective in enough detail. " * 7
        + "$L=\\sum_i p_i$ remains one exact formula.\n\n"
        + "Second paragraph compares Fig. 2 and [17]. " * 7
    )
    page_two = (
        "扫描页也可以保留 Unicode 内容。 " * 10
        + "Use `python train.py` without translating the command."
    )
    segments = segment_context(
        _snapshot(
            _page(1, page_one),
            _page(2, page_two, source="ocr", confidence=0.72),
        ),
        max_characters=300,
    )

    assert [item.segment_id for item in segments] == [
        f"segment-{index:06d}" for index in range(1, len(segments) + 1)
    ]
    assert all(len(segment.protected_text) <= 300 for segment in segments)
    assert all(len(segment.page_numbers) == 1 for segment in segments)
    assert {segment.page_numbers[0] for segment in segments} == {1, 2}
    assert all(segment.block_numbers for segment in segments)
    assert any("$L=\\sum_i p_i$" in segment.source_text for segment in segments)
    assert any(segment.source_kind == "ocr" for segment in segments)
    assert all(
        segment.ocr_confidence == 0.72 for segment in segments if segment.source_kind == "ocr"
    )
    assert all(
        restore_spans(segment.protected_text, segment.protected) == segment.source_text
        for segment in segments
    )
    all_tokens = [span.token for segment in segments for span in segment.protected_spans]
    assert len(all_tokens) == len(set(all_tokens))


def test_selection_translation_uses_only_the_frozen_quote() -> None:
    page_text = "Private surrounding material. Translate only the selected claim. More context."
    selection = {
        "page_number": 5,
        "page_width": 600,
        "page_height": 800,
        "quote_text": "Translate only the selected claim.",
        "context_before": "Private surrounding material.",
        "context_after": "More context.",
        "rects": [{"x0": 10, "y0": 20, "x1": 250, "y1": 40}],
        "verified": True,
    }

    segments = segment_context(
        _snapshot(
            _page(5, page_text),
            scope=ContextScope.SELECTION,
            selection_excerpt=selection,
        ),
        max_characters=300,
    )

    assert len(segments) == 1
    assert segments[0].source_text == "Translate only the selected claim."
    assert "Private" not in segments[0].source_text


def test_segmenter_rejects_unverified_selection_and_tampered_page_hash() -> None:
    selection = {
        "page_number": 1,
        "quote_text": "Selected text",
        "verified": False,
    }
    with pytest.raises(ProtectedSpanError, match="verified"):
        segment_context(
            _snapshot(
                _page(1, "Selected text"),
                scope=ContextScope.SELECTION,
                selection_excerpt=selection,
            )
        )

    tampered = _page(1, "Original text")
    tampered["text"] = "Changed after freezing"
    with pytest.raises(ProtectedSpanError, match="hash"):
        segment_context(_snapshot(tampered))


def test_huge_single_sentence_hard_splits_without_losing_unicode() -> None:
    source = "研究Agentαβγ " * 700 + "$x_i$"
    segments = segment_context(_snapshot(_page(1, source)), max_characters=128)

    assert len(segments) > 2
    assert all(len(segment.protected_text) <= 128 for segment in segments)
    assert "研究Agentαβγ" in "".join(segment.source_text for segment in segments)
    assert sum("$x_i$" in segment.source_text for segment in segments) == 1


def test_hard_split_moves_to_placeholder_edge_instead_of_cutting_token() -> None:
    source = "a" * 120 + "$x_i$" + "b" * 100

    segments = segment_context(_snapshot(_page(1, source)), max_characters=128)

    assert all(len(segment.protected_text) <= 128 for segment in segments)
    assert sum("$x_i$" in segment.source_text for segment in segments) == 1
    tokens = [span.token for segment in segments for span in segment.protected_spans]
    assert len(tokens) == len(set(tokens))


def test_source_character_limit_fails_before_segmentation() -> None:
    oversized = "x" * 20_000_001
    page = _page(1, oversized)

    with pytest.raises(SegmentationLimitError, match="20,000,000"):
        segment_context(_snapshot(page), max_characters=12_000)
