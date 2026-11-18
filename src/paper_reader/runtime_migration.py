"""Recoverable data-root migration, completed only after startup health checks."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryFile
from typing import TYPE_CHECKING, Literal

from platformdirs import user_cache_path, user_documents_path
from pydantic import BaseModel, ConfigDict, Field

from paper_reader.runtime_config import (
    RuntimeConfig,
    RuntimeConfigStore,
    RuntimeConfigUpdate,
    _has_reparse_point,
)

if TYPE_CHECKING:
    from paper_reader.settings import AppPaths

_JOURNAL_NAME = ".data-root-migration.json"
_MAX_JOURNAL_BYTES = 4 * 1024 * 1024
_BOOTSTRAP_FILES = {
    _JOURNAL_NAME, f"{_JOURNAL_NAME}.part", "runtime-config.json",
    "runtime-config.json.part", "runtime-redirect.json", "instance.lock", "instance.json",
}


class DataRootMigrationError(ValueError):
    """Migration cannot safely proceed; journal and source remain recoverable."""


class _Transfer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Path
    destination: Path
    status: Literal["pending", "moving", "renamed", "copied"] = "pending"


class DataRootMigrationJournal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    phase: Literal["moving", "awaiting_health", "committed"] = "moving"
    source_state: Path
    source_cache: Path
    source_library: Path
    target_root: Path
    rollback_config: RuntimeConfig
    transfers: list[_Transfer] = Field(default_factory=list)
    created_directories: list[Path] = Field(default_factory=list)


def _safe_path(path: Path) -> Path:
    if not path.is_absolute() or _has_reparse_point(path):
        raise DataRootMigrationError("目录必须是绝对路径，且不能经过符号链接或目录联接。")
    return path.resolve(strict=False)


def _contains(parent: Path, candidate: Path) -> bool:
    return candidate == parent or parent in candidate.parents


def _assert_plain_tree(path: Path) -> None:
    _safe_path(path)
    if not path.exists():
        return
    if not path.is_file() and not path.is_dir():
        raise DataRootMigrationError("迁移目录包含不支持的文件类型。")
    if path.is_dir():
        for directory, folders, files in os.walk(path, followlinks=False):
            for name in (*folders, *files):
                child = Path(directory) / name
                if _has_reparse_point(child) or not (child.is_file() or child.is_dir()):
                    raise DataRootMigrationError("迁移目录内含符号链接、目录联接或特殊文件。")


def validate_data_root_change(paths: AppPaths, target_root: Path) -> None:
    """Validate before staging; do not change the user's current configuration."""
    root = _safe_path(target_root)
    if paths.data_root == root:
        return
    state, cache, library = (
        _safe_path(path) for path in (paths.state_root, paths.cache_root, paths.library_root)
    )
    for index, first in enumerate((state, cache, library)):
        for second in (state, cache, library)[index + 1:]:
            if not (_contains(first, second) or _contains(second, first)):
                continue
            # The Windows 3.0 default kept its cache at ``state/Cache``.
            # This is a supported legacy layout and must be split into the
            # new root before the state tree is moved.  Other overlaps are
            # ambiguous (and could cause a directory to be copied twice), so
            # continue rejecting them.
            if first == state and second == cache and cache.parent == state:
                continue
            raise DataRootMigrationError("旧状态、缓存和论文库目录不能相互嵌套。")
    for source in (state, cache, library):
        if source == library == root:
            continue  # The 3.0 default library becomes the 3.1 root.
        if _contains(source, root) or _contains(root, source):
            raise DataRootMigrationError("新数据根目录不能与现有数据目录重叠。")
    for name in ("state", "cache", "library"):
        destination = _safe_path(root / name)
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise DataRootMigrationError("新数据根目录已有同名数据，请选择空目录。")
    parent = root
    while not parent.exists():
        parent = parent.parent
    try:
        with TemporaryFile(dir=parent):
            pass
    except OSError as error:
        raise DataRootMigrationError("数据根目录不可写，请选择其他目录。") from error


def pending_data_root(source_state: Path) -> Path | None:
    """Recover interrupted transfers before SQLite is opened."""
    journal = _read_journal(source_state / _JOURNAL_NAME)
    if journal is None:
        return None
    if journal.source_state != source_state.resolve(strict=False):
        raise DataRootMigrationError("迁移日志与启动目录不匹配。")
    if journal.phase == "moving":
        _rollback(journal)
    elif journal.phase == "committed":
        _finish_cleanup(journal)
    return journal.target_root


