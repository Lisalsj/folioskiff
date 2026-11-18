from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from sqlalchemy import func, select

RELATIONSHIP_MATRIX = {
    "source_record_revisions": {
        ("source_record_id", "source_records", "id"),
        ("work_id", "works", "id"),
    },
    "paper_updates": {
        ("run_id", "plan_runs", "id"),
        ("work_id", "works", "id"),
        ("source_record_id", "source_records", "id"),
        ("previous_revision_id", "source_record_revisions", "id"),
        ("current_revision_id", "source_record_revisions", "id"),
    },
    "discovery_plans": {
        ("keyword_version_id", "keyword_set_versions", "id"),
    },
    "annotations": {
        ("document_version_id", "document_versions", "id"),
    },
    "analysis_reports": {
        ("work_id", "works", "id"),
        ("job_id", "jobs", "id"),
        ("document_version_id", "document_versions", "id"),
        ("template_version_id", "prompt_template_versions", "id"),
    },
    "assistant_sessions": {
        ("work_id", "works", "id"),
    },
    "assistant_context_snapshots": {
        ("session_id", "assistant_sessions", "id"),
        ("work_id", "works", "id"),
        ("document_version_id", "document_versions", "id"),
    },
    "assistant_tasks": {
        ("session_id", "assistant_sessions", "id"),
        ("context_snapshot_id", "assistant_context_snapshots", "id"),
        ("retry_of_task_id", "assistant_tasks", "id"),
        ("job_id", "jobs", "id"),
    },
    "assistant_agent_runs": {
        ("task_id", "assistant_tasks", "id"),
    },
    "assistant_messages": {
        ("session_id", "assistant_sessions", "id"),
        ("task_id", "assistant_tasks", "id"),
        ("job_id", "jobs", "id"),
        ("reply_to_message_id", "assistant_messages", "id"),
    },
    "assistant_fragments": {
        ("task_id", "assistant_tasks", "id"),
        ("run_id", "assistant_agent_runs", "id"),
    },
    "assistant_checkpoints": {
        ("session_id", "assistant_sessions", "id"),
    },
    "translation_artifacts": {
        ("work_id", "works", "id"),
        ("document_version_id", "document_versions", "id"),
        ("context_snapshot_id", "assistant_context_snapshots", "id"),
        ("task_id", "assistant_tasks", "id"),
        ("job_id", "jobs", "id"),
    },
    "translation_segments": {
        ("artifact_id", "translation_artifacts", "id"),
    },
    "translation_terms": {
        ("artifact_id", "translation_artifacts", "id"),
    },
    "translation_exports": {
        ("artifact_id", "translation_artifacts", "id"),
    },
    "related_resource_sets": {
        ("work_id", "works", "id"),
        ("document_version_id", "document_versions", "id"),
        ("context_snapshot_id", "assistant_context_snapshots", "id"),
        ("job_id", "jobs", "id"),
    },
    "related_paper_candidates": {
        ("set_id", "related_resource_sets", "id"),
        ("linked_work_id", "works", "id"),
        ("merge_review_id", "merge_reviews", "id"),
    },
    "knowledge_resources": {
        ("set_id", "related_resource_sets", "id"),
    },
    "related_run_sources": {
        ("set_id", "related_resource_sets", "id"),
    },
    "recommendation_sets": {
        ("run_id", "plan_runs", "id"),
        ("keyword_version_id", "keyword_set_versions", "id"),
    },
    "recommendation_items": {
        ("recommendation_set_id", "recommendation_sets", "id"),
        ("scored_work_id", "works", "id"),
        ("relevance_snapshot_id", "score_snapshots", "id"),
        ("quality_snapshot_id", "score_snapshots", "id"),
        ("freshness_snapshot_id", "score_snapshots", "id"),
        ("preference_snapshot_id", "score_snapshots", "id"),
        ("composite_snapshot_id", "score_snapshots", "id"),
    },
    "library_states": {
        ("work_id", "works", "id"),
    },
    "preference_aggregates": {
        ("work_id", "works", "id"),
    },
}


