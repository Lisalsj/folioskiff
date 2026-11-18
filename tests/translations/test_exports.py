from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from paper_reader.discovery.models import WorkRecord
from paper_reader.translations.export import TranslationExporter
from paper_reader.translations.models import TranslationExportRecord
from paper_reader.translations.repository import TranslationStateError
from tests.translations.conftest import TranslationRepositoryContext


def _draft_exporter(
    context: TranslationRepositoryContext,
    root: Path,
) -> tuple[TranslationExporter, str]:
    with context.sessions.begin() as session:
        work = session.get(WorkRecord, "work-1")
        assert work is not None
        work.canonical_title = "Agent Translation <Safety> Fixture"
    artifact = context.repository.create(context.draft, context.segments)
    context.repository.start(artifact.id)
    context.repository.save_batch(
        artifact.id,
        {
            segment.segment_id: f"Chinese translation: {segment.source_text}"
            for segment in context.repository.segments(artifact.id)
        },
    )
    return TranslationExporter(
        repository=context.repository,
        storage_root=root,
    ), artifact.id


def test_markdown_export_preserves_page_anchors_and_source_hash(
    translation_repository_context: TranslationRepositoryContext,
    tmp_path: Path,
) -> None:
    exporter, artifact_id = _draft_exporter(
        translation_repository_context,
        tmp_path / "exports",
    )

    body = exporter.markdown(artifact_id, draft=True)

    assert "<!-- page:1 source-sha256:" in body
    assert "## 第 1 页" in body
    assert translation_repository_context.document_hash in body
    assert "### 原文" in body
    assert "### 中文译文" in body


def test_html_export_is_self_contained_escaped_and_content_addressed(
    translation_repository_context: TranslationRepositoryContext,
    tmp_path: Path,
) -> None:
    exporter, artifact_id = _draft_exporter(
        translation_repository_context,
        tmp_path / "exports",
    )

    rendered = exporter.html(artifact_id, draft=True)
    stored = exporter.store(artifact_id, format="html", draft=True)
    repeated = exporter.store(artifact_id, format="html", draft=True)

    assert "default-src 'none'" in rendered
    assert "https://" not in rendered
    assert "<script" not in rendered.lower()
    assert "&lt;" in rendered
    assert stored == repeated
    assert stored.path.is_file()
    assert stored.path.resolve().is_relative_to((tmp_path / "exports").resolve())
    assert stored.filename.endswith("-draft.html")
    with translation_repository_context.sessions() as session:
        records = tuple(
            session.scalars(
                select(TranslationExportRecord).where(
                    TranslationExportRecord.artifact_id == artifact_id
                )
            )
        )
        assert len(records) == 1
        assert records[0].content_sha256 == stored.content_sha256


def test_export_repository_rejects_non_content_addressed_paths(
    translation_repository_context: TranslationRepositoryContext,
) -> None:
    context = translation_repository_context
    artifact = context.repository.create(context.draft, context.segments)

    with pytest.raises(ValueError, match="content hash"):
        context.repository.record_export(
            artifact.id,
            format="markdown",
            content_sha256="a" * 64,
            relative_path="../outside.md",
        )
    with pytest.raises(TranslationStateError, match="succeeded"):
        TranslationExporter(
            repository=context.repository,
            storage_root=context.store.root / "exports",
        ).markdown(artifact.id)
