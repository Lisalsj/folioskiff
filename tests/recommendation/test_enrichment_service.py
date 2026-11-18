from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest


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


class FlakyEnricher:
    def __init__(self, failing_work_id: str) -> None:
        from paper_reader.recommendation.enrichment import EnricherCapabilities

        self.failing_work_id = failing_work_id
        self.failure_remaining = 1
        self.unexpected_failure = False
        self.cancel_on_work_id: str | None = None
        self.cancel_job_id: str | None = None
        self.jobs = None
        self.calls: list[str] = []
        self.capabilities = EnricherCapabilities(
            enricher_id="fixture",
            display_name="Fixture metadata",
            version="fixture-v1",
            enabled=True,
            disabled_reason=None,
            credential_requirement="none",
            credential_configured=False,
            setup_url="https://example.org/fixture",
            default_requests_per_second=None,
            max_concurrency=1,
        )

    async def enrich(self, work):  # type: ignore[no-untyped-def]
        from paper_reader.recommendation.enrichment import (
            EnricherRequestError,
            EnrichmentResult,
            EnrichmentStatus,
        )

        self.calls.append(work.work_id)
        if self.unexpected_failure:
            raise RuntimeError("private-token-must-not-leak")
        if work.work_id == self.cancel_on_work_id and self.jobs is not None:
            self.jobs.request_cancel(self.cancel_job_id)
        if work.work_id == self.failing_work_id and self.failure_remaining:
            self.failure_remaining -= 1
            raise EnricherRequestError(
                "rate_limited",
                "Fixture rate limit reached",
                retryable=True,
                retry_after_seconds=3,
            )
        return EnrichmentResult(
            work_id=work.work_id,
            enricher_id="fixture",
            enricher_version="fixture-v1",
            status=EnrichmentStatus.OBSERVED,
            external_id=f"external-{work.work_id}",
            matched_by="doi",
            fields={"citation_count": 10},
            field_provenance={"citation_count": "fixture:citation_count"},
            warnings=(),
            fetched_at=datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
            response_sha256="b" * 64,
        )


@pytest.fixture
def enrichment_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.runs import DiscoveryRun
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.recommendation.enrichers.registry import EnricherRegistry
    from paper_reader.recommendation.enrichment_service import (
        MetadataEnrichmentService,
        WorkLookupRepository,
    )
    from paper_reader.recommendation.observations import MetadataObservationRepository

    engine = create_sqlite_engine(tmp_path / "enrichment-service.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    identities = IdentityRepository(sessions)
    first = identities.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.00001",
            title="Reliable Paper Reading Agents",
            authors=("Alice",),
            doi="10.5555/agent.2026.1",
        )
    ).work
    second = identities.ingest(
        SourcePaper(
            connector_id="openreview",
            external_id="forum-2",
            title="Evidence Grounded Literature Agents",
            authors=("Bob",),
            doi="10.5555/agent.2026.2",
        )
    ).work
    now = datetime(2026, 7, 13, tzinfo=UTC)
    run = DiscoveryRun(
        id="run-1",
        plan_id="plan-1",
        job_id="discovery-job-1",
        keyword_version_id="keywords-v1",
        status="partial_success",
        months=("2026-07",),
        plan_snapshot={},
        keyword_snapshot={},
        connector_versions={"arxiv": "1"},
        total_batches=2,
        succeeded_batches=1,
        failed_batches=1,
        cancelled_batches=0,
        created_at=now,
        started_at=now,
        completed_at=now,
        batches=(),
    )
    enricher = FlakyEnricher(second.id)
    jobs = JobRepository(sessions)
    observations = MetadataObservationRepository(sessions)
    service = MetadataEnrichmentService(
        runs=FakeRuns(run, (first.id, second.id)),
        jobs=jobs,
        lookups=WorkLookupRepository(sessions),
        observations=observations,
        enrichers=EnricherRegistry((enricher,)),
    )
    try:
        yield service, jobs, observations, enricher, first, second, sessions
    finally:
        engine.dispose()


