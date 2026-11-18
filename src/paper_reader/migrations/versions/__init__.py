"""Frozen database migration contract.

Changing any value in this module is a release-contract change. New migrations
must be appended to the single chain and accompanied by an updated schema
inventory hash and compatibility evidence.
"""

FROZEN_DATABASE_HEAD = "0038_remove_legacy_conversations"
OLDEST_SUPPORTED_DATABASE_REVISION = "0001_foundation"
FROZEN_DATABASE_SCHEMA_SHA256 = "4d2261f6d12a971a0acf495585f6302d349fa71cdcd028fb1929ac6c7a4579a4"
FROZEN_DATABASE_REVISION_CHAIN = (
    "0001_foundation",
    "0002_discovery_identity",
    "0003_keyword_versions",
    "0004_discovery_plans",
    "0005_discovery_runs",
    "0006_merge_review_decisions",
    "0007_recommendation_fts",
    "0008_run_candidates",
    "0009_metadata_observations",
    "0010_score_snapshots",
    "0011_rating_catalogs",
    "0012_preferences",
    "0013_score_rules",
    "0014_reader_library",
    "0015_reader_text",
    "0016_reader_ocr",
    "0017_reader_annotations",
    "0018_annotation_migrations",
    "0019_agent_templates",
    "0020_analysis_reports",
    "0021_paper_conversations",
    "0022_comparison_reports",
    "0023_backup_archives",
    "0024_plugin_registry",
    "0025_published_snapshots",
    "0026_model_assets",
    "0027_update_settings",
    "0028_source_revisions_and_updates",
    "0028b_discovery_quota_counts",
    "0028c_paper_update_runs",
    "0029_recommendation_sets",
    "0030_user_preferences",
    "0031_library_query_indexes",
    "0032_identity_candidate_indexes",
    "0033_onboarding_state",
    "0034_agent_language_preferences",
    "0035_assistant_core",
    "0036_translations",
    "0037_related_resources",
    "0038_remove_legacy_conversations",
)

__all__ = [
    "FROZEN_DATABASE_HEAD",
    "FROZEN_DATABASE_REVISION_CHAIN",
    "FROZEN_DATABASE_SCHEMA_SHA256",
    "OLDEST_SUPPORTED_DATABASE_REVISION",
]
