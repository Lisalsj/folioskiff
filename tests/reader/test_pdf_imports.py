from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
NOW = datetime(2026, 7, 13, 8, 30, tzinfo=UTC)


@pytest.fixture
def import_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery import models as discovery_models
    from paper_reader.reader.imports import LibraryImportService
    from paper_reader.reader.models import LibraryEntryRecord
    from paper_reader.reader.storage import ContentStore

    assert discovery_models.WorkRecord.__tablename__ == "works"
    assert LibraryEntryRecord.__tablename__ == "library_entries"
    engine = create_sqlite_engine(tmp_path / "reader-imports.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    store = ContentStore(tmp_path / "library")
    service = LibraryImportService(sessions=sessions, store=store, clock=lambda: NOW)
    try:
        yield service, sessions, store, tmp_path / "library"
    finally:
        engine.dispose()


def _pdf_input(
    *,
    work_id: str | None = None,
    title: str | None = "Reliable Paper Reading",
    rights_confirmed: bool = True,
):  # type: ignore[no-untyped-def]
    from paper_reader.reader.imports import PdfImportInput

    return PdfImportInput(
        work_id=work_id,
        title=title,
        authors=("Alice Chen", "Bob Kumar"),
        doi="https://doi.org/10.5555/Reader.42",
        original_filename="reader-paper.pdf",
        rights_confirmed=rights_confirmed,
    )


def _create_work(sessions, *, external_id: str, title: str) -> str:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper

    result = IdentityRepository(sessions).ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id=external_id,
            title=title,
            authors=("Researcher One",),
        )
    )
    return result.work.id


def test_pdf_import_public_contract_is_available() -> None:
    from paper_reader.reader.imports import (
        LibraryImportService,
        PdfImportInput,
        PdfImportResult,
    )

    assert LibraryImportService.__name__ == "LibraryImportService"
    assert PdfImportInput.__name__ == "PdfImportInput"
    assert PdfImportResult.__name__ == "PdfImportResult"


def test_pdf_import_service_accepts_explicit_local_dependencies(tmp_path: Path) -> None:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery import models as discovery_models
    from paper_reader.reader.imports import LibraryImportService
    from paper_reader.reader.models import LibraryEntryRecord
    from paper_reader.reader.storage import ContentStore

    assert LibraryEntryRecord.__tablename__ == "library_entries"
    assert discovery_models.WorkRecord.__tablename__ == "works"
    engine = create_sqlite_engine(tmp_path / "reader-import.sqlite3")
    Base.metadata.create_all(engine)
    try:
        service = LibraryImportService(
            sessions=create_session_factory(engine),
            store=ContentStore(tmp_path / "library"),
            clock=lambda: datetime(2026, 7, 13, tzinfo=UTC),
        )
    finally:
        engine.dispose()

    assert service is not None


def test_pdf_import_requires_rights_confirmation_before_writing_bytes(
    import_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
    from paper_reader.reader.imports import RightsConfirmationRequiredError
    from paper_reader.reader.models import LibraryEntryRecord

    service, sessions, _, library_root = import_context

    with pytest.raises(RightsConfirmationRequiredError):
        service.import_pdf(
            _pdf_input(rights_confirmed=False),
            (PDF_BYTES,),
        )

    assert not tuple(library_root.rglob("*.pdf"))
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(WorkRecord)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentVersionRecord)) == 0
        assert session.scalar(select(func.count()).select_from(LibraryEntryRecord)) == 0


def test_pdf_import_creates_a_manual_work_library_entry_and_document(
    import_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import (
        DocumentVersionRecord,
        SourceRecord,
        WorkRecord,
    )
    from paper_reader.reader.imports import PdfImportInput
    from paper_reader.reader.models import LibraryEntryRecord

    service, sessions, store, _ = import_context
    value = PdfImportInput(
        work_id=None,
        title=None,
        authors=("  Alice   Chen ", "Bob Kumar"),
        doi="HTTPS://DOI.ORG/10.5555/Reader.42",
        original_filename="..\\  Quiet Reader Paper .pdf",
        rights_confirmed=True,
    )

    result = service.import_pdf(value, (PDF_BYTES[:9], PDF_BYTES[9:]))

    assert result.created_work is True
    assert result.created_document is True
    with sessions() as session:
        work = session.get(WorkRecord, result.work_id)
        source = session.scalar(
            select(SourceRecord).where(SourceRecord.work_id == result.work_id)
        )
        document = session.get(DocumentVersionRecord, result.document_version_id)
        library_entry = session.get(LibraryEntryRecord, result.work_id)
        assert work is not None
        assert work.canonical_title == "Quiet Reader Paper"
        assert work.authors == ["Alice Chen", "Bob Kumar"]
        assert work.doi == "10.5555/reader.42"
        assert source is not None
        assert source.connector_id == "manual"
        assert source.external_id == result.content_sha256
        assert document is not None
        assert document.access_status == "user_provided"
        assert document.acquisition_kind == "manual_import"
        assert document.original_filename == "Quiet Reader Paper .pdf"
        assert document.byte_size == len(PDF_BYTES)
        assert document.completeness == 100
        assert document.relative_path is not None
        assert store.resolve(document.relative_path).read_bytes() == PDF_BYTES
        assert library_entry is not None
        assert library_entry.added_via == "pdf"


def test_pdf_import_is_idempotent_for_the_same_work_and_hash(
    import_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.models import LibraryEntryRecord

    service, sessions, _, library_root = import_context
    work_id = _create_work(
        sessions,
        external_id="2607.00001v1",
        title="Existing Reader Work",
    )
    value = _pdf_input(work_id=work_id)

    first = service.import_pdf(value, (PDF_BYTES,))
    second = service.import_pdf(value, (PDF_BYTES,))

    assert first.work_id == second.work_id == work_id
    assert first.document_version_id == second.document_version_id
    assert first.created_work is False
    assert second.created_work is False
    assert first.created_document is True
    assert second.created_document is False
    assert len(tuple(library_root.rglob("*.pdf"))) == 1
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(DocumentVersionRecord)
                .where(DocumentVersionRecord.work_id == work_id)
            )
            == 1
        )
        assert session.get(LibraryEntryRecord, work_id) is not None


