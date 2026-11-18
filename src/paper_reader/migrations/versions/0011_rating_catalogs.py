"""Persist separately sourced venue rating catalogs.

Revision ID: 0011_rating_catalogs
Revises: 0010_score_snapshots
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_rating_catalogs"
down_revision: str | None = "0010_score_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rating_catalogs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("catalog_name", sa.String(length=160), nullable=False),
        sa.Column("catalog_version", sa.String(length=80), nullable=False),
        sa.Column("source_note", sa.Text(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("rights_confirmed", sa.Boolean(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('jcr', 'cas', 'emerging')", name="ck_rating_catalog_kind"),
        sa.CheckConstraint("length(file_sha256) = 64", name="ck_rating_catalog_sha256_length"),
        sa.CheckConstraint("row_count >= 1", name="ck_rating_catalog_row_count"),
        sa.CheckConstraint("rights_confirmed = 1", name="ck_rating_catalog_rights"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "file_sha256", name="uq_rating_catalog_kind_file"),
    )
    op.create_index("ix_rating_catalogs_kind", "rating_catalogs", ["kind"])
    op.create_index("ix_rating_catalogs_file_sha256", "rating_catalogs", ["file_sha256"])

    op.create_table(
        "venue_ratings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("catalog_id", sa.String(length=36), nullable=False),
        sa.Column("venue_key", sa.Text(), nullable=False),
        sa.Column("venue_name", sa.Text(), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("rating_label", sa.String(length=80), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("identifiers", sa.JSON(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_venue_rating_score"),
        sa.CheckConstraint("row_number >= 2", name="ck_venue_rating_row_number"),
        sa.CheckConstraint("length(venue_key) > 0", name="ck_venue_rating_key"),
        sa.ForeignKeyConstraint(["catalog_id"], ["rating_catalogs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("catalog_id", "venue_key", name="uq_venue_rating_catalog_key"),
    )
    op.create_index("ix_venue_ratings_catalog_id", "venue_ratings", ["catalog_id"])
    op.create_index("ix_venue_ratings_venue_key", "venue_ratings", ["venue_key"])

    op.create_table(
        "rating_catalog_selections",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("catalog_id", sa.String(length=36), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('jcr', 'cas', 'emerging')",
            name="ck_rating_catalog_selection_kind",
        ),
        sa.ForeignKeyConstraint(["catalog_id"], ["rating_catalogs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("kind"),
        sa.UniqueConstraint("catalog_id"),
    )
    op.create_index(
        "ix_rating_catalog_selections_catalog_id",
        "rating_catalog_selections",
        ["catalog_id"],
        unique=True,
    )

    for table, label in (("rating_catalogs", "RatingCatalog"), ("venue_ratings", "VenueRating")):
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_{table}_no_{operation.casefold()} "
                f"BEFORE {operation} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{label} is immutable'); END"
            )


def downgrade() -> None:
    for table in ("venue_ratings", "rating_catalogs"):
        for operation in ("delete", "update"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{operation}")
    op.drop_index(
        "ix_rating_catalog_selections_catalog_id",
        table_name="rating_catalog_selections",
    )
    op.drop_table("rating_catalog_selections")
    op.drop_index("ix_venue_ratings_venue_key", table_name="venue_ratings")
    op.drop_index("ix_venue_ratings_catalog_id", table_name="venue_ratings")
    op.drop_table("venue_ratings")
    op.drop_index("ix_rating_catalogs_file_sha256", table_name="rating_catalogs")
    op.drop_index("ix_rating_catalogs_kind", table_name="rating_catalogs")
    op.drop_table("rating_catalogs")
