"""Safe startup transactions for migrations and confirmed backup restores."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from uuid import uuid4

from paper_reader.backups.archive import create_sqlite_snapshot, read_schema_revision
from paper_reader.backups.manifest import (
    MIGRATION_SNAPSHOT_FORMAT_VERSION,
    canonical_json_bytes,
    utc_text,
)
from paper_reader.backups.restore import (
    PendingRestore,
    PendingRestoreError,
    RestoreValidationError,
    load_pending_restore,
    parse_pending_restore_payload,
    pending_restore_path,
    restore_journal_path,
    validate_pending_restore_paths,
)
from paper_reader.settings import AppPaths

RESTORE_JOURNAL_FORMAT_VERSION = "paper-reader-restore-journal-v1"
MAX_RESTORE_JOURNAL_BYTES = 4 * 1024 * 1024
_JOURNAL_PHASES = {
    "prepared",
    "database_rolled_back",
    "objects_rolled_back",
    "database_published",
    "objects_published",
    "checked",
    "migrated",
    "committed",
}


@dataclass(frozen=True, slots=True)
class MigrationSnapshot:
    database_path: Path
    manifest_path: Path
    source_revision: str
    target_revision: str
    database_sha256: str
    database_byte_size: int
    created_at: datetime


def current_schema_revision(database: Path) -> str | None:
    path = Path(database)
    if not path.is_file() or path.stat().st_size == 0:
        return None
    connection = sqlite3.connect(path)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
        ).fetchone()
        if table is None:
            return "unversioned"
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        return str(row[0]) if row is not None else "unversioned"
    finally:
        connection.close()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_token(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value)
    return cleaned.strip("-")[:48] or "unknown"


class MigrationSnapshotManager:
    def __init__(
        self,
        *,
        database: Path,
        snapshot_root: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._database = Path(database).resolve()
        self._root = Path(snapshot_root).resolve()
        self._clock = clock

    def prepare(self, *, target_revision: str) -> MigrationSnapshot | None:
        source_revision = current_schema_revision(self._database)
        if source_revision is None or source_revision == target_revision:
            return None
        return self.create(
            source_revision=source_revision,
            target_revision=target_revision,
        )

    def create(
        self,
        *,
        source_revision: str,
        target_revision: str,
    ) -> MigrationSnapshot:
        self._root.mkdir(parents=True, exist_ok=True)
        created_at = self._clock().astimezone(UTC)
        stamp = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        token = (
            f"{stamp}-{_safe_token(source_revision)}-to-{_safe_token(target_revision)}-"
            f"{uuid4().hex[:8]}"
        )
        final_database = self._root / f"{token}.sqlite3"
        final_manifest = self._root / f"{token}.manifest.json"
        staged_database = self._root / f".{token}.sqlite3.part"
        staged_manifest = self._root / f".{token}.manifest.json.part"
        database_published = False
        try:
            create_sqlite_snapshot(self._database, staged_database)
            database_sha256 = _sha256_file(staged_database)
            database_byte_size = staged_database.stat().st_size
            payload = {
                "created_at": utc_text(created_at),
                "database_byte_size": database_byte_size,
                "database_file": final_database.name,
                "database_sha256": database_sha256,
                "format_version": MIGRATION_SNAPSHOT_FORMAT_VERSION,
                "source_revision": source_revision,
                "target_revision": target_revision,
            }
            staged_manifest.write_bytes(canonical_json_bytes(payload))
            with staged_manifest.open("rb+") as stream:
                os.fsync(stream.fileno())
            os.replace(staged_database, final_database)
            database_published = True
            os.replace(staged_manifest, final_manifest)
        except BaseException:
            staged_database.unlink(missing_ok=True)
            staged_manifest.unlink(missing_ok=True)
            if database_published:
                final_database.unlink(missing_ok=True)
            final_manifest.unlink(missing_ok=True)
            raise
        return MigrationSnapshot(
            database_path=final_database,
            manifest_path=final_manifest,
            source_revision=source_revision,
            target_revision=target_revision,
            database_sha256=database_sha256,
            database_byte_size=database_byte_size,
            created_at=created_at,
        )

    def prune(self, *, keep: int = 4) -> None:
        if keep < 1:
            raise ValueError("keep must be at least one")
        if not self._root.is_dir():
            return
        candidates: list[tuple[datetime, Path, Path]] = []
        for manifest_path in self._root.glob("*.manifest.json"):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                if payload.get("format_version") != MIGRATION_SNAPSHOT_FORMAT_VERSION:
                    continue
                database_name = payload["database_file"]
                if not isinstance(database_name, str) or Path(database_name).name != database_name:
                    continue
                database_path = (self._root / database_name).resolve()
                database_path.relative_to(self._root)
                if not database_path.is_file():
                    continue
                created_at = datetime.fromisoformat(
                    str(payload["created_at"]).replace("Z", "+00:00")
                )
                if created_at.tzinfo is None:
                    continue
                resolved_manifest = manifest_path.resolve()
                resolved_manifest.relative_to(self._root)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            candidates.append((created_at, resolved_manifest, database_path))
        candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
        for _created_at, manifest_path, database_path in candidates[keep:]:
            database_path.relative_to(self._root)
            manifest_path.relative_to(self._root)
            database_path.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)


class RestoreStartupError(RuntimeError):
    """A stable, path-free startup restore failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RestoreOutcome:
    restore_id: str
    package_sha256: str
    migrated_to_revision: str
    recovered_committed: bool = False


