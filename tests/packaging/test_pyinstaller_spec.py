from __future__ import annotations

import sqlite3
import struct
import tomllib
from pathlib import Path

import pytest

from paper_reader import __version__

ROOT = Path(__file__).parents[2]


def test_pyinstaller_spec_is_x64_windowed_onedir_and_model_free() -> None:
    spec = (ROOT / "packaging/pyinstaller/paper-reader.spec").read_text(encoding="utf-8")

    assert "Analysis(" in spec
    assert "PYZ(" in spec
    assert "EXE(" in spec
    assert "COLLECT(" in spec
    assert 'name="paper-reader"' in spec
    assert 'target_arch="x86_64"' in spec
    assert 'DIAGNOSTIC_CONSOLE = os.environ.get("PAPER_READER_DIAGNOSTIC_CONSOLE") == "1"' in spec
    assert "console=DIAGNOSTIC_CONSOLE" in spec
    assert "exclude_binaries=True" in spec
    assert "paper-reader.ico" in spec
    assert "version-info.txt" in spec
    assert "paper_reader/static" in spec
    assert "paper_reader/migrations" in spec
    assert "paper_reader/data" in spec
    assert "paper_reader/publishing/assets" in spec
    assert 'collect_data_files("rfc3987_syntax")' in spec
    assert 'collect_data_files("tzdata")' in spec
    assert 'collect_submodules("keyring.backends")' in spec
    assert 'collect_dynamic_libs("onnxruntime")' in spec
    assert 'collect_dynamic_libs("sqlite_vec")' in spec
    assert '"rapidocr.inference_engine.onnxruntime"' in spec
    assert 'collect_submodules("onnxruntime")' not in spec
    assert 'collect_submodules("rapidocr")' not in spec
    assert 'collect_all("rapidocr")' not in spec
    for excluded in ("pip", "setuptools", "pytest", "torch", "tensorflow", "tests"):
        assert f'"{excluded}"' in spec


def test_windows_build_uses_only_frozen_locked_extras() -> None:
    script = (ROOT / "scripts/build_windows.py").read_text(encoding="utf-8")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "uv" in script
    assert "sync" in script
    assert "--frozen" in script
    assert "--no-dev" in script
    assert script.count('"--extra"') >= 3
    for extra in ("packaging", "models", "ocr"):
        assert f'"{extra}"' in script
        assert extra in project["project"]["optional-dependencies"]
    assert "pyinstaller" in " ".join(project["project"]["optional-dependencies"]["packaging"])


def test_frozen_smoke_verifies_runtime_compatibility_contracts() -> None:
    from paper_reader.migrations.versions import FROZEN_DATABASE_HEAD
    from paper_reader.openapi import API_CONTRACT_VERSION, OPENAPI_CONTRACT_SHA256
    from paper_reader.plugins.contracts import PLUGIN_PROTOCOL_VERSION

    script = (ROOT / "scripts/build_windows.py").read_text(encoding="utf-8")

    for key, constant_name, value in (
        ("api_version", "API_CONTRACT_VERSION", API_CONTRACT_VERSION),
        ("openapi_sha256", "OPENAPI_CONTRACT_SHA256", OPENAPI_CONTRACT_SHA256),
        ("database_schema_revision", "FROZEN_DATABASE_HEAD", FROZEN_DATABASE_HEAD),
        ("plugin_protocol_version", "PLUGIN_PROTOCOL_VERSION", PLUGIN_PROTOCOL_VERSION),
    ):
        assert f'"{key}": {constant_name}' in script
        assert isinstance(value, str) and value


def test_strict_dev_typecheck_installs_packaging_script_imports() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    optional = project["project"]["optional-dependencies"]

    assert "pillow" in " ".join(optional["packaging"]).casefold()
    assert "pillow" in " ".join(optional["dev"]).casefold()


def test_windows_version_resource_matches_current_project_version() -> None:
    source = (ROOT / "packaging/windows/version-info.txt").read_text(encoding="utf-8")
    major, minor, patch = (int(part) for part in __version__.split("."))

    assert f"filevers=({major}, {minor}, {patch}, 0)" in source
    assert f"prodvers=({major}, {minor}, {patch}, 0)" in source
    assert "FolioSkiff contributors" in source
    assert "FolioSkiff local research workspace" in source
    assert "paper-reader.exe" in source
    assert "Licensed under Apache-2.0" in source
    assert f"{__version__}.0" in source


