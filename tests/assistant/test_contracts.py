from __future__ import annotations

import pytest
from pydantic import ValidationError

from paper_reader.agents.contracts import (
    AgentTaskKind,
    ReasoningEffort,
    ServiceTier,
)
from paper_reader.assistant.contracts import (
    AgentSelection,
    AssistantMode,
    AssistantTaskCommand,
    AssistantTaskStatus,
    ContextScope,
    ContextSelection,
    NetworkPolicy,
    SelectionRect,
    TextSelectionContext,
)


def _agent() -> AgentSelection:
    return AgentSelection(
        backend_id="codex_cli",
        model_id=None,
        reasoning_effort=ReasoningEffort.MEDIUM,
        service_tier=ServiceTier.FLEX,
    )


def _page_context() -> ContextSelection:
    return ContextSelection(
        document_version_id="document-1",
        scope=ContextScope.CURRENT_PAGE,
        page_number=2,
    )


def _selection() -> TextSelectionContext:
    return TextSelectionContext(
        page_number=2,
        page_width=420,
        page_height=595,
        quote_text="grounded evidence",
        context_before="before",
        context_after="after",
        rects=(SelectionRect(x0=24, y0=32, x1=180, y1=52),),
    )


def test_assistant_enums_are_closed_and_agent_task_kinds_are_explicit() -> None:
    assert tuple(mode.value for mode in AssistantMode) == (
        "ask",
        "explain",
        "translate",
        "related",
    )
    assert tuple(scope.value for scope in ContextScope) == (
        "selection",
        "current_page",
        "current_section",
        "full_document",
        "annotations",
    )
    assert tuple(policy.value for policy in NetworkPolicy) == (
        "local_only",
        "external_research",
    )
    assert tuple(status.value for status in AssistantTaskStatus) == (
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    )
    assert AgentTaskKind.ASSISTANT_ASK.value == "assistant_ask"
    assert AgentTaskKind.ASSISTANT_EXPLAIN.value == "assistant_explain"


@pytest.mark.parametrize("mode", [AssistantMode.ASK, AssistantMode.EXPLAIN])
def test_ask_and_explain_reject_external_research(mode: AssistantMode) -> None:
    with pytest.raises(ValidationError, match="local_only"):
        AssistantTaskCommand(
            mode=mode,
            message="Explain the evidence.",
            context=_page_context(),
            agent=_agent(),
            network_policy=NetworkPolicy.EXTERNAL_RESEARCH,
            output_language="zh-CN",
        )


def test_selection_scope_requires_bounded_quote_and_rectangles() -> None:
    with pytest.raises(ValidationError, match="selection"):
        ContextSelection(
            document_version_id="document-1",
            scope=ContextScope.SELECTION,
            selection=None,
        )

    with pytest.raises(ValidationError, match="page dimensions"):
        TextSelectionContext(
            page_number=1,
            page_width=100,
            page_height=100,
            quote_text="outside",
            rects=(SelectionRect(x0=10, y0=10, x1=101, y1=30),),
        )

    with pytest.raises(ValidationError):
        TextSelectionContext(
            page_number=1,
            page_width=100,
            page_height=100,
            quote_text=" ",
            rects=(SelectionRect(x0=10, y0=10, x1=20, y1=30),),
        )


@pytest.mark.parametrize(
    ("scope", "page_number", "selection", "annotation_ids"),
    [
        (ContextScope.CURRENT_PAGE, None, None, ()),
        (ContextScope.CURRENT_SECTION, None, None, ()),
        (ContextScope.FULL_DOCUMENT, 1, None, ()),
        (ContextScope.ANNOTATIONS, None, None, ()),
        (ContextScope.CURRENT_PAGE, 1, _selection(), ()),
        (ContextScope.CURRENT_PAGE, 1, None, ("annotation-1",)),
    ],
)
def test_context_scope_rejects_ambiguous_fields(
    scope: ContextScope,
    page_number: int | None,
    selection: TextSelectionContext | None,
    annotation_ids: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError, match="scope"):
        ContextSelection(
            document_version_id="document-1",
            scope=scope,
            page_number=page_number,
            selection=selection,
            annotation_ids=annotation_ids,
        )


def test_valid_context_scopes_are_canonical_and_closed() -> None:
    selected = ContextSelection(
        document_version_id="document-1",
        scope=ContextScope.SELECTION,
        selection=_selection(),
    )
    page = _page_context()
    section = ContextSelection(
        document_version_id="document-1",
        scope=ContextScope.CURRENT_SECTION,
        page_number=2,
    )
    full = ContextSelection(
        document_version_id="document-1",
        scope=ContextScope.FULL_DOCUMENT,
    )
    annotations = ContextSelection(
        document_version_id="document-1",
        scope=ContextScope.ANNOTATIONS,
        annotation_ids=("annotation-2", "annotation-1"),
    )

    assert selected.selection == _selection()
    assert page.page_number == section.page_number == 2
    assert full.page_number is None
    assert annotations.annotation_ids == ("annotation-1", "annotation-2")

    with pytest.raises(ValidationError, match="duplicate"):
        ContextSelection(
            document_version_id="document-1",
            scope=ContextScope.ANNOTATIONS,
            annotation_ids=("annotation-1", "annotation-1"),
        )
    with pytest.raises(ValidationError):
        ContextSelection.model_validate(
            {
                "document_version_id": "document-1",
                "scope": "current_page",
                "page_number": 1,
                "unknown": True,
            }
        )


def test_command_trims_message_and_rejects_blank_or_control_identifiers() -> None:
    command = AssistantTaskCommand(
        mode=AssistantMode.ASK,
        message="  What does this ablation prove?  ",
        context=_page_context(),
        agent=_agent(),
        network_policy=NetworkPolicy.LOCAL_ONLY,
        output_language="zh-CN",
    )
    assert command.message == "What does this ablation prove?"

    with pytest.raises(ValidationError):
        AssistantTaskCommand(
            mode=AssistantMode.ASK,
            message="   ",
            context=_page_context(),
            agent=_agent(),
        )
    with pytest.raises(ValidationError, match="control"):
        AgentSelection(
            backend_id="codex_cli\n",
            reasoning_effort=ReasoningEffort.MEDIUM,
            service_tier=ServiceTier.FLEX,
        )