@dataclass(frozen=True, slots=True)
class RestoreJournal:
    pending: PendingRestore
    phase: str
    started_at: datetime
    had_database: bool
    had_database_wal: bool
    had_database_shm: bool
    had_objects: bool
    migrated_to_revision: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "format_version": RESTORE_JOURNAL_FORMAT_VERSION,
            "had_database": self.had_database,
            "had_database_shm": self.had_database_shm,
            "had_database_wal": self.had_database_wal,
            "had_objects": self.had_objects,
            "migrated_to_revision": self.migrated_to_revision,
            "pending": self.pending.as_dict(),
            "phase": self.phase,
            "started_at": utc_text(self.started_at),
        }

    def advance(
        self,
        phase: str,
        *,
        migrated_to_revision: str | None = None,
    ) -> RestoreJournal:
        if phase not in _JOURNAL_PHASES:
            raise ValueError("unsupported restore journal phase")
        return RestoreJournal(
            pending=self.pending,
            phase=phase,
            started_at=self.started_at,
            had_database=self.had_database,
            had_database_wal=self.had_database_wal,
            had_database_shm=self.had_database_shm,
            had_objects=self.had_objects,
            migrated_to_revision=(
                migrated_to_revision
                if migrated_to_revision is not None
                else self.migrated_to_revision
            ),
        )


def _startup_error(code: str) -> RestoreStartupError:
    messages = {
        "invalid_pending_restore": (
            "The confirmed restore cannot be trusted and was not applied"
        ),
        "restore_deferred": (
            "The restore was rolled back safely and will remain pending"
        ),
        "cleanup_deferred": (
            "The restore completed, but private temporary data still needs cleanup"
        ),
    }
    return RestoreStartupError(code, messages[code])


def _same_volume(left: Path, right: Path) -> bool:
    """Return whether two existing paths can use same-volume atomic renames."""

    try:
        left_path = Path(left).resolve(strict=True)
        right_path = Path(right).resolve(strict=True)
        if os.name == "nt" and left_path.drive.casefold() != right_path.drive.casefold():
            return False
        return os.stat(left_path).st_dev == os.stat(right_path).st_dev
    except OSError:
        return False


def _has_link_between(path: Path, root: Path) -> bool:
    cursor = path
    while True:
        try:
            if cursor.is_symlink() or cursor.is_junction():
                return True
        except OSError:
            return True
        if cursor == root:
            return False
        parent = cursor.parent
        if parent == cursor:
            return True
        cursor = parent


def _strict_existing_root(path: Path) -> Path:
    supplied = Path(path)
    if not supplied.is_absolute():
        raise _startup_error("invalid_pending_restore")
    try:
        resolved = supplied.resolve(strict=True)
        details = os.lstat(supplied)
    except OSError:
        raise _startup_error("invalid_pending_restore") from None
    if (
        str(supplied) != str(resolved)
        or not stat.S_ISDIR(details.st_mode)
        or _has_link_between(resolved, resolved)
    ):
        raise _startup_error("invalid_pending_restore")
    return resolved


