from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import func, select

from paper_reader.assistant.models import AssistantTaskRecord
from paper_reader.discovery.models import DocumentVersionRecord
from paper_reader.jobs.models import utc_now
from paper_reader.translations.glossary import GlossaryTerm, GlossaryTermUpdate
from paper_reader.translations.models import (
    TranslationArtifactRecord,
    TranslationSegmentRecord,
)
from paper_reader.translations.repository import (
    TranslationConflictError,
    TranslationIdentityError,
    TranslationStateError,
)
from tests.translations.conftest import TranslationRepositoryContext


def test_create_persists_artifact_segments_and_terms_in_one_transaction(
    translation_repository_context: TranslationRepositoryContext,
) -> None:
    context = translation_repository_context
    before_document: tuple[str, str | None]
    with context.sessions() as session:
        document = session.get(DocumentVersionRecord, "document-1")
        assert document is not None
        before_document = (document.content_sha256, document.relative_path)

    artifact = context.repository.create(
        context.draft,
        context.segments,
        terms=(GlossaryTerm(source_term="agent"),),
    )

    assert artifact.version == 1
    assert artifact.status.value == "draft"
    stored_segments = context.repository.segments(artifact.id)
    assert [item.segment_index for item in stored_segments] == [1, 2]
    assert stored_segments[0].protected_map
    assert "$x_i$" not in stored_segments[0].protected_text
    assert "⟦PR:" in stored_segments[0].protected_text
    assert context.repository.terms(artifact.id)[0].source_term == "agent"
    with context.sessions() as session:
        document = session.get(DocumentVersionRecord, "document-1")
        assert document is not None
        assert (document.content_sha256, document.relative_path) == before_document


def test_create_rolls_back_every_row_when_terms_conflict(
    translation_repository_context: TranslationRepositoryContext,
) -> None:
    context = translation_repository_context

    with pytest.raises(TranslationConflictError, match="duplicate"):
        context.repository.create(
            context.draft,
            context.segments,
            terms=(
                GlossaryTerm(source_term="Agent"),
                GlossaryTerm(source_term="ＡＧＥＮＴ"),
            ),
        )

    with context.sessions() as session:
        assert session.scalar(select(func.count()).select_from(TranslationArtifactRecord)) == 0
        assert session.scalar(select(func.count()).select_from(TranslationSegmentRecord)) == 0


def test_locked_user_term_survives_generated_term_regeneration(
    translation_repository_context: TranslationRepositoryContext,
) -> None:
    context = translation_repository_context
    artifact = context.repository.create(context.draft, context.segments)
    context.repository.set_term(
        artifact.id,
        GlossaryTermUpdate(source_term="agent", target_term="智能体", locked=True),
    )

    merged = context.repository.merge_generated_terms(
        artifact.id,
        (
            GlossaryTerm(
                source_term="agent",
                target_term="代理",
                origin="agent",
            ),
            GlossaryTerm(source_term="policy"),
        ),
    )

    agent = next(item for item in merged if item.normalized_source_term == "agent")
    assert agent.target_term == "智能体"
    assert agent.origin == "user"
    assert agent.locked is True


def test_term_change_marks_only_affected_succeeded_segments_stale(
    translation_repository_context: TranslationRepositoryContext,
) -> None:
    context = translation_repository_context
    artifact = context.repository.create(context.draft, context.segments)
    with context.sessions.begin() as session:
        records = tuple(
            session.scalars(
                select(TranslationSegmentRecord)
                .where(TranslationSegmentRecord.artifact_id == artifact.id)
                .order_by(TranslationSegmentRecord.segment_index)
            )
        )
        for record in records:
            record.translated_text = f"translated-{record.segment_index}"
            record.status = "succeeded"

    context.repository.set_term(
        artifact.id,
        GlossaryTermUpdate(source_term="agent", target_term="智能体", locked=True),
    )

    stored = context.repository.segments(artifact.id)
    assert [item.status for item in stored] == ["stale", "succeeded"]


