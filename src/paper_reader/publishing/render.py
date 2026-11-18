"""Deterministic, script-free rendering for a frozen public payload."""

from __future__ import annotations

import hashlib
import html
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from paper_reader.publishing.models import (
    PublicAnalysisReport,
    PublicationPayload,
    PublicEvidenceReference,
    PublicReportStatement,
    PublicWork,
    PublishedFile,
)
from paper_reader.publishing.privacy import canonical_json_bytes

_SECTIONS = (
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

Language = Literal["zh-CN", "en"]
_ENGLISH = {
    "精选快照": "Selected papers", "目录": "Contents", "客观质量": "Quality",
    "覆盖": "Coverage", "置信": "Confidence", "规则": "Rule", "摘要": "Abstract",
    "证据": "Evidence", "概览": "Overview", "研究问题": "Research question",
    "作者主张": "Authors’ claims", "核心贡献": "Contributions", "方法": "Method",
    "关键假设": "Key assumptions", "数据与实验": "Data and experiments",
    "主要发现": "Findings", "局限与风险": "Limitations and risks",
    "可复现性": "Reproducibility", "与研究意图的关系": "Relevance to the research question",
    "Agent 判断": "Agent assessment",
}


def _label(text: str, language: Language) -> str:
    return _ENGLISH[text] if language == "en" else text


class RenderedPublication(BaseModel):
    model_config = ConfigDict(frozen=True)

    files: tuple[PublishedFile, ...]


def render_publication(
    payload: PublicationPayload, *, language: Language = "zh-CN"
) -> RenderedPublication:
    if not isinstance(payload, PublicationPayload):
        raise TypeError("payload must be PublicationPayload")
    payload_value = payload.model_dump(mode="json")
    data = canonical_json_bytes(payload_value) + b"\n"
    embedded = _safe_embedded_json(payload_value)
    index = _render_html(payload, embedded, language).encode("utf-8")
    public_files = (
        _file(0, "index.html", "text/html; charset=utf-8", index),
        _file(1, "data.json", "application/json; charset=utf-8", data),
    )
    manifest_value = {
        "format_version": "paper-reader-published-v1",
        "payload_sha256": hashlib.sha256(data).hexdigest(),
        "files": [
            {
                "relative_path": item.relative_path,
                "media_type": item.media_type,
                "byte_size": item.byte_size,
                "sha256": item.sha256,
            }
            for item in public_files
        ],
    }
    manifest = canonical_json_bytes(manifest_value) + b"\n"
    return RenderedPublication(
        files=(
            *public_files,
            _file(
                2,
                "manifest.json",
                "application/json; charset=utf-8",
                manifest,
            ),
        )
    )


def _file(position: int, path: str, media_type: str, content: bytes) -> PublishedFile:
    return PublishedFile(
        position=position,
        relative_path=path,
        media_type=media_type,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        content=content,
    )


def _safe_embedded_json(value: object) -> str:
    rendered = canonical_json_bytes(value).decode("utf-8")
    return (
        rendered.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _render_html(payload: PublicationPayload, embedded: str, language: Language) -> str:
    work_markup = "\n".join(
        _render_work(index, work, language) for index, work in enumerate(payload.works)
    )
    contents = "".join(
        f'<li><a href="#paper-{index + 1}">{html.escape(work.title)}</a></li>'
        for index, work in enumerate(payload.works)
    )
    title = html.escape(payload.title)
    intro = html.escape(payload.public_intro)
    csp = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
        "script-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    styles = """
:root{color-scheme:light dark;--paper:#faf8f2;--ink:#192b24;--secondary:#535e55;
--line:#c6cbbf;--accent:#24543c}
@media(prefers-color-scheme:dark){:root{--paper:#17201c;--ink:#f1efe7;
--secondary:#bcc5bb;--line:#46544a;--accent:#aad3b7}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:1rem/1.65 system-ui,sans-serif}
main{max-width:72rem;margin:auto;padding:clamp(1.25rem,5vw,4rem)}
header{border-block:4px double var(--accent);padding:1.75rem 0}
h1,h2,h3{font-family:Georgia,"Noto Serif SC",serif;line-height:1.18;text-wrap:balance}
h1{font-size:clamp(2.25rem,6vw,4.5rem);margin:.3em 0}
h2{font-size:clamp(1.6rem,3.6vw,2.75rem);margin:.3em 0}
h3{font-size:1.5rem}h4{margin-bottom:.4rem}ul,ol{padding-inline-start:1.5rem}
.intro{max-width:65ch;white-space:pre-wrap}
.collection{display:grid;grid-template-columns:minmax(10rem,1fr) minmax(0,3fr);
gap:clamp(1.5rem,5vw,4rem);align-items:start}
nav{margin-block:2.5rem;font-size:.9rem}nav ol{margin-top:.75rem}nav li{padding:.25rem}
article{padding-block:2.5rem;border-bottom:1px solid var(--line)}
article>p,.report{max-width:68ch}article,nav,h1{overflow-wrap:anywhere}
.meta,.score{color:var(--secondary);font-size:.9rem}.report{border-top:1px solid var(--line)}
a,.doi{color:var(--accent)}a:focus-visible{outline:2px solid;outline-offset:3px}
@media(max-width:46rem){.collection{display:block}nav{margin-block:1.5rem}article{padding:1.5rem 0}}
@media print{nav{display:none}.collection{display:block}body{background:white;color:black}}
"""
    return f"""<!doctype html>
<html lang="{language}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<title>{title}</title>
<style>{styles}</style>
</head>
<body>
<main>
<header>
<p class="meta">FolioSkiff · {_label("精选快照", language)}</p><h1>{title}</h1>
<p class="intro">{intro}</p>
</header>
<div class="collection">
<nav aria-label="{_label("目录", language)}"><strong>{_label("目录", language)}</strong>
<ol>{contents}</ol></nav>
<div>{work_markup}</div>
</div>
</main>
<script type="application/json" id="paper-reader-data">{embedded}</script>
</body>
</html>
"""


def _render_work(position: int, work: PublicWork, language: Language) -> str:
    authors = (", " if language == "en" else "，").join(html.escape(a) for a in work.authors)
    abstract = "" if work.abstract is None else f"<p>{html.escape(work.abstract)}</p>"
    doi = "" if work.doi is None else f'<p class="doi">DOI · {html.escape(work.doi)}</p>'
    published = "" if work.published_at is None else f" · {html.escape(work.published_at)}"
    score = ""
    if work.quality_score is not None:
        value = work.quality_score
        score = (
            f'<p class="score">{_label("客观质量", language)} {value.score:g}/100 · '
            f'{_label("覆盖", language)} {value.coverage:g}% · '
            f'{_label("置信", language)} {value.confidence:g}% · '
            f'{_label("规则", language)} {html.escape(value.rule_version)}</p>'
        )
    report = "" if work.report is None else _render_report(work.report, language)
    return (
        f'<article id="paper-{position + 1}" data-position="{position}">'
        f'<p class="meta">{position + 1:02d}{published}</p>'
        f"<h2>{html.escape(work.title)}</h2><p class=\"meta\">{authors}</p>{doi}{abstract}"
        f"{score}{report}</article>"
    )


def _render_report(report: PublicAnalysisReport, language: Language) -> str:
    values = [f'<section class="report"><h3>{html.escape(report.content.title)}</h3>']
    for heading, field_name in _SECTIONS:
        raw = getattr(report.content, field_name)
        statements: Iterable[PublicReportStatement] = (
            (raw,) if isinstance(raw, PublicReportStatement) else raw
        )
        values.extend((f"<h4>{_label(heading, language)}</h4>", "<ul>"))
        values.extend(
            f"<li>{html.escape(item.text)}{_render_references(item.references, language)}</li>"
            for item in statements
        )
        values.append("</ul>")
    values.append("</section>")
    return "".join(values)


def _render_references(references: tuple[PublicEvidenceReference, ...], language: Language) -> str:
    values: list[str] = []
    for reference in references:
        if reference.kind == "abstract":
            values.append(_label("摘要", language))
        elif reference.kind == "external":
            assert reference.url is not None and reference.external_title is not None
            values.append(
                f'<a href="{html.escape(reference.url, quote=True)}" rel="noreferrer">'
                f"{html.escape(reference.external_title)}</a>"
            )
        else:
            number = reference.page_number
            page = f"p. {number}" if language == "en" else f"第 {number} 页"
            if reference.locator:
                page = f"{html.escape(reference.locator)} ({page})"
            values.append(page)
    if not values:
        return ""
    return f'<small class="meta"> · {_label("证据", language)}: {"; ".join(values)}</small>'
