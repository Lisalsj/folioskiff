from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

SUPPORTED_PREVIEW_REVISIONS = (
    "0001_foundation",
    "0006_merge_review_decisions",
    "0013_score_rules",
    "0018_annotation_migrations",
    "0022_comparison_reports",
    "0026_model_assets",
    "0027_update_settings",
    "0032_identity_candidate_indexes",
)


def upgrade_to(database: Path, revision: str) -> None:
    from alembic import command
    from alembic.config import Config

    import paper_reader.migrations

    configuration = Config()
    configuration.set_main_option(
        "script_location",
        str(Path(paper_reader.migrations.__file__).parent),
    )
    configuration.set_main_option(
        "sqlalchemy.url",
        f"sqlite+pysqlite:///{database.resolve().as_posix()}",
    )
    command.upgrade(configuration, revision)


def downgrade_to(database: Path, revision: str) -> None:
    from alembic import command
    from alembic.config import Config

    import paper_reader.migrations

    configuration = Config()
    configuration.set_main_option(
        "script_location",
        str(Path(paper_reader.migrations.__file__).parent),
    )
    configuration.set_main_option(
        "sqlalchemy.url",
        f"sqlite+pysqlite:///{database.resolve().as_posix()}",
    )
    command.downgrade(configuration, revision)


