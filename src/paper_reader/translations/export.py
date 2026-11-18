"""Safe, reproducible translation exports stored by content hash."""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from paper_reader.translations.contracts import TranslationArtifact, TranslationStatus
from paper_reader.translations.repository import (
    StoredTranslationSegment,
    TranslationExportMetadata,
    TranslationRepository,
    TranslationStateError,
)

_UNSAFE_FILENAME = re.compile(r"[^\w.-]+", re.UNICODE)
_WINDOWS_RESERVED = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


@dataclass(frozen=True, slots=True)
class StoredTranslationFile:
    artifact_id: str
    format: str
    content_sha256: str
    path: Path
    filename: str
    media_type: str
    draft: bool


class TranslationExporter:
    def __init__(self, *, repository: TranslationRepository, storage_root: Path) -> None:
        self.repository = repository
        self.storage_root = storage_root.resolve()

    def markdown(self, artifact_id: str, *, draft: bool = False) -> str:
        artifact, metadata, segments = self._snapshot(artifact_id, draft=draft)
        lines = [
            f"# {_markdown_text(metadata.title)} — 中文译文",
            "",
        ]
        if draft:
            lines.extend(("> **草稿导出：内容可能不完整。**", ""))
        lines.extend(
            (
                f"- Work：`{artifact.work_id}`",
                f"- 翻译版本：`v{artifact.version}`",
                f"- 语言：`{artifact.source_language}` → `{artifact.target_language}`",
                f"- 文档 SHA-256：`{artifact.document_content_sha256}`",
                f"- 证据等级：`{artifact.evidence_level}`",
                f"- Agent 后端：`{_markdown_text(metadata.backend_id)}`",
                f"- 模型：`{_markdown_text(_model_name(metadata))}`",
                f"- 推理强度：`{_markdown_text(metadata.reasoning_effort)}`",
                "",
            )
        )
        for page_number, page_segments in _group_pages(segments):
            page_hash = _page_source_hash(page_segments)
            lines.extend(
                (
                    f"<!-- page:{page_number} source-sha256:{page_hash} -->",
                    f"## 第 {page_number} 页",
                    "",
                )
            )
            for segment in page_segments:
                lines.extend(
                    (
                        (
                            f"<!-- segment:{segment.segment_id} "
                            f"source-sha256:{segment.source_sha256} -->"
                        ),
                        "### 原文",
                        "",
                        _markdown_text(segment.source_text),
                        "",
                        "### 中文译文",
                        "",
                        _markdown_text(segment.translated_text or "[尚未翻译]"),
                        "",
                    )
                )
        terms = self.repository.term_records(artifact.id)
        if terms:
            lines.extend(("## 术语表", "", "| 原文 | 译文 | 锁定 |", "|---|---|---|"))
            for term in terms:
                lines.append(
                    "| "
                    + _markdown_table(term.source_term)
                    + " | "
                    + _markdown_table(term.target_term or "—")
                    + " | "
                    + ("是" if term.locked else "否")
                    + " |"
                )
            lines.append("")
        return "\n".join(lines)

    def html(self, artifact_id: str, *, draft: bool = False, print_view: bool = False) -> str:
        artifact, metadata, segments = self._snapshot(artifact_id, draft=draft)
        title = html.escape(metadata.title)
        draft_banner = (
            '<aside class="draft" role="status">草稿导出：内容可能不完整。</aside>' if draft else ""
        )
        pages: list[str] = []
        for page_number, page_segments in _group_pages(segments):
            page_hash = _page_source_hash(page_segments)
            paragraphs = []
            for segment in page_segments:
                paragraphs.append(
                    "".join(
                        (
                            (
                                f"<!-- segment:{segment.segment_id} "
                                f"source-sha256:{segment.source_sha256} -->"
                            ),
                            '<article class="segment">',
                            "<h3>原文</h3>",
                            f"<p>{_html_text(segment.source_text)}</p>",
                            "<h3>中文译文</h3>",
                            f"<p>{_html_text(segment.translated_text or '[尚未翻译]')}</p>",
                            "</article>",
                        )
                    )
                )
            pages.append(
                "".join(
                    (
                        f"<!-- page:{page_number} source-sha256:{page_hash} -->",
                        f'<section class="page" data-page="{page_number}">',
                        f"<h2>第 {page_number} 页</h2>",
                        *paragraphs,
                        "</section>",
                    )
                )
            )
        glossary = self._html_glossary(artifact.id)
        print_class = " print-view" if print_view else ""
        return "".join(
            (
                '<!doctype html><html lang="zh-CN"><head>',
                '<meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width,initial-scale=1">',
                '<meta http-equiv="Content-Security-Policy" '
                "content=\"default-src 'none'; style-src 'unsafe-inline'; "
                "img-src data:; font-src 'none'; frame-src 'none'\">",
                f"<title>{title} — 中文译文</title>",
                f"<style>{_STYLE}</style>",
                "</head>",
                f'<body class="translation{print_class}"><main>',
                f'<header><p class="eyebrow">paper-reader · 翻译 v{artifact.version}</p>',
                f"<h1>{title}</h1>{draft_banner}",
                '<dl class="metadata">',
                f"<dt>作者</dt><dd>{html.escape(', '.join(metadata.authors) or '未知')}</dd>",
                f"<dt>语言</dt><dd>{artifact.source_language} → {artifact.target_language}</dd>",
                f"<dt>文档 SHA-256</dt><dd><code>{artifact.document_content_sha256}</code></dd>",
                f"<dt>证据等级</dt><dd>{artifact.evidence_level}</dd>",
                f"<dt>Agent</dt><dd>{html.escape(metadata.backend_id)}</dd>",
                f"<dt>模型</dt><dd>{html.escape(_model_name(metadata))}</dd>",
                f"<dt>推理强度</dt><dd>{html.escape(metadata.reasoning_effort)}</dd>",
                "</dl></header>",
                *pages,
                glossary,
                "</main></body></html>",
            )
        )

    def print_html(self, artifact_id: str, *, draft: bool = False) -> str:
        return self.html(artifact_id, draft=draft, print_view=True)

    def store(
        self,
        artifact_id: str,
        *,
        format: str,
        draft: bool = False,
    ) -> StoredTranslationFile:
        if format == "markdown":
            body = self.markdown(artifact_id, draft=draft)
            extension = "md"
            media_type = "text/markdown; charset=utf-8"
        elif format == "html":
            body = self.html(artifact_id, draft=draft)
            extension = "html"
            media_type = "text/html; charset=utf-8"
        else:
            raise ValueError("translation export format must be markdown or html")
        artifact = self.repository.artifact(artifact_id)
        metadata = self.repository.export_metadata(artifact_id)
        content = body.encode("utf-8")
        content_hash = sha256(content).hexdigest()
        relative = Path(content_hash[:2]) / f"{content_hash}.{extension}"
        destination = (self.storage_root / relative).resolve()
        if not destination.is_relative_to(self.storage_root):  # pragma: no cover - hash path
            raise ValueError("translation export path escaped its storage root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(content)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        elif destination.read_bytes() != content:
            raise RuntimeError("translation export content-addressed file is corrupted")
        self.repository.record_export(
            artifact.id,
            format=format,
            content_sha256=content_hash,
            relative_path=relative.as_posix(),
        )
        suffix = "-draft" if draft else ""
        filename = f"{_safe_filename(metadata.title)}-v{artifact.version}{suffix}.{extension}"
        return StoredTranslationFile(
            artifact_id=artifact.id,
            format=format,
            content_sha256=content_hash,
            path=destination,
            filename=filename,
            media_type=media_type,
            draft=draft,
        )

    def _snapshot(
        self,
        artifact_id: str,
        *,
        draft: bool,
    ) -> tuple[
        TranslationArtifact,
        TranslationExportMetadata,
        tuple[StoredTranslationSegment, ...],
    ]:
        artifact = self.repository.artifact(artifact_id)
        if not draft and artifact.status is not TranslationStatus.SUCCEEDED:
            raise TranslationStateError(
                "translation exports require a succeeded artifact or draft=true"
            )
        return (
            artifact,
            self.repository.export_metadata(artifact.id),
            self.repository.segments(artifact.id),
        )

    def _html_glossary(self, artifact_id: str) -> str:
        terms = self.repository.term_records(artifact_id)
        if not terms:
            return ""
        rows = "".join(
            "<tr>"
            f"<td>{html.escape(term.source_term)}</td>"
            f"<td>{html.escape(term.target_term or '—')}</td>"
            f"<td>{'是' if term.locked else '否'}</td>"
            "</tr>"
            for term in terms
        )
        return (
            '<section class="glossary"><h2>术语表</h2>'
            "<table><thead><tr><th>原文</th><th>译文</th><th>锁定</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></section>"
        )


def _group_pages(
    segments: tuple[StoredTranslationSegment, ...],
) -> tuple[tuple[int, tuple[StoredTranslationSegment, ...]], ...]:
    grouped: dict[int, list[StoredTranslationSegment]] = {}
    for segment in segments:
        grouped.setdefault(segment.page_number, []).append(segment)
    return tuple((page, tuple(grouped[page])) for page in sorted(grouped))


def _page_source_hash(segments: tuple[StoredTranslationSegment, ...]) -> str:
    return sha256("".join(item.source_sha256 for item in segments).encode("ascii")).hexdigest()


def _model_name(metadata: TranslationExportMetadata) -> str:
    return metadata.resolved_model_id or metadata.requested_model_id or "default"


def _markdown_text(value: str) -> str:
    return html.escape(value, quote=False)


def _markdown_table(value: str) -> str:
    return _markdown_text(value).replace("|", "\\|").replace("\n", "<br>")


def _html_text(value: str) -> str:
    return html.escape(value).replace("\n", "<br>")


def _safe_filename(value: str) -> str:
    normalized = _UNSAFE_FILENAME.sub("-", value).strip(" .-")[:100]
    reserved_stem = normalized.split(".", 1)[0].casefold()
    if not normalized or reserved_stem in _WINDOWS_RESERVED:
        return "paper-translation"
    return normalized


_STYLE = """
:root{color-scheme:light;--paper:#f7f2e8;--ink:#20231f;--forest:#23483a;--line:#d8d0c2;--wine:#7a3340}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.7 system-ui,sans-serif}
main{max-width:920px;margin:auto;padding:48px 36px}
h1,h2,h3{font-family:Georgia,serif;color:var(--forest);line-height:1.25}
h1{font-size:2.25rem}.eyebrow{letter-spacing:.08em;text-transform:uppercase;color:var(--wine)}
.metadata{display:grid;grid-template-columns:max-content 1fr;gap:6px 18px;padding:18px}
.metadata{border:1px solid var(--line)}.metadata dt{font-weight:700}
.metadata dd{margin:0;overflow-wrap:anywhere}.page{margin-top:48px}
.segment{border-top:1px solid var(--line);padding:20px 0}.segment p{white-space:normal}
.draft{padding:12px 16px;border-left:4px solid var(--wine);background:#fff8e8}
table{width:100%;border-collapse:collapse}
th,td{padding:8px;border-bottom:1px solid var(--line);text-align:left}
@media print{body{background:#fff}main{max-width:none;padding:0}.page{break-before:page}}
@media print{.page:first-of-type{break-before:auto}.draft{border:2px solid #000}}
"""


__all__ = ["StoredTranslationFile", "TranslationExporter"]
