from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DatabaseError

from paper_reader.settings import AppPaths


def custom_dimensions():  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.score_rules import ScoreRuleDimensionInput

    weights = {
        "citation_performance": 0.35,
        "venue_rating": 0.15,
        "author_impact": 0.10,
        "institution_impact": 0.0,
        "code_data_impact": 0.20,
        "research_transparency": 0.10,
        "publication_maturity": 0.10,
    }
    return tuple(
        ScoreRuleDimensionInput(
            key=key,
            enabled=weight > 0,
            weight=weight,
        )
        for key, weight in weights.items()
    )


def test_score_rule_versions_are_immutable_idempotent_and_resettable(tmp_path: Path) -> None:
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.models import ScoreRuleVersionRecord
    from paper_reader.migrations import upgrade_database
    from paper_reader.recommendation.score_rules import ScoreRuleRepository

    database = tmp_path / "score-rules.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    repository = ScoreRuleRepository(sessions)

    builtin = repository.get_active_quality_rule()
    saved = repository.save_quality_rule(
        name="偏重归一化引用",
        dimensions=custom_dimensions(),
    )
    repeated = repository.save_quality_rule(
        name="偏重归一化引用",
        dimensions=custom_dimensions(),
    )

    assert builtin.id == "quality-default-v1"
    assert builtin.builtin is True
    assert builtin.revision == 0
    assert sum(item.weight for item in builtin.dimensions if item.enabled) == 1
    assert saved.builtin is False
    assert saved.revision == 1
    assert saved.active is True
    assert repeated.id == saved.id
    assert repository.get_active_quality_rule().id == saved.id
    assert repository.quality_weights()["institution_impact"] == 0

    with sessions() as session, pytest.raises(DatabaseError, match="immutable"):
        session.execute(
            update(ScoreRuleVersionRecord)
            .where(ScoreRuleVersionRecord.id == saved.id)
            .values(name="tampered")
        )
        session.commit()
    with sessions() as session, pytest.raises(DatabaseError, match="immutable"):
        session.execute(
            delete(ScoreRuleVersionRecord).where(ScoreRuleVersionRecord.id == saved.id)
        )
        session.commit()

    reset = repository.reset_quality_rule()
    assert reset.id == builtin.id
    assert repository.get_active_quality_rule().builtin is True
    engine.dispose()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda values: values[:-1], "exactly"),
        (
            lambda values: (*values[:-1], type(values[-1])("unknown", True, 0.10)),
            "unknown",
        ),
        (
            lambda values: (
                type(values[0])(values[0].key, False, values[0].weight),
                *values[1:],
            ),
            "disabled",
        ),
        (
            lambda values: (
                type(values[0])(values[0].key, True, 0.34),
                *values[1:],
            ),
            "sum",
        ),
    ],
)
def test_score_rule_validation_rejects_ambiguous_formulas(
    mutate,  # type: ignore[no-untyped-def]
    message: str,
) -> None:
    from paper_reader.recommendation.score_rules import validate_quality_dimensions

    with pytest.raises(ValueError, match=message):
        validate_quality_dimensions(mutate(custom_dimensions()))


def test_custom_quality_weights_change_formula_and_snapshot_evidence(tmp_path: Path) -> None:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.recommendation.quality import QualityMetrics, score_quality
    from paper_reader.recommendation.relevance import MetricEvidence
    from paper_reader.recommendation.score_snapshots import ScoreSnapshotRepository

    unknown = MetricEvidence.unknown(reason="not_loaded")
    result = score_quality(
        QualityMetrics(
            citation_performance=MetricEvidence.observed(100),
            venue_rating=unknown,
            author_impact=unknown,
            institution_impact=MetricEvidence.observed(0),
            code_data_impact=unknown,
            research_transparency=unknown,
            publication_maturity=unknown,
        ),
        weights={item.key: item.weight for item in custom_dimensions()},
        rule_version="quality-v1:fixture",
    )

    assert result.rule_version == "quality-v1:fixture"
    assert result.score == 67.5
    assert result.coverage == 35
    assert result.confidence == 35
    assert result.weights["citation_performance"] == 0.35
    assert result.weights["institution_impact"] == 0

    engine = create_sqlite_engine(tmp_path / "custom-score.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    work = IdentityRepository(sessions).ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.50000",
            title="Explainable Score Rules",
            authors=("Alice",),
        )
    ).work
    snapshot = ScoreSnapshotRepository(sessions).record_quality(
        work_id=work.id,
        context_key="custom-rule",
        quality=result,
        evidence_observation_ids=(),
    )
    assert snapshot.weights == dict(result.weights)
    assert snapshot.rule_version == "quality-v1:fixture"
    engine.dispose()


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


