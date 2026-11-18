from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.platform._publishing_fixtures import (
    build_publishing_fixture,
    insert_quality_score,
    insert_report_revision,
)


def _preview(fixture):  # type: ignore[no-untyped-def]
    from paper_reader.publishing.models import PublicationSelection, PublicationWorkSelection
    from paper_reader.publishing.privacy import PublishingPrivacyBoundary

    selection = PublicationSelection(
        slug="quiet-library-picks",
        title="安静图书馆 <2026 夏季精选>",
        public_intro="公开导语 </script><form action='https://evil.example'>",
        works=(
            PublicationWorkSelection(work_id="work-b"),
            PublicationWorkSelection(work_id="work-alias", report_id="report-selected"),
        ),
    )
    return PublishingPrivacyBoundary(fixture.sessions).preview(selection)


@pytest.mark.parametrize("mutation", ["work", "report", "score"])
def test_confirmation_rejects_a_preview_after_any_public_dependency_changes(
    tmp_path: Path,
    mutation: str,
) -> None:
    from paper_reader.publishing.repository import (
        PublishedSnapshotRepository,
        StalePublicationPreview,
    )

    fixture = build_publishing_fixture(tmp_path)
    preview = _preview(fixture)
    if mutation == "work":
        connection = sqlite3.connect(fixture.database)
        try:
            connection.execute(
                "UPDATE works SET canonical_title = 'Changed canonical title', "
                "updated_at = '2026-07-14T11:00:00+00:00' WHERE id = 'work-a'"
            )
            connection.commit()
        finally:
            connection.close()
    elif mutation == "report":
        insert_report_revision(fixture.database)
    else:
        insert_quality_score(
            fixture.database,
            score_id="score-a-v2",
            work_id="work-a",
            score=91,
            created_at="2026-07-14T11:00:00+00:00",
            rule_version="quality-v8",
        )

    with pytest.raises(StalePublicationPreview):
        PublishedSnapshotRepository(fixture.sessions).freeze(preview)

    connection = sqlite3.connect(fixture.database)
    try:
        assert connection.execute("SELECT COUNT(*) FROM published_snapshots").fetchone() == (0,)
    finally:
        connection.close()


def test_freeze_is_idempotent_immutable_and_reloads_exact_file_bytes(tmp_path: Path) -> None:
    from paper_reader.publishing.repository import PublishedSnapshotRepository

    fixture = build_publishing_fixture(tmp_path)
    preview = _preview(fixture)
    repository = PublishedSnapshotRepository(fixture.sessions)

    snapshot = repository.freeze(preview)
    repeated = repository.freeze(preview)
    loaded = repository.require(snapshot.id)

    assert repeated.id == snapshot.id
    assert loaded == snapshot
    assert [item.position for item in snapshot.works] == [0, 1]
    assert [item.canonical_work_id for item in snapshot.works] == ["work-b", "work-a"]
    assert snapshot.works[1].report_id == "report-selected"
    assert snapshot.works[1].score_snapshot_id == "score-a-v1"
    assert snapshot.works[0].score_snapshot_id is None
    assert tuple(item.relative_path for item in snapshot.files) == (
        "index.html",
        "data.json",
        "manifest.json",
    )
    for item in snapshot.files:
        assert item.byte_size == len(item.content)
        assert item.sha256 == hashlib.sha256(item.content).hexdigest()
    assert snapshot.payload_sha256 == hashlib.sha256(
        next(item.content for item in snapshot.files if item.relative_path == "data.json")
    ).hexdigest()
    assert snapshot.manifest_sha256 == hashlib.sha256(
        next(item.content for item in snapshot.files if item.relative_path == "manifest.json")
    ).hexdigest()

    connection = sqlite3.connect(fixture.database)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        mutations = (
            ("UPDATE published_snapshots SET slug = 'changed' WHERE id = ?", (snapshot.id,)),
            ("DELETE FROM published_snapshots WHERE id = ?", (snapshot.id,)),
            (
                "UPDATE published_snapshot_works SET position = 9 WHERE snapshot_id = ? "
                "AND position = 0",
                (snapshot.id,),
            ),
            (
                "DELETE FROM published_snapshot_files WHERE snapshot_id = ? "
                "AND relative_path = 'index.html'",
                (snapshot.id,),
            ),
        )
        for statement, parameters in mutations:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(statement, parameters)
            connection.rollback()
    finally:
        connection.close()