def test_work_lookup_uses_canonical_doi_and_source_identifiers(
    enrichment_context,
) -> None:  # type: ignore[no-untyped-def]
    service, _, _, _, first, _, _ = enrichment_context

    lookup = service.lookups.get(first.id)

    assert lookup.work_id == first.id
    assert lookup.doi == "10.5555/agent.2026.1"
    assert lookup.source_ids == {"arxiv": "2607.00001"}
    assert lookup.authors == ("Alice",)


def test_work_lookup_follows_merge_tombstones_and_rejects_missing_work(
    enrichment_context,
) -> None:  # type: ignore[no-untyped-def]
    service, _, _, _, first, second, sessions = enrichment_context
    with sessions() as session:
        record = session.get(type(second), second.id)
        assert record is not None
        record.status = "merged"
        record.merged_into_work_id = first.id
        session.commit()

    assert service.lookups.get(second.id).work_id == first.id
    with pytest.raises(KeyError):
        service.lookups.get("missing-work")


@pytest.mark.asyncio
async def test_enrichment_job_is_idempotent_partial_and_resumes_only_failed_items(
    enrichment_context,
) -> None:  # type: ignore[no-untyped-def]
    service, jobs, observations, enricher, first, second, _ = enrichment_context
    created = service.create_job(
        "run-1",
        enricher_ids=("fixture",),
        idempotency_key="enrich-run-1",
    )
    duplicate = service.create_job(
        "run-1",
        enricher_ids=("fixture",),
        idempotency_key="enrich-run-1",
    )

    assert service.resumable_job_ids() == (created.id,)

    await service.execute(created.id)
    partial = jobs.get(created.id)

    assert duplicate.id == created.id
    assert created.payload["work_ids"] == [first.id, second.id]
    assert created.payload["enrichers"] == [
        {"enricher_id": "fixture", "version": "fixture-v1"}
    ]
    assert partial is not None
    assert partial.status == "partial_success"
    assert partial.progress == 100
    assert partial.result == {
        "total": 2,
        "completed": 1,
        "observed": 1,
        "skipped": 0,
        "failed": 1,
        "retryable_failed": 1,
    }
    assert partial.checkpoint is not None
    assert service.resumable_job_ids() == ()
    failed = partial.checkpoint["failures"][f"{second.id}|fixture"]
    assert failed["code"] == "rate_limited"
    assert failed["retryable"] is True
    assert failed["retry_after_seconds"] == 3
    assert len(observations.list_history(first.id)) == 1
    assert observations.list_history(second.id) == ()

    await service.execute(created.id)
    succeeded = jobs.get(created.id)

    assert succeeded is not None
    assert succeeded.status == "succeeded"
    assert succeeded.result == {
        "total": 2,
        "completed": 2,
        "observed": 2,
        "skipped": 0,
        "failed": 0,
        "retryable_failed": 0,
    }
    assert enricher.calls.count(first.id) == 1
    assert enricher.calls.count(second.id) == 2
    assert len(observations.list_history(first.id)) == 1
    assert len(observations.list_history(second.id)) == 1


@pytest.mark.asyncio
async def test_enrichment_job_honors_durable_cancellation_before_network_calls(
    enrichment_context,
) -> None:  # type: ignore[no-untyped-def]
    service, jobs, _, enricher, _, _, _ = enrichment_context
    created = service.create_job("run-1", enricher_ids=("fixture",))
    jobs.request_cancel(created.id)

    await service.execute(created.id)
    cancelled = jobs.get(created.id)

    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.progress == 0
    assert enricher.calls == []


def test_enrichment_job_rejects_unknown_or_empty_enricher_selection(
    enrichment_context,
) -> None:  # type: ignore[no-untyped-def]
    service, _, _, _, _, _, _ = enrichment_context

    with pytest.raises(ValueError, match="at least one"):
        service.create_job("run-1", enricher_ids=())
    with pytest.raises(KeyError):
        service.create_job("run-1", enricher_ids=("unknown",))

    from dataclasses import replace

    service._runs.run = replace(service._runs.run, status="running")  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="terminal"):
        service.create_job("run-1", enricher_ids=("fixture",))


