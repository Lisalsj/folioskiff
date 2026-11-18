from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest


def keyword_definition():  # type: ignore[no-untyped-def]
    from paper_reader.discovery.keywords import ConceptGroup, KeywordSetDefinition

    return KeywordSetDefinition(
        required=(
            ConceptGroup(
                key="agent",
                label="智能体",
                terms=("AI agent", "智能体"),
            ),
        ),
        excluded=(
            ConceptGroup(
                key="survey",
                label="综述",
                terms=("survey", "综述"),
            ),
        ),
    )


@pytest.fixture
def run_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository
    from paper_reader.discovery.keywords import KeywordRepository
    from paper_reader.discovery.plans import DiscoveryPlanRepository
    from paper_reader.discovery.runs import DiscoveryRunRepository
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "runs.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    try:
        yield {
            "sessions": sessions,
            "keywords": KeywordRepository(sessions),
            "plans": DiscoveryPlanRepository(sessions),
            "identities": IdentityRepository(sessions),
            "jobs": JobRepository(sessions),
            "runs": DiscoveryRunRepository(sessions),
        }
    finally:
        engine.dispose()


def create_plan(  # type: ignore[no-untyped-def]
    context,
    *,
    sources: tuple[str, ...],
    start_month: str = "2026-06",
    end_month: str = "2026-07",
    priority_limit: int = 2,
    candidate_limit: int = 5,
):
    from paper_reader.discovery.plans import (
        DateRangeSpec,
        DiscoveryPlanCreate,
        PlanQuota,
    )

    version = context["keywords"].create_version(
        research_intent="追踪可靠的 AI 智能体研究",
        definition=keyword_definition(),
        generator_info={"backend": "fixture", "intent_used_for_filtering": False},
    )
    context["keywords"].confirm(version.id)
    return context["plans"].create(
        DiscoveryPlanCreate(
            name="智能体月度追踪",
            research_intent="追踪可靠的 AI 智能体研究",
            keyword_version_id=version.id,
            date_range=DateRangeSpec(
                mode="fixed",
                start_month=start_month,
                end_month=end_month,
            ),
            sources=sources,
            source_filters={source: {"fixture": source} for source in sources},
            quota=PlanQuota(
                priority_limit=priority_limit,
                candidate_limit=candidate_limit,
            ),
        ),
        now=datetime(2026, 7, 13, tzinfo=UTC),
    )


def connector_capabilities(connector_id: str):  # type: ignore[no-untyped-def]
    from paper_reader.discovery.connectors import ConnectorCapabilities

    return ConnectorCapabilities(
        connector_id=connector_id,
        display_name=connector_id.title(),
        credential_requirement="none",
        supports_exact_updated_date=True,
        protocol_version="fixture-v1",
    )


def create_service(context, connectors):  # type: ignore[no-untyped-def]
    from paper_reader.discovery.connectors import ConnectorRegistry
    from paper_reader.discovery.runs import DiscoveryRunService

    return DiscoveryRunService(
        plans=context["plans"],
        keywords=context["keywords"],
        connectors=ConnectorRegistry(connectors),
        identities=context["identities"],
        jobs=context["jobs"],
        runs=context["runs"],
    )


def test_run_creation_freezes_snapshots_builds_month_source_batches_and_is_idempotent(
    run_context,
) -> None:  # type: ignore[no-untyped-def]
    class NeverCalledConnector:
        def __init__(self, connector_id: str) -> None:
            self.capabilities = connector_capabilities(connector_id)

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError("run creation must not access a source")

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            raise AssertionError("run creation must not access a source")

    plan = create_plan(run_context, sources=("arxiv", "openreview"))
    service = create_service(
        run_context,
        (NeverCalledConnector("arxiv"), NeverCalledConnector("openreview")),
    )

    first = service.create(
        plan.id,
        idempotency_key="manual:plan-1:2026-07-13",
        now=datetime(2026, 7, 13, 8, tzinfo=UTC),
    )
    second = service.create(
        plan.id,
        idempotency_key="manual:plan-1:2026-07-13",
        now=datetime(2026, 7, 14, 8, tzinfo=UTC),
    )

    assert second.id == first.id
    assert second.job_id == first.job_id
    assert first.status == "queued"
    assert first.months == ("2026-06", "2026-07")
    assert first.plan_snapshot["name"] == "智能体月度追踪"
    assert first.keyword_snapshot["version_id"] == plan.keyword_version_id
    assert first.keyword_snapshot["boolean_expression"].startswith('("AI agent"')
    assert first.connector_versions == {
        "arxiv": "fixture-v1",
        "openreview": "fixture-v1",
    }
    assert [(batch.month, batch.connector_id) for batch in first.batches] == [
        ("2026-06", "arxiv"),
        ("2026-06", "openreview"),
        ("2026-07", "arxiv"),
        ("2026-07", "openreview"),
    ]
    assert len({batch.query_fingerprint for batch in first.batches}) == 4


