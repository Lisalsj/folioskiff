from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def suggestion_repository(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.keywords import KeywordRepository
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "suggestions.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    repository = KeywordRepository(create_session_factory(engine))
    try:
        yield repository
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_local_fallback_proposes_twelve_to_twenty_four_bilingual_terms(
    suggestion_repository,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.suggestions import KeywordSuggestionService

    service = KeywordSuggestionService(repository=suggestion_repository)

    version = await service.suggest(
        research_intent="研究 diffusion model 在癌症 MRI 影像中的可靠应用",
        target_count=18,
    )

    terms = [
        term
        for section in (
            version.definition.required,
            version.definition.boost,
            version.definition.excluded,
        )
        for group in section
        for term in group.terms
    ]
    assert 12 <= len(terms) <= 24
    assert any(any("a" <= char.casefold() <= "z" for char in term) for term in terms)
    assert any(any("\u4e00" <= char <= "\u9fff" for char in term) for term in terms)
    assert version.confirmed_at is None
    assert version.generator_info["mode"] == "local_fallback"
    assert version.generator_info["intent_used_for_filtering"] is False


@pytest.mark.asyncio
async def test_local_fallback_can_use_visible_unpaired_preference_terms(
    suggestion_repository,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.suggestions import (
        CorpusKeyword,
        KeywordSuggestionService,
    )

    context_hash = "a" * 64
    version = await KeywordSuggestionService(
        repository=suggestion_repository
    ).suggest(
        research_intent="研究 diffusion model 在癌症影像中的应用",
        target_count=18,
        preference_terms=(
            CorpusKeyword(term="radiomics", weight=100),
            CorpusKeyword(term="rare cancer imaging", weight=90),
            CorpusKeyword(term="tumor synthesis", weight=80),
        ),
        preference_context_enabled=True,
        preference_context_sha256=context_hash,
    )

    terms = tuple(
        term
        for section in (
            version.definition.required,
            version.definition.boost,
            version.definition.excluded,
        )
        for group in section
        for term in group.terms
    )
    assert 12 <= len(terms) <= 18
    assert "radiomics" in terms
    assert "rare cancer imaging" in terms
    assert any(any("a" <= char.casefold() <= "z" for char in term) for term in terms)
    assert any(any("\u4e00" <= char <= "\u9fff" for char in term) for term in terms)
    assert version.generator_info["mode"] == "local_fallback"
    assert version.generator_info["intent_used_for_filtering"] is False
    assert version.generator_info["preference_context"] == {
        "enabled": True,
        "sha256": context_hash,
        "term_count": 3,
    }


@pytest.mark.asyncio
async def test_unknown_intent_without_corpus_requires_manual_keywords(
    suggestion_repository,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.suggestions import (
        KeywordSuggestionService,
        ManualKeywordsRequiredError,
    )

    service = KeywordSuggestionService(repository=suggestion_repository)

    with pytest.raises(ManualKeywordsRequiredError, match="manual"):
        await service.suggest(
            research_intent="探索一个尚未进入受控词表的新方向甲乙丙",
            target_count=18,
        )


@pytest.mark.asyncio
async def test_local_corpus_can_seed_an_unknown_intent_without_using_the_intent_as_filter(
    suggestion_repository,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.suggestions import (
        CorpusKeyword,
        KeywordSuggestionService,
    )

    service = KeywordSuggestionService(repository=suggestion_repository)
    version = await service.suggest(
        research_intent="探索一个尚未进入受控词表的新方向甲乙丙",
        corpus_terms=(
            CorpusKeyword(term="causal representation", translation="因果表征", weight=9),
            CorpusKeyword(term="domain shift", translation="领域偏移", weight=7),
            CorpusKeyword(term="invariance", translation="不变性", weight=5),
            CorpusKeyword(term="counterfactual", translation="反事实", weight=4),
            CorpusKeyword(term="robust learning", translation="鲁棒学习", weight=3),
            CorpusKeyword(term="distribution shift", translation="分布偏移", weight=2),
        ),
        target_count=12,
    )

    flattened = [term for group in version.definition.required for term in group.terms]
    assert "causal representation" in flattened
    assert "因果表征" in flattened
    assert version.research_intent == "探索一个尚未进入受控词表的新方向甲乙丙"


@pytest.mark.asyncio
async def test_agent_expansion_preserves_parent_and_can_exceed_initial_limit(
    suggestion_repository,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.keywords import ConceptGroup, KeywordSetDefinition
    from paper_reader.discovery.suggestions import (
        KeywordSuggestionContext,
        KeywordSuggestionService,
    )

    class FakeAgent:
        backend_id = "fake-agent"

        async def suggest(self, context: KeywordSuggestionContext) -> KeywordSetDefinition:
            assert context.parent_definition is not None
            return KeywordSetDefinition(
                required=(
                    ConceptGroup(
                        key="new_direction",
                        label="新增方向",
                        terms=tuple(
                            term
                            for index in range(1, 9)
                            for term in (f"new term {index}", f"新增词 {index}")
                        ),
                    ),
                )
            )

    initial = await KeywordSuggestionService(repository=suggestion_repository).suggest(
        research_intent="diffusion model research",
        target_count=18,
    )
    expanded = await KeywordSuggestionService(
        repository=suggestion_repository,
        backend=FakeAgent(),
    ).suggest(
        research_intent="diffusion model research",
        parent_version_id=initial.id,
        target_count=18,
    )
    term_count = sum(
        len(group.terms)
        for section in (
            expanded.definition.required,
            expanded.definition.boost,
            expanded.definition.excluded,
        )
        for group in section
    )

    assert expanded.parent_version_id == initial.id
    assert term_count > 24
    assert expanded.generator_info["mode"] == "agent"
    assert expanded.generator_info["backend_id"] == "fake-agent"
    assert expanded.change_summary["added_groups"] == ["required:new_direction"]


def test_suggestion_helpers_handle_sparse_corpus_odd_limits_and_cross_section_merges() -> None:
    from paper_reader.discovery.keywords import ConceptGroup, KeywordSetDefinition
    from paper_reader.discovery.suggestions import (
        ControlledConcept,
        CorpusKeyword,
        ManualKeywordsRequiredError,
        _corpus_definition,
        _take_concepts,
        local_fallback,
        merge_definitions,
    )

    taken = _take_concepts(
        [
            (
                "required",
                ControlledConcept(
                    key="first",
                    label="第一组",
                    triggers=(),
                    terms=("one", "一", "two"),
                ),
            ),
            (
                "boost",
                ControlledConcept(
                    key="too_short",
                    label="不足一对",
                    triggers=(),
                    terms=("single",),
                ),
            ),
        ],
        4,
    )
    assert taken.required[0].terms == ("one", "一")
    assert taken.boost == ()

    corpus = _corpus_definition(
        (
            CorpusKeyword(term="ignored", translation=None, weight=100),
            *tuple(
                CorpusKeyword(term=f"term {index}", translation=f"词 {index}", weight=index)
                for index in range(6)
            ),
        ),
        12,
    )
    assert corpus is not None
    assert "ignored" not in corpus.required[0].terms

    with pytest.raises(ManualKeywordsRequiredError, match="fewer than 12"):
        local_fallback(
            research_intent="medical imaging",
            target_count=12,
            corpus_terms=(),
            preference_terms=(),
        )

    merged = merge_definitions(
        KeywordSetDefinition(
            required=(ConceptGroup(key="agent", label="智能体", terms=("agent",)),)
        ),
        KeywordSetDefinition(
            required=(ConceptGroup(key="new", label="新增", terms=("new",)),),
            boost=(
                ConceptGroup(
                    key="agent",
                    label="移动分组也沿用父级位置",
                    terms=("Agent", "tool agent"),
                ),
            ),
        ),
    )
    assert [group.key for group in merged.required] == ["agent", "new"]
    assert merged.required[0].terms == ("agent", "tool agent")
    assert merged.boost == ()


@pytest.mark.asyncio
async def test_agent_validation_falls_back_and_valid_initial_agent_is_recorded(
    suggestion_repository,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.keywords import ConceptGroup, KeywordSetDefinition
    from paper_reader.discovery.suggestions import (
        KeywordSuggestionContext,
        KeywordSuggestionService,
    )

    class InvalidAgent:
        backend_id = "invalid-agent"

        async def suggest(self, context: KeywordSuggestionContext) -> KeywordSetDefinition:
            return KeywordSetDefinition(
                required=(
                    ConceptGroup(key="too_short", label="不足", terms=("agent", "智能体")),
                )
            )

    with pytest.raises(ValueError, match="between 12 and 24"):
        await KeywordSuggestionService(repository=suggestion_repository).suggest(
            research_intent="diffusion",
            target_count=11,
        )

    fallback = await KeywordSuggestionService(
        repository=suggestion_repository,
        backend=InvalidAgent(),
    ).suggest(research_intent="diffusion model", target_count=18)
    assert fallback.generator_info["mode"] == "local_fallback"
    assert fallback.generator_info["fallback_reason"] == "ValueError"

    class ValidAgent:
        backend_id = "valid-agent"

        async def suggest(self, context: KeywordSuggestionContext) -> KeywordSetDefinition:
            return KeywordSetDefinition(
                required=(
                    ConceptGroup(
                        key="agent",
                        label="智能体",
                        terms=tuple(
                            term
                            for index in range(6)
                            for term in (f"agent term {index}", f"智能体词 {index}")
                        ),
                    ),
                )
            )

    agent = await KeywordSuggestionService(
        repository=suggestion_repository,
        backend=ValidAgent(),
    ).suggest(research_intent="a model-proposed direction", target_count=12)
    assert agent.generator_info["mode"] == "agent"
    assert agent.generator_info["backend_id"] == "valid-agent"