def test_idempotent_create_and_resume_preserve_succeeded_segments(
    translation_repository_context: TranslationRepositoryContext,
) -> None:
    context = translation_repository_context
    artifact = context.repository.create(context.draft, context.segments)
    with context.sessions.begin() as session:
        first = session.scalar(
            select(TranslationSegmentRecord).where(
                TranslationSegmentRecord.artifact_id == artifact.id,
                TranslationSegmentRecord.segment_index == 1,
            )
        )
        assert first is not None
        first.translated_text = "智能体遵循策略。"
        first.status = "succeeded"

    replayed = context.repository.create(context.draft, context.segments)
    pending = context.repository.pending_segments(artifact.id)

    assert replayed.id == artifact.id
    assert [item.segment_index for item in pending] == [2]
    assert context.repository.segments(artifact.id)[0].translated_text == "智能体遵循策略。"


def test_repository_rejects_tampered_protected_span_storage(
    translation_repository_context: TranslationRepositoryContext,
) -> None:
    context = translation_repository_context
    artifact = context.repository.create(context.draft, context.segments)
    with context.sessions.begin() as session:
        record = session.scalar(
            select(TranslationSegmentRecord).where(
                TranslationSegmentRecord.artifact_id == artifact.id,
                TranslationSegmentRecord.segment_index == 1,
            )
        )
        assert record is not None
        tampered = {key: dict(value) for key, value in record.protected_map.items()}
        token = next(iter(tampered))
        tampered[token]["literal"] = "$attacker$"
        record.protected_map = tampered

    with pytest.raises(TranslationConflictError, match="literal hash"):
        context.repository.segments(artifact.id)


def test_completed_artifact_rejects_term_rewrites(
    translation_repository_context: TranslationRepositoryContext,
) -> None:
    context = translation_repository_context
    artifact = context.repository.create(context.draft, context.segments)
    with context.sessions.begin() as session:
        record = session.get(TranslationArtifactRecord, artifact.id)
        assert record is not None
        record.status = "succeeded"
        record.completed_at = utc_now()

    with pytest.raises(TranslationStateError, match="completed"):
        context.repository.set_term(
            artifact.id,
            GlossaryTermUpdate(source_term="agent", target_term="智能体"),
        )


def test_completed_artifact_allows_idempotent_term_replay_by_id(
    translation_repository_context: TranslationRepositoryContext,
) -> None:
    context = translation_repository_context
    artifact = context.repository.create(
        context.draft,
        context.segments,
        terms=(
            GlossaryTerm(
                source_term="agent",
                target_term="agent-zh",
                origin="user",
                locked=True,
            ),
        ),
    )
    term = context.repository.term_records(artifact.id)[0]
    with context.sessions.begin() as session:
        record = session.get(TranslationArtifactRecord, artifact.id)
        assert record is not None
        record.status = "succeeded"
        record.completed_at = utc_now()

    replayed = context.repository.set_term_by_id(
        artifact.id,
        term.id,
        target_term="agent-zh",
        locked=True,
    )

    assert replayed == term
    with pytest.raises(TranslationStateError, match="completed"):
        context.repository.set_term_by_id(
            artifact.id,
            term.id,
            target_term="changed",
            locked=True,
        )


def test_repository_enforces_the_paper_level_term_limit(
    translation_repository_context: TranslationRepositoryContext,
) -> None:
    context = translation_repository_context
    artifact = context.repository.create(
        context.draft,
        context.segments,
        terms=tuple(GlossaryTerm(source_term=f"term-{index:03d}") for index in range(500)),
    )

    with pytest.raises(TranslationConflictError, match="500"):
        context.repository.set_term(
            artifact.id,
            GlossaryTermUpdate(source_term="overflow-term", target_term="溢出"),
        )


def test_repository_rejects_nonlocal_or_mismatched_task_identity(
    translation_repository_context: TranslationRepositoryContext,
) -> None:
    context = translation_repository_context
    with context.sessions.begin() as session:
        task = session.get(AssistantTaskRecord, "task-1")
        assert task is not None
        task.network_policy = "external_research"

    with pytest.raises(TranslationIdentityError, match="local_only"):
        context.repository.create(context.draft, context.segments)

    with context.sessions.begin() as session:
        task = session.get(AssistantTaskRecord, "task-1")
        assert task is not None
        task.network_policy = "local_only"

    with pytest.raises(TranslationIdentityError, match="document"):
        context.repository.create(
            replace(context.draft, document_content_sha256="a" * 64),
            context.segments,
        )
