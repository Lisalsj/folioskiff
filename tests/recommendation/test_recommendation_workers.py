from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest


class FakeDiscovery:
    def __init__(self, status: str) -> None:
        self.status = status
        self.calls: list[tuple[str, bool]] = []

    async def execute(self, run_id: str):  # type: ignore[no-untyped-def]
        self.calls.append((run_id, False))
        return self._run(run_id)

    async def retry_failed(self, run_id: str):  # type: ignore[no-untyped-def]
        self.calls.append((run_id, True))
        return self._run(run_id)

    def _run(self, run_id: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            id=run_id,
            job_id="discovery-job",
            status=self.status,
            succeeded_batches=2,
            failed_batches=1 if self.status == "partial_success" else 0,
            completed_at=datetime(2026, 7, 15, tzinfo=UTC),
        )


class FakeMetadata:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, job_id: str) -> None:
        self.calls.append(job_id)


class FakeJobs:
    def __init__(self, status: str) -> None:
        self.job = SimpleNamespace(
            id="metadata-job",
            status=status,
            payload={"run_id": "run-1"},
        )

    def get(self, job_id: str):  # type: ignore[no-untyped-def]
        assert job_id == self.job.id
        return self.job


class FakeRecommendations:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str]] = []
        self.executed: list[str] = []

    def create_job(self, run_id: str, *, trigger: str, trigger_job_id: str):  # type: ignore[no-untyped-def]
        self.created.append((run_id, trigger, trigger_job_id))
        return SimpleNamespace(id=f"recommendation-{len(self.created)}", status="queued")

    async def execute(self, job_id: str):  # type: ignore[no-untyped-def]
        self.executed.append(job_id)
        return SimpleNamespace(id=job_id, status="succeeded")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "partial_success"])
async def test_discovery_terminal_state_automatically_scores_recommendations(
    status: str,
) -> None:
    from paper_reader.recommendation.workers import run_discovery_then_recommend

    discovery = FakeDiscovery(status)
    recommendations = FakeRecommendations()

    await run_discovery_then_recommend(
        discovery=discovery,
        recommendations=recommendations,
        run_id="run-1",
        retry=True,
    )

    assert discovery.calls == [("run-1", True)]
    assert recommendations.created[0][:2] == ("run-1", "discovery")
    assert "discovery-job" in recommendations.created[0][2]
    assert recommendations.executed == ["recommendation-1"]


@pytest.mark.asyncio
async def test_cancelled_discovery_does_not_score_recommendations() -> None:
    from paper_reader.recommendation.workers import run_discovery_then_recommend

    discovery = FakeDiscovery("cancelled")
    recommendations = FakeRecommendations()

    await run_discovery_then_recommend(
        discovery=discovery,
        recommendations=recommendations,
        run_id="run-1",
        retry=False,
    )

    assert recommendations.created == []

@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "partial_success"])
async def test_metadata_completion_automatically_creates_a_new_scoring_pass(
    status: str,
) -> None:
    from paper_reader.recommendation.workers import run_metadata_then_recommend

    metadata = FakeMetadata()
    jobs = FakeJobs(status)
    recommendations = FakeRecommendations()

    await run_metadata_then_recommend(
        metadata=metadata,
        jobs=jobs,
        recommendations=recommendations,
        job_id="metadata-job",
    )

    assert metadata.calls == ["metadata-job"]
    assert recommendations.created == [
        ("run-1", "metadata", "metadata-job")
    ]
    assert recommendations.executed == ["recommendation-1"]


@pytest.mark.asyncio
async def test_failed_metadata_refresh_keeps_the_existing_formal_set() -> None:
    from paper_reader.recommendation.workers import run_metadata_then_recommend

    metadata = FakeMetadata()
    jobs = FakeJobs("failed")
    recommendations = FakeRecommendations()

    await run_metadata_then_recommend(
        metadata=metadata,
        jobs=jobs,
        recommendations=recommendations,
        job_id="metadata-job",
    )

    assert recommendations.created == []
