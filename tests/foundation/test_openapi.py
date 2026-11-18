from __future__ import annotations

import json
from pathlib import Path

from paper_reader import __version__


def test_openapi_export_is_the_versioned_frontend_contract(tmp_path: Path) -> None:
    from paper_reader.openapi import write_openapi_schema

    output = tmp_path / "openapi.json"
    write_openapi_schema(output)
    document = json.loads(output.read_text(encoding="utf-8"))

    assert document["info"]["title"] == "paper-reader"
    assert document["info"]["version"] == __version__
    assert "/api/v1/health" in document["paths"]
    assert "/api/v1/session/activate" in document["paths"]
    assert "/api/v1/jobs" in document["paths"]
    assert "/api/v1/jobs/{job_id}/cancel" in document["paths"]
    assert "/api/v1/backups" in document["paths"]
    assert "/api/v1/backups/policy" in document["paths"]
    assert "/api/v1/backups/{backup_id}/download" in document["paths"]
    assert "/api/v1/keywords/versions" in document["paths"]
    assert "/api/v1/keywords/suggestions" in document["paths"]
    assert "/api/v1/keywords/preference-context" in document["paths"]
    assert "/api/v1/keywords/versions/{version_id}/confirm" in document["paths"]
    assert "/api/v1/keywords/versions/{version_id}/compiled" in document["paths"]
    assert "/api/v1/plans" in document["paths"]
    assert "/api/v1/plans/{plan_id}/runs" in document["paths"]
    assert "/api/v1/runs/{run_id}/recommendations" in document["paths"]
    assert "/api/v1/runs/{run_id}/recommendations/refresh" in document["paths"]
    assert "/api/v1/scores/snapshots/{snapshot_id}" in document["paths"]
    assert "/api/v1/briefing" in document["paths"]
    assert "/api/v1/settings/preferences" in document["paths"]
    assert "/api/v1/plans/catchups" in document["paths"]
    assert "/api/v1/plans/catchups/{catchup_id}/confirm" in document["paths"]
    assert "/api/v1/runs/{run_id}/updates" in document["paths"]
    assert "/api/v1/updates" in document["paths"]
    assert "/api/v1/updates/{update_id}/state" in document["paths"]
    assert "/api/v1/library/imports/reference" in document["paths"]
    assert "/api/v1/library/imports/pdf" in document["paths"]
    assert "/api/v1/library/works" in document["paths"]
    assert "/api/v1/works/{work_id}" in document["paths"]
    assert "/api/v1/works/{work_id}/documents" in document["paths"]
    assert "/api/v1/assistant/works/{work_id}" in document["paths"]
    assert "/api/v1/assistant/works/{work_id}/tasks" in document["paths"]
    assert "/api/v1/assistant/tasks/{task_id}" in document["paths"]
    assert "/api/v1/assistant/tasks/{task_id}/fragments" in document["paths"]
    assert "/api/v1/assistant/tasks/{task_id}/retry" in document["paths"]
    assert "/api/v1/translations" in document["paths"]
    assert "/api/v1/translations/{artifact_id}" in document["paths"]
    assert "/api/v1/translations/{artifact_id}/segments" in document["paths"]
    assert "/api/v1/translations/{artifact_id}/terms/{term_id}" in document["paths"]
    assert "/api/v1/translations/{artifact_id}/resume" in document["paths"]
    assert "/api/v1/translations/{artifact_id}/export.md" in document["paths"]
    assert "/api/v1/translations/{artifact_id}/export.html" in document["paths"]
    assert "/api/v1/translations/{artifact_id}/print" in document["paths"]
    activation = document["paths"]["/api/v1/session/activate"]["post"]
    request_schema = activation["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["additionalProperties"] is False
    assert request_schema["properties"]["activation_secret"]["minLength"] == 43
    assert request_schema["properties"]["activation_secret"]["maxLength"] == 128
    state_request = document["components"]["schemas"]["PaperUpdateStateRequest"]
    assert state_request["additionalProperties"] is False
    refresh_request = document["components"]["schemas"]["RecommendationRefreshRequest"]
    assert refresh_request["additionalProperties"] is False
    assert "model" not in refresh_request.get("properties", {})
    suggestion_request = document["components"]["schemas"]["KeywordSuggestionCreate"]
    assert suggestion_request["additionalProperties"] is False
    assert "use_preference_context" in suggestion_request["properties"]
    assert "preference_context_sha256" in suggestion_request["properties"]
    assert "preference_terms" not in suggestion_request["properties"]
    assert "corpus_terms" not in suggestion_request["properties"]
    preferences_request = document["components"]["schemas"]["UserPreferencesUpdate"]
    assert preferences_request["additionalProperties"] is False
    assert set(preferences_request["properties"]) == {
        "timezone_mode",
        "timezone_name",
        "theme",
        "default_agent_backend",
        "default_agent_model_id",
        "default_reasoning_effort",
        "report_language",
        "translation_source_language",
        "translation_target_language",
    }
