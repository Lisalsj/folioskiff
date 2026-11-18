"""Build verified SQLite-and-object backup archives without copying WAL sidecars."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from paper_reader import __version__
from paper_reader.backups.manifest import (
    BACKUP_FORMAT_VERSION,
    DATABASE_ARCHIVE_PATH,
    MANIFEST_ARCHIVE_PATH,
    BackupDatabaseMember,
    BackupManifest,
    BackupObjectMember,
    utc_text,
)
from paper_reader.settings import AppPaths

CHUNK_SIZE = 1024 * 1024
BackupProgress = Callable[[str, int, int], None]


class BackupError(RuntimeError):
    """Base class for a backup that was not safe to publish."""


class BackupCancelledError(BackupError):
    """The caller cancelled before the archive was atomically published."""


class BackupIntegrityError(BackupError):
    """Database or content-addressed object evidence was inconsistent."""


class BackupInsufficientSpaceError(BackupError):
    """The backup volume cannot safely hold the staging copy and archive."""


class UnsafeBackupPathError(BackupError):
    """A destination or referenced object escaped its configured root."""


@dataclass(frozen=True, slots=True)
class ReferencedObject:
    content_sha256: str
    relative_path: str
    byte_size: int
    media_type: str
    access_status: str


@dataclass(frozen=True, slots=True)
class BackupArchiveResult:
    path: Path
    package_sha256: str
    manifest_sha256: str
    manifest: BackupManifest


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    # Windows requires a writable file descriptor for FlushFileBuffers/fsync.
    # ``rb+`` does not mutate the file unless a write is performed.
    with path.open("rb+") as stream:
        os.fsync(stream.fileno())


def create_sqlite_snapshot(
    source: Path,
    destination: Path,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    pages_per_step: int = 128,
) -> None:
    """Create one consistent SQLite file using the online backup API."""

    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if pages_per_step <= 0:
        raise ValueError("pages_per_step must be positive")
    if not source_path.is_file():
        raise BackupIntegrityError("The source database is unavailable")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
        raise BackupIntegrityError("The snapshot destination already exists")
    if cancelled():
        raise BackupCancelledError("Backup was cancelled")

    source_connection: sqlite3.Connection | None = None
    target_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(source_path)
        source_connection.execute("PRAGMA query_only = ON")
        target_connection = sqlite3.connect(destination_path)

        def progress(_status: int, _remaining: int, _total: int) -> None:
            if cancelled():
                raise BackupCancelledError("Backup was cancelled")

        source_connection.backup(
            target_connection,
            pages=pages_per_step,
            progress=progress,
            sleep=0.01,
        )
        target_connection.commit()
        result = target_connection.execute("PRAGMA quick_check").fetchone()
        if result != ("ok",):
            raise BackupIntegrityError("The SQLite snapshot failed its integrity check")
    except BaseException:
        if target_connection is not None:
            target_connection.close()
            target_connection = None
        destination_path.unlink(missing_ok=True)
        destination_path.with_name(f"{destination_path.name}-wal").unlink(missing_ok=True)
        destination_path.with_name(f"{destination_path.name}-shm").unlink(missing_ok=True)
        raise
    finally:
        if target_connection is not None:
            target_connection.close()
        if source_connection is not None:
            source_connection.close()
    _fsync_file(destination_path)


def read_schema_revision(connection: sqlite3.Connection) -> str:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
    ).fetchone()
    if table is None:
        return "unversioned"
    row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    return str(row[0]) if row is not None else "unversioned"


def mark_snapshot_jobs_interrupted(snapshot: Path, *, restored_at: datetime) -> int:
    """Make a portable database inert before it can ever be restored.

    A live snapshot necessarily sees the backup job itself and may see downloads,
    model calls, or publishing jobs in progress.  Those commands are evidence, not
    permission to repeat external side effects on another startup.
    """

    connection = sqlite3.connect(snapshot)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()
        if table is None:
            return 0
        result = json.dumps(
            {"restored": True, "retryable": False},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        cursor = connection.execute(
            "UPDATE jobs SET status = 'restored_interrupted', result = ?, "
            "error_code = 'restored_interrupted', "
            "error_message = 'Interrupted commands are never replayed after restore', "
            "updated_at = ? WHERE status IN ('queued', 'running')",
            (result, utc_text(restored_at)),
        )
        connection.commit()
        checked = connection.execute("PRAGMA quick_check").fetchone()
        if checked != ("ok",):
            raise BackupIntegrityError("The restore-safe snapshot failed its integrity check")
        return max(0, cursor.rowcount)
    finally:
        connection.close()


def iter_referenced_objects(
    connection: sqlite3.Connection,
    *,
    batch_size: int = 512,
) -> Iterator[ReferencedObject]:
    """Yield unique available PDFs without calling ``fetchall`` on the catalog."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'document_versions'"
    ).fetchone()
    if table is None:
        return
    cursor = connection.execute(
        """
        SELECT content_sha256, relative_path, byte_size, media_type, access_status
        FROM document_versions
        WHERE relative_path IS NOT NULL
          AND access_status IN ('open', 'user_provided')
          AND media_type = 'application/pdf'
        ORDER BY content_sha256, relative_path, byte_size
        """
    )
    previous: ReferencedObject | None = None
    while rows := cursor.fetchmany(batch_size):
        for row in rows:
            current = ReferencedObject(
                content_sha256=str(row[0]),
                relative_path=str(row[1]),
                byte_size=int(row[2]),
                media_type=str(row[3]),
                access_status=str(row[4]),
            )
            if previous is not None and current.content_sha256 == previous.content_sha256:
                current_storage = (
                    current.relative_path,
                    current.byte_size,
                    current.media_type,
                )
                previous_storage = (
                    previous.relative_path,
                    previous.byte_size,
                    previous.media_type,
                )
                if current_storage != previous_storage:
                    raise BackupIntegrityError(
                        "One content hash has conflicting stored-object metadata"
                    )
                continue
            if previous is not None:
                yield previous
            previous = current
    if previous is not None:
        yield previous


