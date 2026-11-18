from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def library_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery import models as discovery_models
    from paper_reader.migrations import upgrade_database
    from paper_reader.reader.imports import LibraryImportService
    from paper_reader.reader.models import LibraryEntryRecord
    from paper_reader.reader.repository import LibraryRepository
    from paper_reader.reader.storage import ContentStore
    from paper_reader.recommendation.lexical import LexicalIndex

    assert discovery_models.WorkRecord.__tablename__ == "works"
    assert LibraryEntryRecord.__tablename__ == "library_entries"
    database = tmp_path / "library-read-model.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    observed = [datetime(2026, 7, 13, 8, 0, tzinfo=UTC)]
    service = LibraryImportService(
        sessions=sessions,
        store=ContentStore(tmp_path / "library"),
        work_index=LexicalIndex(sessions),
        clock=lambda: observed[0],
    )
    repository = LibraryRepository(sessions)
    try:
        yield repository, service, sessions, observed, tmp_path / "library"
    finally:
        engine.dispose()


def _reference(kind: str, value: str, *, title: str, authors=()):  # type: ignore[no-untyped-def]
    from paper_reader.reader.imports import ReferenceImportInput, ReferenceKind

    return ReferenceImportInput(
        kind=ReferenceKind(kind),
        value=value,
        title=title,
        authors=tuple(authors),
    )


def test_library_repository_public_contract_is_available() -> None:
    from paper_reader.reader.repository import (
        LibraryDocument,
        LibraryPage,
        LibraryRepository,
        LibraryWork,
    )

    assert LibraryRepository.__name__ == "LibraryRepository"
    assert LibraryPage.__name__ == "LibraryPage"
    assert LibraryWork.__name__ == "LibraryWork"
    assert LibraryDocument.__name__ == "LibraryDocument"


def test_library_repository_accepts_a_session_factory(tmp_path: Path) -> None:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery import models as discovery_models
    from paper_reader.reader.models import LibraryEntryRecord
    from paper_reader.reader.repository import LibraryRepository

    assert discovery_models.WorkRecord.__tablename__ == "works"
    assert LibraryEntryRecord.__tablename__ == "library_entries"
    engine = create_sqlite_engine(tmp_path / "library-repository.sqlite3")
    Base.metadata.create_all(engine)
    try:
        repository = LibraryRepository(create_session_factory(engine))
    finally:
        engine.dispose()

    assert repository is not None


def test_library_repository_returns_an_empty_page_and_validates_pagination(
    library_context,
) -> None:  # type: ignore[no-untyped-def]
    repository, _, _, _, _ = library_context

    page = repository.list_entries(query=None, limit=20, offset=0)

    assert page.items == ()
    assert page.total == 0
    assert page.limit == 20
    assert page.offset == 0
    with pytest.raises(ValueError, match="limit"):
        repository.list_entries(query=None, limit=0, offset=0)
    with pytest.raises(ValueError, match="limit"):
        repository.list_entries(query=None, limit=101, offset=0)
    with pytest.raises(ValueError, match="offset"):
        repository.list_entries(query=None, limit=20, offset=-1)


