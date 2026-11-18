from __future__ import annotations

from pathlib import Path

import pytest

from paper_reader.runtime_config import RuntimeConfigStore, RuntimeConfigUpdate


def test_runtime_config_persists_only_absolute_cache_and_library_roots(
    tmp_path: Path,
) -> None:
    store = RuntimeConfigStore(tmp_path / "state")

    saved = store.save(
        RuntimeConfigUpdate(
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "papers",
        )
    )

    assert saved.version == 1
    assert saved.cache_root == (tmp_path / "cache").resolve()
    assert saved.library_root == (tmp_path / "papers").resolve()
    assert store.load() == saved
    assert not (tmp_path / "state" / "runtime-config.json.part").exists()


def test_runtime_config_rejects_relative_or_state_nested_library(
    tmp_path: Path,
) -> None:
    store = RuntimeConfigStore(tmp_path / "state")

    with pytest.raises(ValueError, match="absolute"):
        store.save(RuntimeConfigUpdate(library_root=Path("papers")))
    with pytest.raises(ValueError, match="outside the state root"):
        store.save(RuntimeConfigUpdate(library_root=tmp_path / "state" / "papers"))


@pytest.mark.parametrize(
    "payload",
    [
        "{not-json",
        '{"version":1,"library_root":null,"cache_root":null,"secret":"no"}',
    ],
)
def test_runtime_config_rejects_malformed_or_unknown_fields(
    tmp_path: Path,
    payload: str,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    (state_root / "runtime-config.json").write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match="runtime configuration"):
        RuntimeConfigStore(state_root).load()


def test_runtime_config_rejects_oversized_files(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    (state_root / "runtime-config.json").write_bytes(b" " * (64 * 1024 + 1))

    with pytest.raises(ValueError, match="64 KiB"):
        RuntimeConfigStore(state_root).load()


def test_environment_paths_override_persisted_runtime_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from paper_reader.settings import AppPaths

    state_root = tmp_path / "state"
    RuntimeConfigStore(state_root).save(
        RuntimeConfigUpdate(
            cache_root=tmp_path / "configured-cache",
            library_root=tmp_path / "configured-library",
        )
    )
    monkeypatch.setenv("PAPER_READER_STATE_ROOT", str(state_root))
    monkeypatch.setenv("PAPER_READER_CACHE_ROOT", str(tmp_path / "environment-cache"))
    monkeypatch.setenv(
        "PAPER_READER_LIBRARY_ROOT",
        str(tmp_path / "environment-library"),
    )

    paths = AppPaths.default()

    assert paths.cache_root == tmp_path / "environment-cache"
    assert paths.library_root == tmp_path / "environment-library"


def test_persisted_runtime_paths_override_platform_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from paper_reader.settings import AppPaths

    state_root = tmp_path / "state"
    RuntimeConfigStore(state_root).save(
        RuntimeConfigUpdate(
            cache_root=tmp_path / "configured-cache",
            library_root=tmp_path / "configured-library",
        )
    )
    monkeypatch.setenv("PAPER_READER_STATE_ROOT", str(state_root))
    monkeypatch.delenv("PAPER_READER_CACHE_ROOT", raising=False)
    monkeypatch.delenv("PAPER_READER_LIBRARY_ROOT", raising=False)

    paths = AppPaths.default()

    assert paths.cache_root == (tmp_path / "configured-cache").resolve()
    assert paths.library_root == (tmp_path / "configured-library").resolve()