async def bootstrap(client: httpx.AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/v1/session/bootstrap",
        json={"token": "secret"},
        headers={"Origin": "http://127.0.0.1"},
    )
    return {
        "Origin": "http://127.0.0.1",
        "X-CSRF-Token": client.cookies["paper_reader_csrf"],
    }


@pytest.mark.asyncio
async def test_quality_rule_api_returns_exact_neutral_preview_and_versions_changes(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    app = create_app(
        paths=AppPaths.from_overrides(
            state_root=tmp_path / "state",
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "library",
        ),
        bootstrap_token="secret",
    )
    payload = {
        "name": "偏重归一化引用",
        "dimensions": [
            {
                "key": item.key,
                "enabled": item.enabled,
                "weight_percent": item.weight * 100,
            }
            for item in custom_dimensions()
        ],
    }

    async with local_client(app) as client:
        headers = await bootstrap(client)
        default = await client.get("/api/v1/scores/rules/quality")
        saved = await client.put(
            "/api/v1/scores/rules/quality",
            json=payload,
            headers=headers,
        )
        reset = await client.delete(
            "/api/v1/scores/rules/quality",
            headers=headers,
        )

    assert default.status_code == 200
    assert default.json()["builtin"] is True
    preview = default.json()["preview"]
    assert {
        "score": preview["score"],
        "coverage": preview["coverage"],
        "confidence": preview["confidence"],
        "missing_policy": preview["missing_policy"],
    } == {
        "score": 50.0,
        "coverage": 0.0,
        "confidence": 0.0,
        "missing_policy": "unknown_is_neutral_prior",
    }
    assert len(preview["components"]) == 7
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert saved.json()["builtin"] is False
    assert saved.json()["rule_version"].startswith("quality-v1:")
    institution = next(
        item
        for item in saved.json()["dimensions"]
        if item["key"] == "institution_impact"
    )
    assert institution["enabled"] is False
    assert institution["weight_percent"] == 0
    assert saved.json()["preview"]["score"] == 50
    assert reset.status_code == 200
    assert reset.json()["id"] == "quality-default-v1"


@pytest.mark.asyncio
async def test_score_snapshot_api_preserves_formula_evidence_without_ui_recalculation(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.recommendation.quality import QualityMetrics, score_quality
    from paper_reader.recommendation.relevance import MetricEvidence
    from paper_reader.recommendation.score_snapshots import ScoreSnapshotRepository

    app = create_app(
        paths=AppPaths.from_overrides(
            state_root=tmp_path / "state",
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "library",
        ),
        bootstrap_token="secret",
    )
    work = IdentityRepository(app.state.sessions).ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.60000",
            title="Evidence Is Not a UI Guess",
            authors=("Alice",),
        )
    ).work
    unknown = MetricEvidence.unknown(reason="citation_unavailable")
    quality = score_quality(
        QualityMetrics(
            citation_performance=unknown,
            venue_rating=MetricEvidence.observed(80, confidence=90),
            author_impact=unknown,
            institution_impact=unknown,
            code_data_impact=unknown,
            research_transparency=unknown,
            publication_maturity=unknown,
        )
    )
    stored = ScoreSnapshotRepository(app.state.sessions).record_quality(
        work_id=work.id,
        context_key="run:explainable",
        quality=quality,
        evidence_observation_ids=(),
    )

    async with local_client(app) as client:
        await bootstrap(client)
        response = await client.get(
            f"/api/v1/scores/works/{work.id}/snapshots/quality",
            params={"context_key": "run:explainable"},
        )
        missing = await client.get(
            f"/api/v1/scores/works/{work.id}/snapshots/quality",
            params={"context_key": "missing"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == stored.id
    assert body["score"] == stored.score
    assert body["coverage"] == stored.coverage
    assert body["confidence"] == stored.confidence
    assert body["components"] == list(stored.components)
    assert body["weights"] == stored.weights
    assert body["missing_policy"] == stored.missing_policy
    assert missing.status_code == 404