@pytest.mark.parametrize("source_revision", SUPPORTED_PREVIEW_REVISIONS)
def test_supported_preview_boundary_upgrades_preserve_foundation_rows(
    tmp_path: Path,
    source_revision: str,
) -> None:
    database = tmp_path / f"{source_revision}.sqlite3"
    job_id = f"boundary-{source_revision}"
    upgrade_to(database, source_revision)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO jobs "
            "(id, kind, status, progress, payload, result, checkpoint, error_code, "
            "error_message, idempotency_key, cancel_requested, created_at, updated_at) "
            "VALUES (?, 'rc.boundary', 'succeeded', 100, ?, NULL, NULL, NULL, NULL, "
            "?, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (
                job_id,
                f'{{"source_revision":"{source_revision}"}}',
                f"rc-boundary:{source_revision}",
            ),
        )
        connection.execute(
            "INSERT INTO job_events "
            "(job_id, sequence, event_type, progress, payload, created_at) "
            "VALUES (?, 1, 'rc.boundary.saved', 100, '{}', CURRENT_TIMESTAMP)",
            (job_id,),
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_to(database, "head")

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        job = connection.execute(
            "SELECT kind, status, progress, payload, pause_requested FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        event = connection.execute(
            "SELECT event_type, progress FROM job_events WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        foreign_key_failure = connection.execute("PRAGMA foreign_key_check").fetchone()
    finally:
        connection.close()

    assert revision == ("0038_remove_legacy_conversations",)
    assert job == (
        "rc.boundary",
        "succeeded",
        100,
        f'{{"source_revision":"{source_revision}"}}',
        0,
    )
    assert event == ("rc.boundary.saved", 100)
    assert quick_check == ("ok",)
    assert foreign_key_failure is None


def test_database_upgrade_reaches_current_revision_idempotently(tmp_path: Path) -> None:
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "state" / "paper-reader.sqlite3"

    upgrade_database(database)
    upgrade_database(database)

    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        work_columns = {row[1] for row in connection.execute("PRAGMA table_info(works)")}
        review_columns = {row[1] for row in connection.execute("PRAGMA table_info(merge_reviews)")}
        document_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_versions)")
        }
        page_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_page_texts)")
        }
        position_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(reading_positions)")
        }
        annotation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(annotations)")
        }
        migration_batch_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(annotation_migration_batches)")
        }
        migration_candidate_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(annotation_migration_candidates)")
        }
        migration_decision_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(annotation_migration_decisions)")
        }
        analysis_report_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(analysis_reports)")
        }
        analysis_fragment_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(analysis_report_fragments)")
        }
        analysis_failure_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(analysis_failure_drafts)")
        }
        comparison_report_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(comparison_reports)")
        }
        comparison_item_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(comparison_report_items)")
        }
        comparison_evidence_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(comparison_paper_evidence)")
        }
        comparison_fragment_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(comparison_fragments)")
        }
        backup_archive_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(backup_archives)")
        }
        restore_receipt_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(restore_receipts)")
        }
        backup_schedule_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(backup_schedule_state)")
        }
        plugin_installation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(plugin_installations)")
        }
        plugin_permission_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(plugin_permission_decisions)")
        }
        published_snapshot_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(published_snapshots)")
        }
        published_work_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(published_snapshot_works)")
        }
        published_file_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(published_snapshot_files)")
        }
        update_settings_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(update_settings)")
        }
        source_revision_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(source_record_revisions)")
        }
        paper_update_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(paper_updates)")
        }
        paper_update_foreign_keys = tuple(
            connection.execute("PRAGMA foreign_key_list(paper_updates)")
        )
        run_candidate_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(run_candidates)")
        }
        run_batch_columns = {row[1] for row in connection.execute("PRAGMA table_info(run_batches)")}
        recommendation_set_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(recommendation_sets)")
        }
        recommendation_item_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(recommendation_items)")
        }
        recommendation_item_foreign_keys = tuple(
            connection.execute("PRAGMA foreign_key_list(recommendation_items)")
        )
        user_preferences_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(user_preferences)")
        }
    finally:
        connection.close()

    assert "jobs" in tables
    assert "job_events" in tables
    assert {
        "works",
        "source_records",
        "source_record_revisions",
        "paper_updates",
        "document_versions",
        "merge_reviews",
        "keyword_set_versions",
        "keyword_confirmations",
        "discovery_plans",
        "plan_catchups",
        "plan_runs",
        "run_batches",
        "run_candidates",
        "metadata_observations",
        "score_snapshots",
        "rating_catalogs",
        "venue_ratings",
        "rating_catalog_selections",
        "library_states",
        "implicit_preference_events",
        "preference_aggregates",
        "interested_authors",
        "score_rule_versions",
        "score_rule_selections",
        "library_entries",
        "document_page_texts",
        "reading_positions",
        "annotations",
        "annotation_migration_batches",
        "annotation_migration_candidates",
        "annotation_migration_decisions",
        "agent_settings",
        "prompt_template_versions",
        "analysis_reports",
        "analysis_report_fragments",
        "analysis_failure_drafts",
        "comparison_reports",
        "comparison_report_items",
        "comparison_paper_evidence",
        "comparison_fragments",
        "backup_archives",
        "backup_schedule_state",
        "restore_receipts",
        "plugin_installations",
        "plugin_permission_decisions",
        "published_snapshots",
        "published_snapshot_works",
        "published_snapshot_files",
        "model_assets",
        "active_model_assets",
        "model_asset_references",
        "update_settings",
        "recommendation_sets",
        "recommendation_items",
        "user_preferences",
        "work_search",
    }.issubset(tables)
    assert {
        "paper_conversations",
        "conversation_messages",
        "conversation_fragments",
        "conversation_checkpoints",
    }.isdisjoint(tables)
    assert {"status", "merged_into_work_id"}.issubset(work_columns)
    assert {"decision_note", "decided_by"}.issubset(review_columns)
    assert {
        "byte_size",
        "original_filename",
        "acquisition_kind",
        "acquisition_url",
        "last_accessed_at",
    }.issubset(document_columns)
    assert {
        "document_version_id",
        "page_number",
        "page_width",
        "page_height",
        "native_text",
        "native_blocks",
        "native_character_count",
        "likely_scanned",
        "ocr_state",
        "ocr_text",
        "ocr_blocks",
        "ocr_confidence",
        "ocr_engine_version",
        "ocr_model_bundle_id",
        "extractor_version",
    }.issubset(page_columns)
    assert {
        "document_version_id",
        "document_content_sha256",
        "page_number",
        "page_count",
        "scale",
        "rotation",
        "progress",
        "created_at",
        "updated_at",
    }.issubset(position_columns)
    assert {
        "id",
        "document_version_id",
        "document_content_sha256",
        "kind",
        "page_number",
        "page_width",
        "page_height",
        "quote_text",
        "context_before",
        "context_after",
        "rects",
        "color",
        "note",
        "created_at",
        "updated_at",
    }.issubset(annotation_columns)
    assert {
        "id",
        "preview_key",
        "algorithm_version",
        "source_document_version_id",
        "source_document_content_sha256",
        "target_document_version_id",
        "target_document_content_sha256",
        "source_annotations_sha256",
        "target_extraction_sha256",
        "created_at",
    }.issubset(migration_batch_columns)
    assert {
        "id",
        "batch_id",
        "source_annotation_id",
        "source_annotation_sha256",
        "source_snapshot",
        "match_state",
        "confidence",
        "target_rects",
        "alternatives",
        "candidate_sha256",
    }.issubset(migration_candidate_columns)
    assert {"candidate_id", "decision", "target_annotation_id", "decided_at"}.issubset(
        migration_decision_columns
    )
    assert {
        "id",
        "work_id",
        "version",
        "job_id",
        "document_version_id",
        "document_content_sha256",
        "evidence_level",
        "evidence_sha256",
        "evidence_manifest",
        "backend_id",
        "requested_model_id",
        "resolved_model_id",
        "reasoning_effort",
        "service_tier",
        "language",
        "template_version_id",
        "complete_prompt",
        "prompt_sha256",
        "network_policy",
        "structured_report",
        "rendered_markdown",
        "external_citations",
        "schema_version",
        "request_fingerprint",
        "report_sha256",
        "created_at",
    }.issubset(analysis_report_columns)
    assert {"job_id", "sequence", "kind", "text", "created_at"}.issubset(analysis_fragment_columns)
    assert {
        "job_id",
        "work_id",
        "error_category",
        "error_message",
        "raw_response",
        "validation_errors",
        "repair_count",
        "request_fingerprint",
        "created_at",
    }.issubset(analysis_failure_columns)
    assert {
        "id",
        "comparison_key",
        "version",
        "job_id",
        "mode",
        "backend_id",
        "structured_report",
        "rendered_markdown",
        "coverage",
        "provenance",
        "request_fingerprint",
        "report_sha256",
    }.issubset(comparison_report_columns)
    assert {
        "report_id",
        "position",
        "work_id",
        "document_version_id",
        "document_content_sha256",
        "evidence_sha256",
        "extraction_status",
        "extraction_id",
        "coverage",
    }.issubset(comparison_item_columns)
    assert {
        "id",
        "source_job_id",
        "position",
        "work_id",
        "evidence_sha256",
        "requested_model_id",
        "resolved_model_id",
        "reasoning_effort",
        "service_tier",
        "prompt_sha256",
        "status",
        "structured_evidence",
        "error_category",
        "extraction_sha256",
        "reused_from_id",
    }.issubset(comparison_evidence_columns)
    assert {
        "job_id",
        "sequence",
        "stage",
        "item_position",
        "kind",
        "text",
    }.issubset(comparison_fragment_columns)
    assert {
        "id",
        "job_id",
        "kind",
        "format_version",
        "app_version",
        "schema_revision",
        "relative_path",
        "byte_size",
        "package_sha256",
        "manifest_sha256",
        "object_count",
        "encrypted",
        "created_at",
        "deleted_at",
    }.issubset(backup_archive_columns)
    assert {
        "id",
        "package_sha256",
        "source_format_version",
        "source_app_version",
        "source_schema_revision",
        "object_count",
        "restored_at",
        "migrated_to_revision",
    }.issubset(restore_receipt_columns)
    assert {"id", "started_at"}.issubset(backup_schedule_columns)
    assert {
        "plugin_id",
        "entry_point_value",
        "distribution_name",
        "distribution_version",
        "manifest_json",
        "manifest_sha256",
        "protocol_minimum",
        "protocol_maximum",
        "compatible",
        "compatibility_reason",
        "enabled",
        "consecutive_failures",
        "circuit_open_reason",
        "last_discovered_at",
        "last_called_at",
        "created_at",
        "updated_at",
    }.issubset(plugin_installation_columns)
    assert {
        "sequence",
        "id",
        "plugin_id",
        "distribution_version",
        "manifest_sha256",
        "permission_kind",
        "resource",
        "decision",
        "decided_at",
    }.issubset(plugin_permission_columns)
    assert {
        "id",
        "slug",
        "schema_version",
        "preview_fingerprint",
        "selection_json",
        "payload_json",
        "payload_sha256",
        "file_manifest",
        "manifest_sha256",
        "snapshot_sha256",
        "created_at",
    }.issubset(published_snapshot_columns)
    assert {
        "snapshot_id",
        "position",
        "canonical_work_id",
        "report_id",
        "score_snapshot_id",
    }.issubset(published_work_columns)
    assert {
        "snapshot_id",
        "position",
        "relative_path",
        "media_type",
        "byte_size",
        "sha256",
        "content",
    }.issubset(published_file_columns)
    assert {
        "id",
        "automatic_checks_enabled",
        "last_checked_at",
        "last_status",
        "last_error_code",
        "cached_release_json",
        "updated_at",
    }.issubset(update_settings_columns)
    assert {
        "id",
        "source_record_id",
        "work_id",
        "revision_kind",
        "payload_sha256",
        "snapshot",
        "changed_fields",
        "field_provenance",
        "source_updated_at",
        "fetched_at",
        "created_at",
    }.issubset(source_revision_columns)
    assert {
        "id",
        "run_id",
        "work_id",
        "source_record_id",
        "previous_revision_id",
        "current_revision_id",
        "update_type",
        "visibility",
        "state",
        "evidence",
        "detected_at",
        "acted_at",
    }.issubset(paper_update_columns)
    assert any(
        row[2] == "plan_runs" and row[3] == "run_id" and row[4] == "id" and row[6] == "SET NULL"
        for row in paper_update_foreign_keys
    )
    assert "discovery_kind" in run_candidate_columns
    assert {
        "new_work_count",
        "update_count",
        "duplicate_count",
        "scanned_count",
    }.issubset(run_batch_columns)
    assert {
        "run_id",
        "keyword_version_id",
        "version",
        "ranking_rule_version",
        "fixed_historical_months",
        "requested_limit",
        "candidate_cap",
        "candidate_count",
        "recommendation_count",
        "exploration_target",
        "exploration_count",
        "degradation_reasons",
        "set_sha256",
    }.issubset(recommendation_set_columns)
    assert {
        "recommendation_set_id",
        "scored_work_id",
        "default_position",
        "selected",
        "historical_match",
        "eligible_by_default",
        "channel",
        "relevance_snapshot_id",
        "quality_snapshot_id",
        "freshness_snapshot_id",
        "preference_snapshot_id",
        "composite_snapshot_id",
        "ranking_evidence",
        "item_sha256",
    }.issubset(recommendation_item_columns)
    assert {
        (row[2], row[3], row[4], row[6]) for row in recommendation_item_foreign_keys
    }.issuperset(
        {
            ("works", "scored_work_id", "id", "RESTRICT"),
            ("recommendation_sets", "recommendation_set_id", "id", "RESTRICT"),
            ("score_snapshots", "relevance_snapshot_id", "id", "RESTRICT"),
            ("score_snapshots", "quality_snapshot_id", "id", "RESTRICT"),
            ("score_snapshots", "freshness_snapshot_id", "id", "RESTRICT"),
            ("score_snapshots", "preference_snapshot_id", "id", "RESTRICT"),
            ("score_snapshots", "composite_snapshot_id", "id", "RESTRICT"),
        }
    )
    assert {
        "id",
        "timezone_mode",
        "timezone_name",
        "theme",
        "default_agent_backend",
        "default_agent_model_id",
        "default_reasoning_effort",
        "report_language",
        "translation_source_language",
        "translation_target_language",
        "created_at",
        "updated_at",
    }.issubset(user_preferences_columns)
    assert revision == ("0038_remove_legacy_conversations",)