def test_freeze_holds_one_write_transaction_while_revalidating_preview(
    tmp_path: Path,
) -> None:
    from paper_reader.publishing.repository import PublishedSnapshotRepository

    fixture = build_publishing_fixture(tmp_path)
    preview = _preview(fixture)
    contender_results: list[str] = []

    def clock() -> datetime:
        contender = sqlite3.connect(fixture.database, timeout=0)
        try:
            contender.execute(
                "UPDATE works SET canonical_title = 'racing update' WHERE id = 'work-a'"
            )
            contender.commit()
        except sqlite3.OperationalError as error:
            assert "locked" in str(error).casefold()
            contender_results.append("blocked")
        finally:
            contender.close()
        return datetime(2026, 7, 14, 12, tzinfo=UTC)

    snapshot = PublishedSnapshotRepository(fixture.sessions, clock=clock).freeze(preview)

    assert snapshot.payload.works[1].title.startswith("Canonical")
    assert contender_results == ["blocked"]


def test_rendered_snapshot_has_strict_csp_safe_json_and_deterministic_bytes(
    tmp_path: Path,
) -> None:
    from paper_reader.publishing.render import render_publication

    fixture = build_publishing_fixture(tmp_path)
    preview = _preview(fixture)

    first = render_publication(preview.payload)
    second = render_publication(preview.payload)

    assert first == second
    index = next(item.content for item in first.files if item.relative_path == "index.html")
    html = index.decode("utf-8")
    assert "Content-Security-Policy" in html
    assert "default-src 'none'" in html
    assert "base-uri 'none'" in html
    assert "form-action 'none'" in html
    assert "<script src" not in html.casefold()
    assert "<form" not in html.casefold()
    assert "<base" not in html.casefold()
    assert "<img src=x onerror" not in html.casefold()
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "\\u003c/script\\u003e" in html
    assert html.count("</script>") == 1

    payload = json.loads(
        next(item.content for item in first.files if item.relative_path == "data.json")
    )
    assert [work["canonical_work_id"] for work in payload["works"]] == [
        "work-b",
        "work-a",
    ]
    manifest = json.loads(
        next(item.content for item in first.files if item.relative_path == "manifest.json")
    )
    assert manifest["format_version"] == "paper-reader-published-v1"
    assert [item["relative_path"] for item in manifest["files"]] == [
        "index.html",
        "data.json",
    ]


def test_display_language_is_frozen_without_changing_public_paper_content(tmp_path: Path) -> None:
    from paper_reader.publishing.privacy import PublishingPrivacyBoundary
    from paper_reader.publishing.repository import PublishedSnapshotRepository

    fixture = build_publishing_fixture(tmp_path)
    chinese = _preview(fixture)
    # Existing selections omit the language in the bytes used for their fingerprint.
    assert "display_language" not in chinese.selection.model_dump(mode="json")
    english = PublishingPrivacyBoundary(fixture.sessions).preview(
        chinese.selection.model_copy(update={"display_language": "en"})
    )
    assert english.payload == chinese.payload
    assert english.fingerprint != chinese.fingerprint
    repository = PublishedSnapshotRepository(fixture.sessions)
    frozen = repository.freeze(english)
    loaded = repository.require(frozen.id)
    assert loaded.files == frozen.files
    assert loaded.files[1].content == repository.freeze(chinese).files[1].content
    assert b'<html lang="en">' in loaded.files[0].content
    assert b'aria-label="Contents"' in loaded.files[0].content
    assert b'<h4>Method</h4>' in loaded.files[0].content
