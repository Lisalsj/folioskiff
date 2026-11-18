from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _windows_file_sddl(path: Path) -> str:
    import ctypes
    from ctypes import wintypes

    dacl_security_information = 0x00000004
    sddl_revision_1 = 1
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    get_file_security = advapi32.GetFileSecurityW
    get_file_security.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_file_security.restype = wintypes.BOOL
    convert_descriptor = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert_descriptor.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    )
    convert_descriptor.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = (ctypes.c_void_p,)
    local_free.restype = ctypes.c_void_p

    required = wintypes.DWORD()
    get_file_security(str(path), dacl_security_information, None, 0, ctypes.byref(required))
    if required.value == 0:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(required.value)
    if not get_file_security(
        str(path),
        dacl_security_information,
        descriptor,
        required.value,
        ctypes.byref(required),
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    string_descriptor = ctypes.c_void_p()
    if not convert_descriptor(
        descriptor,
        sddl_revision_1,
        dacl_security_information,
        ctypes.byref(string_descriptor),
        None,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.wstring_at(string_descriptor.value)
    finally:
        local_free(string_descriptor)


def test_instance_lock_is_exclusive_non_inheritable_and_cleans_owned_state(
    tmp_path: Path,
) -> None:
    from paper_reader.instance import ApplicationInstanceLock, read_instance_state

    state_root = tmp_path / "用户状态"
    first = ApplicationInstanceLock(state_root)
    second = ApplicationInstanceLock(state_root)

    assert first.acquire() is True
    assert first.file_descriptor is not None
    assert os.get_inheritable(first.file_descriptor) is False
    assert second.acquire() is False

    first.publish(port=43127, activation_secret="a" * 43)
    state = read_instance_state(state_root)
    assert state.pid == os.getpid()
    assert state.port == 43127
    assert state.activation_secret == "a" * 43
    assert set(json.loads(first.state_path.read_text(encoding="utf-8"))) == {
        "schema_version",
        "pid",
        "created_at",
        "port",
        "activation_secret",
    }
    if os.name == "nt":
        for private_file in (first.lock_path, first.state_path):
            sddl = _windows_file_sddl(private_file)
            assert "D:P" in sddl
            assert sddl.count("(A;") == 1
            assert all(marker not in sddl for marker in (";;;WD)", ";;;BU)", ";;;AU)"))

    first.close()
    assert first.state_path.exists() is False
    assert second.acquire() is True
    second.close()


def test_stale_state_never_confers_ownership_and_is_replaced_only_after_lock(
    tmp_path: Path,
) -> None:
    from paper_reader.instance import ApplicationInstanceLock, read_instance_state

    state_root = tmp_path / "state"
    state_root.mkdir()
    stale = {
        "schema_version": "paper-reader-instance-v1",
        "pid": 999_999,
        "created_at": "2020-01-01T00:00:00Z",
        "port": 49876,
        "activation_secret": "s" * 43,
    }
    (state_root / "instance.json").write_text(json.dumps(stale), encoding="utf-8")

    owner = ApplicationInstanceLock(state_root)
    assert owner.acquire() is True
    assert read_instance_state(state_root).pid == 999_999

    owner.publish(port=41234, activation_secret="n" * 43)
    assert read_instance_state(state_root).pid == os.getpid()
    assert read_instance_state(state_root).activation_secret == "n" * 43
    owner.close()


def test_instance_lock_publish_requires_ownership_and_acquire_is_idempotent(
    tmp_path: Path,
) -> None:
    from paper_reader.instance import ApplicationInstanceLock, InstanceLockError

    lock = ApplicationInstanceLock(tmp_path / "state")
    with pytest.raises(InstanceLockError, match="ownership"):
        lock.publish(port=45678, activation_secret="a" * 43)

    assert lock.acquire() is True
    descriptor = lock.file_descriptor
    assert lock.acquire() is True
    assert lock.file_descriptor == descriptor
    with pytest.raises(InstanceLockError, match="port"):
        lock.publish(port=0, activation_secret="a" * 43)
    with pytest.raises(InstanceLockError, match="secret"):
        lock.publish(port=45678, activation_secret="short")
    lock.close()
    lock.close()


def test_windows_atomic_state_replace_retries_only_transient_sharing_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader import instance

    class TransientPermissionError(PermissionError):
        winerror = 5

    source = tmp_path / "source.tmp"
    destination = tmp_path / "instance.json"
    source.write_text("new", encoding="utf-8")
    transient = TransientPermissionError("sharing race")
    calls = 0

    def replace(left: Path, right: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise transient
        assert left == source
        assert right == destination

    monkeypatch.setattr(instance.os, "replace", replace)
    monkeypatch.setattr(instance, "sleep", lambda _seconds: None)

    instance._replace_private_file(source, destination)

    assert calls == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-range lock contract")
def test_windows_kernel_releases_lock_after_abnormal_process_exit(tmp_path: Path) -> None:
    from paper_reader.instance import ApplicationInstanceLock

    state_root = tmp_path / "cross-process"
    script = """
import sys
from pathlib import Path
from paper_reader.instance import ApplicationInstanceLock
lock = ApplicationInstanceLock(Path(sys.argv[1]))
if not lock.acquire():
    raise SystemExit(9)
print('locked', flush=True)
sys.stdin.buffer.read(1)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(state_root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline() == b"locked\r\n"
        contender = ApplicationInstanceLock(state_root)
        assert contender.acquire() is False

        process.kill()
        assert process.wait(timeout=10) != 0
        deadline = time.monotonic() + 2
        while not contender.acquire() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert contender.file_descriptor is not None
        contender.close()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"schema_version": "future"}, "schema"),
        ({"pid": 0}, "pid"),
        ({"created_at": "not-a-time"}, "created_at"),
        ({"port": 0}, "port"),
        ({"activation_secret": "short"}, "activation secret"),
        ({"unexpected": True}, "fields"),
    ],
)
def test_instance_state_parser_rejects_untrusted_or_ambiguous_json(
    tmp_path: Path,
    mutation: dict[str, object],
    message: str,
) -> None:
    from paper_reader.instance import InvalidInstanceStateError, read_instance_state

    state_root = tmp_path / "state"
    state_root.mkdir()
    payload: dict[str, object] = {
        "schema_version": "paper-reader-instance-v1",
        "pid": 123,
        "created_at": "2026-07-14T10:30:00Z",
        "port": 45678,
        "activation_secret": "a" * 43,
    }
    payload.update(mutation)
    (state_root / "instance.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidInstanceStateError, match=message):
        read_instance_state(state_root)


def test_instance_state_rejects_oversized_or_linked_files(tmp_path: Path) -> None:
    from paper_reader.instance import InvalidInstanceStateError, read_instance_state

    state_root = tmp_path / "state"
    state_root.mkdir()
    state = state_root / "instance.json"
    state.write_bytes(b"{" + (b" " * 8192) + b"}")
    with pytest.raises(InvalidInstanceStateError, match="size"):
        read_instance_state(state_root)

    target = tmp_path / "elsewhere.json"
    target.write_text("{}", encoding="utf-8")
    state.unlink()
    try:
        state.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows profile")
    with pytest.raises(InvalidInstanceStateError, match="reparse|link"):
        read_instance_state(state_root)