def test_user_preferences_schema_seeds_defaults_and_round_trips(tmp_path: Path) -> None:
    database = tmp_path / "user-preferences-migration.sqlite3"
    upgrade_to(database, "0029_recommendation_sets")
    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT timezone_mode, timezone_name, theme FROM user_preferences WHERE id = 'default'"
        ).fetchone()
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        connection.close()
    assert row == ("system", None, "system")
    assert revision == ("0038_remove_legacy_conversations",)

    downgrade_to(database, "0029_recommendation_sets")
    connection = sqlite3.connect(database)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'user_preferences'"
        ).fetchone()
    finally:
        connection.close()
    assert table is None

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        restored = connection.execute(
            "SELECT timezone_mode, timezone_name, theme FROM user_preferences"
        ).fetchone()
    finally:
        connection.close()
    assert restored == ("system", None, "system")


def test_library_query_indexes_upgrade_and_downgrade_without_rewriting_0030(
    tmp_path: Path,
) -> None:
    database = tmp_path / "library-query-indexes.sqlite3"
    upgrade_to(database, "0030_user_preferences")
    upgrade_to(database, "0031_library_query_indexes")
    connection = sqlite3.connect(database)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        library_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list('library_entries')")
        }
        document_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list('document_versions')")
        }
        work_indexes = {row[1] for row in connection.execute("PRAGMA index_list('works')")}
    finally:
        connection.close()
    assert revision == ("0031_library_query_indexes",)
    assert "ix_library_entries_created_work" in library_indexes
    assert "ix_document_versions_work_representative" in document_indexes
    assert "ix_works_status_doi" in work_indexes

    downgrade_to(database, "0030_user_preferences")
    connection = sqlite3.connect(database)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        remaining = {
            row[1]
            for table in ("library_entries", "document_versions", "works")
            for row in connection.execute(f"PRAGMA index_list('{table}')")
        }
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        connection.close()
    assert revision == ("0030_user_preferences",)
    assert "ix_library_entries_created_work" not in remaining
    assert "ix_document_versions_work_representative" not in remaining
    assert "ix_works_status_doi" not in remaining
    assert {"library_entries", "document_versions", "works"}.issubset(tables)


def test_identity_candidate_indexes_upgrade_and_downgrade_without_rewriting_0031(
    tmp_path: Path,
) -> None:
    database = tmp_path / "identity-candidate-indexes.sqlite3"
    upgrade_to(database, "0031_library_query_indexes")
    upgrade_to(database, "0032_identity_candidate_indexes")
    connection = sqlite3.connect(database)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        work_indexes = {row[1] for row in connection.execute("PRAGMA index_list('works')")}
    finally:
        connection.close()
    assert revision == ("0032_identity_candidate_indexes",)
    assert "ix_works_status_normalized_title" in work_indexes
    assert "ix_works_status_published_at" in work_indexes

    downgrade_to(database, "0031_library_query_indexes")
    connection = sqlite3.connect(database)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        remaining = {row[1] for row in connection.execute("PRAGMA index_list('works')")}
    finally:
        connection.close()
    assert revision == ("0031_library_query_indexes",)
    assert "ix_works_status_normalized_title" not in remaining
    assert "ix_works_status_published_at" not in remaining
    assert "ix_works_status_doi" in remaining


def test_onboarding_state_migration_seeds_singleton_and_round_trips(tmp_path: Path) -> None:
    database = tmp_path / "onboarding-state.sqlite3"
    upgrade_to(database, "0032_identity_candidate_indexes")
    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        row = connection.execute(
            "SELECT id, current_step, step_statuses, privacy_acknowledged_at, completed_at "
            "FROM onboarding_state"
        ).fetchone()
    finally:
        connection.close()

    assert revision == ("0038_remove_legacy_conversations",)
    assert row == (
        "default",
        "storage_privacy",
        '{"agents":"pending","reading_preferences":"pending",'
        '"sources":"pending","storage_privacy":"pending"}',
        None,
        None,
    )

    downgrade_to(database, "0032_identity_candidate_indexes")
    connection = sqlite3.connect(database)
    try:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'onboarding_state'"
        ).fetchone()
    finally:
        connection.close()
    assert table is None


