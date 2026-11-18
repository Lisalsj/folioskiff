"""Durable public metadata for application update checks."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from paper_reader.database import Base
from paper_reader.jobs.models import utc_now


class UpdateSettingsRecord(Base):
    __tablename__ = "update_settings"
    __table_args__ = (
        CheckConstraint("id = 'default'", name="ck_update_settings_singleton"),
        CheckConstraint(
            "last_status IN ("
            "'never_checked', 'up_to_date', 'update_available', 'server_older', "
            "'prerelease', 'draft', 'rate_limited', 'offline', "
            "'invalid_response', 'service_unavailable'"
            ")",
            name="ck_update_settings_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="default")
    automatic_checks_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(32), default="never_checked")
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cached_release_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
