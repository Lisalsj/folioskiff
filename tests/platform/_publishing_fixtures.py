from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from paper_reader.database import create_session_factory, create_sqlite_engine
from paper_reader.migrations import upgrade_database

CANARIES = {
    "pdf": "CANARY-PDF-FULLTEXT-53081",
    "annotation": "CANARY-PRIVATE-ANNOTATION-53082",
    "chat": "CANARY-PRIVATE-CHAT-53083",
    "preference": "CANARY-PRIVATE-PREFERENCE-53084",
    "credential": "CANARY-PRIVATE-CREDENTIAL-53085",
    "path": "CANARY-PRIVATE-PATH-53086",
    "prompt": "CANARY-COMPLETE-PROMPT-53087",
    "failure": "CANARY-FAILED-DRAFT-53088",
    "unselected_report": "CANARY-UNSELECTED-REPORT-53089",
    "score_evidence": "CANARY-PRIVATE-SCORE-EVIDENCE-53090",
    "source_payload": "CANARY-PRIVATE-SOURCE-PAYLOAD-53091",
    "merged_alias": "CANARY-STALE-MERGED-FACT-53092",
}

NOW = "2026-07-14T08:00:00+00:00"


@dataclass(frozen=True, slots=True)
class PublishingFixture:
    database: Path
    sessions: sessionmaker[Session]
    selected_report_id: str
    unselected_report_id: str


def _report_payload(text: str, *, evidence_id: str = "abstract:work") -> dict[str, object]:
    if evidence_id == "abstract:work":
        reference: dict[str, object] = {
            "evidence_id": evidence_id,
            "kind": "abstract",
            "page_number": None,
            "locator": None,
            "url": None,
            "external_title": None,
        }
    else:
        reference = {
            "evidence_id": evidence_id,
            "kind": "annotation",
            "page_number": 1,
            "locator": None,
            "url": None,
            "external_title": None,
        }
    statement = {"text": text, "references": [reference]}
    return {
        "title": "公开分析报告",
        "overview": statement,
        "research_question": statement,
        "author_claims": [statement],
        "contributions": [statement],
        "method": [statement],
        "assumptions": [statement],
        "data_and_experiments": [statement],
        "findings": [statement],
        "limitations": [statement],
        "reproducibility": [statement],
        "relation_to_user_intent": [statement],
        "agent_judgments": [statement],
    }


def _insert_report(
    connection: sqlite3.Connection,
    *,
    report_id: str,
    work_id: str,
    version: int,
    job_id: str,
    text: str,
    created_at: str,
    prompt: str = "safe prompt",
    evidence_id: str = "abstract:work",
) -> None:
    payload = _report_payload(text, evidence_id=evidence_id)
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    report_hash = sha256(payload_json.encode("utf-8")).hexdigest()
    connection.execute(
        "INSERT INTO analysis_reports (id, work_id, version, job_id, document_version_id, "
        "document_content_sha256, evidence_level, evidence_sha256, evidence_manifest, "
        "backend_id, requested_model_id, resolved_model_id, reasoning_effort, service_tier, "
        "language, template_version_id, complete_prompt, prompt_sha256, network_policy, "
        "structured_report, rendered_markdown, external_citations, schema_version, "
        "request_fingerprint, report_sha256, created_at) VALUES "
        "(?, ?, ?, ?, NULL, NULL, 'abstract_only', ?, ?, 'codex_cli', NULL, NULL, "
        "'high', 'flex', 'zh-CN', 'builtin-general-v1', ?, ?, "
        "'local_evidence_only', ?, ?, '[]', 'analysis-report-v1', ?, ?, ?)",
        (
            report_id,
            work_id,
            version,
            job_id,
            "e" * 64,
            json.dumps({"private": CANARIES["annotation"]}),
            prompt,
            sha256(prompt.encode("utf-8")).hexdigest(),
            payload_json,
            f"# {text}\n",
            sha256(f"request:{job_id}".encode()).hexdigest(),
            report_hash,
            created_at,
        ),
    )


