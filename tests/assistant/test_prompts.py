from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from pydantic import ValidationError

from paper_reader.agents.contracts import ReasoningEffort, ServiceTier
from paper_reader.assistant.contracts import (
    AgentSelection,
    AssistantMode,
    AssistantTaskCommand,
    ContextScope,
    ContextSelection,
    NetworkPolicy,
)
from paper_reader.assistant.prompts import compose_assistant_prompt
from paper_reader.assistant.schemas import (
    AssistantReply,
    AssistantReplyEvidenceError,
    assistant_reply_json_schema,
    validate_assistant_reply,
)
from paper_reader.discovery.models import DocumentVersionRecord

AssistantTestContext = Any


def _ask_command(message: str = "What evidence supports the main claim?") -> AssistantTaskCommand:
    return AssistantTaskCommand(
        mode=AssistantMode.ASK,
        message=message,
        context=ContextSelection(
            document_version_id="document-1",
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


def _valid_reply(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "answer": "作者在所选页面介绍了一种稳定方法。",
        "citations": [{"evidence_id": "page:1:native", "page_number": 1}],
        "uncertainties": [],
        "follow_up_questions": [],
    }
    payload.update(overrides)
    return payload


def test_assistant_reply_schema_is_closed_and_bounded() -> None:
    schema = assistant_reply_json_schema()

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "answer",
        "citations",
        "uncertainties",
        "follow_up_questions",
    }
    assert schema["properties"]["answer"]["maxLength"] == 200_000
    assert schema["properties"]["uncertainties"]["maxItems"] == 50
    assert schema["properties"]["follow_up_questions"]["maxItems"] == 8

    with pytest.raises(ValidationError, match="extra_forbidden"):
        AssistantReply.model_validate(_valid_reply(debug="not allowed"))
    with pytest.raises(ValidationError):
        AssistantReply.model_validate(_valid_reply(answer="   "))
    with pytest.raises(ValidationError):
        AssistantReply.model_validate(
            _valid_reply(uncertainties=[f"uncertainty-{index}" for index in range(51)])
        )


def test_reply_accepts_only_citations_in_the_frozen_snapshot(
    assistant_context: AssistantTestContext,
) -> None:
    reply = validate_assistant_reply(
        _valid_reply(),
        assistant_context.snapshot,
    )

    assert reply.citations[0].evidence_id == "page:1:native"

    with pytest.raises(ValueError, match="citation.*snapshot"):
        validate_assistant_reply(
            _valid_reply(citations=[{"evidence_id": "page:99:native", "page_number": 99}]),
            assistant_context.snapshot,
        )
    with pytest.raises(ValueError, match="page number"):
        validate_assistant_reply(
            _valid_reply(citations=[{"evidence_id": "page:1:native", "page_number": 2}]),
            assistant_context.snapshot,
        )


def test_reply_rejects_external_or_url_citations(
    assistant_context: AssistantTestContext,
) -> None:
    external = {
        "evidence_id": "external:malicious",
        "kind": "external",
        "url": "https://example.test/injected",
    }

    with pytest.raises(ValidationError):
        validate_assistant_reply(
            _valid_reply(citations=[external]),
            assistant_context.snapshot,
        )


def test_annotation_and_abstract_citations_use_only_the_selected_manifest(
    assistant_context: AssistantTestContext,
) -> None:
    annotation_snapshot = assistant_context.builder.build(
        "canonical-work",
        ContextSelection(
            document_version_id="document-1",
            scope=ContextScope.ANNOTATIONS,
            annotation_ids=("annotation-1",),
        ),
    )
    annotation = annotation_snapshot.evidence["annotations"][0]
    reply = validate_assistant_reply(
        _valid_reply(
            citations=[
                {
                    "evidence_id": annotation["evidence_id"],
                    "kind": "annotation",
                    "page_number": 4,
                }
            ]
        ),
        annotation_snapshot,
    )
    assert reply.citations[0].kind == "annotation"

    with assistant_context.sessions() as session:
        document = session.get(DocumentVersionRecord, "document-1")
        assert document is not None
        document.access_status = "restricted"
        session.commit()
    abstract_snapshot = assistant_context.builder.build(
        "canonical-work",
        ContextSelection(
            document_version_id="document-1",
            scope=ContextScope.CURRENT_PAGE,
            page_number=1,
        ),
    )
    abstract_reply = validate_assistant_reply(
        _valid_reply(citations=[{"evidence_id": "abstract:work", "kind": "abstract"}]),
        abstract_snapshot,
    )
    assert abstract_reply.citations[0].evidence_id == "abstract:work"


def test_reply_rejects_a_tampered_persisted_snapshot(
    assistant_context: AssistantTestContext,
) -> None:
    task = assistant_context.repository.create_task(
        "canonical-work",
        _ask_command(),
        assistant_context.snapshot,
        idempotency_key="prompt-tamper-fixture",
    )
    snapshot = assistant_context.repository.snapshot(task.context_snapshot_id)
    evidence = dict(snapshot.evidence)
    evidence["warnings"] = ["mutated-after-freeze"]

    with pytest.raises(AssistantReplyEvidenceError, match="identity"):
        validate_assistant_reply(_valid_reply(), replace(snapshot, evidence=evidence))


def test_prompt_declares_mode_language_policy_and_strict_schema(
    assistant_context: AssistantTestContext,
) -> None:
    command = _ask_command(message="请解释作者的主要主张。")
    prompt = compose_assistant_prompt(command, assistant_context.snapshot, history=())

    assert '"mode":"ask"' in prompt
    assert '"output_language":"zh-CN"' in prompt
    assert "Respond in Simplified Chinese" in prompt
    assert "Use only the supplied evidence" in prompt
    assert "Distinguish authors' claims from Assistant interpretation" in prompt
    assert "Cite every material claim" in prompt
    assert "State uncertainty and missing evidence explicitly" in prompt
    assert "Never modify scores, rankings, feedback, or files" in prompt
    assert '"additionalProperties":false' in prompt
    assert prompt.count("<UNTRUSTED_USER_MESSAGE_BEGIN>") == 1
    assert "请解释作者的主要主张。" in prompt


def test_persisted_task_reads_its_committed_user_message_from_history(
    assistant_context: AssistantTestContext,
) -> None:
    command = _ask_command(message="Explain the bounded evidence.")
    task = assistant_context.repository.create_task(
        "canonical-work",
        command,
        assistant_context.snapshot,
        idempotency_key="prompt-persisted-task",
    )
    snapshot = assistant_context.repository.snapshot(task.context_snapshot_id)
    history = assistant_context.repository.messages_for_work("canonical-work")

    prompt = compose_assistant_prompt(task, snapshot, history)

    assert prompt.count("Explain the bounded evidence.") == 1
    assert '"mode":"ask"' in prompt