def migrate_data_root(*, source_state: Path, target: AppPaths) -> None:
    source_state = _safe_path(source_state)
    root = _safe_path(target.data_root or target.state_root.parent)
    if source_state == target.state_root:
        return
    existing = _read_journal(source_state / _JOURNAL_NAME)
    if existing is not None:
        if existing.target_root != root:
            raise DataRootMigrationError("请先完成或恢复上一次数据迁移。")
        if existing.phase == "moving":
            _rollback(existing)
        elif existing.phase == "committed":
            _finish_cleanup(existing)
            return
        else:
            return  # Verified destination awaits application startup health.
    config = RuntimeConfigStore(source_state).load()
    cache = _safe_path(config.legacy_cache_root or config.cache_root or
                       user_cache_path("paper-reader", appauthor=False))
    library = _safe_path(config.legacy_library_root or config.library_root or
                         user_documents_path() / "paper-reader")
    from paper_reader.settings import AppPaths

    current = AppPaths.from_overrides(
        state_root=source_state, cache_root=cache, library_root=library,
    )
    validate_data_root_change(current, root)
    for path in (source_state, cache, library):
        _assert_plain_tree(path)
    previous_root = source_state.parent
    rollback_config = (
        RuntimeConfig(version=2, data_root=previous_root)
        if cache == previous_root / "cache" and library == previous_root / "library"
        else RuntimeConfig(cache_root=cache, library_root=library)
    )
    journal = DataRootMigrationJournal(
        source_state=source_state, source_cache=cache, source_library=library,
        target_root=root, rollback_config=rollback_config,
    )
    if cache.exists():
        journal.transfers.append(_Transfer(source=cache, destination=target.cache_root))
    if library == root:
        journal.transfers.extend(
            _Transfer(source=item, destination=target.library_root / item.name)
            for item in library.iterdir()
            if item.name not in {"state", "cache", "library"}
        )
    elif library.exists():
        journal.transfers.append(_Transfer(source=library, destination=target.library_root))
    if source_state.exists():
        journal.transfers.extend(
            _Transfer(source=item, destination=target.state_root / item.name)
            for item in source_state.iterdir()
            if item.name not in _BOOTSTRAP_FILES and item != cache
        )
    for path in (root, target.state_root, target.cache_root, target.library_root):
        if not path.exists():
            journal.created_directories.append(path)
    _write_journal(journal)
    try:
        for transfer in journal.transfers:
            transfer.status = "moving"
            _write_journal(journal)
            _transfer(transfer)
            _write_journal(journal)
        for path in (target.state_root, target.cache_root, target.library_root):
            path.mkdir(parents=True, exist_ok=True)
        RuntimeConfigStore(target.state_root).save(RuntimeConfigUpdate(data_root=root))
        journal.phase = "awaiting_health"
        _write_journal(journal)
    except Exception as error:
        _rollback(journal)
        raise DataRootMigrationError("数据迁移失败，已恢复旧目录。") from error


def complete_data_root_migration(paths: AppPaths) -> None:
    journal = _read_journal(paths.state_root / _JOURNAL_NAME)
    if journal is None:
        # The source journal is written first.  A process can stop between
        # that write and the mirror in the new state directory.
        from paper_reader.settings import AppPaths

        candidate = AppPaths.bootstrap_state_root()
        visited: set[Path] = set()
        while candidate not in visited and len(visited) < 16:
            visited.add(candidate)
            source_journal = _read_journal(candidate / _JOURNAL_NAME)
            if (
                source_journal is not None
                and source_journal.target_root / "state" == paths.state_root
            ):
                journal = source_journal
                break
            config = RuntimeConfigStore(candidate).load()
            if config.data_root is None or not (candidate / "runtime-redirect.json").is_file():
                break
            candidate = config.data_root / "state"
    if journal is None:
        return
    if journal.target_root / "state" != paths.state_root:
        raise DataRootMigrationError("迁移目标与应用状态目录不匹配。")
    # Called at the end of lifespan startup, before requests can be accepted.
    try:
        if not paths.database.is_file():
            raise DataRootMigrationError("迁移后的数据库不存在。")
        connection = sqlite3.connect(paths.database)
        try:
            try:
                healthy = connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
            except sqlite3.DatabaseError as error:
                raise DataRootMigrationError("迁移后的数据库健康检查失败。") from error
        finally:
            connection.close()
        if not healthy:
            raise DataRootMigrationError("迁移后的数据库健康检查失败。")
    except Exception:
        _rollback(journal)
        raise
    journal.phase = "committed"
    _write_journal(journal)
    _finish_cleanup(journal)


def rollback_data_root_migration(paths: AppPaths) -> None:
    journal = _read_journal(paths.state_root / _JOURNAL_NAME)
    if journal is not None and journal.phase != "committed":
        _rollback(journal)


def _transfer(item: _Transfer) -> None:
    _assert_plain_tree(item.source)
    _safe_path(item.destination)
    item.destination.parent.mkdir(parents=True, exist_ok=True)
    if item.destination.exists():
        if not item.destination.is_dir() or any(item.destination.iterdir()):
            raise DataRootMigrationError("迁移目标存在冲突。")
        item.destination.rmdir()
    try:
        os.rename(item.source, item.destination)
        item.status = "renamed"
    except OSError as error:
        if error.errno != errno.EXDEV and getattr(error, "winerror", None) != 17:
            raise
        _copy_verified(item.source, item.destination)
        item.status = "copied"  # Originals remain until health succeeds.


