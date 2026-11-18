from __future__ import annotations

from pathlib import Path

import pytest

from paper_reader.database import create_session_factory, create_sqlite_engine
from paper_reader.migrations import upgrade_database
from paper_reader.onboarding.contracts import (
    OnboardingStep,
    OnboardingStepStatus,
    OnboardingStepUpdate,
)
from paper_reader.onboarding.repository import (
    OnboardingRepository,
    OnboardingTransitionError,
)


def _repository(tmp_path: Path) -> OnboardingRepository:
    database = tmp_path / "state" / "paper-reader.sqlite3"
    upgrade_database(database)
    return OnboardingRepository(
        create_session_factory(create_sqlite_engine(database)),
    )


def _save(
    repository: OnboardingRepository,
    step: OnboardingStep,
    status: OnboardingStepStatus,
):  # type: ignore[no-untyped-def]
    return repository.save_step(OnboardingStepUpdate(step=step, status=status))


def test_onboarding_advances_in_order_and_allows_optional_skips(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    initial = repository.snapshot()
    storage = _save(
        repository,
        OnboardingStep.STORAGE_PRIVACY,
        OnboardingStepStatus.COMPLETED,
    )
    agents = _save(
        repository,
        OnboardingStep.AGENTS,
        OnboardingStepStatus.SKIPPED,
    )
    sources = _save(
        repository,
        OnboardingStep.SOURCES,
        OnboardingStepStatus.COMPLETED,
    )
    complete = _save(
        repository,
        OnboardingStep.READING_PREFERENCES,
        OnboardingStepStatus.SKIPPED,
    )

    assert initial.current_step is OnboardingStep.STORAGE_PRIVACY
    assert initial.completed is False
    assert storage.current_step is OnboardingStep.AGENTS
    assert storage.privacy_acknowledged_at is not None
    assert agents.current_step is OnboardingStep.SOURCES
    assert agents.steps[OnboardingStep.AGENTS] is OnboardingStepStatus.SKIPPED
    assert sources.current_step is OnboardingStep.READING_PREFERENCES
    assert complete.current_step is OnboardingStep.COMPLETE
    assert complete.completed is True


def test_storage_step_cannot_be_skipped_or_bypassed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(OnboardingTransitionError, match="storage"):
        _save(
            repository,
            OnboardingStep.STORAGE_PRIVACY,
            OnboardingStepStatus.SKIPPED,
        )
    with pytest.raises(OnboardingTransitionError, match="current"):
        _save(
            repository,
            OnboardingStep.AGENTS,
            OnboardingStepStatus.COMPLETED,
        )


def test_repeated_updates_are_idempotent_and_completed_optional_steps_can_reopen(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _save(
        repository,
        OnboardingStep.STORAGE_PRIVACY,
        OnboardingStepStatus.COMPLETED,
    )
    first_agents = _save(
        repository,
        OnboardingStep.AGENTS,
        OnboardingStepStatus.SKIPPED,
    )
    repeated_agents = _save(
        repository,
        OnboardingStep.AGENTS,
        OnboardingStepStatus.SKIPPED,
    )
    _save(
        repository,
        OnboardingStep.SOURCES,
        OnboardingStepStatus.COMPLETED,
    )
    _save(
        repository,
        OnboardingStep.READING_PREFERENCES,
        OnboardingStepStatus.COMPLETED,
    )

    reopened = _save(
        repository,
        OnboardingStep.AGENTS,
        OnboardingStepStatus.COMPLETED,
    )

    assert repeated_agents.updated_at == first_agents.updated_at
    assert reopened.completed is True
    assert reopened.current_step is OnboardingStep.COMPLETE
    assert reopened.steps[OnboardingStep.SOURCES] is OnboardingStepStatus.COMPLETED
    assert (
        reopened.steps[OnboardingStep.READING_PREFERENCES]
        is OnboardingStepStatus.COMPLETED
    )


def test_pending_and_derived_complete_are_not_user_writable(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(OnboardingTransitionError, match="pending"):
        _save(
            repository,
            OnboardingStep.STORAGE_PRIVACY,
            OnboardingStepStatus.PENDING,
        )
    with pytest.raises(OnboardingTransitionError, match="derived"):
        _save(
            repository,
            OnboardingStep.COMPLETE,
            OnboardingStepStatus.COMPLETED,
        )
