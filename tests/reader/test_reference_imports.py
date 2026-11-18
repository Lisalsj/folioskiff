from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import func, select


@pytest.fixture
def reference_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery import models as discovery_models
    from paper_reader.reader.imports import LibraryImportService
    from paper_reader.reader.models import LibraryEntryRecord
    from paper_reader.reader.storage import ContentStore

    assert discovery_models.WorkRecord.__tablename__ == "works"
    assert LibraryEntryRecord.__tablename__ == "library_entries"
    engine = create_sqlite_engine(tmp_path / "reference-imports.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    service = LibraryImportService(
        sessions=sessions,
        store=ContentStore(tmp_path / "library"),
        clock=lambda: datetime(2026, 7, 13, tzinfo=UTC),
    )
    try:
        yield service, sessions, tmp_path / "library"
    finally:
        engine.dispose()


def test_reference_import_public_contract_is_available() -> None:
    from paper_reader.reader.imports import (
        NormalizedReference,
        ReferenceImportInput,
        ReferenceImportResult,
        ReferenceKind,
        normalize_reference,
    )

    assert ReferenceKind.DOI.value == "doi"
    assert ReferenceKind.BIBTEX.value == "bibtex"
    assert ReferenceKind.URL.value == "url"
    assert ReferenceImportInput.__name__ == "ReferenceImportInput"
    assert ReferenceImportResult.__name__ == "ReferenceImportResult"
    assert NormalizedReference.__name__ == "NormalizedReference"
    assert callable(normalize_reference)


def test_reference_import_rejects_an_unknown_runtime_kind() -> None:
    from paper_reader.reader.imports import (
        ReferenceImportError,
        ReferenceImportInput,
        normalize_reference,
    )

    value = ReferenceImportInput(
        kind=cast(Any, "unknown"),
        value="value",
    )
    with pytest.raises(ReferenceImportError, match="Unsupported"):
        normalize_reference(value)


@pytest.mark.parametrize(
    "raw_doi",
    (
        "doi: 10.5555/Reader.42",
        "HTTPS://DOI.ORG/10.5555/Reader.42",
        "\u00a0https://dx.doi.org/10.5555/Reader.42\u00a0",
    ),
)
def test_doi_reference_normalizes_prefix_case_title_and_authors(raw_doi: str) -> None:
    from paper_reader.reader.imports import (
        ReferenceImportInput,
        ReferenceKind,
        normalize_reference,
    )

    normalized = normalize_reference(
        ReferenceImportInput(
            kind=ReferenceKind.DOI,
            value=raw_doi,
            title="  Reliable   Reader Agents  ",
            authors=(" Alice   Chen ", "alice chen", "Bob Kumar"),
        )
    )

    assert normalized.kind is ReferenceKind.DOI
    assert normalized.external_id == "10.5555/reader.42"
    assert normalized.normalized_value == "10.5555/reader.42"
    assert normalized.doi == "10.5555/reader.42"
    assert normalized.title == "Reliable Reader Agents"
    assert normalized.authors == ("Alice Chen", "Bob Kumar")
    assert normalized.landing_url == "https://doi.org/10.5555/reader.42"


def test_doi_reference_uses_an_explicit_nonempty_placeholder_without_metadata() -> None:
    from paper_reader.reader.imports import (
        ReferenceImportInput,
        ReferenceKind,
        normalize_reference,
    )

    normalized = normalize_reference(
        ReferenceImportInput(kind=ReferenceKind.DOI, value="10.1000/example")
    )

    assert normalized.title == "DOI 10.1000/example"
    assert normalized.authors == ()


@pytest.mark.parametrize(
    "raw_doi",
    (
        "",
        "https://example.com/not-a-doi",
        "10.12/short-prefix",
        "10.1234/contains space",
        "10.1234/",
        "10.1234/" + ("x" * 250),
    ),
)
def test_doi_reference_rejects_invalid_or_oversized_values(raw_doi: str) -> None:
    from paper_reader.reader.imports import (
        ReferenceImportError,
        ReferenceImportInput,
        ReferenceKind,
        normalize_reference,
    )

    with pytest.raises(ReferenceImportError):
        normalize_reference(ReferenceImportInput(kind=ReferenceKind.DOI, value=raw_doi))


