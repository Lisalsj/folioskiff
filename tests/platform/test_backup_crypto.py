from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest


def _payload() -> bytes:
    return (b"paper-reader encrypted backup evidence\n" * 70_000) + b"tail"


def test_encrypted_backup_uses_locked_scrypt_and_aes_gcm_header(tmp_path: Path) -> None:
    from paper_reader.backups.crypto import (
        AES_KEY_BYTES,
        GCM_NONCE_BYTES,
        GCM_TAG_BYTES,
        SCRYPT_N,
        SCRYPT_P,
        SCRYPT_R,
        SCRYPT_SALT_BYTES,
        encrypt_backup,
        read_encrypted_header,
    )

    source = tmp_path / "plain.backup"
    encrypted = tmp_path / "portable.backup.enc"
    source.write_bytes(_payload())

    result = encrypt_backup(source, encrypted, password="correct horse battery staple")
    header = read_encrypted_header(encrypted)

    assert result.path == encrypted
    assert result.header == header
    assert header.scrypt_n == SCRYPT_N == 2**15
    assert header.scrypt_r == SCRYPT_R == 8
    assert header.scrypt_p == SCRYPT_P == 1
    assert len(header.salt) == SCRYPT_SALT_BYTES == 16
    assert len(header.nonce) == GCM_NONCE_BYTES == 12
    assert AES_KEY_BYTES == 32
    assert GCM_TAG_BYTES == 16
    assert header.plaintext_size == source.stat().st_size
    assert header.canonical_bytes() == encrypted.read_bytes()[: header.byte_size]
    assert len(result.package_sha256) == 64


def test_streaming_encryption_round_trip_is_randomized(tmp_path: Path) -> None:
    from paper_reader.backups.crypto import decrypt_backup, encrypt_backup

    source = tmp_path / "plain.backup"
    first = tmp_path / "first.backup.enc"
    second = tmp_path / "second.backup.enc"
    restored = tmp_path / "restored.backup"
    source.write_bytes(_payload())

    first_result = encrypt_backup(source, first, password="library password")
    second_result = encrypt_backup(source, second, password="library password")
    decrypt_result = decrypt_backup(first, restored, password="library password")

    assert restored.read_bytes() == source.read_bytes()
    assert first.read_bytes() != second.read_bytes()
    assert first_result.header.salt != second_result.header.salt
    assert first_result.header.nonce != second_result.header.nonce
    assert decrypt_result.plaintext_sha256 == first_result.plaintext_sha256
    assert source.read_bytes()[:128] not in first.read_bytes()


@pytest.mark.parametrize("password", ["", "x" * 4_097])
def test_password_policy_rejects_empty_or_oversized_values_without_output(
    tmp_path: Path,
    password: str,
) -> None:
    from paper_reader.backups.crypto import BackupCryptoError, encrypt_backup

    source = tmp_path / "plain.backup"
    destination = tmp_path / "rejected.backup.enc"
    source.write_bytes(b"backup")

    with pytest.raises(BackupCryptoError) as captured:
        encrypt_backup(source, destination, password=password)

    assert captured.value.code == "invalid_password"
    assert str(captured.value) == "The backup password is invalid"
    if password:
        assert password not in str(captured.value)
    assert not destination.exists()
    assert not tuple(tmp_path.glob("*.part"))


@pytest.mark.parametrize(
    "mutation",
    ["wrong_password", "header", "ciphertext", "tag", "truncate"],
)
def test_authentication_failures_are_stable_redacted_and_publish_no_plaintext(
    tmp_path: Path,
    mutation: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from paper_reader.backups.crypto import (
        BackupCryptoError,
        decrypt_backup,
        encrypt_backup,
        read_encrypted_header,
    )

    secret = "CANARY-password-never-persist-77421"
    source = tmp_path / "plain.backup"
    encrypted = tmp_path / "encrypted.backup"
    restored = tmp_path / "restored.backup"
    source.write_bytes(_payload())
    encrypt_backup(source, encrypted, password=secret)
    password = secret
    if mutation == "wrong_password":
        password = "wrong password"
    else:
        data = bytearray(encrypted.read_bytes())
        header = read_encrypted_header(encrypted)
        if mutation == "header":
            data[header.byte_size - 1] ^= 0x01
        elif mutation == "ciphertext":
            data[header.byte_size + 17] ^= 0x01
        elif mutation == "tag":
            data[-1] ^= 0x01
        else:
            del data[-9:]
        encrypted.write_bytes(data)

    with caplog.at_level(logging.DEBUG), pytest.raises(BackupCryptoError) as captured:
        decrypt_backup(encrypted, restored, password=password)

    assert captured.value.code == "invalid_encrypted_backup"
    assert str(captured.value) == "The encrypted backup could not be authenticated"
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)
    assert secret not in caplog.text
    assert not restored.exists()
    assert not tuple(tmp_path.glob("*.part"))


