from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest


class FakeLexicalIndex:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int = 50):  # type: ignore[no-untyped-def]
        from paper_reader.recommendation.lexical import LexicalHit

        self.queries.append(query)
        return [
            LexicalHit(
                work_id=work_id,
                lexical_score=score,
                raw_bm25=-score / 100,
                matched_fields=("title",),
            )
            for work_id, score in self.scores.items()
        ][:limit]


class FakeSemanticIndex:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.synced: list[str] = []
        self.queries: list[str] = []

    def sync_work(self, work_id: str) -> bool:
        self.synced.append(work_id)
        return True

    def score_candidates(self, query: str, work_ids: tuple[str, ...]):  # type: ignore[no-untyped-def]
        from paper_reader.recommendation.semantic import SemanticHit

        self.queries.append(query)
        return {
            work_id: SemanticHit(
                work_id=work_id,
                semantic_score=self.scores[work_id],
                distance=1 - (self.scores[work_id] / 100),
            )
            for work_id in work_ids
        }


class FailingSemanticIndex(FakeSemanticIndex):
    def score_candidates(self, query: str, work_ids: tuple[str, ...]):  # type: ignore[no-untyped-def]
        raise RuntimeError("native runtime failed")


class FakeRuns:
    def __init__(self, run, work_ids: tuple[str, ...]) -> None:  # type: ignore[no-untyped-def]
        self.run = run
        self.work_ids = work_ids

    def get(self, run_id: str):  # type: ignore[no-untyped-def]
        if run_id != self.run.id:
            raise KeyError(run_id)
        return self.run

    def list_candidate_work_ids(self, run_id: str) -> tuple[str, ...]:
        if run_id != self.run.id:
            raise KeyError(run_id)
        return self.work_ids


@pytest.fixture
def relevance_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.keywords import ConceptGroup, KeywordSetDefinition
    from paper_reader.discovery.runs import DiscoveryRun

    engine = create_sqlite_engine(tmp_path / "relevance-service.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    identities = IdentityRepository(sessions)
    first = identities.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2604.00001",
            title="Reliable paper reading AI agent",
            authors=("Alice",),
            abstract="Evidence-grounded literature recommendation.",
        )
    ).work
    second = identities.ingest(
        SourcePaper(
            connector_id="openreview",
            external_id="forum-2",
            title="General AI agent planning",
            authors=("Bob",),
            abstract="A planning benchmark.",
        )
    ).work
    definition = KeywordSetDefinition(
        required=(
            ConceptGroup(key="agent", label="智能体", terms=("AI agent", "智能体")),
        ),
        boost=(
            ConceptGroup(
                key="reading",
                label="论文阅读",
                terms=("paper reading", "论文阅读"),
            ),
        ),
    )
    now = datetime(2026, 7, 13, tzinfo=UTC)
    run = DiscoveryRun(
        id="run-1",
        plan_id="plan-1",
        job_id="job-1",
        keyword_version_id="keywords-v1",
        status="succeeded",
        months=("2026-04",),
        plan_snapshot={},
        keyword_snapshot={
            "version_id": "keywords-v1",
            "definition": definition.model_dump(mode="json"),
        },
        connector_versions={"arxiv": "1", "openreview": "1"},
        total_batches=1,
        succeeded_batches=1,
        failed_batches=0,
        cancelled_batches=0,
        created_at=now,
        started_at=now,
        completed_at=now,
        batches=(),
    )
    try:
        yield sessions, FakeRuns(run, (first.id, second.id)), first.id, second.id
    finally:
        engine.dispose()


def test_run_relevance_combines_candidate_scoped_lexical_semantic_and_keyword_evidence(
    relevance_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.service import RelevanceService

    sessions, runs, first_id, second_id = relevance_context
    lexical = FakeLexicalIndex({first_id: 90, second_id: 50})
    semantic = FakeSemanticIndex({first_id: 70, second_id: 90})
    service = RelevanceService(
        sessions,
        runs=runs,
        lexical=lexical,
        semantic=semantic,
    )

    result = service.score_run("run-1")

    assert result.query == "AI agent 智能体 paper reading 论文阅读"
    assert [candidate.work_id for candidate in result.candidates] == [first_id, second_id]
    assert [candidate.relevance.score for candidate in result.candidates] == [84.0, 70.0]
    assert all(candidate.relevance.coverage == 100 for candidate in result.candidates)
    assert result.candidates[0].keyword_alignment.score == 100
    assert result.candidates[1].keyword_alignment.score == 70
    assert semantic.synced == [first_id, second_id]
    assert semantic.queries == [result.query]
    assert lexical.queries == [result.query]


def test_run_relevance_degrades_to_lexical_and_keywords_without_semantic_runtime(
    relevance_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.service import RelevanceService

    sessions, runs, first_id, second_id = relevance_context
    service = RelevanceService(
        sessions,
        runs=runs,
        lexical=FakeLexicalIndex({first_id: 90, second_id: 50}),
        semantic=None,
        semantic_degradation_reasons=("embedding_model_not_installed",),
    )

    result = service.score_run("run-1")

    assert [candidate.relevance.score for candidate in result.candidates] == [
        93.333333,
        56.666667,
    ]
    assert all(candidate.relevance.coverage == 60 for candidate in result.candidates)
    assert all(candidate.relevance.confidence == 60 for candidate in result.candidates)
    assert all(
        candidate.relevance.degradation_reasons == ("embedding_model_not_installed",)
        for candidate in result.candidates
    )


def test_run_relevance_discards_partial_semantic_state_when_runtime_fails(
    relevance_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.service import RelevanceService

    sessions, runs, first_id, second_id = relevance_context
    semantic = FailingSemanticIndex({})
    service = RelevanceService(
        sessions,
        runs=runs,
        lexical=FakeLexicalIndex({first_id: 90, second_id: 50}),
        semantic=semantic,
    )

    result = service.score_run("run-1")

    assert semantic.synced == [first_id, second_id]
    assert [candidate.relevance.coverage for candidate in result.candidates] == [60, 60]
    assert all(candidate.semantic_hit is None for candidate in result.candidates)
    assert all(
        candidate.relevance.degradation_reasons == ("semantic_runtime_failed",)
        for candidate in result.candidates
    )
