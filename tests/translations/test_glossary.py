from __future__ import annotations

from hashlib import sha256

from paper_reader.translations.glossary import GlossaryBuilder
from paper_reader.translations.segmenter import SourceSegment, protect_spans


def _segment(text: str, index: int = 1) -> SourceSegment:
    protected = protect_spans(text)
    return SourceSegment(
        segment_id=f"segment-{index:06d}",
        segment_index=index,
        page_numbers=(1,),
        block_numbers=(index,),
        source_text=text,
        source_sha256=sha256(text.encode("utf-8")).hexdigest(),
        protected=protected,
        source_kind="native",
        ocr_confidence=None,
    )


def test_glossary_extracts_repeated_domain_terms_without_model_call() -> None:
    terms = GlossaryBuilder().build(
        title="Diffusion agents",
        keywords=("diffusion model", "agent"),
        segments=(_segment("A diffusion model supports an agent. " * 4),),
    )

    assert [term.source_term for term in terms] == ["agent", "diffusion model"]
    assert all(term.origin == "local_extraction" for term in terms)
    assert all(term.target_term is None for term in terms)


def test_glossary_extracts_abbreviations_and_repeated_hyphenated_terms() -> None:
    terms = GlossaryBuilder().build(
        title="Large Language Model (LLM) Alignment",
        keywords=(),
        segments=(
            _segment(
                "Large Language Model (LLM) uses self-attention. "
                "The LLM repeats self-attention in every layer."
            ),
        ),
    )

    sources = {term.source_term for term in terms}
    assert {"Large Language Model", "LLM", "self-attention"}.issubset(sources)


def test_glossary_nfkc_deduplicates_and_caps_terms_deterministically() -> None:
    keywords = tuple(
        ["ＡＧＥＮＴ", "agent", *(f"technical-term-{index:03d}" for index in range(600))]
    )

    terms = GlossaryBuilder(max_terms=500).build(
        title="Bounded terminology",
        keywords=keywords,
        segments=(_segment("agent"),),
    )

    assert len(terms) == 500
    normalized = [term.normalized_source_term for term in terms]
    assert normalized == sorted(set(normalized))
    assert normalized.count("agent") == 1
