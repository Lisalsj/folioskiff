"""Validated runtime path configuration stored outside the application database."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

_MAX_CONFIG_BYTES = 64 * 1024
_CONFIG_NAME = "runtime-config.json"


class RuntimeConfig(BaseModel):
    """Closed, versioned configuration that never contains credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1, 2] = 1
    data_root: Path | None = None
    cache_root: Path | None = None
    library_root: Path | None = None
    legacy_cache_root: Path | None = None
    legacy_library_root: Path | None = None


class RuntimeConfigUpdate(BaseModel):
    """Complete update for user-selectable runtime paths."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data_root: Path | None = None
    # Kept for one release so older clients can still publish a legacy layout.
    cache_root: Path | None = None
    library_root: Path | None = None
    legacy_cache_root: Path | None = None
    legacy_library_root: Path | None = None


class RuntimeConfigStore:
    """Load and atomically publish runtime paths under a fixed state root."""

    def __init__(self, state_root: Path) -> None:
        raw_root = Path(state_root)
        if not raw_root.is_absolute():
            raise ValueError("state root must be an absolute path")
        self.state_root = raw_root.resolve(strict=False)
        self.path = self.state_root / _CONFIG_NAME
        self.part_path = self.state_root / f"{_CONFIG_NAME}.part"

    def load(self) -> RuntimeConfig:
        if not self.path.exists():
            return RuntimeConfig()
        if self.path.stat().st_size > _MAX_CONFIG_BYTES:
            raise ValueError("runtime configuration exceeds 64 KiB")

        try:
            payload = self.path.read_text(encoding="utf-8")
            config = RuntimeConfig.model_validate_json(payload)
            return self._validated(config)
        except (OSError, UnicodeError, ValidationError, ValueError) as exc:
            if isinstance(exc, ValueError) and "64 KiB" in str(exc):
                raise
            raise ValueError("invalid runtime configuration") from exc

    def save(self, update: RuntimeConfigUpdate) -> RuntimeConfig:
        if update.data_root is not None and (
            update.cache_root is not None or update.library_root is not None
        ):
            raise ValueError("data root cannot be combined with legacy roots")
        config = self._validated(
            RuntimeConfig(
                version=2 if update.data_root is not None else 1,
                data_root=update.data_root,
                cache_root=update.cache_root,
                library_root=update.library_root,
                legacy_cache_root=update.legacy_cache_root,
                legacy_library_root=update.legacy_library_root,
            )
        )
        encoded = (
            json.dumps(
                config.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_CONFIG_BYTES:
            raise ValueError("runtime configuration exceeds 64 KiB")

        self.state_root.mkdir(parents=True, exist_ok=True)
        try:
            with self.part_path.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(self.part_path, self.path)
        finally:
            self.part_path.unlink(missing_ok=True)
        return config

    def _validated(self, config: RuntimeConfig) -> RuntimeConfig:
        data_root = self._absolute(config.data_root, "data root")
        if data_root is not None and self._contains(self.state_root, data_root):
            raise ValueError("data root must be outside the state root")
        cache_root = self._absolute(config.cache_root, "cache root")
        library_root = self._absolute(config.library_root, "library root")
        legacy_cache_root = self._absolute(config.legacy_cache_root, "legacy cache root")
        legacy_library_root = self._absolute(config.legacy_library_root, "legacy library root")
        if data_root is not None and (cache_root is not None or library_root is not None):
            raise ValueError("data root cannot be combined with legacy roots")
        if library_root is not None and self._contains(self.state_root, library_root):
            raise ValueError("library root must be outside the state root")
        return RuntimeConfig(
            version=config.version,
            data_root=data_root,
            cache_root=cache_root,
            library_root=library_root,
            legacy_cache_root=legacy_cache_root,
            legacy_library_root=legacy_library_root,
        )

    @staticmethod
    def _absolute(path: Path | None, label: str) -> Path | None:
        if path is None:
            return None
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ValueError(f"{label} must be absolute")
        if _has_reparse_point(candidate):
            raise ValueError(f"{label} cannot be a symbolic link")
        return candidate.resolve(strict=False)

    @staticmethod
    def _contains(parent: Path, candidate: Path) -> bool:
        try:
            candidate.relative_to(parent)
        except ValueError:
            return False
        return True


def _has_reparse_point(path: Path) -> bool:
    current = path
    while True:
        try:
            if current.is_symlink():
                return True
            is_junction = getattr(current, "is_junction", None)
            if callable(is_junction) and is_junction():
                return True
        except OSError as error:
            raise ValueError("runtime path cannot be inspected safely") from error
        parent = current.parent
        if parent == current:
            return False
        current = parent
