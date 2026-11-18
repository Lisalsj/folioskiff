"""Consent-based, recoverable metadata rebuild applied before SQLite opens."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from paper_reader.backups.manifest import canonical_json_bytes, utc_text
from paper_reader.backups.startup import MigrationSnapshotManager, current_schema_revision
from paper_reader.instance import restrict_file_to_current_user
from paper_reader.migrations import migration_head_revision, upgrade_database
from paper_reader.settings import AppPaths

DATA_RESET_CONFIRMATION = "REBUILD PAPER-READER DATA"
_MAX_MARKER_BYTES = 16 * 1024
_MAX_JOURNAL_BYTES = 32 * 1024


class DataResetError(RuntimeError):
    """A reset request is invalid or cannot be completed safely."""


class PendingDataReset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["paper-reader-data-reset-v1"] = "paper-reader-data-reset-v1"
    request_id: UUID
    requested_at: datetime
    database_path_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preserve_objects: Literal[True] = True

    @field_validator("requested_at")
    @classmethod
    def _aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("requested_at must include a timezone")
        return value.astimezone(UTC)


class _ResetJournal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["paper-reader-data-reset-journal-v1"] = (
        "paper-reader-data-reset-journal-v1"
    )
    request_id: UUID
    phase: Literal[
        "prepared",
        "old_database_moved",
        "new_database_published",
        "committed",
    ]
    had_database: bool
    had_wal: bool
    had_shm: bool
    snapshot_database_name: str | None
    snapshot_manifest_name: str | None
    schema_revision: str

    def advance(
        self,
        phase: Literal[
            "old_database_moved",
            "new_database_published",
            "committed",
        ],
    ) -> _ResetJournal:
        return self.model_copy(update={"phase": phase})


@dataclass(frozen=True, slots=True)
class DataResetOutcome:
    request_id: UUID
    snapshot_database: Path | None
    snapshot_manifest: Path | None
    rollback_directory: Path
    schema_revision: str
    recovered: bool = False


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        return bool(checker()) if checker is not None else False
    except OSError:
        return True


def _path_digest(path: Path) -> str:
    normalized = os.path.normcase(str(Path(path).resolve(strict=False)))
    return sha256(normalized.encode("utf-8")).hexdigest()


def _canonical_model(model: BaseModel) -> bytes:
    return canonical_json_bytes(model.model_dump(mode="json")) + b"\n"


def _atomic_private_write(path: Path, payload: bytes) -> None:
    if path.exists() and _is_link_or_junction(path):
        raise DataResetError("pending data reset path is unsafe")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.part")
    descriptor: int | None = None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
        os.set_inheritable(descriptor, False)
        restrict_file_to_current_user(temporary)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
    except OSError as error:
        raise DataResetError("pending data reset could not be published") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _read_regular_payload(path: Path, *, maximum: int) -> bytes:
    try:
        if _is_link_or_junction(path):
            raise DataResetError("pending data reset path is unsafe")
        details = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(details.st_mode) or not 1 <= details.st_size <= maximum:
            raise DataResetError("pending data reset payload is invalid")
        return path.read_bytes()
    except DataResetError:
        raise
    except OSError as error:
        raise DataResetError("pending data reset payload cannot be read") from error


def _safe_name(value: str | None) -> str | None:
    if value is None:
        return None
    if not value or Path(value).name != value or "/" in value or "\\" in value:
        raise DataResetError("pending data reset journal is invalid")
    return value


class DataResetService:
    """Stage a single rebuild request without storing the database path."""

    def __init__(
        self,
        paths: AppPaths,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.paths = paths
        self.pending_path = paths.state_root / "pending-data-reset.json"
        self.epoch_path = paths.state_root / "data-epoch.json"
        self._clock = clock

    def stage(self, confirmation: str) -> PendingDataReset:
        if confirmation != DATA_RESET_CONFIRMATION:
            raise DataResetError("data reset confirmation does not match")
        self.paths.state_root.mkdir(parents=True, exist_ok=True)
        existing = self._load()
        expected_hash = _path_digest(self.paths.database)
        if existing is not None:
            if existing.database_path_sha256 != expected_hash:
                raise DataResetError("a different pending data reset already exists")
            return existing
        requested_at = self._clock()
        if requested_at.tzinfo is None or requested_at.utcoffset() is None:
            raise DataResetError("data reset clock must include a timezone")
        pending = PendingDataReset(
            request_id=uuid4(),
            requested_at=requested_at.astimezone(UTC),
            database_path_sha256=expected_hash,
        )
        _atomic_private_write(self.pending_path, _canonical_model(pending))
        return pending

    def _load(self) -> PendingDataReset | None:
        if not self.pending_path.exists() and not self.pending_path.is_symlink():
            return None
        try:
            return PendingDataReset.model_validate_json(
                _read_regular_payload(self.pending_path, maximum=_MAX_MARKER_BYTES)
            )
        except DataResetError:
            raise
        except (ValidationError, ValueError) as error:
            raise DataResetError("pending data reset payload is invalid") from error


def _journal_path(rollback: Path) -> Path:
    return rollback / "journal.json"


def _write_journal(rollback: Path, journal: _ResetJournal) -> None:
    _atomic_private_write(_journal_path(rollback), _canonical_model(journal))


def _read_journal(rollback: Path) -> _ResetJournal:
    try:
        journal = _ResetJournal.model_validate_json(
            _read_regular_payload(_journal_path(rollback), maximum=_MAX_JOURNAL_BYTES)
        )
        _safe_name(journal.snapshot_database_name)
        _safe_name(journal.snapshot_manifest_name)
        return journal
    except DataResetError:
        raise
    except (ValidationError, ValueError) as error:
        raise DataResetError("pending data reset journal is invalid") from error


def _database_sidecar(database: Path, suffix: str) -> Path:
    return database.with_name(f"{database.name}-{suffix}")


def _strict_request_directory(root: Path, request_id: UUID) -> Path:
    base = root.resolve(strict=False)
    candidate = base / str(request_id)
    if candidate.parent != base or candidate.name != str(request_id):
        raise DataResetError("pending data reset directory is invalid")
    return candidate


def _prepare_journal(
    paths: AppPaths,
    pending: PendingDataReset,
    rollback: Path,
) -> _ResetJournal:
    rollback.parent.mkdir(parents=True, exist_ok=True)
    staging = rollback.parent / f".{pending.request_id}.part"
    if rollback.exists() or staging.exists():
        raise DataResetError("pending data reset transaction is invalid")
    staging.mkdir()
    try:
        fresh_database = staging / "new-paper-reader.sqlite3"
        upgrade_database(
            fresh_database,
            snapshot_root=staging / "migration-snapshots",
        )
        schema_revision = current_schema_revision(fresh_database)
        if schema_revision != migration_head_revision():
            raise DataResetError("rebuilt database schema is invalid")

        database = paths.database
        had_database = database.is_file()
        had_wal = _database_sidecar(database, "wal").is_file()
        had_shm = _database_sidecar(database, "shm").is_file()
        snapshot_database_name: str | None = None
        snapshot_manifest_name: str | None = None
        if had_database:
            snapshot = MigrationSnapshotManager(
                database=database,
                snapshot_root=paths.state_root / "data-reset-snapshots",
            ).create(
                source_revision=current_schema_revision(database) or "unversioned",
                target_revision=f"rebuild-{schema_revision}",
            )
            snapshot_database_name = snapshot.database_path.name
            snapshot_manifest_name = snapshot.manifest_path.name

        journal = _ResetJournal(
            request_id=pending.request_id,
            phase="prepared",
            had_database=had_database,
            had_wal=had_wal,
            had_shm=had_shm,
            snapshot_database_name=snapshot_database_name,
            snapshot_manifest_name=snapshot_manifest_name,
            schema_revision=schema_revision,
        )
        _write_journal(staging, journal)
        os.replace(staging, rollback)
        return journal
    except BaseException:
        if staging.exists() and staging.parent == rollback.parent and not _is_link_or_junction(
            staging
        ):
            shutil.rmtree(staging)
        raise


def _move_expected(source: Path, destination: Path, *, expected: bool) -> None:
    if source.exists():
        if destination.exists():
            raise DataResetError("pending data reset has conflicting database files")
        os.replace(source, destination)
        return
    if expected and not destination.exists():
        raise DataResetError("pending data reset rollback data is incomplete")
    if not expected and destination.exists():
        raise DataResetError("pending data reset rollback data is unexpected")


def _snapshot_paths(paths: AppPaths, journal: _ResetJournal) -> tuple[Path | None, Path | None]:
    root = paths.state_root / "data-reset-snapshots"
    database_name = _safe_name(journal.snapshot_database_name)
    manifest_name = _safe_name(journal.snapshot_manifest_name)
    database = root / database_name if database_name is not None else None
    manifest = root / manifest_name if manifest_name is not None else None
    if (database is None) != (manifest is None):
        raise DataResetError("pending data reset snapshot is incomplete")
    if database is not None:
        assert manifest is not None
        if not database.is_file() or not manifest.is_file():
            raise DataResetError("pending data reset snapshot is unavailable")
    return database, manifest


def _write_epoch(service: DataResetService, journal: _ResetJournal) -> None:
    payload = canonical_json_bytes(
        {
            "applied_at": utc_text(datetime.now(UTC)),
            "format_version": "paper-reader-data-epoch-v1",
            "preserved_objects": True,
            "request_id": str(journal.request_id),
            "schema_revision": journal.schema_revision,
        }
    ) + b"\n"
    _atomic_private_write(service.epoch_path, payload)


def apply_pending_data_reset(
    paths: AppPaths,
    *,
    fault: Callable[[str], None] = lambda _phase: None,
) -> DataResetOutcome | None:
    """Apply or resume one staged rebuild before application services open SQLite."""

    service = DataResetService(paths)
    pending = service._load()
    if pending is None:
        return None
    if pending.database_path_sha256 != _path_digest(paths.database):
        raise DataResetError("pending data reset does not match this database")

    rollback_root = paths.state_root / "data-reset-rollbacks"
    rollback = _strict_request_directory(rollback_root, pending.request_id)
    recovered = rollback.exists()
    if recovered:
        if not rollback.is_dir() or _is_link_or_junction(rollback):
            raise DataResetError("pending data reset transaction is unsafe")
        journal = _read_journal(rollback)
        if journal.request_id != pending.request_id:
            raise DataResetError("pending data reset request does not match its journal")
    else:
        journal = _prepare_journal(paths, pending, rollback)

    database = paths.database
    old_database = rollback / "old-paper-reader.sqlite3"
    old_wal = rollback / "old-paper-reader.sqlite3-wal"
    old_shm = rollback / "old-paper-reader.sqlite3-shm"
    fresh_database = rollback / "new-paper-reader.sqlite3"

    try:
        if journal.phase == "prepared":
            _move_expected(database, old_database, expected=journal.had_database)
            _move_expected(
                _database_sidecar(database, "wal"),
                old_wal,
                expected=journal.had_wal,
            )
            _move_expected(
                _database_sidecar(database, "shm"),
                old_shm,
                expected=journal.had_shm,
            )
            journal = journal.advance("old_database_moved")
            _write_journal(rollback, journal)
            fault("old_database_moved")

        if journal.phase == "old_database_moved":
            if fresh_database.exists() and not database.exists():
                os.replace(fresh_database, database)
            elif fresh_database.exists() or not database.is_file():
                raise DataResetError("pending data reset publish state is invalid")
            journal = journal.advance("new_database_published")
            _write_journal(rollback, journal)
            fault("new_database_published")

        if journal.phase == "new_database_published":
            if current_schema_revision(database) != journal.schema_revision:
                raise DataResetError("rebuilt database failed schema verification")
            _write_epoch(service, journal)
            journal = journal.advance("committed")
            _write_journal(rollback, journal)
            fault("committed")

        if journal.phase != "committed":
            raise DataResetError("pending data reset did not reach a committed state")
        service.pending_path.unlink()
        snapshot_database, snapshot_manifest = _snapshot_paths(paths, journal)
        return DataResetOutcome(
            request_id=pending.request_id,
            snapshot_database=snapshot_database,
            snapshot_manifest=snapshot_manifest,
            rollback_directory=rollback,
            schema_revision=journal.schema_revision,
            recovered=recovered,
        )
    except DataResetError:
        raise
    except Exception as error:
        raise DataResetError("pending data reset could not be completed") from error


__all__ = [
    "DATA_RESET_CONFIRMATION",
    "DataResetError",
    "DataResetOutcome",
    "DataResetService",
    "PendingDataReset",
    "apply_pending_data_reset",
]
