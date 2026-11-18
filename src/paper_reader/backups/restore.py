"""Strict validation and isolated extraction for untrusted portable backups."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory

from paper_reader import __version__
from paper_reader.backups.archive import (
    BackupIntegrityError,
    iter_referenced_objects,
    read_schema_revision,
)
from paper_reader.backups.crypto import (
    BackupCryptoError,
    PasswordMaterial,
    decrypt_backup,
    is_encrypted_backup,
)
from paper_reader.backups.manifest import (
    BACKUP_FORMAT_VERSION,
    DATABASE_ARCHIVE_PATH,
    MANIFEST_ARCHIVE_PATH,
    BackupDatabaseMember,
    BackupManifest,
    BackupObjectMember,
    canonical_json_bytes,
    utc_text,
)
from paper_reader.settings import AppPaths

CHUNK_SIZE = 1024 * 1024
MAX_PACKAGE_BYTES = 20 * 1024 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 20 * 1024 * 1024 * 1024
MAX_DATABASE_BYTES = 10 * 1024 * 1024 * 1024
MAX_OBJECT_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_MEMBER_COUNT = 200_000
MAX_COMPRESSION_RATIO = 100
COMPRESSION_RATIO_MINIMUM_BYTES = 1024 * 1024
PENDING_RESTORE_FORMAT_VERSION = "paper-reader-pending-restore-v1"
STAGED_RESTORE_EVIDENCE_VERSION = "paper-reader-staged-restore-v1"
PENDING_RESTORE_FILENAME = "pending-restore.json"
RESTORE_JOURNAL_FILENAME = "restore-journal.json"
MAX_PENDING_RESTORE_BYTES = 2 * 1024 * 1024
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SEMVER = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


class RestoreValidationError(RuntimeError):
    """A stable, path-free restore validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PendingRestoreError(RuntimeError):
    """A confirmed staging area is missing, changed, ambiguous, or already owned."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _invalid_package() -> RestoreValidationError:
    return RestoreValidationError(
        "invalid_package",
        "The selected file is not a valid paper-reader backup",
    )


def _unsupported_version() -> RestoreValidationError:
    return RestoreValidationError(
        "unsupported_version",
        "The backup requires a newer or unsupported paper-reader version",
    )


def _cancelled() -> RestoreValidationError:
    return RestoreValidationError("cancelled", "Backup validation was cancelled")


def _check_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise _cancelled()


def _remove_tree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return


@dataclass(frozen=True, slots=True)
class ValidatedRestorePackage:
    """Authenticated staged data; owning caller must clean it or complete restore."""

    manifest: BackupManifest
    package_sha256: str
    manifest_sha256: str
    encrypted: bool
    state_stage_root: Path
    library_stage_root: Path
    database_path: Path
    objects_root: Path
    object_count: int
    payload_bytes: int

    def cleanup(self) -> None:
        _remove_tree(self.state_stage_root)
        if self.library_stage_root != self.state_stage_root:
            _remove_tree(self.library_stage_root)

    def __enter__(self) -> ValidatedRestorePackage:
        return self

    def __exit__(self, *_: object) -> None:
        self.cleanup()


def _file_identity(details: os.stat_result) -> tuple[int, int, int]:
    return (details.st_size, details.st_mtime_ns, details.st_ino)


def _hash_package(
    path: Path,
    *,
    cancelled: Callable[[], bool],
) -> tuple[str, tuple[int, int, int]]:
    before = os.lstat(path)
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            _check_cancelled(cancelled)
            digest.update(chunk)
        descriptor = os.fstat(stream.fileno())
    after = os.lstat(path)
    identity = _file_identity(before)
    if identity != _file_identity(descriptor) or identity != _file_identity(after):
        raise _invalid_package()
    _check_cancelled(cancelled)
    return digest.hexdigest(), identity


def _regular_package(path: Path) -> Path:
    supplied = Path(path)
    try:
        if supplied.is_symlink() or supplied.is_junction():
            raise _invalid_package()
        candidate = supplied.resolve(strict=True)
        details = os.lstat(candidate)
    except RestoreValidationError:
        raise
    except OSError:
        raise _invalid_package() from None
    if not stat.S_ISREG(details.st_mode):
        raise _invalid_package()
    if details.st_size <= 0 or details.st_size > MAX_PACKAGE_BYTES:
        raise _invalid_package()
    return candidate


def _make_stage(root: Path) -> Path:
    resolved = root.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".restore-stage-", dir=resolved)).resolve()
    with suppress(OSError):
        os.chmod(stage, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return stage


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _exact_keys(value: dict[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise _invalid_package()


def _text(value: Any, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise _invalid_package()
    return value


def _nonnegative_integer(value: Any, *, maximum: int) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise _invalid_package()
    return value


def _sha256_text(value: Any) -> str:
    text = _text(value, maximum=64)
    if _HEX_SHA256.fullmatch(text) is None:
        raise _invalid_package()
    return text


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 64:
        raise _invalid_package()
    strings = tuple(_text(item, maximum=160) for item in value)
    if list(strings) != sorted(set(strings)):
        raise _invalid_package()
    return strings


def _created_at(value: Any) -> datetime:
    text = _text(value, maximum=40)
    if not text.endswith("Z"):
        raise _invalid_package()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00").astimezone(UTC)
    except ValueError:
        raise _invalid_package() from None
    if utc_text(parsed) != text:
        raise _invalid_package()
    return parsed


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise _invalid_package()
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


@lru_cache(maxsize=1)
def supported_schema_revisions() -> frozenset[str]:
    from paper_reader import migrations

    configuration = Config()
    configuration.set_main_option(
        "script_location",
        str(Path(migrations.__file__).parent),
    )
    revisions = {
        str(item.revision)
        for item in ScriptDirectory.from_config(configuration).walk_revisions()
    }
    return frozenset(revisions)


def _expected_object_path(content_sha256: str) -> str:
    return (
        f"objects/{content_sha256[:2]}/{content_sha256[2:4]}/"
        f"{content_sha256}.pdf"
    )


def parse_backup_manifest(payload: bytes) -> BackupManifest:
    """Decode the exact canonical v1 schema and reject ambiguous JSON."""

    if not payload or len(payload) > MAX_MANIFEST_BYTES:
        raise _invalid_package()
    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise _invalid_package() from None
    if not isinstance(decoded, dict):
        raise _invalid_package()
    _exact_keys(
        decoded,
        {
            "app_version",
            "created_at",
            "database",
            "excluded",
            "format_version",
            "included",
            "objects",
            "schema_revision",
        },
    )
    format_version = _text(decoded["format_version"], maximum=80)
    if format_version != BACKUP_FORMAT_VERSION:
        raise _unsupported_version()
    app_version = _text(decoded["app_version"], maximum=40)
    if _version_tuple(app_version) > _version_tuple(__version__):
        raise _unsupported_version()
    schema_revision = _text(decoded["schema_revision"], maximum=100)
    if schema_revision not in supported_schema_revisions():
        raise _unsupported_version()

    database_raw = decoded["database"]
    if not isinstance(database_raw, dict):
        raise _invalid_package()
    _exact_keys(database_raw, {"archive_path", "byte_size", "sha256"})
    database_path = _text(database_raw["archive_path"], maximum=160)
    if database_path != DATABASE_ARCHIVE_PATH:
        raise _invalid_package()
    database = BackupDatabaseMember(
        archive_path=database_path,
        byte_size=_nonnegative_integer(
            database_raw["byte_size"],
            maximum=MAX_DATABASE_BYTES,
        ),
        sha256=_sha256_text(database_raw["sha256"]),
    )
    if database.byte_size == 0:
        raise _invalid_package()

    objects_raw = decoded["objects"]
    if not isinstance(objects_raw, list) or len(objects_raw) > MAX_MEMBER_COUNT - 2:
        raise _invalid_package()
    objects: list[BackupObjectMember] = []
    seen_hashes: set[str] = set()
    seen_paths: set[str] = set()
    for raw in objects_raw:
        if not isinstance(raw, dict):
            raise _invalid_package()
        _exact_keys(raw, {"archive_path", "byte_size", "content_sha256", "sha256"})
        content_sha256 = _sha256_text(raw["content_sha256"])
        member_sha256 = _sha256_text(raw["sha256"])
        archive_path = _text(raw["archive_path"], maximum=180)
        byte_size = _nonnegative_integer(raw["byte_size"], maximum=MAX_OBJECT_BYTES)
        if (
            content_sha256 != member_sha256
            or byte_size == 0
            or archive_path != _expected_object_path(content_sha256)
            or content_sha256 in seen_hashes
            or archive_path.casefold() in seen_paths
        ):
            raise _invalid_package()
        seen_hashes.add(content_sha256)
        seen_paths.add(archive_path.casefold())
        objects.append(
            BackupObjectMember(
                content_sha256=content_sha256,
                archive_path=archive_path,
                byte_size=byte_size,
                sha256=member_sha256,
            )
        )

    manifest = BackupManifest(
        format_version=format_version,
        app_version=app_version,
        schema_revision=schema_revision,
        created_at=_created_at(decoded["created_at"]),
        database=database,
        objects=tuple(objects),
        included=_string_tuple(decoded["included"]),
        excluded=_string_tuple(decoded["excluded"]),
    )
    if manifest.canonical_bytes() != payload:
        raise _invalid_package()
    return manifest


def _safe_member_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    original = getattr(info, "orig_filename", name)
    if (
        not name
        or name != original
        or "\x00" in original
        or "\\" in name
        or "//" in name
        or name.startswith(("/", "//"))
        or re.match(r"^[A-Za-z]:", name) is not None
        or info.is_dir()
    ):
        raise _invalid_package()
    parts = PurePosixPath(name).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise _invalid_package()
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if file_type not in {0, stat.S_IFREG}:
        raise _invalid_package()
    if info.flag_bits & 0x1:
        raise _invalid_package()
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise _invalid_package()
    if info.file_size < 0 or info.compress_size < 0:
        raise _invalid_package()
    if (
        info.file_size > COMPRESSION_RATIO_MINIMUM_BYTES
        and (
            info.compress_size == 0
            or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
        )
    ):
        raise _invalid_package()
    return name


def _inspect_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if not infos or len(infos) > MAX_MEMBER_COUNT:
        raise _invalid_package()
    result: dict[str, zipfile.ZipInfo] = {}
    casefolded: set[str] = set()
    total = 0
    for info in infos:
        name = _safe_member_name(info)
        folded = name.casefold()
        if name in result or folded in casefolded:
            raise _invalid_package()
        casefolded.add(folded)
        result[name] = info
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise _invalid_package()
    return result


def _validate_declared_members(
    infos: dict[str, zipfile.ZipInfo],
    manifest: BackupManifest,
) -> None:
    expected = {
        MANIFEST_ARCHIVE_PATH,
        manifest.database.archive_path,
        *(item.archive_path for item in manifest.objects),
    }
    if set(infos) != expected:
        raise _invalid_package()
    if infos[MANIFEST_ARCHIVE_PATH].file_size > MAX_MANIFEST_BYTES:
        raise _invalid_package()
    if infos[manifest.database.archive_path].file_size != manifest.database.byte_size:
        raise _invalid_package()
    for item in manifest.objects:
        info = infos[item.archive_path]
        if info.file_size != item.byte_size or info.file_size > MAX_OBJECT_BYTES:
            raise _invalid_package()


def _extract_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    cancelled: Callable[[], bool],
) -> None:
    _check_cancelled(cancelled)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256()
    copied = 0
    with archive.open(info, "r") as input_stream, destination.open("xb") as output_stream:
        while chunk := input_stream.read(CHUNK_SIZE):
            _check_cancelled(cancelled)
            copied += len(chunk)
            if copied > expected_size:
                raise _invalid_package()
            digest.update(chunk)
            output_stream.write(chunk)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    if (
        copied != expected_size
        or copied != info.file_size
        or digest.hexdigest() != expected_sha256
    ):
        raise _invalid_package()


def _validate_database(
    database_path: Path,
    manifest: BackupManifest,
) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{database_path.as_uri()}?mode=ro",
            uri=True,
        )
        connection.execute("PRAGMA query_only = ON")
        checks = connection.execute("PRAGMA quick_check").fetchall()
        if checks != [("ok",)]:
            raise _invalid_package()
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise _invalid_package()
        if read_schema_revision(connection) != manifest.schema_revision:
            raise _invalid_package()
        jobs_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()
        if jobs_table is not None:
            active = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchone()
            if active is None or int(active[0]) != 0:
                raise _invalid_package()
        referenced = tuple(iter_referenced_objects(connection))
    except RestoreValidationError:
        raise
    except (
        BackupIntegrityError,
        sqlite3.DatabaseError,
        OSError,
        OverflowError,
        TypeError,
        ValueError,
    ):
        raise _invalid_package() from None
    finally:
        if connection is not None:
            connection.close()

    expected = {item.content_sha256: item for item in manifest.objects}
    if {item.content_sha256 for item in referenced} != set(expected):
        raise _invalid_package()
    for item in referenced:
        declared = expected[item.content_sha256]
        if (
            item.relative_path != declared.archive_path
            or item.byte_size != declared.byte_size
            or item.media_type != "application/pdf"
            or item.access_status not in {"open", "user_provided"}
        ):
            raise _invalid_package()


class RestorePackageValidator:
    """Authenticate, inspect and stage one package without touching current state."""

    def __init__(self, *, paths: AppPaths) -> None:
        self._paths = paths

    def validate(
        self,
        package: Path,
        *,
        password: PasswordMaterial | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> ValidatedRestorePackage:
        package_path = _regular_package(package)
        _check_cancelled(cancelled)
        encrypted = is_encrypted_backup(package_path)
        if not encrypted:
            try:
                with package_path.open("rb") as stream:
                    signature = stream.read(4)
            except OSError:
                raise _invalid_package() from None
            if signature != b"PK\x03\x04":
                raise _invalid_package()
        package_sha256, package_identity = _hash_package(
            package_path,
            cancelled=cancelled,
        )
        _check_cancelled(cancelled)

        state_stage = _make_stage(self._paths.state_root)
        library_stage: Path | None = None
        completed = False
        try:
            library_stage = _make_stage(self._paths.library_root)
            archive_path = package_path
            decrypted_path: Path | None = None
            if encrypted:
                if password is None:
                    raise RestoreValidationError(
                        "password_required",
                        "This backup requires a password",
                    )
                decrypted_path = library_stage / "authenticated-package.zip"
                try:
                    decrypt_backup(
                        package_path,
                        decrypted_path,
                        password=password,
                        cancelled=cancelled,
                    )
                except BackupCryptoError as error:
                    if error.code == "cancelled":
                        raise _cancelled() from None
                    if error.code == "invalid_password":
                        raise RestoreValidationError(
                            "invalid_password",
                            "The backup password is invalid",
                        ) from None
                    raise RestoreValidationError(
                        "invalid_encrypted_backup",
                        "The encrypted backup could not be authenticated",
                    ) from None
                archive_path = decrypted_path

            database_path = state_stage / DATABASE_ARCHIVE_PATH
            objects_root = library_stage / "objects"
            objects_root.mkdir(parents=True, exist_ok=False)
            try:
                with zipfile.ZipFile(archive_path, mode="r", allowZip64=True) as archive:
                    infos = _inspect_members(archive)
                    manifest_info = infos.get(MANIFEST_ARCHIVE_PATH)
                    if manifest_info is None:
                        raise _invalid_package()
                    manifest = parse_backup_manifest(archive.read(manifest_info))
                    _validate_declared_members(infos, manifest)
                    _extract_member(
                        archive,
                        infos[manifest.database.archive_path],
                        database_path,
                        expected_size=manifest.database.byte_size,
                        expected_sha256=manifest.database.sha256,
                        cancelled=cancelled,
                    )
                    for item in manifest.objects:
                        destination = library_stage.joinpath(*item.archive_path.split("/"))
                        _extract_member(
                            archive,
                            infos[item.archive_path],
                            destination,
                            expected_size=item.byte_size,
                            expected_sha256=item.sha256,
                            cancelled=cancelled,
                        )
            except RestoreValidationError:
                raise
            except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
                raise _invalid_package() from None
            finally:
                if decrypted_path is not None:
                    decrypted_path.unlink(missing_ok=True)

            _validate_database(database_path, manifest)
            # SQLite may create empty WAL coordination files even for a read-only
            # integrity check when the archived database records WAL mode.  They
            # are validation artifacts, not members of the authenticated package.
            database_path.with_name(f"{database_path.name}-wal").unlink(missing_ok=True)
            database_path.with_name(f"{database_path.name}-shm").unlink(missing_ok=True)
            _check_cancelled(cancelled)
            try:
                if _file_identity(os.lstat(package_path)) != package_identity:
                    raise _invalid_package()
            except OSError:
                raise _invalid_package() from None
            result = ValidatedRestorePackage(
                manifest=manifest,
                package_sha256=package_sha256,
                manifest_sha256=manifest.sha256(),
                encrypted=encrypted,
                state_stage_root=state_stage,
                library_stage_root=library_stage,
                database_path=database_path,
                objects_root=objects_root,
                object_count=len(manifest.objects),
                payload_bytes=(
                    manifest.database.byte_size
                    + sum(item.byte_size for item in manifest.objects)
                ),
            )
            completed = True
            return result
        finally:
            if not completed:
                _remove_tree(state_stage)
                if library_stage is not None:
                    _remove_tree(library_stage)


@dataclass(frozen=True, slots=True)
class StagedPathIdentity:
    device: int
    inode: int
    file_type: int

    @classmethod
    def from_stat(cls, details: os.stat_result) -> StagedPathIdentity:
        return cls(
            device=int(details.st_dev),
            inode=int(details.st_ino),
            file_type=int(stat.S_IFMT(details.st_mode)),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "device": self.device,
            "file_type": self.file_type,
            "inode": self.inode,
        }


@dataclass(frozen=True, slots=True)
class StagedRestoreEvidence:
    fingerprint: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class PendingRestore:
    restore_id: str
    confirmed_at: datetime
    fingerprint: str
    package_sha256: str
    manifest_sha256: str
    source_format_version: str
    source_app_version: str
    source_schema_revision: str
    object_count: int
    payload_bytes: int
    database_sha256: str
    database_byte_size: int
    objects: tuple[BackupObjectMember, ...]
    state_stage_root: Path
    library_stage_root: Path
    staged_database: Path
    staged_objects_root: Path
    target_database: Path
    target_objects_root: Path
    evidence: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "confirmed_at": utc_text(self.confirmed_at),
            "database_byte_size": self.database_byte_size,
            "database_sha256": self.database_sha256,
            "evidence": self.evidence,
            "fingerprint": self.fingerprint,
            "format_version": PENDING_RESTORE_FORMAT_VERSION,
            "library_stage_root": str(self.library_stage_root),
            "manifest_sha256": self.manifest_sha256,
            "object_count": self.object_count,
            "objects": [item.as_dict() for item in self.objects],
            "package_sha256": self.package_sha256,
            "payload_bytes": self.payload_bytes,
            "restore_id": self.restore_id,
            "source_app_version": self.source_app_version,
            "source_format_version": self.source_format_version,
            "source_schema_revision": self.source_schema_revision,
            "staged_database": str(self.staged_database),
            "staged_objects_root": str(self.staged_objects_root),
            "state_stage_root": str(self.state_stage_root),
            "target_database": str(self.target_database),
            "target_objects_root": str(self.target_objects_root),
        }


def pending_restore_path(paths: AppPaths) -> Path:
    return paths.state_root.resolve() / PENDING_RESTORE_FILENAME


def restore_journal_path(paths: AppPaths) -> Path:
    return paths.state_root.resolve() / RESTORE_JOURNAL_FILENAME


def _pending_error(code: str = "invalid_pending_restore") -> PendingRestoreError:
    messages = {
        "invalid_pending_restore": "The confirmed restore staging area is invalid or changed",
        "restore_already_pending": "Another restore is already waiting for restart",
        "stale_restore_preview": "The restore preview no longer matches staged data",
    }
    return PendingRestoreError(code, messages[code])


def _strict_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise _pending_error() from None
    if parsed.version != 4 or str(parsed) != value:
        raise _pending_error()
    return value


def _path_has_link(path: Path, root: Path) -> bool:
    cursor = path
    while cursor != root:
        try:
            if cursor.is_symlink() or cursor.is_junction():
                return True
        except OSError:
            return True
        parent = cursor.parent
        if parent == cursor:
            return True
        cursor = parent
    return False


def _strict_stage_root(candidate: Path, root: Path) -> Path:
    expected_root = root.resolve()
    supplied = Path(candidate)
    if not supplied.is_absolute() or not supplied.name.startswith(".restore-stage-"):
        raise _pending_error()
    try:
        resolved = supplied.resolve(strict=True)
    except OSError:
        raise _pending_error() from None
    if (
        str(supplied) != str(resolved)
        or resolved.parent != expected_root
        or not resolved.is_dir()
        or _path_has_link(resolved, expected_root)
    ):
        raise _pending_error()
    return resolved


def _strict_regular_file(path: Path, root: Path) -> tuple[StagedPathIdentity, int, str]:
    candidate = Path(path)
    try:
        if _path_has_link(candidate, root):
            raise _pending_error()
        before = os.lstat(candidate)
        if not stat.S_ISREG(before.st_mode):
            raise _pending_error()
        digest = sha256()
        copied = 0
        with candidate.open("rb") as stream:
            while chunk := stream.read(CHUNK_SIZE):
                digest.update(chunk)
                copied += len(chunk)
            descriptor = os.fstat(stream.fileno())
        after = os.lstat(candidate)
    except PendingRestoreError:
        raise
    except OSError:
        raise _pending_error() from None
    identity = StagedPathIdentity.from_stat(before)
    if (
        identity != StagedPathIdentity.from_stat(descriptor)
        or identity != StagedPathIdentity.from_stat(after)
        or copied != before.st_size
        or copied != descriptor.st_size
    ):
        raise _pending_error()
    return identity, copied, digest.hexdigest()


def _strict_directory_identity(path: Path, root: Path) -> StagedPathIdentity:
    try:
        if _path_has_link(path, root):
            raise _pending_error()
        details = os.lstat(path)
    except PendingRestoreError:
        raise
    except OSError:
        raise _pending_error() from None
    if not stat.S_ISDIR(details.st_mode):
        raise _pending_error()
    return StagedPathIdentity.from_stat(details)


def _collect_stage(
    *,
    paths: AppPaths,
    state_stage_root: Path,
    library_stage_root: Path,
    database_path: Path,
    objects_root: Path,
    package_sha256: str,
    manifest_sha256: str,
    source_format_version: str,
    source_app_version: str,
    source_schema_revision: str,
    database_sha256: str,
    database_byte_size: int,
    objects: tuple[BackupObjectMember, ...],
) -> StagedRestoreEvidence:
    state_stage = _strict_stage_root(state_stage_root, paths.state_root)
    library_stage = _strict_stage_root(library_stage_root, paths.library_root)
    expected_database = state_stage / DATABASE_ARCHIVE_PATH
    expected_objects = library_stage / "objects"
    if (
        str(Path(database_path)) != str(expected_database)
        or str(Path(objects_root)) != str(expected_objects)
    ):
        raise _pending_error()

    try:
        state_entries = {item.name for item in state_stage.iterdir()}
        database_entries = {item.name for item in expected_database.parent.iterdir()}
        library_entries = {item.name for item in library_stage.iterdir()}
    except OSError:
        raise _pending_error() from None
    if (
        state_entries != {"database"}
        or database_entries != {expected_database.name}
        or library_entries != {"objects"}
    ):
        raise _pending_error()

    state_identity = _strict_directory_identity(state_stage, paths.state_root.resolve())
    library_identity = _strict_directory_identity(
        library_stage,
        paths.library_root.resolve(),
    )
    objects_identity = _strict_directory_identity(expected_objects, library_stage)
    database_identity, actual_size, actual_hash = _strict_regular_file(
        expected_database,
        state_stage,
    )
    if actual_size != database_byte_size or actual_hash != database_sha256:
        raise _pending_error()

    expected_files = {item.archive_path for item in objects}
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    try:
        descendants = tuple(expected_objects.rglob("*"))
    except OSError:
        raise _pending_error() from None
    for candidate in descendants:
        if candidate.is_symlink() or candidate.is_junction():
            raise _pending_error()
        relative = candidate.relative_to(library_stage).as_posix()
        if candidate.is_dir():
            actual_directories.add(relative)
        elif candidate.is_file():
            actual_files.add(relative)
        else:
            raise _pending_error()
    if actual_files != expected_files:
        raise _pending_error()
    expected_directories = {"objects"}
    for archive_path in expected_files:
        parent = PurePosixPath(archive_path).parent
        while str(parent) not in {".", "objects"}:
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    if actual_directories != expected_directories - {"objects"}:
        raise _pending_error()

    object_evidence: list[dict[str, object]] = []
    for item in sorted(objects, key=lambda value: value.archive_path):
        candidate = library_stage.joinpath(*item.archive_path.split("/"))
        identity, size, digest = _strict_regular_file(candidate, library_stage)
        if size != item.byte_size or digest != item.sha256:
            raise _pending_error()
        object_evidence.append(
            {
                "archive_path": item.archive_path,
                "byte_size": size,
                "content_sha256": item.content_sha256,
                "identity": identity.as_dict(),
                "sha256": digest,
            }
        )

    payload: dict[str, object] = {
        "database": {
            "byte_size": actual_size,
            "identity": database_identity.as_dict(),
            "sha256": actual_hash,
        },
        "format_version": STAGED_RESTORE_EVIDENCE_VERSION,
        "library_stage": {
            "identity": library_identity.as_dict(),
            "name": library_stage.name,
        },
        "manifest_sha256": manifest_sha256,
        "objects": object_evidence,
        "objects_root_identity": objects_identity.as_dict(),
        "package_sha256": package_sha256,
        "source_app_version": source_app_version,
        "source_format_version": source_format_version,
        "source_schema_revision": source_schema_revision,
        "state_stage": {
            "identity": state_identity.as_dict(),
            "name": state_stage.name,
        },
    }
    fingerprint = sha256(canonical_json_bytes(payload)).hexdigest()
    return StagedRestoreEvidence(fingerprint=fingerprint, payload=payload)


def staged_restore_fingerprint(
    *,
    paths: AppPaths,
    package: ValidatedRestorePackage,
) -> str:
    return _collect_stage(
        paths=paths,
        state_stage_root=package.state_stage_root,
        library_stage_root=package.library_stage_root,
        database_path=package.database_path,
        objects_root=package.objects_root,
        package_sha256=package.package_sha256,
        manifest_sha256=package.manifest_sha256,
        source_format_version=package.manifest.format_version,
        source_app_version=package.manifest.app_version,
        source_schema_revision=package.manifest.schema_revision,
        database_sha256=package.manifest.database.sha256,
        database_byte_size=package.manifest.database.byte_size,
        objects=package.manifest.objects,
    ).fingerprint


def _write_new_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.parent / f".{path.name}.{uuid4().hex}.part"
    try:
        with partial.open("xb") as stream:
            stream.write(canonical_json_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            os.rename(partial, path)
        else:  # pragma: no cover - POSIX release path
            os.link(partial, path)
            partial.unlink()
    finally:
        partial.unlink(missing_ok=True)


def schedule_pending_restore(
    *,
    paths: AppPaths,
    package: ValidatedRestorePackage,
    restore_id: str,
    expected_fingerprint: str,
    confirmed_at: datetime,
) -> PendingRestore:
    pending_path = pending_restore_path(paths)
    if pending_path.exists() or restore_journal_path(paths).exists():
        raise _pending_error("restore_already_pending")
    restore_id = _strict_uuid(restore_id)
    if confirmed_at.tzinfo is None or confirmed_at.utcoffset() is None:
        raise _pending_error()
    evidence = _collect_stage(
        paths=paths,
        state_stage_root=package.state_stage_root,
        library_stage_root=package.library_stage_root,
        database_path=package.database_path,
        objects_root=package.objects_root,
        package_sha256=package.package_sha256,
        manifest_sha256=package.manifest_sha256,
        source_format_version=package.manifest.format_version,
        source_app_version=package.manifest.app_version,
        source_schema_revision=package.manifest.schema_revision,
        database_sha256=package.manifest.database.sha256,
        database_byte_size=package.manifest.database.byte_size,
        objects=package.manifest.objects,
    )
    if not _HEX_SHA256.fullmatch(expected_fingerprint) or not compare_digest(
        evidence.fingerprint,
        expected_fingerprint,
    ):
        raise _pending_error("stale_restore_preview")
    pending = PendingRestore(
        restore_id=restore_id,
        confirmed_at=confirmed_at.astimezone(UTC),
        fingerprint=evidence.fingerprint,
        package_sha256=package.package_sha256,
        manifest_sha256=package.manifest_sha256,
        source_format_version=package.manifest.format_version,
        source_app_version=package.manifest.app_version,
        source_schema_revision=package.manifest.schema_revision,
        object_count=package.object_count,
        payload_bytes=package.payload_bytes,
        database_sha256=package.manifest.database.sha256,
        database_byte_size=package.manifest.database.byte_size,
        objects=package.manifest.objects,
        state_stage_root=package.state_stage_root,
        library_stage_root=package.library_stage_root,
        staged_database=package.database_path,
        staged_objects_root=package.objects_root,
        target_database=paths.database.resolve(),
        target_objects_root=paths.objects.resolve(),
        evidence=evidence.payload,
    )
    try:
        _write_new_json(pending_path, pending.as_dict())
    except FileExistsError:
        raise _pending_error("restore_already_pending") from None
    except OSError:
        raise _pending_error() from None
    return pending


def _required_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _pending_error()
    return value


def _required_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise _pending_error()
    return value


def parse_pending_restore_payload(payload: bytes) -> PendingRestore:
    if not payload or len(payload) > MAX_PENDING_RESTORE_BYTES:
        raise _pending_error()
    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise _pending_error() from None
    raw = _required_dict(decoded)
    _exact_keys(
        raw,
        {
            "confirmed_at",
            "database_byte_size",
            "database_sha256",
            "evidence",
            "fingerprint",
            "format_version",
            "library_stage_root",
            "manifest_sha256",
            "object_count",
            "objects",
            "package_sha256",
            "payload_bytes",
            "restore_id",
            "source_app_version",
            "source_format_version",
            "source_schema_revision",
            "staged_database",
            "staged_objects_root",
            "state_stage_root",
            "target_database",
            "target_objects_root",
        },
    )
    if raw["format_version"] != PENDING_RESTORE_FORMAT_VERSION:
        raise _pending_error()
    objects: list[BackupObjectMember] = []
    for value in _required_list(raw["objects"]):
        item = _required_dict(value)
        _exact_keys(item, {"archive_path", "byte_size", "content_sha256", "sha256"})
        objects.append(
            BackupObjectMember(
                content_sha256=_sha256_text(item["content_sha256"]),
                archive_path=_text(item["archive_path"], maximum=180),
                byte_size=_nonnegative_integer(item["byte_size"], maximum=MAX_OBJECT_BYTES),
                sha256=_sha256_text(item["sha256"]),
            )
        )
    confirmed_at = _created_at(raw["confirmed_at"])
    pending = PendingRestore(
        restore_id=_strict_uuid(_text(raw["restore_id"], maximum=36)),
        confirmed_at=confirmed_at,
        fingerprint=_sha256_text(raw["fingerprint"]),
        package_sha256=_sha256_text(raw["package_sha256"]),
        manifest_sha256=_sha256_text(raw["manifest_sha256"]),
        source_format_version=_text(raw["source_format_version"], maximum=80),
        source_app_version=_text(raw["source_app_version"], maximum=40),
        source_schema_revision=_text(raw["source_schema_revision"], maximum=100),
        object_count=_nonnegative_integer(raw["object_count"], maximum=MAX_MEMBER_COUNT),
        payload_bytes=_nonnegative_integer(
            raw["payload_bytes"],
            maximum=MAX_TOTAL_UNCOMPRESSED_BYTES,
        ),
        database_sha256=_sha256_text(raw["database_sha256"]),
        database_byte_size=_nonnegative_integer(
            raw["database_byte_size"],
            maximum=MAX_DATABASE_BYTES,
        ),
        objects=tuple(objects),
        state_stage_root=Path(_text(raw["state_stage_root"], maximum=1024)),
        library_stage_root=Path(_text(raw["library_stage_root"], maximum=1024)),
        staged_database=Path(_text(raw["staged_database"], maximum=1200)),
        staged_objects_root=Path(_text(raw["staged_objects_root"], maximum=1200)),
        target_database=Path(_text(raw["target_database"], maximum=1024)),
        target_objects_root=Path(_text(raw["target_objects_root"], maximum=1024)),
        evidence=_required_dict(raw["evidence"]),
    )
    if (
        pending.object_count != len(pending.objects)
        or pending.payload_bytes
        != pending.database_byte_size + sum(item.byte_size for item in pending.objects)
        or canonical_json_bytes(pending.as_dict()) != payload
    ):
        raise _pending_error()
    return pending


def verify_pending_restore(paths: AppPaths, pending: PendingRestore) -> None:
    state_stage = _strict_stage_root(pending.state_stage_root, paths.state_root)
    library_stage = _strict_stage_root(pending.library_stage_root, paths.library_root)
    if (
        str(pending.target_database) != str(paths.database.resolve())
        or str(pending.target_objects_root) != str(paths.objects.resolve())
        or str(pending.staged_database) != str(state_stage / DATABASE_ARCHIVE_PATH)
        or str(pending.staged_objects_root) != str(library_stage / "objects")
    ):
        raise _pending_error()
    evidence = _collect_stage(
        paths=paths,
        state_stage_root=state_stage,
        library_stage_root=library_stage,
        database_path=pending.staged_database,
        objects_root=pending.staged_objects_root,
        package_sha256=pending.package_sha256,
        manifest_sha256=pending.manifest_sha256,
        source_format_version=pending.source_format_version,
        source_app_version=pending.source_app_version,
        source_schema_revision=pending.source_schema_revision,
        database_sha256=pending.database_sha256,
        database_byte_size=pending.database_byte_size,
        objects=pending.objects,
    )
    if evidence.payload != pending.evidence or not compare_digest(
        evidence.fingerprint,
        pending.fingerprint,
    ):
        raise _pending_error()


def validate_pending_restore_paths(
    paths: AppPaths,
    pending: PendingRestore,
    *,
    require_staging: bool = True,
) -> None:
    expected_state_root = paths.state_root.resolve()
    expected_library_root = paths.library_root.resolve()
    state_stage = Path(pending.state_stage_root)
    library_stage = Path(pending.library_stage_root)
    if (
        not state_stage.is_absolute()
        or not library_stage.is_absolute()
        or state_stage.parent != expected_state_root
        or library_stage.parent != expected_library_root
        or not state_stage.name.startswith(".restore-stage-")
        or not library_stage.name.startswith(".restore-stage-")
        or str(pending.target_database) != str(paths.database.resolve())
        or str(pending.target_objects_root) != str(paths.objects.resolve())
        or str(pending.staged_database) != str(state_stage / DATABASE_ARCHIVE_PATH)
        or str(pending.staged_objects_root) != str(library_stage / "objects")
    ):
        raise _pending_error()
    if require_staging:
        _strict_stage_root(state_stage, paths.state_root)
        _strict_stage_root(library_stage, paths.library_root)


def load_pending_restore(
    paths: AppPaths,
    *,
    verify: bool = True,
) -> PendingRestore | None:
    path = pending_restore_path(paths)
    if not path.exists():
        return None
    try:
        if path.is_symlink() or path.is_junction():
            raise _pending_error()
        details = os.lstat(path)
        if not stat.S_ISREG(details.st_mode):
            raise _pending_error()
        payload = path.read_bytes()
    except PendingRestoreError:
        raise
    except OSError:
        raise _pending_error() from None
    try:
        pending = parse_pending_restore_payload(payload)
    except RestoreValidationError:
        raise _pending_error() from None
    validate_pending_restore_paths(paths, pending, require_staging=verify)
    if verify:
        verify_pending_restore(paths, pending)
    return pending


__all__ = [
    "MAX_COMPRESSION_RATIO",
    "MAX_DATABASE_BYTES",
    "MAX_MANIFEST_BYTES",
    "MAX_MEMBER_COUNT",
    "MAX_OBJECT_BYTES",
    "MAX_PACKAGE_BYTES",
    "MAX_TOTAL_UNCOMPRESSED_BYTES",
    "PENDING_RESTORE_FILENAME",
    "PENDING_RESTORE_FORMAT_VERSION",
    "PendingRestore",
    "PendingRestoreError",
    "RestorePackageValidator",
    "RestoreValidationError",
    "StagedPathIdentity",
    "StagedRestoreEvidence",
    "ValidatedRestorePackage",
    "load_pending_restore",
    "parse_backup_manifest",
    "parse_pending_restore_payload",
    "pending_restore_path",
    "restore_journal_path",
    "schedule_pending_restore",
    "staged_restore_fingerprint",
    "supported_schema_revisions",
    "validate_pending_restore_paths",
    "verify_pending_restore",
]
