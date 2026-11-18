from __future__ import annotations

from pathlib import Path

import pytest


def test_app_paths_create_separate_state_cache_and_library_roots(tmp_path: Path) -> None:
    from paper_reader.settings import AppPaths

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )

    paths.ensure()

    assert paths.database == tmp_path / "state" / "paper-reader.sqlite3"
    assert paths.logs == tmp_path / "state" / "logs"
    assert paths.models == tmp_path / "cache" / "models"
    assert paths.objects == tmp_path / "library" / "objects"
    assert paths.backups == tmp_path / "library" / "backups"
    assert all(path.exists() for path in paths.directories())


def test_app_paths_accept_absolute_smoke_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from paper_reader.settings import AppPaths

    expected = {
        "PAPER_READER_STATE_ROOT": tmp_path / "state",
        "PAPER_READER_CACHE_ROOT": tmp_path / "cache",
        "PAPER_READER_LIBRARY_ROOT": tmp_path / "library",
    }
    for name, value in expected.items():
        monkeypatch.setenv(name, str(value))

    paths = AppPaths.default()

    assert paths.state_root == expected["PAPER_READER_STATE_ROOT"]
    assert paths.cache_root == expected["PAPER_READER_CACHE_ROOT"]
    assert paths.library_root == expected["PAPER_READER_LIBRARY_ROOT"]


@pytest.mark.parametrize(
    "name",
    ["PAPER_READER_STATE_ROOT", "PAPER_READER_CACHE_ROOT", "PAPER_READER_LIBRARY_ROOT"],
)
def test_app_paths_reject_relative_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    from paper_reader.settings import AppPaths

    monkeypatch.setenv(name, "relative/private-path")

    with pytest.raises(ValueError, match=f"{name} must be an absolute path"):
        AppPaths.default()
