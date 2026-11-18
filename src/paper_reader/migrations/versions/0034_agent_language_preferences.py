"""Persist Agent, report, and translation defaults.

Revision ID: 0034_agent_language_preferences
Revises: 0033_onboarding_state
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_agent_language_preferences"
down_revision: str | None = "0033_onboarding_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_preferences", recreate="always") as batch:
        batch.add_column(
            sa.Column(
                "default_agent_backend",
                sa.String(length=24),
                nullable=False,
                server_default="codex_cli",
            )
        )
        batch.add_column(
            sa.Column("default_agent_model_id", sa.String(length=200), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "default_reasoning_effort",
                sa.String(length=16),
                nullable=False,
                server_default="medium",
            )
        )
        batch.add_column(
            sa.Column(
                "report_language",
                sa.String(length=16),
                nullable=False,
                server_default="zh-CN",
            )
        )
        batch.add_column(
            sa.Column(
                "translation_source_language",
                sa.String(length=16),
                nullable=False,
                server_default="en",
            )
        )
        batch.add_column(
            sa.Column(
                "translation_target_language",
                sa.String(length=16),
                nullable=False,
                server_default="zh-CN",
            )
        )
        batch.create_check_constraint(
            "ck_user_preferences_agent_backend",
            "default_agent_backend IN ('codex_cli', 'ollama_local', 'ollama_cloud')",
        )
        batch.create_check_constraint(
            "ck_user_preferences_agent_model_id",
            "default_agent_model_id IS NULL OR "
            "(length(default_agent_model_id) > 0 AND length(default_agent_model_id) <= 200)",
        )
        batch.create_check_constraint(
            "ck_user_preferences_reasoning_effort",
            "default_reasoning_effort IN ('minimal', 'low', 'medium', 'high', 'xhigh')",
        )
        batch.create_check_constraint(
            "ck_user_preferences_report_language",
            "report_language IN ('zh-CN', 'en', 'bilingual')",
        )
        batch.create_check_constraint(
            "ck_user_preferences_translation_source_language",
            "translation_source_language IN ('en', 'zh-CN')",
        )
        batch.create_check_constraint(
            "ck_user_preferences_translation_target_language",
            "translation_target_language IN ('en', 'zh-CN')",
        )
        batch.create_check_constraint(
            "ck_user_preferences_translation_pair",
            "translation_source_language <> translation_target_language",
        )


def downgrade() -> None:
    with op.batch_alter_table("user_preferences", recreate="always") as batch:
        batch.drop_constraint("ck_user_preferences_translation_pair", type_="check")
        batch.drop_constraint(
            "ck_user_preferences_translation_target_language", type_="check"
        )
        batch.drop_constraint(
            "ck_user_preferences_translation_source_language", type_="check"
        )
        batch.drop_constraint("ck_user_preferences_report_language", type_="check")
        batch.drop_constraint("ck_user_preferences_reasoning_effort", type_="check")
        batch.drop_constraint("ck_user_preferences_agent_model_id", type_="check")
        batch.drop_constraint("ck_user_preferences_agent_backend", type_="check")
        batch.drop_column("translation_target_language")
        batch.drop_column("translation_source_language")
        batch.drop_column("report_language")
        batch.drop_column("default_reasoning_effort")
        batch.drop_column("default_agent_model_id")
        batch.drop_column("default_agent_backend")
