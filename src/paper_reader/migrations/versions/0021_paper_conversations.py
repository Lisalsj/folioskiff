"""Add durable per-Work paper conversations.

Revision ID: 0021_paper_conversations
Revises: 0020_analysis_reports
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_paper_conversations"
down_revision: str | None = "0020_analysis_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_TABLES = (
    "conversation_messages",
    "conversation_fragments",
    "conversation_checkpoints",
)


def upgrade() -> None:
    op.create_table(
        "paper_conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("active_backend_id", sa.String(length=80), nullable=False),
        sa.Column("active_model_id", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_id", name="uq_paper_conversation_work"),
    )
    op.create_index(
        "ix_paper_conversations_work_id",
        "paper_conversations",
        ["work_id"],
        unique=True,
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=True),
        sa.Column("document_content_sha256", sa.String(length=64), nullable=True),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("backend_id", sa.String(length=80), nullable=True),
        sa.Column("requested_model_id", sa.String(length=200), nullable=True),
        sa.Column("resolved_model_id", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("reply_to_message_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_conversation_message_sequence"),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system_diagnostic')",
            name="ck_conversation_message_role",
        ),
        sa.CheckConstraint(
            "status IN ('committed', 'complete', 'failed', 'cancelled')",
            name="ck_conversation_message_status",
        ),
        sa.CheckConstraint(
            "(document_version_id IS NULL AND document_content_sha256 IS NULL) OR "
            "(document_version_id IS NOT NULL AND document_content_sha256 IS NOT NULL)",
            name="ck_conversation_message_document_pair",
        ),
        sa.CheckConstraint(
            "evidence_sha256 IS NULL OR length(evidence_sha256) = 64",
            name="ck_conversation_message_evidence_hash",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["paper_conversations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["document_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["reply_to_message_id"], ["conversation_messages.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_conversation_message_sequence",
        ),
    )
    for column in (
        "conversation_id",
        "document_version_id",
        "evidence_sha256",
        "job_id",
        "reply_to_message_id",
    ):
        op.create_index(
            f"ix_conversation_messages_{column}",
            "conversation_messages",
            [column],
        )

    op.create_table(
        "conversation_fragments",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 0", name="ck_conversation_fragment_sequence"),
        sa.CheckConstraint(
            "kind IN ('text', 'reasoning', 'diagnostic')",
            name="ck_conversation_fragment_kind",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("job_id", "sequence"),
    )
    op.create_index(
        "ix_conversation_fragments_job_id",
        "conversation_fragments",
        ["job_id"],
    )

    op.create_table(
        "conversation_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("source_start_sequence", sa.Integer(), nullable=False),
        sa.Column("source_end_sequence", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("summary_sha256", sa.String(length=64), nullable=False),
        sa.Column("backend_id", sa.String(length=80), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=True),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_characters", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_start_sequence >= 1 AND source_end_sequence >= source_start_sequence",
            name="ck_conversation_checkpoint_range",
        ),
        sa.CheckConstraint(
            "length(summary_sha256) = 64",
            name="ck_conversation_checkpoint_hash",
        ),
        sa.CheckConstraint(
            "estimated_tokens >= 0 AND estimated_characters >= 0",
            name="ck_conversation_checkpoint_estimates",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["paper_conversations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "source_end_sequence",
            name="uq_conversation_checkpoint_end",
        ),
    )
    op.create_index(
        "ix_conversation_checkpoints_conversation_id",
        "conversation_checkpoints",
        ["conversation_id"],
    )

    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END"
        )


def downgrade() -> None:
    for table in reversed(_IMMUTABLE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_update")
    op.drop_index(
        "ix_conversation_checkpoints_conversation_id",
        table_name="conversation_checkpoints",
    )
    op.drop_table("conversation_checkpoints")
    op.drop_index(
        "ix_conversation_fragments_job_id",
        table_name="conversation_fragments",
    )
    op.drop_table("conversation_fragments")
    for column in reversed(
        (
            "conversation_id",
            "document_version_id",
            "evidence_sha256",
            "job_id",
            "reply_to_message_id",
        )
    ):
        op.drop_index(
            f"ix_conversation_messages_{column}",
            table_name="conversation_messages",
        )
    op.drop_table("conversation_messages")
    op.drop_index(
        "ix_paper_conversations_work_id",
        table_name="paper_conversations",
    )
    op.drop_table("paper_conversations")