def test_pdf_import_reuses_physical_bytes_across_distinct_works(
    import_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import DocumentVersionRecord

    service, sessions, _, library_root = import_context
    first_work = _create_work(
        sessions,
        external_id="2607.00002v1",
        title="First Work",
    )
    second_work = _create_work(
        sessions,
        external_id="2607.00003v1",
        title="Second Work",
    )

    first = service.import_pdf(_pdf_input(work_id=first_work), (PDF_BYTES,))
    second = service.import_pdf(_pdf_input(work_id=second_work), (PDF_BYTES,))

    assert first.document_version_id != second.document_version_id
    assert first.content_sha256 == second.content_sha256
    assert len(tuple(library_root.rglob("*.pdf"))) == 1
    with sessions() as session:
        documents = list(
            session.scalars(
                select(DocumentVersionRecord).order_by(DocumentVersionRecord.work_id)
            )
        )
        assert len(documents) == 2
        assert documents[0].relative_path == documents[1].relative_path


def test_pdf_import_rejects_unknown_work_before_storing_content(
    import_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.reader.imports import WorkNotFoundError

    service, _, _, library_root = import_context

    with pytest.raises(WorkNotFoundError):
        service.import_pdf(_pdf_input(work_id="missing-work"), (PDF_BYTES,))

    assert not tuple(library_root.rglob("*.pdf"))


def test_pdf_import_does_not_create_database_rows_for_invalid_pdf(
    import_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
    from paper_reader.reader.storage import InvalidPdfError

    service, sessions, _, _ = import_context

    with pytest.raises(InvalidPdfError):
        service.import_pdf(_pdf_input(), (b"not a pdf",))

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(WorkRecord)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentVersionRecord)) == 0


def test_pdf_import_upgrades_an_existing_restricted_source_version(
    import_context,
) -> None:  # type: ignore[no-untyped-def]
    from hashlib import sha256

    from paper_reader.discovery.identity import IdentityRepository
    from paper_reader.discovery.models import DocumentVersionRecord

    service, sessions, _, _ = import_context
    work_id = _create_work(
        sessions,
        external_id="2607.00004v1",
        title="Restricted Before User Import",
    )
    digest = sha256(PDF_BYTES).hexdigest()
    existing = IdentityRepository(sessions).add_document_version(
        work_id=work_id,
        content_sha256=digest,
        access_status="restricted",
        completeness=100,
        source_updated_at=NOW,
    )

    result = service.import_pdf(_pdf_input(work_id=work_id), (PDF_BYTES,))

    assert result.document_version_id == existing.id
    assert result.created_document is False
    with sessions() as session:
        updated = session.get(DocumentVersionRecord, existing.id)
        assert updated is not None
        assert updated.access_status == "user_provided"
        assert updated.acquisition_kind == "manual_import"
        assert updated.relative_path is not None


@pytest.mark.parametrize(
    "value",
    (
        _pdf_input(work_id=" "),
        _pdf_input(title=" "),
    ),
)
def test_pdf_import_rejects_blank_identity_or_title_before_database_commit(
    import_context,
    value,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.reader.imports import PdfImportError, WorkNotFoundError

    service, _, _, library_root = import_context
    with pytest.raises((PdfImportError, WorkNotFoundError)):
        service.import_pdf(value, (PDF_BYTES,))
    assert not tuple(library_root.rglob("*.pdf"))


def test_pdf_import_rejects_unsafe_metadata_and_naive_clock(tmp_path: Path) -> None:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery import models as discovery_models
    from paper_reader.reader.imports import LibraryImportService, PdfImportError, PdfImportInput
    from paper_reader.reader.models import LibraryEntryRecord
    from paper_reader.reader.storage import ContentStore

    assert discovery_models.WorkRecord.__tablename__ == "works"
    assert LibraryEntryRecord.__tablename__ == "library_entries"
    engine = create_sqlite_engine(tmp_path / "invalid-import-metadata.sqlite3")
    Base.metadata.create_all(engine)
    service = LibraryImportService(
        sessions=create_session_factory(engine),
        store=ContentStore(tmp_path / "library"),
        clock=lambda: datetime(2026, 7, 13),
    )
    try:
        with pytest.raises(PdfImportError, match="original_filename"):
            service.import_pdf(
                PdfImportInput(None, None, (), None, "\x00", True),
                (PDF_BYTES,),
            )
        with pytest.raises(PdfImportError, match="author names"):
            service.import_pdf(
                PdfImportInput(None, "Title", ("a" * 256,), None, "paper.pdf", True),
                (PDF_BYTES,),
            )
        with pytest.raises(ValueError, match="timezone-aware"):
            service.import_pdf(
                PdfImportInput(None, "Title", ("",), None, "paper.pdf", True),
                (PDF_BYTES,),
            )
        assert not tuple((tmp_path / "library").rglob("*.pdf"))
    finally:
        engine.dispose()
