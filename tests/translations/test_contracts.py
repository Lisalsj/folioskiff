from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from paper_reader.agents.contracts import AgentTaskKind
from paper_reader.assistant.contracts import (
    ContextScope,
    ContextSelection,
    NetworkPolicy,
    SelectionRect,
    TextSelectionContext,
)
from paper_reader.translations.contracts import (
    SUPPORTED_LANGUAGES,
    TranslationArtifact,
    TranslationCommand,
    TranslationScope,
    TranslationStatus,
    TranslationTermInput,
)


def page_context(
    *,
    document_version_id: str = "document-1",
    page_number: int = 2,
) -> ContextSelection:
    return ContextSelection(
        document_version_id=document_version_id,
        scope=ContextScope.CURRENT_PAGE,
        page_number=page_number,
    )


def selection_context() -> ContextSelection:
    return ContextSelection(
        document_version_id="document-1",
        scope=ContextScope.SELECTION,
        selection=TextSelectionContext(
            page_number=3,
            page_width=600,
            page_height=800,
            quote_text="A bounded source selection.",
            rects=(SelectionRect(x0=10, y0=20, x1=220, y1=45),),
        ),
    )


def test_translation_defaults_to_english_simplified_chinese() -> None:
    command = TranslationCommand(
        context=page_context(document_version_id="document-1", page_number=2),
    )

    assert command.source_language == "en"
    assert command.target_language == "zh-CN"
    assert command.network_policy is NetworkPolicy.LOCAL_ONLY
    assert command.scope is TranslationScope.PAGE
    assert command.agent.backend_id == "codex_cli"
    assert AgentTaskKind.ASSISTANT_TRANSLATE == "assistant_translate"


@pytest.mark.parametrize(
    ("context", "expected"),
    (
        (selection_context(), TranslationScope.SELECTION),
        (page_context(), TranslationScope.PAGE),
        (
            ContextSelection(
                document_version_id="document-1",
                scope=ContextScope.CURRENT_SECTION,
                page_number=4,
            ),
            TranslationScope.SECTION,
        ),
        (
            ContextSelection(
                document_version_id="document-1",
                scope=ContextScope.FULL_DOCUMENT,
            ),
            TranslationScope.FULL_DOCUMENT,
        ),
    ),
)
def test_translation_scope_is_derived_unambiguously(
    context: ContextSelection,
    expected: TranslationScope,
) -> None:
    assert TranslationCommand(context=context).scope is expected


def test_translation_rejects_external_research_and_same_language_pair() -> None:
    with pytest.raises(ValidationError, match="local_only"):
        TranslationCommand(
            context=page_context(),
            source_language="en",
            target_language="zh-CN",
            network_policy=NetworkPolicy.EXTERNAL_RESEARCH,
        )
    with pytest.raises(ValidationError, match="different"):
        TranslationCommand(
            context=page_context(),
            source_language="zh-CN",
            target_language="zh-CN",
        )


def test_translation_rejects_unknown_language_and_annotation_scope() -> None:
    assert frozenset({"en", "zh-CN", "zh-TW", "ja", "ko", "de", "fr"}) == (SUPPORTED_LANGUAGES)
    with pytest.raises(ValidationError, match="supported"):
        TranslationCommand(
            context=page_context(),
            source_language="es",
        )
    with pytest.raises(ValidationError, match="scope"):
        TranslationCommand(
            context=ContextSelection(
                document_version_id="document-1",
                scope=ContextScope.ANNOTATIONS,
                annotation_ids=("annotation-1",),
            )
        )


def test_user_terminology_is_bounded_normalized_and_unique() -> None:
    command = TranslationCommand(
        context=page_context(),
        user_terminology=(
            TranslationTermInput(source_term="  Agent  ", target_term="智能体"),
            TranslationTermInput(source_term="Diffusion model", target_term="扩散模型"),
        ),
    )

    assert command.user_terminology[0].source_term == "Agent"
    with pytest.raises(ValidationError, match="duplicate"):
        TranslationCommand(
            context=page_context(),
            user_terminology=(
                TranslationTermInput(source_term="Agent", target_term="智能体"),
                TranslationTermInput(source_term="ＡＧＥＮＴ", target_term="代理"),
            ),
        )


def test_translation_artifact_projection_is_immutable_and_typed() -> None:
    artifact = TranslationArtifact(
        id="artifact-1",
        work_id="work-1",
        version=1,
        document_version_id="document-1",
        document_content_sha256="a" * 64,
        context_snapshot_id="snapshot-1",
        task_id="task-1",
        job_id="job-1",
        source_language="en",
        target_language="zh-CN",
        scope=TranslationScope.PAGE,
        status=TranslationStatus.DRAFT,
        evidence_level="native_text",
        prompt_version="translation-v1",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        updated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert artifact.status is TranslationStatus.DRAFT
    with pytest.raises(ValidationError, match="frozen"):
        artifact.status = TranslationStatus.RUNNING
