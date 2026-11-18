from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.settings import AppPaths

NOW = datetime(2026, 7, 14, 16, 0, tzinfo=UTC)


def _paths(tmp_path: Path, name: str) -> AppPaths:
    paths = AppPaths.from_overrides(
        state_root=tmp_path / name / "state",
        cache_root=tmp_path / name / "cache",
        library_root=tmp_path / name / "library",
    )
    paths.ensure()
    return paths


def _marker(database: Path, table: str, value: str) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(f"CREATE TABLE {table} (value TEXT NOT NULL)")
        connection.execute(f"INSERT INTO {table} VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _has_table(database: Path, table: str) -> bool:
    connection = sqlite3.connect(database)
    try:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            is not None
        )
    finally:
        connection.close()


def _package(tmp_path: Path) -> tuple[Path, AppPaths]:
    from paper_reader.backups.archive import BackupArchiveBuilder
    from paper_reader.migrations import upgrade_database

    paths = _paths(tmp_path, "source")
    upgrade_database(paths.database, snapshot_root=paths.migration_snapshots)
    _marker(paths.database, "portable_gold", "from-portable-backup")
    package = paths.backups / "portable.paper-reader-backup"
    BackupArchiveBuilder(paths=paths, clock=lambda: NOW).create(destination=package)
    return package, paths


GOLD_TABLES = (
    "jobs",
    "keyword_set_versions",
    "keyword_confirmations",
    "discovery_plans",
    "plan_runs",
    "run_batches",
    "run_candidates",
    "works",
    "source_records",
    "source_record_revisions",
    "paper_updates",
    "score_snapshots",
    "recommendation_sets",
    "recommendation_items",
    "library_states",
    "library_entries",
    "implicit_preference_events",
    "preference_aggregates",
    "interested_authors",
    "user_preferences",
    "document_versions",
    "reading_positions",
    "annotations",
    "annotation_migration_batches",
    "annotation_migration_candidates",
    "annotation_migration_decisions",
    "prompt_template_versions",
    "analysis_reports",
    "assistant_sessions",
    "assistant_context_snapshots",
    "assistant_tasks",
    "assistant_agent_runs",
    "assistant_messages",
    "assistant_fragments",
    "assistant_checkpoints",
    "comparison_reports",
    "comparison_report_items",
)


def _hash(label: str) -> str:
    return sha256(label.encode()).hexdigest()


