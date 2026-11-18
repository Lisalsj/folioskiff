"""Canonical manifest models for portable paper-reader backups."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

BACKUP_FORMAT_VERSION = "paper-reader-backup-v1"
MIGRATION_SNAPSHOT_FORMAT_VERSION = "paper-reader-migration-snapshot-v1"
DATABASE_ARCHIVE_PATH = "database/paper-reader.sqlite3"
MANIFEST_ARCHIVE_PATH = "manifest.json"


def utc_text(value: datetime) -> str:
    normalized = value.astimezone(UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class BackupDatabaseMember:
    archive_path: str
    byte_size: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "archive_path": self.archive_path,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class BackupObjectMember:
    content_sha256: str
    archive_path: str
    byte_size: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "archive_path": self.archive_path,
            "byte_size": self.byte_size,
            "content_sha256": self.content_sha256,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class BackupManifest:
    format_version: str
    app_version: str
    schema_revision: str
    created_at: datetime
    database: BackupDatabaseMember
    objects: tuple[BackupObjectMember, ...]
    included: tuple[str, ...]
    excluded: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        objects = sorted(self.objects, key=lambda item: item.content_sha256)
        return {
            "app_version": self.app_version,
            "created_at": utc_text(self.created_at),
            "database": self.database.as_dict(),
            "excluded": sorted(set(self.excluded)),
            "format_version": self.format_version,
            "included": sorted(set(self.included)),
            "objects": [item.as_dict() for item in objects],
            "schema_revision": self.schema_revision,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_bytes()).hexdigest()