def test_bibtex_reference_parses_nested_braces_quotes_and_multiline_authors() -> None:
    from paper_reader.reader.imports import (
        ReferenceImportInput,
        ReferenceKind,
        normalize_reference,
    )

    bibtex = r'''
      @article{reader2026,
        title = {Agent {A} for Cancer Reading},
        author = {Chen, Alice and
                  Kumar, Bob},
        doi = "10.7777/Reader.2026",
        url = {https://example.org/papers/reader.pdf#page=2},
        year = {2026}
      }
    '''

    normalized = normalize_reference(
        ReferenceImportInput(kind=ReferenceKind.BIBTEX, value=bibtex)
    )

    assert normalized.kind is ReferenceKind.BIBTEX
    assert normalized.normalized_value == "reader2026"
    assert normalized.title == "Agent A for Cancer Reading"
    assert normalized.authors == ("Chen, Alice", "Kumar, Bob")
    assert normalized.doi == "10.7777/reader.2026"
    assert normalized.landing_url == "https://example.org/papers/reader.pdf"
    assert normalized.raw_payload["entry_type"] == "article"
    assert normalized.raw_payload["citation_key"] == "reader2026"


def test_bibtex_reference_accepts_quoted_fields_and_title_override() -> None:
    from paper_reader.reader.imports import (
        ReferenceImportInput,
        ReferenceKind,
        normalize_reference,
    )

    normalized = normalize_reference(
        ReferenceImportInput(
            kind=ReferenceKind.BIBTEX,
            value='@misc{agent, title="Original Title", author="Li Ming and Ana Smith"}',
            title="Curated Title",
        )
    )

    assert normalized.title == "Curated Title"
    assert normalized.authors == ("Li Ming", "Ana Smith")


def test_bibtex_reference_accepts_parentheses_escaped_quotes_and_bare_values() -> None:
    from paper_reader.reader.imports import (
        ReferenceImportInput,
        ReferenceKind,
        normalize_reference,
    )

    normalized = normalize_reference(
        ReferenceImportInput(
            kind=ReferenceKind.BIBTEX,
            value=(
                '@misc(agent, title="A \\"Quoted\\" Reader", '
                "author={Alice Chen}, year=2026)"
            ),
        )
    )

    assert normalized.normalized_value == "agent"
    assert normalized.raw_payload["fields"]["year"] == "2026"
    assert "Quoted" in normalized.title


@pytest.mark.parametrize(
    "bibtex",
    (
        "not bibtex",
        "@article{no-fields}",
        "@article{, title={One}}",
        "@article{" + ("k" * 256) + ", title={One}}",
        "@article{missing, author={Alice}}",
        "@article{broken, title={Unclosed}",
        "@article{one, title={One}} @article{two, title={Two}}",
        "@article{missing-key, title={One},",
        "@article{invalid-field, this is not a field}",
        "@article{duplicate, title={One}, title={Two}}",
        "@article{missing-value, title=}",
        "@article{missing-comma, title={One} author={Alice}}",
        '@article{unclosed-quote, title="One}',
        pytest.param("x" * 100_001, id="oversized"),
    ),
)
def test_bibtex_reference_rejects_missing_malformed_multiple_and_oversized_entries(
    bibtex: str,
) -> None:
    from paper_reader.reader.imports import (
        ReferenceImportError,
        ReferenceImportInput,
        ReferenceKind,
        normalize_reference,
    )

    with pytest.raises(ReferenceImportError):
        normalize_reference(
            ReferenceImportInput(kind=ReferenceKind.BIBTEX, value=bibtex)
        )


def test_url_reference_normalizes_idn_default_port_fragment_and_derived_title() -> None:
    from paper_reader.reader.imports import (
        ReferenceImportInput,
        ReferenceKind,
        normalize_reference,
    )

    normalized = normalize_reference(
        ReferenceImportInput(
            kind=ReferenceKind.URL,
            value="HTTPS://例子.测试:443/papers/quiet%20reader.pdf?view=1#page=2",
        )
    )

    assert normalized.kind is ReferenceKind.URL
    assert normalized.normalized_value == (
        "https://xn--fsqu00a.xn--0zwm56d/papers/quiet%20reader.pdf?view=1"
    )
    assert normalized.landing_url == normalized.normalized_value
    assert normalized.title == "quiet reader"
    assert normalized.doi is None
    assert len(normalized.external_id) == 64


def test_url_reference_derives_host_title_and_keeps_a_public_nondefault_port() -> None:
    from paper_reader.reader.imports import (
        ReferenceImportInput,
        ReferenceKind,
        normalize_reference,
    )

    normalized = normalize_reference(
        ReferenceImportInput(
            kind=ReferenceKind.URL,
            value="https://example.org:8443/",
        )
    )

    assert normalized.normalized_value == "https://example.org:8443/"
    assert normalized.title == "example.org"