def test_windows_icon_contains_16_through_256_pixel_frames() -> None:
    payload = (ROOT / "packaging/windows/paper-reader.ico").read_bytes()
    reserved, image_type, count = struct.unpack_from("<HHH", payload)
    assert (reserved, image_type) == (0, 1)
    assert count >= 7

    sizes: set[int] = set()
    depths: set[int] = set()
    for index in range(count):
        width, height, _colors, _reserved, _planes, depth, length, offset = struct.unpack_from(
            "<BBBBHHII", payload, 6 + index * 16
        )
        decoded_width = width or 256
        decoded_height = height or 256
        assert decoded_width == decoded_height
        assert offset + length <= len(payload)
        sizes.add(decoded_width)
        depths.add(depth)

    assert {16, 24, 32, 48, 64, 128, 256}.issubset(sizes)
    assert depths == {32}


def test_icon_provenance_and_distribution_notices_are_explicit() -> None:
    notices = (ROOT / "packaging/THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "FolioSkiff application icon" in notices
    assert "original project artwork" in notices
    assert "Apache-2.0" in notices
    assert "model files are not included" in notices.lower()


def test_build_environment_drops_parent_paths_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts import build_windows

    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "foreign-python"))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-enter-build")
    monkeypatch.setenv("VITE_PRIVATE_VALUE", "must-not-enter-web-build")
    monkeypatch.setenv("CUDA_PATH", str(tmp_path / "foreign-dlls"))

    environment = build_windows._sanitized_build_environment(virtual_environment=tmp_path / "venv")

    assert environment["PYTHONPATH"] == ""
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["VIRTUAL_ENV"] == str(tmp_path / "venv")
    assert "OPENAI_API_KEY" not in environment
    assert "VITE_PRIVATE_VALUE" not in environment
    assert "CUDA_PATH" not in environment


def test_failed_atomic_publish_restores_previous_distribution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts import build_windows

    destination = tmp_path / "paper-reader"
    destination.mkdir()
    (destination / "state.txt").write_text("previous", encoding="utf-8")
    candidate = tmp_path / ".paper-reader.candidate"
    candidate.mkdir()
    (candidate / "state.txt").write_text("candidate", encoding="utf-8")
    real_replace = build_windows.os.replace
    failed = False

    def fail_candidate(source: object, target: object) -> None:
        nonlocal failed
        if Path(source) == candidate and not failed:
            failed = True
            raise OSError("injected promotion failure")
        real_replace(source, target)

    monkeypatch.setattr(build_windows.os, "replace", fail_candidate)
    with pytest.raises(OSError, match="injected promotion failure"):
        build_windows._atomic_publish(candidate, destination)

    assert (destination / "state.txt").read_text(encoding="utf-8") == "previous"
    assert (candidate / "state.txt").read_text(encoding="utf-8") == "candidate"


def test_smoke_sqlite_probe_releases_windows_file_handle(tmp_path: Path) -> None:
    from scripts import build_windows

    database = tmp_path / "smoke.sqlite3"
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    finally:
        connection.close()
    assert build_windows._sqlite_journal_mode(database) == "wal"

    # Windows refuses this unlink if the probe retained its sqlite connection.
    database.unlink()
    assert not database.exists()


@pytest.mark.parametrize("helper", ["_safe_destination", "_safe_workspace"])
def test_windows_build_rejects_filesystem_root(helper: str) -> None:
    from scripts import build_windows

    filesystem_root = Path(Path.cwd().anchor)
    with pytest.raises(build_windows.WindowsBuildError, match="unsafe"):
        getattr(build_windows, helper)(filesystem_root)


def test_staging_cleanup_retries_transient_windows_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts import build_windows

    target = tmp_path / "staging-transient"
    target.mkdir()
    (target / "database.sqlite3").write_bytes(b"closed")
    real_rmtree = build_windows.shutil.rmtree
    attempts = 0

    def transient_rmtree(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("injected transient scanner lock")
        real_rmtree(*args, **kwargs)

    monkeypatch.setattr(build_windows.shutil, "rmtree", transient_rmtree)
    monkeypatch.setattr(build_windows.time, "sleep", lambda _seconds: None)

    build_windows._remove_tree(target)

    assert attempts == 3
    assert not target.exists()