def _copy_verified(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=False)
    else:
        shutil.copy2(source, destination)
    if _tree_digest(source) != _tree_digest(destination):
        raise DataRootMigrationError("迁移副本校验失败。")


def _tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    items = [path] if path.is_file() else sorted(path.rglob("*"))
    for item in items:
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"f" if item.is_file() else b"d")
        if item.is_file():
            with item.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _remove_tree(path: Path) -> None:
    _assert_plain_tree(path)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _rollback(journal: DataRootMigrationJournal) -> None:
    _validate_journal(journal)
    try:
        for item in reversed(journal.transfers):
            if item.status == "pending" or not item.destination.exists():
                continue
            if item.source.exists():
                # A cross-volume copy may be incomplete; the original is intact.
                _remove_tree(item.destination)
            else:
                reverse = _Transfer(source=item.destination, destination=item.source)
                _transfer(reverse)
                if reverse.status == "copied":
                    _remove_tree(item.destination)
        source_store = RuntimeConfigStore(journal.source_state)
        source_store.save(RuntimeConfigUpdate(**journal.rollback_config.model_dump(
            exclude={"version"},
        )))
        target_state = journal.target_root / "state"
        for path in (target_state / "runtime-config.json", target_state / _JOURNAL_NAME,
                     journal.source_state / _JOURNAL_NAME):
            path.unlink(missing_ok=True)
        for path in reversed(journal.created_directories):
            with suppress(OSError):
                path.rmdir()
    except Exception as error:
        raise DataRootMigrationError("迁移恢复未完成；原始数据和迁移日志已保留。") from error


def _finish_cleanup(journal: DataRootMigrationJournal) -> None:
    _validate_journal(journal)
    # Commit the bootstrap pointer before any cross-volume original is removed.
    RuntimeConfigStore(journal.source_state).save(
        RuntimeConfigUpdate(data_root=journal.target_root)
    )
    marker = journal.source_state / "runtime-redirect.json"
    marker.write_text(json.dumps({"version": 1, "data_root": str(journal.target_root)}),
                      encoding="utf-8")
    for item in journal.transfers:
        if item.status == "copied" and item.source.exists():
            _remove_tree(item.source)
    for root in (journal.source_cache, journal.source_library):
        if root != journal.target_root:
            with suppress(OSError):
                root.rmdir()
    (journal.source_state / _JOURNAL_NAME).unlink(missing_ok=True)
    (journal.target_root / "state" / _JOURNAL_NAME).unlink(missing_ok=True)


def _validate_journal(journal: DataRootMigrationJournal) -> None:
    for path in (journal.source_state, journal.source_cache, journal.source_library,
                 journal.target_root):
        _safe_path(path)
    if _contains(journal.target_root, journal.source_state):
        raise DataRootMigrationError("迁移日志包含重叠目录。")
    for item in journal.transfers:
        _safe_path(item.source)
        _safe_path(item.destination)
        valid = (
            (item.source == journal.source_cache and
             item.destination == journal.target_root / "cache") or
            (item.source == journal.source_library and item.source != journal.target_root and
             item.destination == journal.target_root / "library") or
            (item.source.parent == journal.source_library == journal.target_root and
             item.source.name not in {"state", "cache", "library"} and
             item.destination == journal.target_root / "library" / item.source.name) or
            (item.source.parent == journal.source_state and
             item.source.name not in _BOOTSTRAP_FILES and
             item.source != journal.source_cache and
             item.destination == journal.target_root / "state" / item.source.name)
        )
        if not valid:
            raise DataRootMigrationError("迁移日志包含越界文件。")
    allowed = {journal.target_root, *(journal.target_root / name
                                    for name in ("state", "cache", "library"))}
    if any(path not in allowed for path in journal.created_directories):
        raise DataRootMigrationError("迁移日志包含越界目录。")


def _read_journal(path: Path) -> DataRootMigrationJournal | None:
    _safe_path(path)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > _MAX_JOURNAL_BYTES:
            raise ValueError("oversized journal")
        journal = DataRootMigrationJournal.model_validate_json(path.read_bytes())
        _validate_journal(journal)
        return journal
    except (OSError, ValueError) as error:
        raise DataRootMigrationError("迁移日志无效；请保留旧目录以便恢复。") from error


def _write_journal(journal: DataRootMigrationJournal) -> None:
    _validate_journal(journal)
    encoded = journal.model_dump_json().encode("utf-8")
    if len(encoded) > _MAX_JOURNAL_BYTES:
        raise DataRootMigrationError("迁移日志超过大小限制。")
    paths = [journal.source_state / _JOURNAL_NAME]
    if journal.phase != "moving":
        paths.append(journal.target_root / "state" / _JOURNAL_NAME)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.part")
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
