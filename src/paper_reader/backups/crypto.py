"""Streaming authenticated encryption for portable backup archives.

The wrapper intentionally has one fixed, versioned cryptographic profile.  The
exact header bytes are authenticated as AES-GCM additional data, so an attacker
cannot silently weaken KDF parameters or alter the declared plaintext length.
"""

from __future__ import annotations

import os
import secrets
import stat
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

ENCRYPTED_BACKUP_MAGIC = b"PAPER-READER-BK1"
ENCRYPTED_BACKUP_VERSION = 1
KDF_SCRYPT = 1
CIPHER_AES_256_GCM = 1
AES_KEY_BYTES = 32
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_SALT_BYTES = 16
GCM_NONCE_BYTES = 12
GCM_TAG_BYTES = 16
MAX_PASSWORD_BYTES = 4096
DEFAULT_CHUNK_SIZE = 1024 * 1024
DEFAULT_SECRET_LEASE_SECONDS = 5 * 60
PasswordMaterial = str | bytes | bytearray | memoryview

_HEADER = struct.Struct(
    f">16sBBBBIII{SCRYPT_SALT_BYTES}s{GCM_NONCE_BYTES}sQ"
)


class BackupCryptoError(RuntimeError):
    """A stable, redacted encryption error safe for jobs and UI events."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PasswordLease:
    """One consumed password buffer that is cleared when its context closes."""

    __slots__ = ("_secret",)

    def __init__(self, secret: bytearray) -> None:
        self._secret = secret

    def __enter__(self) -> bytearray:
        if not self._secret:
            raise BackupCryptoError(
                "secret_lease_unavailable",
                "The one-time password lease is unavailable",
            )
        return self._secret

    def __exit__(self, *_: object) -> None:
        self.clear()

    def clear(self) -> None:
        _clear(self._secret)
        self._secret.clear()

    def __repr__(self) -> str:
        return "<PasswordLease redacted>"

    def __del__(self) -> None:
        self.clear()


class PasswordLeaseStore:
    """Thread-safe, in-memory-only, expiring one-time password handoff."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_SECRET_LEASE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._token_factory = token_factory
        self._leases: dict[str, tuple[float, bytearray]] = {}
        self._lock = threading.Lock()

    def issue(self, password: PasswordMaterial) -> str:
        secret = _password_bytes(password)
        owned_by_store = False
        try:
            with self._lock:
                self._purge_expired_locked()
                for _attempt in range(16):
                    lease_id = self._token_factory()
                    if (
                        isinstance(lease_id, str)
                        and 16 <= len(lease_id) <= 160
                        and lease_id not in self._leases
                    ):
                        self._leases[lease_id] = (
                            self._clock() + self._ttl_seconds,
                            secret,
                        )
                        owned_by_store = True
                        return lease_id
            raise BackupCryptoError(
                "secret_lease_unavailable",
                "The one-time password lease is unavailable",
            )
        except BaseException:
            if not owned_by_store:
                _clear(secret)
            raise

    def consume(self, lease_id: str) -> PasswordLease:
        if not isinstance(lease_id, str) or not lease_id:
            raise BackupCryptoError(
                "secret_lease_unavailable",
                "The one-time password lease is unavailable",
            )
        with self._lock:
            self._purge_expired_locked()
            stored = self._leases.pop(lease_id, None)
        if stored is None:
            raise BackupCryptoError(
                "secret_lease_unavailable",
                "The one-time password lease is unavailable",
            )
        return PasswordLease(stored[1])

    def discard(self, lease_id: str) -> None:
        with self._lock:
            stored = self._leases.pop(lease_id, None)
        if stored is not None:
            _clear(stored[1])

    def close(self) -> None:
        with self._lock:
            stored = tuple(self._leases.values())
            self._leases.clear()
        for _expires_at, secret in stored:
            _clear(secret)

    def _purge_expired_locked(self) -> None:
        now = self._clock()
        expired = [
            lease_id
            for lease_id, (expires_at, _secret) in self._leases.items()
            if expires_at <= now
        ]
        for lease_id in expired:
            _expires_at, secret = self._leases.pop(lease_id)
            _clear(secret)

    def __repr__(self) -> str:
        return "<PasswordLeaseStore redacted>"