def _safe_destination(destination: Path, backup_root: Path) -> Path:
    root = backup_root.resolve()
    candidate = Path(destination)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate.parent.resolve().relative_to(root)
    except ValueError as error:
        raise UnsafeBackupPathError("Backup destination escapes the backup root") from error
    if candidate.parent.resolve() != root:
        raise UnsafeBackupPathError("Backup destination must be directly in the backup root")
    if candidate.name in {"", ".", ".."} or "\x00" in candidate.name:
        raise UnsafeBackupPathError("Backup destination name is invalid")
    if candidate.exists():
        raise BackupIntegrityError("Backup destination already exists")
    return candidate


def _expected_object_path(content_sha256: str) -> str:
    lowered = content_sha256.casefold()
    if len(lowered) != 64 or any(character not in "0123456789abcdef" for character in lowered):
        raise BackupIntegrityError("Stored object hash is invalid")
    return f"objects/{lowered[:2]}/{lowered[2:4]}/{lowered}.pdf"


def _resolve_object(root: Path, item: ReferencedObject) -> Path:
    expected = _expected_object_path(item.content_sha256)
    if item.relative_path != expected or "\\" in item.relative_path or "\x00" in item.relative_path:
        raise BackupIntegrityError("Stored object path does not match its hash")
    unresolved = root.joinpath(*item.relative_path.split("/"))
    cursor = unresolved
    while cursor != root:
        try:
            if cursor.is_symlink() or cursor.is_junction():
                raise BackupIntegrityError("Stored object path contains a link")
        except OSError as error:
            raise BackupIntegrityError("Stored object path cannot be inspected") from error
        cursor = cursor.parent
    try:
        resolved = unresolved.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except (OSError, ValueError) as error:
        raise BackupIntegrityError("Stored object is unavailable or outside the library") from error
    if not resolved.is_file():
        raise BackupIntegrityError("Stored object is not a regular file")
    return resolved