@pytest.mark.asyncio
async def test_changed_enricher_version_is_a_terminal_failure_and_is_not_retried(
    enrichment_context,
) -> None:  # type: ignore[no-untyped-def]
    from dataclasses import replace

    service, jobs, _, enricher, _, _, _ = enrichment_context
    created = service.create_job("run-1", enricher_ids=("fixture",))
    enricher.capabilities = replace(enricher.capabilities, version="fixture-v2")

    await service.execute(created.id)
    failed = jobs.get(created.id)
    await service.execute(created.id)

    assert failed is not None
    assert failed.status == "failed"
    assert failed.result is not None
    assert failed.result["failed"] == 2
    assert failed.result["retryable_failed"] == 0
    assert failed.checkpoint is not None
    assert {
        value["code"] for value in failed.checkpoint["failures"].values()
    } == {"enricher_version_unavailable"}
    assert enricher.calls == []


@pytest.mark.asyncio
async def test_unexpected_enricher_errors_are_terminal_and_secret_free(
    enrichment_context,
) -> None:  # type: ignore[no-untyped-def]
    service, jobs, _, enricher, _, _, _ = enrichment_context
    enricher.unexpected_failure = True
    created = service.create_job("run-1", enricher_ids=None)

    await service.execute(created.id)
    failed = jobs.get(created.id)

    assert failed is not None
    assert failed.status == "failed"
    assert failed.checkpoint is not None
    serialized = str(failed.checkpoint)
    assert "enricher_internal_error" in serialized
    assert "private-token-must-not-leak" not in serialized


@pytest.mark.asyncio
async def test_cancellation_requested_during_a_result_stops_before_next_enricher_call(
    enrichment_context,
) -> None:  # type: ignore[no-untyped-def]
    service, jobs, _, enricher, first, _, _ = enrichment_context
    enricher.failure_remaining = 0
    created = service.create_job("run-1", enricher_ids=("fixture",))
    enricher.cancel_on_work_id = first.id
    enricher.cancel_job_id = created.id
    enricher.jobs = jobs

    await service.execute(created.id)
    cancelled = jobs.get(created.id)

    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.progress == 50
    assert enricher.calls == [first.id]


@pytest.mark.asyncio
async def test_corrupt_job_state_and_idempotency_collisions_fail_closed(
    enrichment_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.enrichment_service import ENRICHMENT_JOB_KIND

    service, jobs, _, _, _, _, _ = enrichment_context
    jobs.create(kind="another.command", payload={}, idempotency_key="collision")
    with pytest.raises(ValueError, match="idempotency"):
        service.create_job(
            "run-1",
            enricher_ids=("fixture",),
            idempotency_key="collision",
        )

    wrong_kind = jobs.create(kind="another.command", payload={})
    with pytest.raises(ValueError, match="not a metadata"):
        await service.execute(wrong_kind.id)
    with pytest.raises(KeyError):
        await service.execute("missing-job")

    corrupt = jobs.create(kind=ENRICHMENT_JOB_KIND, payload={"work_ids": "bad"})
    with pytest.raises(ValueError, match="payload"):
        await service.execute(corrupt.id)

    created = service.create_job("run-1", enricher_ids=("fixture",))
    jobs.update_state(
        created.id,
        status="queued",
        progress=0,
        checkpoint={"completed": [], "failures": {}},
    )
    with pytest.raises(ValueError, match="checkpoint"):
        await service.execute(created.id)

    invalid_payloads = (
        {"work_ids": [1], "enrichers": []},
        {"work_ids": ["work"], "enrichers": ["bad"]},
        {"work_ids": ["work"], "enrichers": [{"enricher_id": 1, "version": None}]},
    )
    for payload in invalid_payloads:
        with pytest.raises(ValueError):
            service._tasks(payload)