@dataclass(frozen=True, slots=True)
class EncryptedBackupHeader:
    format_version: int
    kdf_id: int
    cipher_id: int
    flags: int
    scrypt_n: int
    scrypt_r: int
    scrypt_p: int
    salt: bytes
    nonce: bytes
    plaintext_size: int

    @property
    def byte_size(self) -> int:
        return _HEADER.size

    def canonical_bytes(self) -> bytes:
        return _HEADER.pack(
            ENCRYPTED_BACKUP_MAGIC,
            self.format_version,
            self.kdf_id,
            self.cipher_id,
            self.flags,
            self.scrypt_n,
            self.scrypt_r,
            self.scrypt_p,
            self.salt,
            self.nonce,
            self.plaintext_size,
        )


@dataclass(frozen=True, slots=True)
class EncryptedBackupResult:
    path: Path
    package_sha256: str
    plaintext_sha256: str
    header: EncryptedBackupHeader


@dataclass(frozen=True, slots=True)
class DecryptedBackupResult:
    path: Path
    plaintext_sha256: str
    header: EncryptedBackupHeader


def _invalid_encrypted_backup() -> BackupCryptoError:
    return BackupCryptoError(
        "invalid_encrypted_backup",
        "The encrypted backup could not be authenticated",
    )


def _password_bytes(password: PasswordMaterial) -> bytearray:
    if isinstance(password, str):
        encoded = bytearray(password.encode("utf-8"))
    elif isinstance(password, bytes | bytearray | memoryview):
        encoded = bytearray(password)
    else:
        raise BackupCryptoError("invalid_password", "The backup password is invalid")
    if not encoded or len(encoded) > MAX_PASSWORD_BYTES:
        _clear(encoded)
        raise BackupCryptoError("invalid_password", "The backup password is invalid")
    return encoded


