"""SQLAlchemy persistence model for onboarding state."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from paper_reader.database import Base

DEFAULT_STEP_STATUSES_JSON = (
    '{"agents":"pending","reading_preferences":"pending",'
    '"sources":"pending","storage_privacy":"pending"}'
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OnboardingStateRecord(Base):
    __tablename__ = "onboarding_state"
    __table_args__ = (
        CheckConstraint("id = 'default'", name="ck_onboarding_state_singleton"),
        CheckConstraint(
            "current_step IN ('storage_privacy', 'agents', 'sources', "
            "'reading_preferences', 'complete')",
            name="ck_onboarding_state_current_step",
        ),
        CheckConstraint(
            "json_valid(step_statuses)",
            name="ck_onboarding_state_statuses_json",
        ),
        CheckConstraint(
            "(current_step = 'complete' AND completed_at IS NOT NULL) OR "
            "(current_step <> 'complete' AND completed_at IS NULL)",
            name="ck_onboarding_state_completion",
        ),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="default")
    current_step: Mapped[str] = mapped_column(String(32), default="storage_privacy")
    step_statuses: Mapped[str] = mapped_column(Text, default=DEFAULT_STEP_STATUSES_JSON)
    privacy_acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
    )


__all__ = ["DEFAULT_STEP_STATUSES_JSON", "OnboardingStateRecord"]