@pytest.mark.asyncio
async def test_run_persists_partial_success_and_strict_local_matches(run_context) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.connectors import (
        ConnectorRequestError,
        SourcePage,
    )
    from paper_reader.discovery.identity import SourcePaper

    class SuccessfulConnector:
        capabilities = connector_capabilities("arxiv")

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError("execution does not need a separate estimate")

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            assert query.months == ("2026-07",)
            assert cursor is None
            assert limit == 50
            return SourcePage(
                records=(
                    SourcePaper(
                        connector_id="arxiv",
                        external_id="2607.00001v1",
                        title="Reliable AI agent planning",
                        authors=("Alice",),
                        updated_at=datetime(2026, 7, 10, tzinfo=UTC),
                    ),
                    SourcePaper(
                        connector_id="arxiv",
                        external_id="2507.00002v1",
                        title="Reliable AI agent from last year",
                        authors=("Bob",),
                        updated_at=datetime(2025, 7, 10, tzinfo=UTC),
                    ),
                    SourcePaper(
                        connector_id="arxiv",
                        external_id="2607.00003v1",
                        title="Diffusion image generation",
                        authors=("Carol",),
                        updated_at=datetime(2026, 7, 11, tzinfo=UTC),
                    ),
                    SourcePaper(
                        connector_id="arxiv",
                        external_id="2607.00004v1",
                        title="AI agent survey",
                        authors=("Dan",),
                        updated_at=datetime(2026, 7, 12, tzinfo=UTC),
                    ),
                    SourcePaper(
                        connector_id="arxiv",
                        external_id="unknown-date",
                        title="Reliable AI agent without date",
                        authors=("Eve",),
                    ),
                ),
                next_cursor=None,
                total=5,
            )

    class FailingConnector:
        capabilities = connector_capabilities("openreview")

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            raise ConnectorRequestError(
                "rate_limited",
                "try later",
                retryable=True,
                retry_after_seconds=30,
            )

    plan = create_plan(
        run_context,
        sources=("arxiv", "openreview"),
        start_month="2026-07",
        end_month="2026-07",
    )
    service = create_service(run_context, (SuccessfulConnector(), FailingConnector()))
    created = service.create(plan.id, idempotency_key="partial")

    completed = await service.execute(created.id)

    assert completed.status == "partial_success"
    assert completed.succeeded_batches == 1
    assert completed.failed_batches == 1
    by_source = {batch.connector_id: batch for batch in completed.batches}
    assert by_source["arxiv"].status == "succeeded"
    assert by_source["arxiv"].fetched_count == 5
    assert by_source["arxiv"].accepted_count == 1
    assert by_source["arxiv"].ingested_count == 1
    assert by_source["arxiv"].filter_counts is not None
    assert by_source["arxiv"].filter_counts.to_dict() == {
        "evaluated_count": 5,
        "missing_updated_at_count": 1,
        "outside_date_range_count": 1,
        "in_date_range_count": 3,
        "keyword_mismatch_count": 2,
        "accepted_count": 1,
    }
    assert by_source["openreview"].filter_counts is None
    assert by_source["openreview"].status == "failed"
    assert by_source["openreview"].error_code == "rate_limited"
    assert by_source["openreview"].retryable is True
    assert by_source["openreview"].retry_after_seconds == 30
    assert run_context["identities"].count_works() == 1
    assert run_context["identities"].count_source_records() == 1
    assert len(run_context["runs"].list_candidates(created.id)) == 1
    assert len(run_context["runs"].list_candidate_work_ids(created.id)) == 1
    job = run_context["jobs"].get(created.job_id)
    assert job is not None
    assert job.status == "partial_success"
    assert job.progress == 100


