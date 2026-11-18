"""Build, audit, smoke, and atomically publish the Windows x64 onedir."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import shutil
import sqlite3
import stat
import struct
import subprocess
import sys
import time
from collections.abc import Sequence
from contextlib import closing, suppress
from pathlib import Path
from secrets import token_hex
from typing import Any, cast

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paper_reader import __version__  # noqa: E402
from paper_reader.migrations.versions import FROZEN_DATABASE_HEAD  # noqa: E402
from paper_reader.openapi import (  # noqa: E402
    API_CONTRACT_VERSION,
    OPENAPI_CONTRACT_SHA256,
)
from paper_reader.packaging.distribution import (  # noqa: E402
    DistributionError,
    DistributionReport,
    verify_distribution,
    write_bundle_manifest,
)
from paper_reader.plugins.contracts import PLUGIN_PROTOCOL_VERSION  # noqa: E402


class WindowsBuildError(RuntimeError):
    """A stable, path-free Windows build failure."""

    def __init__(self, code: str, *, member: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.member = member


def build_windows_distribution(
    *,
    output: Path,
    work_root: Path,
    sync_environment: bool = True,
    build_web: bool = True,
    run_smoke: bool = True,
) -> DistributionReport:
    if os.name != "nt" or struct.calcsize("P") != 8:
        raise WindowsBuildError("windows_x64_required")
    destination = _safe_destination(output)
    workspace = _safe_workspace(work_root)
    python = _prepare_build_environment(workspace) if sync_environment else Path(sys.executable)
    if build_web:
        _run(
            [_required_command("npm"), "run", "build"],
            cwd=ROOT,
            env=_sanitized_build_environment(),
        )
    _run(
        [str(python), str(ROOT / "scripts/build_icon.py")],
        cwd=ROOT,
        env=_sanitized_build_environment(virtual_environment=python.parent.parent),
    )
    _verify_source_version_files()

    staging = workspace / f"staging-{token_hex(8)}"
    staged_dist = staging / "dist"
    staged_work = staging / "work"
    candidate = destination.parent / f".{destination.name}.candidate-{token_hex(8)}"
    try:
        staging.mkdir(parents=True, exist_ok=False)
        _run(
            [
                str(python),
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--distpath",
                str(staged_dist),
                "--workpath",
                str(staged_work),
                str(ROOT / "packaging/pyinstaller/paper-reader.spec"),
            ],
            cwd=ROOT,
            env=_sanitized_build_environment(virtual_environment=python.parent.parent),
        )
        bundle = staged_dist / "paper-reader"
        if not bundle.is_dir():
            raise WindowsBuildError("pyinstaller_output_missing")
        shutil.copyfile(ROOT / "LICENSE", bundle / "LICENSE")
        shutil.copyfile(
            ROOT / "packaging/THIRD_PARTY_NOTICES.md",
            bundle / "THIRD_PARTY_NOTICES.md",
        )
        write_bundle_manifest(bundle, version=__version__)
        report = _audit(bundle, workspace)
        if run_smoke:
            smoke_distribution(bundle, scratch_root=staging / "smoke")
            report = _audit(bundle, workspace)

        destination.parent.mkdir(parents=True, exist_ok=True)
        _reject_reparse_ancestors(destination.parent)
        shutil.copytree(bundle, candidate, symlinks=False)
        candidate_report = _audit(candidate, workspace)
        if _business_signature(report) != _business_signature(candidate_report):
            raise WindowsBuildError("distribution_copy_mismatch")
        _atomic_publish(candidate, destination)
        return _audit(destination, workspace)
    except (DistributionError, OSError, subprocess.SubprocessError) as error:
        if isinstance(error, DistributionError):
            raise WindowsBuildError(error.code, member=error.member) from error
        raise WindowsBuildError("windows_build_failed") from error
    finally:
        _remove_tree(candidate)
        _remove_tree(staging)


def smoke_distribution(root: Path, *, scratch_root: Path) -> None:
    """Run the frozen entrypoint with no Python/Node/Git on PATH and isolated data roots."""

    distribution = Path(root).absolute()
    scratch = Path(scratch_root).absolute()
    scratch.mkdir(parents=True, exist_ok=False)
    state = scratch / "state"
    cache = scratch / "cache"
    library = scratch / "library"
    ready = scratch / "ready.json"
    for path in (state, cache, library):
        path.mkdir(parents=True)
    executable = distribution / "paper-reader.exe"
    before = _file_signature(distribution)
    original_modes = _make_distribution_read_only(distribution)
    environment = _smoke_environment(state=state, cache=cache, library=library)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                str(executable),
                "serve",
                "--no-browser",
                "--ready-file",
                str(ready),
                "--shutdown-after",
                "5",
            ],
            cwd=scratch,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 30
        while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready.is_file():
            raise WindowsBuildError("frozen_startup_failed")
        payload = cast(dict[str, Any], json.loads(ready.read_text(encoding="utf-8")))
        if set(payload) != {"schema_version", "pid", "port"}:
            raise WindowsBuildError("frozen_ready_invalid")
        port = payload.get("port")
        if not isinstance(port, int) or not 0 < port <= 65535:
            raise WindowsBuildError("frozen_ready_invalid")
        health = _loopback_get_json(port, "/api/v1/health")
        expected_health = {
            "name": "paper-reader",
            "status": "ok",
            "version": __version__,
            "api_version": API_CONTRACT_VERSION,
            "openapi_sha256": OPENAPI_CONTRACT_SHA256,
            "database_schema_revision": FROZEN_DATABASE_HEAD,
            "plugin_protocol_version": PLUGIN_PROTOCOL_VERSION,
        }
        if health != expected_health:
            raise WindowsBuildError("frozen_health_invalid")
        spa = _loopback_get(port, "/")
        if b'<div id="app"></div>' not in spa:
            raise WindowsBuildError("frozen_spa_invalid")
        openapi = cast(dict[str, Any], json.loads(_loopback_get(port, "/openapi.json")))
        if "/api/v1/health" not in cast(dict[str, Any], openapi.get("paths", {})):
            raise WindowsBuildError("frozen_openapi_invalid")
        database = state / "paper-reader.sqlite3"
        if _sqlite_journal_mode(database) != "wal":
            raise WindowsBuildError("frozen_sqlite_not_wal")
        try:
            return_code = process.wait(timeout=15)
        except subprocess.TimeoutExpired as error:
            raise WindowsBuildError("frozen_shutdown_timeout") from error
        if return_code != 0:
            raise WindowsBuildError("frozen_shutdown_failed")
        if _file_signature(distribution) != before:
            raise WindowsBuildError("frozen_distribution_modified")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        _restore_modes(original_modes)
        _remove_tree(scratch)


def _prepare_build_environment(work_root: Path) -> Path:
    environment_root = work_root / "venv"
    environment = _sanitized_build_environment()
    environment["UV_PROJECT_ENVIRONMENT"] = str(environment_root)
    _run(
        [
            _required_command("uv"),
            "sync",
            "--frozen",
            "--no-dev",
            "--no-editable",
            "--extra",
            "packaging",
            "--extra",
            "models",
            "--extra",
            "ocr",
            "--python",
            "3.12",
        ],
        cwd=ROOT,
        env=environment,
    )
    python = environment_root / "Scripts/python.exe"
    if not python.is_file():
        raise WindowsBuildError("build_environment_missing")
    return python


def _sanitized_build_environment(*, virtual_environment: Path | None = None) -> dict[str, str]:
    environment: dict[str, str] = {}
    for name in (
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "PATH",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment.update(
        {
            "NO_COLOR": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": "",
            "UV_LINK_MODE": "copy",
        }
    )
    if virtual_environment is not None:
        environment["VIRTUAL_ENV"] = str(virtual_environment)
    return environment


def _audit(root: Path, workspace: Path) -> DistributionReport:
    return verify_distribution(
        root,
        layout_path=ROOT / "packaging/release-layout.json",
        project_root=ROOT,
        forbidden_build_roots=(ROOT, workspace),
    )


def _safe_destination(path: Path) -> Path:
    candidate = Path(path).absolute()
    if candidate == Path(candidate.anchor) or candidate in {ROOT, ROOT.parent}:
        raise WindowsBuildError("distribution_destination_unsafe")
    if candidate.name.casefold() != "paper-reader":
        raise WindowsBuildError("distribution_destination_name")
    return candidate


def _safe_workspace(path: Path) -> Path:
    candidate = Path(path).absolute()
    if candidate == Path(candidate.anchor) or candidate in {ROOT, ROOT.parent}:
        raise WindowsBuildError("build_workspace_unsafe")
    candidate.mkdir(parents=True, exist_ok=True)
    _reject_reparse_ancestors(candidate)
    return candidate


def _reject_reparse_ancestors(path: Path) -> None:
    current = path
    while True:
        try:
            if current.is_symlink() or bool(getattr(current, "is_junction", lambda: False)()):
                raise WindowsBuildError("build_path_reparse")
        except OSError as error:
            raise WindowsBuildError("build_path_reparse") from error
        parent = current.parent
        if parent == current:
            return
        current = parent


def _verify_source_version_files() -> None:
    version_info = (ROOT / "packaging/windows/version-info.txt").read_text(encoding="utf-8")
    major, minor, patch = (int(item) for item in __version__.split("."))
    if (
        f"filevers=({major}, {minor}, {patch}, 0)" not in version_info
        or f"prodvers=({major}, {minor}, {patch}, 0)" not in version_info
        or f"{__version__}.0" not in version_info
    ):
        raise WindowsBuildError("windows_version_resource_stale")


def _atomic_publish(candidate: Path, destination: Path) -> None:
    backup = destination.parent / f".{destination.name}.previous-{token_hex(8)}"
    had_previous = destination.exists()
    try:
        if had_previous:
            os.replace(destination, backup)
        try:
            os.replace(candidate, destination)
        except OSError:
            if had_previous and backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        _remove_tree(backup)
    finally:
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> None:
    subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        check=True,
        stdin=subprocess.DEVNULL,
        shell=False,
    )


def _required_command(name: str) -> str:
    command = shutil.which(name)
    if command is None:
        raise WindowsBuildError(f"required_command_missing_{name}")
    return command


def _smoke_environment(*, state: Path, cache: Path, library: Path) -> dict[str, str]:
    environment: dict[str, str] = {}
    for name in (
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMSPEC",
        "LOCALAPPDATA",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    system_root = Path(environment.get("SYSTEMROOT", r"C:\Windows"))
    environment["PATH"] = str(system_root / "System32")
    if any(shutil.which(name, path=environment["PATH"]) for name in ("python", "node", "git")):
        raise WindowsBuildError("frozen_path_not_clean")
    environment.update(
        {
            "PAPER_READER_CACHE_ROOT": str(cache),
            "PAPER_READER_DISABLE_UPDATE_CHECKS": "1",
            "PAPER_READER_LIBRARY_ROOT": str(library),
            "PAPER_READER_STATE_ROOT": str(state),
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": "",
        }
    )
    return environment


def _loopback_get(port: int, path: str) -> bytes:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        payload = response.read(8 * 1024 * 1024 + 1)
        if response.status != 200 or len(payload) > 8 * 1024 * 1024:
            raise WindowsBuildError("frozen_http_invalid")
        return payload
    finally:
        connection.close()


def _loopback_get_json(port: int, path: str) -> dict[str, Any]:
    try:
        value = cast(Any, json.loads(_loopback_get(port, path)))
    except ValueError as error:
        raise WindowsBuildError("frozen_http_invalid") from error
    if not isinstance(value, dict):
        raise WindowsBuildError("frozen_http_invalid")
    return cast(dict[str, Any], value)


def _sqlite_journal_mode(database: Path) -> str:
    """Read journal mode without retaining a Windows file handle."""

    with closing(sqlite3.connect(database, timeout=2)) as connection:
        row = connection.execute("PRAGMA journal_mode").fetchone()
    return str(row[0]).casefold() if row else ""


def _make_distribution_read_only(root: Path) -> tuple[tuple[Path, int], ...]:
    original: list[tuple[Path, int]] = []
    for path in root.rglob("*"):
        if path.is_file():
            mode = stat.S_IMODE(path.stat().st_mode)
            original.append((path, mode))
            path.chmod(stat.S_IREAD)
    return tuple(original)


def _restore_modes(items: Sequence[tuple[Path, int]]) -> None:
    for path, mode in items:
        with suppress(OSError):
            path.chmod(mode)


def _file_signature(root: Path) -> tuple[tuple[str, int, str], ...]:
    result: list[tuple[str, int, str]] = []
    for path in root.rglob("*"):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result.append((path.relative_to(root).as_posix(), path.stat().st_size, digest))
    return tuple(sorted(result, key=lambda item: item[0].casefold()))


def _business_signature(report: DistributionReport) -> tuple[object, ...]:
    return (
        report.application,
        report.version,
        report.architecture,
        report.entrypoint,
        report.file_count,
        report.total_size_bytes,
        report.manifest_sha256,
    )


def _remove_tree(path: Path) -> None:
    def on_error(function: Any, target: str, _error: object) -> None:
        Path(target).chmod(stat.S_IWRITE)
        function(target)

    for attempt in range(6):
        if not path.exists():
            return
        try:
            shutil.rmtree(path, onerror=on_error)
            return
        except OSError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (2**attempt))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist/windows/paper-reader")
    parser.add_argument("--work-root", type=Path, default=ROOT / ".build/windows")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--skip-web-build", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    args = parser.parse_args()
    try:
        report = build_windows_distribution(
            output=args.output,
            work_root=args.work_root,
            sync_environment=not args.skip_sync,
            build_web=not args.skip_web_build,
            run_smoke=not args.skip_smoke,
        )
    except WindowsBuildError as error:
        failure = {"ok": False, "error": error.code}
        if error.member is not None:
            failure["member"] = error.member
        print(json.dumps(failure, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "version": report.version,
                "file_count": report.file_count,
                "total_size_bytes": report.total_size_bytes,
                "manifest_sha256": report.manifest_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
