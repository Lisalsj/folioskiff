"""Transactional state machine for the first-run workflow."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from paper_reader.onboarding.contracts import (
    ONBOARDING_STEPS,
    OnboardingSnapshot,
    OnboardingStep,
    OnboardingStepStatus,
    OnboardingStepUpdate,
)
from paper_reader.onboarding.models import DEFAULT_STEP_STATUSES_JSON, OnboardingStateRecord


class OnboardingStateError(RuntimeError):
    """Persisted onboarding state violates its closed contract."""


class OnboardingTransitionError(ValueError):
    """A requested transition is invalid for the current step."""


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _canonical_statuses(statuses: dict[OnboardingStep, OnboardingStepStatus]) -> str:
    return json.dumps(
        {step.value: statuses[step].value for step in ONBOARDING_STEPS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_statuses(payload: str) -> dict[OnboardingStep, OnboardingStepStatus]:
    try:
        raw = json.loads(payload)
        expected = {step.value for step in ONBOARDING_STEPS}
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ValueError("step keys are invalid")
        return {
            step: OnboardingStepStatus(raw[step.value])
            for step in ONBOARDING_STEPS
        }
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise OnboardingStateError("persisted onboarding steps are invalid") from error


def _current_step(
    statuses: dict[OnboardingStep, OnboardingStepStatus],
) -> OnboardingStep:
    for step in ONBOARDING_STEPS:
        if statuses[step] is OnboardingStepStatus.PENDING:
            return step
    return OnboardingStep.COMPLETE


class OnboardingRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    def snapshot(self) -> OnboardingSnapshot:
        with self._sessions() as session:
            record = self._required(session)
            session.commit()
            session.refresh(record)
            return self._snapshot(record)

    def save_step(self, update: OnboardingStepUpdate) -> OnboardingSnapshot:
        if update.step is OnboardingStep.COMPLETE:
            raise OnboardingTransitionError("complete is derived and cannot be written")
        if update.status is OnboardingStepStatus.PENDING:
            raise OnboardingTransitionError("pending is not a writable step status")
        if (
            update.step is OnboardingStep.STORAGE_PRIVACY
            and update.status is OnboardingStepStatus.SKIPPED
        ):
            raise OnboardingTransitionError("storage and privacy cannot be skipped")

        with self._sessions() as session:
            record = self._required(session)
            statuses = _parse_statuses(record.step_statuses)
            current = _current_step(statuses)
            stored_current = self._stored_step(record.current_step)
            if stored_current is not current:
                raise OnboardingStateError("persisted onboarding current step is inconsistent")

            previous = statuses[update.step]
            if previous is OnboardingStepStatus.PENDING and update.step is not current:
                raise OnboardingTransitionError("only the current onboarding step may advance")
            if previous is update.status:
                session.commit()
                session.refresh(record)
                return self._snapshot(record)

            now = self._now()
            statuses[update.step] = update.status
            next_step = _current_step(statuses)
            record.step_statuses = _canonical_statuses(statuses)
            record.current_step = next_step.value
            if update.step is OnboardingStep.STORAGE_PRIVACY:
                record.privacy_acknowledged_at = now
            if next_step is OnboardingStep.COMPLETE and record.completed_at is None:
                record.completed_at = now
            record.updated_at = now
            session.commit()
            session.refresh(record)
            return self._snapshot(record)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise OnboardingStateError("onboarding clock must include a timezone")
        return value.astimezone(UTC)

    @staticmethod
    def _required(session: Session) -> OnboardingStateRecord:
        record = session.get(OnboardingStateRecord, "default")
        if record is None:
            record = OnboardingStateRecord(
                id="default",
                current_step=OnboardingStep.STORAGE_PRIVACY.value,
                step_statuses=DEFAULT_STEP_STATUSES_JSON,
            )
            session.add(record)
            session.flush()
        return record

    @staticmethod
    def _stored_step(value: str) -> OnboardingStep:
        try:
            return OnboardingStep(value)
        except ValueError as error:
            raise OnboardingStateError("persisted onboarding current step is invalid") from error

    def _snapshot(self, record: OnboardingStateRecord) -> OnboardingSnapshot:
        statuses = _parse_statuses(record.step_statuses)
        current = _current_step(statuses)
        if self._stored_step(record.current_step) is not current:
            raise OnboardingStateError("persisted onboarding current step is inconsistent")
        if current is OnboardingStep.COMPLETE and record.completed_at is None:
            raise OnboardingStateError("completed onboarding state lacks a timestamp")
        if (
            statuses[OnboardingStep.STORAGE_PRIVACY]
            is OnboardingStepStatus.COMPLETED
            and record.privacy_acknowledged_at is None
        ):
            raise OnboardingStateError("storage privacy acknowledgement is missing")
        return OnboardingSnapshot(
            current_step=current,
            completed=current is OnboardingStep.COMPLETE,
            steps=statuses,
            privacy_acknowledged_at=(
                _aware_utc(record.privacy_acknowledged_at)
                if record.privacy_acknowledged_at is not None
                else None
            ),
            updated_at=_aware_utc(record.updated_at),
        )


__all__ = [
    "OnboardingRepository",
    "OnboardingStateError",
    "OnboardingTransitionError",
]
