from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select


@pytest.fixture
def lexical_workspace(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "paper-reader.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    identities = IdentityRepository(sessions)

    title_hit = identities.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2601.00001",
            title="Diffusion Models for Robust Medical Imaging",
            authors=("Ming Li",),
            abstract="A robust image synthesis method.",
        )
    ).work
    abstract_hit = identities.ingest(
        SourcePaper(
            connector_id="openreview",
            external_id="forum-2",
            title="Reliable Clinical Image Synthesis",
            authors=("Ana Smith",),
            abstract="We evaluate diffusion methods for clinical images.",
        )
    ).work
    chinese_hit = identities.ingest(
        SourcePaper(
            connector_id="europe_pmc",
            external_id="MED/3",
            title="面向癌症影像的智能诊断方法",
            authors=("王明",),
            abstract="研究多模态医学图像分析。",
        )
    ).work

    try:
        yield sessions, title_hit.id, abstract_hit.id, chinese_hit.id
    finally:
        engine.dispose()


def test_multilingual_tokenizer_has_a_versioned_chinese_and_english_golden_sample() -> None:
    from paper_reader.recommendation.lexical import (
        TOKENIZER_VERSION,
        compile_match_expression,
        multilingual_tokens,
    )

    assert TOKENIZER_VERSION == "multilingual-v1"
    assert multilingual_tokens("Diffusion模型用于癌症影像") == (
        "diffusion",
        "模",
        "型",
        "用",
        "于",
        "癌",
        "症",
        "影",
        "像",
        "模型",
        "型用",
        "用于",
        "于癌",
        "癌症",
        "症影",
        "影像",
    )
    assert compile_match_expression('agent" OR work_search:*') == (
        '"agent" OR "or" OR "work" OR "search"'
    )
    assert compile_match_expression("Alice Chen", operator="AND", prefix=True) == (
        '"alice"* AND "chen"*'
    )
    assert compile_match_expression('" : *') == ""


def test_fts5_search_is_multilingual_weighted_and_normalized(lexical_workspace) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.lexical import LexicalIndex

    sessions, title_hit, abstract_hit, chinese_hit = lexical_workspace
    index = LexicalIndex(sessions)

    assert index.rebuild() == 3
    english = index.search("diffusion")
    chinese = index.search("癌症影像")

    assert [hit.work_id for hit in english[:2]] == [title_hit, abstract_hit]
    assert english[0].lexical_score == 100.0
    assert 0.0 <= english[1].lexical_score <= 100.0
    assert chinese[0].work_id == chinese_hit
    assert chinese[0].matched_fields == ("title",)
    assert index.search('" OR *') == []


def test_incremental_sync_replaces_stale_terms_and_removes_merged_work(
    lexical_workspace,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import WorkRecord
    from paper_reader.recommendation.lexical import LexicalIndex

    sessions, title_hit, abstract_hit, _ = lexical_workspace
    index = LexicalIndex(sessions)
    index.rebuild()

    with sessions() as session:
        work = session.get(WorkRecord, abstract_hit)
        assert work is not None
        work.abstract = "Graph neural retrieval for literature agents."
        session.commit()
    index.sync_work(abstract_hit)

    assert [hit.work_id for hit in index.search("diffusion")] == [title_hit]
    assert index.search("graph neural")[0].work_id == abstract_hit

    with sessions() as session:
        work = session.get(WorkRecord, abstract_hit)
        assert work is not None
        work.status = "merged"
        work.merged_into_work_id = title_hit
        session.commit()
    index.sync_work(abstract_hit)

    assert index.search("graph neural") == []


def test_equal_bm25_scores_use_work_id_as_a_stable_tie_breaker(
    lexical_workspace,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import WorkRecord
    from paper_reader.recommendation.lexical import LexicalIndex

    sessions, title_hit, abstract_hit, chinese_hit = lexical_workspace
    with sessions() as session:
        for work_id in (title_hit, abstract_hit, chinese_hit):
            work = session.scalar(select(WorkRecord).where(WorkRecord.id == work_id))
            assert work is not None
            work.canonical_title = "Identical Agent Paper"
            work.abstract = None
            work.authors = []
        session.commit()

    index = LexicalIndex(sessions)
    index.rebuild()
    hits = index.search("agent")

    assert [hit.work_id for hit in hits] == sorted((title_hit, abstract_hit, chinese_hit))


def test_identity_ingestion_updates_the_derived_index_without_a_rebuild(tmp_path: Path) -> None:
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.migrations import upgrade_database
    from paper_reader.recommendation.lexical import LexicalIndex

    database = tmp_path / "incremental.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    index = LexicalIndex(sessions)
    identities = IdentityRepository(sessions, work_index=index)
    try:
        work = identities.ingest(
            SourcePaper(
                connector_id="arxiv",
                external_id="2602.00042",
                title="Auditable Literature Recommendation Agents",
                authors=("Lin Example",),
            )
        ).work

        assert index.search("auditable agents")[0].work_id == work.id
    finally:
        engine.dispose()


def test_confirmed_merge_removes_the_alias_from_the_search_index(tmp_path: Path) -> None:
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.migrations import upgrade_database
    from paper_reader.recommendation.lexical import LexicalIndex

    database = tmp_path / "merge-index.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    index = LexicalIndex(sessions)
    identities = IdentityRepository(sessions, work_index=index)
    try:
        target = identities.ingest(
            SourcePaper(
                connector_id="openreview",
                external_id="forum-a",
                title="Tool-Augmented Agents for Scientific Discovery",
                authors=("A. Chen", "R. Kumar"),
            )
        ).work
        candidate = identities.ingest(
            SourcePaper(
                connector_id="arxiv",
                external_id="2601.00002v1",
                title="Tool Augmented Agent for Scientific Discoveries",
                authors=("Alice Chen", "Ravi Kumar"),
            )
        )
        assert candidate.merge_review is not None
        assert len(index.search("tool augmented")) == 2

        identities.decide_merge_review(candidate.merge_review.id, decision="merge")

        assert [hit.work_id for hit in index.search("tool augmented")] == [target.id]
    finally:
        engine.dispose()
