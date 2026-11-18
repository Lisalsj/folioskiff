"""Strict structured output and deterministic Markdown for paper reports."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from paper_reader.agents.evidence import EvidenceBundle

ReferenceKind = Literal[
    "abstract",
    "page",
    "figure",
    "table",
    "equation",
    "annotation",
    "external",
]
_PAGE_KINDS = frozenset({"page", "figure", "table", "equation"})
_LOCATOR_KINDS = frozenset({"figure", "table", "equation"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>])")
_REPORT_SECTIONS = (
    ("概览", "overview"),
    ("研究问题", "research_question"),
    ("作者主张", "author_claims"),
    ("核心贡献", "contributions"),
    ("方法", "method"),
    ("关键假设", "assumptions"),
    ("数据与实验", "data_and_experiments"),
    ("主要发现", "findings"),
    ("局限与风险", "limitations"),
    ("可复现性", "reproducibility"),
    ("与研究意图的关系", "relation_to_user_intent"),
    ("Agent 判断", "agent_judgments"),
)


class ReportEvidenceError(ValueError):
    """A model reference is not supported by the selected evidence snapshot."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class EvidenceReference(_StrictModel):
    evidence_id: str = Field(min_length=1, max_length=200)
    kind: ReferenceKind
    page_number: int | None = Field(default=None, ge=1)
    locator: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, max_length=2048)
    external_title: str | None = Field(default=None, max_length=300)

    @field_validator("evidence_id")
    @classmethod
    def _safe_evidence_id(cls, value: str) -> str:
        if _CONTROL.search(value):
            raise ValueError("evidence_id cannot contain control characters")
        return value

    @model_validator(mode="after")
    def _typed_locator(self) -> EvidenceReference:
        if self.kind in _PAGE_KINDS:
            if self.page_number is None:
                raise ValueError(f"page_number is required for {self.kind} evidence")
            if self.kind in _LOCATOR_KINDS and not self.locator:
                raise ValueError(f"locator is required for {self.kind} evidence")
            if self.kind == "page" and self.locator is not None:
                raise ValueError("locator is not valid for page evidence")
            self._reject_external_fields()
            return self
        if self.kind == "annotation":
            if self.page_number is None:
                raise ValueError("page_number is required for annotation evidence")
            if self.locator is not None:
                raise ValueError("locator is not valid for annotation evidence")
            self._reject_external_fields()
            return self
        if self.kind == "abstract":
            if self.evidence_id != "abstract:work":
                raise ValueError("abstract evidence_id must be abstract:work")
            if self.page_number is not None:
                raise ValueError("page_number is not valid for abstract evidence")
            if self.locator is not None:
                raise ValueError("locator is not valid for abstract evidence")
            self._reject_external_fields()
            return self

        if not self.evidence_id.startswith("external:"):
            raise ValueError("external evidence_id must begin with external:")
        if self.page_number is not None:
            raise ValueError("page_number is not valid for external evidence")
        if self.locator is not None:
            raise ValueError("locator is not valid for external evidence")
        if not self.url or not self.external_title:
            raise ValueError("external evidence requires an HTTPS URL and title")
        _validate_https_url(self.url)
        return self

    def _reject_external_fields(self) -> None:
        if self.url is not None or self.external_title is not None:
            raise ValueError("local evidence cannot contain external URL fields")


class ReportStatement(_StrictModel):
    text: str = Field(min_length=1, max_length=20_000)
    references: Annotated[tuple[EvidenceReference, ...], Field(min_length=1, max_length=30)]


StatementList = Annotated[
    tuple[ReportStatement, ...],
    Field(min_length=1, max_length=100),
]


class StructuredAnalysisReport(_StrictModel):
    """One fixed report structure shared by every Agent backend."""

    title: str = Field(min_length=1, max_length=500)
    overview: ReportStatement
    research_question: ReportStatement
    author_claims: StatementList
    contributions: StatementList
    method: StatementList
    assumptions: StatementList
    data_and_experiments: StatementList
    findings: StatementList
    limitations: StatementList
    reproducibility: StatementList
    relation_to_user_intent: StatementList
    agent_judgments: StatementList

    def statements(self) -> tuple[ReportStatement, ...]:
        values: list[ReportStatement] = [self.overview, self.research_question]
        for _, field_name in _REPORT_SECTIONS[2:]:
            values.extend(getattr(self, field_name))
        return tuple(values)


