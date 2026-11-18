"""Preview-first retention policy for unprotected disliked PDF cache."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import (
    DocumentVersionRecord,
    LibraryStateRecord,
)
from paper_reader.reader.storage import ContentStore, UnsafeStoredPathError

DEFAULT_RETENTION_DAYS = 90
MAX_RETENTION_DAYS = 3650
_REASON_ORDER = (
    "feedback_not_disliked",
    "favorite",
    "pinned",
    "analysis_available",
    "annotation",
    "within_retention",
    "path_conflict",
    "unsafe_path",
)
_ANNOTATION_HASH_QUERIES = (
    (
        "document_content_sha256",
        text('SELECT DISTINCT "document_content_sha256" FROM annotations'),
    ),
    ("content_sha256", text('SELECT DISTINCT "content_sha256" FROM annotations')),
    ("document_sha256", text('SELECT DISTINCT "document_sha256" FROM annotations')),
)


class CleanupPreviewError(ValueError):
    """A cleanup preview cannot be safely applied."""


class UnknownCleanupPreviewError(CleanupPreviewError):
    """The token was not issued by this running local application."""


class StaleCleanupPreviewError(CleanupPreviewError):
    """Protection or document state changed after the preview."""


@dataclass(frozen=True, slots=True)
class CleanupCandidate:
    content_sha256: str
    byte_size: int
    work_ids: tuple[str, ...]
    age_days: int
    protected_reasons: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return not self.protected_reasons


@dataclass(frozen=True, slots=True)
class CleanupPreview:
    preview_token: str
    generated_at: datetime
    retention_days: int
    candidates: tuple[CleanupCandidate, ...]
    eligible_count: int
    protected_count: int
    reclaimable_bytes: int


@dataclass(frozen=True, slots=True)
class CleanupResult:
    preview_token: str
    deleted_objects: int
    missing_objects: int
    cleared_document_versions: int
    reclaimed_bytes: int
    cancelled: bool


@dataclass(frozen=True, slots=True)
class _RetentionSnapshot:
    preview: CleanupPreview
    relative_paths: dict[str, tuple[str, ...]]


class RetentionService:
    """Delete only content proven eligible by a current explicit preview."""

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        store: ContentStore,
    ) -> None:
        self._sessions = sessions
        self._store = store
        self._previews: dict[str, _RetentionSnapshot] = {}
        self._results: dict[str, CleanupResult] = {}
        self._lock = RLock()

    def preview(
        self,
        *,
        now: datetime,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> CleanupPreview:
        with self._lock:
            return self._preview(now=now, retention_days=retention_days)

    def _preview(
        self,
        *,
        now: datetime,
        retention_days: int,
    ) -> CleanupPreview:
        normalized_now = _aware_utc(now)
        _validate_retention_days(retention_days)
        snapshot = self._snapshot(now=normalized_now, retention_days=retention_days)
        self._previews[snapshot.preview.preview_token] = snapshot
        if len(self._previews) > 32:
            self._previews.pop(next(iter(self._previews)))
        return snapshot.preview

    def apply(
        self,
        preview_token: str,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> CleanupResult:
        with self._lock:
            return self._apply(preview_token, cancelled=cancelled)

    def _apply(
        self,
        preview_token: str,
        *,
        cancelled: Callable[[], bool],
    ) -> CleanupResult:
        previous_result = self._results.get(preview_token)
        if previous_result is not None:
            return previous_result
        expected = self._previews.get(preview_token)
        if expected is None:
            raise UnknownCleanupPreviewError(
                "Cleanup preview is unknown or expired; create a new preview"
            )
        # Re-resolve the exact paths that the user approved before rebuilding the
        # database snapshot.  A link or junction introduced after preview is a
        # concrete filesystem safety violation, not merely a generic stale token.
        # Protected candidates are never deletion targets and need no path preflight.
        for candidate in expected.preview.candidates:
            if candidate.eligible:
                self._resolve_candidate_path(
                    candidate.content_sha256,
                    expected.relative_paths[candidate.content_sha256],
                )
        current = self._snapshot(
            now=expected.preview.generated_at,
            retention_days=expected.preview.retention_days,
        )
        if current.preview.preview_token != preview_token:
            raise StaleCleanupPreviewError(
                "Cleanup preview is stale because protection state changed"
            )

        eligible = tuple(
            candidate for candidate in current.preview.candidates if candidate.eligible
        )
        resolved_paths = {
            candidate.content_sha256: self._resolve_candidate_path(
                candidate.content_sha256,
                current.relative_paths[candidate.content_sha256],
            )
            for candidate in eligible
        }

        deleted_objects = 0
        missing_objects = 0
        cleared_versions = 0
        reclaimed_bytes = 0
        was_cancelled = False
        for candidate in eligible:
            if cancelled():
                was_cancelled = True
                break
            path = resolved_paths[candidate.content_sha256]
            existed = path.exists()
            if existed:
                try:
                    path.unlink()
                except FileNotFoundError:
                    missing_objects += 1
                else:
                    deleted_objects += 1
                    reclaimed_bytes += candidate.byte_size
            else:
                missing_objects += 1
            with self._sessions() as session:
                documents = list(
                    session.scalars(
                        select(DocumentVersionRecord).where(
                            DocumentVersionRecord.content_sha256
                            == candidate.content_sha256
                        )
                    )
                )
                for document in documents:
                    if document.relative_path is not None:
                        document.relative_path = None
                        cleared_versions += 1
                session.commit()

        result = CleanupResult(
            preview_token=preview_token,
            deleted_objects=deleted_objects,
            missing_objects=missing_objects,
            cleared_document_versions=cleared_versions,
            reclaimed_bytes=reclaimed_bytes,
            cancelled=was_cancelled,
        )
        if not was_cancelled:
            self._results[preview_token] = result
        return result

    def _snapshot(self, *, now: datetime, retention_days: int) -> _RetentionSnapshot:
        cutoff = now - timedelta(days=retention_days)
        with self._sessions() as session:
            documents = list(
                session.scalars(
                    select(DocumentVersionRecord).order_by(
                        DocumentVersionRecord.content_sha256,
                        DocumentVersionRecord.work_id,
                        DocumentVersionRecord.id,
                    )
                )
            )
            annotation_hashes = _annotation_hashes(session)
            grouped: dict[str, list[DocumentVersionRecord]] = {}
            for document in documents:
                grouped.setdefault(document.content_sha256, []).append(document)

            candidates: list[CleanupCandidate] = []
            relative_paths: dict[str, tuple[str, ...]] = {}
            token_rows: list[dict[str, Any]] = []
            for content_sha256, versions in grouped.items():
                paths = tuple(
                    sorted(
                        {
                            version.relative_path
                            for version in versions
                            if version.relative_path is not None
                        }
                    )
                )
                if not paths:
                    continue
                work_ids = tuple(sorted({version.work_id for version in versions}))
                latest_access = max(
                    _database_utc(version.last_accessed_at or version.created_at)
                    for version in versions
                )
                reasons: set[str] = set()
                if latest_access >= cutoff:
                    reasons.add("within_retention")
                for work_id in work_ids:
                    state = session.get(LibraryStateRecord, work_id)
                    if state is None or state.feedback != "dislike":
                        reasons.add("feedback_not_disliked")
                    if state is not None and state.favorite:
                        reasons.add("favorite")
                    if state is not None and state.pinned:
                        reasons.add("pinned")
                    if state is not None and state.analysis_state == "available":
                        reasons.add("analysis_available")
                if content_sha256 in annotation_hashes:
                    reasons.add("annotation")
                if len(paths) != 1:
                    reasons.add("path_conflict")
                for relative_path in paths:
                    try:
                        self._store.resolve_object(content_sha256, relative_path)
                    except UnsafeStoredPathError:
                        reasons.add("unsafe_path")

                protected_reasons = tuple(
                    reason for reason in _REASON_ORDER if reason in reasons
                )
                candidate = CleanupCandidate(
                    content_sha256=content_sha256,
                    byte_size=max(version.byte_size for version in versions),
                    work_ids=work_ids,
                    age_days=max(0, int((now - latest_access).total_seconds() // 86400)),
                    protected_reasons=protected_reasons,
                )
                candidates.append(candidate)
                relative_paths[content_sha256] = paths
                token_rows.append(
                    {
                        "content_sha256": content_sha256,
                        "byte_size": candidate.byte_size,
                        "work_ids": work_ids,
                        "latest_access": latest_access.isoformat(),
                        "paths": paths,
                        "protected_reasons": protected_reasons,
                    }
                )

        token_payload = {
            "retention_days": retention_days,
            "candidates": token_rows,
        }
        preview_token = sha256(
            json.dumps(
                token_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        candidate_tuple = tuple(candidates)
        eligible = tuple(candidate for candidate in candidate_tuple if candidate.eligible)
        preview = CleanupPreview(
            preview_token=preview_token,
            generated_at=now,
            retention_days=retention_days,
            candidates=candidate_tuple,
            eligible_count=len(eligible),
            protected_count=len(candidate_tuple) - len(eligible),
            reclaimable_bytes=sum(candidate.byte_size for candidate in eligible),
        )
        return _RetentionSnapshot(preview=preview, relative_paths=relative_paths)

    def _resolve_candidate_path(
        self,
        content_sha256: str,
        relative_paths: tuple[str, ...],
    ) -> Path:
        if len(relative_paths) != 1:
            raise UnsafeStoredPathError("Cleanup candidate has conflicting paths")
        return self._store.resolve_object(content_sha256, relative_paths[0])


def _annotation_hashes(session: Session) -> set[str]:
    inspector = inspect(session.get_bind())
    if not inspector.has_table("annotations"):
        return set()
    columns = {column["name"] for column in inspector.get_columns("annotations")}
    statement = next(
        (query for name, query in _ANNOTATION_HASH_QUERIES if name in columns),
        None,
    )
    if statement is None:
        return set()
    return {
        value
        for value in session.scalars(statement)
        if isinstance(value, str)
    }


def _validate_retention_days(value: int) -> None:
    if not 1 <= value <= MAX_RETENTION_DAYS:
        raise ValueError("retention_days must be between 1 and 3650")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)


def _database_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