def test_agent_language_preferences_migration_preserves_existing_rows_and_round_trips(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-language-preferences.sqlite3"
    upgrade_to(database, "0033_onboarding_state")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE user_preferences SET timezone_mode = 'custom', "
            "timezone_name = 'Europe/Berlin', theme = 'dark' WHERE id = 'default'"
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        columns = {row[1] for row in connection.execute("PRAGMA table_info(user_preferences)")}
        row = connection.execute(
            "SELECT timezone_mode, timezone_name, theme, default_agent_backend, "
            "default_agent_model_id, default_reasoning_effort, report_language, "
            "translation_source_language, translation_target_language "
            "FROM user_preferences WHERE id = 'default'"
        ).fetchone()
    finally:
        connection.close()

    assert revision == ("0038_remove_legacy_conversations",)
    assert {
        "default_agent_backend",
        "default_agent_model_id",
        "default_reasoning_effort",
        "report_language",
        "translation_source_language",
        "translation_target_language",
    }.issubset(columns)
    assert row == (
        "custom",
        "Europe/Berlin",
        "dark",
        "codex_cli",
        None,
        "medium",
        "zh-CN",
        "en",
        "zh-CN",
    )

    downgrade_to(database, "0033_onboarding_state")
    connection = sqlite3.connect(database)
    try:
        downgraded_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(user_preferences)")
        }
        restored = connection.execute(
            "SELECT timezone_mode, timezone_name, theme FROM user_preferences WHERE id = 'default'"
        ).fetchone()
    finally:
        connection.close()

    assert "default_agent_backend" not in downgraded_columns
    assert restored == ("custom", "Europe/Berlin", "dark")


def test_recommendation_set_schema_round_trips_from_previous_head(tmp_path: Path) -> None:
    database = tmp_path / "recommendation-set-migration.sqlite3"
    upgrade_to(database, "0028c_paper_update_runs")
    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        upgraded_tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()
    assert {"recommendation_sets", "recommendation_items"}.issubset(upgraded_tables)

    downgrade_to(database, "0028c_paper_update_runs")
    connection = sqlite3.connect(database)
    try:
        downgraded_tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        connection.close()
    assert "recommendation_sets" not in downgraded_tables
    assert "recommendation_items" not in downgraded_tables
    assert "score_snapshots" in downgraded_tables
    assert revision == ("0028c_paper_update_runs",)

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        restored = connection.execute("SELECT COUNT(*) FROM recommendation_sets").fetchone()
    finally:
        connection.close()
    assert restored == (0,)


def test_backup_catalog_migration_round_trip_preserves_existing_jobs(tmp_path: Path) -> None:
    database = tmp_path / "backup-migration.sqlite3"
    upgrade_to(database, "0022_comparison_reports")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO jobs (id, kind, status, progress, payload, result, checkpoint, "
            "error_code, error_message, idempotency_key, cancel_requested, created_at, "
            "updated_at) VALUES ('job-before-backup', 'fixture', 'succeeded', 100, '{}', "
            "NULL, NULL, NULL, NULL, NULL, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO backup_archives (id, job_id, kind, format_version, app_version, "
            "schema_revision, relative_path, byte_size, package_sha256, manifest_sha256, "
            "object_count, encrypted, created_at, deleted_at) VALUES "
            "('backup-a', 'job-before-backup', 'manual', 'paper-reader-backup-v1', "
            "'0.6.0', '0023_backup_archives', 'backup-a.paper-reader-backup', 12, ?, ?, "
            "0, 0, CURRENT_TIMESTAMP, NULL)",
            ("a" * 64, "b" * 64),
        )
        connection.commit()
    finally:
        connection.close()

    downgrade_to(database, "0022_comparison_reports")
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        job = connection.execute(
            "SELECT id, status FROM jobs WHERE id = 'job-before-backup'"
        ).fetchone()
    finally:
        connection.close()
    assert "backup_archives" not in tables
    assert "backup_schedule_state" not in tables
    assert "restore_receipts" not in tables
    assert job == ("job-before-backup", "succeeded")

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        backup_count = connection.execute("SELECT COUNT(*) FROM backup_archives").fetchone()
        job = connection.execute(
            "SELECT id, status FROM jobs WHERE id = 'job-before-backup'"
        ).fetchone()
    finally:
        connection.close()
    assert backup_count == (0,)
    assert job == ("job-before-backup", "succeeded")