def analysis_report_json_schema() -> dict[str, Any]:
    """Return the backend-facing strict JSON Schema."""

    return StructuredAnalysisReport.model_json_schema(mode="validation")


def validate_report_evidence(
    report: StructuredAnalysisReport,
    evidence: EvidenceBundle,
    *,
    external_research: bool,
) -> None:
    """Reject citations outside the exact evidence manifest and network policy."""

    pages = {page.evidence_id: page for page in evidence.pages}
    annotations = {item.evidence_id: item for item in evidence.annotations}
    abstract_available = bool(evidence.abstract_evidence or evidence.work.abstract)
    for statement in report.statements():
        for reference in statement.references:
            if reference.kind == "external":
                if not external_research:
                    raise ReportEvidenceError(
                        "Report contains external research while the request disabled it"
                    )
                continue
            if reference.kind == "abstract":
                if not abstract_available:
                    raise ReportEvidenceError(
                        "Report references abstract evidence that is not available"
                    )
                continue
            if reference.kind == "annotation":
                annotation = annotations.get(reference.evidence_id)
                if annotation is None:
                    raise ReportEvidenceError(
                        f"Evidence {reference.evidence_id!r} is not present in the manifest"
                    )
                if annotation.page_number != reference.page_number:
                    raise ReportEvidenceError(
                        f"Evidence {reference.evidence_id!r} has a different page number"
                    )
                continue
            page = pages.get(reference.evidence_id)
            if page is None:
                raise ReportEvidenceError(
                    f"Evidence {reference.evidence_id!r} is not present in the manifest"
                )
            if page.page_number != reference.page_number:
                raise ReportEvidenceError(
                    f"Evidence {reference.evidence_id!r} has a different page number"
                )


def render_report_markdown(report: StructuredAnalysisReport) -> str:
    """Render model text as inert Markdown under a controlled heading structure."""

    lines = [f"# {_escape_markdown(report.title)}", ""]
    for heading, field_name in _REPORT_SECTIONS:
        lines.extend((f"## {heading}", ""))
        value = getattr(report, field_name)
        statements: Iterable[ReportStatement] = (
            (value,) if isinstance(value, ReportStatement) else value
        )
        for statement in statements:
            lines.append(f"- {_escape_markdown(statement.text)}")
            lines.append(f"  - 证据：{_render_references(statement.references)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def external_citations(report: StructuredAnalysisReport) -> tuple[dict[str, str], ...]:
    """Return a deterministic de-duplicated external citation manifest."""

    values: dict[tuple[str, str, str], dict[str, str]] = {}
    for statement in report.statements():
        for reference in statement.references:
            if reference.kind != "external":
                continue
            assert reference.url is not None
            assert reference.external_title is not None
            key = (reference.evidence_id, reference.url, reference.external_title)
            values[key] = {
                "evidence_id": reference.evidence_id,
                "url": reference.url,
                "title": reference.external_title,
            }
    return tuple(values[key] for key in sorted(values))


def _render_references(references: tuple[EvidenceReference, ...]) -> str:
    rendered: list[str] = []
    for reference in references:
        if reference.kind == "abstract":
            rendered.append("论文摘要")
        elif reference.kind == "page":
            rendered.append(f"第 {reference.page_number} 页")
        elif reference.kind == "annotation":
            rendered.append(
                f"标注 {_escape_markdown(reference.evidence_id)}"
                f"（第 {reference.page_number} 页）"
            )
        elif reference.kind in _LOCATOR_KINDS:
            rendered.append(
                f"{_escape_markdown(reference.locator or '')}"
                f"（第 {reference.page_number} 页）"
            )
        else:
            assert reference.url is not None
            assert reference.external_title is not None
            rendered.append(
                f"[{_escape_markdown(reference.external_title)}]({reference.url})"
            )
    return "；".join(rendered)


def _escape_markdown(value: str) -> str:
    escaped_html = html.escape(value, quote=False)
    return _MARKDOWN_SPECIAL.sub(r"\\\1", escaped_html)


def _validate_https_url(value: str) -> None:
    if _CONTROL.search(value) or any(character.isspace() for character in value):
        raise ValueError("external citation must use a valid HTTPS URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(character in value for character in "<>[]\\")
    ):
        raise ValueError("external citation must use a valid HTTPS URL")
