"""Durable lifecycle records for optional model revisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from paper_reader.database import Base
from paper_reader.jobs.models import utc_now


class ModelAssetRecord(Base):
    __tablename__ = "model_assets"
    __table_args__ = (
        UniqueConstraint("bundle_id", "revision", name="uq_model_asset_revision"),
        CheckConstraint(
            "status IN ('missing', 'downloading', 'paused', 'failed', 'ready')",
            name="ck_model_asset_status",
        ),
        CheckConstraint(
            "integrity IN ('missing', 'partial', 'invalid', 'verified')",
            name="ck_model_asset_integrity",
        ),
        CheckConstraint(
            "received_bytes >= 0 AND total_bytes > 0 AND received_bytes <= total_bytes",
            name="ck_model_asset_byte_counts",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    capability: Mapped[str] = mapped_column(String(24), index=True)
    bundle_id: Mapped[str] = mapped_column(String(120), index=True)
    display_name: Mapped[str] = mapped_column(String(240))
    purpose: Mapped[str] = mapped_column(String(1000))
    version: Mapped[str] = mapped_column(String(120))
    revision: Mapped[str] = mapped_column(String(40))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    total_bytes: Mapped[int] = mapped_column(BigInteger)
    received_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(24), default="missing", index=True)
    integrity: Mapped[str] = mapped_column(String(24), default="missing")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ActiveModelAssetRecord(Base):
    __tablename__ = "active_model_assets"

    bundle_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("model_assets.id", ondelete="RESTRICT"), unique=True, index=True
    )
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModelAssetReferenceRecord(Base):
    __tablename__ = "model_asset_references"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "kind",
            "reference_id",
            name="uq_model_asset_reference",
        ),
        CheckConstraint(
            "kind IN ('job', 'runtime', 'index')",
            name="ck_model_asset_reference_kind",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("model_assets.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24))
    reference_id: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
