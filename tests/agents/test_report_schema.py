from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError


def _evidence_bundle():  # type: ignore[no-untyped-def]
    from paper_reader.agents.evidence import (
        EvidenceAnnotation,
        EvidenceBundle,
        EvidenceCoverage,
        EvidenceLevel,
        EvidencePage,
        EvidenceWork,
    )

    return EvidenceBundle.create(
        work=EvidenceWork(
            work_id="work-report",
            title="<Diffusion> fixture",
            authors=("Alice", "Bob"),
            abstract="The authors claim improved robustness.",
            doi="10.1000/report",
            published_at="2026-06-01T00:00:00+00:00",
        ),
        document=None,
        evidence_level=EvidenceLevel.FULL_TEXT,
        pages=(
            EvidencePage(
                evidence_id="page:1:native",
                page_number=1,
                source="native",
                text="The method uses a controlled diffusion objective.",
                source_text_sha256="a" * 64,
                page_width=420,
                page_height=595,
                confidence=None,
                truncated=False,
            ),
            EvidencePage(
                evidence_id="page:2:ocr",
                page_number=2,
                source="ocr",
                text="Figure 2 and Table 1 report the experiment.",
                source_text_sha256="b" * 64,
                page_width=420,
                page_height=595,
                confidence=0.95,
                truncated=False,
            ),
        ),
        annotations=(
            EvidenceAnnotation(
                evidence_id="annotation:important",
                annotation_id="important",
                document_content_sha256="c" * 64,
                kind="highlight",
                page_number=2,
                quote_text="Table 1",
                context_before="See ",
                context_after=" for results.",
                rects=(),
                color="amber",
                note="Check the comparison.",
            ),
        ),
        coverage=EvidenceCoverage(
            total_pages=2,
            available_pages=2,
            included_pages=2,
            total_characters=94,
            included_characters=94,
            percentage=100,
            truncated=False,
        ),
        research_intent="Study robustness",
    )


def _reference(
    *,
    evidence_id: str = "page:1:native",
    kind: str = "page",
    page_number: int | None = 1,
    locator: str | None = None,
    url: str | None = None,
    external_title: str | None = None,
) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "kind": kind,
        "page_number": page_number,
        "locator": locator,
        "url": url,
        "external_title": external_title,
    }


def _statement(
    text: str = "Evidence-supported statement.",
    *,
    references: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "text": text,
        "references": references or [_reference()],
    }


def valid_report_payload() -> dict[str, object]:
    return {
        "title": "Diffusion robustness analysis",
        "overview": _statement("The paper studies diffusion robustness."),
        "research_question": _statement("Can the method improve robustness?"),
        "author_claims": [_statement("The authors report an improvement.")],
        "contributions": [_statement("The paper introduces a controlled objective.")],
        "method": [_statement("The method modifies the training objective.")],
        "assumptions": [_statement("The evaluation distribution is representative.")],
        "data_and_experiments": [
            _statement(
                "Table 1 contains the primary comparison.",
                references=[
                    _reference(
                        evidence_id="page:2:ocr",
                        kind="table",
                        page_number=2,
                        locator="Table 1",
                    )
                ],
            )
        ],
        "findings": [_statement("The reported metric increases.")],
        "limitations": [_statement("The evidence covers one benchmark.")],
        "reproducibility": [
            _statement(
                "A highlighted comparison needs independent verification.",
                references=[
                    _reference(
                        evidence_id="annotation:important",
                        kind="annotation",
                        page_number=2,
                    )
                ],
            )
        ],
        "relation_to_user_intent": [_statement("The work directly concerns robustness.")],
        "agent_judgments": [_statement("The evidence is promising but narrow.")],
    }


def test_minimum_and_full_report_are_strict_and_keep_claims_separate() -> None:
    from paper_reader.agents.report_schema import StructuredAnalysisReport

    payload = valid_report_payload()
    minimum = StructuredAnalysisReport.model_validate(payload)
    assert minimum.author_claims[0].text.startswith("The authors")
    assert minimum.agent_judgments[0].text.startswith("The evidence")
    assert (
        StructuredAnalysisReport.model_validate(minimum.model_dump(mode="json"))
        == minimum
    )

    full_payload = deepcopy(payload)
    full_payload["author_claims"] = [
        _statement("The authors report an improvement."),
        _statement(
            "The authors identify Figure 2 as supporting evidence.",
            references=[
                _reference(
                    evidence_id="page:2:ocr",
                    kind="figure",
                    page_number=2,
                    locator="Figure 2",
                )
            ],
        ),
    ]
    full = StructuredAnalysisReport.model_validate(full_payload)
    assert len(full.author_claims) == 2

    unknown = deepcopy(payload)
    unknown["score"] = 97
    with pytest.raises(ValidationError, match="Extra inputs"):
        StructuredAnalysisReport.model_validate(unknown)
    missing_judgments = deepcopy(payload)
    missing_judgments.pop("agent_judgments")
    with pytest.raises(ValidationError, match="agent_judgments"):
        StructuredAnalysisReport.model_validate(missing_judgments)
    mixed_claim = deepcopy(payload)
    mixed_claim["author_claims"][0]["agent_confidence"] = 0.9  # type: ignore[index]
    with pytest.raises(ValidationError, match="Extra inputs"):
        StructuredAnalysisReport.model_validate(mixed_claim)


