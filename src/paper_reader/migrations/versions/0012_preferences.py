"""Add explicit library state and privacy-bounded preference events.

Revision ID: 0012_preferences
Revises: 0011_rating_catalogs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_preferences"
down_revision: str | None = "0011_rating_catalogs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_states",
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("favorite", sa.Boolean(), nullable=False),
        sa.Column("feedback", sa.String(length=16), nullable=False),
        sa.Column("feedback_reasons", sa.JSON(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("reading_progress", sa.Float(), nullable=False),
        sa.Column("analysis_state", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "feedback IN ('neutral', 'like', 'dislike')",
            name="ck_library_state_feedback",
        ),
        sa.CheckConstraint(
            "reading_progress >= 0 AND reading_progress <= 100",
            name="ck_library_state_progress",
        ),
        sa.CheckConstraint(
            "analysis_state IN ('not_analyzed', 'available', 'failed')",
            name="ck_library_state_analysis",
        ),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("work_id"),
    )

    op.create_table(
        "implicit_preference_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("duration_bucket", sa.String(length=16), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(event_key) > 0", name="ck_preference_event_key"),
        sa.CheckConstraint(
            "event_type IN ('list_open', 'detail_open', 'pdf_open', "
            "'reading_duration', 'reading_completed', 'analysis_report_generated')",
            name="ck_preference_event_type",
        ),
        sa.CheckConstraint(
            "(event_type = 'reading_duration' AND duration_bucket IN "
            "('lt_30s', '30s_2m', '2m_10m', 'gte_10m')) OR "
            "(event_type != 'reading_duration' AND duration_bucket IS NULL)",
            name="ck_preference_event_duration",
        ),
        sa.CheckConstraint(
            "weight > 0 AND weight <= 10",
            name="ck_preference_event_weight",
        ),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_implicit_preference_events_work_id",
        "implicit_preference_events",
        ["work_id"],
    )
    op.create_index(
        "ix_implicit_preference_events_event_key",
        "implicit_preference_events",
        ["event_key"],
        unique=True,
    )
    op.create_index(
        "ix_implicit_preference_events_event_type",
        "implicit_preference_events",
        ["event_type"],
    )
    op.create_index(
        "ix_implicit_preference_events_occurred_at",
        "implicit_preference_events",
        ["occurred_at"],
    )
    op.execute(
        "CREATE TRIGGER trg_implicit_preference_events_no_update "
        "BEFORE UPDATE ON implicit_preference_events "
        "WHEN NEW.id IS NOT OLD.id "
        "OR NEW.event_key IS NOT OLD.event_key "
        "OR NEW.event_type IS NOT OLD.event_type "
        "OR NEW.duration_bucket IS NOT OLD.duration_bucket "
        "OR NEW.weight IS NOT OLD.weight "
        "OR NEW.occurred_at IS NOT OLD.occurred_at "
        "OR NEW.created_at IS NOT OLD.created_at "
        "BEGIN SELECT RAISE(ABORT, 'ImplicitPreferenceEvent is immutable'); END"
    )

    op.create_table(
        "preference_aggregates",
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("total_weight", sa.Float(), nullable=False),
        sa.Column("aggregated_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "total_weight >= 0",
            name="ck_preference_aggregate_weight",
        ),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("work_id"),
    )

    op.create_table(
        "interested_authors",
        sa.Column("author_key", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(author_key) > 0", name="ck_interested_author_key"),
        sa.CheckConstraint("length(display_name) > 0", name="ck_interested_author_name"),
        sa.PrimaryKeyConstraint("author_key"),
    )
    op.create_index(
        "ix_interested_authors_active",
        "interested_authors",
        ["active"],
    )


def downgrade() -> None:
    op.drop_index("ix_interested_authors_active", table_name="interested_authors")
    op.drop_table("interested_authors")
    op.drop_table("preference_aggregates")
    op.execute("DROP TRIGGER IF EXISTS trg_implicit_preference_events_no_update")
    op.drop_index(
        "ix_implicit_preference_events_occurred_at",
        table_name="implicit_preference_events",
    )
    op.drop_index(
        "ix_implicit_preference_events_event_type",
        table_name="implicit_preference_events",
    )
    op.drop_index(
        "ix_implicit_preference_events_event_key",
        table_name="implicit_preference_events",
    )
    op.drop_index(
        "ix_implicit_preference_events_work_id",
        table_name="implicit_preference_events",
    )
    op.drop_table("implicit_preference_events")
    op.drop_table("library_states")
