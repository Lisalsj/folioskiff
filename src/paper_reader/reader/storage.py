"""Content-addressed storage for locally available paper documents."""

from __future__ import annotations

import os
import string
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO


class ContentStoreError(ValueError):
    """Base class for rejected or interrupted local content operations."""


class InvalidPdfError(ContentStoreError):
    """The supplied bytes are not a non-empty PDF document."""


class DocumentTooLargeError(ContentStoreError):
    """The supplied document exceeds the explicit byte limit."""


class StorageCancelledError(ContentStoreError):
    """The caller requested cancellation before an object was committed."""


class UnsafeStoredPathError(ContentStoreError):
    """A persisted relative path escapes the configured library root."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    content_sha256: str
    byte_size: int
    media_type: str
    relative_path: str


class ContentStage:
    """Incrementally validate and atomically commit one PDF object."""

    def __init__(
        self,
        store: ContentStore,
        *,
        media_type: str,
        max_bytes: int,
        cancelled: Callable[[], bool],
    ) -> None:
        if media_type.casefold() != "application/pdf":
            raise InvalidPdfError("Only application/pdf content can be stored")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="paper-reader-",
            suffix=".part",
            dir=store._incoming,
        )
        self._store = store
        self._temporary = Path(temporary_name)
        self._stream: BinaryIO | None = os.fdopen(descriptor, "wb")
        self._max_bytes = max_bytes
        self._cancelled = cancelled
        self._digest = sha256()
        self._header = bytearray()
        self._byte_size = 0
        self._committed = False

    @property
    def byte_size(self) -> int:
        return self._byte_size

    def write(self, chunk: bytes) -> None:
        stream = self._require_open()
        if self._cancelled():
            raise StorageCancelledError("Document storage was cancelled")
        if not isinstance(chunk, bytes):
            raise TypeError("Document chunks must be bytes")
        if not chunk:
            return
        next_size = self._byte_size + len(chunk)
        if next_size > self._max_bytes:
            raise DocumentTooLargeError(
                f"Document exceeds the {self._max_bytes}-byte limit"
            )
        if len(self._header) < 5:
            self._header.extend(chunk[: 5 - len(self._header)])
        self._digest.update(chunk)
        stream.write(chunk)
        self._byte_size = next_size

    def commit(self) -> StoredObject:
        stream = self._require_open()
        if self._cancelled():
            raise StorageCancelledError("Document storage was cancelled")
        if self._byte_size == 0 or bytes(self._header) != b"%PDF-":
            raise InvalidPdfError("Document does not begin with the PDF signature")
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()
        self._stream = None

        content_sha256 = self._digest.hexdigest()
        relative_path = self._store._relative_object_path(content_sha256)
        destination = self._store.resolve(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._temporary.unlink()
        else:
            os.replace(self._temporary, destination)
        self._committed = True
        return StoredObject(
            content_sha256=content_sha256,
            byte_size=self._byte_size,
            media_type="application/pdf",
            relative_path=relative_path,
        )

    def abort(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if not self._committed:
            self._temporary.unlink(missing_ok=True)

    def __enter__(self) -> ContentStage:
        return self

    def __exit__(self, *_: object) -> None:
        self.abort()

    def _require_open(self) -> BinaryIO:
        if self._stream is None:
            raise RuntimeError("Content stage is already closed")
        return self._stream


class ContentStore:
    """Store lawful paper bytes under opaque content hashes."""

    def __init__(self, library_root: Path) -> None:
        self._root = Path(library_root).resolve()
        self._objects = self._root / "objects"
        self._incoming = self._objects / ".incoming"
        self._incoming.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def ingest(
        self,
        chunks: Iterable[bytes],
        *,
        media_type: str,
        max_bytes: int,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> StoredObject:
        with self.stage(
            media_type=media_type,
            max_bytes=max_bytes,
            cancelled=cancelled,
        ) as stage:
            for chunk in chunks:
                stage.write(chunk)
            return stage.commit()

    def stage(
        self,
        *,
        media_type: str,
        max_bytes: int,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> ContentStage:
        return ContentStage(
            self,
            media_type=media_type,
            max_bytes=max_bytes,
            cancelled=cancelled,
        )

    def resolve(self, relative_path: str) -> Path:
        if not relative_path or "\x00" in relative_path:
            raise UnsafeStoredPathError("Stored object path is invalid")
        candidate_path = Path(relative_path)
        if candidate_path.is_absolute():
            raise UnsafeStoredPathError("Stored object path must be relative")
        candidate = (self._root / candidate_path).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as error:
            raise UnsafeStoredPathError(
                "Stored object path escapes the library root"
            ) from error
        return candidate

    def resolve_object(self, content_sha256: str, relative_path: str) -> Path:
        """Resolve the exact hash path without following any symlink component."""

        if (
            len(content_sha256) != 64
            or any(character not in string.hexdigits for character in content_sha256)
        ):
            raise UnsafeStoredPathError("Stored object hash is invalid")
        expected = self._relative_object_path(content_sha256.casefold())
        if relative_path != expected:
            raise UnsafeStoredPathError("Stored object path does not match its hash")

        unresolved = self._root.joinpath(*relative_path.split("/"))
        cursor = unresolved
        while cursor != self._root:
            if cursor.is_symlink() or cursor.is_junction():
                raise UnsafeStoredPathError(
                    "Stored object path contains a symbolic link or junction"
                )
            cursor = cursor.parent
        return self.resolve(relative_path)

    @staticmethod
    def _relative_object_path(content_sha256: str) -> str:
        return (
            f"objects/{content_sha256[:2]}/{content_sha256[2:4]}/"
            f"{content_sha256}.pdf"
        )