def test_plugin_registry_migration_round_trip_preserves_backup_catalog(tmp_path: Path) -> None:
    database = tmp_path / "plugin-registry-migration.sqlite3"
    upgrade_to(database, "0023_backup_archives")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO jobs (id, kind, status, progress, payload, result, checkpoint, "
            "error_code, error_message, idempotency_key, cancel_requested, created_at, "
            "updated_at) VALUES ('plugin-migration-job', 'fixture', 'succeeded', 100, '{}', "
            "NULL, NULL, NULL, NULL, NULL, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO backup_archives (id, job_id, kind, format_version, app_version, "
            "schema_revision, relative_path, byte_size, package_sha256, manifest_sha256, "
            "object_count, encrypted, created_at, deleted_at) VALUES "
            "('plugin-migration-backup', 'plugin-migration-job', 'manual', "
            "'paper-reader-backup-v1', '0.6.0', '0023_backup_archives', "
            "'plugin-migration.paper-reader-backup', 12, ?, ?, 0, 0, CURRENT_TIMESTAMP, NULL)",
            ("a" * 64, "b" * 64),
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO plugin_installations (plugin_id, entry_point_value, "
            "distribution_name, distribution_version, manifest_json, manifest_sha256, "
            "protocol_minimum, protocol_maximum, compatible, compatibility_reason, enabled, "
            "consecutive_failures, circuit_open_reason, last_discovered_at, last_called_at, "
            "created_at, updated_at) VALUES ('fixture.source', 'fixture.plugin:plugin', "
            "'fixture-dist', '1.0.0', '{}', ?, '1.0', '1.0', 1, NULL, 0, 0, NULL, "
            "CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            ("c" * 64,),
        )
        connection.commit()
    finally:
        connection.close()

    downgrade_to(database, "0023_backup_archives")
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        backup = connection.execute(
            "SELECT id, package_sha256 FROM backup_archives WHERE id = 'plugin-migration-backup'"
        ).fetchone()
    finally:
        connection.close()
    assert "plugin_installations" not in tables
    assert "plugin_permission_decisions" not in tables
    assert backup == ("plugin-migration-backup", "a" * 64)

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        restored_plugins = connection.execute(
            "SELECT COUNT(*) FROM plugin_installations"
        ).fetchone()
        preserved_backup = connection.execute(
            "SELECT id FROM backup_archives WHERE id = 'plugin-migration-backup'"
        ).fetchone()
    finally:
        connection.close()
    assert restored_plugins == (0,)
    assert preserved_backup == ("plugin-migration-backup",)


def test_published_snapshot_migration_round_trip_preserves_plugin_registry(
    tmp_path: Path,
) -> None:
    database = tmp_path / "published-snapshot-migration.sqlite3"
    upgrade_to(database, "0024_plugin_registry")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO plugin_installations (plugin_id, entry_point_value, "
            "distribution_name, distribution_version, manifest_json, manifest_sha256, "
            "protocol_minimum, protocol_maximum, compatible, compatibility_reason, enabled, "
            "consecutive_failures, circuit_open_reason, last_discovered_at, last_called_at, "
            "created_at, updated_at) VALUES ('fixture.publisher', 'fixture:publisher', "
            "'fixture-publisher', '1.0.0', '{}', ?, '1.0', '1.0', 1, NULL, 0, 0, NULL, "
            "CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            ("9" * 64,),
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO published_snapshots (id, slug, schema_version, preview_fingerprint, "
            "selection_json, payload_json, payload_sha256, file_manifest, manifest_sha256, "
            "snapshot_sha256, created_at) VALUES ('snapshot-migration', 'fixture', "
            "'paper-reader-publication-v1', ?, '{}', '{}', ?, '[]', ?, ?, CURRENT_TIMESTAMP)",
            ("1" * 64, "2" * 64, "3" * 64, "4" * 64),
        )
        connection.commit()
    finally:
        connection.close()

    downgrade_to(database, "0024_plugin_registry")
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        plugin = connection.execute(
            "SELECT plugin_id FROM plugin_installations WHERE plugin_id = 'fixture.publisher'"
        ).fetchone()
    finally:
        connection.close()
    assert "published_snapshots" not in tables
    assert "published_snapshot_works" not in tables
    assert "published_snapshot_files" not in tables
    assert plugin == ("fixture.publisher",)

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        snapshots = connection.execute("SELECT COUNT(*) FROM published_snapshots").fetchone()
        preserved_plugin = connection.execute(
            "SELECT plugin_id FROM plugin_installations WHERE plugin_id = 'fixture.publisher'"
        ).fetchone()
    finally:
        connection.close()
    assert snapshots == (0,)
    assert preserved_plugin == ("fixture.publisher",)


def test_merge_decision_migration_preserves_existing_identity_rows(tmp_path: Path) -> None:
    database = tmp_path / "existing.sqlite3"
    upgrade_to(database, "0005_discovery_runs")
    connection = sqlite3.connect(database)
    try:
        for work_id, title in (("work-a", "Paper A"), ("work-b", "Paper B")):
            connection.execute(
                "INSERT INTO works "
                "(id, canonical_title, normalized_title, authors, abstract, doi, "
                "published_at, created_at, updated_at) "
                "VALUES (?, ?, ?, '[]', NULL, NULL, NULL, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)",
                (work_id, title, title.casefold()),
            )
        connection.execute(
            "INSERT INTO merge_reviews "
            "(id, candidate_work_id, matched_work_id, similarity, evidence, status, "
            "created_at, decided_at) "
            "VALUES ('review-a', 'work-b', 'work-a', 0.8, '{}', 'pending', "
            "CURRENT_TIMESTAMP, NULL)"
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_to(database, "head")

    connection = sqlite3.connect(database)
    try:
        works = connection.execute(
            "SELECT id, status, merged_into_work_id FROM works ORDER BY id"
        ).fetchall()
        review = connection.execute(
            "SELECT status, decision_note, decided_by FROM merge_reviews WHERE id = 'review-a'"
        ).fetchone()
    finally:
        connection.close()
    assert works == [("work-a", "active", None), ("work-b", "active", None)]
    assert review == ("pending", None, None)


def test_reader_migration_preserves_documents_and_can_downgrade_then_upgrade(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reader-migration.sqlite3"
    upgrade_to(database, "0013_score_rules")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO works "
            "(id, canonical_title, normalized_title, authors, abstract, doi, "
            "published_at, status, merged_into_work_id, created_at, updated_at) "
            "VALUES ('work-reader', 'Reader Work', 'reader work', '[]', NULL, NULL, "
            "NULL, 'active', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO document_versions "
            "(id, work_id, source_record_id, content_sha256, access_status, media_type, "
            "completeness, source_updated_at, extraction_status, relative_path, created_at) "
            "VALUES ('document-reader', 'work-reader', NULL, ?, 'open', "
            "'application/pdf', 100, NULL, 'pending', 'objects/paper.pdf', "
            "CURRENT_TIMESTAMP)",
            ("a" * 64,),
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        upgraded = connection.execute(
            "SELECT content_sha256, byte_size, acquisition_kind, relative_path "
            "FROM document_versions WHERE id = 'document-reader'"
        ).fetchone()
    finally:
        connection.close()
    assert upgraded == ("a" * 64, 0, "source", "objects/paper.pdf")

    downgrade_to(database, "0013_score_rules")
    connection = sqlite3.connect(database)
    try:
        downgraded_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_versions)")
        }
        library_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'library_entries'"
        ).fetchone()
    finally:
        connection.close()
    assert "byte_size" not in downgraded_columns
    assert library_table is None

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        restored = connection.execute(
            "SELECT content_sha256, byte_size, acquisition_kind "
            "FROM document_versions WHERE id = 'document-reader'"
        ).fetchone()
    finally:
        connection.close()
    assert restored == ("a" * 64, 0, "source")


def test_reader_text_migration_preserves_documents_across_downgrade_and_upgrade(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reader-text-migration.sqlite3"
    upgrade_to(database, "0014_reader_library")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO works "
            "(id, canonical_title, normalized_title, authors, abstract, doi, "
            "published_at, status, merged_into_work_id, created_at, updated_at) "
            "VALUES ('work-text', 'Text Work', 'text work', '[]', NULL, NULL, "
            "NULL, 'active', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO document_versions "
            "(id, work_id, source_record_id, content_sha256, access_status, media_type, "
            "completeness, source_updated_at, extraction_status, relative_path, byte_size, "
            "original_filename, acquisition_kind, acquisition_url, last_accessed_at, "
            "created_at) VALUES ('document-text', 'work-text', NULL, ?, 'open', "
            "'application/pdf', 100, NULL, 'pending', 'objects/text.pdf', 12, "
            "'text.pdf', 'download', NULL, NULL, CURRENT_TIMESTAMP)",
            ("b" * 64,),
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO document_page_texts "
            "(document_version_id, page_number, page_width, page_height, native_text, "
            "native_blocks, native_character_count, likely_scanned, ocr_state, ocr_text, "
            "ocr_blocks, ocr_confidence, extractor_version, created_at, updated_at) "
            "VALUES ('document-text', 1, 420, 595, 'evidence', '[]', 8, 0, "
            "'not_needed', NULL, '[]', NULL, 'fixture-v1', CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP)"
        )
        connection.commit()
    finally:
        connection.close()

    downgrade_to(database, "0014_reader_library")
    connection = sqlite3.connect(database)
    try:
        page_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'document_page_texts'"
        ).fetchone()
        document = connection.execute(
            "SELECT content_sha256, extraction_status FROM document_versions "
            "WHERE id = 'document-text'"
        ).fetchone()
    finally:
        connection.close()
    assert page_table is None
    assert document == ("b" * 64, "pending")

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        restored_document = connection.execute(
            "SELECT content_sha256 FROM document_versions WHERE id = 'document-text'"
        ).fetchone()
        page_count = connection.execute("SELECT COUNT(*) FROM document_page_texts").fetchone()
    finally:
        connection.close()
    assert restored_document == ("b" * 64,)
    assert page_count == (0,)


