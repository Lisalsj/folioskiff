"""Create the portable ZIP and per-user Inno installer from one audited onedir."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from secrets import token_hex
from typing import Any, cast

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from paper_reader.packaging.distribution import (  # noqa: E402
    DistributionError,
    DistributionReport,
    verify_distribution,
)
from paper_reader.packaging.windows import INSTALLER_APP_ID  # noqa: E402
from scripts.build_release_metadata import (  # noqa: E402
    ReleaseMetadataError,
    ReleaseMetadataReport,
    build_npm_sbom,
    build_python_sbom,
    build_release_metadata,
)

_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_MAX_ARCHIVE_MEMBERS = 100_000
_MAX_ARCHIVE_FILE_BYTES = 1024 * 1024 * 1024
_MAX_ARCHIVE_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
_UNINSTALL_MEMBER = re.compile(r"^unins[0-9]{3}\.(?:dat|exe|msg)$", re.IGNORECASE)
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_UNINSTALL_KEY = f"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{INSTALLER_APP_ID}_is1"


class PortableArchiveError(ValueError):
    """A portable archive violated its closed layout contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ReleasePackageError(RuntimeError):
    """Release packaging failed without exposing a local build path."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def sign_command_from_environment(
    *,
    enabled: bool,
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...] | None:
    """Read a signing argv only after an explicit opt-in; never interpret shell text."""

    if not enabled:
        return None
    source = os.environ if environment is None else environment
    raw = source.get("PAPER_READER_SIGN_COMMAND")
    if raw is None or not raw or len(raw) > 16 * 1024:
        raise ReleasePackageError("sign_command_missing")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ReleasePackageError("sign_command_invalid") from error
    if (
        not isinstance(value, list)
        or not 2 <= len(value) <= 64
        or any(
            not isinstance(item, str)
            or not item
            or len(item) > 4096
            or any(ord(character) < 32 for character in item)
            for item in value
        )
    ):
        raise ReleasePackageError("sign_command_invalid")
    command = tuple(cast(list[str], value))
    executable = Path(command[0])
    if (
        not executable.is_absolute()
        or not executable.is_file()
        or _is_link_or_reparse(executable)
        or command.count("{artifact}") != 1
        or any("{artifact}" in item and item != "{artifact}" for item in command)
    ):
        raise ReleasePackageError("sign_command_invalid")
    return (str(executable.absolute()), *command[1:])


def run_signing_hook(
    *,
    artifact: Path,
    command: Sequence[str],
    scanner: Callable[[Path], object],
    release_version: str | None = None,
) -> None:
    """Run an explicitly supplied signer without a shell and rescan both byte states."""

    if release_version == "0.8.0":
        raise ReleasePackageError("v08_must_be_unsigned")
    target = Path(artifact).absolute()
    if not target.is_file() or _is_link_or_reparse(target):
        raise ReleasePackageError("sign_artifact_unsafe")
    argv = tuple(command)
    if (
        not argv
        or command.count("{artifact}") != 1
        or any("{artifact}" in item and item != "{artifact}" for item in argv)
    ):
        raise ReleasePackageError("sign_command_invalid")
    executable = Path(argv[0])
    if not executable.is_absolute() or not executable.is_file() or _is_link_or_reparse(executable):
        raise ReleasePackageError("sign_command_invalid")
    scanner(target)
    expanded = tuple(str(target) if item == "{artifact}" else item for item in argv)
    environment = _sanitized_packaging_environment()
    environment["PATH"] = str(executable.parent)
    try:
        completed = subprocess.run(
            expanded,
            cwd=target.parent,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            shell=False,
            timeout=10 * 60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleasePackageError("sign_hook_failed") from error
    require_successful_exit(completed.returncode, operation="sign_hook")
    if not target.is_file() or _is_link_or_reparse(target):
        raise ReleasePackageError("sign_artifact_unsafe")
    scanner(target)


@dataclass(frozen=True, slots=True)
class PortableArchiveReport:
    file_count: int
    uncompressed_size_bytes: int
    archive_size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class InstallerPeReport:
    product_name: str
    version: str
    publisher: str


@dataclass(frozen=True, slots=True)
class ReleasePackageReport:
    version: str
    distribution: DistributionReport
    portable: PortableArchiveReport
    portable_path: str
    installer_path: str | None


@dataclass(frozen=True, slots=True)
class ReleaseBundleReport:
    version: str
    package: ReleasePackageReport
    metadata: ReleaseMetadataReport


@dataclass(frozen=True, slots=True)
class InstallerSmokeReport:
    install_exit_code: int
    portable_activation_exit_code: int
    running_block_exit_code: int
    reinstall_exit_code: int
    downgrade_block_exit_code: int
    uninstall_exit_code: int
    program_removed: bool
    user_data_preserved: bool


def create_portable_archive(source_root: Path, destination: Path) -> PortableArchiveReport:
    source = _portable_source(source_root)
    target = _safe_output_file(destination, suffix=".zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    if _has_reparse_ancestor(target.parent):
        raise PortableArchiveError("portable_destination_unsafe")
    temporary = target.with_name(f".{target.name}.{token_hex(8)}.tmp")
    try:
        _write_portable_payload(source, temporary)
        report = verify_portable_archive(temporary, source_root=source)
        os.replace(temporary, target)
        return report
    except PortableArchiveError:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise PortableArchiveError("portable_write_failed") from error
    finally:
        with suppress(OSError):
            temporary.unlink()


def _write_portable_payload(source: Path, destination: Path) -> None:
    files = _collect_source_files(source)
    with zipfile.ZipFile(
        destination,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for path in files:
            relative = path.relative_to(source).as_posix()
            member = f"paper-reader/{relative}"
            info = zipfile.ZipInfo(member, date_time=_FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o444) << 16
            info.flag_bits |= 0x800
            with (
                path.open("rb") as reader,
                archive.open(info, mode="w", force_zip64=True) as writer,
            ):
                shutil.copyfileobj(reader, writer, length=1024 * 1024)


def verify_portable_archive(
    archive_path: Path,
    *,
    source_root: Path | None = None,
) -> PortableArchiveReport:
    archive_file = Path(archive_path).absolute()
    try:
        if not archive_file.is_file() or archive_file.is_symlink():
            raise PortableArchiveError("portable_archive_missing")
        with zipfile.ZipFile(archive_file, mode="r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > _MAX_ARCHIVE_MEMBERS:
                raise PortableArchiveError("portable_member_count")
            seen: set[str] = set()
            names: list[str] = []
            for info in infos:
                name = info.filename
                if info.orig_filename != name:
                    raise PortableArchiveError("portable_member_name")
                _validate_portable_member(name)
                folded = name.casefold()
                if folded in seen:
                    raise PortableArchiveError("portable_member_collision")
                seen.add(folded)
                names.append(name)
            if names != sorted(names, key=str.casefold):
                raise PortableArchiveError("portable_member_order")

            total = 0
            actual: dict[str, tuple[int, str]] = {}
            for info in infos:
                name = info.filename
                if info.is_dir():
                    raise PortableArchiveError("portable_directory_member")
                if info.flag_bits & 0x1:
                    raise PortableArchiveError("portable_encrypted_member")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise PortableArchiveError("portable_link_member")
                if (
                    info.date_time != _FIXED_ZIP_TIME
                    or info.create_system != 3
                    or info.external_attr >> 16 != (stat.S_IFREG | 0o444)
                    or info.compress_type != zipfile.ZIP_DEFLATED
                ):
                    raise PortableArchiveError("portable_member_metadata")
                if not 0 <= info.file_size <= _MAX_ARCHIVE_FILE_BYTES:
                    raise PortableArchiveError("portable_member_size")
                total += info.file_size
                if total > _MAX_ARCHIVE_TOTAL_BYTES:
                    raise PortableArchiveError("portable_total_size")
                with archive.open(info, mode="r") as reader:
                    actual[name] = (info.file_size, _sha256_stream(reader))
    except PortableArchiveError:
        raise
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise PortableArchiveError("portable_archive_invalid") from error

    if source_root is not None:
        source = _portable_source(source_root)
        expected = {
            f"paper-reader/{path.relative_to(source).as_posix()}": (
                path.stat().st_size,
                _sha256_file(path),
            )
            for path in _collect_source_files(source)
        }
        if actual != expected:
            raise PortableArchiveError("portable_source_mismatch")

    return PortableArchiveReport(
        file_count=len(actual),
        uncompressed_size_bytes=total,
        archive_size_bytes=archive_file.stat().st_size,
        sha256=_sha256_file(archive_file),
    )


def package_distribution(
    *,
    distribution: Path,
    output_dir: Path,
    iscc: Path | None = None,
    portable_only: bool = False,
) -> ReleasePackageReport:
    source = Path(distribution).absolute()
    destination = _safe_output_directory(output_dir, source=source)
    try:
        audited = verify_distribution(
            source,
            layout_path=ROOT / "packaging/release-layout.json",
            project_root=ROOT,
            forbidden_build_roots=(ROOT,),
        )
    except DistributionError as error:
        raise ReleasePackageError(error.code) from error
    version = audited.version
    _parse_version(version)
    portable_name = f"paper-reader-{version}-windows-x64-portable.zip"
    installer_name = f"paper-reader-{version}-windows-x64-setup.exe"
    destination.mkdir(parents=True, exist_ok=True)
    if _has_reparse_ancestor(destination):
        raise ReleasePackageError("release_output_unsafe")
    staging = destination / f".package-staging-{token_hex(8)}"
    staging.mkdir(parents=False, exist_ok=False)
    try:
        candidate_portable = staging / portable_name
        portable_report = create_portable_archive(source, candidate_portable)
        promotions: list[tuple[Path, Path]] = [(candidate_portable, destination / portable_name)]
        installer_path: Path | None = None
        if not portable_only:
            compiler = resolve_iscc(iscc)
            compiler_output = staging / "inno"
            compiler_output.mkdir()
            command = installer_command(
                iscc=compiler,
                script=ROOT / "packaging/windows/paper-reader.iss",
                distribution=source,
                output_dir=compiler_output,
                version=version,
            )
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=_sanitized_packaging_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                shell=False,
                timeout=20 * 60,
            )
            require_successful_exit(completed.returncode, operation="inno_compile")
            built = compiler_output / installer_name
            if not built.is_file() or tuple(compiler_output.iterdir()) != (built,):
                raise ReleasePackageError("inno_output_invalid")
            identity = inspect_installer_pe(built)
            if (
                identity.product_name != "页舟 · FolioSkiff"
                or identity.version != version
                or identity.publisher != "FolioSkiff contributors"
            ):
                raise ReleasePackageError("inno_identity_invalid")
            promotions.append((built, destination / installer_name))
            installer_path = destination / installer_name

        _atomic_publish_set(tuple(promotions))
        final_portable = destination / portable_name
        return ReleasePackageReport(
            version=version,
            distribution=audited,
            portable=portable_report,
            portable_path=final_portable.name,
            installer_path=installer_path.name if installer_path is not None else None,
        )
    except (OSError, subprocess.SubprocessError, PortableArchiveError) as error:
        if isinstance(error, PortableArchiveError):
            raise ReleasePackageError(error.code) from error
        raise ReleasePackageError("release_packaging_failed") from error
    finally:
        _remove_tree(staging)


def _changelog_notes(version: str) -> tuple[str, str]:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = re.search(
        rf"(?ms)^## \[{re.escape(version)}\] - [^\n]+\n(?P<body>.*?)(?=^## |\Z)",
        text,
    )
    if section is None:
        raise ReleasePackageError("release_changelog_version_missing")
    body = section.group("body").strip()
    summary = body.partition("\n\n")[0].strip()
    upgrade = re.search(r"(?ms)^### Upgrade\s*\n(.*?)(?=^### |\Z)", body)
    if not summary or summary.startswith("#") or upgrade is None or not upgrade[1].strip():
        raise ReleasePackageError("release_changelog_notes_missing")
    return summary, upgrade[1].strip()


def package_release_bundle(
    *,
    distribution: Path,
    output_dir: Path,
    python_executable: Path,
    npm_executable: Path,
    git_commit: str,
    database_revision: str,
    iscc: Path | None,
    sign_command: Sequence[str] | None,
    changelog_summary: str | None = None,
    migration_notes: str | None = None,
) -> ReleaseBundleReport:
    """Stage every release byte, verify the closed set, then publish it atomically."""

    source = Path(distribution).absolute()
    destination = _safe_output_directory(output_dir, source=source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _has_reparse_ancestor(destination.parent):
        raise ReleasePackageError("release_output_unsafe")
    staging = destination.parent / f".release-staging-{token_hex(8)}"
    staging.mkdir(parents=False, exist_ok=False)
    try:
        artifacts = staging / "artifacts"
        package = package_distribution(
            distribution=source,
            output_dir=artifacts,
            iscc=iscc,
            portable_only=False,
        )
        if package.installer_path is None:
            raise ReleasePackageError("release_installer_missing")
        version = package.version
        if changelog_summary is None or migration_notes is None:
            summary, upgrade = _changelog_notes(version)
            changelog_summary = changelog_summary if changelog_summary is not None else summary
            migration_notes = migration_notes if migration_notes is not None else upgrade
        release = staging / "release"
        release.mkdir()
        artifact_names = (package.portable_path, package.installer_path)
        for name in artifact_names:
            candidate = artifacts / name
            if not candidate.is_file() or _is_link_or_reparse(candidate):
                raise ReleasePackageError("release_artifact_missing")
            os.replace(candidate, release / name)

        installer = release / package.installer_path
        if sign_command is not None:

            def scan_signed_installer(path: Path) -> None:
                identity = inspect_installer_pe(path)
                if (
                    identity.product_name != "页舟 · FolioSkiff"
                    or identity.version != version
                    or identity.publisher != "FolioSkiff contributors"
                ):
                    raise ReleasePackageError("inno_identity_invalid")

            run_signing_hook(
                artifact=installer,
                command=sign_command,
                scanner=scan_signed_installer,
                release_version=version,
            )

        python_sbom_name = f"paper-reader-{version}-python.cdx.json"
        npm_sbom_name = f"paper-reader-{version}-npm.cdx.json"
        build_python_sbom(
            python_executable=Path(python_executable),
            pyproject=(ROOT / "pyproject.toml").absolute(),
            version=version,
            destination=release / python_sbom_name,
        )
        build_npm_sbom(
            npm_executable=Path(npm_executable),
            project_root=ROOT,
            package_lock=(ROOT / "package-lock.json").absolute(),
            version=version,
            destination=release / npm_sbom_name,
        )
        metadata = build_release_metadata(
            release_dir=release,
            version=version,
            git_commit=git_commit,
            database_revision=database_revision,
            artifact_roles={
                package.installer_path: "installer",
                package.portable_path: "portable",
                python_sbom_name: "python_sbom",
                npm_sbom_name: "npm_sbom",
            },
            changelog_path=(ROOT / "CHANGELOG.md").absolute(),
            changelog_summary=changelog_summary,
            migration_notes=migration_notes,
            signed=sign_command is not None,
        )
        candidate_names = {item.name for item in release.iterdir() if item.is_file()}
        if len(candidate_names) != metadata.file_count:
            raise ReleasePackageError("release_metadata_incomplete")
        if destination.exists():
            if not destination.is_dir() or _is_link_or_reparse(destination):
                raise ReleasePackageError("release_output_unsafe")
            existing = tuple(destination.iterdir())
            if any(
                not item.is_file() or _is_link_or_reparse(item) or item.name not in candidate_names
                for item in existing
            ):
                raise ReleasePackageError("release_output_not_closed")
        promotions = tuple(
            (release / name, destination / name)
            for name in sorted(candidate_names, key=str.casefold)
        )
        _atomic_publish_set(promotions)
        return ReleaseBundleReport(version=version, package=package, metadata=metadata)
    except ReleasePackageError:
        raise
    except ReleaseMetadataError as error:
        raise ReleasePackageError(error.code) from error
    except OSError as error:
        raise ReleasePackageError("release_bundle_failed") from error
    finally:
        _remove_tree(staging)


def resolve_iscc(explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = Path(explicit)
        if (
            not candidate.is_absolute()
            or candidate.name.casefold() != "iscc.exe"
            or not candidate.is_file()
            or _is_link_or_reparse(candidate)
        ):
            raise ReleasePackageError("inno_compiler_path_unsafe")
        return candidate.absolute()

    roots = tuple(
        Path(value)
        for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
        if (value := os.environ.get(name))
    )
    candidates: list[Path] = []
    for root in roots:
        candidates.extend(
            (
                root / "Inno Setup 7/ISCC.exe",
                root / "Inno Setup 6/ISCC.exe",
                root / "Programs/Inno Setup 7/ISCC.exe",
                root / "Programs/Inno Setup 6/ISCC.exe",
            )
        )
    for candidate in candidates:
        if candidate.is_file() and not _is_link_or_reparse(candidate):
            return candidate.absolute()
    raise ReleasePackageError("inno_compiler_missing")


def installer_command(
    *,
    iscc: Path,
    script: Path,
    distribution: Path,
    output_dir: Path,
    version: str,
) -> tuple[str, ...]:
    _parse_version(version)
    values = (iscc, script, distribution, output_dir)
    if any(not Path(value).is_absolute() for value in values):
        raise ReleasePackageError("inno_command_path_unsafe")
    if any('"' in str(value) for value in values):
        raise ReleasePackageError("inno_command_path_unsafe")
    return (
        str(iscc),
        "/Qp",
        f"/DAppVersion={version}",
        f"/DSourceDir={distribution}",
        f"/DOutputDir={output_dir}",
        str(script),
    )


def require_successful_exit(return_code: int, *, operation: str) -> None:
    if return_code != 0:
        raise ReleasePackageError(f"{operation}_failed")


def silent_installer_arguments(
    *,
    install_root: Path,
    group_name: str,
) -> tuple[str, ...]:
    target = Path(install_root)
    if (
        not target.is_absolute()
        or target == Path(target.anchor)
        or '"' in str(target)
        or not re.fullmatch(r"paper-reader-smoke-[a-z0-9]{6,32}", group_name)
    ):
        raise ReleasePackageError("installer_smoke_argument_unsafe")
    return (
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/NOCLOSEAPPLICATIONS",
        "/NOFORCECLOSEAPPLICATIONS",
        "/SP-",
        f"/DIR={target}",
        f"/GROUP={group_name}",
        "/MERGETASKS=!desktopicon",
    )


def smoke_installer_lifecycle(
    *,
    installer: Path,
    distribution: Path,
    work_root: Path,
) -> InstallerSmokeReport:
    """Exercise an installer without touching an existing paper-reader installation."""

    if os.name != "nt":
        raise ReleasePackageError("installer_smoke_windows_required")
    source = _portable_source(distribution)
    try:
        audited = verify_distribution(
            source,
            layout_path=ROOT / "packaging/release-layout.json",
            project_root=ROOT,
            forbidden_build_roots=(ROOT,),
        )
    except DistributionError as error:
        raise ReleasePackageError(error.code) from error
    setup = Path(installer).absolute()
    identity = inspect_installer_pe(setup)
    if (
        identity.product_name != "页舟 · FolioSkiff"
        or identity.publisher != "FolioSkiff contributors"
        or identity.version != audited.version
        or setup.name != f"paper-reader-{audited.version}-windows-x64-setup.exe"
        or _is_link_or_reparse(setup)
    ):
        raise ReleasePackageError("installer_smoke_identity")

    root = _safe_smoke_root(work_root, source=source, installer=setup)
    if _read_uninstall_registration() is not None:
        raise ReleasePackageError("installer_smoke_existing_install")
    root.mkdir(parents=True, exist_ok=False)
    install_root = root / "已安装 paper-reader"
    group_name = f"paper-reader-smoke-{token_hex(4)}"
    arguments = silent_installer_arguments(
        install_root=install_root,
        group_name=group_name,
    )
    shortcut = _start_menu_shortcut(group_name)
    environment = _sanitized_packaging_environment()
    app_process: subprocess.Popen[bytes] | None = None
    uninstall_exit_code = -1
    try:
        install_exit_code = _run_release_process(
            (str(setup), *arguments),
            cwd=root,
            environment=environment,
            timeout=10 * 60,
        )
        require_successful_exit(install_exit_code, operation="installer_smoke_install")
        _verify_uninstall_registration(
            version=audited.version,
            install_root=install_root,
        )
        verify_installed_tree(install_root, source_root=source)
        _verify_shortcut(shortcut, install_root=install_root)

        state = root / "user state"
        cache = root / "user cache"
        library = root / "user library"
        for private_root in (state, cache, library):
            private_root.mkdir()
            (private_root / "preserve.txt").write_text("preserve", encoding="utf-8")
        app_environment = dict(environment)
        app_environment.update(
            {
                "PAPER_READER_CACHE_ROOT": str(cache),
                "PAPER_READER_DISABLE_UPDATE_CHECKS": "1",
                "PAPER_READER_LIBRARY_ROOT": str(library),
                "PAPER_READER_STATE_ROOT": str(state),
            }
        )
        ready = root / "installed-ready.json"
        app_process = subprocess.Popen(
            [
                str(install_root / "paper-reader.exe"),
                "serve",
                "--no-browser",
                "--ready-file",
                str(ready),
                "--shutdown-after",
                "20",
            ],
            cwd=root,
            env=app_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        _wait_for_ready_file(ready, process=app_process)
        portable_activation_exit_code = _run_release_process(
            (str(source / "paper-reader.exe"), "serve", "--no-browser"),
            cwd=root,
            environment=app_environment,
            timeout=30,
        )
        require_successful_exit(
            portable_activation_exit_code,
            operation="installer_smoke_portable_activation",
        )
        before_running_block = _installed_program_signature(install_root)
        running_block_exit_code = _run_release_process(
            (str(setup), *arguments),
            cwd=root,
            environment=environment,
            timeout=2 * 60,
        )
        if running_block_exit_code == 0:
            raise ReleasePackageError("installer_smoke_running_not_blocked")
        if _installed_program_signature(install_root) != before_running_block:
            raise ReleasePackageError("installer_smoke_running_modified")
        try:
            owned_exit_code = app_process.wait(timeout=35)
        except subprocess.TimeoutExpired as error:
            raise ReleasePackageError("installer_smoke_app_shutdown") from error
        if owned_exit_code != 0:
            raise ReleasePackageError("installer_smoke_app_failed")
        app_process = None

        canary = install_root / "reinstall-canary.txt"
        canary.write_text("preserve", encoding="utf-8")
        (install_root / "LICENSE").write_bytes(b"tampered")
        reinstall_exit_code = _run_release_process(
            (str(setup), *arguments),
            cwd=root,
            environment=environment,
            timeout=10 * 60,
        )
        require_successful_exit(
            reinstall_exit_code,
            operation="installer_smoke_reinstall",
        )
        if canary.read_text(encoding="utf-8") != "preserve":
            raise ReleasePackageError("installer_smoke_reinstall_canary")
        canary.unlink()
        verify_installed_tree(install_root, source_root=source)

        before_downgrade = _installed_program_signature(install_root)
        original_version = _set_uninstall_display_version("9.9.9")
        try:
            downgrade_block_exit_code = _run_release_process(
                (str(setup), *arguments),
                cwd=root,
                environment=environment,
                timeout=2 * 60,
            )
        finally:
            _set_uninstall_display_version(original_version)
        if downgrade_block_exit_code == 0:
            raise ReleasePackageError("installer_smoke_downgrade_not_blocked")
        if _installed_program_signature(install_root) != before_downgrade:
            raise ReleasePackageError("installer_smoke_downgrade_modified")

        uninstall_canary = install_root / "uninstall-canary.txt"
        uninstall_canary.write_text("preserve", encoding="utf-8")
        uninstaller = install_root / "unins000.exe"
        uninstall_exit_code = _run_release_process(
            (
                str(uninstaller),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
            ),
            cwd=root,
            environment=environment,
            timeout=10 * 60,
        )
        require_successful_exit(uninstall_exit_code, operation="installer_smoke_uninstall")
        _wait_for_uninstall(install_root=install_root, shortcut=shortcut)
        program_removed = not (install_root / "paper-reader.exe").exists()
        user_data_preserved = uninstall_canary.read_text(encoding="utf-8") == "preserve" and all(
            (private_root / "preserve.txt").read_text(encoding="utf-8") == "preserve"
            for private_root in (state, cache, library)
        )
        if not program_removed or not user_data_preserved:
            raise ReleasePackageError("installer_smoke_uninstall_boundary")
        return InstallerSmokeReport(
            install_exit_code=install_exit_code,
            portable_activation_exit_code=portable_activation_exit_code,
            running_block_exit_code=running_block_exit_code,
            reinstall_exit_code=reinstall_exit_code,
            downgrade_block_exit_code=downgrade_block_exit_code,
            uninstall_exit_code=uninstall_exit_code,
            program_removed=program_removed,
            user_data_preserved=user_data_preserved,
        )
    except ReleasePackageError:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise ReleasePackageError("installer_smoke_failed") from error
    finally:
        if app_process is not None and app_process.poll() is None:
            app_process.kill()
            with suppress(subprocess.TimeoutExpired):
                app_process.wait(timeout=10)
        _best_effort_uninstall_test_install(
            install_root=install_root,
            work_root=root,
            environment=environment,
        )
        _best_effort_remove_test_registration(install_root=install_root)
        _best_effort_remove_shortcut_group(shortcut=shortcut, group_name=group_name)
        _remove_tree(root)


def verify_installed_tree(installed: Path, *, source_root: Path) -> None:
    source = _portable_source(source_root)
    target = Path(installed).absolute()
    if not target.is_dir() or _is_link_or_reparse(target):
        raise ReleasePackageError("installed_tree_missing")
    try:
        source_files = {
            path.relative_to(source).as_posix(): (path.stat().st_size, _sha256_file(path))
            for path in _collect_source_files(source)
        }
        installed_files = {
            path.relative_to(target).as_posix(): (path.stat().st_size, _sha256_file(path))
            for path in _collect_source_files(target)
        }
    except PortableArchiveError as error:
        raise ReleasePackageError("installed_tree_invalid") from error
    extras = set(installed_files) - set(source_files)
    if any("/" in item or _UNINSTALL_MEMBER.fullmatch(item) is None for item in extras):
        raise ReleasePackageError("installed_tree_members")
    projected = {name: installed_files.get(name) for name in source_files}
    if projected != source_files:
        raise ReleasePackageError("installed_tree_mismatch")


def inspect_installer_pe(path: Path) -> InstallerPeReport:
    candidate = Path(path).absolute()
    if os.name != "nt" or not candidate.is_file():
        raise ReleasePackageError("installer_pe_unavailable")
    try:
        version = ctypes.WinDLL("version", use_last_error=True)
        size = version.GetFileVersionInfoSizeW(str(candidate), None)
        if not size:
            raise OSError(ctypes.get_last_error(), "version size")
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(candidate), 0, size, buffer):
            raise OSError(ctypes.get_last_error(), "version data")

        def query(sub_block: str) -> str:
            value = ctypes.c_void_p()
            length = ctypes.c_uint()
            if not version.VerQueryValueW(
                buffer,
                sub_block,
                ctypes.byref(value),
                ctypes.byref(length),
            ):
                return ""
            return ctypes.wstring_at(value, max(0, length.value - 1)).strip()

        translation = ctypes.c_void_p()
        translation_size = ctypes.c_uint()
        if (
            not version.VerQueryValueW(
                buffer,
                r"\VarFileInfo\Translation",
                ctypes.byref(translation),
                ctypes.byref(translation_size),
            )
            or translation_size.value < 4
        ):
            raise OSError("translation missing")
        language, codepage = ctypes.cast(
            translation,
            ctypes.POINTER(ctypes.c_ushort * 2),
        ).contents
        prefix = rf"\StringFileInfo\{language:04x}{codepage:04x}"
        product_name = query(prefix + r"\ProductName")
        publisher = query(prefix + r"\CompanyName")
        raw_version = query(prefix + r"\ProductVersion")
        normalized = raw_version[:-2] if raw_version.endswith(".0") else raw_version
        _parse_version(normalized)
        if not product_name or not publisher:
            raise OSError("identity missing")
        return InstallerPeReport(
            product_name=product_name,
            version=normalized,
            publisher=publisher,
        )
    except (OSError, ValueError, ReleasePackageError) as error:
        raise ReleasePackageError("installer_pe_invalid") from error


def _safe_smoke_root(work_root: Path, *, source: Path, installer: Path) -> Path:
    target = Path(work_root).absolute()
    allowed_roots = ((ROOT / ".tmp").absolute(), Path(tempfile.gettempdir()).absolute())
    if (
        target == Path(target.anchor)
        or target.exists()
        or not any(
            target != allowed and target.is_relative_to(allowed) for allowed in allowed_roots
        )
        or target in {ROOT, ROOT.parent, source, installer.parent}
        or target.is_relative_to(source)
        or source.is_relative_to(target)
        or installer.is_relative_to(target)
        or '"' in str(target)
        or _has_reparse_ancestor(target.parent)
    ):
        raise ReleasePackageError("installer_smoke_root_unsafe")
    return target


def _read_uninstall_registration() -> dict[str, str] | None:
    if os.name != "nt":
        raise ReleasePackageError("installer_smoke_windows_required")
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_KEY)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ReleasePackageError("installer_smoke_registration_invalid") from error
    try:
        with key:
            values: dict[str, str] = {}
            for name in (
                "DisplayName",
                "DisplayVersion",
                "InstallLocation",
                "QuietUninstallString",
                "UninstallString",
            ):
                raw, kind = winreg.QueryValueEx(key, name)
                if kind not in {winreg.REG_SZ, winreg.REG_EXPAND_SZ} or not isinstance(raw, str):
                    raise ReleasePackageError("installer_smoke_registration_invalid")
                values[name] = raw
            return values
    except ReleasePackageError:
        raise
    except OSError as error:
        raise ReleasePackageError("installer_smoke_registration_invalid") from error


def _verify_uninstall_registration(*, version: str, install_root: Path) -> None:
    registration = _read_uninstall_registration()
    expected_uninstaller = install_root / "unins000.exe"
    if registration is None:
        raise ReleasePackageError("installer_smoke_registration_missing")
    install_value = registration["InstallLocation"].rstrip("\\/")
    expected_uninstall_string = f'"{expected_uninstaller}"'
    if (
        Path(install_value).absolute() != install_root.absolute()
        or registration["DisplayName"] != f"页舟 · FolioSkiff {version}"
        or registration["DisplayVersion"] != version
        or registration["UninstallString"] != expected_uninstall_string
        or registration["QuietUninstallString"] != f"{expected_uninstall_string} /SILENT"
    ):
        raise ReleasePackageError("installer_smoke_registration_invalid")


def _set_uninstall_display_version(value: str) -> str:
    if os.name != "nt" or not value or len(value) > 64:
        raise ReleasePackageError("installer_smoke_registration_invalid")
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _UNINSTALL_KEY,
            0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as key:
            original, kind = winreg.QueryValueEx(key, "DisplayVersion")
            if kind != winreg.REG_SZ or not isinstance(original, str):
                raise ReleasePackageError("installer_smoke_registration_invalid")
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, value)
            return original
    except ReleasePackageError:
        raise
    except OSError as error:
        raise ReleasePackageError("installer_smoke_registration_invalid") from error


def _start_menu_shortcut(group_name: str) -> Path:
    app_data = os.environ.get("APPDATA")
    if not app_data:
        raise ReleasePackageError("installer_smoke_shortcut_unavailable")
    return (
        Path(app_data)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / group_name
        / "FolioSkiff.lnk"
    ).absolute()


def _verify_shortcut(shortcut: Path, *, install_root: Path) -> None:
    if not shortcut.is_file() or _is_link_or_reparse(shortcut):
        raise ReleasePackageError("installer_smoke_shortcut_missing")
    system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
    if not system_root:
        raise ReleasePackageError("installer_smoke_powershell_missing")
    powershell = (
        Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    ).absolute()
    if not powershell.is_file() or _is_link_or_reparse(powershell):
        raise ReleasePackageError("installer_smoke_powershell_missing")
    script = (
        "$ErrorActionPreference='Stop';"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
        "$env:PAPER_READER_SHORTCUT);"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "@{target=$s.TargetPath;arguments=$s.Arguments;working=$s.WorkingDirectory}"
        "|ConvertTo-Json -Compress"
    )
    environment = _sanitized_packaging_environment()
    environment["PAPER_READER_SHORTCUT"] = str(shortcut)
    try:
        completed = subprocess.run(
            (
                str(powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ),
            cwd=install_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        require_successful_exit(completed.returncode, operation="installer_smoke_shortcut")
        if len(completed.stdout) > 16 * 1024:
            raise ReleasePackageError("installer_smoke_shortcut_invalid")
        payload = cast(Any, json.loads(completed.stdout.decode("utf-8")))
    except ReleasePackageError:
        raise
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as error:
        raise ReleasePackageError("installer_smoke_shortcut_invalid") from error
    if not isinstance(payload, dict) or set(payload) != {"arguments", "target", "working"}:
        raise ReleasePackageError("installer_smoke_shortcut_invalid")
    if (
        payload["target"] != str(install_root / "paper-reader.exe")
        or payload["arguments"] != "serve"
        or payload["working"] != str(install_root)
    ):
        raise ReleasePackageError("installer_smoke_shortcut_invalid")


def _run_release_process(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout: float,
) -> int:
    if not command or any(not item or "\x00" in item for item in command):
        raise ReleasePackageError("installer_smoke_command_invalid")
    try:
        completed = subprocess.run(
            tuple(command),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleasePackageError("installer_smoke_process_failed") from error


def _wait_for_ready_file(ready: Path, *, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        payload = cast(Any, json.loads(ready.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError) as error:
        raise ReleasePackageError("installer_smoke_app_not_ready") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"pid", "port", "schema_version"}
        or payload.get("schema_version") != "paper-reader-ready-v1"
        or payload.get("pid") != process.pid
        or not isinstance(payload.get("port"), int)
        or isinstance(payload.get("port"), bool)
        or not 0 < payload["port"] <= 65535
    ):
        raise ReleasePackageError("installer_smoke_app_not_ready")


def _installed_program_signature(install_root: Path) -> tuple[tuple[str, int, str], ...]:
    members = ("LICENSE", "paper-reader.exe", "release-manifest.json")
    try:
        return tuple(
            (name, (install_root / name).stat().st_size, _sha256_file(install_root / name))
            for name in members
        )
    except OSError as error:
        raise ReleasePackageError("installer_smoke_program_missing") from error


def _wait_for_uninstall(*, install_root: Path, shortcut: Path) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if (
            _read_uninstall_registration() is None
            and not (install_root / "paper-reader.exe").exists()
            and not shortcut.exists()
        ):
            return
        time.sleep(0.05)
    raise ReleasePackageError("installer_smoke_uninstall_incomplete")


def _best_effort_uninstall_test_install(
    *,
    install_root: Path,
    work_root: Path,
    environment: dict[str, str],
) -> None:
    uninstaller = install_root / "unins000.exe"
    if uninstaller.is_file() and not _is_link_or_reparse(uninstaller):
        with suppress(ReleasePackageError):
            _run_release_process(
                (
                    str(uninstaller),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                ),
                cwd=work_root,
                environment=environment,
                timeout=2 * 60,
            )


def _best_effort_remove_test_registration(*, install_root: Path) -> None:
    if os.name != "nt":
        return
    try:
        registration = _read_uninstall_registration()
    except ReleasePackageError:
        return
    if registration is None:
        return
    registered_root = Path(registration["InstallLocation"].rstrip("\\/")).absolute()
    if registered_root != install_root.absolute():
        return
    import winreg

    with suppress(OSError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_KEY)


def _best_effort_remove_shortcut_group(*, shortcut: Path, group_name: str) -> None:
    group = shortcut.parent
    app_data = os.environ.get("APPDATA")
    if not app_data:
        return
    programs = (Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs").absolute()
    if (
        group.parent != programs
        or group.name != group_name
        or not group_name.startswith("paper-reader-smoke-")
        or _is_link_or_reparse(group)
    ):
        return
    with suppress(OSError):
        _remove_tree(group)


def _atomic_publish_set(items: Sequence[tuple[Path, Path]]) -> None:
    backups: dict[Path, Path] = {}
    promoted: list[tuple[Path, Path]] = []
    try:
        for candidate, destination in items:
            if not candidate.is_file():
                raise OSError("candidate missing")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if _has_reparse_ancestor(destination.parent):
                raise OSError("destination parent is unsafe")
            if destination.exists():
                backup = destination.with_name(f".{destination.name}.previous-{token_hex(8)}")
                os.replace(destination, backup)
                backups[destination] = backup
            os.replace(candidate, destination)
            promoted.append((candidate, destination))
    except OSError:
        for candidate, destination in reversed(promoted):
            if destination.exists() and not candidate.exists():
                os.replace(destination, candidate)
        for destination, backup in backups.items():
            if backup.exists():
                if destination.exists():
                    destination.unlink()
                os.replace(backup, destination)
        raise
    else:
        for backup in backups.values():
            with suppress(OSError):
                backup.unlink()


def _sanitized_packaging_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    for name in (
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMSPEC",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "PATH",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "ProgramFiles",
        "ProgramFiles(x86)",
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
    environment.update({"NO_COLOR": "1", "PYTHONNOUSERSITE": "1", "PYTHONPATH": ""})
    return environment


def _portable_source(path: Path) -> Path:
    source = Path(path).absolute()
    if (
        source.name.casefold() != "paper-reader"
        or not source.is_dir()
        or _is_link_or_reparse(source)
    ):
        raise PortableArchiveError("portable_source_invalid")
    return source


def _collect_source_files(root: Path) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()
    try:
        for current, directory_names, file_names in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(current)
            directory_names.sort(key=str.casefold)
            file_names.sort(key=str.casefold)
            for name in directory_names:
                directory = current_path / name
                if _is_link_or_reparse(directory):
                    raise PortableArchiveError("portable_source_link")
                _register_source_name(root, directory, seen)
            for name in file_names:
                path = current_path / name
                if _is_link_or_reparse(path) or not path.is_file():
                    raise PortableArchiveError("portable_source_link")
                _register_source_name(root, path, seen)
                if path.stat().st_size > _MAX_ARCHIVE_FILE_BYTES:
                    raise PortableArchiveError("portable_member_size")
                result.append(path)
    except PortableArchiveError:
        raise
    except OSError as error:
        raise PortableArchiveError("portable_source_invalid") from error
    result.sort(key=lambda path: path.relative_to(root).as_posix().casefold())
    if not result or len(result) > _MAX_ARCHIVE_MEMBERS:
        raise PortableArchiveError("portable_member_count")
    return tuple(result)


def _register_source_name(root: Path, path: Path, seen: set[str]) -> None:
    relative = path.relative_to(root).as_posix()
    member = f"paper-reader/{relative}"
    _validate_portable_member(member)
    folded = member.casefold()
    if folded in seen:
        raise PortableArchiveError("portable_member_collision")
    seen.add(folded)


def _validate_portable_member(value: str) -> None:
    if not value or value != unicodedata.normalize("NFC", value) or "\\" in value or ":" in value:
        raise PortableArchiveError("portable_member_name")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or len(pure.parts) < 2
        or pure.parts[0] != "paper-reader"
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise PortableArchiveError("portable_member_name")


def _safe_output_file(path: Path, *, suffix: str) -> Path:
    target = Path(path).absolute()
    if target == Path(target.anchor) or target.suffix.casefold() != suffix:
        raise PortableArchiveError("portable_destination_unsafe")
    if _has_reparse_ancestor(target.parent):
        raise PortableArchiveError("portable_destination_unsafe")
    return target


def _safe_output_directory(path: Path, *, source: Path) -> Path:
    target = Path(path).absolute()
    if target == Path(target.anchor) or target in {ROOT, ROOT.parent, source}:
        raise ReleasePackageError("release_output_unsafe")
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ReleasePackageError("release_output_unsafe")
    if _has_reparse_ancestor(target):
        raise ReleasePackageError("release_output_unsafe")
    return target


def _parse_version(version: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(version)
    if match is None:
        raise ReleasePackageError("release_version_invalid")
    values = tuple(int(item) for item in match.groups())
    if any(value > 65535 for value in values):
        raise ReleasePackageError("release_version_invalid")
    return cast(tuple[int, int, int], values)


def _sha256_stream(stream: Any) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return _sha256_stream(stream)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        return bool(checker()) if checker is not None else False
    except OSError:
        return True


def _has_reparse_ancestor(path: Path) -> bool:
    current = Path(path).absolute()
    while True:
        if current.exists() and _is_link_or_reparse(current):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return

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
    parser.add_argument("distribution", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist/release")
    parser.add_argument("--iscc", type=Path, default=None)
    parser.add_argument("--portable-only", action="store_true")
    parser.add_argument("--python", type=Path, default=None)
    parser.add_argument("--npm", type=Path, default=None)
    parser.add_argument("--git-commit", default=None)
    parser.add_argument("--database-revision", default=None)
    parser.add_argument("--enable-signing", action="store_true")
    args = parser.parse_args()
    if not args.portable_only and any(
        value is None
        for value in (args.python, args.npm, args.git_commit, args.database_revision)
    ):
        print(
            json.dumps(
                {"ok": False, "error": "release_metadata_inputs_required"},
                sort_keys=True,
            )
        )
        return 1
    try:
        if args.portable_only:
            report: ReleasePackageReport | ReleaseBundleReport = package_distribution(
                distribution=args.distribution,
                output_dir=args.output_dir,
                iscc=args.iscc,
                portable_only=True,
            )
        else:
            sign_command = sign_command_from_environment(enabled=args.enable_signing)
            report = package_release_bundle(
                distribution=args.distribution,
                output_dir=args.output_dir,
                python_executable=cast(Path, args.python),
                npm_executable=cast(Path, args.npm),
                git_commit=cast(str, args.git_commit),
                database_revision=cast(str, args.database_revision),
                iscc=args.iscc,
                sign_command=sign_command,
            )
    except ReleasePackageError as error:
        print(json.dumps({"ok": False, "error": error.code}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "report": asdict(report)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
