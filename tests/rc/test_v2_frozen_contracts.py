from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).parents[2]

EXPECTED_V2_CAPABILITIES = {
    "assistant": "ask_explain_translate_related_explicit_context",
    "external_research": "related_resources_only_explicit_consent",
    "onboarding": "four_step_skippable_except_storage",
    "translation": "versioned_bilingual_english_to_simplified_chinese_default",
}
REQUIRED_V2_PATHS = {
    "/api/v1/onboarding",
    "/api/v1/onboarding/probe",
    "/api/v1/assistant/works/{work_id}",
    "/api/v1/translations",
    "/api/v1/related-resources/works/{work_id}/draft",
    "/api/v1/conversations/works/{work_id}",
}
REQUIRED_V2_TABLES = {
    "onboarding_state",
    "assistant_sessions",
    "assistant_context_snapshots",
    "assistant_tasks",
    "assistant_agent_runs",
    "assistant_messages",
    "assistant_fragments",
    "assistant_checkpoints",
    "translation_artifacts",
    "translation_segments",
    "translation_terms",
    "translation_exports",
    "related_resource_sets",
    "related_paper_candidates",
    "knowledge_resources",
    "related_run_sources",
}
REMOVED_LEGACY_TABLES = {
    "paper_conversations",
    "conversation_messages",
    "conversation_fragments",
    "conversation_checkpoints",
}


def test_v2_database_and_api_contracts_are_closed() -> None:
    from paper_reader.migrations.versions import (
        FROZEN_DATABASE_HEAD,
        FROZEN_DATABASE_REVISION_CHAIN,
    )
    from paper_reader.openapi import (
        API_CONTRACT_VERSION,
        OPENAPI_CONTRACT_SHA256,
        load_openapi_document,
        openapi_contract_sha256,
    )

    document = load_openapi_document(ROOT / "openapi" / "openapi.json")

    assert FROZEN_DATABASE_HEAD == "0038_remove_legacy_conversations"
    assert FROZEN_DATABASE_REVISION_CHAIN[-5:] == (
        "0034_agent_language_preferences",
        "0035_assistant_core",
        "0036_translations",
        "0037_related_resources",
        "0038_remove_legacy_conversations",
    )
    assert API_CONTRACT_VERSION == "v1"
    assert openapi_contract_sha256(document) == OPENAPI_CONTRACT_SHA256
    assert REQUIRED_V2_PATHS.issubset(document["paths"])


def test_legacy_conversation_route_is_a_storage_free_compatibility_adapter(
    tmp_path: Path,
) -> None:
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "v2-contract.sqlite3"
    upgrade_database(database)
    connection = sqlite3.connect(database)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()

    assert REQUIRED_V2_TABLES.issubset(tables)
    assert tables.isdisjoint(REMOVED_LEGACY_TABLES)


def test_v2_release_capability_matrix_is_explicit() -> None:
    from scripts.build_release_metadata import CAPABILITY_MATRIX

    assert EXPECTED_V2_CAPABILITIES.items() <= CAPABILITY_MATRIX.items()