def test_reader_ocr_migration_preserves_page_evidence_across_round_trip(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reader-ocr-migration.sqlite3"
    upgrade_to(database, "0015_reader_text")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO works "
            "(id, canonical_title, normalized_title, authors, abstract, doi, "
            "published_at, status, merged_into_work_id, created_at, updated_at) "
            "VALUES ('work-ocr', 'OCR Work', 'ocr work', '[]', NULL, NULL, "
            "NULL, 'active', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO document_versions "
            "(id, work_id, source_record_id, content_sha256, access_status, media_type, "
            "completeness, source_updated_at, extraction_status, relative_path, byte_size, "
            "original_filename, acquisition_kind, acquisition_url, last_accessed_at, "
            "created_at) VALUES ('document-ocr', 'work-ocr', NULL, ?, 'open', "
            "'application/pdf', 100, NULL, 'complete', 'objects/ocr.pdf', 12, "
            "'ocr.pdf', 'download', NULL, NULL, CURRENT_TIMESTAMP)",
            ("c" * 64,),
        )
        connection.execute(
            "INSERT INTO document_page_texts "
            "(document_version_id, page_number, page_width, page_height, native_text, "
            "native_blocks, native_character_count, likely_scanned, ocr_state, ocr_text, "
            "ocr_blocks, ocr_confidence, extractor_version, created_at, updated_at) "
            "VALUES ('document-ocr', 1, 420, 595, '', '[]', 0, 1, 'pending', NULL, "
            "'[]', NULL, 'fixture-v1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(document_page_texts)")}
        row = connection.execute(
            "SELECT native_text, ocr_engine_version, ocr_model_bundle_id "
            "FROM document_page_texts WHERE document_version_id = 'document-ocr'"
        ).fetchone()
    finally:
        connection.close()
    assert {"ocr_engine_version", "ocr_model_bundle_id"}.issubset(columns)
    assert row == ("", None, None)

    downgrade_to(database, "0015_reader_text")
    connection = sqlite3.connect(database)
    try:
        downgraded_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_page_texts)")
        }
        preserved = connection.execute(
            "SELECT native_text, ocr_state FROM document_page_texts "
            "WHERE document_version_id = 'document-ocr'"
        ).fetchone()
    finally:
        connection.close()
    assert "ocr_engine_version" not in downgraded_columns
    assert "ocr_model_bundle_id" not in downgraded_columns
    assert preserved == ("", "pending")

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        restored = connection.execute(
            "SELECT native_text, ocr_engine_version, ocr_model_bundle_id "
            "FROM document_page_texts WHERE document_version_id = 'document-ocr'"
        ).fetchone()
    finally:
        connection.close()
    assert restored == ("", None, None)


def test_reader_annotation_migration_round_trip_preserves_document_versions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reader-annotations-migration.sqlite3"
    upgrade_to(database, "0016_reader_ocr")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO works "
            "(id, canonical_title, normalized_title, authors, abstract, doi, "
            "published_at, status, merged_into_work_id, created_at, updated_at) "
            "VALUES ('work-annotation', 'Annotation Work', 'annotation work', '[]', "
            "NULL, NULL, NULL, 'active', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO document_versions "
            "(id, work_id, source_record_id, content_sha256, access_status, media_type, "
            "completeness, source_updated_at, extraction_status, relative_path, byte_size, "
            "original_filename, acquisition_kind, acquisition_url, last_accessed_at, "
            "created_at) VALUES ('document-annotation', 'work-annotation', NULL, ?, "
            "'user_provided', 'application/pdf', 100, NULL, 'complete', "
            "'objects/annotation.pdf', 12, 'annotation.pdf', 'manual_import', NULL, NULL, "
            "CURRENT_TIMESTAMP)",
            ("d" * 64,),
        )
        connection.execute(
            "INSERT INTO library_entries (work_id, added_via, created_at, updated_at) "
            "VALUES ('work-annotation', 'pdf', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO reading_positions "
            "(document_version_id, document_content_sha256, page_number, page_count, "
            "scale, rotation, progress, created_at, updated_at) VALUES "
            "('document-annotation', ?, 2, 4, 1.25, 90, 50, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP)",
            ("d" * 64,),
        )
        connection.execute(
            "INSERT INTO annotations "
            "(id, document_version_id, document_content_sha256, kind, page_number, "
            "page_width, page_height, quote_text, context_before, context_after, rects, "
            "color, note, created_at, updated_at) VALUES "
            "('annotation-a', 'document-annotation', ?, 'highlight', 2, 420, 595, "
            "'evidence', 'before', 'after', '[]', 'amber', NULL, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP)",
            ("d" * 64,),
        )
        connection.commit()
    finally:
        connection.close()

    downgrade_to(database, "0016_reader_ocr")
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        document = connection.execute(
            "SELECT content_sha256, relative_path FROM document_versions "
            "WHERE id = 'document-annotation'"
        ).fetchone()
    finally:
        connection.close()
    assert "annotations" not in tables
    assert "reading_positions" not in tables
    assert document == ("d" * 64, "objects/annotation.pdf")

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        restored_document = connection.execute(
            "SELECT content_sha256 FROM document_versions WHERE id = 'document-annotation'"
        ).fetchone()
        annotation_count = connection.execute("SELECT COUNT(*) FROM annotations").fetchone()
        position_count = connection.execute("SELECT COUNT(*) FROM reading_positions").fetchone()
    finally:
        connection.close()
    assert restored_document == ("d" * 64,)
    assert annotation_count == (0,)
    assert position_count == (0,)


def test_annotation_migration_audit_rows_are_immutable_and_schema_round_trips(
    tmp_path: Path,
) -> None:
    database = tmp_path / "annotation-migration-audit.sqlite3"
    upgrade_to(database, "0017_reader_annotations")
    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO annotation_migration_batches "
            "(id, preview_key, algorithm_version, source_document_version_id, "
            "source_document_content_sha256, target_document_version_id, "
            "target_document_content_sha256, source_annotations_sha256, "
            "target_extraction_sha256, created_at) VALUES "
            "('batch-a', ?, 'annotation-migration-v1', 'source-a', ?, 'target-a', ?, ?, ?, "
            "CURRENT_TIMESTAMP)",
            ("1" * 64, "2" * 64, "3" * 64, "4" * 64, "5" * 64),
        )
        connection.execute(
            "INSERT INTO annotation_migration_candidates "
            "(id, batch_id, source_annotation_id, source_annotation_sha256, "
            "source_snapshot, order_index, match_state, confidence, score, "
            "target_page_number, target_page_width, target_page_height, target_quote_text, "
            "target_context_before, target_context_after, target_rects, text_source, "
            "alternatives, candidate_sha256, created_at) VALUES "
            "('candidate-a', 'batch-a', 'annotation-a', ?, ?, 0, 'orphaned', 'none', 0, "
            "NULL, NULL, NULL, NULL, NULL, NULL, '[]', NULL, '[]', ?, CURRENT_TIMESTAMP)",
            (
                "6" * 64,
                '{"kind":"bookmark","page_number":1,"color":"amber"}',
                "7" * 64,
            ),
        )
        connection.execute(
            "INSERT INTO annotation_migration_decisions "
            "(candidate_id, decision, target_annotation_id, decided_at) "
            "VALUES ('candidate-a', 'reject', NULL, CURRENT_TIMESTAMP)"
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE annotation_migration_candidates SET score = 0.5 WHERE id = 'candidate-a'"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM annotation_migration_decisions WHERE candidate_id = 'candidate-a'"
            )
        connection.rollback()
    finally:
        connection.close()

    downgrade_to(database, "0017_reader_annotations")
    connection = sqlite3.connect(database)
    try:
        removed = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name LIKE 'annotation_migration_%'"
            )
        }
        annotation_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'annotations'"
        ).fetchone()
    finally:
        connection.close()
    assert removed == set()
    assert annotation_table == ("annotations",)

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        restored = connection.execute(
            "SELECT COUNT(*) FROM annotation_migration_batches"
        ).fetchone()
    finally:
        connection.close()
    assert restored == (0,)


