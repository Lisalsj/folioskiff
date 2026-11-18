"""Filesystem and runtime settings for the local application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_path, user_data_path, user_documents_path

from paper_reader.runtime_config import RuntimeConfigStore


def _optional_environment(name: str) -> str | None:
    value = os.environ.get(name)
    cleaned = value.strip() if value is not None else ""
    return cleaned or None


def _optional_absolute_path(name: str) -> Path | None:
    value = _optional_environment(name)
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path


@dataclass(frozen=True, slots=True)
class SourceCredentials:
    """Process-local integration credentials; never persisted in the application database."""

    ieee_api_key: str | None = None
    springer_nature_api_key: str | None = None
    openreview_access_token: str | None = None
    openalex_api_key: str | None = None
    semantic_scholar_api_key: str | None = None
    crossref_contact_email: str | None = None
    crossref_plus_api_token: str | None = None

    @classmethod
    def from_environment(cls) -> SourceCredentials:
        return cls(
            ieee_api_key=_optional_environment("IEEE_XPLORE_API_KEY"),
            springer_nature_api_key=_optional_environment("SPRINGER_NATURE_API_KEY"),
            openreview_access_token=_optional_environment("OPENREVIEW_ACCESS_TOKEN"),
            openalex_api_key=_optional_environment("OPENALEX_API_KEY"),
            semantic_scholar_api_key=_optional_environment("SEMANTIC_SCHOLAR_API_KEY"),
            crossref_contact_email=_optional_environment("CROSSREF_CONTACT_EMAIL"),
            crossref_plus_api_token=_optional_environment("CROSSREF_PLUS_API_TOKEN"),
        )


@dataclass(frozen=True, slots=True)
class AppPaths:
    state_root: Path
    cache_root: Path
    library_root: Path
    data_root: Path | None = None

    @classmethod
    def bootstrap_state_root(cls) -> Path:
        """Stable lock and redirect location, independent of the selected data root."""
        return (_optional_absolute_path("PAPER_READER_STATE_ROOT") or
                user_data_path("paper-reader", appauthor=False)).resolve(strict=False)

    @classmethod
    def default(cls) -> AppPaths:
        explicit_state = _optional_absolute_path("PAPER_READER_STATE_ROOT")
        explicit_data = _optional_absolute_path("PAPER_READER_DATA_ROOT")
        legacy_state = cls.bootstrap_state_root()
        legacy_layout_requested = any(
            _optional_absolute_path(name) is not None
            for name in ("PAPER_READER_CACHE_ROOT", "PAPER_READER_LIBRARY_ROOT")
        ) or explicit_state is not None
        from paper_reader.runtime_migration import migrate_data_root, pending_data_root

        visited: set[Path] = set()
        while True:
            if legacy_state in visited or len(visited) >= 16:
                raise ValueError("runtime data-root redirects contain a cycle")
            visited.add(legacy_state)
            pending = pending_data_root(legacy_state)
            if pending is not None:
                paths = cls.from_data_root(pending)
                migrate_data_root(source_state=legacy_state, target=paths)
                return paths
            runtime_config = RuntimeConfigStore(legacy_state).load()
            data_root = runtime_config.data_root
            if data_root is None:
                break
            target_state = data_root / "state"
            if target_state == legacy_state:
                break
            if (legacy_state / "runtime-redirect.json").is_file():
                legacy_state = target_state
                continue
            break
        data_root = explicit_data or runtime_config.data_root
        if data_root is None and not legacy_layout_requested:
            data_root = (user_documents_path() / "paper-reader").resolve(strict=False)
        if data_root is not None:
            data_root = Path(data_root).resolve(strict=False)
            paths = cls.from_data_root(data_root)
            if paths.state_root != legacy_state:
                migrate_data_root(
                    source_state=legacy_state,
                    target=paths,
                )
            return paths
        return cls(
            state_root=legacy_state,
            cache_root=_optional_absolute_path("PAPER_READER_CACHE_ROOT")
            or runtime_config.cache_root
            or user_cache_path("paper-reader", appauthor=False),
            library_root=_optional_absolute_path("PAPER_READER_LIBRARY_ROOT")
            or runtime_config.library_root
            or user_documents_path() / "paper-reader",
        )

    @classmethod
    def from_data_root(cls, data_root: Path) -> AppPaths:
        root = Path(data_root).resolve(strict=False)
        return cls(
            state_root=root / "state",
            cache_root=root / "cache",
            library_root=root / "library",
            data_root=root,
        )

    @classmethod
    def from_overrides(
        cls,
        *,
        state_root: Path,
        cache_root: Path,
        library_root: Path,
    ) -> AppPaths:
        return cls(
            state_root=Path(state_root),
            cache_root=Path(cache_root),
            library_root=Path(library_root),
        )

    @property
    def database(self) -> Path:
        return self.state_root / "paper-reader.sqlite3"

    @property
    def logs(self) -> Path:
        return self.state_root / "logs"

    @property
    def models(self) -> Path:
        return self.cache_root / "models"

    @property
    def objects(self) -> Path:
        return self.library_root / "objects"

    @property
    def backups(self) -> Path:
        return self.library_root / "backups"

    @property
    def migration_snapshots(self) -> Path:
        return self.state_root / "migration-snapshots"

    def directories(self) -> tuple[Path, ...]:
        return (
            self.state_root,
            self.logs,
            self.cache_root,
            self.models,
            self.library_root,
            self.objects,
            self.backups,
            self.migration_snapshots,
        )

    def ensure(self) -> None:
        for directory in self.directories():
            directory.mkdir(parents=True, exist_ok=True)
