from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from sqlalchemy import select

from tests.reader.pdf_fixtures import (
    write_encrypted_pdf,
    write_image_only_pdf,
    write_malformed_pdf,
    write_mixed_pdf,
    write_text_pdf,
    write_truncated_pdf,
)

NOW = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)


def assert_extraction_error(callable_: Any, code: str) -> None:
    from paper_reader.reader.extraction import ExtractionError

    with pytest.raises(ExtractionError) as captured:
        callable_()
    assert captured.value.code == code


def test_extraction_public_contract_is_available() -> None:
    from paper_reader.reader.extraction import (
        DocumentExtractionService,
        ExtractionError,
        ExtractionResult,
        PageText,
        PdfTextExtractor,
        TextBlock,
    )

    assert TextBlock.__name__ == "TextBlock"
    assert PageText.__name__ == "PageText"
    assert ExtractionResult.__name__ == "ExtractionResult"
    assert issubclass(ExtractionError, RuntimeError)
    assert PdfTextExtractor.__name__ == "PdfTextExtractor"
    assert DocumentExtractionService.__name__ == "DocumentExtractionService"


def test_extracts_stable_page_text_unicode_and_pdf_point_coordinates(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.extraction import PdfTextExtractor

    document = write_text_pdf(
        tmp_path / "native.pdf",
        (
            "First page says “quoted evidence”.",
            "扩散模型 diffusion 第二页",
        ),
    )

    result = PdfTextExtractor().extract(document, cancelled=lambda: False)

    assert result.page_count == 2
    assert tuple(page.page_number for page in result.pages) == (1, 2)
    first_page_line = " ".join(result.pages[0].text.splitlines())
    assert "quoted evidence" in first_page_line
    assert "“" in result.pages[0].text and "”" in result.pages[0].text
    assert "扩散模型" in result.pages[1].text
    assert result.native_character_count == sum(
        page.native_character_count for page in result.pages
    )
    assert result.scanned_page_count == 0
    assert result.extractor_version.startswith("pymupdf-")
    for page in result.pages:
        assert page.page_width == pytest.approx(420)
        assert page.page_height == pytest.approx(595)
        assert page.native_character_count > 0
        assert page.likely_scanned is False
        assert page.ocr_state == "not_needed"
        assert page.blocks
        assert all(0 <= block.x0 < block.x1 <= page.page_width for block in page.blocks)
        assert all(0 <= block.y0 < block.y1 <= page.page_height for block in page.blocks)


def test_text_normalization_is_conservative_and_never_invokes_ocr(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.extraction import PdfTextExtractor

    source = tmp_path / "fake.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")

    class Rect:
        width = 100.0
        height = 200.0

    class Page:
        rect = Rect()
        ocr_calls = 0

        @staticmethod
        def get_text(option: str, *, sort: bool) -> list[tuple[object, ...]]:
            assert option == "blocks"
            assert sort is True
            return [(1.0, 2.0, 90.0, 20.0, "Cafe\u0301 “quoted”\r\n", 7, 0)]

        @staticmethod
        def get_image_info(*, hashes: bool, xrefs: bool) -> list[dict[str, object]]:
            assert hashes is False
            assert xrefs is False
            return []

        @classmethod
        def get_textpage_ocr(cls, *_: object, **__: object) -> None:
            cls.ocr_calls += 1
            raise AssertionError("native extraction must never invoke OCR")

    class Document:
        needs_pass = False
        page_count = 1

        def __enter__(self) -> Document:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        @staticmethod
        def load_page(index: int) -> Page:
            assert index == 0
            return Page()

    class Module:
        __version__ = "fixture"

        @staticmethod
        def open(_: Path) -> Document:
            return Document()

    result = PdfTextExtractor(module_loader=lambda: Module()).extract(
        source,
        cancelled=lambda: False,
    )

    assert result.pages[0].text == "Café “quoted”\n"
    assert result.pages[0].blocks[0].text == "Café “quoted”\n"
    assert result.pages[0].blocks[0].block_number == 7
    assert Page.ocr_calls == 0


def test_image_only_pages_are_marked_for_later_ocr_but_blank_pages_are_not(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.extraction import PdfTextExtractor

    image_only = write_image_only_pdf(tmp_path / "image-only.pdf")
    blank = write_text_pdf(tmp_path / "blank.pdf", ("",))

    image_result = PdfTextExtractor().extract(image_only, cancelled=lambda: False)
    blank_result = PdfTextExtractor().extract(blank, cancelled=lambda: False)

    image_page = image_result.pages[0]
    assert image_page.text == ""
    assert image_page.native_character_count == 0
    assert image_page.likely_scanned is True
    assert image_page.ocr_state == "pending"
    assert image_result.scanned_page_count == 1
    assert blank_result.pages[0].likely_scanned is False
    assert blank_result.pages[0].ocr_state == "not_needed"


def test_mixed_native_and_scanned_pages_keep_stable_classification(tmp_path: Path) -> None:
    from paper_reader.reader.extraction import PdfTextExtractor

    result = PdfTextExtractor().extract(
        write_mixed_pdf(tmp_path / "mixed.pdf"),
        cancelled=lambda: False,
    )

    assert tuple(page.page_number for page in result.pages) == (1, 2)
    assert result.pages[0].likely_scanned is False
    assert result.pages[1].likely_scanned is True
    assert result.scanned_page_count == 1


def test_missing_encrypted_damaged_and_oversized_documents_have_stable_codes(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.extraction import PdfTextExtractor

    extractor = PdfTextExtractor(max_pages=2)
    assert_extraction_error(
        lambda: extractor.extract(tmp_path / "missing.pdf", cancelled=lambda: False),
        "missing_file",
    )
    encrypted = write_encrypted_pdf(tmp_path / "encrypted.pdf")
    assert_extraction_error(
        lambda: extractor.extract(encrypted, cancelled=lambda: False),
        "encrypted",
    )
    for path in (
        write_malformed_pdf(tmp_path / "malformed.pdf"),
        write_truncated_pdf(tmp_path / "truncated.pdf"),
    ):
        assert_extraction_error(
            lambda path=path: extractor.extract(path, cancelled=lambda: False),
            "damaged",
        )
    oversized = write_text_pdf(tmp_path / "oversized.pdf", ("one", "two", "three"))
    assert_extraction_error(
        lambda: extractor.extract(oversized, cancelled=lambda: False),
        "too_many_pages",
    )


def test_extractor_unavailable_and_cancellation_after_page_one_have_stable_codes(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.extraction import PdfTextExtractor

    available_file = tmp_path / "available.pdf"
    available_file.write_bytes(b"%PDF-1.7\nfixture")

    def unavailable() -> object:
        raise OSError("native runtime failed to load")

    assert_extraction_error(
        lambda: PdfTextExtractor(module_loader=unavailable).extract(
            available_file,
            cancelled=lambda: False,
        ),
        "extractor_unavailable",
    )

    stop = False

    def progress(completed: int, total: int) -> None:
        nonlocal stop
        assert total == 2
        stop = completed == 1

    assert_extraction_error(
        lambda: PdfTextExtractor().extract(
            write_text_pdf(tmp_path / "cancel.pdf", ("one", "two")),
            cancelled=lambda: stop,
            progress=progress,
        ),
        "cancelled",
    )


def test_extractor_validates_configuration_prestart_cancel_and_callback_failures(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.extraction import PdfTextExtractor

    for options in (
        {"max_pages": 0},
        {"max_page_dimension_points": 0},
        {"max_page_area_points": 0},
        {"max_blocks_per_page": 0},
        {"max_blocks_total": 0},
        {"max_images_per_page": 0},
        {"max_characters_per_page": 0},
        {"max_characters_total": 0},
        {"max_seconds": 0},
    ):
        name = next(iter(options))
        with pytest.raises(ValueError, match=name):
            PdfTextExtractor(**options)
    source = write_text_pdf(tmp_path / "callbacks.pdf", ("callback",))
    assert_extraction_error(
        lambda: PdfTextExtractor().extract(source, cancelled=lambda: True),
        "cancelled",
    )

    def broken_progress(_: int, __: int) -> None:
        raise RuntimeError("progress callback failed")

    with pytest.raises(RuntimeError, match="progress callback failed"):
        PdfTextExtractor().extract(
            source,
            cancelled=lambda: False,
            progress=broken_progress,
        )

    cancellation_checks = 0

    def broken_cancellation() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        if cancellation_checks >= 2:
            raise RuntimeError("cancellation callback failed")
        return False

    with pytest.raises(RuntimeError, match="cancellation callback failed"):
        PdfTextExtractor().extract(source, cancelled=broken_cancellation)


@pytest.mark.parametrize(
    ("options", "width", "height", "blocks", "images", "expected_code"),
    (
        ({"max_page_dimension_points": 100}, 101, 50, [], [], "page_too_large"),
        ({"max_page_area_points": 100}, 11, 10, [], [], "page_too_large"),
        (
            {"max_blocks_per_page": 1},
            100,
            100,
            [(0, 0, 10, 10, "one", 0, 0), (0, 10, 10, 20, "two", 1, 0)],
            [],
            "too_many_blocks",
        ),
        (
            {"max_images_per_page": 1},
            100,
            100,
            [],
            [{"bbox": (0, 0, 10, 10)}, {"bbox": (10, 10, 20, 20)}],
            "too_many_images",
        ),
        (
            {"max_characters_per_page": 4},
            100,
            100,
            [(0, 0, 10, 10, "12345", 0, 0)],
            [],
            "too_much_text",
        ),
    ),
)
def test_extractor_enforces_page_resource_limits_before_publishing(
    tmp_path: Path,
    options: dict[str, int],
    width: float,
    height: float,
    blocks: list[tuple[object, ...]],
    images: list[dict[str, object]],
    expected_code: str,
) -> None:
    from paper_reader.reader.extraction import ExtractionError, PdfTextExtractor

    source = tmp_path / f"{expected_code}.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")

    class Rect:
        pass

    Rect.width = width
    Rect.height = height

    class Page:
        rect = Rect()

        @staticmethod
        def get_text(_: str, *, sort: bool) -> list[tuple[object, ...]]:
            assert sort is True
            return blocks

        @staticmethod
        def get_image_info(*, hashes: bool, xrefs: bool) -> list[dict[str, object]]:
            assert hashes is False and xrefs is False
            return images

    class Document:
        needs_pass = False
        is_repaired = False
        page_count = 1

        def __enter__(self) -> Document:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        @staticmethod
        def load_page(_: int) -> Page:
            return Page()

    class Module:
        @staticmethod
        def open(_: Path) -> Document:
            return Document()

    with pytest.raises(ExtractionError) as captured:
        PdfTextExtractor(
            module_loader=lambda: Module(),
            **options,
        ).extract(source, cancelled=lambda: False)
    assert captured.value.code == expected_code
    assert str(source) not in str(captured.value)
    assert "12345" not in str(captured.value)


def test_extractor_enforces_document_text_and_elapsed_time_limits(tmp_path: Path) -> None:
    from paper_reader.reader.extraction import ExtractionError, PdfTextExtractor

    source = tmp_path / "bounded-document.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")

    class Rect:
        width = 100.0
        height = 100.0

    class Page:
        rect = Rect()

        @staticmethod
        def get_text(_: str, *, sort: bool) -> list[tuple[object, ...]]:
            assert sort is True
            return [(0, 0, 10, 10, "four", 0, 0)]

        @staticmethod
        def get_image_info(*, hashes: bool, xrefs: bool) -> list[dict[str, object]]:
            return []

    class Document:
        needs_pass = False
        is_repaired = False
        page_count = 2

        def __enter__(self) -> Document:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        @staticmethod
        def load_page(_: int) -> Page:
            return Page()

    class Module:
        @staticmethod
        def open(_: Path) -> Document:
            return Document()

    with pytest.raises(ExtractionError) as captured:
        PdfTextExtractor(
            module_loader=lambda: Module(),
            max_characters_per_page=10,
            max_characters_total=7,
        ).extract(source, cancelled=lambda: False)
    assert captured.value.code == "too_much_text"

    with pytest.raises(ExtractionError) as captured:
        PdfTextExtractor(
            module_loader=lambda: Module(),
            max_blocks_per_page=2,
            max_blocks_total=1,
        ).extract(source, cancelled=lambda: False)
    assert captured.value.code == "too_many_blocks"

    ticks = iter((0.0, 0.0, 2.0))
    with pytest.raises(ExtractionError) as captured:
        PdfTextExtractor(
            module_loader=lambda: Module(),
            max_seconds=1.0,
            clock=lambda: next(ticks, 2.0),
        ).extract(source, cancelled=lambda: False)
    assert captured.value.code == "time_limit"


def test_extractor_skips_image_inventory_when_native_text_is_sufficient(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.extraction import PdfTextExtractor

    source = tmp_path / "native-text.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")

    class Rect:
        width = 100.0
        height = 100.0

    class Page:
        rect = Rect()

        @staticmethod
        def get_text(_: str, *, sort: bool) -> list[tuple[object, ...]]:
            assert sort is True
            return [(0, 0, 90, 20, "native text " * 4, 0, 0)]

        @staticmethod
        def get_image_info(*, hashes: bool, xrefs: bool) -> list[dict[str, object]]:
            raise AssertionError("text pages must not enumerate image objects")

    class Document:
        needs_pass = False
        is_repaired = False
        page_count = 1

        def __enter__(self) -> Document:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        @staticmethod
        def load_page(_: int) -> Page:
            return Page()

    class Module:
        __version__ = "fixture"

        @staticmethod
        def open(_: Path) -> Document:
            return Document()

    result = PdfTextExtractor(module_loader=lambda: Module()).extract(
        source,
        cancelled=lambda: False,
    )

    assert result.pages[0].likely_scanned is False
    assert result.pages[0].native_character_count >= 32


def test_extractor_classifies_disappearing_repaired_empty_and_broken_pages(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.extraction import PdfTextExtractor

    disappearing = tmp_path / "disappearing.pdf"
    disappearing.write_bytes(b"%PDF-1.7\nfixture")

    class DisappearingModule:
        @staticmethod
        def open(path: Path) -> None:
            path.unlink()
            raise RuntimeError("file disappeared")

    assert_extraction_error(
        lambda: PdfTextExtractor(module_loader=lambda: DisappearingModule()).extract(
            disappearing,
            cancelled=lambda: False,
        ),
        "missing_file",
    )

    source = tmp_path / "fake-structures.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")

    class Document:
        needs_pass = False

        def __init__(
            self,
            *,
            page_count: int,
            repaired: bool = False,
            page: object | None = None,
            load_error: bool = False,
            exit_error: bool = False,
        ) -> None:
            self.page_count = page_count
            self.is_repaired = repaired
            self.page = page
            self.load_error = load_error
            self.exit_error = exit_error

        def __enter__(self) -> Document:
            return self

        def __exit__(self, *_: object) -> None:
            if self.exit_error:
                raise RuntimeError("close failed")

        def load_page(self, _: int) -> object:
            if self.load_error:
                raise RuntimeError("load failed")
            assert self.page is not None
            return self.page

    class Module:
        def __init__(self, document: Document) -> None:
            self.document = document

        def open(self, _: Path) -> Document:
            return self.document

    class Rect:
        width = 0.0
        height = 100.0

    class InvalidDimensionPage:
        rect = Rect()

    for document in (
        Document(page_count=1, repaired=True),
        Document(page_count=0),
        Document(page_count=1, load_error=True),
        Document(page_count=1, page=InvalidDimensionPage()),
        Document(page_count=1, page=InvalidDimensionPage(), exit_error=True),
    ):
        assert_extraction_error(
            lambda document=document: PdfTextExtractor(
                module_loader=lambda: Module(document)
            ).extract(source, cancelled=lambda: False),
            "damaged",
        )


def test_extractor_ignores_nontext_empty_and_out_of_page_blocks(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.extraction import PdfTextExtractor

    source = tmp_path / "ignored-blocks.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")

    class Rect:
        width = 100.0
        height = 100.0

    class Page:
        rect = Rect()

        @staticmethod
        def get_text(_: str, *, sort: bool) -> list[tuple[object, ...]]:
            assert sort is True
            return [
                (0, 0, 100, 100, "image metadata", 0, 1),
                (0, 0, 10, 10, "", 1, 0),
                (200, 200, 300, 300, "outside", 2, 0),
            ]

        @staticmethod
        def get_image_info(*, hashes: bool, xrefs: bool) -> list[dict[str, object]]:
            assert hashes is False and xrefs is False
            return [{"bbox": (200, 200, 300, 300)}]

    class Document:
        needs_pass = False
        is_repaired = False
        page_count = 1

        def __enter__(self) -> Document:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        @staticmethod
        def load_page(_: int) -> Page:
            return Page()

    class Module:
        @staticmethod
        def open(_: Path) -> Document:
            return Document()

    result = PdfTextExtractor(module_loader=lambda: Module()).extract(
        source,
        cancelled=lambda: False,
    )
    assert result.extractor_version == "pymupdf-unknown/native-v1"
    assert result.pages[0].text == ""
    assert result.pages[0].blocks == ()
    assert result.pages[0].likely_scanned is False


@pytest.mark.parametrize(
    "raw_block",
    (
        (0, 1),
        (float("nan"), 0, 10, 10, "invalid", 0, 0),
        ("not-a-number", 0, 10, 10, "invalid", 0, 0),
    ),
)
def test_extractor_rejects_malformed_text_blocks(
    tmp_path: Path,
    raw_block: tuple[object, ...],
) -> None:
    from paper_reader.reader.extraction import PdfTextExtractor

    source = tmp_path / "bad-block.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")

    class Rect:
        width = 100.0
        height = 100.0

    class Page:
        rect = Rect()

        @staticmethod
        def get_text(_: str, *, sort: bool) -> list[tuple[object, ...]]:
            assert sort is True
            return [raw_block]

        @staticmethod
        def get_image_info(*, hashes: bool, xrefs: bool) -> list[dict[str, object]]:
            return []

    class Document:
        needs_pass = False
        is_repaired = False
        page_count = 1

        def __enter__(self) -> Document:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        @staticmethod
        def load_page(_: int) -> Page:
            return Page()

    class Module:
        @staticmethod
        def open(_: Path) -> Document:
            return Document()

    assert_extraction_error(
        lambda: PdfTextExtractor(module_loader=lambda: Module()).extract(
            source,
            cancelled=lambda: False,
        ),
        "damaged",
    )


@dataclass(slots=True)
class ExtractionContext:
    sessions: Any
    store: Any
    engine: Any


@pytest.fixture
def extraction_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery import models as discovery_models
    from paper_reader.jobs import models as job_models
    from paper_reader.reader import models as reader_models
    from paper_reader.reader.storage import ContentStore

    _ = (
        discovery_models.WorkRecord,
        job_models.JobRecord,
        reader_models.DocumentPageTextRecord,
    )
    engine = create_sqlite_engine(tmp_path / "extraction.sqlite3")
    Base.metadata.create_all(engine)
    context = ExtractionContext(
        sessions=create_session_factory(engine),
        store=ContentStore(tmp_path / "library"),
        engine=engine,
    )
    try:
        yield context
    finally:
        engine.dispose()


def add_document(
    context: ExtractionContext,
    payload: bytes,
    *,
    label: str,
) -> tuple[str, str, str]:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.models import LibraryEntryRecord

    stored = context.store.ingest(
        (payload,),
        media_type="application/pdf",
        max_bytes=10 * 1024 * 1024,
    )
    ingested = IdentityRepository(context.sessions).ingest(
        SourcePaper(
            connector_id="fixture",
            external_id=f"extraction-{label}",
            title=f"Extraction {label}",
            authors=("Alice",),
        )
    )
    with context.sessions() as session:
        document = DocumentVersionRecord(
            work_id=ingested.work.id,
            source_record_id=ingested.source_record.id,
            content_sha256=stored.content_sha256,
            access_status="user_provided",
            media_type="application/pdf",
            completeness=100,
            source_updated_at=NOW,
            extraction_status="pending",
            relative_path=stored.relative_path,
            byte_size=stored.byte_size,
            original_filename=f"{label}.pdf",
            acquisition_kind="manual_import",
            last_accessed_at=NOW,
            created_at=NOW,
        )
        session.add(document)
        session.add(
            LibraryEntryRecord(
                work_id=ingested.work.id,
                added_via="pdf",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()
        return ingested.work.id, document.id, stored.content_sha256


def one_page_result(text: str = "fixture evidence\n"):  # type: ignore[no-untyped-def]
    from paper_reader.reader.extraction import ExtractionResult, PageText, TextBlock

    block = TextBlock(
        x0=1,
        y0=2,
        x1=90,
        y1=20,
        text=text,
        block_number=0,
    )
    page = PageText(
        page_number=1,
        page_width=100,
        page_height=200,
        text=text,
        blocks=(block,),
        native_character_count=sum(not character.isspace() for character in text),
        likely_scanned=False,
        ocr_state="not_needed",
    )
    return ExtractionResult(
        pages=(page,),
        page_count=1,
        native_character_count=page.native_character_count,
        scanned_page_count=0,
        extractor_version="fixture-v1",
    )


@pytest.mark.asyncio
async def test_extraction_job_atomically_persists_pages_and_is_repeatable(
    extraction_context: ExtractionContext,
    tmp_path: Path,
) -> None:
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.extraction import (
        EXTRACTION_JOB_KIND,
        DocumentExtractionService,
        PdfTextExtractor,
    )
    from paper_reader.reader.models import DocumentPageTextRecord

    source = write_text_pdf(tmp_path / "service.pdf", ("first", "second"))
    work_id, document_id, _ = add_document(
        extraction_context,
        source.read_bytes(),
        label="service",
    )
    jobs = JobRepository(extraction_context.sessions)
    service = DocumentExtractionService(
        sessions=extraction_context.sessions,
        jobs=jobs,
        store=extraction_context.store,
        extractor=PdfTextExtractor(),
    )

    first = service.create_job(
        work_id=work_id,
        document_version_id=document_id,
        idempotency_key="extract-service",
    )
    duplicate = service.create_job(
        work_id=work_id,
        document_version_id=document_id,
        idempotency_key="extract-service",
    )
    assert duplicate.id == first.id
    assert first.kind == EXTRACTION_JOB_KIND
    await service.execute(first.id)
    first_finished = jobs.get(first.id)
    assert first_finished is not None
    assert first_finished.status == "succeeded"
    assert first_finished.result == {
        "work_id": work_id,
        "document_version_id": document_id,
        "page_count": 2,
        "native_character_count": 11,
        "scanned_page_count": 0,
        "extractor_version": first_finished.result["extractor_version"],
    }

    second = service.create_job(
        work_id=work_id,
        document_version_id=document_id,
        idempotency_key="extract-service-second",
    )
    await service.execute(second.id)
    with extraction_context.sessions() as session:
        rows = list(
            session.scalars(
                select(DocumentPageTextRecord)
                .where(DocumentPageTextRecord.document_version_id == document_id)
                .order_by(DocumentPageTextRecord.page_number)
            )
        )
    assert tuple(row.page_number for row in rows) == (1, 2)
    assert tuple(row.native_text.strip() for row in rows) == ("first", "second")
    assert all(row.ocr_state == "not_needed" for row in rows)
    assert jobs.get(second.id).status == "succeeded"  # type: ignore[union-attr]
    events = jobs.list_events()
    assert "reader.extraction.started" in {event.event_type for event in events}
    assert "reader.extraction.succeeded" in {event.event_type for event in events}

    with extraction_context.sessions() as session:
        first_page = session.get(DocumentPageTextRecord, (document_id, 1))
        assert first_page is not None
        first_page.ocr_state = "complete"
        first_page.ocr_text = "OCR evidence\n"
        first_page.ocr_blocks = [
            {
                "x0": 3,
                "y0": 4,
                "x1": 80,
                "y1": 22,
                "text": "OCR evidence\n",
                "block_number": 1,
            }
        ]
        first_page.ocr_confidence = 0.95
        session.commit()
    evidence = service.list_pages(
        work_id=work_id,
        document_version_id=document_id,
    )
    assert evidence[0].text_source == "ocr"
    assert evidence[0].text == "OCR evidence\n"
    assert evidence[0].blocks[0].x0 == 3
    assert evidence[0].native_text.strip() == "first"
    assert evidence[0].ocr_confidence == pytest.approx(0.95)
    with pytest.raises(KeyError):
        service.list_pages(work_id="missing", document_version_id=document_id)
    with pytest.raises(KeyError):
        service.list_pages(work_id=work_id, document_version_id="missing")

    with extraction_context.sessions() as session:
        first_page = session.get(DocumentPageTextRecord, (document_id, 1))
        assert first_page is not None
        first_page.ocr_state = "not_needed"
        first_page.native_blocks = [{"x0": True}]
        session.commit()
    with pytest.raises(RuntimeError, match="Stored page text block"):
        service.list_pages(work_id=work_id, document_version_id=document_id)


@pytest.mark.asyncio
async def test_failed_refresh_preserves_previous_pages_and_records_failure(
    extraction_context: ExtractionContext,
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.extraction import DocumentExtractionService, ExtractionError
    from paper_reader.reader.models import DocumentPageTextRecord

    source = write_text_pdf(tmp_path / "old.pdf", ("old source",))
    work_id, document_id, _ = add_document(
        extraction_context,
        source.read_bytes(),
        label="failed-refresh",
    )
    with extraction_context.sessions() as session:
        session.add(
            DocumentPageTextRecord(
                document_version_id=document_id,
                page_number=1,
                page_width=100,
                page_height=200,
                native_text="previous evidence\n",
                native_blocks=[],
                native_character_count=16,
                likely_scanned=False,
                ocr_state="not_needed",
                ocr_text=None,
                ocr_blocks=[],
                ocr_confidence=None,
                extractor_version="fixture-v1",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()

    class FailingExtractor:
        @staticmethod
        def extract(*_: object, **__: object) -> object:
            raise ExtractionError("damaged", "The PDF is damaged", retryable=False)

    jobs = JobRepository(extraction_context.sessions)
    service = DocumentExtractionService(
        sessions=extraction_context.sessions,
        jobs=jobs,
        store=extraction_context.store,
        extractor=FailingExtractor(),
    )
    job = service.create_job(
        work_id=work_id,
        document_version_id=document_id,
        idempotency_key="failed-refresh",
    )

    await service.execute(job.id)

    finished = jobs.get(job.id)
    assert finished is not None
    assert finished.status == "failed"
    assert finished.error_code == "damaged"
    with extraction_context.sessions() as session:
        document = session.get(DocumentVersionRecord, document_id)
        rows = list(
            session.scalars(
                select(DocumentPageTextRecord).where(
                    DocumentPageTextRecord.document_version_id == document_id
                )
            )
        )
    assert document is not None
    assert document.extraction_status == "failed"
    assert len(rows) == 1
    assert rows[0].native_text == "previous evidence\n"


@pytest.mark.asyncio
async def test_task_shutdown_stops_the_worker_but_leaves_the_job_resumable(
    extraction_context: ExtractionContext,
    tmp_path: Path,
) -> None:
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.extraction import DocumentExtractionService, ExtractionError

    source = write_text_pdf(tmp_path / "shutdown.pdf", ("shutdown",))
    work_id, document_id, _ = add_document(
        extraction_context,
        source.read_bytes(),
        label="shutdown",
    )
    started = Event()
    stopped = Event()

    class BlockingExtractor:
        @staticmethod
        def extract(document, *, cancelled, progress):  # type: ignore[no-untyped-def]
            assert document.is_file()
            started.set()
            while not cancelled():
                stopped.wait(0.01)
            stopped.set()
            raise ExtractionError(
                "cancelled",
                "worker stopped for application shutdown",
                retryable=False,
            )

    jobs = JobRepository(extraction_context.sessions)
    service = DocumentExtractionService(
        sessions=extraction_context.sessions,
        jobs=jobs,
        store=extraction_context.store,
        extractor=BlockingExtractor(),
    )
    job = service.create_job(
        work_id=work_id,
        document_version_id=document_id,
        idempotency_key="shutdown-extraction",
    )
    execution = asyncio.create_task(service.execute(job.id))
    assert await asyncio.to_thread(started.wait, 1)

    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    stopped_before_cleanup = await asyncio.to_thread(stopped.wait, 0.25)
    if not stopped_before_cleanup:
        jobs.request_cancel(job.id)
        assert await asyncio.to_thread(stopped.wait, 1)
    assert stopped_before_cleanup is True
    current = jobs.get(job.id)
    assert current is not None
    assert current.status == "running"
    assert service.resumable_job_ids() == (job.id,)


@pytest.mark.asyncio
async def test_user_cancel_during_extraction_is_terminal_and_persists_no_pages(
    extraction_context: ExtractionContext,
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.extraction import DocumentExtractionService, ExtractionError
    from paper_reader.reader.models import DocumentPageTextRecord

    source = write_text_pdf(tmp_path / "user-cancel.pdf", ("cancel",))
    work_id, document_id, _ = add_document(
        extraction_context,
        source.read_bytes(),
        label="user-cancel",
    )
    started = Event()

    class WaitingExtractor:
        @staticmethod
        def extract(document, *, cancelled, progress):  # type: ignore[no-untyped-def]
            assert document.is_file()
            started.set()
            while not cancelled():
                started.wait(0.01)
            raise ExtractionError("cancelled", "user cancelled", retryable=False)

    jobs = JobRepository(extraction_context.sessions)
    service = DocumentExtractionService(
        sessions=extraction_context.sessions,
        jobs=jobs,
        store=extraction_context.store,
        extractor=WaitingExtractor(),
    )
    job = service.create_job(
        work_id=work_id,
        document_version_id=document_id,
        idempotency_key="user-cancel",
    )
    execution = asyncio.create_task(service.execute(job.id))
    assert await asyncio.to_thread(started.wait, 1)
    jobs.request_cancel(job.id)

    await execution

    finished = jobs.get(job.id)
    assert finished is not None
    assert finished.status == "cancelled"
    assert finished.error_code == "cancelled"
    with extraction_context.sessions() as session:
        document = session.get(DocumentVersionRecord, document_id)
        pages = list(
            session.scalars(
                select(DocumentPageTextRecord).where(
                    DocumentPageTextRecord.document_version_id == document_id
                )
            )
        )
    assert document is not None
    assert document.extraction_status == "pending"
    assert pages == []


@pytest.mark.asyncio
async def test_document_restricted_before_commit_cannot_publish_extracted_pages(
    extraction_context: ExtractionContext,
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.extraction import DocumentExtractionService
    from paper_reader.reader.models import DocumentPageTextRecord

    source = write_text_pdf(tmp_path / "restricted-before-save.pdf", ("restricted",))
    work_id, document_id, _ = add_document(
        extraction_context,
        source.read_bytes(),
        label="restricted-before-save",
    )

    class RestrictingExtractor:
        @staticmethod
        def extract(*_: object, **__: object):  # type: ignore[no-untyped-def]
            with extraction_context.sessions() as session:
                document = session.get(DocumentVersionRecord, document_id)
                assert document is not None
                document.access_status = "restricted"
                session.commit()
            return one_page_result()

    jobs = JobRepository(extraction_context.sessions)
    service = DocumentExtractionService(
        sessions=extraction_context.sessions,
        jobs=jobs,
        store=extraction_context.store,
        extractor=RestrictingExtractor(),
    )
    job = service.create_job(
        work_id=work_id,
        document_version_id=document_id,
        idempotency_key="restricted-before-save",
    )

    await service.execute(job.id)

    finished = jobs.get(job.id)
    assert finished is not None
    assert finished.status == "failed"
    assert finished.error_code == "missing_file"
    with extraction_context.sessions() as session:
        pages = list(
            session.scalars(
                select(DocumentPageTextRecord).where(
                    DocumentPageTextRecord.document_version_id == document_id
                )
            )
        )
        assert pages == []


def test_page_evidence_requires_current_legal_pdf_access(
    extraction_context: ExtractionContext,
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.extraction import DocumentExtractionService, PdfTextExtractor
    from paper_reader.reader.models import DocumentPageTextRecord

    source = write_text_pdf(tmp_path / "read-access.pdf", ("private evidence",))
    work_id, document_id, _ = add_document(
        extraction_context,
        source.read_bytes(),
        label="read-access",
    )
    with extraction_context.sessions() as session:
        session.add(
            DocumentPageTextRecord(
                document_version_id=document_id,
                page_number=1,
                page_width=100,
                page_height=200,
                native_text="private evidence\n",
                native_blocks=[],
                native_character_count=15,
                likely_scanned=False,
                ocr_state="not_needed",
                ocr_text=None,
                ocr_blocks=[],
                ocr_confidence=None,
                extractor_version="fixture-v1",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()

    service = DocumentExtractionService(
        sessions=extraction_context.sessions,
        jobs=JobRepository(extraction_context.sessions),
        store=extraction_context.store,
        extractor=PdfTextExtractor(),
    )
    assert (
        service.list_pages(
            work_id=work_id,
            document_version_id=document_id,
        )[0].text
        == "private evidence\n"
    )

    with extraction_context.sessions() as session:
        document = session.get(DocumentVersionRecord, document_id)
        assert document is not None
        document.access_status = "restricted"
        session.commit()

    with pytest.raises(KeyError):
        service.list_pages(
            work_id=work_id,
            document_version_id=document_id,
        )


def test_extraction_job_rejects_missing_or_nonlocal_document(
    extraction_context: ExtractionContext,
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.extraction import DocumentExtractionService, PdfTextExtractor

    source = write_text_pdf(tmp_path / "nonlocal.pdf", ("content",))
    work_id, document_id, _ = add_document(
        extraction_context,
        source.read_bytes(),
        label="nonlocal",
    )
    service = DocumentExtractionService(
        sessions=extraction_context.sessions,
        jobs=JobRepository(extraction_context.sessions),
        store=extraction_context.store,
        extractor=PdfTextExtractor(),
    )
    with extraction_context.sessions() as session:
        document = session.get(DocumentVersionRecord, document_id)
        assert document is not None
        document.relative_path = None
        session.commit()

    with pytest.raises(ValueError, match="locally available"):
        service.create_job(
            work_id=work_id,
            document_version_id=document_id,
            idempotency_key="nonlocal",
        )
    with pytest.raises(KeyError):
        service.create_job(
            work_id=work_id,
            document_version_id="missing",
            idempotency_key="missing",
        )


def test_extraction_job_validates_keys_membership_paths_and_idempotency_collisions(
    extraction_context: ExtractionContext,
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.extraction import DocumentExtractionService, PdfTextExtractor
    from paper_reader.reader.models import LibraryEntryRecord

    first_pdf = write_text_pdf(tmp_path / "validation-first.pdf", ("first",))
    second_pdf = write_text_pdf(tmp_path / "validation-second.pdf", ("second",))
    first_work, first_document, _ = add_document(
        extraction_context,
        first_pdf.read_bytes(),
        label="validation-first",
    )
    second_work, second_document, _ = add_document(
        extraction_context,
        second_pdf.read_bytes(),
        label="validation-second",
    )
    service = DocumentExtractionService(
        sessions=extraction_context.sessions,
        jobs=JobRepository(extraction_context.sessions),
        store=extraction_context.store,
        extractor=PdfTextExtractor(),
    )

    for key in ("", "x" * 161):
        with pytest.raises(ValueError, match="idempotency key"):
            service.create_job(
                work_id=first_work,
                document_version_id=first_document,
                idempotency_key=key,
            )
    service.create_job(
        work_id=first_work,
        document_version_id=first_document,
        idempotency_key="collision",
    )
    with pytest.raises(ValueError, match="different extraction command"):
        service.create_job(
            work_id=second_work,
            document_version_id=second_document,
            idempotency_key="collision",
        )

    with extraction_context.sessions() as session:
        entry = session.get(LibraryEntryRecord, second_work)
        assert entry is not None
        session.delete(entry)
        session.commit()
    with pytest.raises(KeyError):
        service.create_job(
            work_id=second_work,
            document_version_id=second_document,
            idempotency_key="not-in-library",
        )

    with extraction_context.sessions() as session:
        document = session.get(DocumentVersionRecord, first_document)
        assert document is not None
        original_path = document.relative_path
        document.relative_path = "../outside.pdf"
        session.commit()
    with pytest.raises(ValueError, match="locally available"):
        service.create_job(
            work_id=first_work,
            document_version_id=first_document,
            idempotency_key="unsafe-path",
        )

    with extraction_context.sessions() as session:
        document = session.get(DocumentVersionRecord, first_document)
        assert document is not None
        document.relative_path = original_path
        session.commit()
    assert original_path is not None
    extraction_context.store.resolve(original_path).unlink()
    with pytest.raises(ValueError, match="locally available"):
        service.create_job(
            work_id=first_work,
            document_version_id=first_document,
            idempotency_key="missing-path",
        )


@pytest.mark.asyncio
async def test_extraction_jobs_classify_pre_cancel_internal_and_stale_inputs(
    extraction_context: ExtractionContext,
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.extraction import (
        EXTRACTION_JOB_KIND,
        DocumentExtractionService,
    )

    jobs = JobRepository(extraction_context.sessions)

    class SuccessfulExtractor:
        @staticmethod
        def extract(*_: object, **__: object):  # type: ignore[no-untyped-def]
            return one_page_result()

    first_pdf = write_text_pdf(tmp_path / "pre-cancel.pdf", ("cancel",))
    first_work, first_document, _ = add_document(
        extraction_context,
        first_pdf.read_bytes(),
        label="pre-cancel",
    )
    successful = DocumentExtractionService(
        sessions=extraction_context.sessions,
        jobs=jobs,
        store=extraction_context.store,
        extractor=SuccessfulExtractor(),
    )
    pre_cancelled = successful.create_job(
        work_id=first_work,
        document_version_id=first_document,
        idempotency_key="pre-cancel",
    )
    jobs.request_cancel(pre_cancelled.id)
    await successful.execute(pre_cancelled.id)
    cancelled_job = jobs.get(pre_cancelled.id)
    assert cancelled_job is not None
    assert cancelled_job.status == "cancelled"
    event_count = len(jobs.list_events())
    await successful.execute(pre_cancelled.id)
    assert len(jobs.list_events()) == event_count

    class BrokenExtractor:
        @staticmethod
        def extract(*_: object, **__: object) -> object:
            raise RuntimeError("unexpected adapter failure")

    second_pdf = write_text_pdf(tmp_path / "internal.pdf", ("internal",))
    second_work, second_document, _ = add_document(
        extraction_context,
        second_pdf.read_bytes(),
        label="internal",
    )
    broken = DocumentExtractionService(
        sessions=extraction_context.sessions,
        jobs=jobs,
        store=extraction_context.store,
        extractor=BrokenExtractor(),
    )
    internal = broken.create_job(
        work_id=second_work,
        document_version_id=second_document,
        idempotency_key="internal",
    )
    await broken.execute(internal.id)
    internal_job = jobs.get(internal.id)
    assert internal_job is not None
    assert internal_job.status == "failed"
    assert internal_job.error_code == "internal_error"

    third_pdf = write_text_pdf(tmp_path / "stale.pdf", ("stale",))
    third_work, third_document, _ = add_document(
        extraction_context,
        third_pdf.read_bytes(),
        label="stale",
    )
    stale = successful.create_job(
        work_id=third_work,
        document_version_id=third_document,
        idempotency_key="stale",
    )
    with extraction_context.sessions() as session:
        document = session.get(DocumentVersionRecord, third_document)
        assert document is not None
        document.relative_path = None
        session.commit()
    await successful.execute(stale.id)
    stale_job = jobs.get(stale.id)
    assert stale_job is not None
    assert stale_job.error_code == "missing_file"

    with pytest.raises(KeyError):
        await successful.execute("missing-job")
    wrong_kind = jobs.create(kind="fixture.other", payload={}, idempotency_key="wrong-kind")
    with pytest.raises(ValueError, match="not a document extraction"):
        await successful.execute(wrong_kind.id)
    bad_payload = jobs.create(
        kind=EXTRACTION_JOB_KIND,
        payload={"work_id": first_work},
        idempotency_key="bad-payload",
    )
    with pytest.raises(ValueError, match="payload is invalid"):
        await successful.execute(bad_payload.id)
