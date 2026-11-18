from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest


def _report_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.agents.reports import AnalysisReportRepository
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
    from paper_reader.jobs.models import JobRecord
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "paper-reader.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    with sessions() as session:
        work = WorkRecord(
            id="work-report",
            canonical_title="Report fixture",
            normalized_title="report fixture",
            authors=["Alice"],
            abstract="Fixture abstract.",
            published_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        session.add(work)
        session.flush()
        session.add(
            DocumentVersionRecord(
                id="document-report",
                work_id=work.id,
                source_record_id=None,
                content_sha256="d" * 64,
                access_status="user_provided",
                media_type="application/pdf",
                completeness=100,
                extraction_status="complete",
                relative_path=None,
                byte_size=100,
                acquisition_kind="manual_import",
            )
        )
        session.add_all(
            JobRecord(
                id=f"job-report-{index}",
                kind="analysis.report",
                status="running",
                progress=0,
                payload={},
            )
            for index in range(1, 7)
        )
        session.commit()
    return database, engine, sessions, AnalysisReportRepository(sessions)


def _evidence():  # type: ignore[no-untyped-def]
    from paper_reader.agents.evidence import (
        EvidenceBundle,
        EvidenceCoverage,
        EvidenceDocument,
        EvidenceLevel,
        EvidencePage,
        EvidenceWork,
    )

    return EvidenceBundle.create(
        work=EvidenceWork(
            work_id="work-report",
            title="Report fixture",
            authors=("Alice",),
            abstract="Fixture abstract.",
            doi=None,
            published_at="2026-06-01T00:00:00+00:00",
        ),
        document=EvidenceDocument(
            document_version_id="document-report",
            content_sha256="d" * 64,
            access_status="user_provided",
            media_type="application/pdf",
            completeness=100,
            extraction_status="complete",
        ),
        evidence_level=EvidenceLevel.FULL_TEXT,
        pages=(
            EvidencePage(
                evidence_id="page:1:native",
                page_number=1,
                source="native",
                text="Stable report evidence.",
                source_text_sha256="a" * 64,
                page_width=420,
                page_height=595,
                confidence=None,
                truncated=False,
            ),
        ),
        annotations=(),
        coverage=EvidenceCoverage(
            total_pages=1,
            available_pages=1,
            included_pages=1,
            total_characters=23,
            included_characters=23,
            percentage=100,
            truncated=False,
        ),
        research_intent="Review the evidence",
    )


def _report(title: str = "Fixture report"):  # type: ignore[no-untyped-def]
    from paper_reader.agents.report_schema import (
        EvidenceReference,
        ReportStatement,
        StructuredAnalysisReport,
    )

    statement = ReportStatement(
        text="Supported by the selected paper page.",
        references=(
            EvidenceReference(
                evidence_id="page:1:native",
                kind="page",
                page_number=1,
            ),
        ),
    )
    return StructuredAnalysisReport(
        title=title,
        overview=statement,
        research_question=statement,
        author_claims=(statement,),
        contributions=(statement,),
        method=(statement,),
        assumptions=(statement,),
        data_and_experiments=(statement,),
        findings=(statement,),
        limitations=(statement,),
        reproducibility=(statement,),
        relation_to_user_intent=(statement,),
        agent_judgments=(statement,),
    )


def _provenance(job_id: str, *, prompt: str = "exact composed prompt"):  # type: ignore[no-untyped-def]
    from paper_reader.agents.contracts import ReasoningEffort, ServiceTier
    from paper_reader.agents.reports import AnalysisProvenance

    return AnalysisProvenance.create(
        job_id=job_id,
        work_id="work-report",
        document_version_id="document-report",
        evidence=_evidence(),
        backend_id="codex_cli",
        requested_model_id=None,
        resolved_model_id="resolved-model-id",
        reasoning_effort=ReasoningEffort.HIGH,
        service_tier=ServiceTier.FLEX,
        language="zh-CN",
        template_version_id="builtin-general-v1",
        complete_prompt=prompt,
        external_research=False,
    )


def test_formal_reports_use_immutable_versions_and_preserve_exact_provenance(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.report_schema import render_report_markdown
    from paper_reader.agents.reports import ReportConflictError

    _, engine, _, repository = _report_context(tmp_path)
    try:
        first_provenance = _provenance("job-report-1")
        first = repository.save_formal(first_provenance, _report("Version one"))
        repeated = repository.save_formal(first_provenance, _report("Version one"))
        second = repository.save_formal(
            _provenance("job-report-2", prompt="second exact prompt"),
            _report("Version two"),
        )
        versions = repository.list_for_work("work-report")
    finally:
        engine.dispose()

    assert first == repeated
    assert (first.version, second.version) == (1, 2)
    assert [item.version for item in versions] == [2, 1]
    assert versions[1].content.title == "Version one"
    assert first.job_id == first_provenance.job_id
    assert first.document_version_id == "document-report"
    assert first.document_content_sha256 == "d" * 64
    assert first.evidence_level == "full_text"
    assert first.evidence_sha256 == first_provenance.evidence.evidence_sha256
    assert first.evidence_manifest["evidence_sha256"] == first.evidence_sha256
    assert first.backend_id == "codex_cli"
    assert first.requested_model_id is None
    assert first.resolved_model_id == "resolved-model-id"
    assert first.reasoning_effort == "high"
    assert first.service_tier == "flex"
    assert first.language == "zh-CN"
    assert first.template_version_id == "builtin-general-v1"
    assert first.complete_prompt == "exact composed prompt"
    assert first.prompt_sha256 == first_provenance.prompt_sha256
    assert first.request_fingerprint == first_provenance.request_fingerprint
    assert first.network_policy == "local_evidence_only"
    assert first.schema_version == "analysis-report-v1"
    assert first.rendered_markdown == render_report_markdown(first.content)
    assert first.external_citations == ()
    with pytest.raises(ValueError, match="prompt SHA-256"):
        replace(first_provenance, prompt_sha256="0" * 64)
    with pytest.raises(ValueError, match="request fingerprint"):
        replace(first_provenance, request_fingerprint="0" * 64)
    with pytest.raises(ReportConflictError, match="different formal report"):
        repository.save_formal(first_provenance, _report("Changed same job"))


def test_fragments_are_ordered_idempotent_append_only_and_stop_after_outcome(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.reports import ReportConflictError

    _, engine, _, repository = _report_context(tmp_path)
    try:
        zero = repository.append_fragment(
            job_id="job-report-3",
            sequence=0,
            kind="reasoning",
            text="first",
        )
        one = repository.append_fragment(
            job_id="job-report-3",
            sequence=1,
            kind="text",
            text="second",
        )
        repeated = repository.append_fragment(
            job_id="job-report-3",
            sequence=1,
            kind="text",
            text="second",
        )
        fragments = repository.list_fragments("job-report-3")
        with pytest.raises(ReportConflictError, match="different fragment"):
            repository.append_fragment(
                job_id="job-report-3",
                sequence=1,
                kind="text",
                text="changed",
            )
        with pytest.raises(ReportConflictError, match="next sequence"):
            repository.append_fragment(
                job_id="job-report-3",
                sequence=3,
                kind="text",
                text="gap",
            )
        repository.save_formal(_provenance("job-report-3"), _report())
        with pytest.raises(ReportConflictError, match="finished"):
            repository.append_fragment(
                job_id="job-report-3",
                sequence=2,
                kind="text",
                text="late",
            )
    finally:
        engine.dispose()

    assert repeated == one
    assert [item.sequence for item in fragments] == [0, 1]
    assert [item.text for item in fragments] == ["first", "second"]
    assert zero.kind == "reasoning"


def test_formal_and_failure_are_exclusive_and_failure_diagnostics_are_bounded(
    tmp_path: Path,
) -> None:
    from paper_reader.agents.reports import ReportConflictError

    _, engine, _, repository = _report_context(tmp_path)
    fingerprint = _provenance("job-report-4").request_fingerprint
    raw = "token=sk-" + "a" * 40 + "\n" + "x" * 120_000
    try:
        failure = repository.save_failure(
            job_id="job-report-4",
            work_id="work-report",
            request_fingerprint=fingerprint,
            error_category="invalid_output",
            error_message="api_key=super-secret invalid JSON",
            raw_response=raw,
            validation_errors=tuple(f"error-{index}" for index in range(120)),
            repair_count=1,
        )
        repeated = repository.save_failure(
            job_id="job-report-4",
            work_id="work-report",
            request_fingerprint=fingerprint,
            error_category="invalid_output",
            error_message="api_key=super-secret invalid JSON",
            raw_response=raw,
            validation_errors=tuple(f"error-{index}" for index in range(120)),
            repair_count=1,
        )
        with pytest.raises(ReportConflictError, match="failure draft"):
            repository.save_formal(_provenance("job-report-4"), _report())

        repository.save_formal(_provenance("job-report-5"), _report("Old formal"))
        with pytest.raises(ReportConflictError, match="formal report"):
            repository.save_failure(
                job_id="job-report-5",
                work_id="work-report",
                request_fingerprint=_provenance("job-report-5").request_fingerprint,
                error_category="timeout",
                error_message="timeout",
                raw_response="",
                validation_errors=(),
                repair_count=0,
            )
        assert [item.content.title for item in repository.list_for_work("work-report")] == [
            "Old formal"
        ]
    finally:
        engine.dispose()

    assert repeated == failure
    assert failure.repair_count == 1
    assert len(failure.error_message) <= 4_000
    assert len(failure.raw_response) <= 100_000
    assert len(failure.validation_errors) == 100
    assert "super-secret" not in failure.error_message
    assert "sk-" not in failure.raw_response


def test_migration_triggers_reject_update_delete_and_cross_outcome_insert(
    tmp_path: Path,
) -> None:
    database, engine, _, repository = _report_context(tmp_path)
    repository.append_fragment(
        job_id="job-report-1",
        sequence=0,
        kind="text",
        text="fragment",
    )
    report = repository.save_formal(_provenance("job-report-1"), _report())
    repository.save_failure(
        job_id="job-report-2",
        work_id="work-report",
        request_fingerprint=_provenance("job-report-2").request_fingerprint,
        error_category="invalid_output",
        error_message="invalid",
        raw_response="bad",
        validation_errors=("missing field",),
        repair_count=1,
    )
    engine.dispose()

    connection = sqlite3.connect(database)
    try:
        for statement in (
            "UPDATE analysis_reports SET version = 9 WHERE id = ?",
            "DELETE FROM analysis_reports WHERE id = ?",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(statement, (report.id,))
            connection.rollback()
        for statement, parameters in (
            (
                "UPDATE analysis_report_fragments SET text = 'changed' "
                "WHERE job_id = ? AND sequence = 0",
                ("job-report-1",),
            ),
            (
                "DELETE FROM analysis_report_fragments "
                "WHERE job_id = ? AND sequence = 0",
                ("job-report-1",),
            ),
            (
                "UPDATE analysis_failure_drafts SET repair_count = 0 WHERE job_id = ?",
                ("job-report-2",),
            ),
            (
                "DELETE FROM analysis_failure_drafts WHERE job_id = ?",
                ("job-report-2",),
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(statement, parameters)
            connection.rollback()
    finally:
        connection.close()


def test_repository_rejects_stale_document_evidence_and_invalid_repair_count(
    tmp_path: Path,
) -> None:
    _, engine, _, repository = _report_context(tmp_path)
    provenance = _provenance("job-report-6")
    try:
        stale_evidence = provenance.evidence.model_copy(
            update={
                "document": provenance.evidence.document.model_copy(  # type: ignore[union-attr]
                    update={"content_sha256": "e" * 64}
                )
            }
        )
        with pytest.raises(ValueError, match="evidence SHA-256"):
            replace(provenance, evidence=stale_evidence)
        with pytest.raises(ValueError, match="repair_count"):
            repository.save_failure(
                job_id="job-report-6",
                work_id="work-report",
                request_fingerprint=provenance.request_fingerprint,
                error_category="invalid_output",
                error_message="invalid",
                raw_response="bad",
                validation_errors=(),
                repair_count=2,
            )
    finally:
        engine.dispose()