def _strict_target_paths(paths: AppPaths) -> tuple[Path, Path, Path, Path]:
    state_root = _strict_existing_root(paths.state_root)
    library_root = _strict_existing_root(paths.library_root)
    cache_root = _strict_existing_root(paths.cache_root)
    database = paths.database
    objects = paths.objects
    state_and_library_overlap = (
        state_root == library_root
        or state_root in library_root.parents
        or library_root in state_root.parents
    )
    cache_and_objects_overlap = (
        cache_root == objects
        or cache_root in objects.parents
        or objects in cache_root.parents
    )
    if (
        database.parent != state_root
        or objects.parent != library_root
        or str(database) != str(database.resolve())
        or str(objects) != str(objects.resolve())
        or _has_link_between(database, state_root)
        or _has_link_between(objects, library_root)
        or state_and_library_overlap
        or cache_and_objects_overlap
    ):
        raise _startup_error("invalid_pending_restore")
    try:
        if database.exists():
            details = os.lstat(database)
            if not stat.S_ISREG(details.st_mode):
                raise _startup_error("invalid_pending_restore")
        if objects.is_symlink() or objects.is_junction():
            raise _startup_error("invalid_pending_restore")
        if objects.exists() and not objects.is_dir():
            raise _startup_error("invalid_pending_restore")
    except OSError:
        raise _startup_error("invalid_pending_restore") from None
    return state_root, library_root, database, objects


def _transaction_paths(
    paths: AppPaths,
    restore_id: str,
) -> dict[str, Path]:
    state_root = paths.state_root.resolve()
    library_root = paths.library_root.resolve()
    state_transaction = state_root / f".restore-transaction-{restore_id}"
    library_transaction = library_root / f".restore-transaction-{restore_id}"
    return {
        "state_transaction": state_transaction,
        "library_transaction": library_transaction,
        "new_database": state_transaction / "new.sqlite3",
        "old_database": state_transaction / "old.sqlite3",
        "old_database_wal": state_transaction / "old.sqlite3-wal",
        "old_database_shm": state_transaction / "old.sqlite3-shm",
        "migration_snapshots": state_transaction / "migration-snapshots",
        "old_objects": library_transaction / "old-objects",
    }


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("invalid timestamp")
    return parsed.astimezone(UTC)


def _parse_journal(payload: bytes) -> RestoreJournal:
    try:
        if not payload or len(payload) > MAX_RESTORE_JOURNAL_BYTES:
            raise ValueError("invalid size")
        raw = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("constant")),
        )
        if not isinstance(raw, dict) or set(raw) != {
            "format_version",
            "had_database",
            "had_database_shm",
            "had_database_wal",
            "had_objects",
            "migrated_to_revision",
            "pending",
            "phase",
            "started_at",
        }:
            raise ValueError("invalid keys")
        if raw["format_version"] != RESTORE_JOURNAL_FORMAT_VERSION:
            raise ValueError("invalid version")
        phase = raw["phase"]
        if not isinstance(phase, str) or phase not in _JOURNAL_PHASES:
            raise ValueError("invalid phase")
        booleans = (
            raw["had_database"],
            raw["had_database_shm"],
            raw["had_database_wal"],
            raw["had_objects"],
        )
        if any(type(value) is not bool for value in booleans):
            raise ValueError("invalid flags")
        migrated = raw["migrated_to_revision"]
        if migrated is not None and (
            not isinstance(migrated, str) or not migrated or len(migrated) > 100
        ):
            raise ValueError("invalid migrated revision")
        if phase == "committed" and migrated is None:
            raise ValueError("committed journal lacks revision")
        pending_payload = canonical_json_bytes(raw["pending"])
        pending = parse_pending_restore_payload(pending_payload)
        journal = RestoreJournal(
            pending=pending,
            phase=phase,
            started_at=_parse_utc(raw["started_at"]),
            had_database=raw["had_database"],
            had_database_wal=raw["had_database_wal"],
            had_database_shm=raw["had_database_shm"],
            had_objects=raw["had_objects"],
            migrated_to_revision=migrated,
        )
        if canonical_json_bytes(journal.as_dict()) != payload:
            raise ValueError("non-canonical journal")
        return journal
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        PendingRestoreError,
        RestoreValidationError,
        ValueError,
    ):
        raise _startup_error("invalid_pending_restore") from None


def _read_journal(paths: AppPaths) -> RestoreJournal | None:
    path = restore_journal_path(paths)
    if not path.exists():
        return None
    try:
        if path.is_symlink() or path.is_junction():
            raise _startup_error("invalid_pending_restore")
        details = os.lstat(path)
        if not stat.S_ISREG(details.st_mode):
            raise _startup_error("invalid_pending_restore")
        return _parse_journal(path.read_bytes())
    except RestoreStartupError:
        raise
    except OSError:
        raise _startup_error("invalid_pending_restore") from None


