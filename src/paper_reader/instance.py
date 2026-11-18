"""Kernel-owned single-instance lock and untrusted activation state."""

from __future__ import annotations

import errno
import http.client
import json
import os
import re
import stat
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from secrets import token_hex
from time import monotonic, sleep
from typing import Any, cast

_INSTANCE_SCHEMA = "paper-reader-instance-v1"
_INSTANCE_FIELDS = {
    "schema_version",
    "pid",
    "created_at",
    "port",
    "activation_secret",
}
_SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_MAX_STATE_BYTES = 4096


class InstanceLockError(RuntimeError):
    """The process cannot safely establish single-instance ownership."""


class InvalidInstanceStateError(RuntimeError):
    """The untrusted instance activation file is absent, unsafe, or malformed."""


class ActivationError(RuntimeError):
    """An existing instance could not safely mint a browser entry grant."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class InstanceState:
    schema_version: str
    pid: int
    created_at: datetime
    port: int
    activation_secret: str


@dataclass(frozen=True, slots=True)
class ActivationResult:
    url: str = field(repr=False)
    pid: int
    expires_in_seconds: int


class ApplicationInstanceLock:
    """Own one byte in a profile-local file for the full process lifetime."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root).absolute()
        self.lock_path = self.state_root / "instance.lock"
        self.state_path = self.state_root / "instance.json"
        self._file_descriptor: int | None = None
        self._published_bytes: bytes | None = None
        self._alias_state_root: Path | None = None
        self._alias_published_bytes: bytes | None = None
        self._guard = threading.RLock()

    @property
    def file_descriptor(self) -> int | None:
        return self._file_descriptor

    def acquire(self) -> bool:
        """Attempt ownership; a normal contention result is ``False``."""

        with self._guard:
            if self._file_descriptor is not None:
                return True
            self._prepare_state_root()
            descriptor = _open_private_regular_file(self.lock_path)
            try:
                os.set_inheritable(descriptor, False)
                if os.fstat(descriptor).st_size < 1:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                try:
                    _lock_first_byte(descriptor)
                except OSError as error:
                    if error.errno not in {
                        errno.EACCES,
                        errno.EAGAIN,
                        errno.EDEADLK,
                        errno.EPERM,
                    }:
                        raise
                    os.close(descriptor)
                    return False
                self._file_descriptor = descriptor
                return True
            except BaseException:
                if descriptor != self._file_descriptor:
                    with suppress(OSError):
                        os.close(descriptor)
                raise

    def publish(self, *, port: int, activation_secret: str) -> InstanceState:
        """Atomically publish activation coordinates after the server is ready."""

        with self._guard:
            if self._file_descriptor is None:
                raise InstanceLockError("instance ownership is required before publish")
            state = InstanceState(
                schema_version=_INSTANCE_SCHEMA,
                pid=os.getpid(),
                created_at=datetime.now(UTC),
                port=_validate_port(port),
                activation_secret=_validate_secret(activation_secret),
            )
            payload = {
                "schema_version": state.schema_version,
                "pid": state.pid,
                "created_at": state.created_at.isoformat().replace("+00:00", "Z"),
                "port": state.port,
                "activation_secret": state.activation_secret,
            }
            encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
            if len(encoded) > _MAX_STATE_BYTES:  # pragma: no cover - fixed schema invariant
                raise InstanceLockError("instance state exceeds its private file budget")
            _atomic_private_write(self.state_path, encoded)
            self._published_bytes = encoded
            if self._alias_state_root is not None:
                alias_path = self._alias_state_root / "instance.json"
                self._alias_state_root.mkdir(parents=True, exist_ok=True)
                _atomic_private_write(alias_path, encoded)
                self._alias_published_bytes = encoded
            return state

    def set_alias_root(self, state_root: Path) -> None:
        """Mirror transient activation state into the selected data-root state.

        The bootstrap lock remains canonical during migration.  The mirror is
        removed on close, so an old state directory retains only its redirect
        after a clean migration.
        """
        root = Path(state_root).absolute()
        with self._guard:
            if root == self.state_root:
                if self._alias_state_root is not None and self._alias_published_bytes is not None:
                    _unlink_if_exact_regular_file(
                        self._alias_state_root / "instance.json",
                        self._alias_published_bytes,
                    )
                self._alias_state_root = None
                self._alias_published_bytes = None
                return
            if (
                self._alias_state_root is not None
                and self._alias_state_root != root
                and self._alias_published_bytes is not None
            ):
                _unlink_if_exact_regular_file(
                    self._alias_state_root / "instance.json",
                    self._alias_published_bytes,
                )
            self._alias_state_root = root
            self._alias_published_bytes = None

    def close(self) -> None:
        with self._guard:
            descriptor = self._file_descriptor
            if descriptor is None:
                return
            if self._published_bytes is not None:
                _unlink_if_exact_regular_file(self.state_path, self._published_bytes)
                self._published_bytes = None
            if self._alias_state_root is not None and self._alias_published_bytes is not None:
                _unlink_if_exact_regular_file(
                    self._alias_state_root / "instance.json",
                    self._alias_published_bytes,
                )
                self._alias_published_bytes = None
            try:
                _unlock_first_byte(descriptor)
            finally:
                os.close(descriptor)
                self._file_descriptor = None

    def __enter__(self) -> ApplicationInstanceLock:
        if not self.acquire():
            raise InstanceLockError("another paper-reader instance owns the profile lock")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _prepare_state_root(self) -> None:
        if _has_reparse_ancestor(self.state_root):
            raise InstanceLockError("instance state root cannot be a link or reparse point")
        try:
            self.state_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise InstanceLockError("instance state root cannot be created") from error
        if not self.state_root.is_dir() or _has_reparse_ancestor(self.state_root):
            raise InstanceLockError("instance state root is not a safe directory")


