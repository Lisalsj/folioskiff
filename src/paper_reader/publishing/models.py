"""Strict public publication models and immutable database records."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from paper_reader.database import Base
from paper_reader.jobs.models import utc_now

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

PublicReferenceKind = Literal[
    "abstract",
    "page",
    "figure",
    "table",
    "equation",
    "annotation",
    "external",
]


class _PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class PublicationWorkSelection(_PublicModel):
    work_id: str = Field(min_length=1, max_length=36)
    report_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("work_id", "report_id")
    @classmethod
    def _safe_identifiers(cls, value: str | None) -> str | None:
        if value is not None and _CONTROL.search(value):
            raise ValueError("publication identifiers cannot contain control characters")
        return value


class PublicationSelection(_PublicModel):
    slug: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    public_intro: str = Field(default="", max_length=4_000)
    # Omit the historical default from canonical JSON so old snapshot hashes still verify.
    display_language: Literal["zh-CN", "en"] = Field(
        default="zh-CN", exclude_if=lambda language: language == "zh-CN"
    )
    works: Annotated[tuple[PublicationWorkSelection, ...], Field(min_length=1, max_length=100)]

    @field_validator("slug")
    @classmethod
    def _safe_slug(cls, value: str) -> str:
        if not _SLUG.fullmatch(value):
            raise ValueError("slug must contain lowercase letters, digits, and single hyphens")
        return value

    @field_validator("title", "public_intro")
    @classmethod
    def _safe_public_copy(cls, value: str) -> str:
        if _CONTROL.search(value):
            raise ValueError("public copy cannot contain control characters")
        return value

    @model_validator(mode="after")
    def _unique_requested_works(self) -> PublicationSelection:
        ids = [item.work_id for item in self.works]
        if len(ids) != len(set(ids)):
            raise ValueError("a Work can only be selected once")
        return self


class PublicEvidenceReference(_PublicModel):
    kind: PublicReferenceKind
    page_number: int | None = Field(default=None, ge=1)
    locator: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, max_length=2_048)
    external_title: str | None = Field(default=None, max_length=300)


class PublicReportStatement(_PublicModel):
    text: str = Field(min_length=1, max_length=20_000)
    references: tuple[PublicEvidenceReference, ...]


PublicStatementList = Annotated[
    tuple[PublicReportStatement, ...],
    Field(min_length=1, max_length=100),
]


class PublicReportContent(_PublicModel):
    title: str = Field(min_length=1, max_length=500)
    overview: PublicReportStatement
    research_question: PublicReportStatement
    author_claims: PublicStatementList
    contributions: PublicStatementList
    method: PublicStatementList
    assumptions: PublicStatementList
    data_and_experiments: PublicStatementList
    findings: PublicStatementList
    limitations: PublicStatementList
    reproducibility: PublicStatementList
    relation_to_user_intent: PublicStatementList
    agent_judgments: PublicStatementList


class PublicAnalysisReport(_PublicModel):
    id: str
    version: int = Field(ge=1)
    evidence_level: Literal["full_text", "abstract_only"]
    language: Literal["zh-CN", "en", "bilingual"]
    schema_version: str
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: str
    content: PublicReportContent


class PublicQualityScore(_PublicModel):
    snapshot_id: str
    score: float = Field(ge=0, le=100)
    coverage: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=100)
    rule_version: str
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublicWork(_PublicModel):
    canonical_work_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str | None
    doi: str | None
    published_at: str | None
    quality_score: PublicQualityScore | None
    report: PublicAnalysisReport | None


class PublicationPayload(_PublicModel):
    schema_version: Literal["paper-reader-publication-v1"] = "paper-reader-publication-v1"
    slug: str
    title: str
    public_intro: str
    works: tuple[PublicWork, ...]


class PublicationPreview(_PublicModel):
    selection: PublicationSelection
    payload: PublicationPayload
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublishedFile(_PublicModel):
    position: int = Field(ge=0)
    relative_path: str
    media_type: str
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: bytes


class PublishedFileManifestEntry(_PublicModel):
    position: int = Field(ge=0)
    relative_path: str
    media_type: str
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublishedSnapshotWork(_PublicModel):
    position: int = Field(ge=0)
    canonical_work_id: str
    report_id: str | None
    score_snapshot_id: str | None


class PublishedSnapshot(_PublicModel):
    id: str
    slug: str
    schema_version: str
    preview_fingerprint: str
    selection: PublicationSelection
    payload: PublicationPayload
    payload_sha256: str
    file_manifest: tuple[PublishedFileManifestEntry, ...]
    manifest_sha256: str
    snapshot_sha256: str
    works: tuple[PublishedSnapshotWork, ...]
    files: tuple[PublishedFile, ...]
    created_at: str


class PublishedSnapshotRecord(Base):
    __tablename__ = "published_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_sha256", name="uq_published_snapshot_hash"),
        CheckConstraint(
            "length(slug) >= 1 AND length(slug) <= 80",
            name="ck_published_snapshot_slug_length",
        ),
        CheckConstraint(
            "length(preview_fingerprint) = 64 AND length(payload_sha256) = 64 "
            "AND length(manifest_sha256) = 64 AND length(snapshot_sha256) = 64",
            name="ck_published_snapshot_hashes",
        ),
        Index("ix_published_snapshots_slug_created", "slug", "created_at"),
        Index("ix_published_snapshots_preview_fingerprint", "preview_fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    slug: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(80))
    preview_fingerprint: Mapped[str] = mapped_column(String(64))
    selection_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    file_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PublishedSnapshotWorkRecord(Base):
    __tablename__ = "published_snapshot_works"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "canonical_work_id",
            name="uq_published_snapshot_canonical_work",
        ),
        CheckConstraint("position >= 0", name="ck_published_snapshot_work_position"),
        Index("ix_published_snapshot_works_canonical_work", "canonical_work_id"),
        Index("ix_published_snapshot_works_report", "report_id"),
        Index("ix_published_snapshot_works_score", "score_snapshot_id"),
    )

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("published_snapshots.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"),
    )
    report_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_reports.id", ondelete="RESTRICT"),
        nullable=True,
    )
    score_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("score_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )


class PublishedSnapshotFileRecord(Base):
    __tablename__ = "published_snapshot_files"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "position",
            name="uq_published_snapshot_file_position",
        ),
        CheckConstraint("position >= 0", name="ck_published_snapshot_file_position"),
        CheckConstraint("byte_size >= 0", name="ck_published_snapshot_file_size"),
        CheckConstraint("length(sha256) = 64", name="ck_published_snapshot_file_hash"),
        CheckConstraint(
            "relative_path IN ('index.html', 'data.json', 'manifest.json')",
            name="ck_published_snapshot_file_path",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("published_snapshots.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    relative_path: Mapped[str] = mapped_column(String(255), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(100))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