def insert_quality_score(
    database: Path,
    *,
    score_id: str,
    work_id: str,
    score: float,
    created_at: str,
    rule_version: str,
) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO score_snapshots (id, work_id, run_id, context_key, score_type, "
            "rule_version, score, coverage, confidence, components, weights, missing_policy, "
            "evidence_observation_ids, hard_constraints, snapshot_sha256, created_at) VALUES "
            "(?, ?, NULL, 'publication-quality', 'quality', ?, ?, 72, 81, ?, '{}', '{}', "
            "'[]', '{}', ?, ?)",
            (
                score_id,
                work_id,
                rule_version,
                score,
                json.dumps([{"private": CANARIES["score_evidence"]}]),
                sha256(f"{score_id}:{score}".encode()).hexdigest(),
                created_at,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def insert_report_revision(
    database: Path,
    *,
    report_id: str = "report-latest-after-preview",
    created_at: str = "2026-07-14T12:00:00+00:00",
) -> None:
    connection = sqlite3.connect(database)
    try:
        _insert_report(
            connection,
            report_id=report_id,
            work_id="work-a",
            version=3,
            job_id=f"job-{report_id}",
            text="A newer formal report exists.",
            created_at=created_at,
        )
        connection.commit()
    finally:
        connection.close()


def build_publishing_fixture(tmp_path: Path) -> PublishingFixture:
    database = tmp_path / "publishing.sqlite3"
    upgrade_database(database)
    connection = sqlite3.connect(database)
    try:
        works = (
            (
                "work-a",
                "Canonical <img src=x onerror=alert(1)> Paper",
                "canonical img src x onerror alert 1 paper",
                json.dumps(["Alice & Bob", "陈研究员"]),
                "Canonical public abstract.",
                "10.5555/canonical",
                "2026-06-01T00:00:00+00:00",
                "active",
                None,
                "2026-07-14T07:00:00+00:00",
            ),
            (
                "work-alias",
                CANARIES["merged_alias"],
                "stale merged fact",
                json.dumps(["Private Alias Author"]),
                CANARIES["merged_alias"],
                None,
                None,
                "merged",
                "work-a",
                "2026-07-13T07:00:00+00:00",
            ),
            (
                "work-b",
                "A Second Public Paper",
                "a second public paper",
                json.dumps(["Beatrice"]),
                None,
                None,
                "2026-05-02T00:00:00+00:00",
                "active",
                None,
                "2026-07-14T06:00:00+00:00",
            ),
        )
        connection.executemany(
            "INSERT INTO works (id, canonical_title, normalized_title, authors, abstract, doi, "
            "published_at, status, merged_into_work_id, created_at, updated_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-07-01T00:00:00+00:00', ?)",
            works,
        )
        connection.execute(
            "INSERT INTO source_records (id, work_id, connector_id, external_id, title, "
            "normalized_title, authors, abstract, doi, published_at, source_updated_at, "
            "landing_url, field_provenance, raw_payload, fetched_at) VALUES "
            "('source-a', 'work-a', 'arxiv', '2607.00001', 'Source title', 'source title', "
            "'[]', NULL, NULL, NULL, NULL, NULL, '{}', ?, ?)",
            (json.dumps({"private": CANARIES["source_payload"]}), NOW),
        )
        connection.execute(
            "INSERT INTO document_versions (id, work_id, source_record_id, content_sha256, "
            "access_status, media_type, completeness, source_updated_at, extraction_status, "
            "relative_path, byte_size, original_filename, acquisition_kind, acquisition_url, "
            "last_accessed_at, created_at) VALUES "
            "('document-a', 'work-a', 'source-a', ?, 'user_provided', 'application/pdf', 100, "
            "NULL, 'complete', ?, 42, 'private.pdf', 'manual_import', NULL, NULL, ?)",
            ("d" * 64, CANARIES["path"], NOW),
        )
        connection.execute(
            "INSERT INTO document_page_texts (document_version_id, page_number, page_width, "
            "page_height, native_text, native_blocks, native_character_count, likely_scanned, "
            "ocr_state, ocr_text, ocr_blocks, ocr_confidence, extractor_version, created_at, "
            "updated_at, ocr_engine_version, ocr_model_bundle_id) VALUES "
            "('document-a', 1, 612, 792, ?, '[]', 28, 0, 'not_needed', NULL, '[]', NULL, "
            "'fixture', ?, ?, NULL, NULL)",
            (CANARIES["pdf"], NOW, NOW),
        )
        connection.execute(
            "INSERT INTO annotations (id, document_version_id, document_content_sha256, kind, "
            "page_number, page_width, page_height, quote_text, context_before, context_after, "
            "rects, color, note, created_at, updated_at) VALUES "
            "('annotation-a', 'document-a', ?, 'note', 1, 612, 792, 'private quote', NULL, "
            "NULL, '[]', 'amber', ?, ?, ?)",
            ("d" * 64, CANARIES["annotation"], NOW, NOW),
        )
        connection.execute(
            "INSERT INTO assistant_sessions (id, work_id, created_at, updated_at) "
            "VALUES ('assistant-session-a', 'work-a', ?, ?)",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO assistant_context_snapshots (id, session_id, work_id, "
            "document_version_id, document_content_sha256, scope, page_numbers, selection, "
            "annotation_ids, section_fallback, extraction_version, evidence, evidence_sha256, "
            "warnings, snapshot_sha256, created_at) VALUES "
            "('assistant-snapshot-a', 'assistant-session-a', 'work-a', 'document-a', ?, "
            "'full_document', '[1]', NULL, '[]', 0, 'fixture', '{}', ?, '[]', ?, ?)",
            ("d" * 64, "e" * 64, "s" * 64, NOW),
        )
        connection.execute(
            "INSERT INTO assistant_tasks (id, session_id, context_snapshot_id, retry_of_task_id, "
            "mode, status, network_policy, backend_id, requested_model_id, reasoning_effort, "
            "service_tier, output_language, idempotency_key, command_sha256, job_id, error_code, "
            "diagnostic, created_at, started_at, completed_at, updated_at) VALUES "
            "('assistant-task-a', 'assistant-session-a', 'assistant-snapshot-a', NULL, 'ask', "
            "'succeeded', 'local_only', 'codex_cli', NULL, 'medium', 'flex', 'zh-CN', "
            "'publishing-private-chat', ?, 'assistant-private-job', NULL, NULL, ?, ?, ?, ?)",
            ("c" * 64, NOW, NOW, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO assistant_messages (id, session_id, task_id, sequence, role, status, "
            "content, citations, evidence_manifest, job_id, reply_to_message_id, created_at) "
            "VALUES ('assistant-message-a', 'assistant-session-a', 'assistant-task-a', 1, "
            "'user', 'committed', ?, '[]', '{}', 'assistant-private-job', NULL, ?)",
            (CANARIES["chat"], NOW),
        )
        connection.execute(
            "INSERT INTO implicit_preference_events (id, work_id, event_key, event_type, "
            "duration_bucket, weight, occurred_at, created_at) VALUES "
            "('preference-a', 'work-a', ?, 'detail_open', NULL, 1, ?, ?)",
            (CANARIES["preference"], NOW, NOW),
        )
        connection.execute(
            "INSERT INTO plugin_installations (plugin_id, entry_point_value, distribution_name, "
            "distribution_version, manifest_json, manifest_sha256, protocol_minimum, "
            "protocol_maximum, compatible, compatibility_reason, enabled, consecutive_failures, "
            "circuit_open_reason, last_discovered_at, last_called_at, created_at, updated_at) "
            "VALUES ('fixture.private', 'fixture:app', 'fixture', '1.0.0', ?, ?, '1.0', '1.0', "
            "0, 'fixture', 0, 0, NULL, ?, NULL, ?, ?)",
            (json.dumps({"credential": CANARIES["credential"]}), "c" * 64, NOW, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO analysis_failure_drafts (job_id, work_id, error_category, "
            "error_message, raw_response, validation_errors, repair_count, request_fingerprint, "
            "created_at) VALUES ('job-failed', 'work-a', 'validation', 'safe message', ?, '[]', "
            "1, ?, ?)",
            (CANARIES["failure"], "f" * 64, NOW),
        )
        _insert_report(
            connection,
            report_id="report-selected",
            work_id="work-alias",
            version=1,
            job_id="job-selected",
            text="Selected public analysis.",
            created_at="2026-07-14T08:10:00+00:00",
            prompt=CANARIES["prompt"],
            evidence_id=CANARIES["annotation"],
        )
        _insert_report(
            connection,
            report_id="report-unselected",
            work_id="work-a",
            version=2,
            job_id="job-unselected",
            text=CANARIES["unselected_report"],
            created_at="2026-07-14T09:00:00+00:00",
        )
        connection.commit()
    finally:
        connection.close()

    insert_quality_score(
        database,
        score_id="score-a-v1",
        work_id="work-a",
        score=88,
        created_at="2026-07-14T08:30:00+00:00",
        rule_version="quality-v7",
    )
    sessions = create_session_factory(create_sqlite_engine(database))
    return PublishingFixture(
        database=database,
        sessions=sessions,
        selected_report_id="report-selected",
        unselected_report_id="report-unselected",
    )