def read_instance_state(state_root: Path) -> InstanceState:
    """Read a bounded, closed activation state without treating it as ownership."""

    path = Path(state_root).absolute() / "instance.json"
    try:
        if _has_reparse_ancestor(path):
            raise InvalidInstanceStateError("instance state link or reparse point is forbidden")
        descriptor = _open_existing_regular_file(path)
        try:
            size = os.fstat(descriptor).st_size
            if not 1 <= size <= _MAX_STATE_BYTES:
                raise InvalidInstanceStateError("instance state size is invalid")
            chunks: list[bytes] = []
            remaining = _MAX_STATE_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(remaining, 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(data) > _MAX_STATE_BYTES:
            raise InvalidInstanceStateError("instance state size is invalid")
        raw = cast(Any, json.loads(data.decode("utf-8")))
    except InvalidInstanceStateError:
        raise
    except (OSError, UnicodeError, ValueError, InstanceLockError) as error:
        raise InvalidInstanceStateError("instance state cannot be read") from error

    if not isinstance(raw, dict) or set(raw) != _INSTANCE_FIELDS:
        raise InvalidInstanceStateError("instance state fields are incomplete or unknown")
    schema = raw.get("schema_version")
    if schema != _INSTANCE_SCHEMA:
        raise InvalidInstanceStateError("instance state schema is unsupported")
    pid = raw.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or not 1 <= pid <= 2**32 - 1:
        raise InvalidInstanceStateError("instance state pid is invalid")
    port = raw.get("port")
    try:
        validated_port = _validate_port(port)
    except InstanceLockError as error:
        raise InvalidInstanceStateError("instance state port is invalid") from error
    secret = raw.get("activation_secret")
    try:
        validated_secret = _validate_secret(secret)
    except InstanceLockError as error:
        raise InvalidInstanceStateError("instance state activation secret is invalid") from error
    created_raw = raw.get("created_at")
    if not isinstance(created_raw, str) or len(created_raw) > 64:
        raise InvalidInstanceStateError("instance state created_at is invalid")
    try:
        created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidInstanceStateError("instance state created_at is invalid") from error
    if created_at.tzinfo is None:
        raise InvalidInstanceStateError("instance state created_at is invalid")
    return InstanceState(
        schema_version=schema,
        pid=pid,
        created_at=created_at.astimezone(UTC),
        port=validated_port,
        activation_secret=validated_secret,
    )


def activate_existing_instance(
    state_root: Path,
    *,
    timeout_seconds: float = 2,
) -> ActivationResult:
    """Exchange the private activation secret over a direct loopback connection."""

    if not 0 < timeout_seconds <= 10:
        raise ValueError("activation timeout is invalid")
    deadline = monotonic() + timeout_seconds
    last_error: ActivationError | None = None
    while True:
        remaining = deadline - monotonic()
        try:
            state = read_instance_state(state_root)
            return _activate_state(state, timeout_seconds=max(0.01, min(0.5, remaining)))
        except InvalidInstanceStateError:
            last_error = ActivationError(
                "instance_state_invalid",
                "the existing instance state is invalid",
            )
        except ActivationError as error:
            if error.code != "instance_unreachable":
                raise
            last_error = error
        remaining = deadline - monotonic()
        if remaining <= 0:
            assert last_error is not None
            raise last_error
        sleep(min(0.05, remaining))


def _activate_state(state: InstanceState, *, timeout_seconds: float) -> ActivationResult:
    body = json.dumps(
        {"activation_secret": state.activation_secret},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        state.port,
        timeout=timeout_seconds,
    )
    try:
        connection.request(
            "POST",
            "/api/v1/session/activate",
            body=body,
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-store",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        payload_bytes = response.read(8193)
    except (OSError, TimeoutError, http.client.HTTPException) as error:
        raise ActivationError(
            "instance_unreachable",
            "the existing instance is unreachable",
        ) from error
    finally:
        connection.close()
    if response.status != 200 or len(payload_bytes) > 8192:
        raise ActivationError(
            "instance_activation_rejected",
            "the existing instance rejected activation",
        )
    content_type = response.headers.get_content_type()
    if content_type != "application/json":
        raise ActivationError(
            "instance_response_invalid",
            "the existing instance returned an invalid activation response",
        )
    try:
        payload = cast(Any, json.loads(payload_bytes.decode("utf-8")))
    except (UnicodeError, ValueError) as error:
        raise ActivationError(
            "instance_response_invalid",
            "the existing instance returned an invalid activation response",
        ) from error
    if not isinstance(payload, dict) or set(payload) != {
        "bootstrap_token",
        "expires_in_seconds",
        "pid",
    }:
        raise ActivationError(
            "instance_response_invalid",
            "the existing instance returned an invalid activation response",
        )
    token = payload.get("bootstrap_token")
    expires = payload.get("expires_in_seconds")
    pid = payload.get("pid")
    if (
        not isinstance(token, str)
        or _SECRET_PATTERN.fullmatch(token) is None
        or isinstance(expires, bool)
        or not isinstance(expires, int)
        or not 1 <= expires <= 300
        or isinstance(pid, bool)
        or not isinstance(pid, int)
        or not 1 <= pid <= 2**32 - 1
    ):
        raise ActivationError(
            "instance_response_invalid",
            "the existing instance returned an invalid activation response",
        )
    if pid != state.pid:
        raise ActivationError(
            "instance_identity_mismatch",
            "the existing instance identity does not match",
        )
    return ActivationResult(
        url=f"http://127.0.0.1:{state.port}/#bootstrap={token}",
        pid=pid,
        expires_in_seconds=expires,
    )


def _validate_port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise InstanceLockError("instance port is invalid")
    return value


def _validate_secret(value: object) -> str:
    if not isinstance(value, str) or _SECRET_PATTERN.fullmatch(value) is None:
        raise InstanceLockError("instance activation secret is invalid")
    return value


def _is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        return bool(checker()) if checker is not None else False
    except OSError:
        return True


def _has_reparse_ancestor(path: Path) -> bool:
    current = path
    while True:
        if _is_link_or_reparse(current):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _open_private_regular_file(path: Path) -> int:
    if path.exists() and _is_link_or_reparse(path):
        raise InstanceLockError("instance lock cannot be a link or reparse point")
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise InstanceLockError("instance lock cannot be opened") from error
    try:
        _assert_descriptor_matches_regular_path(descriptor, path)
        os.set_inheritable(descriptor, False)
        restrict_file_to_current_user(path)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_existing_regular_file(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        _assert_descriptor_matches_regular_path(descriptor, path)
        os.set_inheritable(descriptor, False)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _assert_descriptor_matches_regular_path(descriptor: int, path: Path) -> None:
    opened = os.fstat(descriptor)
    linked = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(linked.st_mode):
        raise InstanceLockError("instance file is not regular")
    if (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino):
        raise InstanceLockError("instance file changed while opening")


def _atomic_private_write(path: Path, payload: bytes) -> None:
    if path.exists() and _is_link_or_reparse(path):
        raise InstanceLockError("instance state cannot replace a link or reparse point")
    temporary = path.with_name(f".{path.name}.{token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    descriptor: int | None = None
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
        _replace_private_file(temporary, path)
    except OSError as error:
        raise InstanceLockError("instance state cannot be published") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()


def _replace_private_file(source: Path, destination: Path) -> None:
    deadline = monotonic() + 1
    while True:
        if _has_reparse_ancestor(destination):
            raise InstanceLockError("instance state cannot replace a reparse point")
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            transient = os.name == "nt" and getattr(error, "winerror", None) in {5, 32}
            if not transient or monotonic() >= deadline:
                raise
            sleep(0.02)


def _unlink_if_exact_regular_file(path: Path, expected: bytes) -> None:
    try:
        if _has_reparse_ancestor(path) or not path.is_file():
            return
        if path.stat().st_size > _MAX_STATE_BYTES or path.read_bytes() != expected:
            return
        path.unlink()
    except OSError:
        return


def _lock_first_byte(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return
    fcntl = import_module("fcntl")  # pragma: no cover - Windows is the v1 release target
    fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB, 1)


def _unlock_first_byte(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    fcntl = import_module("fcntl")  # pragma: no cover - Windows is the v1 release target
    fcntl.lockf(descriptor, fcntl.LOCK_UN, 1)


def restrict_file_to_current_user(path: Path) -> None:
    """Apply mode 0600 or a protected Windows DACL for the current user SID."""

    if os.name != "nt":  # pragma: no cover - Windows is the v1 release target
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR, follow_symlinks=False)
        return
    _restrict_windows_file_dacl(path)


def _restrict_windows_file_dacl(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _TokenUser(ctypes.Structure):
        _fields_ = [("User", _SidAndAttributes)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    TOKEN_QUERY = 0x0008
    TOKEN_USER_CLASS = 1
    ERROR_INSUFFICIENT_BUFFER = 122
    DACL_SECURITY_INFORMATION = 0x00000004
    PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.SetFileSecurityW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    advapi32.SetFileSecurityW.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        TOKEN_QUERY,
        ctypes.byref(token),
    ):
        raise InstanceLockError("instance file permissions cannot be restricted")
    sid_string = wintypes.LPWSTR()
    descriptor = ctypes.c_void_p()
    try:
        required = wintypes.DWORD()
        advapi32.GetTokenInformation(
            token,
            TOKEN_USER_CLASS,
            None,
            0,
            ctypes.byref(required),
        )
        if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER or required.value == 0:
            raise InstanceLockError("instance file permissions cannot be restricted")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token,
            TOKEN_USER_CLASS,
            buffer,
            required,
            ctypes.byref(required),
        ):
            raise InstanceLockError("instance file permissions cannot be restricted")
        token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        if not advapi32.ConvertSidToStringSidW(
            token_user.User.Sid,
            ctypes.byref(sid_string),
        ):
            raise InstanceLockError("instance file permissions cannot be restricted")
        sddl = f"D:P(A;;FA;;;{sid_string.value})"
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            1,
            ctypes.byref(descriptor),
            None,
        ):
            raise InstanceLockError("instance file permissions cannot be restricted")
        if not advapi32.SetFileSecurityW(
            str(path),
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            descriptor,
        ):
            raise InstanceLockError("instance file permissions cannot be restricted")
    finally:
        if descriptor:
            kernel32.LocalFree(descriptor)
        if sid_string:
            kernel32.LocalFree(sid_string)
        kernel32.CloseHandle(token)
