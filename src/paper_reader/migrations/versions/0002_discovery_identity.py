"""Create canonical Work identity and source/document provenance tables.

Revision ID: 0002_discovery_identity
Revises: 0001_foundation
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_discovery_identity"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "works",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canonical_title", sa.Text(), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_works_normalized_title", "works", ["normalized_title"])
    op.create_index("ix_works_doi", "works", ["doi"], unique=True)

    op.create_table(
        "source_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("connector_id", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("landing_url", sa.Text(), nullable=True),
        sa.Column("field_provenance", sa.JSON(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connector_id",
            "external_id",
            name="uq_source_records_connector_external",
        ),
    )
    op.create_index("ix_source_records_work_id", "source_records", ["work_id"])
    op.create_index("ix_source_records_connector_id", "source_records", ["connector_id"])
    op.create_index("ix_source_records_normalized_title", "source_records", ["normalized_title"])
    op.create_index("ix_source_records_doi", "source_records", ["doi"])
    op.create_index(
        "ix_source_records_source_updated_at", "source_records", ["source_updated_at"]
    )

    op.create_table(
        "document_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("source_record_id", sa.String(length=36), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("access_status", sa.String(length=24), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("completeness", sa.Integer(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extraction_status", sa.String(length=24), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(content_sha256) = 64", name="ck_document_sha256_length"),
        sa.CheckConstraint(
            "access_status IN ('open', 'user_provided', 'restricted', 'unknown')",
            name="ck_document_access_status",
        ),
        sa.CheckConstraint(
            "completeness >= 0 AND completeness <= 100",
            name="ck_document_completeness",
        ),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_records.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_id", "content_sha256", name="uq_document_versions_hash"),
    )
    op.create_index("ix_document_versions_work_id", "document_versions", ["work_id"])
    op.create_index(
        "ix_document_versions_source_record_id", "document_versions", ["source_record_id"]
    )
    op.create_index(
        "ix_document_versions_content_sha256", "document_versions", ["content_sha256"]
    )

    op.create_table(
        "merge_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_work_id", sa.String(length=36), nullable=False),
        sa.Column("matched_work_id", sa.String(length=36), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "candidate_work_id != matched_work_id", name="ck_merge_review_distinct_works"
        ),
        sa.CheckConstraint(
            "similarity >= 0 AND similarity <= 1", name="ck_merge_review_similarity"
        ),
        sa.ForeignKeyConstraint(["candidate_work_id"], ["works.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matched_work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_work_id",
            "matched_work_id",
            name="uq_merge_reviews_candidate_match",
        ),
    )
    op.create_index(
        "ix_merge_reviews_candidate_work_id", "merge_reviews", ["candidate_work_id"]
    )
    op.create_index("ix_merge_reviews_matched_work_id", "merge_reviews", ["matched_work_id"])
    op.create_index("ix_merge_reviews_status", "merge_reviews", ["status"])


def downgrade() -> None:
    op.drop_index("ix_merge_reviews_status", table_name="merge_reviews")
    op.drop_index("ix_merge_reviews_matched_work_id", table_name="merge_reviews")
    op.drop_index("ix_merge_reviews_candidate_work_id", table_name="merge_reviews")
    op.drop_table("merge_reviews")
    op.drop_index("ix_document_versions_content_sha256", table_name="document_versions")
    op.drop_index("ix_document_versions_source_record_id", table_name="document_versions")
    op.drop_index("ix_document_versions_work_id", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index("ix_source_records_source_updated_at", table_name="source_records")
    op.drop_index("ix_source_records_doi", table_name="source_records")
    op.drop_index("ix_source_records_normalized_title", table_name="source_records")
    op.drop_index("ix_source_records_connector_id", table_name="source_records")
    op.drop_index("ix_source_records_work_id", table_name="source_records")
    op.drop_table("source_records")
    op.drop_index("ix_works_doi", table_name="works")
    op.drop_index("ix_works_normalized_title", table_name="works")
    op.drop_table("works")