def _seed_golden_domain(paths: AppPaths) -> tuple[str, bytes]:
    pdf = b"%PDF-1.7\ngolden paper-reader evidence\n%%EOF\n"
    pdf_hash = sha256(pdf).hexdigest()
    relative_pdf = f"objects/{pdf_hash[:2]}/{pdf_hash[2:4]}/{pdf_hash}.pdf"
    pdf_path = paths.library_root.joinpath(*relative_pdf.split("/"))
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(pdf)
    timestamp = "2026-07-14 16:00:00+00:00"
    ids = {
        name: f"00000000-0000-4000-8000-{index:012d}"
        for index, name in enumerate(
            (
                "keywords",
                "plan",
                "run_job",
                "run",
                "batch",
                "work_one",
                "work_two",
                "source_one",
                "source_two",
                "source_revision_previous",
                "source_revision_current",
                "paper_update",
                "run_candidate",
                "score",
                "score_relevance",
                "score_freshness",
                "score_preference",
                "score_composite",
                "recommendation_set",
                "recommendation_item",
                "event",
                "document_one",
                "document_two",
                "annotation",
                "migration_batch",
                "migration_candidate",
                "analysis_job",
                "analysis",
                "assistant_job",
                "assistant_session",
                "assistant_snapshot",
                "assistant_task",
                "assistant_run",
                "message_one",
                "message_two",
                "checkpoint",
                "comparison_job",
                "comparison",
                "comparison_item_one",
                "comparison_item_two",
            ),
            start=1,
        )
    }

    connection = sqlite3.connect(paths.database)
    connection.execute("PRAGMA foreign_keys = ON")

    def insert(table: str, **values: object) -> None:
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        connection.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
            tuple(values.values()),
        )

    try:
        insert(
            "keyword_set_versions",
            id=ids["keywords"],
            parent_version_id=None,
            research_intent="可解释的多模态癌症智能体",
            required_groups=json.dumps(
                [{"id": "agent", "terms": ["agent", "智能体"]}],
                ensure_ascii=False,
            ),
            boost_groups=json.dumps(
                [{"id": "cancer", "terms": ["cancer", "癌症"]}],
                ensure_ascii=False,
            ),
            excluded_groups="[]",
            change_summary=json.dumps({"created": True}),
            generator_info=json.dumps({"backend": "gold-fixture"}),
            created_at=timestamp,
        )
        insert(
            "keyword_confirmations",
            keyword_version_id=ids["keywords"],
            confirmed_at=timestamp,
        )
        insert(
            "discovery_plans",
            id=ids["plan"],
            name="金样月度计划",
            research_intent="可解释的多模态癌症智能体",
            keyword_version_id=ids["keywords"],
            date_mode="fixed",
            start_month="2026-05",
            end_month="2026-07",
            rolling_months=None,
            timezone="Asia/Shanghai",
            sources=json.dumps(["arxiv", "openreview"]),
            source_filters=json.dumps({"arxiv": {"categories": ["cs.AI"]}}),
            schedule_frequency="monthly",
            schedule_local_time="08:30",
            weekly_day=0,
            monthly_day=14,
            priority_limit=20,
            candidate_limit=50,
            enabled=1,
            schedule_checked_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
        for job_name, kind in (
            ("run_job", "discovery.run"),
            ("analysis_job", "analysis.generate_report"),
            ("assistant_job", "assistant.reply"),
            ("comparison_job", "comparison.generate"),
        ):
            insert(
                "jobs",
                id=ids[job_name],
                kind=kind,
                status="succeeded",
                progress=100,
                payload=json.dumps({"gold": job_name}),
                result=json.dumps({"complete": True}),
                checkpoint=json.dumps({"phase": "complete"}),
                error_code=None,
                error_message=None,
                idempotency_key=f"gold:{job_name}",
                cancel_requested=0,
                created_at=timestamp,
                updated_at=timestamp,
            )
        insert(
            "plan_runs",
            id=ids["run"],
            plan_id=ids["plan"],
            job_id=ids["run_job"],
            keyword_version_id=ids["keywords"],
            status="succeeded",
            months=json.dumps(["2026-07"]),
            plan_snapshot=json.dumps({"name": "金样月度计划"}, ensure_ascii=False),
            keyword_snapshot=json.dumps({"required": ["agent"]}),
            connector_versions=json.dumps({"arxiv": "v1"}),
            total_batches=1,
            succeeded_batches=1,
            failed_batches=0,
            cancelled_batches=0,
            created_at=timestamp,
            started_at=timestamp,
            completed_at=timestamp,
        )
        insert(
            "run_batches",
            id=ids["batch"],
            run_id=ids["run"],
            month="2026-07",
            connector_id="arxiv",
            status="succeeded",
            query_fingerprint=_hash("query"),
            checkpoint=json.dumps({"cursor": None}),
            fetched_count=2,
            accepted_count=2,
            ingested_count=2,
            attempt_count=1,
            error_code=None,
            error_message=None,
            retryable=0,
            retry_after_seconds=None,
            created_at=timestamp,
            updated_at=timestamp,
            completed_at=timestamp,
        )
        for work_name, title, doi in (
            ("work_one", "Golden Agent Paper", "10.5555/golden.agent"),
            ("work_two", "Golden Cancer Paper", "10.5555/golden.cancer"),
        ):
            insert(
                "works",
                id=ids[work_name],
                canonical_title=title,
                normalized_title=title.casefold(),
                authors=json.dumps(["Ada Researcher", "Lin Scholar"]),
                abstract=f"Frozen abstract for {title}.",
                doi=doi,
                published_at=timestamp,
                created_at=timestamp,
                updated_at=timestamp,
                status="active",
                merged_into_work_id=None,
            )
        for source_name, work_name, connector, external_id in (
            ("source_one", "work_one", "arxiv", "2607.00001"),
            ("source_two", "work_two", "openreview", "gold-forum-id"),
        ):
            insert(
                "source_records",
                id=ids[source_name],
                work_id=ids[work_name],
                connector_id=connector,
                external_id=external_id,
                title="Golden source record",
                normalized_title="golden source record",
                authors=json.dumps(["Ada Researcher"]),
                abstract="Source-level evidence.",
                doi=None,
                published_at=timestamp,
                source_updated_at=timestamp,
                landing_url="https://example.invalid/golden",
                field_provenance=json.dumps({"title": connector}),
                raw_payload=json.dumps({"fixture": True}),
                fetched_at=timestamp,
            )
        previous_snapshot = {
            "connector_id": "arxiv",
            "external_id": "2607.00001",
            "title": "Golden Agent Paper",
            "abstract": "Initial source-level evidence.",
        }
        current_snapshot = {
            **previous_snapshot,
            "abstract": "Important revised source-level evidence.",
        }
        for revision_name, kind, snapshot, changed_fields in (
            (
                "source_revision_previous",
                "baseline",
                previous_snapshot,
                [],
            ),
            (
                "source_revision_current",
                "important_revision",
                current_snapshot,
                ["abstract"],
            ),
        ):
            insert(
                "source_record_revisions",
                id=ids[revision_name],
                source_record_id=ids["source_one"],
                work_id=ids["work_one"],
                revision_kind=kind,
                payload_sha256=_hash(f"gold-{revision_name}"),
                snapshot=json.dumps(snapshot),
                changed_fields=json.dumps(changed_fields),
                field_provenance=json.dumps({"abstract": "arxiv"}),
                source_updated_at=timestamp,
                fetched_at=timestamp,
                created_at=timestamp,
            )
        insert(
            "paper_updates",
            id=ids["paper_update"],
            work_id=ids["work_one"],
            source_record_id=ids["source_one"],
            previous_revision_id=ids["source_revision_previous"],
            current_revision_id=ids["source_revision_current"],
            update_type="important_revision",
            visibility="updates",
            state="read",
            evidence=json.dumps({"changed_fields": ["abstract"]}),
            detected_at=timestamp,
            acted_at=timestamp,
            run_id=ids["run"],
        )
        insert(
            "run_candidates",
            id=ids["run_candidate"],
            run_id=ids["run"],
            batch_id=ids["batch"],
            work_id=ids["work_one"],
            source_record_id=ids["source_one"],
            match_kind="existing_source",
            created_at=timestamp,
            discovery_kind="important_revision",
        )
        score_ids = {
            "relevance": ids["score_relevance"],
            "quality": ids["score"],
            "freshness": ids["score_freshness"],
            "preference": ids["score_preference"],
            "composite": ids["score_composite"],
        }
        for score_type, score_id in score_ids.items():
            insert(
                "score_snapshots",
                id=score_id,
                work_id=ids["work_one"],
                run_id=ids["run"],
                context_key=f"gold-{score_type}",
                score_type=score_type,
                rule_version="gold-v1",
                score=88.5,
                coverage=76.0,
                confidence=82.0,
                components=json.dumps({score_type: 88.5}),
                weights=json.dumps({score_type: 1.0}),
                missing_policy=json.dumps({"unknown": "neutral"}),
                evidence_observation_ids="[]",
                hard_constraints=json.dumps({"retracted": False}),
                snapshot_sha256=_hash(f"gold-{score_type}-snapshot"),
                created_at=timestamp,
            )
        insert(
            "recommendation_sets",
            id=ids["recommendation_set"],
            run_id=ids["run"],
            keyword_version_id=ids["keywords"],
            version=1,
            ranking_rule_version="gold-composite-v1",
            fixed_historical_months=0,
            requested_limit=20,
            candidate_cap=50,
            candidate_count=1,
            recommendation_count=1,
            exploration_target=0,
            exploration_count=0,
            degradation_reasons="[]",
            set_sha256=_hash("gold-recommendation-set"),
            created_at=timestamp,
        )
        insert(
            "recommendation_items",
            id=ids["recommendation_item"],
            recommendation_set_id=ids["recommendation_set"],
            scored_work_id=ids["work_one"],
            default_position=1,
            selected=1,
            historical_match=0,
            eligible_by_default=1,
            channel="exploitation",
            ineligibility_reasons="[]",
            exploration_reasons="[]",
            source_updated_at=timestamp,
            relevance_snapshot_id=score_ids["relevance"],
            quality_snapshot_id=score_ids["quality"],
            freshness_snapshot_id=score_ids["freshness"],
            preference_snapshot_id=score_ids["preference"],
            composite_snapshot_id=score_ids["composite"],
            ranking_evidence=json.dumps({"signals": list(score_ids)}),
            item_sha256=_hash("gold-recommendation-item"),
            created_at=timestamp,
        )
        insert(
            "library_states",
            work_id=ids["work_one"],
            favorite=1,
            feedback="like",
            feedback_reasons=json.dumps(["方法严谨"], ensure_ascii=False),
            pinned=1,
            reading_progress=62.5,
            analysis_state="available",
            created_at=timestamp,
            updated_at=timestamp,
        )
        insert(
            "library_entries",
            work_id=ids["work_one"],
            added_via="pdf",
            created_at=timestamp,
            updated_at=timestamp,
        )
        insert(
            "implicit_preference_events",
            id=ids["event"],
            work_id=ids["work_one"],
            event_key="gold-reading-event",
            event_type="reading_completed",
            duration_bucket=None,
            weight=2.5,
            occurred_at=timestamp,
            created_at=timestamp,
        )
        insert(
            "preference_aggregates",
            work_id=ids["work_one"],
            total_weight=2.5,
            aggregated_through=timestamp,
            updated_at=timestamp,
        )
        insert(
            "interested_authors",
            author_key="ada researcher",
            display_name="Ada Researcher",
            active=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
        connection.execute(
            "UPDATE user_preferences SET timezone_mode = 'custom', "
            "timezone_name = 'Asia/Shanghai', theme = 'dark', updated_at = ? "
            "WHERE id = 'default'",
            (timestamp,),
        )
        target_hash = _hash("target-document-version")
        insert(
            "document_versions",
            id=ids["document_one"],
            work_id=ids["work_one"],
            source_record_id=ids["source_one"],
            content_sha256=pdf_hash,
            access_status="user_provided",
            media_type="application/pdf",
            completeness=100,
            source_updated_at=timestamp,
            extraction_status="complete",
            relative_path=relative_pdf,
            created_at=timestamp,
            byte_size=len(pdf),
            original_filename="golden.pdf",
            acquisition_kind="manual_import",
            acquisition_url=None,
            last_accessed_at=timestamp,
        )
        insert(
            "document_versions",
            id=ids["document_two"],
            work_id=ids["work_one"],
            source_record_id=ids["source_one"],
            content_sha256=target_hash,
            access_status="restricted",
            media_type="application/pdf",
            completeness=80,
            source_updated_at=timestamp,
            extraction_status="not_started",
            relative_path=None,
            created_at=timestamp,
            byte_size=0,
            original_filename=None,
            acquisition_kind="source",
            acquisition_url=None,
            last_accessed_at=None,
        )
        insert(
            "reading_positions",
            document_version_id=ids["document_one"],
            document_content_sha256=pdf_hash,
            page_number=3,
            page_count=10,
            scale=1.25,
            rotation=0,
            progress=30.0,
            created_at=timestamp,
            updated_at=timestamp,
        )
        insert(
            "annotations",
            id=ids["annotation"],
            document_version_id=ids["document_one"],
            document_content_sha256=pdf_hash,
            kind="highlight",
            page_number=3,
            page_width=612.0,
            page_height=792.0,
            quote_text="Golden quoted evidence",
            context_before="Before",
            context_after="After",
            rects=json.dumps([{"x": 10, "y": 20, "width": 100, "height": 12}]),
            color="green",
            note="保留这条批注",
            created_at=timestamp,
            updated_at=timestamp,
        )
        insert(
            "annotation_migration_batches",
            id=ids["migration_batch"],
            preview_key=_hash("migration-preview"),
            algorithm_version="gold-v1",
            source_document_version_id=ids["document_one"],
            source_document_content_sha256=pdf_hash,
            target_document_version_id=ids["document_two"],
            target_document_content_sha256=target_hash,
            source_annotations_sha256=_hash("source-annotations"),
            target_extraction_sha256=_hash("target-extraction"),
            created_at=timestamp,
        )
        insert(
            "annotation_migration_candidates",
            id=ids["migration_candidate"],
            batch_id=ids["migration_batch"],
            source_annotation_id=ids["annotation"],
            source_annotation_sha256=_hash("source-annotation"),
            source_snapshot=json.dumps({"quote": "Golden quoted evidence"}),
            order_index=0,
            match_state="matched",
            confidence="high",
            score=0.94,
            target_page_number=4,
            target_page_width=612.0,
            target_page_height=792.0,
            target_quote_text="Golden quoted evidence",
            target_context_before="Before",
            target_context_after="After",
            target_rects=json.dumps([{"x": 12, "y": 22, "width": 100, "height": 12}]),
            text_source="native",
            alternatives="[]",
            candidate_sha256=_hash("migration-candidate"),
            created_at=timestamp,
        )
        insert(
            "annotation_migration_decisions",
            candidate_id=ids["migration_candidate"],
            decision="reject",
            target_annotation_id=None,
            decided_at=timestamp,
        )
        template_id = "custom:gold-analysis:v1"
        insert(
            "prompt_template_versions",
            id=template_id,
            family="custom",
            name="Gold analysis",
            version=1,
            language="zh-CN",
            body="请严格区分作者主张与 Agent 判断，并逐页引用论文证据。",
            output_schema_version="analysis-report-v1",
            built_in=0,
            parent_version_id=None,
            content_sha256=_hash("gold-template"),
            created_at=timestamp,
        )
        insert(
            "analysis_reports",
            id=ids["analysis"],
            work_id=ids["work_one"],
            version=1,
            job_id=ids["analysis_job"],
            document_version_id=ids["document_one"],
            document_content_sha256=pdf_hash,
            evidence_level="full_text",
            evidence_sha256=_hash("analysis-evidence"),
            evidence_manifest=json.dumps({"pages": [1, 3]}),
            backend_id="codex_cli",
            requested_model_id="gold-model",
            resolved_model_id="gold-model",
            reasoning_effort="high",
            service_tier="fast",
            language="zh-CN",
            template_version_id=template_id,
            complete_prompt="<SYSTEM>Gold prompt</SYSTEM>",
            prompt_sha256=_hash("analysis-prompt"),
            network_policy="local_evidence_only",
            structured_report=json.dumps({"title": "金样分析", "claims": []}, ensure_ascii=False),
            rendered_markdown="# 金样分析\n\n证据完整。",
            external_citations="[]",
            schema_version="analysis-report-v1",
            request_fingerprint=_hash("analysis-request"),
            report_sha256=_hash("analysis-report"),
            created_at=timestamp,
        )
        insert(
            "assistant_sessions",
            id=ids["assistant_session"],
            work_id=ids["work_one"],
            created_at=timestamp,
            updated_at=timestamp,
        )
        insert(
            "assistant_context_snapshots",
            id=ids["assistant_snapshot"],
            session_id=ids["assistant_session"],
            work_id=ids["work_one"],
            document_version_id=ids["document_one"],
            document_content_sha256=pdf_hash,
            scope="full_document",
            page_numbers=json.dumps([3]),
            selection=None,
            annotation_ids="[]",
            section_fallback=0,
            extraction_version="gold-extractor-v1",
            evidence=json.dumps({"pages": [3], "document_content_sha256": pdf_hash}),
            evidence_sha256=_hash("assistant-evidence"),
            warnings="[]",
            snapshot_sha256=_hash("assistant-snapshot"),
            created_at=timestamp,
        )
        insert(
            "assistant_tasks",
            id=ids["assistant_task"],
            session_id=ids["assistant_session"],
            context_snapshot_id=ids["assistant_snapshot"],
            retry_of_task_id=None,
            mode="ask",
            status="succeeded",
            network_policy="local_only",
            backend_id="codex_cli",
            requested_model_id="gold-model",
            reasoning_effort="medium",
            service_tier="fast",
            output_language="zh-CN",
            idempotency_key="gold:assistant-task",
            command_sha256=_hash("assistant-command"),
            job_id=ids["assistant_job"],
            error_code=None,
            diagnostic=None,
            created_at=timestamp,
            started_at=timestamp,
            completed_at=timestamp,
            updated_at=timestamp,
        )
        insert(
            "assistant_agent_runs",
            id=ids["assistant_run"],
            task_id=ids["assistant_task"],
            attempt=1,
            status="succeeded",
            backend_id="codex_cli",
            requested_model_id="gold-model",
            resolved_model_id="gold-model",
            reasoning_effort="medium",
            service_tier="fast",
            request_sha256=_hash("assistant-request"),
            prompt_sha256=_hash("assistant-prompt"),
            raw_output_sha256=_hash("assistant-output"),
            usage=json.dumps({"input_tokens": 20, "output_tokens": 10}),
            repair_count=0,
            error_code=None,
            diagnostic=None,
            created_at=timestamp,
            started_at=timestamp,
            completed_at=timestamp,
        )
        insert(
            "assistant_messages",
            id=ids["message_one"],
            session_id=ids["assistant_session"],
            task_id=ids["assistant_task"],
            sequence=1,
            role="user",
            content="这篇论文的关键限制是什么？",
            status="committed",
            citations="[]",
            evidence_manifest=json.dumps({"document_content_sha256": pdf_hash}),
            job_id=ids["assistant_job"],
            reply_to_message_id=None,
            created_at=timestamp,
        )
        insert(
            "assistant_messages",
            id=ids["message_two"],
            session_id=ids["assistant_session"],
            task_id=ids["assistant_task"],
            sequence=2,
            role="assistant",
            content="关键限制来自样本规模。",
            status="complete",
            citations=json.dumps(
                [{"evidence_id": "page:3:native", "kind": "page", "page_number": 3}]
            ),
            evidence_manifest=json.dumps({"document_content_sha256": pdf_hash, "pages": [3]}),
            job_id=ids["assistant_job"],
            reply_to_message_id=ids["message_one"],
            created_at=timestamp,
        )
        insert(
            "assistant_checkpoints",
            id=ids["checkpoint"],
            session_id=ids["assistant_session"],
            source_start_sequence=1,
            through_sequence=2,
            summary="用户询问限制，Agent 指出样本规模。",
            summary_sha256=_hash("assistant-checkpoint"),
            backend_id="codex_cli",
            model_id="gold-model",
            estimated_tokens=25,
            estimated_characters=20,
            created_at=timestamp,
        )
        insert(
            "comparison_reports",
            id=ids["comparison"],
            comparison_key=_hash("comparison-key"),
            version=1,
            job_id=ids["comparison_job"],
            title="Agent 与癌症论文比较",
            mode="direct",
            backend_id="codex_cli",
            requested_model_id="gold-model",
            resolved_model_id="gold-model",
            reasoning_effort="high",
            service_tier="fast",
            language="zh-CN",
            complete_prompt="<COMPARISON>Gold prompt</COMPARISON>",
            prompt_sha256=_hash("comparison-prompt"),
            structured_report=json.dumps({"title": "比较", "papers": 2}, ensure_ascii=False),
            rendered_markdown="# 比较\n\n两篇论文证据。",
            coverage=75,
            coverage_details=json.dumps({"available": 2}),
            provenance=json.dumps({"mode": "direct"}),
            schema_version="comparison-report-v1",
            request_fingerprint=_hash("comparison-request"),
            report_sha256=_hash("comparison-report"),
            created_at=timestamp,
        )
        for position, item_name, work_name, document in (
            (0, "comparison_item_one", "work_one", True),
            (1, "comparison_item_two", "work_two", False),
        ):
            insert(
                "comparison_report_items",
                id=ids[item_name],
                report_id=ids["comparison"],
                position=position,
                work_id=ids[work_name],
                document_version_id=ids["document_one"] if document else None,
                document_content_sha256=pdf_hash if document else None,
                evidence_level="full_text" if document else "abstract_only",
                evidence_sha256=_hash(f"comparison-evidence-{position}"),
                evidence_manifest=json.dumps({"position": position}),
                extraction_status="direct",
                extraction_id=None,
                coverage=100 if document else 50,
                created_at=timestamp,
            )
        connection.commit()
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
    finally:
        connection.close()
    return relative_pdf, pdf


def _domain_dump(database: Path) -> dict[str, tuple[tuple[object, ...], ...]]:
    connection = sqlite3.connect(database)
    try:
        return {
            table: tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1"))
            for table in GOLD_TABLES
        }
    finally:
        connection.close()


@asynccontextmanager
async def _client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


async def _bootstrap(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/session/bootstrap",
        json={"token": "secret"},
        headers={"Origin": "http://127.0.0.1"},
    )
    assert response.status_code == 204
    return {
        "Origin": "http://127.0.0.1",
        "X-CSRF-Token": client.cookies["paper_reader_csrf"],
    }


async def _wait(client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    for _ in range(300):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"succeeded", "failed", "cancelled"}:
            return body
        await asyncio.sleep(0.01)
    raise AssertionError("restore validation did not finish")


@pytest.mark.asyncio
async def test_restore_preview_expires_and_cleans_plaintext_without_touching_current_data(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.repository import BackupRepository
    from paper_reader.backups.service import RestorePreviewError, RestoreService
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.migrations import upgrade_database

    package, _source = _package(tmp_path)
    paths = _paths(tmp_path, "target-expiry")
    upgrade_database(paths.database, snapshot_root=paths.migration_snapshots)
    _marker(paths.database, "current_gold", "must-stay-current")
    current = NOW

    def clock() -> datetime:
        return current

    engine = create_sqlite_engine(paths.database)
    try:
        sessions = create_session_factory(engine)
        service = RestoreService(
            paths=paths,
            jobs=JobRepository(sessions),
            archives=BackupRepository(sessions),
            clock=clock,
            preview_ttl=timedelta(minutes=10),
        )
        with package.open("rb") as stream:
            upload = service.stage_upload(stream)
        job = service.create_validation_job(
            upload=upload,
            password=None,
            idempotency_key="expiring-preview",
        )
        await service.execute_validation(job.id)
        preview = service.get_preview(job.id)
        assert preview.fingerprint
        assert preview.expires_at == NOW + timedelta(minutes=10)
        assert _has_table(paths.database, "current_gold")
        assert not _has_table(paths.database, "portable_gold")

        current = NOW + timedelta(minutes=11)
        with pytest.raises(RestorePreviewError) as captured:
            service.get_preview(job.id)
        assert captured.value.code == "preview_expired"
        assert not tuple(paths.state_root.glob(".restore-stage-*"))
        assert not tuple(paths.library_root.glob(".restore-stage-*"))
        assert not (paths.state_root / "pending-restore.json").exists()
        assert _has_table(paths.database, "current_gold")
    finally:
        engine.dispose()


def test_restore_service_interrupts_unrecoverable_validation_and_cleans_owned_orphans(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.repository import BackupRepository
    from paper_reader.backups.service import RESTORE_VALIDATION_JOB_KIND, RestoreService
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.migrations import upgrade_database

    paths = _paths(tmp_path, "abandoned-validation")
    upgrade_database(paths.database, snapshot_root=paths.migration_snapshots)
    engine = create_sqlite_engine(paths.database)
    sessions = create_session_factory(engine)
    jobs = JobRepository(sessions)
    job = jobs.create(
        kind=RESTORE_VALIDATION_JOB_KIND,
        payload={"encrypted": False},
        idempotency_key="backup.restore.validate:abandoned",
    )
    upload_root = paths.state_root / ".restore-uploads"
    upload_root.mkdir()
    abandoned_upload = upload_root / "upload-abandoned.package"
    abandoned_upload.write_bytes(b"not reusable after a restart")
    state_stage = paths.state_root / ".restore-stage-abandoned"
    library_stage = paths.library_root / ".restore-stage-abandoned"
    state_stage.mkdir()
    library_stage.mkdir()
    (state_stage / "private.sqlite3").write_bytes(b"staged")
    (library_stage / "objects").mkdir()
    unrelated = paths.state_root / ".restore-stage-not-owned.txt"
    unrelated.write_bytes(b"preserve regular files outside the stage shape")

    service = RestoreService(
        paths=paths,
        jobs=jobs,
        archives=BackupRepository(sessions),
    )
    try:
        interrupted = jobs.get(job.id)
        assert interrupted is not None
        assert interrupted.status == "failed"
        assert interrupted.error_code == "upload_unavailable"
        assert interrupted.result == {"retryable": True}
        assert interrupted.checkpoint == {"phase": "validation_interrupted"}
        assert not abandoned_upload.exists()
        assert not state_stage.exists()
        assert not library_stage.exists()
        assert unrelated.read_bytes() == b"preserve regular files outside the stage shape"
    finally:
        service.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_restore_api_requires_preview_fingerprint_and_only_applies_on_next_start(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.migrations import upgrade_database

    package, _source = _package(tmp_path)
    paths = _paths(tmp_path, "target-api")
    upgrade_database(paths.database, snapshot_root=paths.migration_snapshots)
    _marker(paths.database, "current_gold", "before-restart")
    cache_canary = paths.cache_root / "machine-model-canary.bin"
    cache_canary.write_bytes(b"MACHINE-CACHE-MUST-SURVIVE")

    app = create_app(paths=paths, bootstrap_token="secret")
    async with _client(app) as client:
        headers = await _bootstrap(client)
        uploaded = await client.post(
            "/api/v1/backups/restore-packages",
            headers={**headers, "Idempotency-Key": "validate-portable"},
            files={
                "package": (
                    "portable.paper-reader-backup",
                    package.read_bytes(),
                    "application/octet-stream",
                )
            },
        )
        assert uploaded.status_code == 202
        validation = await _wait(client, uploaded.json()["id"])
        assert validation["status"] == "succeeded"
        assert "path" not in json.dumps(validation).casefold()
        preview = await client.get(f"/api/v1/backups/restore-previews/{uploaded.json()['id']}")
        assert preview.status_code == 200
        assert preview.headers["cache-control"] == "private, no-store"
        fingerprint = preview.json()["fingerprint"]

        stale = await client.post(
            "/api/v1/backups/restores",
            headers={**headers, "Idempotency-Key": "stale-confirmation"},
            json={
                "preview_id": uploaded.json()["id"],
                "fingerprint": "0" * 64,
            },
        )
        assert stale.status_code == 409
        confirmed = await client.post(
            "/api/v1/backups/restores",
            headers={**headers, "Idempotency-Key": "confirmed-restore"},
            json={
                "preview_id": uploaded.json()["id"],
                "fingerprint": fingerprint,
            },
        )
        assert confirmed.status_code == 202
        assert confirmed.json()["status"] == "pending_restart"
        duplicate = await client.post(
            "/api/v1/backups/restores",
            headers={**headers, "Idempotency-Key": "another-confirmation"},
            json={
                "preview_id": uploaded.json()["id"],
                "fingerprint": fingerprint,
            },
        )
        assert duplicate.status_code == 409
        assert _has_table(paths.database, "current_gold")
        assert not _has_table(paths.database, "portable_gold")
        assert (paths.state_root / "pending-restore.json").is_file()

    restarted = create_app(paths=paths, bootstrap_token="after-restore")
    try:
        assert _has_table(paths.database, "portable_gold")
        assert not _has_table(paths.database, "current_gold")
        assert cache_canary.read_bytes() == b"MACHINE-CACHE-MUST-SURVIVE"
        assert not (paths.state_root / "pending-restore.json").exists()
        connection = sqlite3.connect(paths.database)
        try:
            receipts = connection.execute(
                "SELECT package_sha256, migrated_to_revision FROM restore_receipts"
            ).fetchall()
        finally:
            connection.close()
        assert len(receipts) == 1
        assert len(receipts[0][0]) == 64
    finally:
        restarted.state.engine.dispose()


@pytest.mark.asyncio
async def test_encrypted_export_and_restore_validation_never_persist_password(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.backups.crypto import ENCRYPTED_BACKUP_MAGIC

    password = "private-library-password-73491"
    source_paths = _paths(tmp_path, "encrypted-source")
    source = create_app(paths=source_paths, bootstrap_token="secret")
    async with _client(source) as client:
        headers = await _bootstrap(client)
        queued = await client.post(
            "/api/v1/backups",
            headers={**headers, "Idempotency-Key": "encrypted-export"},
            json={
                "kind": "manual",
                "encrypted": True,
                "password": password,
            },
        )
        assert queued.status_code == 202
        finished = await _wait(client, queued.json()["id"])
        assert finished["status"] == "succeeded"
        listing = (await client.get("/api/v1/backups")).json()
        archive = next(item for item in listing if item["job_id"] == queued.json()["id"])
        assert archive["encrypted"] is True
        downloaded = await client.get(f"/api/v1/backups/{archive['id']}/download")
        assert downloaded.status_code == 200
        assert downloaded.content.startswith(ENCRYPTED_BACKUP_MAGIC)
        assert password not in json.dumps(finished)

    assert password.encode() not in source_paths.database.read_bytes()

    target_paths = _paths(tmp_path, "encrypted-target")
    target = create_app(paths=target_paths, bootstrap_token="secret")
    async with _client(target) as client:
        headers = await _bootstrap(client)
        validation = await client.post(
            "/api/v1/backups/restore-packages",
            headers={**headers, "Idempotency-Key": "encrypted-validation"},
            data={"password": password},
            files={
                "package": (
                    "portable.paper-reader-backup.enc",
                    downloaded.content,
                    "application/octet-stream",
                )
            },
        )
        assert validation.status_code == 202
        terminal = await _wait(client, validation.json()["id"])
        assert terminal["status"] == "succeeded"
        preview = await client.get(f"/api/v1/backups/restore-previews/{validation.json()['id']}")
        assert preview.status_code == 200
        assert preview.json()["encrypted"] is True

    assert password.encode() not in target_paths.database.read_bytes()


def test_golden_restore_preserves_all_domain_versions_but_not_machine_state(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.backups.archive import BackupArchiveBuilder
    from paper_reader.backups.restore import (
        RestorePackageValidator,
        schedule_pending_restore,
        staged_restore_fingerprint,
    )
    from paper_reader.migrations import upgrade_database

    class EmptyVault:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def get(self, name: str) -> str | None:
            return self.values.get(name)

        def set(self, name: str, value: str) -> None:
            self.values[name] = value

        def delete(self, name: str) -> None:
            self.values.pop(name, None)

    source = _paths(tmp_path, "gold-source")
    upgrade_database(source.database, snapshot_root=source.migration_snapshots)
    relative_pdf, pdf = _seed_golden_domain(source)
    expected = _domain_dump(source.database)
    package = source.backups / "gold-domain.paper-reader-backup"
    BackupArchiveBuilder(paths=source, clock=lambda: NOW).create(destination=package)

    target = _paths(tmp_path, "gold-target")
    upgrade_database(target.database, snapshot_root=target.migration_snapshots)
    _marker(target.database, "current_machine_only", "must-be-replaced")
    cache_canary = target.cache_root / "models" / "machine-only.onnx"
    cache_canary.parent.mkdir(parents=True, exist_ok=True)
    cache_canary.write_bytes(b"CURRENT-MACHINE-MODEL")
    vault = EmptyVault()
    validated = RestorePackageValidator(paths=target).validate(package)
    fingerprint = staged_restore_fingerprint(paths=target, package=validated)
    schedule_pending_restore(
        paths=target,
        package=validated,
        restore_id="22222222-2222-4222-8222-222222222222",
        expected_fingerprint=fingerprint,
        confirmed_at=NOW,
    )

    app = create_app(
        paths=target,
        bootstrap_token="gold-restart",
        credential_store=vault,
    )
    try:
        assert _domain_dump(target.database) == expected
        assert not _has_table(target.database, "current_machine_only")
        restored_pdf = target.library_root.joinpath(*relative_pdf.split("/"))
        assert restored_pdf.read_bytes() == pdf
        assert cache_canary.read_bytes() == b"CURRENT-MACHINE-MODEL"
        assert vault.values == {}
        assert not tuple(target.state_root.glob(".restore-stage-*"))
        assert not tuple(target.library_root.glob(".restore-stage-*"))
    finally:
        app.state.engine.dispose()


def test_launcher_stops_before_socket_or_browser_when_restore_is_deferred(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from paper_reader import cli
    from paper_reader.backups.startup import RestoreStartupError

    paths = _paths(tmp_path, "launcher")
    socket_opened = False

    def fail_create_app(**_kwargs: object) -> None:
        raise RestoreStartupError(
            "restore_deferred",
            "The restore was rolled back safely and will remain pending",
        )

    def record_socket(_port: int) -> None:
        nonlocal socket_opened
        socket_opened = True

    monkeypatch.setattr(cli.AppPaths, "default", lambda: paths)
    monkeypatch.setattr(cli.AppPaths, "bootstrap_state_root", lambda: paths.state_root)
    monkeypatch.setattr(cli, "create_app", fail_create_app)
    monkeypatch.setattr(cli, "_loopback_socket", record_socket)

    assert cli.run_server(port=0, open_browser=True) == 2
    assert socket_opened is False
    captured = capsys.readouterr()
    assert "rolled back safely" in captured.err
    assert "D:/" not in captured.err
