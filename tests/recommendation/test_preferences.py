from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DatabaseError, IntegrityError


@pytest.fixture
def preference_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.recommendation.preferences import PreferenceRepository

    engine = create_sqlite_engine(tmp_path / "preferences.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    identity = IdentityRepository(sessions)
    first = identity.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.00001",
            title="Reliable Paper Reading Agents",
            authors=("Alice",),
        )
    ).work
    second = identity.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.00002",
            title="Private Preference Learning",
            authors=("Bob",),
        )
    ).work
    try:
        yield sessions, PreferenceRepository(sessions), first, second
    finally:
        engine.dispose()


def test_library_state_keeps_explicit_fields_independent_and_marks_history(
    preference_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.preferences import (
        AnalysisState,
        LibraryFeedback,
        LibraryStateInput,
    )

    _, repository, work, _ = preference_context
    untouched = repository.get_library_state(work.id)
    saved = repository.set_library_state(
        work.id,
        LibraryStateInput(
            favorite=True,
            feedback=LibraryFeedback.LIKE,
            feedback_reasons=("method_matches_interest",),
            pinned=False,
            reading_progress=35,
            analysis_state=AnalysisState.NOT_ANALYZED,
        ),
    )

    assert untouched.favorite is False
    assert untouched.feedback is LibraryFeedback.NEUTRAL
    assert untouched.persisted is False
    assert untouched.historical_match is False
    assert saved.favorite is True
    assert saved.feedback is LibraryFeedback.LIKE
    assert saved.feedback_reasons == ("method_matches_interest",)
    assert saved.pinned is False
    assert saved.reading_progress == 35
    assert saved.analysis_state is AnalysisState.NOT_ANALYZED
    assert saved.persisted is True
    assert saved.historical_match is True
    assert repository.get_library_state(work.id) == saved


def test_library_state_validation_rejects_ambiguous_feedback() -> None:
    from paper_reader.recommendation.preferences import (
        AnalysisState,
        LibraryFeedback,
        LibraryStateInput,
    )

    with pytest.raises(ValueError, match="neutral feedback"):
        LibraryStateInput(
            favorite=False,
            feedback=LibraryFeedback.NEUTRAL,
            feedback_reasons=("should_not_exist",),
            pinned=False,
            reading_progress=0,
            analysis_state=AnalysisState.NOT_ANALYZED,
        )
    with pytest.raises(ValueError, match="reading_progress"):
        LibraryStateInput(
            favorite=False,
            feedback=LibraryFeedback.DISLIKE,
            feedback_reasons=(),
            pinned=False,
            reading_progress=101,
            analysis_state=AnalysisState.NOT_ANALYZED,
        )


def test_interested_authors_are_normalized_idempotent_and_removable(
    preference_context,
) -> None:  # type: ignore[no-untyped-def]
    _, repository, _, _ = preference_context

    first = repository.set_interested_author(
        author_key=" OpenAlex:A123 ",
        display_name=" Alice  Researcher ",
        active=True,
    )
    duplicate = repository.set_interested_author(
        author_key="openalex:a123",
        display_name="Alice Researcher",
        active=True,
    )

    assert first.author_key == "openalex:a123"
    assert first.display_name == "Alice Researcher"
    assert duplicate.author_key == first.author_key
    assert repository.list_interested_authors() == (duplicate,)

    removed = repository.set_interested_author(
        author_key="openalex:a123",
        display_name="Alice Researcher",
        active=False,
    )
    assert removed.active is False
    assert repository.list_interested_authors() == ()


def test_implicit_events_accept_only_whitelisted_bucketed_payloads(
    preference_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.preferences import (
        ImplicitEventInput,
        ImplicitEventType,
        ReadingDurationBucket,
    )

    _, repository, work, _ = preference_context
    occurred_at = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    event = repository.record_implicit_event(
        work.id,
        ImplicitEventInput(
            event_key="browser-event-1",
            event_type=ImplicitEventType.READING_DURATION,
            duration_bucket=ReadingDurationBucket.TWO_TO_TEN_MINUTES,
            occurred_at=occurred_at,
        ),
    )
    duplicate = repository.record_implicit_event(
        work.id,
        ImplicitEventInput(
            event_key="browser-event-1",
            event_type=ImplicitEventType.READING_DURATION,
            duration_bucket=ReadingDurationBucket.TWO_TO_TEN_MINUTES,
            occurred_at=occurred_at + timedelta(seconds=10),
        ),
    )

    assert duplicate.id == event.id
    assert event.event_type is ImplicitEventType.READING_DURATION
    assert event.duration_bucket is ReadingDurationBucket.TWO_TO_TEN_MINUTES
    assert event.weight == 2
    assert repository.list_raw_events(work.id) == (event,)

    with pytest.raises(ValueError):
        ImplicitEventType("mouse_move")
    with pytest.raises(ValueError, match="requires a duration bucket"):
        ImplicitEventInput(
            event_key="invalid-duration",
            event_type=ImplicitEventType.READING_DURATION,
            duration_bucket=None,
            occurred_at=occurred_at,
        )
    with pytest.raises(ValueError, match="only valid for reading duration"):
        ImplicitEventInput(
            event_key="invalid-pdf",
            event_type=ImplicitEventType.PDF_OPEN,
            duration_bucket=ReadingDurationBucket.UNDER_THIRTY_SECONDS,
            occurred_at=occurred_at,
        )


def test_reused_event_key_with_different_meaning_is_rejected(
    preference_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.preferences import (
        ImplicitEventInput,
        ImplicitEventType,
    )

    _, repository, first, second = preference_context
    occurred_at = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    repository.record_implicit_event(
        first.id,
        ImplicitEventInput(
            event_key="shared-event-key",
            event_type=ImplicitEventType.DETAIL_OPEN,
            occurred_at=occurred_at,
        ),
    )

    with pytest.raises(ValueError, match="event_key conflict"):
        repository.record_implicit_event(
            second.id,
            ImplicitEventInput(
                event_key="shared-event-key",
                event_type=ImplicitEventType.PDF_OPEN,
                occurred_at=occurred_at,
            ),
        )


def test_expired_raw_events_become_irreversible_weights_once(
    preference_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.preferences import (
        ImplicitEventInput,
        ImplicitEventType,
        ReadingDurationBucket,
    )

    _, repository, work, _ = preference_context
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    cutoff = now - timedelta(days=180)
    inputs = (
        ImplicitEventInput(
            event_key="expired-detail",
            event_type=ImplicitEventType.DETAIL_OPEN,
            occurred_at=cutoff - timedelta(seconds=1),
        ),
        ImplicitEventInput(
            event_key="expired-read",
            event_type=ImplicitEventType.READING_DURATION,
            duration_bucket=ReadingDurationBucket.OVER_TEN_MINUTES,
            occurred_at=cutoff - timedelta(days=10),
        ),
        ImplicitEventInput(
            event_key="boundary-pdf",
            event_type=ImplicitEventType.PDF_OPEN,
            occurred_at=cutoff,
        ),
    )
    for payload in inputs:
        repository.record_implicit_event(work.id, payload)

    first = repository.aggregate_expired_events(now=now)
    second = repository.aggregate_expired_events(now=now)

    assert first.event_count == 2
    assert first.work_count == 1
    assert first.cutoff == cutoff
    assert second.event_count == 0
    assert repository.get_aggregated_weight(work.id) == 3.5
    raw = repository.list_raw_events(work.id)
    assert [event.event_key for event in raw] == ["boundary-pdf"]
    assert repository.get_total_implicit_weight(work.id) == 4.5


def test_database_constraints_protect_preference_event_semantics(
    preference_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import (
        ImplicitPreferenceEventRecord,
        LibraryStateRecord,
    )

    sessions, _, work, _ = preference_context
    with sessions() as session, pytest.raises(IntegrityError):
        session.add(
            ImplicitPreferenceEventRecord(
                work_id=work.id,
                event_key="invalid-db-event",
                event_type="pdf_open",
                duration_bucket="2m_10m",
                weight=1,
                occurred_at=datetime(2026, 7, 13, tzinfo=UTC),
            )
        )
        session.commit()

    with sessions() as session:
        session.add(
            ImplicitPreferenceEventRecord(
                work_id=work.id,
                event_key="immutable-db-event",
                event_type="pdf_open",
                duration_bucket=None,
                weight=1,
                occurred_at=datetime(2026, 7, 13, tzinfo=UTC),
            )
        )
        session.commit()
    with sessions() as session, pytest.raises(DatabaseError, match="immutable"):
        session.execute(
            update(ImplicitPreferenceEventRecord)
            .where(ImplicitPreferenceEventRecord.event_key == "immutable-db-event")
            .values(weight=5)
        )
        session.commit()

    with sessions() as session, pytest.raises(IntegrityError):
        session.add(
            LibraryStateRecord(
                work_id=work.id,
                favorite=False,
                feedback="neutral",
                feedback_reasons=[],
                pinned=False,
                reading_progress=101,
                analysis_state="not_analyzed",
            )
        )
        session.commit()


def test_work_merge_preserves_and_conservatively_combines_preferences(tmp_path: Path) -> None:
    from sqlalchemy import func, select

    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository
    from paper_reader.discovery.models import (
        ImplicitPreferenceEventRecord,
        LibraryStateRecord,
        PreferenceAggregateRecord,
    )
    from paper_reader.migrations import upgrade_database
    from paper_reader.recommendation.preferences import (
        AnalysisState,
        ImplicitEventInput,
        ImplicitEventType,
        LibraryFeedback,
        LibraryStateInput,
        PreferenceRepository,
    )
    from tests.discovery.test_merge_reviews import create_ambiguous_pair

    database = tmp_path / "merge-preferences.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    identities = IdentityRepository(sessions)
    preferences = PreferenceRepository(sessions)
    existing, candidate_work = create_ambiguous_pair(identities)
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=181)

    preferences.set_library_state(
        existing.work.id,
        LibraryStateInput(
            favorite=False,
            feedback=LibraryFeedback.LIKE,
            feedback_reasons=("useful_method",),
            pinned=True,
            reading_progress=20,
            analysis_state=AnalysisState.FAILED,
        ),
    )
    preferences.set_library_state(
        candidate_work.work.id,
        LibraryStateInput(
            favorite=True,
            feedback=LibraryFeedback.DISLIKE,
            feedback_reasons=("wrong_scope",),
            pinned=False,
            reading_progress=80,
            analysis_state=AnalysisState.AVAILABLE,
        ),
    )
    for work_id, key in (
        (existing.work.id, "old-target"),
        (candidate_work.work.id, "old-candidate"),
    ):
        preferences.record_implicit_event(
            work_id,
            ImplicitEventInput(
                event_key=key,
                event_type=ImplicitEventType.PDF_OPEN,
                occurred_at=old,
            ),
        )
    preferences.aggregate_expired_events(now=now)
    preferences.record_implicit_event(
        candidate_work.work.id,
        ImplicitEventInput(
            event_key="current-candidate",
            event_type=ImplicitEventType.DETAIL_OPEN,
            occurred_at=now,
        ),
    )

    identities.decide_merge_review(candidate_work.merge_review.id, decision="merge")

    merged = preferences.get_library_state(existing.work.id)
    assert merged.favorite is True
    assert merged.pinned is True
    assert merged.feedback is LibraryFeedback.DISLIKE
    assert merged.feedback_reasons == ("wrong_scope",)
    assert merged.reading_progress == 80
    assert merged.analysis_state is AnalysisState.AVAILABLE
    assert preferences.get_aggregated_weight(existing.work.id) == 2
    assert [event.event_key for event in preferences.list_raw_events(existing.work.id)] == [
        "current-candidate"
    ]
    with sessions() as session:
        assert session.get(LibraryStateRecord, candidate_work.work.id) is None
        assert session.get(PreferenceAggregateRecord, candidate_work.work.id) is None
        candidate_events = session.scalar(
            select(func.count())
            .select_from(ImplicitPreferenceEventRecord)
            .where(ImplicitPreferenceEventRecord.work_id == candidate_work.work.id)
        )
    assert candidate_events == 0
    engine.dispose()


def test_work_merge_reparents_candidate_only_preference_rows(tmp_path: Path) -> None:
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository
    from paper_reader.migrations import upgrade_database
    from paper_reader.recommendation.preferences import (
        AnalysisState,
        ImplicitEventInput,
        ImplicitEventType,
        LibraryFeedback,
        LibraryStateInput,
        PreferenceRepository,
    )
    from tests.discovery.test_merge_reviews import create_ambiguous_pair

    database = tmp_path / "merge-candidate-preferences.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    identities = IdentityRepository(sessions)
    preferences = PreferenceRepository(sessions)
    existing, candidate_work = create_ambiguous_pair(identities)
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    preferences.set_library_state(
        candidate_work.work.id,
        LibraryStateInput(
            favorite=True,
            feedback=LibraryFeedback.LIKE,
            feedback_reasons=("candidate_only",),
            pinned=False,
            reading_progress=10,
            analysis_state=AnalysisState.NOT_ANALYZED,
        ),
    )
    preferences.record_implicit_event(
        candidate_work.work.id,
        ImplicitEventInput(
            event_key="candidate-only-old",
            event_type=ImplicitEventType.PDF_OPEN,
            occurred_at=now - timedelta(days=181),
        ),
    )
    preferences.aggregate_expired_events(now=now)

    identities.decide_merge_review(candidate_work.merge_review.id, decision="merge")

    assert preferences.get_library_state(existing.work.id).favorite is True
    assert preferences.get_aggregated_weight(existing.work.id) == 1
    engine.dispose()


def test_preference_score_uses_neutral_prior_when_no_signal_exists() -> None:
    from paper_reader.recommendation.preferences import (
        AnalysisState,
        LibraryFeedback,
        LibraryState,
        score_preference,
    )

    result = score_preference(
        LibraryState(
            work_id="paper",
            favorite=False,
            feedback=LibraryFeedback.NEUTRAL,
            feedback_reasons=(),
            pinned=False,
            reading_progress=0,
            analysis_state=AnalysisState.NOT_ANALYZED,
            persisted=False,
            created_at=None,
            updated_at=None,
        ),
        work_authors=("Alice Chen",),
        interested_author_keys=(),
        implicit_weight=0,
    )

    assert result.rule_version == "preference-v1"
    assert result.score == 50
    assert result.coverage == 0
    assert result.confidence == 0
    assert result.suppressed_by_default is False
    assert result.historical_match is False


def test_preference_score_combines_explicit_author_and_bounded_behavior_signals() -> None:
    from paper_reader.recommendation.preferences import (
        AnalysisState,
        LibraryFeedback,
        LibraryState,
        score_preference,
    )

    result = score_preference(
        LibraryState(
            work_id="paper",
            favorite=True,
            feedback=LibraryFeedback.LIKE,
            feedback_reasons=("method_matches_interest",),
            pinned=False,
            reading_progress=75,
            analysis_state=AnalysisState.AVAILABLE,
            persisted=True,
            created_at=datetime(2026, 7, 1, tzinfo=UTC),
            updated_at=datetime(2026, 7, 13, tzinfo=UTC),
        ),
        work_authors=("ALICE  CHEN", "Other Author"),
        interested_author_keys=("alice chen",),
        implicit_weight=999,
    )

    assert result.score == 100
    assert result.coverage == 100
    assert result.confidence == 100
    assert result.suppressed_by_default is False
    assert result.historical_match is True
    assert result.matched_author_keys == ("alice chen",)
    assert result.behavior_boost == 10


def test_dislike_is_a_hard_default_suppression_not_a_low_coverage_penalty() -> None:
    from paper_reader.recommendation.preferences import (
        AnalysisState,
        LibraryFeedback,
        LibraryState,
        score_preference,
    )

    result = score_preference(
        LibraryState(
            work_id="paper",
            favorite=False,
            feedback=LibraryFeedback.DISLIKE,
            feedback_reasons=("wrong_scope",),
            pinned=False,
            reading_progress=0,
            analysis_state=AnalysisState.NOT_ANALYZED,
            persisted=True,
            created_at=datetime(2026, 7, 1, tzinfo=UTC),
            updated_at=datetime(2026, 7, 13, tzinfo=UTC),
        ),
        work_authors=(),
        interested_author_keys=(),
        implicit_weight=50,
    )

    assert result.score == 0
    assert result.suppressed_by_default is True
    assert result.historical_match is False
    assert "wrong_scope" in result.evidence["feedback_reasons"]


def test_preference_score_ignores_malformed_source_author_names() -> None:
    from paper_reader.recommendation.preferences import (
        AnalysisState,
        LibraryFeedback,
        LibraryState,
        score_preference,
    )

    result = score_preference(
        LibraryState(
            work_id="paper",
            favorite=False,
            feedback=LibraryFeedback.NEUTRAL,
            feedback_reasons=(),
            pinned=False,
            reading_progress=0,
            analysis_state=AnalysisState.NOT_ANALYZED,
            persisted=False,
            created_at=None,
            updated_at=None,
        ),
        work_authors=("", "x" * 256, "Alice Chen"),
        interested_author_keys=("alice chen",),
        implicit_weight=0,
    )

    assert result.matched_author_keys == ("alice chen",)
    assert result.score == 60
