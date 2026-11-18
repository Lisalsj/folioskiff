from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError


def test_reader_schema_adds_library_membership_and_document_file_metadata(
    tmp_path: Path,
) -> None:
    from paper_reader.database import Base, create_sqlite_engine
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.models import LibraryEntryRecord

    engine = create_sqlite_engine(tmp_path / "reader-models.sqlite3")
    try:
        Base.metadata.create_all(engine)
        table_names = set(inspect(engine).get_table_names())
        document_columns = {
            column["name"] for column in inspect(engine).get_columns("document_versions")
        }
    finally:
        engine.dispose()

    assert LibraryEntryRecord.__tablename__ == "library_entries"
    assert "library_entries" in table_names
    assert {
        "byte_size",
        "original_filename",
        "acquisition_kind",
        "acquisition_url",
        "last_accessed_at",
    }.issubset(document_columns)
    assert DocumentVersionRecord.byte_size.default is not None
    assert DocumentVersionRecord.acquisition_kind.default is not None


def test_reader_schema_enforces_file_metadata_and_library_origin(
    tmp_path: Path,
) -> None:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
    from paper_reader.reader.models import LibraryEntryRecord

    engine = create_sqlite_engine(tmp_path / "reader-constraints.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    now = datetime(2026, 7, 13, tzinfo=UTC)
    try:
        with sessions() as session:
            session.add(
                WorkRecord(
                    id="work-reader",
                    canonical_title="Reader Models",
                    normalized_title="reader models",
                    authors=[],
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()

        with sessions() as session:
            session.add(
                DocumentVersionRecord(
                    id="document-invalid-size",
                    work_id="work-reader",
                    content_sha256="a" * 64,
                    access_status="user_provided",
                    completeness=100,
                    byte_size=-1,
                    acquisition_kind="manual_import",
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()

        with sessions() as session:
            session.add(
                LibraryEntryRecord(
                    work_id="work-reader",
                    added_via="crawler",
                    created_at=now,
                    updated_at=now,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()
