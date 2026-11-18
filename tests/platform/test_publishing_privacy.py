from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import event

from tests.platform._publishing_fixtures import CANARIES, build_publishing_fixture


def _selection(*, work_ids: tuple[str, ...] = ("work-b", "work-alias")):
    from paper_reader.publishing.models import PublicationSelection, PublicationWorkSelection

    return PublicationSelection(
        slug="quiet-library-picks",
        title="安静图书馆 <2026 夏季精选>",
        public_intro="公开导语 </script><form action='https://evil.example'>",
        works=tuple(
            PublicationWorkSelection(
                work_id=work_id,
                report_id="report-selected" if work_id == "work-alias" else None,
            )
            for work_id in work_ids
        ),
    )


def test_preview_is_read_only_canonical_ordered_and_strictly_whitelisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.publishing.privacy import PublishingPrivacyBoundary
    from paper_reader.publishing.render import render_publication

    fixture = build_publishing_fixture(tmp_path)
    monkeypatch.setenv("PAPER_READER_PRIVATE_KEY", CANARIES["credential"])
    boundary = PublishingPrivacyBoundary(fixture.sessions)
    statements: list[str] = []

    def capture_statement(  # type: ignore[no-untyped-def]
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(str(statement))

    engine = fixture.sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture_statement)
    before = sqlite3.connect(fixture.database)
    try:
        before_counts = tuple(
            before.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "published_snapshots",
                "published_snapshot_works",
                "published_snapshot_files",
            )
        )
    finally:
        before.close()

    try:
        preview = boundary.preview(_selection())
        repeated = boundary.preview(_selection())
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)
    rendered = render_publication(preview.payload)

    assert preview.fingerprint == repeated.fingerprint
    assert preview.payload == repeated.payload
    assert [work.canonical_work_id for work in preview.payload.works] == [
        "work-b",
        "work-a",
    ]
    canonical = preview.payload.works[1]
    assert canonical.title == "Canonical <img src=x onerror=alert(1)> Paper"
    assert canonical.authors == ("Alice & Bob", "陈研究员")
    assert canonical.abstract == "Canonical public abstract."
    assert canonical.report is not None
    assert canonical.report.id == fixture.selected_report_id
    assert canonical.report.version == 1
    assert canonical.quality_score is not None
    assert canonical.quality_score.score == 88
    assert canonical.quality_score.rule_version == "quality-v7"
    assert canonical.quality_score.coverage == 72
    assert canonical.quality_score.confidence == 81
    assert preview.payload.works[0].abstract is None
    assert preview.payload.works[0].report is None
    assert preview.payload.works[0].quality_score is None
    query_text = "\n".join(statements).casefold()
    assert "analysis_reports.complete_prompt" not in query_text
    assert "analysis_reports.evidence_manifest" not in query_text
    assert "score_snapshots.components" not in query_text
    assert "score_snapshots.weights" not in query_text
    for private_table in (
        "annotations",
        "assistant_messages",
        "document_page_texts",
        "implicit_preference_events",
        "plugin_installations",
    ):
        assert private_table not in query_text

    all_public_bytes = preview.model_dump_json().encode("utf-8") + b"".join(
        item.content for item in rendered.files
    )
    for canary in CANARIES.values():
        assert canary.encode() not in all_public_bytes
    forbidden_keys = {
        "complete_prompt",
        "evidence_manifest",
        "relative_path",
        "raw_payload",
        "components",
        "weights",
        "missing_policy",
        "hard_constraints",
        "requested_model_id",
        "resolved_model_id",
    }
    assert forbidden_keys.isdisjoint(_all_keys(preview.payload.model_dump(mode="json")))

    after = sqlite3.connect(fixture.database)
    try:
        after_counts = tuple(
            after.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "published_snapshots",
                "published_snapshot_works",
                "published_snapshot_files",
            )
        )
    finally:
        after.close()
    assert before_counts == after_counts == (0, 0, 0)


def test_selection_rejects_empty_invalid_or_duplicate_canonical_works(tmp_path: Path) -> None:
    from paper_reader.publishing.models import PublicationSelection
    from paper_reader.publishing.privacy import (
        DuplicateCanonicalWorkError,
        PublishingPrivacyBoundary,
    )

    fixture = build_publishing_fixture(tmp_path)
    boundary = PublishingPrivacyBoundary(fixture.sessions)

    with pytest.raises(ValidationError):
        PublicationSelection(
            slug="Not A Safe Slug",
            title="Title",
            public_intro="Intro",
            works=(),
        )
    with pytest.raises(DuplicateCanonicalWorkError):
        boundary.preview(_selection(work_ids=("work-a", "work-alias")))


def test_only_an_explicit_formal_report_for_the_same_canonical_work_is_allowed(
    tmp_path: Path,
) -> None:
    from paper_reader.publishing.models import (
        PublicationSelection,
        PublicationWorkSelection,
    )
    from paper_reader.publishing.privacy import (
        InvalidPublicationSelection,
        PublishingPrivacyBoundary,
    )

    fixture = build_publishing_fixture(tmp_path)
    boundary = PublishingPrivacyBoundary(fixture.sessions)
    failed_draft = PublicationSelection(
        slug="failed-draft",
        title="Failed draft",
        public_intro="",
        works=(PublicationWorkSelection(work_id="work-a", report_id="job-failed"),),
    )
    mismatched = PublicationSelection(
        slug="mismatched-report",
        title="Mismatch",
        public_intro="",
        works=(PublicationWorkSelection(work_id="work-b", report_id="report-selected"),),
    )

    with pytest.raises(InvalidPublicationSelection, match="formal report"):
        boundary.preview(failed_draft)
    with pytest.raises(InvalidPublicationSelection, match="same canonical Work"):
        boundary.preview(mismatched)


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_all_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_all_keys(child))
    return keys