@pytest.mark.asyncio
async def test_retry_resumes_from_last_successful_cursor_without_reingesting_page(
    run_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.connectors import ConnectorRequestError, SourcePage
    from paper_reader.discovery.identity import SourcePaper

    class RecoveringConnector:
        capabilities = connector_capabilities("arxiv")

        def __init__(self) -> None:
            self.recovered = False
            self.cursors: list[str | None] = []

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            self.cursors.append(cursor)
            external_id = "2607.00001v1" if cursor is None else "2607.00002v1"
            title = (
                "Reliable AI agent planning under uncertainty"
                if cursor is None
                else "AI agent tool use in scientific laboratories"
            )
            if cursor == "page-2" and not self.recovered:
                raise ConnectorRequestError("remote_5xx", "temporary", retryable=True)
            return SourcePage(
                records=(
                    SourcePaper(
                        connector_id="arxiv",
                        external_id=external_id,
                        title=title,
                        authors=("Alice",),
                        updated_at=datetime(2026, 7, 10, tzinfo=UTC),
                    ),
                ),
                next_cursor="page-2" if cursor is None else None,
                total=2,
            )

    connector = RecoveringConnector()
    plan = create_plan(
        run_context,
        sources=("arxiv",),
        start_month="2026-07",
        end_month="2026-07",
    )
    service = create_service(run_context, (connector,))
    created = service.create(plan.id, idempotency_key="resume")

    failed = await service.execute(created.id)
    connector.recovered = True
    succeeded = await service.retry_failed(created.id)

    assert failed.status == "failed"
    assert failed.batches[0].checkpoint["cursor"] == "page-2"
    assert failed.batches[0].accepted_count == 1
    assert failed.batches[0].new_work_count == 1
    assert failed.batches[0].scanned_count == 1
    assert succeeded.status == "succeeded"
    assert succeeded.batches[0].accepted_count == 2
    assert succeeded.batches[0].new_work_count == 2
    assert succeeded.batches[0].scanned_count == 2
    assert failed.batches[0].filter_counts is not None
    assert failed.batches[0].filter_counts.evaluated_count == 1
    assert succeeded.batches[0].filter_counts is not None
    assert succeeded.batches[0].filter_counts.evaluated_count == 2
    assert succeeded.batches[0].filter_counts.accepted_count == 2
    assert connector.cursors == [None, "page-2", "page-2"]
    assert run_context["identities"].count_works() == 2
    assert run_context["identities"].count_source_records() == 2
    assert len(run_context["runs"].list_candidates(created.id)) == 2
    assert len(run_context["runs"].list_candidate_work_ids(created.id)) == 2


@pytest.mark.parametrize("legacy_checkpoint", [False, True])
def test_filter_counts_are_idempotent_and_honest_after_legacy_resume(
    run_context, legacy_checkpoint: bool
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.filtering import SourceFilterCounts

    class NeverCalledConnector:
        capabilities = connector_capabilities("arxiv")

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError("checkpoint persistence does not fetch sources")

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            raise AssertionError("checkpoint persistence does not fetch sources")

    plan = create_plan(run_context, sources=("arxiv",))
    run = create_service(run_context, (NeverCalledConnector(),)).create(plan.id)
    repository = run_context["runs"]
    counts = SourceFilterCounts(
        evaluated_count=3,
        outside_date_range_count=1,
        in_date_range_count=2,
        keyword_mismatch_count=2,
    )
    parameters = dict(
        page_cursor=None,
        next_cursor="page-2",
        fetched=3,
        accepted=0,
        ingested=0,
        candidates=(),
        now=datetime.now(UTC),
    )
    first = repository.save_page(
        run.batches[0].id,
        **parameters,
        filter_counts=None if legacy_checkpoint else counts,
    )
    replay = repository.save_page(run.batches[0].id, **parameters, filter_counts=counts)
    assert replay.fetched_count == first.fetched_count == 3
    assert replay.filter_counts == first.filter_counts
    next_page = repository.save_page(
        run.batches[0].id,
        **{**parameters, "page_cursor": "page-2", "next_cursor": None},
        filter_counts=counts,
    )
    assert next_page.fetched_count == 6
    assert next_page.filter_counts is not None
    assert next_page.filter_counts.evaluated_count == (3 if legacy_checkpoint else 6)
    assert next_page.filter_counts.keyword_mismatch_count == (2 if legacy_checkpoint else 4)


@pytest.mark.asyncio
async def test_cancelled_job_never_calls_connector_and_marks_remaining_batches(
    run_context,
) -> None:  # type: ignore[no-untyped-def]
    class CountingConnector:
        capabilities = connector_capabilities("arxiv")

        def __init__(self) -> None:
            self.calls = 0

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise AssertionError("cancelled work must not call a connector")

    connector = CountingConnector()
    plan = create_plan(run_context, sources=("arxiv",))
    service = create_service(run_context, (connector,))
    created = service.create(plan.id, idempotency_key="cancel")
    run_context["jobs"].request_cancel(created.job_id)

    cancelled = await service.execute(created.id)

    assert cancelled.status == "cancelled"
    assert connector.calls == 0
    assert {batch.status for batch in cancelled.batches} == {"cancelled"}
    job = run_context["jobs"].get(created.job_id)
    assert job is not None
    assert job.status == "cancelled"


def test_run_repository_rejects_empty_batches_foreign_idempotency_and_missing_ids(
    run_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.runs import RunBatchSpec

    now = datetime(2026, 7, 13, tzinfo=UTC)
    with pytest.raises(ValueError, match="at least one batch"):
        run_context["runs"].create_with_job(
            plan_id="missing-plan",
            keyword_version_id="missing-keywords",
            months=("2026-07",),
            plan_snapshot={},
            keyword_snapshot={},
            connector_versions={},
            batches=(),
            job_payload={},
            idempotency_key=None,
            created_at=now,
        )
    with pytest.raises(KeyError):
        run_context["runs"].get("missing-run")
    with pytest.raises(KeyError):
        run_context["runs"].start_run("missing-run", now=now)
    with pytest.raises(KeyError):
        run_context["runs"].start_batch("missing-batch", now=now)

    plan = create_plan(
        run_context,
        sources=("arxiv",),
        start_month="2026-07",
        end_month="2026-07",
    )
    run_context["jobs"].create(
        kind="unrelated",
        payload={},
        idempotency_key="discovery.run:foreign-owner",
    )
    service = create_service(run_context, ())
    with pytest.raises(ValueError, match="another job kind"):
        service.create(plan.id, idempotency_key="foreign-owner", now=now)

    # A non-empty spec reaches the database boundary; the empty case above must fail first.
    assert RunBatchSpec(month="2026-07", connector_id="arxiv", query_fingerprint="x")


def test_confirmed_work_merge_redirects_existing_run_candidate_evidence(run_context) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import SourcePaper
    from paper_reader.discovery.runs import CandidateIngest

    plan = create_plan(
        run_context,
        sources=("arxiv",),
        start_month="2026-07",
        end_month="2026-07",
    )
    created = create_service(run_context, ()).create(plan.id, idempotency_key="merge-links")
    target = run_context["identities"].ingest(
        SourcePaper(
            connector_id="openreview",
            external_id="forum-a",
            title="Tool-Augmented Agents for Scientific Discovery",
            authors=("A. Chen", "R. Kumar"),
        )
    )
    candidate = run_context["identities"].ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2601.00002v1",
            title="Tool Augmented Agent for Scientific Discoveries",
            authors=("Alice Chen", "Ravi Kumar"),
        )
    )
    assert candidate.merge_review is not None
    run_context["runs"].record_candidates(
        run_id=created.id,
        batch_id=created.batches[0].id,
        candidates=(
            CandidateIngest(
                work_id=target.work.id,
                source_record_id=target.source_record.id,
                match_kind=target.match_kind,
                discovery_kind=target.discovery_kind,
            ),
            CandidateIngest(
                work_id=candidate.work.id,
                source_record_id=candidate.source_record.id,
                match_kind=candidate.match_kind,
                discovery_kind=candidate.discovery_kind,
            ),
        ),
        now=datetime(2026, 7, 13, tzinfo=UTC),
    )
    assert len(run_context["runs"].list_candidate_work_ids(created.id)) == 2

    run_context["identities"].decide_merge_review(
        candidate.merge_review.id,
        decision="merge",
    )

    assert run_context["runs"].list_candidate_work_ids(created.id) == (target.work.id,)


@pytest.mark.asyncio
async def test_disabled_and_crashing_connectors_are_isolated_as_nonretryable_failures(
    run_context,
) -> None:  # type: ignore[no-untyped-def]
    from dataclasses import replace

    from paper_reader.discovery.connectors import ConnectorCapabilities

    class DisabledConnector:
        capabilities = ConnectorCapabilities(
            connector_id="arxiv",
            display_name="Disabled",
            credential_requirement="none",
            supports_exact_updated_date=True,
            enabled=False,
            disabled_reason=None,
        )

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            raise AssertionError

    class CrashingConnector:
        capabilities = connector_capabilities("openreview")

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            raise RuntimeError("connector bug")

    plan = create_plan(
        run_context,
        sources=("arxiv", "openreview"),
        start_month="2026-07",
        end_month="2026-07",
    )
    service = create_service(run_context, (DisabledConnector(), CrashingConnector()))
    created = service.create(plan.id, idempotency_key="isolated-errors")
    empty_view = replace(created, batches=())
    cancelled_view = replace(
        created,
        batches=tuple(replace(batch, status="cancelled") for batch in created.batches),
    )
    assert service._final_status(empty_view, cancelled=False) == "failed"
    assert service._final_status(cancelled_view, cancelled=False) == "cancelled"
    with pytest.raises(KeyError):
        service._cancel_requested("missing-job")

    failed = await service.execute(created.id)
    repeated = await service.retry_failed(created.id)

    assert failed.status == "failed"
    assert repeated.status == "failed"
    by_source = {batch.connector_id: batch for batch in repeated.batches}
    assert by_source["arxiv"].error_code == "connector_disabled"
    assert by_source["openreview"].error_code == "connector_internal_error"
    assert by_source["arxiv"].attempt_count == 0
    assert by_source["openreview"].attempt_count == 1


@pytest.mark.asyncio
async def test_duplicate_source_page_is_ingested_once_and_terminal_run_is_idempotent(
    run_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.connectors import SourcePage
    from paper_reader.discovery.identity import SourcePaper

    paper = SourcePaper(
        connector_id="arxiv",
        external_id="2607.77777v1",
        title="Reliable AI agent planning",
        authors=("Alice",),
        updated_at=datetime(2026, 7, 10, tzinfo=UTC),
    )

    class DuplicateConnector:
        capabilities = connector_capabilities("arxiv")

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            return SourcePage(records=(paper, paper), next_cursor=None, total=2)

    plan = create_plan(
        run_context,
        sources=("arxiv",),
        start_month="2026-07",
        end_month="2026-07",
    )
    service = create_service(run_context, (DuplicateConnector(),))
    created = service.create(plan.id, idempotency_key="duplicate-page")

    completed = await service.execute(created.id)
    terminal_repeat = await service.execute(created.id)

    assert completed.status == "succeeded"
    assert terminal_repeat.id == completed.id
    assert completed.batches[0].accepted_count == 2
    assert completed.batches[0].ingested_count == 1
    assert len(run_context["runs"].list_candidates(created.id)) == 1


@pytest.mark.asyncio
async def test_cancellation_requested_during_fetch_discards_page_and_cancels_run(
    run_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.connectors import SourcePage

    class CancellingConnector:
        capabilities = connector_capabilities("arxiv")

        def __init__(self) -> None:
            self.job_id = ""

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            run_context["jobs"].request_cancel(self.job_id)
            return SourcePage(records=(), next_cursor=None, total=0)

    connector = CancellingConnector()
    plan = create_plan(
        run_context,
        sources=("arxiv",),
        start_month="2026-07",
        end_month="2026-07",
    )
    service = create_service(run_context, (connector,))
    created = service.create(plan.id, idempotency_key="cancel-during-fetch")
    connector.job_id = created.job_id

    cancelled = await service.execute(created.id)

    assert cancelled.status == "cancelled"
    assert cancelled.batches[0].status == "cancelled"
    assert cancelled.batches[0].fetched_count == 0


@pytest.mark.asyncio
async def test_new_paper_quota_scans_past_duplicates_and_keeps_updates_outside_quota(
    run_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.connectors import SourcePage
    from paper_reader.discovery.identity import SourcePaper

    unchanged = SourcePaper(
        connector_id="arxiv",
        external_id="2607.10001",
        title="Reliable AI agent memory",
        authors=("Alice",),
        updated_at=datetime(2026, 7, 10, tzinfo=UTC),
    )
    metadata_before = SourcePaper(
        connector_id="arxiv",
        external_id="2607.10002",
        title="Reliable AI agent retrieval",
        authors=("Bob",),
        updated_at=datetime(2026, 7, 10, tzinfo=UTC),
        landing_url="https://arxiv.org/abs/2607.10002",
    )
    important_before = SourcePaper(
        connector_id="arxiv",
        external_id="2607.10003",
        title="Reliable AI agent evaluation",
        authors=("Carol",),
        abstract="Initial evidence.",
        updated_at=datetime(2026, 7, 10, tzinfo=UTC),
    )
    prior_cross_source = SourcePaper(
        connector_id="openreview",
        external_id="forum-cross-source",
        title="Reliable AI agent planning",
        authors=("Dan",),
        doi="10.1000/quota-cross-source",
        updated_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    for paper in (unchanged, metadata_before, important_before, prior_cross_source):
        run_context["identities"].ingest(paper)

    first_page = (
        unchanged,
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.10002",
            title="Reliable AI agent retrieval",
            authors=("Bob",),
            updated_at=datetime(2026, 7, 10, tzinfo=UTC),
            landing_url="https://export.arxiv.org/abs/2607.10002",
        ),
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.10003",
            title="Reliable AI agent evaluation",
            authors=("Carol",),
            abstract="Expanded evaluation evidence.",
            updated_at=datetime(2026, 7, 10, tzinfo=UTC),
        ),
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.10004",
            title="Reliable AI agent planning",
            authors=("Dan",),
            doi="10.1000/quota-cross-source",
            updated_at=datetime(2026, 7, 11, tzinfo=UTC),
        ),
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.10005",
            title="Reliable AI agent verification",
            authors=("Eve",),
            updated_at=datetime(2026, 7, 12, tzinfo=UTC),
        ),
    )
    second_page = (
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.10006",
            title="Reliable AI agent observability",
            authors=("Frank",),
            updated_at=datetime(2026, 7, 13, tzinfo=UTC),
        ),
    )

    class MixedConnector:
        capabilities = connector_capabilities("arxiv")

        def __init__(self) -> None:
            self.calls: list[tuple[str | None, int]] = []

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            self.calls.append((cursor, limit))
            if cursor is None:
                return SourcePage(records=first_page, next_cursor="page-2", total=6)
            assert cursor == "page-2"
            return SourcePage(records=second_page, next_cursor="unused", total=6)

    connector = MixedConnector()
    plan = create_plan(
        run_context,
        sources=("arxiv",),
        start_month="2026-07",
        end_month="2026-07",
        priority_limit=1,
        candidate_limit=2,
    )
    service = create_service(run_context, (connector,))
    created = service.create(plan.id, idempotency_key="quota-classification")

    assert created.plan_snapshot["quota"]["scan_limit"] == 50
    completed = await service.execute(created.id)

    assert completed.status == "succeeded"
    assert len(connector.calls) == 2
    batch = completed.batches[0]
    assert batch.new_work_count == 2
    assert batch.update_count == 2
    assert batch.duplicate_count == 2
    assert batch.scanned_count == 6
    assert batch.fetched_count == 6
    assert batch.accepted_count == 6
    assert batch.ingested_count == 3
    assert batch.checkpoint["stop_reason"] == "new_work_quota"
    assert Counter(
        candidate.discovery_kind for candidate in run_context["runs"].list_candidates(created.id)
    ) == Counter(
        {
            "new_work": 2,
            "metadata_refresh": 1,
            "important_revision": 1,
            "existing_work_new_source": 1,
            "unchanged": 1,
        }
    )