def _zip_datetime(value: datetime) -> tuple[int, int, int, int, int, int]:
    normalized = value.astimezone(UTC).replace(microsecond=0)
    year = min(max(normalized.year, 1980), 2107)
    return (
        year,
        normalized.month,
        normalized.day,
        normalized.hour,
        normalized.minute,
        normalized.second,
    )


def _zip_info(name: str, created_at: datetime) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_zip_datetime(created_at))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def _copy_member(
    archive: zipfile.ZipFile,
    *,
    source: Path,
    archive_path: str,
    created_at: datetime,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
    cancelled: Callable[[], bool],
) -> tuple[int, str]:
    before = os.lstat(source)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise BackupIntegrityError("Backup member is not a regular file")
    digest = sha256()
    copied = 0
    with (
        source.open("rb") as input_stream,
        archive.open(
            _zip_info(archive_path, created_at),
            "w",
            force_zip64=True,
        ) as output_stream,
    ):
        while chunk := input_stream.read(CHUNK_SIZE):
            if cancelled():
                raise BackupCancelledError("Backup was cancelled")
            output_stream.write(chunk)
            digest.update(chunk)
            copied += len(chunk)
        descriptor = os.fstat(input_stream.fileno())
    after = os.lstat(source)
    identity_before = (before.st_size, before.st_mtime_ns, before.st_ino)
    identity_descriptor = (descriptor.st_size, descriptor.st_mtime_ns, descriptor.st_ino)
    identity_after = (after.st_size, after.st_mtime_ns, after.st_ino)
    if identity_before != identity_descriptor or identity_before != identity_after:
        raise BackupIntegrityError("Backup member changed while it was copied")
    actual_sha256 = digest.hexdigest()
    if expected_size is not None and copied != expected_size:
        raise BackupIntegrityError("Stored object size does not match the database")
    if expected_sha256 is not None and actual_sha256 != expected_sha256.casefold():
        raise BackupIntegrityError("Stored object hash does not match the database")
    return copied, actual_sha256


def _default_free_space(path: Path) -> int:
    return shutil.disk_usage(path).free


def _publish_without_overwrite(source: Path, destination: Path) -> None:
    """Atomically publish without replacing a concurrently created backup."""

    if os.name == "nt":
        os.rename(source, destination)
        return
    os.link(source, destination)
    source.unlink()


