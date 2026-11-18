from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select, update

from paper_reader.related_resources.contracts import RelatedSearchIntent
from paper_reader.related_resources.models import RelatedResourceSetRecord
from paper_reader.related_resources.repository import (
    RelatedIntentConflict,
    RelatedIntentStateError,
)

RelatedIntentTestContext = Any


def test_repository_binds_draft_to_immutable_local_context(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    intent = context.service.draft(context.work_id, context.snapshot_id)
    state = context.repository.get(intent.set_id)

    assert state.work_id == context.work_id
    assert state.context_snapshot_id == context.snapshot_id
    assert state.document_content_sha256 == "a" * 64
    assert state.status == "draft"
    assert state.intent == intent
    assert len(state.history) == 1
    assert state.history[0].revision == 1
    assert state.history[0].origin == "local_draft"


def test_confirm_rejects_stale_revision_without_partial_update(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    draft = context.service.draft(context.work_id, context.snapshot_id)
    current = context.repository.revise(
        draft.set_id,
        expected_revision=1,
        intent=draft.model_copy(update={"summary": "A local manual refinement"}),
        origin="manual_edit",
    )

    with pytest.raises(RelatedIntentConflict, match="revision"):
        context.service.confirm(draft.set_id, revision=1, intent=current)

    state = context.repository.get(draft.set_id)
    assert state.intent.revision == 2
    assert state.intent.confirmed is False
    assert state.status == "draft"


def test_regrouping_required_terms_is_recorded_as_a_semantic_move(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    initial = RelatedSearchIntent(
        summary="Diffusion agents for scientific discovery",
        required_groups=(
            ("diffusion model", "扩散模型"),
            ("planning agent", "规划智能体"),
        ),
        optional_terms=("scientific discovery", "科学发现"),
        source_ids=("arxiv",),
    )
    draft = context.repository.create_draft(
        context.work_id,
        context.snapshot_id,
        initial,
    )

    revised = context.repository.revise(
        draft.set_id,
        expected_revision=draft.revision,
        intent=draft.model_copy(
            update={
                "required_groups": (
                    ("diffusion model", "扩散模型", "planning agent", "规划智能体"),
                )
            }
        ),
        origin="manual_edit",
    )

    assert revised.diff.added_terms == ()
    assert revised.diff.removed_terms == ()
    assert revised.diff.moved_terms == (
        "diffusion model",
        "planning agent",
        "扩散模型",
        "规划智能体",
    )


def test_repository_rejects_foreign_snapshot_and_leaves_no_set(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context

    with pytest.raises(RelatedIntentConflict, match="Work"):
        context.repository.create_draft(
            "different-work",
            context.snapshot_id,
            context.service.local_draft(context.work_id, context.snapshot_id),
        )

    with context.sessions() as session:
        count = session.scalar(select(func.count()).select_from(RelatedResourceSetRecord))
    assert count == 0


def test_executed_revision_cannot_be_rewritten_or_reconfirmed(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    draft = context.service.draft(context.work_id, context.snapshot_id)
    confirmed = context.service.confirm(
        draft.set_id,
        revision=draft.revision,
        intent=draft,
    )
    with context.sessions() as session:
        session.execute(
            update(RelatedResourceSetRecord)
            .where(RelatedResourceSetRecord.id == draft.set_id)
            .values(status="queued")
        )
        session.commit()

    with pytest.raises(RelatedIntentStateError, match="executed"):
        context.repository.revise(
            confirmed.set_id,
            expected_revision=confirmed.revision,
            intent=confirmed.model_copy(update={"summary": "Forbidden rewrite"}),
            origin="manual_edit",
        )
    with pytest.raises(RelatedIntentStateError, match="executed"):
        context.repository.confirm(
            confirmed.set_id,
            expected_revision=confirmed.revision,
            intent=confirmed,
        )


def test_editing_confirmed_payload_requires_a_fresh_confirmation(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    draft = context.service.draft(context.work_id, context.snapshot_id)
    confirmed = context.service.confirm(
        draft.set_id,
        revision=draft.revision,
        intent=draft,
    )

    edited = context.service.confirm(
        confirmed.set_id,
        revision=confirmed.revision,
        intent=confirmed.model_copy(update={"summary": "A narrower confirmed edit"}),
    )

    assert edited.revision == confirmed.revision + 1
    assert edited.confirmed is False
    assert context.repository.get(edited.set_id).status == "draft"
    reconfirmed = context.service.confirm(
        edited.set_id,
        revision=edited.revision,
        intent=edited,
    )
    assert reconfirmed.confirmed is True


def test_corrupt_revision_history_fails_closed(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    draft = context.service.draft(context.work_id, context.snapshot_id)
    with context.sessions() as session:
        session.execute(
            update(RelatedResourceSetRecord)
            .where(RelatedResourceSetRecord.id == draft.set_id)
            .values(intent_history=[{"revision": 999, "origin": "forged"}])
        )
        session.commit()

    with pytest.raises(RelatedIntentConflict, match="history"):
        context.repository.get(draft.set_id)


def test_forged_term_diff_fails_closed(
    related_intent_context: RelatedIntentTestContext,
) -> None:
    context = related_intent_context
    draft = context.service.draft(context.work_id, context.snapshot_id)
    with context.sessions() as session:
        record = session.get(RelatedResourceSetRecord, draft.set_id)
        assert record is not None
        history = list(record.intent_history)
        first = dict(history[0])
        historical_intent = dict(first["intent"])
        historical_intent["diff"] = {
            **historical_intent["diff"],
            "summary_changed": True,
        }
        first["intent"] = historical_intent
        history[0] = first
        record.intent_history = history
        session.commit()

    with pytest.raises(RelatedIntentConflict, match="history"):
        context.repository.get(draft.set_id)