def test_report_references_require_valid_typed_locators_and_https_urls() -> None:
    from paper_reader.agents.report_schema import EvidenceReference, ReportStatement

    with pytest.raises(ValidationError, match="references"):
        ReportStatement(text="Unsupported", references=[])
    with pytest.raises(ValidationError, match="locator"):
        EvidenceReference(
            evidence_id="page:2:ocr",
            kind="figure",
            page_number=2,
        )
    with pytest.raises(ValidationError, match="page_number"):
        EvidenceReference(
            evidence_id="page:2:ocr",
            kind="table",
            page_number=0,
            locator="Table 1",
        )
    with pytest.raises(ValidationError, match="HTTPS"):
        EvidenceReference(
            evidence_id="external:source-1",
            kind="external",
            url="http://example.com/report",
            external_title="External report",
        )
    with pytest.raises(ValidationError, match="HTTPS"):
        EvidenceReference(
            evidence_id="external:source-1",
            kind="external",
            url="javascript:alert(1)",
            external_title="External report",
        )
    with pytest.raises(ValidationError, match="page_number"):
        EvidenceReference(
            evidence_id="external:source-1",
            kind="external",
            page_number=1,
            url="https://example.com/report",
            external_title="External report",
        )


def test_cross_reference_validation_uses_only_the_exact_evidence_manifest() -> None:
    from paper_reader.agents.report_schema import (
        ReportEvidenceError,
        StructuredAnalysisReport,
        validate_report_evidence,
    )

    evidence = _evidence_bundle()
    report = StructuredAnalysisReport.model_validate(valid_report_payload())
    validate_report_evidence(report, evidence, external_research=False)

    unknown = report.model_copy(
        update={
            "findings": (
                report.findings[0].model_copy(
                    update={
                        "references": (
                            report.findings[0].references[0].model_copy(
                                update={"evidence_id": "page:999:native"}
                            ),
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ReportEvidenceError, match="not present"):
        validate_report_evidence(unknown, evidence, external_research=False)

    wrong_page = report.model_copy(
        update={
            "method": (
                report.method[0].model_copy(
                    update={
                        "references": (
                            report.method[0].references[0].model_copy(
                                update={"page_number": 2}
                            ),
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ReportEvidenceError, match="page number"):
        validate_report_evidence(wrong_page, evidence, external_research=False)

    external_payload = valid_report_payload()
    external_payload["limitations"] = [
        _statement(
            "An external record reports a later correction.",
            references=[
                _reference(
                    evidence_id="external:correction",
                    kind="external",
                    page_number=None,
                    url="https://example.org/correction",
                    external_title="Correction record",
                )
            ],
        )
    ]
    external = StructuredAnalysisReport.model_validate(external_payload)
    with pytest.raises(ReportEvidenceError, match="external research"):
        validate_report_evidence(external, evidence, external_research=False)
    validate_report_evidence(external, evidence, external_research=True)


def test_abstract_reference_requires_available_abstract_evidence() -> None:
    from paper_reader.agents.report_schema import (
        ReportEvidenceError,
        StructuredAnalysisReport,
        validate_report_evidence,
    )

    payload = valid_report_payload()
    payload["overview"] = _statement(
        "The abstract states the research goal.",
        references=[
            _reference(
                evidence_id="abstract:work",
                kind="abstract",
                page_number=None,
            )
        ],
    )
    report = StructuredAnalysisReport.model_validate(payload)
    validate_report_evidence(report, _evidence_bundle(), external_research=False)

    without_abstract = _evidence_bundle().model_copy(
        update={
            "work": _evidence_bundle().work.model_copy(update={"abstract": None}),
            "abstract_evidence": None,
        }
    )
    with pytest.raises(ReportEvidenceError, match="abstract"):
        validate_report_evidence(report, without_abstract, external_research=False)


def test_markdown_rendering_is_deterministic_ordered_and_escapes_model_text() -> None:
    from paper_reader.agents.report_schema import (
        StructuredAnalysisReport,
        render_report_markdown,
        validate_report_evidence,
    )

    payload = valid_report_payload()
    payload["title"] = "<script>alert(1)</script> # report"
    payload["overview"] = _statement("<img src=x onerror=alert(1)> **claimed** [link]")
    payload["limitations"] = [
        _statement(
            "External evidence has a narrow scope.",
            references=[
                _reference(
                    evidence_id="external:source-1",
                    kind="external",
                    page_number=None,
                    url="https://example.org/source?id=1",
                    external_title="[External] <source>",
                )
            ],
        )
    ]
    report = StructuredAnalysisReport.model_validate(payload)
    validate_report_evidence(report, _evidence_bundle(), external_research=True)

    first = render_report_markdown(report)
    repeated = render_report_markdown(report)

    assert first == repeated
    assert first.endswith("\n")
    assert "<script>" not in first
    assert "<img" not in first
    assert "javascript:" not in first
    assert "&lt;script&gt;" in first
    assert "\\*\\*claimed\\*\\*" in first
    assert "https://example.org/source?id=1" in first
    headings = (
        "## 概览",
        "## 研究问题",
        "## 作者主张",
        "## 核心贡献",
        "## 方法",
        "## 关键假设",
        "## 数据与实验",
        "## 主要发现",
        "## 局限与风险",
        "## 可复现性",
        "## 与研究意图的关系",
        "## Agent 判断",
    )
    assert [first.index(heading) for heading in headings] == sorted(
        first.index(heading) for heading in headings
    )
