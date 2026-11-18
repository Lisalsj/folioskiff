"""Track append-only source revisions and classified paper updates.

Revision ID: 0028_source_revisions_and_updates
Revises: 0027_update_settings
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0028_source_revisions_and_updates"
down_revision: str | None = "0027_update_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _utc_iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    aware = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return aware.isoformat().replace("+00:00", "Z")


def _baseline_snapshot(row: Any) -> dict[str, object]:
    return {
        "connector_id": row.connector_id,
        "external_id": row.external_id,
        "title": row.title,
        "authors": list(row.authors or []),
        "abstract": row.abstract,
        "doi": row.doi,
        "published_at": _utc_iso(row.published_at),
        "source_updated_at": _utc_iso(row.source_updated_at),
        "landing_url": row.landing_url,
        "field_provenance": dict(row.field_provenance or {}),
        "raw_payload": dict(row.raw_payload or {}),
    }


def _snapshot_sha256(snapshot: dict[str, object]) -> str:
    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def upgrade() -> None:
    op.create_table(
        "source_record_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_record_id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("revision_kind", sa.String(length=24), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("changed_fields", sa.JSON(), nullable=False),
        sa.Column("field_provenance", sa.JSON(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "revision_kind IN ('baseline', 'metadata_refresh', 'important_revision')",
            name="ck_source_revision_kind",
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_source_revision_payload_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_records.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_record_id",
            "payload_sha256",
            name="uq_source_revision_payload",
        ),
    )
    op.create_index(
        "ix_source_record_revisions_source_record_id",
        "source_record_revisions",
        ["source_record_id"],
    )
    op.create_index(
        "ix_source_record_revisions_work_id",
        "source_record_revisions",
        ["work_id"],
    )
    op.create_index(
        "ix_source_record_revisions_revision_kind",
        "source_record_revisions",
        ["revision_kind"],
    )
    op.create_index(
        "ix_source_record_revisions_payload_sha256",
        "source_record_revisions",
        ["payload_sha256"],
    )
    op.create_index(
        "ix_source_record_revisions_source_updated_at",
        "source_record_revisions",
        ["source_updated_at"],
    )
    op.create_index(
        "ix_source_record_revisions_fetched_at",
        "source_record_revisions",
        ["fetched_at"],
    )

    source_records = sa.table(
        "source_records",
        sa.column("id", sa.String()),
        sa.column("work_id", sa.String()),
        sa.column("connector_id", sa.String()),
        sa.column("external_id", sa.String()),
        sa.column("title", sa.Text()),
        sa.column("authors", sa.JSON()),
        sa.column("abstract", sa.Text()),
        sa.column("doi", sa.String()),
        sa.column("published_at", sa.DateTime(timezone=True)),
        sa.column("source_updated_at", sa.DateTime(timezone=True)),
        sa.column("landing_url", sa.Text()),
        sa.column("field_provenance", sa.JSON()),
        sa.column("raw_payload", sa.JSON()),
        sa.column("fetched_at", sa.DateTime(timezone=True)),
    )
    revisions = sa.table(
        "source_record_revisions",
        sa.column("id", sa.String()),
        sa.column("source_record_id", sa.String()),
        sa.column("work_id", sa.String()),
        sa.column("revision_kind", sa.String()),
        sa.column("payload_sha256", sa.String()),
        sa.column("snapshot", sa.JSON()),
        sa.column("changed_fields", sa.JSON()),
        sa.column("field_provenance", sa.JSON()),
        sa.column("source_updated_at", sa.DateTime(timezone=True)),
        sa.column("fetched_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    rows: list[dict[str, object]] = []
    for row in bind.execute(sa.select(source_records)).all():
        snapshot = _baseline_snapshot(row)
        rows.append(
            {
                "id": str(uuid4()),
                "source_record_id": row.id,
                "work_id": row.work_id,
                "revision_kind": "baseline",
                "payload_sha256": _snapshot_sha256(snapshot),
                "snapshot": snapshot,
                "changed_fields": [],
                "field_provenance": dict(row.field_provenance or {}),
                "source_updated_at": row.source_updated_at,
                "fetched_at": row.fetched_at,
                "created_at": row.fetched_at,
            }
        )
    if rows:
        bind.execute(revisions.insert(), rows)

    op.execute(
        """
        CREATE TRIGGER trg_source_record_revisions_no_update
        BEFORE UPDATE ON source_record_revisions
        BEGIN SELECT RAISE(ABORT, 'SourceRevision is immutable'); END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_source_record_revisions_no_delete
        BEFORE DELETE ON source_record_revisions
        BEGIN SELECT RAISE(ABORT, 'SourceRevision is immutable'); END
        """
    )

    op.create_table(
        "paper_updates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("source_record_id", sa.String(length=36), nullable=False),
        sa.Column("previous_revision_id", sa.String(length=36), nullable=False),
        sa.Column("current_revision_id", sa.String(length=36), nullable=False),
        sa.Column("update_type", sa.String(length=24), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="unread"),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "update_type IN ('metadata_refresh', 'important_revision')",
            name="ck_paper_update_type",
        ),
        sa.CheckConstraint(
            "visibility IN ('internal', 'updates')",
            name="ck_paper_update_visibility",
        ),
        sa.CheckConstraint(
            "state IN ('unread', 'read', 'dismissed')",
            name="ck_paper_update_state",
        ),
        sa.CheckConstraint(
            "(update_type = 'metadata_refresh' AND visibility = 'internal') OR "
            "(update_type = 'important_revision' AND visibility = 'updates')",
            name="ck_paper_update_visibility_type",
        ),
        sa.CheckConstraint(
            "previous_revision_id != current_revision_id",
            name="ck_paper_update_distinct_revisions",
        ),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_records.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["previous_revision_id"],
            ["source_record_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["current_revision_id"],
            ["source_record_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("current_revision_id", name="uq_paper_update_revision"),
    )
    for column in (
        "work_id",
        "source_record_id",
        "previous_revision_id",
        "current_revision_id",
        "update_type",
        "visibility",
        "state",
        "detected_at",
    ):
        op.create_index(f"ix_paper_updates_{column}", "paper_updates", [column])

    with op.batch_alter_table("run_candidates", recreate="always") as batch:
        batch.add_column(
            sa.Column(
                "discovery_kind",
                sa.String(length=32),
                nullable=False,
                server_default="unchanged",
            )
        )
        batch.create_check_constraint(
            "ck_run_candidate_discovery_kind",
            "discovery_kind IN ('new_work', 'existing_work_new_source', 'unchanged', "
            "'metadata_refresh', 'important_revision')",
        )
    op.create_index(
        "ix_run_candidates_discovery_kind",
        "run_candidates",
        ["discovery_kind"],
    )
    op.execute(
        """
        UPDATE run_candidates
        SET discovery_kind = CASE match_kind
            WHEN 'new' THEN 'new_work'
            WHEN 'review_required' THEN 'new_work'
            WHEN 'exact_doi' THEN 'existing_work_new_source'
            WHEN 'auto_similarity' THEN 'existing_work_new_source'
            ELSE 'unchanged'
        END
        """
    )


def downgrade() -> None:
    op.drop_index("ix_run_candidates_discovery_kind", table_name="run_candidates")
    with op.batch_alter_table("run_candidates", recreate="always") as batch:
        batch.drop_constraint("ck_run_candidate_discovery_kind", type_="check")
        batch.drop_column("discovery_kind")

    for column in reversed(
        (
            "work_id",
            "source_record_id",
            "previous_revision_id",
            "current_revision_id",
            "update_type",
            "visibility",
            "state",
            "detected_at",
        )
    ):
        op.drop_index(f"ix_paper_updates_{column}", table_name="paper_updates")
    op.drop_table("paper_updates")

    op.execute("DROP TRIGGER IF EXISTS trg_source_record_revisions_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_source_record_revisions_no_update")
    for index in (
        "ix_source_record_revisions_fetched_at",
        "ix_source_record_revisions_source_updated_at",
        "ix_source_record_revisions_payload_sha256",
        "ix_source_record_revisions_revision_kind",
        "ix_source_record_revisions_work_id",
        "ix_source_record_revisions_source_record_id",
    ):
        op.drop_index(index, table_name="source_record_revisions")
    op.drop_table("source_record_revisions")