@pytest.mark.asyncio
async def test_same_work_from_second_source_does_not_consume_that_batch_new_quota(
    run_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.connectors import SourcePage
    from paper_reader.discovery.identity import SourcePaper

    arxiv_paper = SourcePaper(
        connector_id="arxiv",
        external_id="2607.20001",
        title="Reliable AI agent benchmarks",
        authors=("Alice",),
        doi="10.1000/cross-batch-quota",
        updated_at=datetime(2026, 7, 10, tzinfo=UTC),
    )
    ieee_alias = SourcePaper(
        connector_id="ieee",
        external_id="20001",
        title="Reliable AI agent benchmarks",
        authors=("Alice",),
        doi="10.1000/cross-batch-quota",
        updated_at=datetime(2026, 7, 11, tzinfo=UTC),
    )
    ieee_new = SourcePaper(
        connector_id="ieee",
        external_id="20002",
        title="Reliable AI agent safeguards",
        authors=("Bob",),
        updated_at=datetime(2026, 7, 12, tzinfo=UTC),
    )

    class ArxivConnector:
        capabilities = connector_capabilities("arxiv")

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            assert cursor is None
            return SourcePage(records=(arxiv_paper,), next_cursor=None, total=1)

    class IeeeConnector:
        capabilities = connector_capabilities("ieee")

        def __init__(self) -> None:
            self.calls = 0

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            self.calls += 1
            if cursor is None:
                return SourcePage(records=(ieee_alias,), next_cursor="next", total=2)
            return SourcePage(records=(ieee_new,), next_cursor=None, total=2)

    ieee = IeeeConnector()
    plan = create_plan(
        run_context,
        sources=("arxiv", "ieee"),
        start_month="2026-07",
        end_month="2026-07",
        priority_limit=1,
        candidate_limit=1,
    )
    service = create_service(run_context, (ArxivConnector(), ieee))
    completed = await service.execute(
        service.create(plan.id, idempotency_key="cross-source-quota").id
    )

    assert completed.status == "succeeded"
    assert ieee.calls == 2
    by_source = {batch.connector_id: batch for batch in completed.batches}
    assert by_source["arxiv"].new_work_count == 1
    assert by_source["arxiv"].duplicate_count == 0
    assert by_source["ieee"].new_work_count == 1
    assert by_source["ieee"].duplicate_count == 1
    assert by_source["ieee"].scanned_count == 2
    assert len(run_context["runs"].list_candidate_work_ids(completed.id)) == 2


@pytest.mark.asyncio
async def test_closed_scan_limit_stops_duplicate_only_connector_pages(
    run_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.connectors import SourcePage
    from paper_reader.discovery.identity import SourcePaper

    duplicate = SourcePaper(
        connector_id="arxiv",
        external_id="2607.30001",
        title="Reliable AI agent duplicate stream",
        authors=("Alice",),
        updated_at=datetime(2026, 7, 10, tzinfo=UTC),
    )
    run_context["identities"].ingest(duplicate)

    class DuplicateStreamConnector:
        capabilities = connector_capabilities("arxiv")

        def __init__(self) -> None:
            self.calls = 0

        async def estimate(self, query):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
            self.calls += 1
            assert limit == 60 - (self.calls * 10)
            return SourcePage(
                records=(duplicate,) * 10,
                next_cursor=str(self.calls),
                total=None,
            )

    connector = DuplicateStreamConnector()
    plan = create_plan(
        run_context,
        sources=("arxiv",),
        start_month="2026-07",
        end_month="2026-07",
        priority_limit=1,
        candidate_limit=2,
    )
    service = create_service(run_context, (connector,))
    completed = await service.execute(
        service.create(plan.id, idempotency_key="closed-scan-limit").id
    )

    assert completed.status == "succeeded"
    assert connector.calls == 5
    batch = completed.batches[0]
    assert batch.new_work_count == 0
    assert batch.update_count == 0
    assert batch.duplicate_count == 50
    assert batch.scanned_count == 50
    assert batch.checkpoint["stop_reason"] == "scan_limit"
    assert len(run_context["runs"].list_candidates(completed.id)) == 1