def _clear(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0


def _derive_key(password: PasswordMaterial, header: EncryptedBackupHeader) -> bytearray:
    encoded = _password_bytes(password)
    try:
        derived = Scrypt(
            salt=header.salt,
            length=AES_KEY_BYTES,
            n=header.scrypt_n,
            r=header.scrypt_r,
            p=header.scrypt_p,
        ).derive(encoded)
        return bytearray(derived)
    finally:
        _clear(encoded)


def _validate_header(header: EncryptedBackupHeader) -> None:
    if (
        header.format_version != ENCRYPTED_BACKUP_VERSION
        or header.kdf_id != KDF_SCRYPT
        or header.cipher_id != CIPHER_AES_256_GCM
        or header.flags != 0
        or header.scrypt_n != SCRYPT_N
        or header.scrypt_r != SCRYPT_R
        or header.scrypt_p != SCRYPT_P
    ):
        raise _invalid_encrypted_backup()


def read_encrypted_header(path: Path) -> EncryptedBackupHeader:
    """Parse and strictly validate the public wrapper header."""

    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(_HEADER.size)
        if len(raw) != _HEADER.size:
            raise _invalid_encrypted_backup()
        unpacked = _HEADER.unpack(raw)
        if unpacked[0] != ENCRYPTED_BACKUP_MAGIC:
            raise _invalid_encrypted_backup()
        header = EncryptedBackupHeader(
            format_version=unpacked[1],
            kdf_id=unpacked[2],
            cipher_id=unpacked[3],
            flags=unpacked[4],
            scrypt_n=unpacked[5],
            scrypt_r=unpacked[6],
            scrypt_p=unpacked[7],
            salt=unpacked[8],
            nonce=unpacked[9],
            plaintext_size=unpacked[10],
        )
        _validate_header(header)
        return header
    except BackupCryptoError:
        raise
    except (OSError, struct.error, ValueError, OverflowError):
        raise _invalid_encrypted_backup() from None


def is_encrypted_backup(path: Path) -> bool:
    try:
        with Path(path).open("rb") as stream:
            return stream.read(len(ENCRYPTED_BACKUP_MAGIC)) == ENCRYPTED_BACKUP_MAGIC
    except OSError:
        return False


def _regular_source(path: Path) -> tuple[Path, os.stat_result]:
    supplied = Path(path)
    try:
        if supplied.is_symlink() or supplied.is_junction():
            raise BackupCryptoError("invalid_source", "The backup source is unavailable")
        candidate = supplied.resolve(strict=True)
        details = os.lstat(candidate)
    except BackupCryptoError:
        raise
    except OSError:
        raise BackupCryptoError("invalid_source", "The backup source is unavailable") from None
    if not stat.S_ISREG(details.st_mode):
        raise BackupCryptoError("invalid_source", "The backup source is unavailable")
    return candidate, details


def _prepare_destination(path: Path) -> Path:
    supplied = Path(path)
    if supplied.name in {"", ".", ".."} or "\x00" in supplied.name:
        raise BackupCryptoError("invalid_destination", "The backup destination is invalid")
    try:
        if supplied.exists() or supplied.is_symlink() or supplied.is_junction():
            raise BackupCryptoError(
                "destination_exists",
                "The backup destination already exists",
            )
        supplied.parent.mkdir(parents=True, exist_ok=True)
        candidate = supplied.parent.resolve(strict=True) / supplied.name
    except BackupCryptoError:
        raise
    except OSError:
        raise BackupCryptoError(
            "invalid_destination",
            "The backup destination is invalid",
        ) from None
    if candidate.exists() or candidate.is_symlink() or candidate.is_junction():
        raise BackupCryptoError("destination_exists", "The backup destination already exists")
    return candidate


def _partial_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.{uuid4().hex}.part"


def _hash_file(path: Path, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_without_overwrite(source: Path, destination: Path) -> None:
    if os.name == "nt":
        os.rename(source, destination)
        return
    os.link(source, destination)  # pragma: no cover - POSIX release path
    source.unlink()  # pragma: no cover - POSIX release path


def _check_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise BackupCryptoError("cancelled", "Backup encryption was cancelled")


def encrypt_backup(
    source: Path,
    destination: Path,
    *,
    password: PasswordMaterial,
    cancelled: Callable[[], bool] = lambda: False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> EncryptedBackupResult:
    """Encrypt one archive incrementally and publish only an authenticated wrapper."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    source_path, before = _regular_source(source)
    destination_path = _prepare_destination(destination)
    _check_cancelled(cancelled)
    header = EncryptedBackupHeader(
        format_version=ENCRYPTED_BACKUP_VERSION,
        kdf_id=KDF_SCRYPT,
        cipher_id=CIPHER_AES_256_GCM,
        flags=0,
        scrypt_n=SCRYPT_N,
        scrypt_r=SCRYPT_R,
        scrypt_p=SCRYPT_P,
        salt=os.urandom(SCRYPT_SALT_BYTES),
        nonce=os.urandom(GCM_NONCE_BYTES),
        plaintext_size=before.st_size,
    )
    key = _derive_key(password, header)
    partial = _partial_path(destination_path)
    plaintext_digest = sha256()
    try:
        encryptor = Cipher(
            algorithms.AES(key),
            modes.GCM(header.nonce),
        ).encryptor()
        header_bytes = header.canonical_bytes()
        encryptor.authenticate_additional_data(header_bytes)
        with source_path.open("rb") as input_stream, partial.open("xb") as output_stream:
            output_stream.write(header_bytes)
            while chunk := input_stream.read(chunk_size):
                _check_cancelled(cancelled)
                plaintext_digest.update(chunk)
                output_stream.write(encryptor.update(chunk))
            output_stream.write(encryptor.finalize())
            output_stream.write(encryptor.tag)
            output_stream.flush()
            os.fsync(output_stream.fileno())
            descriptor = os.fstat(input_stream.fileno())
        after = os.lstat(source_path)
        identity_before = (before.st_size, before.st_mtime_ns, before.st_ino)
        identity_descriptor = (
            descriptor.st_size,
            descriptor.st_mtime_ns,
            descriptor.st_ino,
        )
        identity_after = (after.st_size, after.st_mtime_ns, after.st_ino)
        if identity_before != identity_descriptor or identity_before != identity_after:
            raise BackupCryptoError("source_changed", "The backup changed during encryption")
        _check_cancelled(cancelled)
        package_sha256 = _hash_file(partial, chunk_size=chunk_size)
        _publish_without_overwrite(partial, destination_path)
        return EncryptedBackupResult(
            path=destination_path,
            package_sha256=package_sha256,
            plaintext_sha256=plaintext_digest.hexdigest(),
            header=header,
        )
    except BackupCryptoError:
        raise
    except FileExistsError:
        raise BackupCryptoError(
            "destination_exists",
            "The backup destination already exists",
        ) from None
    except (OSError, ValueError, OverflowError):
        raise BackupCryptoError("encryption_failed", "The backup could not be encrypted") from None
    finally:
        _clear(key)
        partial.unlink(missing_ok=True)


def decrypt_backup(
    source: Path,
    destination: Path,
    *,
    password: PasswordMaterial,
    cancelled: Callable[[], bool] = lambda: False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> DecryptedBackupResult:
    """Authenticate and decrypt to a private partial file before publishing plaintext."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    source_path, _before = _regular_source(source)
    destination_path = _prepare_destination(destination)
    partial = _partial_path(destination_path)
    key: bytearray | None = None
    try:
        _check_cancelled(cancelled)
        header = read_encrypted_header(source_path)
        expected_size = header.byte_size + header.plaintext_size + GCM_TAG_BYTES
        if source_path.stat().st_size != expected_size:
            raise _invalid_encrypted_backup()
        with source_path.open("rb") as input_stream:
            input_stream.seek(-GCM_TAG_BYTES, os.SEEK_END)
            tag = input_stream.read(GCM_TAG_BYTES)
            if len(tag) != GCM_TAG_BYTES:
                raise _invalid_encrypted_backup()
            input_stream.seek(header.byte_size)
            key = _derive_key(password, header)
            decryptor = Cipher(
                algorithms.AES(key),
                modes.GCM(header.nonce, tag),
            ).decryptor()
            decryptor.authenticate_additional_data(header.canonical_bytes())
            remaining = header.plaintext_size
            digest = sha256()
            written = 0
            with partial.open("xb") as output_stream:
                while remaining:
                    _check_cancelled(cancelled)
                    chunk = input_stream.read(min(chunk_size, remaining))
                    if not chunk:
                        raise _invalid_encrypted_backup()
                    remaining -= len(chunk)
                    plaintext = decryptor.update(chunk)
                    output_stream.write(plaintext)
                    digest.update(plaintext)
                    written += len(plaintext)
                final = decryptor.finalize()
                output_stream.write(final)
                digest.update(final)
                written += len(final)
                output_stream.flush()
                os.fsync(output_stream.fileno())
        _check_cancelled(cancelled)
        _publish_without_overwrite(partial, destination_path)
        return DecryptedBackupResult(
            path=destination_path,
            plaintext_sha256=digest.hexdigest(),
            header=header,
        )
    except BackupCryptoError:
        raise
    except FileExistsError:
        raise BackupCryptoError(
            "destination_exists",
            "The backup destination already exists",
        ) from None
    except (InvalidTag, OSError, ValueError, OverflowError):
        raise _invalid_encrypted_backup() from None
    finally:
        if key is not None:
            _clear(key)
        partial.unlink(missing_ok=True)


__all__ = [
    "AES_KEY_BYTES",
    "CIPHER_AES_256_GCM",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_SECRET_LEASE_SECONDS",
    "DecryptedBackupResult",
    "ENCRYPTED_BACKUP_MAGIC",
    "ENCRYPTED_BACKUP_VERSION",
    "EncryptedBackupHeader",
    "EncryptedBackupResult",
    "GCM_NONCE_BYTES",
    "GCM_TAG_BYTES",
    "KDF_SCRYPT",
    "MAX_PASSWORD_BYTES",
    "PasswordLease",
    "PasswordLeaseStore",
    "PasswordMaterial",
    "SCRYPT_N",
    "SCRYPT_P",
    "SCRYPT_R",
    "SCRYPT_SALT_BYTES",
    "BackupCryptoError",
    "decrypt_backup",
    "encrypt_backup",
    "is_encrypted_backup",
    "read_encrypted_header",
]
