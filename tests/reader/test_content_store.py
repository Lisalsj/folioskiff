from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


def test_reader_content_store_public_contract_is_available() -> None:
    from paper_reader.reader.storage import ContentStore, StoredObject

    assert ContentStore.__name__ == "ContentStore"
    assert StoredObject.__name__ == "StoredObject"


def test_store_writes_pdf_atomically_to_a_sharded_content_address(tmp_path: Path) -> None:
    from paper_reader.reader.storage import ContentStore

    store = ContentStore(tmp_path / "library")

    stored = store.ingest(
        (PDF_BYTES[:12], PDF_BYTES[12:]),
        media_type="application/pdf",
        max_bytes=1024,
    )

    digest = sha256(PDF_BYTES).hexdigest()
    assert stored.content_sha256 == digest
    assert stored.byte_size == len(PDF_BYTES)
    assert stored.media_type == "application/pdf"
    assert stored.relative_path == f"objects/{digest[:2]}/{digest[2:4]}/{digest}.pdf"
    assert store.resolve(stored.relative_path).read_bytes() == PDF_BYTES
    assert not tuple((tmp_path / "library").rglob("*.part"))


def test_store_reuses_one_physical_object_for_duplicate_bytes(tmp_path: Path) -> None:
    from paper_reader.reader.storage import ContentStore

    store = ContentStore(tmp_path / "library")

    first = store.ingest((PDF_BYTES,), media_type="application/pdf", max_bytes=1024)
    second = store.ingest((PDF_BYTES,), media_type="application/pdf", max_bytes=1024)

    assert second == first
    assert tuple((tmp_path / "library" / "objects").rglob("*.pdf")) == (
        store.resolve(first.relative_path),
    )


def test_store_resolves_only_the_exact_path_for_a_valid_content_hash(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.storage import ContentStore, UnsafeStoredPathError

    store = ContentStore(tmp_path / "library")
    stored = store.ingest((PDF_BYTES,), media_type="application/pdf", max_bytes=1024)

    assert (
        store.resolve_object(stored.content_sha256, stored.relative_path)
        == store.resolve(stored.relative_path)
    )
    with pytest.raises(UnsafeStoredPathError, match="hash is invalid"):
        store.resolve_object("not-a-sha256", stored.relative_path)
    with pytest.raises(UnsafeStoredPathError, match="does not match"):
        store.resolve_object(stored.content_sha256, "objects/00/00/other.pdf")


@pytest.mark.parametrize(
    ("chunks", "max_bytes", "error_name"),
    [
        ((b"",), 1024, "InvalidPdfError"),
        ((b"<!doctype html>not a pdf",), 1024, "InvalidPdfError"),
        ((PDF_BYTES,), len(PDF_BYTES) - 1, "DocumentTooLargeError"),
    ],
)
def test_store_rejects_empty_non_pdf_and_oversized_content_without_artifacts(
    tmp_path: Path,
    chunks: tuple[bytes, ...],
    max_bytes: int,
    error_name: str,
) -> None:
    from paper_reader.reader import storage

    store = storage.ContentStore(tmp_path / "library")
    expected_error = getattr(storage, error_name)

    with pytest.raises(expected_error):
        store.ingest(chunks, media_type="application/pdf", max_bytes=max_bytes)

    assert not tuple((tmp_path / "library").rglob("*.pdf"))
    assert not tuple((tmp_path / "library").rglob("*.part"))


def test_store_cancels_between_chunks_and_removes_partial_bytes(tmp_path: Path) -> None:
    from paper_reader.reader.storage import ContentStore, StorageCancelledError

    store = ContentStore(tmp_path / "library")
    cancellation_checks = 0

    def cancelled() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks >= 3

    with pytest.raises(StorageCancelledError):
        store.ingest(
            (PDF_BYTES[:12], PDF_BYTES[12:]),
            media_type="application/pdf",
            max_bytes=1024,
            cancelled=cancelled,
        )

    assert cancellation_checks >= 3
    assert not tuple((tmp_path / "library").rglob("*.pdf"))
    assert not tuple((tmp_path / "library").rglob("*.part"))


@pytest.mark.parametrize(
    "relative_path",
    (
        "../outside.pdf",
        "objects/../../outside.pdf",
        "objects\\..\\..\\outside.pdf",
        "C:/outside.pdf",
        "/outside.pdf",
    ),
)
def test_store_rejects_paths_outside_the_library_root(
    tmp_path: Path,
    relative_path: str,
) -> None:
    from paper_reader.reader.storage import ContentStore, UnsafeStoredPathError

    store = ContentStore(tmp_path / "library")

    with pytest.raises(UnsafeStoredPathError):
        store.resolve(relative_path)


def test_store_rejects_a_symlink_escape_when_supported(tmp_path: Path) -> None:
    from paper_reader.reader.storage import ContentStore, UnsafeStoredPathError

    library = tmp_path / "library"
    outside = tmp_path / "outside"
    outside.mkdir()
    link = library / "objects" / "escape"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows environment does not permit test symlinks")

    store = ContentStore(library)
    with pytest.raises(UnsafeStoredPathError):
        store.resolve("objects/escape/paper.pdf")
