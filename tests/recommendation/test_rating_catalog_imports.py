from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DatabaseError


@pytest.fixture
def catalog_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.recommendation.rating_catalogs import RatingCatalogRepository

    engine = create_sqlite_engine(tmp_path / "rating-catalogs.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    try:
        yield sessions, RatingCatalogRepository(sessions)
    finally:
        engine.dispose()


def test_csv_catalog_import_is_rights_gated_content_idempotent_and_source_separated(
    catalog_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.rating_catalogs import RatingCatalogKind

    _, repository = catalog_context
    jcr_bytes = b"venue,rating,aliases,issn\nJournal One,Q1,J One|JO,1234-5678\n"
    cas_bytes = "venue,rating\nJournal One,2区\n".encode()

    with pytest.raises(ValueError, match="rights"):
        repository.import_bytes(
            kind=RatingCatalogKind.JCR,
            catalog_name="JCR local export",
            catalog_version="2025",
            source_note="Licensed institutional export",
            filename="jcr.csv",
            content=jcr_bytes,
            rights_confirmed=False,
        )

    first = repository.import_bytes(
        kind=RatingCatalogKind.JCR,
        catalog_name="JCR local export",
        catalog_version="2025",
        source_note="Licensed institutional export",
        filename="jcr.csv",
        content=jcr_bytes,
        rights_confirmed=True,
    )
    duplicate = repository.import_bytes(
        kind=RatingCatalogKind.JCR,
        catalog_name="Ignored duplicate label",
        catalog_version="duplicate",
        source_note="Same bytes",
        filename="same.csv",
        content=jcr_bytes,
        rights_confirmed=True,
    )
    cas = repository.import_bytes(
        kind=RatingCatalogKind.CAS,
        catalog_name="CAS 2025 final local export",
        catalog_version="2025-final",
        source_note="User-provided licensed file",
        filename="cas.csv",
        content=cas_bytes,
        rights_confirmed=True,
    )

    assert duplicate.id == first.id
    assert first.kind is RatingCatalogKind.JCR
    assert first.file_sha256 != cas.file_sha256
    assert repository.active(RatingCatalogKind.JCR) == first
    assert repository.active(RatingCatalogKind.CAS) == cas
    assert repository.find_active(RatingCatalogKind.JCR, "JO").score == 100
    assert repository.find_active(RatingCatalogKind.CAS, "Journal One").score == 75
    assert {catalog.kind for catalog in repository.list_catalogs()} == {
        RatingCatalogKind.JCR,
        RatingCatalogKind.CAS,
    }


def test_xlsx_catalog_import_supports_explicit_scores_and_rejects_formulas(
    catalog_context,
) -> None:  # type: ignore[no-untyped-def]
    from openpyxl import Workbook

    from paper_reader.recommendation.rating_catalogs import RatingCatalogKind

    _, repository = catalog_context
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["venue", "rating", "score", "aliases"])
    sheet.append(["Frontier Journal", "gold", 92, "FJ;Frontier J."])
    stream = BytesIO()
    workbook.save(stream)

    catalog = repository.import_bytes(
        kind=RatingCatalogKind.EMERGING,
        catalog_name="Emerging journals",
        catalog_version="2026",
        source_note="User-curated source",
        filename="emerging.xlsx",
        content=stream.getvalue(),
        rights_confirmed=True,
    )

    match = repository.find_active(RatingCatalogKind.EMERGING, "Frontier J.")
    assert catalog.row_count == 1
    assert match is not None
    assert (match.rating_label, match.score, match.row_number) == ("gold", 92, 2)

    malicious = Workbook()
    malicious.active.append(["venue", "rating", "score"])
    malicious.active.append(["=HYPERLINK(\"https://example.test\",\"Venue\")", "A", 90])
    malicious_stream = BytesIO()
    malicious.save(malicious_stream)
    with pytest.raises(ValueError, match="formula"):
        repository.import_bytes(
            kind=RatingCatalogKind.EMERGING,
            catalog_name="Unsafe",
            catalog_version="1",
            source_note="fixture",
            filename="unsafe.xlsx",
            content=malicious_stream.getvalue(),
            rights_confirmed=True,
        )


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"name,tier\nJournal,Q1\n", "venue"),
        (b"venue,rating\nJournal,Q1\nJournal,Q2\n", "duplicate"),
        (b"venue,rating\nJournal,unknown\n", "score"),
        (b"venue,rating,score\nJournal,Q1,101\n", "between 0 and 100"),
    ],
)
def test_catalog_import_rejects_ambiguous_or_invalid_rows(
    catalog_context,
    content: bytes,
    message: str,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.rating_catalogs import RatingCatalogKind

    _, repository = catalog_context
    with pytest.raises(ValueError, match=message):
        repository.import_bytes(
            kind=RatingCatalogKind.JCR,
            catalog_name="Invalid",
            catalog_version="1",
            source_note="fixture",
            filename="invalid.csv",
            content=content,
            rights_confirmed=True,
        )


def test_imported_catalog_contents_are_immutable_but_active_selection_can_change(
    catalog_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import RatingCatalogRecord, VenueRatingRecord
    from paper_reader.recommendation.rating_catalogs import RatingCatalogKind

    sessions, repository = catalog_context
    catalog = repository.import_bytes(
        kind=RatingCatalogKind.JCR,
        catalog_name="JCR",
        catalog_version="2025",
        source_note="licensed",
        filename="jcr.csv",
        content=b"venue,rating\nJournal One,Q1\n",
        rights_confirmed=True,
    )

    with sessions() as session, pytest.raises(DatabaseError, match="immutable"):
        session.execute(
            update(RatingCatalogRecord)
            .where(RatingCatalogRecord.id == catalog.id)
            .values(catalog_version="changed")
        )
        session.commit()
    with sessions() as session, pytest.raises(DatabaseError, match="immutable"):
        session.execute(
            delete(VenueRatingRecord).where(VenueRatingRecord.catalog_id == catalog.id)
        )
        session.commit()

    repository.deactivate(RatingCatalogKind.JCR)
    assert repository.active(RatingCatalogKind.JCR) is None
    assert len(repository.list_catalogs()) == 1