def test_library_repository_paginates_searches_and_composes_independent_state(
    library_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.preferences import (
        AnalysisState,
        LibraryFeedback,
        LibraryStateInput,
        PreferenceRepository,
    )

    repository, service, sessions, observed, _ = library_context
    diffusion = service.import_reference(
        _reference(
            "doi",
            "10.1000/diffusion",
            title="Diffusion Models for Reading",
            authors=("Alice Chen",),
        )
    )
    observed[0] += timedelta(minutes=1)
    agent = service.import_reference(
        _reference(
            "bibtex",
            "@article{agent, title={Ignored by override}, author={Bob Kumar}}",
            title="Research Agents for Paper Triage",
        )
    )
    observed[0] += timedelta(minutes=1)
    cancer = service.import_reference(
        _reference(
            "url",
            "https://example.org/cancer-reader.pdf",
            title="Cancer Evidence Reader",
            authors=("Carla Smith",),
        )
    )
    PreferenceRepository(sessions).set_library_state(
        agent.work_id,
        LibraryStateInput(
            favorite=True,
            feedback=LibraryFeedback.LIKE,
            feedback_reasons=("method_match",),
            pinned=True,
            reading_progress=42,
            analysis_state=AnalysisState.AVAILABLE,
        ),
    )

    page = repository.list_entries(query=None, limit=2, offset=0)
    second_page = repository.list_entries(query=None, limit=2, offset=2)
    author_match = repository.list_entries(query="ALICE", limit=20, offset=0)
    title_match = repository.list_entries(query="agent", limit=20, offset=0)
    doi_match = repository.list_entries(query="10.1000/DIFFUSION", limit=20, offset=0)
    favorite_match = repository.list_entries(
        query=None,
        limit=20,
        offset=0,
        favorite=True,
    )
    unread_match = repository.list_entries(
        query=None,
        limit=20,
        offset=0,
        unread=True,
    )

    assert page.total == 3
    assert [item.id for item in page.items] == [cancer.work_id, agent.work_id]
    assert [item.id for item in second_page.items] == [diffusion.work_id]
    assert [item.id for item in author_match.items] == [diffusion.work_id]
    assert [item.id for item in title_match.items] == [agent.work_id]
    assert [item.id for item in doi_match.items] == [diffusion.work_id]
    assert [item.id for item in favorite_match.items] == [agent.work_id]
    assert {item.id for item in unread_match.items} == {
        cancer.work_id,
        agent.work_id,
        diffusion.work_id,
    }
    agent_item = title_match.items[0]
    assert agent_item.favorite is True
    assert agent_item.feedback == "like"
    assert agent_item.pinned is True
    assert agent_item.reading_progress == 42
    assert agent_item.analysis_state == "available"
    assert agent_item.document_count == 0
    assert agent_item.representative_document is None


def test_library_repository_exposes_documents_without_local_paths_and_selects_representative(
    library_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import IdentityRepository
    from paper_reader.reader.imports import PdfImportInput

    repository, service, sessions, observed, _ = library_context
    imported = service.import_reference(
        _reference(
            "doi",
            "10.1000/documents",
            title="Document Versions for Readers",
        )
    )
    manual_pdf = service.import_pdf(
        PdfImportInput(
            work_id=imported.work_id,
            title=None,
            authors=(),
            doi=None,
            original_filename="reader.pdf",
            rights_confirmed=True,
        ),
        (b"%PDF-1.7\nreader\n%%EOF\n",),
    )
    restricted = IdentityRepository(sessions).add_document_version(
        work_id=imported.work_id,
        content_sha256="f" * 64,
        access_status="restricted",
        completeness=100,
        source_updated_at=observed[0] + timedelta(days=1),
    )

    documents = repository.list_documents(imported.work_id)
    work = repository.get_work(imported.work_id)

    assert {document.id for document in documents} == {
        manual_pdf.document_version_id,
        restricted.id,
    }
    representative = next(document for document in documents if document.is_representative)
    assert representative.id == manual_pdf.document_version_id
    assert representative.available is True
    assert work.document_count == 2
    assert work.representative_document == representative
    for document in documents:
        assert not hasattr(document, "relative_path")


def test_library_repository_resolves_merged_aliases_and_rejects_unsaved_works(
    library_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.models import WorkRecord

    repository, service, sessions, _, _ = library_context
    target = service.import_reference(_reference("doi", "10.1000/target", title="Canonical Target"))
    alias = (
        IdentityRepository(sessions)
        .ingest(
            SourcePaper(
                connector_id="arxiv",
                external_id="2607.alias",
                title="Distinct Alias Before Merge",
                authors=("Alias Author",),
            )
        )
        .work
    )
    unsaved = (
        IdentityRepository(sessions)
        .ingest(
            SourcePaper(
                connector_id="arxiv",
                external_id="2607.unsaved",
                title="Unsaved Work",
                authors=("No Library",),
            )
        )
        .work
    )
    with sessions() as session:
        alias_record = session.get(WorkRecord, alias.id)
        assert alias_record is not None
        alias_record.status = "merged"
        alias_record.merged_into_work_id = target.work_id
        session.commit()

    resolved = repository.get_work(alias.id)

    assert resolved.id == target.work_id
    with pytest.raises(KeyError):
        repository.get_work(unsaved.id)
    with pytest.raises(KeyError):
        repository.list_documents(unsaved.id)
