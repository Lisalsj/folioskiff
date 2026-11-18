"""Durable, evidence-bound paper Assistant domain."""

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

__all__ = [
    "AgentSelection",
    "AssistantMode",
    "AssistantTaskCommand",
    "AssistantTaskStatus",
    "ContextScope",
    "ContextSelection",
    "NetworkPolicy",
    "SelectionRect",
    "TextSelectionContext",
]