@pytest.mark.parametrize(
    "url",
    (
        "file:///C:/paper.pdf",
        "https://user:secret@example.org/paper.pdf",
        "http://localhost/paper.pdf",
        "http://research.local/paper.pdf",
        "http://127.0.0.1/paper.pdf",
        "http://10.2.3.4/paper.pdf",
        "http://169.254.1.2/paper.pdf",
        "http://[::1]/paper.pdf",
        "//example.org/paper.pdf",
        "https:///missing-host.pdf",
        "https://example.org:not-a-port/paper.pdf",
        pytest.param(
            "https://example.org/" + ("x" * 2_100),
            id="oversized",
        ),
    ),
)
def test_url_reference_rejects_unsafe_or_oversized_values(url: str) -> None:
    from paper_reader.reader.imports import (
        ReferenceImportError,
        ReferenceImportInput,
        ReferenceKind,
        normalize_reference,
    )

    with pytest.raises(ReferenceImportError):
        normalize_reference(ReferenceImportInput(kind=ReferenceKind.URL, value=url))


def test_doi_import_merges_existing_work_and_saves_without_a_document(
    reference_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.models import DocumentVersionRecord, SourceRecord
    from paper_reader.reader.imports import ReferenceImportInput, ReferenceKind
    from paper_reader.reader.models import LibraryEntryRecord

    service, sessions, library_root = reference_context
    existing = IdentityRepository(sessions).ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.00011v1",
            title="Canonical Reader Work",
            authors=("Alice Chen",),
            doi="10.5555/reader.42",
        )
    ).work

    result = service.import_reference(
        ReferenceImportInput(
            kind=ReferenceKind.DOI,
            value="https://doi.org/10.5555/READER.42",
            title="Publisher Title Variant",
        )
    )

    assert result.work_id == existing.id
    assert result.created_work is False
    assert result.kind is ReferenceKind.DOI
    assert result.normalized_value == "10.5555/reader.42"
    assert not tuple(library_root.rglob("*.pdf"))
    with sessions() as session:
        assert session.get(LibraryEntryRecord, existing.id) is not None
        source = session.scalar(
            select(SourceRecord).where(
                SourceRecord.connector_id == "manual-doi",
                SourceRecord.external_id == "10.5555/reader.42",
            )
        )
        assert source is not None
        assert source.work_id == existing.id
        assert session.scalar(select(func.count()).select_from(DocumentVersionRecord)) == 0


def test_bibtex_import_is_idempotent_and_keeps_one_library_entry(
    reference_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import DocumentVersionRecord, SourceRecord
    from paper_reader.reader.imports import ReferenceImportInput, ReferenceKind
    from paper_reader.reader.models import LibraryEntryRecord

    service, sessions, _ = reference_context
    value = ReferenceImportInput(
        kind=ReferenceKind.BIBTEX,
        value=(
            "@article{quiet2026, title={Quiet Paper Reading}, "
            "author={Alice Chen and Bob Kumar}}"
        ),
    )

    first = service.import_reference(value)
    repeated = service.import_reference(value)

    assert first.work_id == repeated.work_id
    assert first.created_work is True
    assert repeated.created_work is False
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(LibraryEntryRecord)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(SourceRecord)
                .where(SourceRecord.connector_id == "manual-bibtex")
            )
            == 1
        )
        assert session.scalar(select(func.count()).select_from(DocumentVersionRecord)) == 0


def test_url_import_records_public_landing_url_without_fetching_full_text(
    reference_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import DocumentVersionRecord, SourceRecord
    from paper_reader.reader.imports import ReferenceImportInput, ReferenceKind

    service, sessions, library_root = reference_context

    result = service.import_reference(
        ReferenceImportInput(
            kind=ReferenceKind.URL,
            value="https://example.org/papers/agent-reader.pdf#abstract",
            rights_confirmed=False,
        )
    )

    assert result.created_work is True
    assert result.normalized_value == "https://example.org/papers/agent-reader.pdf"
    assert not tuple(library_root.rglob("*.pdf"))
    with sessions() as session:
        source = session.scalar(
            select(SourceRecord).where(SourceRecord.connector_id == "manual-url")
        )
        assert source is not None
        assert source.work_id == result.work_id
        assert source.landing_url == result.normalized_value
        assert session.scalar(select(func.count()).select_from(DocumentVersionRecord)) == 0
