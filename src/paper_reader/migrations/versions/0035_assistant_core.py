"""Add durable evidence-bound Assistant sessions and tasks.

Revision ID: 0035_assistant_core
Revises: 0034_agent_language_preferences
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_assistant_core"
down_revision: str | None = "0034_agent_language_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_TABLES = (
    "assistant_context_snapshots",
    "assistant_messages",
    "assistant_fragments",
    "assistant_checkpoints",
)


def upgrade() -> None:
    op.create_table(
        "assistant_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_id", name="uq_assistant_session_work"),
    )
    op.create_table(
        "assistant_context_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("document_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=24), nullable=False),
        sa.Column("page_numbers", sa.JSON(), nullable=False),
        sa.Column("selection", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("annotation_ids", sa.JSON(), nullable=False),
        sa.Column("section_fallback", sa.Boolean(), nullable=False),
        sa.Column("extraction_version", sa.String(length=80), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope IN ('selection', 'current_page', 'current_section', "
            "'full_document', 'annotations')",
            name="ck_assistant_context_scope",
        ),
        sa.CheckConstraint(
            "(scope = 'selection' AND selection IS NOT NULL) OR "
            "(scope != 'selection' AND selection IS NULL)",
            name="ck_assistant_context_selection_scope",
        ),
        sa.CheckConstraint(
            "length(document_content_sha256) = 64 AND length(evidence_sha256) = 64 "
            "AND length(snapshot_sha256) = 64",
            name="ck_assistant_context_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["assistant_sessions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["document_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_sha256", name="uq_assistant_context_snapshot_hash"),
    )
    for column in (
        "session_id",
        "work_id",
        "document_version_id",
        "document_content_sha256",
        "scope",
        "evidence_sha256",
    ):
        op.create_index(
            f"ix_assistant_context_snapshots_{column}",
            "assistant_context_snapshots",
            [column],
        )

    op.create_table(
        "assistant_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("context_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("retry_of_task_id", sa.String(length=36), nullable=True),
        sa.Column("mode", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("network_policy", sa.String(length=24), nullable=False),
        sa.Column("backend_id", sa.String(length=80), nullable=False),
        sa.Column("requested_model_id", sa.String(length=200), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=16), nullable=False),
        sa.Column("service_tier", sa.String(length=16), nullable=False),
        sa.Column("output_language", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("command_sha256", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("diagnostic", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mode IN ('ask', 'explain', 'translate', 'related')",
            name="ck_assistant_task_mode",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_assistant_task_status",
        ),
        sa.CheckConstraint(
            "network_policy IN ('local_only', 'external_research')",
            name="ck_assistant_task_network_policy",
        ),
        sa.CheckConstraint(
            "(mode NOT IN ('ask', 'explain')) OR network_policy = 'local_only'",
            name="ck_assistant_task_quick_local_only",
        ),
        sa.CheckConstraint(
            "reasoning_effort IN ('minimal', 'low', 'medium', 'high', 'xhigh')",
            name="ck_assistant_task_reasoning",
        ),
        sa.CheckConstraint(
            "service_tier IN ('flex', 'fast')",
            name="ck_assistant_task_service_tier",
        ),
        sa.CheckConstraint(
            "output_language IN ('zh-CN', 'en', 'bilingual')",
            name="ck_assistant_task_output_language",
        ),
        sa.CheckConstraint(
            "length(command_sha256) = 64",
            name="ck_assistant_task_command_hash",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["assistant_sessions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["context_snapshot_id"],
            ["assistant_context_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_task_id"], ["assistant_tasks.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_assistant_task_idempotency"),
        sa.UniqueConstraint("job_id", name="uq_assistant_task_job"),
    )
    for column in (
        "session_id",
        "context_snapshot_id",
        "retry_of_task_id",
        "mode",
        "status",
        "command_sha256",
    ):
        op.create_index(
            f"ix_assistant_tasks_{column}",
            "assistant_tasks",
            [column],
        )

    op.create_table(
        "assistant_agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("backend_id", sa.String(length=80), nullable=False),
        sa.Column("requested_model_id", sa.String(length=200), nullable=True),
        sa.Column("resolved_model_id", sa.String(length=200), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=16), nullable=False),
        sa.Column("service_tier", sa.String(length=16), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=True),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=True),
        sa.Column("raw_output_sha256", sa.String(length=64), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("repair_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("diagnostic", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt >= 1", name="ck_assistant_agent_run_attempt"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_assistant_agent_run_status",
        ),
        sa.CheckConstraint(
            "reasoning_effort IN ('minimal', 'low', 'medium', 'high', 'xhigh')",
            name="ck_assistant_agent_run_reasoning",
        ),
        sa.CheckConstraint(
            "service_tier IN ('flex', 'fast')",
            name="ck_assistant_agent_run_service_tier",
        ),
        sa.CheckConstraint(
            "repair_count >= 0 AND repair_count <= 1",
            name="ck_assistant_agent_run_repair_count",
        ),
        sa.CheckConstraint(
            "(request_sha256 IS NULL OR length(request_sha256) = 64) AND "
            "(prompt_sha256 IS NULL OR length(prompt_sha256) = 64) AND "
            "(raw_output_sha256 IS NULL OR length(raw_output_sha256) = 64)",
            name="ck_assistant_agent_run_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["assistant_tasks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "attempt", name="uq_assistant_agent_run_attempt"),
    )
    op.create_index(
        "ix_assistant_agent_runs_task_id",
        "assistant_agent_runs",
        ["task_id"],
    )
    op.create_index(
        "ix_assistant_agent_runs_status",
        "assistant_agent_runs",
        ["status"],
    )

    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("reply_to_message_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_assistant_message_sequence"),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system_diagnostic')",
            name="ck_assistant_message_role",
        ),
        sa.CheckConstraint(
            "status IN ('committed', 'complete', 'failed', 'cancelled')",
            name="ck_assistant_message_status",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["assistant_sessions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["assistant_tasks.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["reply_to_message_id"], ["assistant_messages.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "sequence",
            name="uq_assistant_message_sequence",
        ),
    )
    for column in (
        "session_id",
        "task_id",
        "role",
        "job_id",
        "reply_to_message_id",
    ):
        op.create_index(
            f"ix_assistant_messages_{column}",
            "assistant_messages",
            [column],
        )

    op.create_table(
        "assistant_fragments",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 0", name="ck_assistant_fragment_sequence"),
        sa.CheckConstraint(
            "kind IN ('text', 'reasoning', 'diagnostic')",
            name="ck_assistant_fragment_kind",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["assistant_tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["assistant_agent_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("task_id", "sequence"),
    )
    op.create_index(
        "ix_assistant_fragments_run_id",
        "assistant_fragments",
        ["run_id"],
    )

    op.create_table(
        "assistant_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("source_start_sequence", sa.Integer(), nullable=False),
        sa.Column("through_sequence", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("summary_sha256", sa.String(length=64), nullable=False),
        sa.Column("backend_id", sa.String(length=80), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=True),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_characters", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_start_sequence >= 1 AND through_sequence >= source_start_sequence",
            name="ck_assistant_checkpoint_range",
        ),
        sa.CheckConstraint(
            "length(summary_sha256) = 64",
            name="ck_assistant_checkpoint_hash",
        ),
        sa.CheckConstraint(
            "estimated_tokens >= 0 AND estimated_characters >= 0",
            name="ck_assistant_checkpoint_estimates",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["assistant_sessions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "through_sequence",
            name="uq_assistant_checkpoint_through_sequence",
        ),
    )
    op.create_index(
        "ix_assistant_checkpoints_session_id",
        "assistant_checkpoints",
        ["session_id"],
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
        "ix_assistant_checkpoints_session_id",
        table_name="assistant_checkpoints",
    )
    op.drop_table("assistant_checkpoints")
    op.drop_index("ix_assistant_fragments_run_id", table_name="assistant_fragments")
    op.drop_table("assistant_fragments")
    for column in reversed(
        ("session_id", "task_id", "role", "job_id", "reply_to_message_id")
    ):
        op.drop_index(
            f"ix_assistant_messages_{column}",
            table_name="assistant_messages",
        )
    op.drop_table("assistant_messages")
    op.drop_index("ix_assistant_agent_runs_status", table_name="assistant_agent_runs")
    op.drop_index("ix_assistant_agent_runs_task_id", table_name="assistant_agent_runs")
    op.drop_table("assistant_agent_runs")
    for column in reversed(
        (
            "session_id",
            "context_snapshot_id",
            "retry_of_task_id",
            "mode",
            "status",
            "command_sha256",
        )
    ):
        op.drop_index(f"ix_assistant_tasks_{column}", table_name="assistant_tasks")
    op.drop_table("assistant_tasks")
    for column in reversed(
        (
            "session_id",
            "work_id",
            "document_version_id",
            "document_content_sha256",
            "scope",
            "evidence_sha256",
        )
    ):
        op.drop_index(
            f"ix_assistant_context_snapshots_{column}",
            table_name="assistant_context_snapshots",
        )
    op.drop_table("assistant_context_snapshots")
    op.drop_table("assistant_sessions")
