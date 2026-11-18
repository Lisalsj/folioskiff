"""Add explicit related-paper and knowledge-resource sets.

Revision ID: 0037_related_resources
Revises: 0036_translations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_related_resources"
down_revision: str | None = "0036_translations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "related_resource_sets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("document_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("context_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("intent_revision", sa.Integer(), nullable=False),
        sa.Column("intent", sa.JSON(), nullable=False),
        sa.Column("intent_history", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("include_academic_sources", sa.Boolean(), nullable=False),
        sa.Column("include_external_web", sa.Boolean(), nullable=False),
        sa.Column("external_web_consent", sa.Boolean(), nullable=False),
        sa.Column("coverage", sa.Float(), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "intent_revision >= 1",
            name="ck_related_resource_set_revision",
        ),
        sa.CheckConstraint(
            "length(document_content_sha256) = 64",
            name="ck_related_resource_set_document_hash",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'confirmed', 'queued', 'running', 'partial', "
            "'succeeded', 'failed', 'cancelled')",
            name="ck_related_resource_set_status",
        ),
        sa.CheckConstraint(
            "coverage >= 0 AND coverage <= 100",
            name="ck_related_resource_set_coverage",
        ),
        sa.CheckConstraint(
            "include_academic_sources = 1 OR include_external_web = 1",
            name="ck_related_resource_set_target",
        ),
        sa.CheckConstraint(
            "(include_external_web = 0 AND external_web_consent = 0) OR "
            "(include_external_web = 1 AND external_web_consent = 1)",
            name="ck_related_resource_set_external_consent",
        ),
        sa.CheckConstraint(
            "(status = 'draft' AND confirmed_at IS NULL) OR "
            "(status != 'draft' AND confirmed_at IS NOT NULL)",
            name="ck_related_resource_set_confirmation",
        ),
        sa.CheckConstraint(
            "(status IN ('draft', 'confirmed', 'queued', 'running') "
            "AND completed_at IS NULL) OR "
            "(status IN ('partial', 'succeeded', 'failed', 'cancelled') "
            "AND completed_at IS NOT NULL)",
            name="ck_related_resource_set_completion",
        ),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["context_snapshot_id"],
            ["assistant_context_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_related_resource_set_job"),
    )
    for column in (
        "work_id",
        "document_version_id",
        "document_content_sha256",
        "context_snapshot_id",
        "status",
    ):
        op.create_index(
            f"ix_related_resource_sets_{column}",
            "related_resource_sets",
            [column],
        )

    op.create_table(
        "related_paper_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("set_id", sa.String(length=36), nullable=False),
        sa.Column("connector_id", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=False),
        sa.Column("normalized_doi", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("match_evidence", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("linked_work_id", sa.String(length=36), nullable=True),
        sa.Column("merge_review_id", sa.String(length=36), nullable=True),
        sa.Column("field_provenance", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(connector_id) >= 1",
            name="ck_related_paper_connector",
        ),
        sa.CheckConstraint(
            "length(external_id) >= 1",
            name="ck_related_paper_external_id",
        ),
        sa.CheckConstraint("length(title) >= 1", name="ck_related_paper_title"),
        sa.CheckConstraint("length(reason) >= 1", name="ck_related_paper_reason"),
        sa.CheckConstraint(
            "normalized_doi IS NULL OR length(normalized_doi) >= 3",
            name="ck_related_paper_doi",
        ),
        sa.CheckConstraint(
            "publication_year IS NULL OR (publication_year >= 1000 AND publication_year <= 9999)",
            name="ck_related_paper_year",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_related_paper_confidence",
        ),
        sa.CheckConstraint(
            "verification_status IN ('verified', 'pending_verification', 'rejected')",
            name="ck_related_paper_verification",
        ),
        sa.CheckConstraint(
            "linked_work_id IS NULL OR merge_review_id IS NULL",
            name="ck_related_paper_identity_result",
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["related_resource_sets.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["linked_work_id"],
            ["works.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["merge_review_id"],
            ["merge_reviews.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "set_id",
            "connector_id",
            "external_id",
            name="uq_related_paper_set_connector_external",
        ),
        sa.UniqueConstraint(
            "set_id",
            "normalized_doi",
            name="uq_related_paper_set_doi",
        ),
    )
    for column in (
        "connector_id",
        "normalized_doi",
        "verification_status",
        "linked_work_id",
        "merge_review_id",
    ):
        op.create_index(
            f"ix_related_paper_candidates_{column}",
            "related_paper_candidates",
            [column],
        )

    op.create_table(
        "knowledge_resources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("set_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("resource_type", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("publisher", sa.String(length=200), nullable=True),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("field_provenance", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(title) >= 1", name="ck_knowledge_resource_title"),
        sa.CheckConstraint(
            "canonical_url LIKE 'https://%' AND final_url LIKE 'https://%'",
            name="ck_knowledge_resource_https",
        ),
        sa.CheckConstraint("length(domain) >= 3", name="ck_knowledge_resource_domain"),
        sa.CheckConstraint("length(reason) >= 1", name="ck_knowledge_resource_reason"),
        sa.CheckConstraint(
            "resource_type IN ('official_docs', 'review', 'course', 'textbook', 'database')",
            name="ck_knowledge_resource_type",
        ),
        sa.CheckConstraint(
            "verification_status IN ('verified', 'pending_verification', 'rejected')",
            name="ck_knowledge_resource_verification",
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR (http_status >= 100 AND http_status <= 599)",
            name="ck_knowledge_resource_http_status",
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["related_resource_sets.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "set_id",
            "canonical_url",
            name="uq_knowledge_resource_set_url",
        ),
    )
    for column in ("domain", "resource_type", "verification_status"):
        op.create_index(
            f"ix_knowledge_resources_{column}",
            "knowledge_resources",
            [column],
        )

    op.create_table(
        "related_run_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("set_id", sa.String(length=36), nullable=False),
        sa.Column("connector_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("displayed_count", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(connector_id) >= 1",
            name="ck_related_run_source_connector",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'skipped')",
            name="ck_related_run_source_status",
        ),
        sa.CheckConstraint(
            "candidate_count >= 0 AND displayed_count >= 0 AND displayed_count <= candidate_count",
            name="ck_related_run_source_counts",
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_related_run_source_progress",
        ),
        sa.CheckConstraint(
            "(status IN ('queued', 'running') AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'cancelled', 'skipped') "
            "AND completed_at IS NOT NULL)",
            name="ck_related_run_source_completion",
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["related_resource_sets.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "set_id",
            "connector_id",
            name="uq_related_run_source_set_connector",
        ),
    )
    for column in ("connector_id", "status"):
        op.create_index(
            f"ix_related_run_sources_{column}",
            "related_run_sources",
            [column],
        )


def downgrade() -> None:
    for column in reversed(("connector_id", "status")):
        op.drop_index(
            f"ix_related_run_sources_{column}",
            table_name="related_run_sources",
        )
    op.drop_table("related_run_sources")
    for column in reversed(("domain", "resource_type", "verification_status")):
        op.drop_index(
            f"ix_knowledge_resources_{column}",
            table_name="knowledge_resources",
        )
    op.drop_table("knowledge_resources")
    for column in reversed(
        (
            "connector_id",
            "normalized_doi",
            "verification_status",
            "linked_work_id",
            "merge_review_id",
        )
    ):
        op.drop_index(
            f"ix_related_paper_candidates_{column}",
            table_name="related_paper_candidates",
        )
    op.drop_table("related_paper_candidates")
    for column in reversed(
        (
            "work_id",
            "document_version_id",
            "document_content_sha256",
            "context_snapshot_id",
            "status",
        )
    ):
        op.drop_index(
            f"ix_related_resource_sets_{column}",
            table_name="related_resource_sets",
        )
    op.drop_table("related_resource_sets")