def test_legacy_conversation_storage_is_removed_and_schema_round_trips(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-conversation-removal.sqlite3"
    legacy_tables = {
        "paper_conversations",
        "conversation_messages",
        "conversation_fragments",
        "conversation_checkpoints",
    }

    upgrade_to(database, "0037_related_resources")
    connection = sqlite3.connect(database)
    try:
        before = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()
    assert legacy_tables.issubset(before)

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        after = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        foreign_key_failure = connection.execute("PRAGMA foreign_key_check").fetchone()
    finally:
        connection.close()
    assert revision == ("0038_remove_legacy_conversations",)
    assert legacy_tables.isdisjoint(after)
    assert quick_check == ("ok",)
    assert foreign_key_failure is None

    downgrade_to(database, "0037_related_resources")
    connection = sqlite3.connect(database)
    try:
        restored = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()
    assert legacy_tables.issubset(restored)

    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        removed_again = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()
    assert legacy_tables.isdisjoint(removed_again)


def test_assistant_core_schema_is_normalized_and_round_trips(tmp_path: Path) -> None:
    database = tmp_path / "assistant-core.sqlite3"
    upgrade_to(database, "0034_agent_language_preferences")
    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        task_columns = {row[1] for row in connection.execute("PRAGMA table_info(assistant_tasks)")}
        snapshot_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(assistant_context_snapshots)")
        }
        run_foreign_keys = tuple(
            connection.execute("PRAGMA foreign_key_list(assistant_agent_runs)")
        )
        fragment_foreign_keys = tuple(
            connection.execute("PRAGMA foreign_key_list(assistant_fragments)")
        )
        session_indexes = tuple(connection.execute("PRAGMA index_list(assistant_sessions)"))
        snapshot_indexes = tuple(
            connection.execute("PRAGMA index_list(assistant_context_snapshots)")
        )
        task_indexes = tuple(connection.execute("PRAGMA index_list(assistant_tasks)"))
        unique_task_index_columns = {
            tuple(row[2] for row in connection.execute(f'PRAGMA index_info("{index_row[1]}")'))
            for index_row in task_indexes
            if index_row[2] == 1
        }
        immutable_triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND name LIKE 'assistant_%_immutable_%'"
            )
        }
    finally:
        connection.close()

    assistant_tables = {
        "assistant_sessions",
        "assistant_context_snapshots",
        "assistant_tasks",
        "assistant_agent_runs",
        "assistant_messages",
        "assistant_fragments",
        "assistant_checkpoints",
    }
    assert revision == ("0038_remove_legacy_conversations",)
    assert assistant_tables.issubset(tables)
    assert {
        "session_id",
        "context_snapshot_id",
        "mode",
        "status",
        "network_policy",
        "backend_id",
        "requested_model_id",
        "reasoning_effort",
        "service_tier",
        "output_language",
        "idempotency_key",
        "command_sha256",
        "job_id",
    }.issubset(task_columns)
    assert {
        "work_id",
        "document_version_id",
        "document_content_sha256",
        "scope",
        "page_numbers",
        "selection",
        "annotation_ids",
        "evidence",
        "evidence_sha256",
        "snapshot_sha256",
    }.issubset(snapshot_columns)
    assert any(
        row[2] == "assistant_tasks"
        and row[3] == "task_id"
        and row[4] == "id"
        and row[6] == "CASCADE"
        for row in run_foreign_keys
    )
    assert {(row[2], row[3], row[4], row[6]) for row in fragment_foreign_keys}.issuperset(
        {
            ("assistant_tasks", "task_id", "id", "CASCADE"),
            ("assistant_agent_runs", "run_id", "id", "CASCADE"),
        }
    )
    assert {("idempotency_key",), ("job_id",)}.issubset(unique_task_index_columns)
    assert "ix_assistant_sessions_work_id" not in {row[1] for row in session_indexes}
    assert "ix_assistant_context_snapshots_snapshot_sha256" not in {
        row[1] for row in snapshot_indexes
    }
    assert {
        "ix_assistant_tasks_idempotency_key",
        "ix_assistant_tasks_job_id",
    }.isdisjoint({row[1] for row in task_indexes})
    assert immutable_triggers == {
        f"{table}_{action}"
        for table in (
            "assistant_context_snapshots",
            "assistant_messages",
            "assistant_fragments",
            "assistant_checkpoints",
        )
        for action in ("immutable_update", "immutable_delete")
    }

    downgrade_to(database, "0034_agent_language_preferences")
    connection = sqlite3.connect(database)
    try:
        downgraded_revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        remaining = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()
    assert downgraded_revision == ("0034_agent_language_preferences",)
    assert assistant_tables.isdisjoint(remaining)
    assert {"works", "document_versions", "jobs", "user_preferences"}.issubset(remaining)