def test_head_schema_enforces_release_relationship_matrix(tmp_path: Path) -> None:
    from paper_reader.migrations import migration_head_revision, upgrade_database

    database = tmp_path / "integrity-matrix.sqlite3"
    upgrade_database(database)
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        actual = {
            table: {
                (str(row[3]), str(row[2]), str(row[4]))
                for row in connection.execute(f"PRAGMA foreign_key_list({table})")
            }
            for table in RELATIONSHIP_MATRIX
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        foreign_key_failure = connection.execute("PRAGMA foreign_key_check").fetchone()
    finally:
        connection.close()

    assert revision == (migration_head_revision(),)
    assert quick_check == ("ok",)
    assert foreign_key_failure is None
    assert {"user_preferences", *RELATIONSHIP_MATRIX}.issubset(tables)
    for table, expected in RELATIONSHIP_MATRIX.items():
        assert expected.issubset(actual[table]), table


def test_pdf_doi_and_bibtex_reimports_converge_on_one_work_and_one_object(
    tmp_path: Path,
) -> None:
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.models import (
        DocumentVersionRecord,
        SourceRecord,
        WorkRecord,
    )
    from paper_reader.migrations import upgrade_database
    from paper_reader.reader.imports import (
        LibraryImportService,
        PdfImportInput,
        ReferenceImportInput,
        ReferenceKind,
    )
    from paper_reader.reader.models import LibraryEntryRecord
    from paper_reader.reader.storage import ContentStore

    database = tmp_path / "cross-import.sqlite3"
    library = tmp_path / "library"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    service = LibraryImportService(
        sessions=sessions,
        store=ContentStore(library),
        clock=lambda: datetime(2026, 7, 15, 8, 0, tzinfo=UTC),
    )
    pdf = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
    bibtex = ReferenceImportInput(
        kind=ReferenceKind.BIBTEX,
        value=(
            "@article{integrity2026, title={Reliable Paper Reader Agent}, "
            "author={Alice Chen and Bob Kumar}, doi={10.5555/integrity.reader}}"
        ),
    )
    doi = ReferenceImportInput(
        kind=ReferenceKind.DOI,
        value="https://doi.org/10.5555/INTEGRITY.READER",
        title="Reliable Paper-Reader Agents",
        authors=("Alice Chen", "Bob Kumar"),
    )
    pdf_input = PdfImportInput(
        work_id=None,
        title="Reliable Paper Reader Agent",
        authors=("Alice Chen", "Bob Kumar"),
        doi="10.5555/integrity.reader",
        original_filename="integrity-reader.pdf",
        rights_confirmed=True,
    )
    try:
        first = service.import_reference(bibtex)
        second = service.import_reference(doi)
        third = service.import_pdf(pdf_input, (pdf,))
        repeated_bibtex = service.import_reference(bibtex)
        repeated_doi = service.import_reference(doi)
        repeated_pdf = service.import_pdf(pdf_input, (pdf,))

        assert {
            first.work_id,
            second.work_id,
            third.work_id,
            repeated_bibtex.work_id,
            repeated_doi.work_id,
            repeated_pdf.work_id,
        } == {first.work_id}
        assert third.created_document is True
        assert repeated_pdf.created_document is False
        assert third.document_version_id == repeated_pdf.document_version_id
        assert third.content_sha256 == repeated_pdf.content_sha256 == sha256(pdf).hexdigest()

        with sessions() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(WorkRecord)
                    .where(WorkRecord.status == "active")
                )
                == 1
            )
            assert session.scalar(select(func.count()).select_from(SourceRecord)) == 3
            assert session.scalar(select(func.count()).select_from(DocumentVersionRecord)) == 1
            assert session.scalar(select(func.count()).select_from(LibraryEntryRecord)) == 1
            assert (
                session.connection().exec_driver_sql("PRAGMA foreign_key_check").fetchone() is None
            )
        objects = tuple((library / "objects").rglob("*.pdf"))
        assert len(objects) == 1
        assert objects[0].read_bytes() == pdf
    finally:
        engine.dispose()