def test_cancelled_crypto_cleans_partial_files_and_never_overwrites(tmp_path: Path) -> None:
    from paper_reader.backups.crypto import BackupCryptoError, encrypt_backup

    source = tmp_path / "plain.backup"
    destination = tmp_path / "existing.backup"
    source.write_bytes(_payload())
    destination.write_bytes(b"another backup")

    with pytest.raises(BackupCryptoError) as existing:
        encrypt_backup(source, destination, password="safe password")
    assert existing.value.code == "destination_exists"
    assert destination.read_bytes() == b"another backup"

    destination.unlink()
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(BackupCryptoError) as stopped:
        encrypt_backup(
            source,
            destination,
            password="safe password",
            cancelled=cancelled,
            chunk_size=32 * 1024,
        )
    assert stopped.value.code == "cancelled"
    assert not destination.exists()
    assert not tuple(tmp_path.glob("*.part"))


def test_password_lease_is_one_time_memory_only_and_never_persisted(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from paper_reader.backups.crypto import (
        BackupCryptoError,
        PasswordLeaseStore,
        encrypt_backup,
    )
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.jobs.events import encode_sse_event
    from paper_reader.jobs.repository import JobRepository

    secret = "CANARY-one-time-password-99173"
    source = tmp_path / "plain.backup"
    encrypted = tmp_path / "encrypted.backup"
    database = tmp_path / "jobs.sqlite3"
    source.write_bytes(b"portable evidence")
    engine = create_sqlite_engine(database)
    Base.metadata.create_all(engine)
    repository = JobRepository(create_session_factory(engine))
    leases = PasswordLeaseStore()
    lease_id = leases.issue(secret)

    job = repository.create(
        kind="backup.portable",
        payload={"password_lease_id": lease_id},
    )
    event = repository.append_event(
        job_id=job.id,
        event_type="backup.password.accepted",
        progress=1,
        payload={"password_lease_id": lease_id},
    )
    with caplog.at_level(logging.INFO):
        logging.getLogger("paper_reader.backups").info("accepted lease %s", lease_id)

    with leases.consume(lease_id) as password_material:
        encrypt_backup(source, encrypted, password=password_material)
    assert password_material == bytearray()
    with pytest.raises(BackupCryptoError) as reused:
        leases.consume(lease_id)

    engine.dispose()
    persisted = database.read_bytes()
    wal = database.with_name(f"{database.name}-wal")
    if wal.exists():
        persisted += wal.read_bytes()
    assert reused.value.code == "secret_lease_unavailable"
    assert secret not in repr(leases)
    assert secret not in repr(reused.value)
    assert secret not in encode_sse_event(event)
    assert secret.encode() not in persisted
    assert secret not in caplog.text


def test_password_lease_expiry_discard_close_and_token_policy() -> None:
    from paper_reader.backups.crypto import BackupCryptoError, PasswordLeaseStore

    now = [10.0]
    with pytest.raises(ValueError, match="positive"):
        PasswordLeaseStore(ttl_seconds=0)

    invalid_tokens = PasswordLeaseStore(token_factory=lambda: "short")
    with pytest.raises(BackupCryptoError) as unavailable:
        invalid_tokens.issue("secret")
    assert unavailable.value.code == "secret_lease_unavailable"

    tokens: Iterator[str] = iter(("a" * 16, "a" * 16, "b" * 16))
    leases = PasswordLeaseStore(
        ttl_seconds=5,
        clock=lambda: now[0],
        token_factory=lambda: next(tokens),
    )
    first_id = leases.issue(memoryview(b"first secret"))
    second_id = leases.issue(b"second secret")
    assert first_id != second_id
    leases.discard(first_id)
    with pytest.raises(BackupCryptoError):
        leases.consume(first_id)

    now[0] = 16.0
    with pytest.raises(BackupCryptoError):
        leases.consume(second_id)
    with pytest.raises(BackupCryptoError):
        leases.consume("")

    third = PasswordLeaseStore(token_factory=lambda: "c" * 16)
    third_id = third.issue("third secret")
    lease = third.consume(third_id)
    assert "third secret" not in repr(lease)
    lease.clear()
    with pytest.raises(BackupCryptoError):
        lease.__enter__()
    fourth_id = third.issue("fourth secret")
    third.close()
    with pytest.raises(BackupCryptoError):
        third.consume(fourth_id)


def test_crypto_rejects_invalid_material_sources_destinations_and_chunk_sizes(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.crypto import (
        BackupCryptoError,
        PasswordLeaseStore,
        decrypt_backup,
        encrypt_backup,
        is_encrypted_backup,
    )

    source = tmp_path / "plain.backup"
    source.write_bytes(b"plain")
    with pytest.raises(BackupCryptoError) as invalid_material:
        PasswordLeaseStore().issue(cast(Any, object()))
    assert invalid_material.value.code == "invalid_password"

    for invalid_source in (tmp_path / "missing", tmp_path):
        with pytest.raises(BackupCryptoError) as rejected:
            encrypt_backup(
                invalid_source,
                tmp_path / f"{invalid_source.name}.enc",
                password="password",
            )
        assert rejected.value.code == "invalid_source"

    with pytest.raises(BackupCryptoError) as bad_name:
        encrypt_backup(source, Path(""), password="password")
    assert bad_name.value.code == "invalid_destination"
    blocking_parent = tmp_path / "parent-is-file"
    blocking_parent.write_bytes(b"file")
    with pytest.raises(BackupCryptoError) as bad_parent:
        encrypt_backup(
            source,
            blocking_parent / "child.enc",
            password="password",
        )
    assert bad_parent.value.code == "invalid_destination"

    with pytest.raises(ValueError, match="positive"):
        encrypt_backup(source, tmp_path / "invalid-chunk.enc", password="password", chunk_size=0)
    with pytest.raises(ValueError, match="positive"):
        decrypt_backup(source, tmp_path / "invalid-chunk.out", password="password", chunk_size=0)
    assert is_encrypted_backup(tmp_path / "missing") is False


@pytest.mark.parametrize(
    ("offset", "replacement"),
    [
        (16, 2),
        (17, 2),
        (18, 2),
        (19, 1),
        (23, 1),
        (27, 9),
        (31, 2),
    ],
)
def test_encrypted_header_rejects_every_profile_downgrade(
    tmp_path: Path,
    offset: int,
    replacement: int,
) -> None:
    from paper_reader.backups.crypto import BackupCryptoError, encrypt_backup, read_encrypted_header

    source = tmp_path / "plain.backup"
    encrypted = tmp_path / "encrypted.backup"
    source.write_bytes(b"plain")
    encrypt_backup(source, encrypted, password="password")
    payload = bytearray(encrypted.read_bytes())
    payload[offset] = replacement
    encrypted.write_bytes(payload)

    with pytest.raises(BackupCryptoError) as captured:
        read_encrypted_header(encrypted)
    assert captured.value.code == "invalid_encrypted_backup"


@pytest.mark.parametrize("payload", [b"short", b"X" * 80])
def test_encrypted_header_rejects_short_or_unknown_magic(
    tmp_path: Path,
    payload: bytes,
) -> None:
    from paper_reader.backups.crypto import BackupCryptoError, read_encrypted_header

    candidate = tmp_path / "header.backup"
    candidate.write_bytes(payload)
    with pytest.raises(BackupCryptoError) as captured:
        read_encrypted_header(candidate)
    assert captured.value.code == "invalid_encrypted_backup"


def test_crypto_maps_publish_races_io_failures_and_source_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.backups import crypto
    from paper_reader.backups.crypto import BackupCryptoError, decrypt_backup, encrypt_backup

    source = tmp_path / "plain.backup"
    source.write_bytes(b"plain evidence")
    encrypted = tmp_path / "encrypted.backup"
    encrypt_backup(source, encrypted, password="password")

    def file_exists(_source: Path, _destination: Path) -> None:
        raise FileExistsError("injected race")

    monkeypatch.setattr(crypto, "_publish_without_overwrite", file_exists)
    with pytest.raises(BackupCryptoError) as encrypt_race:
        encrypt_backup(source, tmp_path / "race.enc", password="password")
    assert encrypt_race.value.code == "destination_exists"
    with pytest.raises(BackupCryptoError) as decrypt_race:
        decrypt_backup(encrypted, tmp_path / "race.out", password="password")
    assert decrypt_race.value.code == "destination_exists"

    def io_failure(_source: Path, _destination: Path) -> None:
        raise OSError("injected io failure")

    monkeypatch.setattr(crypto, "_publish_without_overwrite", io_failure)
    with pytest.raises(BackupCryptoError) as encrypt_io:
        encrypt_backup(source, tmp_path / "io.enc", password="password")
    assert encrypt_io.value.code == "encryption_failed"
    with pytest.raises(BackupCryptoError) as decrypt_io:
        decrypt_backup(encrypted, tmp_path / "io.out", password="password")
    assert decrypt_io.value.code == "invalid_encrypted_backup"

    monkeypatch.setattr(crypto, "_publish_without_overwrite", os.rename)
    original_lstat = crypto.os.lstat
    source_calls = 0

    def changed_lstat(path: os.PathLike[str] | str) -> os.stat_result | SimpleNamespace:
        nonlocal source_calls
        result = original_lstat(path)
        if Path(path) == source.resolve():
            source_calls += 1
            if source_calls >= 2:
                return SimpleNamespace(
                    st_mode=result.st_mode,
                    st_size=result.st_size,
                    st_mtime_ns=result.st_mtime_ns + 1,
                    st_ino=result.st_ino,
                )
        return result

    monkeypatch.setattr(crypto.os, "lstat", changed_lstat)
    with pytest.raises(BackupCryptoError) as changed:
        encrypt_backup(source, tmp_path / "changed.enc", password="password")
    assert changed.value.code == "source_changed"
    assert not tuple(tmp_path.glob("*.part"))
