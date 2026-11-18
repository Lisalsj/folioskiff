"""Persist normalized metadata enrichment observations.

Revision ID: 0009_metadata_observations
Revises: 0008_run_candidates
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_metadata_observations"
down_revision: str | None = "0008_run_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metadata_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("enricher_id", sa.String(length=80), nullable=False),
        sa.Column("enricher_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=True),
        sa.Column("matched_by", sa.String(length=80), nullable=True),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("field_provenance", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("response_sha256", sa.String(length=64), nullable=True),
        sa.Column("observation_sha256", sa.String(length=64), nullable=False),
        sa.Column("rate_limit", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('observed', 'not_found', 'not_applicable', 'disabled')",
            name="ck_metadata_observation_status",
        ),
        sa.CheckConstraint(
            "length(observation_sha256) = 64",
            name="ck_metadata_observation_sha256_length",
        ),
        sa.CheckConstraint(
            "response_sha256 IS NULL OR length(response_sha256) = 64",
            name="ck_metadata_response_sha256_length",
        ),
        sa.CheckConstraint(
            "(status = 'observed' AND external_id IS NOT NULL "
            "AND matched_by IS NOT NULL AND response_sha256 IS NOT NULL) OR "
            "(status != 'observed' AND response_sha256 IS NULL)",
            name="ck_metadata_observation_payload",
        ),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_id",
            "enricher_id",
            "observation_sha256",
            name="uq_metadata_observation_identity",
        ),
    )
    op.create_index(
        "ix_metadata_observations_work_id",
        "metadata_observations",
        ["work_id"],
    )
    op.create_index(
        "ix_metadata_observations_enricher_id",
        "metadata_observations",
        ["enricher_id"],
    )
    op.create_index(
        "ix_metadata_observations_status",
        "metadata_observations",
        ["status"],
    )
    op.create_index(
        "ix_metadata_observations_fetched_at",
        "metadata_observations",
        ["fetched_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_metadata_observations_fetched_at", table_name="metadata_observations")
    op.drop_index("ix_metadata_observations_status", table_name="metadata_observations")
    op.drop_index("ix_metadata_observations_enricher_id", table_name="metadata_observations")
    op.drop_index("ix_metadata_observations_work_id", table_name="metadata_observations")
    op.drop_table("metadata_observations")
