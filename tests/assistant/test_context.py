from __future__ import annotations

from typing import Any

import pytest

from paper_reader.agents.evidence import EvidenceLevel, EvidenceUnavailableError
from paper_reader.assistant.contracts import (
    ContextScope,
    ContextSelection,
    SelectionRect,
    TextSelectionContext,
)
from paper_reader.discovery.models import DocumentVersionRecord

AssistantTestContext = Any


@pytest.mark.parametrize(
    ("scope", "page_number", "expected_pages"),
    [
        (ContextScope.CURRENT_PAGE, 2, (2,)),
        (ContextScope.CURRENT_SECTION, 2, (2, 3, 4)),
        (ContextScope.FULL_DOCUMENT, None, (1, 2, 3, 4, 5)),
    ],
)
def test_context_scope_freezes_exact_pages(
    assistant_context: AssistantTestContext,
    scope: ContextScope,
    page_number: int | None,
    expected_pages: tuple[int, ...],
) -> None:
    draft = assistant_context.builder.build(
        "canonical-work",
        ContextSelection(
            document_version_id="document-1",
            scope=scope,
            page_number=page_number,
        ),
    )

    assert draft.page_numbers == expected_pages
    assert tuple(page["page_number"] for page in draft.evidence["pages"]) == expected_pages
    assert draft.document_content_sha256 == assistant_context.snapshot.document_content_sha256
    assert draft.section_fallback is False


def test_selection_scope_verifies_quote_and_preserves_pdf_coordinates(
    assistant_context: AssistantTestContext,
) -> None:
    selection = TextSelectionContext(
        page_number=4,
        page_width=420,
        page_height=595,
        quote_text="Ablation evidence",
        context_before=None,
        context_after=" improves robustness.",
        rects=(SelectionRect(x0=20, y0=20, x1=150, y1=35),),
    )
    draft = assistant_context.builder.build(
        "canonical-work",
        ContextSelection(
            document_version_id="document-1",
            scope=ContextScope.SELECTION,
            selection=selection,
        ),
    )

    assert draft.page_numbers == (4,)
    assert draft.selection == selection
    assert draft.evidence["selection_excerpt"]["verified"] is True
    assert draft.evidence["selection_excerpt"]["rects"][0] == {
        "x0": 20.0,
        "y0": 20.0,
        "x1": 150.0,
        "y1": 35.0,
    }
    assert "selection_unverified" not in draft.warnings


def test_selection_text_not_found_is_visible_instead_of_silently_trusted(
    assistant_context: AssistantTestContext,
) -> None:
    selection = TextSelectionContext(
        page_number=4,
        page_width=420,
        page_height=595,
        quote_text="Text not present on the selected page",
        rects=(SelectionRect(x0=20, y0=20, x1=180, y1=35),),
    )

    draft = assistant_context.builder.build(
        "canonical-work",
        ContextSelection(
            document_version_id="document-1",
            scope=ContextScope.SELECTION,
            selection=selection,
        ),
    )

    assert draft.evidence["selection_excerpt"]["verified"] is False
    assert "selection_unverified" in draft.warnings


def test_annotation_scope_contains_only_requested_hash_bound_annotations(
    assistant_context: AssistantTestContext,
) -> None:
    draft = assistant_context.builder.build(
        "canonical-work",
        ContextSelection(
            document_version_id="document-1",
            scope=ContextScope.ANNOTATIONS,
            annotation_ids=("annotation-1",),
        ),
    )

    assert draft.page_numbers == ()
    assert draft.annotation_ids == ("annotation-1",)
    assert draft.evidence["pages"] == []
    assert [item["annotation_id"] for item in draft.evidence["annotations"]] == [
        "annotation-1"
    ]
    assert draft.evidence["evidence_level"] == EvidenceLevel.FULL_TEXT.value

    with pytest.raises(EvidenceUnavailableError, match="annotation"):
        assistant_context.builder.build(
            "canonical-work",
            ContextSelection(
                document_version_id="document-1",
                scope=ContextScope.ANNOTATIONS,
                annotation_ids=("missing-annotation",),
            ),
        )


def test_abstract_fallback_is_labeled_and_has_no_page_citations(
    assistant_context: AssistantTestContext,
) -> None:
    with assistant_context.sessions() as session:
        document = session.get(DocumentVersionRecord, "document-1")
        assert document is not None
        document.access_status = "restricted"
        session.commit()

    draft = assistant_context.builder.build(
        "canonical-work",
        ContextSelection(
            document_version_id="document-1",
            scope=ContextScope.CURRENT_PAGE,
            page_number=2,
        ),
    )

    assert draft.evidence["evidence_level"] == EvidenceLevel.ABSTRACT_ONLY.value
    assert draft.evidence["pages"] == []
    assert draft.evidence["selection_excerpt"] is None
    assert "abstract_fallback_no_page_citations" in draft.warnings


def test_stored_snapshot_reloads_without_selecting_a_newer_document(
    assistant_context: AssistantTestContext,
) -> None:
    draft = assistant_context.builder.build(
        "canonical-work",
        ContextSelection(
            document_version_id="document-1",
            scope=ContextScope.CURRENT_PAGE,
            page_number=1,
        ),
    )
    task = assistant_context.repository.create_task(
        "canonical-work",
        _ask_for_context(draft.document_version_id),
        draft,
        idempotency_key="stored-context",
    )
    frozen = assistant_context.repository.snapshot(task.context_snapshot_id)

    with assistant_context.sessions() as session:
        session.add(
            DocumentVersionRecord(
                id="document-newer",
                work_id="canonical-work",
                source_record_id=None,
                content_sha256="f" * 64,
                access_status="unknown",
                media_type="application/pdf",
                completeness=100,
                extraction_status="pending",
                relative_path=None,
                byte_size=0,
                acquisition_kind="source",
            )
        )
        session.commit()

    repeated = assistant_context.builder.stored(task.context_snapshot_id)
    assert repeated == frozen
    assert repeated.document_version_id == "document-1"
    assert repeated.document_content_sha256 == draft.document_content_sha256


def _ask_for_context(document_version_id: str):  # type: ignore[no-untyped-def]
    from paper_reader.agents.contracts import ReasoningEffort, ServiceTier
    from paper_reader.assistant.contracts import (
        AgentSelection,
        AssistantMode,
        AssistantTaskCommand,
        NetworkPolicy,
    )

    return AssistantTaskCommand(
        mode=AssistantMode.ASK,
        message="Explain page one.",
        context=ContextSelection(
            document_version_id=document_version_id,
            scope=ContextScope.CURRENT_PAGE,
            page_number=1,
        ),
        agent=AgentSelection(
            backend_id="codex_cli",
            model_id="fixture-model",
            reasoning_effort=ReasoningEffort.MEDIUM,
            service_tier=ServiceTier.FLEX,
        ),
        network_policy=NetworkPolicy.LOCAL_ONLY,
        output_language="zh-CN",
    )
