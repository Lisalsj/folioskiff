from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def make_definition():  # type: ignore[no-untyped-def]
    from paper_reader.discovery.keywords import ConceptGroup, KeywordSetDefinition

    return KeywordSetDefinition(
        required=(
            ConceptGroup(
                key="method",
                label="扩散模型",
                terms=("diffusion model", "扩散模型"),
            ),
            ConceptGroup(
                key="domain",
                label="癌症影像",
                terms=("cancer imaging", "癌症影像"),
            ),
        ),
        boost=(
            ConceptGroup(
                key="modality",
                label="MRI",
                terms=("magnetic resonance imaging", "MRI"),
            ),
        ),
        excluded=(
            ConceptGroup(
                key="paper_type",
                label="排除综述",
                terms=("survey", "综述"),
            ),
        ),
    )


@pytest.fixture
def keyword_repository(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.keywords import KeywordRepository
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "keywords.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    repository = KeywordRepository(create_session_factory(engine))
    try:
        yield repository
    finally:
        engine.dispose()


def test_unconfirmed_version_cannot_compile_or_filter(keyword_repository) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.keywords import UnconfirmedKeywordVersionError

    version = keyword_repository.create_version(
        research_intent="研究扩散模型在癌症 MRI 影像中的应用",
        definition=make_definition(),
    )

    assert version.confirmed_at is None
    with pytest.raises(UnconfirmedKeywordVersionError, match="must be confirmed"):
        keyword_repository.compile(version.id)


def test_confirmed_groups_compile_as_or_within_and_between_required_groups(
    keyword_repository,
) -> None:  # type: ignore[no-untyped-def]
    version = keyword_repository.create_version(
        research_intent="研究扩散模型在癌症 MRI 影像中的应用",
        definition=make_definition(),
    )
    keyword_repository.confirm(version.id)

    compiled = keyword_repository.compile(version.id)

    assert compiled.boolean_expression == (
        '("diffusion model" OR "扩散模型") AND '
        '("cancer imaging" OR "癌症影像") AND NOT ("survey" OR "综述")'
    )
    assert compiled.boost_terms == ("magnetic resonance imaging", "MRI")
    assert compiled.matches(
        title="A diffusion model for robust diagnosis",
        abstract="Experiments on cancer imaging and magnetic resonance imaging.",
    )
    assert not compiled.matches(
        title="A survey of diffusion model methods",
        abstract="A review for cancer imaging.",
    )
    assert not compiled.matches(
        title="Diffusion model for natural images",
        abstract="No medical application is studied.",
    )


def test_child_version_records_a_deterministic_parent_diff(keyword_repository) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.keywords import ConceptGroup, KeywordSetDefinition

    parent = keyword_repository.create_version(
        research_intent="扩散模型",
        definition=KeywordSetDefinition(
            required=(
                ConceptGroup(
                    key="method",
                    label="扩散模型",
                    terms=("diffusion model", "扩散模型"),
                ),
            )
        ),
    )
    child = keyword_repository.create_version(
        research_intent="扩散模型与 agent",
        parent_version_id=parent.id,
        definition=KeywordSetDefinition(
            required=(
                ConceptGroup(
                    key="method",
                    label="扩散模型",
                    terms=("diffusion model", "score-based model"),
                ),
                ConceptGroup(
                    key="agent",
                    label="智能体",
                    terms=("agent", "智能体"),
                ),
            )
        ),
    )

    assert child.change_summary == {
        "added_groups": ["required:agent"],
        "removed_groups": [],
        "added_terms": {
            "required:method": ["score-based model"],
        },
        "removed_terms": {
            "required:method": ["扩散模型"],
        },
    }


def test_keyword_version_row_is_database_immutable(tmp_path: Path) -> None:
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.keywords import KeywordRepository
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "immutable.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    repository = KeywordRepository(create_session_factory(engine))
    version = repository.create_version(
        research_intent="agents",
        definition=make_definition(),
    )
    engine.dispose()

    connection = sqlite3.connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE keyword_set_versions SET research_intent = ? WHERE id = ?",
                ("silently changed", version.id),
            )
    finally:
        connection.close()


def test_concept_groups_reject_duplicate_keys_and_empty_terms() -> None:
    from pydantic import ValidationError

    from paper_reader.discovery.keywords import ConceptGroup, KeywordSetDefinition

    with pytest.raises(ValidationError):
        ConceptGroup(key="empty", label="Empty", terms=(" ",))

    group = ConceptGroup(key="same", label="One", terms=("agent",))
    with pytest.raises(ValidationError, match="unique"):
        KeywordSetDefinition(required=(group,), boost=(group,))


def test_keyword_domain_normalizes_duplicates_and_rejects_missing_versions(
    keyword_repository,
) -> None:  # type: ignore[no-untyped-def]
    from pydantic import ValidationError

    from paper_reader.discovery.keywords import (
        ConceptGroup,
        KeywordSetDefinition,
        KeywordVersionNotFoundError,
    )

    with pytest.raises(ValidationError, match="blank"):
        ConceptGroup(key="blank", label="   ", terms=("agent",))
    normalized = ConceptGroup(
        key="agent",
        label="  智能体  研究 ",
        terms=("Agent", " agent ", "AGENT"),
    )
    assert normalized.label == "智能体 研究"
    assert normalized.terms == ("Agent",)
    definition = KeywordSetDefinition(required=(normalized,))

    with pytest.raises(ValueError, match="research_intent"):
        keyword_repository.create_version(research_intent="   ", definition=definition)
    with pytest.raises(KeywordVersionNotFoundError):
        keyword_repository.create_version(
            research_intent="agents",
            definition=definition,
            parent_version_id="missing-parent",
        )
    with pytest.raises(KeywordVersionNotFoundError):
        keyword_repository.confirm("missing-version")
    with pytest.raises(KeywordVersionNotFoundError):
        keyword_repository.get("missing-version")

    version = keyword_repository.create_version(
        research_intent="agents",
        definition=definition,
    )
    first = keyword_repository.confirm(version.id)
    repeated = keyword_repository.confirm(version.id)
    assert repeated.confirmed_at == first.confirmed_at