class BackupArchiveBuilder:
    """Create a portable backup in a verified temporary area, then atomically publish it."""

    def __init__(
        self,
        *,
        paths: AppPaths,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        free_space: Callable[[Path], int] = _default_free_space,
    ) -> None:
        self._paths = paths
        self._clock = clock
        self._free_space = free_space

    def create(
        self,
        *,
        destination: Path,
        cancelled: Callable[[], bool] = lambda: False,
        progress: BackupProgress = lambda _phase, _completed, _total: None,
    ) -> BackupArchiveResult:
        backup_root = self._paths.backups.resolve()
        destination_path = _safe_destination(destination, backup_root)
        if cancelled():
            raise BackupCancelledError("Backup was cancelled")
        required = self._estimate_required_bytes()
        if self._free_space(backup_root) < required:
            raise BackupInsufficientSpaceError("Not enough free space for a verified backup")
        if cancelled():
            raise BackupCancelledError("Backup was cancelled")

        # Keep the staging member in the same directory for atomic publication, but
        # do not repeat the potentially long user-visible destination name. The
        # fixed prefix leaves headroom on Windows installations without long-path
        # support while mode="x" still prevents a random-name collision overwrite.
        partial = backup_root / f".backup-{uuid4().hex}.part"
        created_at = self._clock()
        try:
            with tempfile.TemporaryDirectory(
                prefix=".backup-stage-",
                dir=backup_root,
            ) as stage_name:
                stage = Path(stage_name)
                snapshot = stage / "paper-reader.sqlite3"
                create_sqlite_snapshot(
                    self._paths.database,
                    snapshot,
                    cancelled=cancelled,
                )
                mark_snapshot_jobs_interrupted(snapshot, restored_at=created_at)
                if cancelled():
                    raise BackupCancelledError("Backup was cancelled")
                result = self._write_archive(
                    snapshot=snapshot,
                    partial=partial,
                    destination=destination_path,
                    created_at=created_at,
                    cancelled=cancelled,
                    progress=progress,
                )
                return result
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def _estimate_required_bytes(self) -> int:
        if not self._paths.database.is_file():
            raise BackupIntegrityError("The application database is unavailable")
        database_bytes = self._paths.database.stat().st_size
        for suffix in ("-wal", "-shm"):
            sidecar = self._paths.database.with_name(f"{self._paths.database.name}{suffix}")
            if sidecar.is_file():
                database_bytes += sidecar.stat().st_size
        object_bytes = 0
        connection = sqlite3.connect(self._paths.database)
        try:
            for item in iter_referenced_objects(connection):
                object_bytes += item.byte_size
        finally:
            connection.close()
        payload = database_bytes + object_bytes
        return payload * 2 + max(1024 * 1024, payload // 10)

    def _write_archive(
        self,
        *,
        snapshot: Path,
        partial: Path,
        destination: Path,
        created_at: datetime,
        cancelled: Callable[[], bool],
        progress: BackupProgress,
    ) -> BackupArchiveResult:
        connection = sqlite3.connect(snapshot)
        try:
            schema_revision = read_schema_revision(connection)
            objects = tuple(iter_referenced_objects(connection))
        finally:
            connection.close()

        total_steps = 2 + len(objects)
        progress("database_snapshot", 1, total_steps)
        object_members: list[BackupObjectMember] = []
        with zipfile.ZipFile(
            partial,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            database_size, database_sha256 = _copy_member(
                archive,
                source=snapshot,
                archive_path=DATABASE_ARCHIVE_PATH,
                created_at=created_at,
                cancelled=cancelled,
            )
            progress("database_archive", 2, total_steps)
            for index, item in enumerate(objects, start=3):
                source = _resolve_object(self._paths.library_root.resolve(), item)
                copied, content_sha256 = _copy_member(
                    archive,
                    source=source,
                    archive_path=item.relative_path,
                    created_at=created_at,
                    expected_size=item.byte_size,
                    expected_sha256=item.content_sha256,
                    cancelled=cancelled,
                )
                object_members.append(
                    BackupObjectMember(
                        content_sha256=item.content_sha256.casefold(),
                        archive_path=item.relative_path,
                        byte_size=copied,
                        sha256=content_sha256,
                    )
                )
                progress("pdf_objects", index, total_steps)
            manifest = BackupManifest(
                format_version=BACKUP_FORMAT_VERSION,
                app_version=__version__,
                schema_revision=schema_revision,
                created_at=created_at,
                database=BackupDatabaseMember(
                    archive_path=DATABASE_ARCHIVE_PATH,
                    byte_size=database_size,
                    sha256=database_sha256,
                ),
                objects=tuple(object_members),
                included=(
                    "complete local SQLite application state",
                    "research plans, metadata and score evidence",
                    "extracted and OCR paper text",
                    "library state, annotations and reading progress",
                    "analysis reports, complete prompts, failure diagnostics, "
                    "conversations and preferences",
                    "referenced lawful PDF objects",
                ),
                excluded=(
                    "operating-system credentials",
                    "logs",
                    "models",
                    "vector and derived caches",
                    "temporary files",
                    "existing backups",
                ),
            )
            archive.writestr(
                _zip_info(MANIFEST_ARCHIVE_PATH, created_at),
                manifest.canonical_bytes(),
            )
        _fsync_file(partial)
        package_sha256 = _sha256_file(partial)
        if cancelled():
            raise BackupCancelledError("Backup was cancelled")
        _publish_without_overwrite(partial, destination)
        return BackupArchiveResult(
            path=destination,
            package_sha256=package_sha256,
            manifest_sha256=manifest.sha256(),
            manifest=manifest,
        )