def _write_journal(paths: AppPaths, journal: RestoreJournal) -> None:
    destination = restore_journal_path(paths)
    partial = destination.parent / f".{destination.name}.{uuid4().hex}.part"
    try:
        with partial.open("xb") as stream:
            stream.write(canonical_json_bytes(journal.as_dict()))
            stream.flush()
            os.fsync(stream.fileno())
        _replace_journal_file(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def _replace_journal_file(source: Path, destination: Path) -> None:
    """Publish a journal through short-lived Windows scanner contention."""

    deadline = monotonic() + 1
    while True:
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            transient = os.name == "nt" and getattr(error, "winerror", None) in {5, 32}
            if not transient or monotonic() >= deadline:
                raise
            sleep(0.02)


def _sha256_path(path: Path) -> tuple[int, str]:
    digest = sha256()
    copied = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            copied += len(chunk)
            digest.update(chunk)
    return copied, digest.hexdigest()


def _copy_database(source: Path, destination: Path, pending: PendingRestore) -> None:
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    size, digest = _sha256_path(destination)
    if size != pending.database_byte_size or digest != pending.database_sha256:
        raise OSError("staged database changed while copying")


def _check_database(database: Path) -> str:
    connection = sqlite3.connect(database)
    try:
        checks = connection.execute("PRAGMA quick_check").fetchall()
        if checks != [("ok",)]:
            raise sqlite3.DatabaseError("quick check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise sqlite3.DatabaseError("foreign key check failed")
        return read_schema_revision(connection)
    finally:
        connection.close()


def _verify_published_payload(pending: PendingRestore) -> None:
    database = pending.target_database
    objects_root = pending.target_objects_root
    if (
        not database.is_file()
        or database.is_symlink()
        or not objects_root.is_dir()
        or objects_root.is_symlink()
        or objects_root.is_junction()
    ):
        raise OSError("published restore paths are invalid")
    size, digest = _sha256_path(database)
    if size != pending.database_byte_size or digest != pending.database_sha256:
        raise OSError("published database does not match confirmation")
    expected = {item.archive_path: item for item in pending.objects}
    actual: set[str] = set()
    for candidate in objects_root.rglob("*"):
        if candidate.is_symlink() or candidate.is_junction():
            raise OSError("published object tree contains a link")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise OSError("published object tree contains an invalid entry")
        relative = candidate.relative_to(objects_root.parent).as_posix()
        actual.add(relative)
        item = expected.get(relative)
        if item is None:
            raise OSError("published object tree has an unexpected file")
        object_size, object_hash = _sha256_path(candidate)
        if object_size != item.byte_size or object_hash != item.sha256:
            raise OSError("published object does not match confirmation")
    if actual != set(expected):
        raise OSError("published object tree is incomplete")
    _check_database(database)


def _database_sidecar(database: Path, suffix: str) -> Path:
    return database.with_name(f"{database.name}-{suffix}")


def _unlink_database_bundle(database: Path) -> None:
    database.unlink(missing_ok=True)
    _database_sidecar(database, "wal").unlink(missing_ok=True)
    _database_sidecar(database, "shm").unlink(missing_ok=True)


def _safe_remove_tree(path: Path, parent: Path, expected_name: str) -> None:
    candidate = Path(path)
    if candidate.parent != parent or candidate.name != expected_name:
        raise OSError("unsafe cleanup target")
    if not candidate.exists():
        return
    resolved = candidate.resolve(strict=True)
    if (
        str(candidate) != str(resolved)
        or candidate.is_symlink()
        or candidate.is_junction()
        or not candidate.is_dir()
    ):
        raise OSError("unsafe cleanup target")
    shutil.rmtree(candidate)


class RestoreStartupManager:
    """Apply one confirmed restore before the application opens its database."""

    def __init__(
        self,
        *,
        paths: AppPaths,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        fault: Callable[[str], None] = lambda _phase: None,
    ) -> None:
        self._paths = paths
        self._clock = clock
        self._fault = fault

    def apply_pending(
        self,
        *,
        migrate: Callable[[Path, Path], None],
    ) -> RestoreOutcome | None:
        _strict_target_paths(self._paths)
        journal = _read_journal(self._paths)
        if journal is not None and journal.phase == "committed":
            try:
                validate_pending_restore_paths(
                    self._paths,
                    journal.pending,
                    require_staging=False,
                )
                self._cleanup(journal)
            except (OSError, PendingRestoreError, RestoreStartupError):
                raise _startup_error("cleanup_deferred") from None
            return RestoreOutcome(
                restore_id=journal.pending.restore_id,
                package_sha256=journal.pending.package_sha256,
                migrated_to_revision=journal.migrated_to_revision or "unknown",
                recovered_committed=True,
            )

        if journal is not None:
            try:
                validate_pending_restore_paths(
                    self._paths,
                    journal.pending,
                    require_staging=False,
                )
                self._rollback(journal)
                pending = load_pending_restore(self._paths, verify=True)
            except (OSError, PendingRestoreError, RestoreStartupError):
                raise _startup_error("restore_deferred") from None
        else:
            try:
                pending = load_pending_restore(self._paths, verify=True)
            except PendingRestoreError:
                raise _startup_error("invalid_pending_restore") from None
        if pending is None and journal is not None:
            raise _startup_error("invalid_pending_restore")
        if pending is None:
            return None

        try:
            self._validate_pending_runtime(pending)
        except (OSError, PendingRestoreError, RestoreStartupError):
            raise _startup_error("invalid_pending_restore") from None

        transaction = _transaction_paths(self._paths, pending.restore_id)
        if transaction["state_transaction"].exists() or transaction[
            "library_transaction"
        ].exists():
            raise _startup_error("invalid_pending_restore")
        current = RestoreJournal(
            pending=pending,
            phase="prepared",
            started_at=self._now(),
            had_database=pending.target_database.exists(),
            had_database_wal=_database_sidecar(pending.target_database, "wal").exists(),
            had_database_shm=_database_sidecar(pending.target_database, "shm").exists(),
            had_objects=pending.target_objects_root.exists(),
        )
        try:
            _write_journal(self._paths, current)
            transaction["state_transaction"].mkdir()
            transaction["library_transaction"].mkdir()
            _copy_database(
                pending.staged_database,
                transaction["new_database"],
                pending,
            )

            self._fault("database_rollback")
            if current.had_database:
                os.replace(pending.target_database, transaction["old_database"])
            if current.had_database_wal:
                os.replace(
                    _database_sidecar(pending.target_database, "wal"),
                    transaction["old_database_wal"],
                )
            if current.had_database_shm:
                os.replace(
                    _database_sidecar(pending.target_database, "shm"),
                    transaction["old_database_shm"],
                )
            current = current.advance("database_rolled_back")
            _write_journal(self._paths, current)

            self._fault("objects_rollback")
            if current.had_objects:
                os.replace(pending.target_objects_root, transaction["old_objects"])
            current = current.advance("objects_rolled_back")
            _write_journal(self._paths, current)

            self._fault("database_publish")
            os.replace(transaction["new_database"], pending.target_database)
            current = current.advance("database_published")
            _write_journal(self._paths, current)

            self._fault("objects_publish")
            os.replace(pending.staged_objects_root, pending.target_objects_root)
            current = current.advance("objects_published")
            _write_journal(self._paths, current)

            self._fault("quick_check")
            _verify_published_payload(pending)
            current = current.advance("checked")
            _write_journal(self._paths, current)

            self._fault("migration")
            migrate(pending.target_database, transaction["migration_snapshots"])
            migrated_to_revision = _check_database(pending.target_database)
            current = current.advance(
                "migrated",
                migrated_to_revision=migrated_to_revision,
            )
            _write_journal(self._paths, current)
            self._write_receipt(current)
            committed = current.advance("committed")
            _write_journal(self._paths, committed)
            current = committed
        except BaseException:
            if current.phase == "committed":
                raise _startup_error("cleanup_deferred") from None
            with suppress(BaseException):
                self._rollback(current)
            raise _startup_error("restore_deferred") from None

        try:
            self._cleanup(current)
        except (OSError, RestoreStartupError):
            raise _startup_error("cleanup_deferred") from None
        return RestoreOutcome(
            restore_id=pending.restore_id,
            package_sha256=pending.package_sha256,
            migrated_to_revision=current.migrated_to_revision or "unknown",
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise _startup_error("invalid_pending_restore")
        return value.astimezone(UTC)

    def _validate_pending_runtime(self, pending: PendingRestore) -> None:
        state_root, library_root, database, objects = _strict_target_paths(self._paths)
        validate_pending_restore_paths(self._paths, pending, require_staging=True)
        if (
            pending.target_database != database
            or pending.target_objects_root != objects
            or not _same_volume(pending.state_stage_root, state_root)
            or not _same_volume(pending.library_stage_root, library_root)
        ):
            raise _startup_error("invalid_pending_restore")

    def _rollback(self, journal: RestoreJournal) -> None:
        pending = journal.pending
        transaction = _transaction_paths(self._paths, pending.restore_id)
        old_database = transaction["old_database"]
        old_objects = transaction["old_objects"]

        if old_objects.exists():
            if not pending.staged_objects_root.exists() and pending.target_objects_root.exists():
                pending.staged_objects_root.parent.mkdir(parents=True, exist_ok=True)
                os.replace(pending.target_objects_root, pending.staged_objects_root)
            if pending.target_objects_root.exists():
                raise OSError("cannot restore old object tree")
            os.replace(old_objects, pending.target_objects_root)
        elif not journal.had_objects and pending.target_objects_root.exists():
            if not pending.staged_objects_root.exists():
                pending.staged_objects_root.parent.mkdir(parents=True, exist_ok=True)
                os.replace(pending.target_objects_root, pending.staged_objects_root)
            else:
                _safe_remove_tree(
                    pending.target_objects_root,
                    self._paths.library_root.resolve(),
                    "objects",
                )

        if old_database.exists():
            pending.target_database.unlink(missing_ok=True)
            for suffix, rollback_key, had_sidecar in (
                ("wal", "old_database_wal", journal.had_database_wal),
                ("shm", "old_database_shm", journal.had_database_shm),
            ):
                target_sidecar = _database_sidecar(pending.target_database, suffix)
                old_sidecar = transaction[rollback_key]
                if old_sidecar.exists():
                    target_sidecar.unlink(missing_ok=True)
                    os.replace(old_sidecar, target_sidecar)
                elif had_sidecar and not target_sidecar.exists():
                    # A crash may occur after the main DB rename but during a
                    # sidecar rename.  Never discard a committed old WAL merely
                    # because it had not reached the rollback directory yet.
                    raise OSError("old database sidecar is unavailable")
                elif not had_sidecar:
                    target_sidecar.unlink(missing_ok=True)
            os.replace(old_database, pending.target_database)
        elif not journal.had_database:
            _unlink_database_bundle(pending.target_database)
        elif not pending.target_database.exists():
            raise OSError("old database is unavailable")

        self._remove_transactions(journal)
        restore_journal_path(self._paths).unlink(missing_ok=True)

    def _remove_transactions(self, journal: RestoreJournal) -> None:
        transaction = _transaction_paths(self._paths, journal.pending.restore_id)
        _safe_remove_tree(
            transaction["state_transaction"],
            self._paths.state_root.resolve(),
            transaction["state_transaction"].name,
        )
        _safe_remove_tree(
            transaction["library_transaction"],
            self._paths.library_root.resolve(),
            transaction["library_transaction"].name,
        )

    def _cleanup(self, journal: RestoreJournal) -> None:
        self._fault("cleanup")
        pending = journal.pending
        self._remove_transactions(journal)
        _safe_remove_tree(
            pending.state_stage_root,
            self._paths.state_root.resolve(),
            pending.state_stage_root.name,
        )
        _safe_remove_tree(
            pending.library_stage_root,
            self._paths.library_root.resolve(),
            pending.library_stage_root.name,
        )
        pending_restore_path(self._paths).unlink(missing_ok=True)
        restore_journal_path(self._paths).unlink(missing_ok=True)

    def _write_receipt(self, journal: RestoreJournal) -> None:
        if journal.migrated_to_revision is None:
            raise sqlite3.DatabaseError("restore migration revision is missing")
        connection = sqlite3.connect(journal.pending.target_database)
        try:
            connection.execute(
                "INSERT INTO restore_receipts ("
                "id, package_sha256, source_format_version, source_app_version, "
                "source_schema_revision, object_count, restored_at, "
                "migrated_to_revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid4()),
                    journal.pending.package_sha256,
                    journal.pending.source_format_version,
                    journal.pending.source_app_version,
                    journal.pending.source_schema_revision,
                    journal.pending.object_count,
                    utc_text(self._now()),
                    journal.migrated_to_revision,
                ),
            )
            connection.commit()
            if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise sqlite3.DatabaseError("receipt integrity check failed")
        finally:
            connection.close()


__all__ = [
    "MAX_RESTORE_JOURNAL_BYTES",
    "MigrationSnapshot",
    "MigrationSnapshotManager",
    "RESTORE_JOURNAL_FORMAT_VERSION",
    "RestoreJournal",
    "RestoreOutcome",
    "RestoreStartupError",
    "RestoreStartupManager",
    "current_schema_revision",
]