def test_translation_schema_is_normalized_and_round_trips(tmp_path: Path) -> None:
    database = tmp_path / "translations.sqlite3"
    upgrade_to(database, "0035_assistant_core")
    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        artifact_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(translation_artifacts)")
        }
        segment_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(translation_segments)")
        }
        term_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(translation_terms)")
        }
        export_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(translation_exports)")
        }
        artifact_foreign_keys = tuple(
            connection.execute("PRAGMA foreign_key_list(translation_artifacts)")
        )
        segment_foreign_keys = tuple(
            connection.execute("PRAGMA foreign_key_list(translation_segments)")
        )
        unique_indexes: dict[str, set[tuple[str, ...]]] = {}
        for table in (
            "translation_artifacts",
            "translation_segments",
            "translation_terms",
            "translation_exports",
        ):
            unique_indexes[table] = {
                tuple(row[2] for row in connection.execute(f'PRAGMA index_info("{index_row[1]}")'))
                for index_row in connection.execute(f'PRAGMA index_list("{table}")')
                if index_row[2] == 1
            }
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        foreign_key_failure = connection.execute("PRAGMA foreign_key_check").fetchone()
    finally:
        connection.close()

    translation_tables = {
        "translation_artifacts",
        "translation_segments",
        "translation_terms",
        "translation_exports",
    }
    assert revision == ("0038_remove_legacy_conversations",)
    assert translation_tables.issubset(tables)
    assert {
        "id",
        "work_id",
        "version",
        "document_version_id",
        "document_content_sha256",
        "context_snapshot_id",
        "task_id",
        "job_id",
        "source_language",
        "target_language",
        "scope",
        "status",
        "evidence_level",
        "prompt_version",
        "created_at",
        "updated_at",
        "completed_at",
    }.issubset(artifact_columns)
    assert {
        "id",
        "artifact_id",
        "segment_index",
        "page_number",
        "block_numbers",
        "source_text",
        "source_sha256",
        "protected_map",
        "translated_text",
        "source_kind",
        "ocr_confidence",
        "status",
        "diagnostic",
    }.issubset(segment_columns)
    assert {
        "id",
        "artifact_id",
        "normalized_source_term",
        "source_term",
        "target_term",
        "origin",
        "locked",
        "created_at",
        "updated_at",
    }.issubset(term_columns)
    assert {
        "id",
        "artifact_id",
        "format",
        "content_sha256",
        "relative_path",
        "created_at",
    }.issubset(export_columns)
    assert {(row[2], row[3], row[4], row[6]) for row in artifact_foreign_keys}.issuperset(
        {
            ("works", "work_id", "id", "RESTRICT"),
            ("document_versions", "document_version_id", "id", "RESTRICT"),
            (
                "assistant_context_snapshots",
                "context_snapshot_id",
                "id",
                "RESTRICT",
            ),
            ("assistant_tasks", "task_id", "id", "RESTRICT"),
            ("jobs", "job_id", "id", "RESTRICT"),
        }
    )
    assert any(
        row[2] == "translation_artifacts"
        and row[3] == "artifact_id"
        and row[4] == "id"
        and row[6] == "CASCADE"
        for row in segment_foreign_keys
    )
    assert {("work_id", "version"), ("task_id",), ("job_id",)}.issubset(
        unique_indexes["translation_artifacts"]
    )
    assert ("artifact_id", "segment_index") in unique_indexes["translation_segments"]
    assert ("artifact_id", "normalized_source_term") in unique_indexes["translation_terms"]
    assert ("artifact_id", "format", "content_sha256") in unique_indexes["translation_exports"]
    assert quick_check == ("ok",)
    assert foreign_key_failure is None

    downgrade_to(database, "0035_assistant_core")
    connection = sqlite3.connect(database)
    try:
        downgraded_revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        remaining = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()
    assert downgraded_revision == ("0035_assistant_core",)
    assert translation_tables.isdisjoint(remaining)


def test_related_resource_schema_is_normalized_and_round_trips(tmp_path: Path) -> None:
    database = tmp_path / "related-resources.sqlite3"
    upgrade_to(database, "0036_translations")
    upgrade_to(database, "head")
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        set_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(related_resource_sets)")
        }
        paper_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(related_paper_candidates)")
        }
        resource_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(knowledge_resources)")
        }
        run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(related_run_sources)")
        }
        set_foreign_keys = tuple(
            connection.execute("PRAGMA foreign_key_list(related_resource_sets)")
        )
        paper_foreign_keys = tuple(
            connection.execute("PRAGMA foreign_key_list(related_paper_candidates)")
        )
        unique_indexes: dict[str, set[tuple[str, ...]]] = {}
        for table in (
            "related_resource_sets",
            "related_paper_candidates",
            "knowledge_resources",
            "related_run_sources",
        ):
            unique_indexes[table] = {
                tuple(row[2] for row in connection.execute(f'PRAGMA index_info("{index[1]}")'))
                for index in connection.execute(f'PRAGMA index_list("{table}")')
                if index[2] == 1
            }
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        foreign_key_failure = connection.execute("PRAGMA foreign_key_check").fetchone()
    finally:
        connection.close()

    related_tables = {
        "related_resource_sets",
        "related_paper_candidates",
        "knowledge_resources",
        "related_run_sources",
    }
    assert revision == ("0038_remove_legacy_conversations",)
    assert related_tables.issubset(tables)
    assert {
        "id",
        "work_id",
        "document_version_id",
        "document_content_sha256",
        "context_snapshot_id",
        "intent_revision",
        "intent",
        "intent_history",
        "status",
        "include_academic_sources",
        "include_external_web",
        "external_web_consent",
        "coverage",
        "job_id",
        "confirmed_at",
        "completed_at",
    }.issubset(set_columns)
    assert {
        "id",
        "set_id",
        "connector_id",
        "external_id",
        "normalized_doi",
        "title",
        "authors",
        "publication_year",
        "reason",
        "match_evidence",
        "confidence",
        "verification_status",
        "linked_work_id",
        "merge_review_id",
        "field_provenance",
    }.issubset(paper_columns)
    assert {
        "id",
        "set_id",
        "title",
        "canonical_url",
        "final_url",
        "domain",
        "resource_type",
        "reason",
        "publisher",
        "verification_status",
        "http_status",
        "accessed_at",
        "field_provenance",
    }.issubset(resource_columns)
    assert {
        "id",
        "set_id",
        "connector_id",
        "status",
        "candidate_count",
        "displayed_count",
        "cursor",
        "error_code",
        "error_message",
        "progress",
        "checkpoint",
        "completed_at",
    }.issubset(run_columns)
    assert {(row[2], row[3], row[4], row[6]) for row in set_foreign_keys}.issuperset(
        {
            ("works", "work_id", "id", "RESTRICT"),
            ("document_versions", "document_version_id", "id", "RESTRICT"),
            ("assistant_context_snapshots", "context_snapshot_id", "id", "RESTRICT"),
            ("jobs", "job_id", "id", "RESTRICT"),
        }
    )
    assert any(
        row[2] == "related_resource_sets"
        and row[3] == "set_id"
        and row[4] == "id"
        and row[6] == "CASCADE"
        for row in paper_foreign_keys
    )
    assert ("job_id",) in unique_indexes["related_resource_sets"]
    assert {
        ("set_id", "connector_id", "external_id"),
        ("set_id", "normalized_doi"),
    }.issubset(unique_indexes["related_paper_candidates"])
    assert ("set_id", "canonical_url") in unique_indexes["knowledge_resources"]
    assert ("set_id", "connector_id") in unique_indexes["related_run_sources"]
    assert quick_check == ("ok",)
    assert foreign_key_failure is None

    downgrade_to(database, "0036_translations")
    connection = sqlite3.connect(database)
    try:
        downgraded_revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        remaining = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()
    assert downgraded_revision == ("0036_translations",)
    assert related_tables.isdisjoint(remaining)
    assert "translation_artifacts" in remaining
