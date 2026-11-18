from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from paper_reader.agents.contracts import AgentTaskKind
from paper_reader.related_resources.contracts import (
    PaperVerificationStatus,
    RelatedSearchCommand,
    RelatedSearchIntent,
    ResourceVerificationStatus,
)


def confirmed_intent(**overrides: object) -> RelatedSearchIntent:
    values: dict[str, object] = {
        "set_id": "related-set-1",
        "summary": "Diffusion agents for scientific discovery",
        "required_groups": (("diffusion model", "扩散模型"),),
        "optional_terms": ("agent", "智能体"),
        "excluded_terms": ("image editing",),
        "recent_months": 60,
        "source_ids": ("arxiv", "openreview"),
        "revision": 2,
        "confirmed": True,
        "confirmed_at": datetime(2026, 8, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return RelatedSearchIntent.model_validate(values)


def test_related_intent_requires_user_confirmation_before_search() -> None:
    intent = RelatedSearchIntent(
        summary="Diffusion agents for scientific discovery",
        required_groups=(("diffusion model", "扩散模型"),),
        optional_terms=("agent", "智能体"),
        excluded_terms=("image editing",),
        recent_months=60,
        source_ids=("arxiv", "openreview"),
        confirmed=False,
    )

    with pytest.raises(ValidationError, match="confirmed"):
        RelatedSearchCommand(intent=intent, include_external_web=False)


def test_related_intent_confirmation_requires_a_timezone_aware_timestamp() -> None:
    with pytest.raises(ValidationError, match="confirmed_at"):
        confirmed_intent(confirmed_at=None)

    with pytest.raises(ValidationError, match="timezone"):
        confirmed_intent(confirmed_at=datetime(2026, 8, 1))

    with pytest.raises(ValidationError, match="confirmed_at"):
        confirmed_intent(confirmed=False)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("required_groups", (("Planning", "planning"),)),
        ("required_groups", (("planning",), ("ＰＬＡＮＮＩＮＧ",))),
        ("optional_terms", ("agent", "ＡＧＥＮＴ")),
        ("optional_terms", ("diffusion model",)),
        ("excluded_terms", ("智能体",)),
        ("source_ids", ("arxiv", "ARXIV")),
    ],
)
def test_related_intent_rejects_normalized_duplicates_and_conflicts(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        confirmed_intent(**{field: value})


def test_external_web_requires_visible_explicit_consent() -> None:
    with pytest.raises(ValidationError, match="consent"):
        RelatedSearchCommand(
            intent=confirmed_intent(),
            include_external_web=True,
            external_web_consent=False,
        )

    command = RelatedSearchCommand(
        intent=confirmed_intent(),
        include_external_web=True,
        external_web_consent=True,
    )
    assert command.include_academic_sources is True
    assert command.include_external_web is True


def test_related_command_requires_at_least_one_explicit_retrieval_target() -> None:
    with pytest.raises(ValidationError, match="retrieval target"):
        RelatedSearchCommand(
            intent=confirmed_intent(),
            include_academic_sources=False,
            include_external_web=False,
        )

    with pytest.raises(ValidationError, match="consent"):
        RelatedSearchCommand(
            intent=confirmed_intent(),
            include_academic_sources=True,
            include_external_web=False,
            external_web_consent=True,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("summary", "unsafe\x00summary"),
        ("required_groups", (("unsafe\nterm",),)),
        ("optional_terms", ("unsafe\tterm",)),
        ("excluded_terms", ("unsafe\x7fterm",)),
        ("source_ids", ("arxiv/unsafe",)),
    ],
)
def test_related_intent_rejects_controls_and_unsafe_source_ids(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        confirmed_intent(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("required_groups", ()),
        ("required_groups", tuple((f"group-{index}",) for index in range(13))),
        ("required_groups", (tuple(f"term-{index}" for index in range(21)),)),
        ("optional_terms", tuple(f"optional-{index}" for index in range(41))),
        ("excluded_terms", tuple(f"excluded-{index}" for index in range(41))),
        ("recent_months", 0),
        ("recent_months", 121),
        ("source_ids", ()),
        ("source_ids", tuple(f"source-{index}" for index in range(11))),
    ],
)
def test_related_intent_enforces_all_query_bounds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        confirmed_intent(**{field: value})


def test_related_intent_is_trimmed_immutable_and_revisioned() -> None:
    intent = confirmed_intent(
        summary="  Diffusion agents  ",
        required_groups=((" diffusion model ", " 扩散模型 "),),
    )

    assert intent.summary == "Diffusion agents"
    assert intent.required_groups == (("diffusion model", "扩散模型"),)
    assert intent.revision == 2
    with pytest.raises(ValidationError, match="frozen"):
        intent.summary = "changed"


def test_related_verification_and_agent_task_kinds_are_closed() -> None:
    assert {item.value for item in PaperVerificationStatus} == {
        "verified",
        "pending_verification",
        "rejected",
    }
    assert {item.value for item in ResourceVerificationStatus} == {
        "verified",
        "pending_verification",
        "rejected",
    }
    assert AgentTaskKind.RELATED_INTENT.value == "related_intent"
    assert AgentTaskKind.RELATED_EXTERNAL.value == "related_external"
