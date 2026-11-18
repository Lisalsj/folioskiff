"""Transactional persistence for immutable, privacy-safe publication snapshots."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.jobs.models import utc_now
from paper_reader.publishing.models import (
    PublicationPayload,
    PublicationPreview,
    PublicationSelection,
    PublishedFile,
    PublishedFileManifestEntry,
    PublishedSnapshot,
    PublishedSnapshotFileRecord,
    PublishedSnapshotRecord,
    PublishedSnapshotWork,
    PublishedSnapshotWorkRecord,
)
from paper_reader.publishing.privacy import PublishingPrivacyBoundary, canonical_json_bytes
from paper_reader.publishing.render import render_publication


class StalePublicationPreview(RuntimeError):
    """The user-confirmed preview no longer matches current server facts."""


class PublishedSnapshotIntegrityError(RuntimeError):
    """Stored immutable bytes no longer match their recorded hashes."""


class PublishedSnapshotRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    def freeze(self, preview: PublicationPreview) -> PublishedSnapshot:
        if not isinstance(preview, PublicationPreview):
            raise TypeError("preview must be PublicationPreview")
        with self._sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            return self._freeze_locked(session, preview)

    def _freeze_locked(
        self,
        session: Session,
        preview: PublicationPreview,
    ) -> PublishedSnapshot:
        current = PublishingPrivacyBoundary(
            self._sessions,
            transaction_session=session,
        ).preview(preview.selection)
        if current.fingerprint != preview.fingerprint or current.payload != preview.payload:
            raise StalePublicationPreview("publication preview is stale; review it again")

        rendered = render_publication(current.payload, language=current.selection.display_language)
        files = rendered.files
        data_file = _require_file(files, "data.json")
        manifest_file = _require_file(files, "manifest.json")
        file_manifest = tuple(
            PublishedFileManifestEntry(
                position=item.position,
                relative_path=item.relative_path,
                media_type=item.media_type,
                byte_size=item.byte_size,
                sha256=item.sha256,
            )
            for item in files
        )
        snapshot_hash = _snapshot_sha256(
            preview_fingerprint=current.fingerprint,
            selection=current.selection,
            payload_sha256=data_file.sha256,
            manifest_sha256=manifest_file.sha256,
            file_manifest=file_manifest,
        )
        existing = session.scalar(
            select(PublishedSnapshotRecord).where(
                PublishedSnapshotRecord.snapshot_sha256 == snapshot_hash
            )
        )
        if existing is not None:
            return self._domain(session, existing)

        record = PublishedSnapshotRecord(
            slug=current.payload.slug,
            schema_version=current.payload.schema_version,
            preview_fingerprint=current.fingerprint,
            selection_json=current.selection.model_dump(mode="json"),
            payload_json=current.payload.model_dump(mode="json"),
            payload_sha256=data_file.sha256,
            file_manifest=[item.model_dump(mode="json") for item in file_manifest],
            manifest_sha256=manifest_file.sha256,
            snapshot_sha256=snapshot_hash,
            created_at=_aware_utc(self._clock()),
        )
        session.add(record)
        session.flush()
        for position, work in enumerate(current.payload.works):
            session.add(
                PublishedSnapshotWorkRecord(
                    snapshot_id=record.id,
                    position=position,
                    canonical_work_id=work.canonical_work_id,
                    report_id=None if work.report is None else work.report.id,
                    score_snapshot_id=(
                        None if work.quality_score is None else work.quality_score.snapshot_id
                    ),
                )
            )
        for item in files:
            session.add(
                PublishedSnapshotFileRecord(
                    snapshot_id=record.id,
                    position=item.position,
                    relative_path=item.relative_path,
                    media_type=item.media_type,
                    byte_size=item.byte_size,
                    sha256=item.sha256,
                    content=item.content,
                )
            )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            winner = session.scalar(
                select(PublishedSnapshotRecord).where(
                    PublishedSnapshotRecord.snapshot_sha256 == snapshot_hash
                )
            )
            if winner is None:
                raise
            return self._domain(session, winner)
        return self._domain(session, record)

    def get(self, snapshot_id: str) -> PublishedSnapshot | None:
        with self._sessions() as session:
            record = session.get(PublishedSnapshotRecord, snapshot_id)
            return None if record is None else self._domain(session, record)

    def require(self, snapshot_id: str) -> PublishedSnapshot:
        snapshot = self.get(snapshot_id)
        if snapshot is None:
            raise KeyError(snapshot_id)
        return snapshot

    @staticmethod
    def _domain(session: Session, record: PublishedSnapshotRecord) -> PublishedSnapshot:
        works = tuple(
            PublishedSnapshotWork(
                position=item.position,
                canonical_work_id=item.canonical_work_id,
                report_id=item.report_id,
                score_snapshot_id=item.score_snapshot_id,
            )
            for item in session.scalars(
                select(PublishedSnapshotWorkRecord)
                .where(PublishedSnapshotWorkRecord.snapshot_id == record.id)
                .order_by(PublishedSnapshotWorkRecord.position)
            )
        )
        files = tuple(
            PublishedFile(
                position=item.position,
                relative_path=item.relative_path,
                media_type=item.media_type,
                byte_size=item.byte_size,
                sha256=item.sha256,
                content=item.content,
            )
            for item in session.scalars(
                select(PublishedSnapshotFileRecord)
                .where(PublishedSnapshotFileRecord.snapshot_id == record.id)
                .order_by(PublishedSnapshotFileRecord.position)
            )
        )
        manifest = tuple(
            PublishedFileManifestEntry.model_validate(item) for item in record.file_manifest
        )
        payload = PublicationPayload.model_validate(record.payload_json)
        selection = PublicationSelection.model_validate(record.selection_json)
        _verify_record(
            record,
            selection=selection,
            payload=payload,
            files=files,
            manifest=manifest,
        )
        return PublishedSnapshot(
            id=record.id,
            slug=record.slug,
            schema_version=record.schema_version,
            preview_fingerprint=record.preview_fingerprint,
            selection=selection,
            payload=payload,
            payload_sha256=record.payload_sha256,
            file_manifest=manifest,
            manifest_sha256=record.manifest_sha256,
            snapshot_sha256=record.snapshot_sha256,
            works=works,
            files=files,
            created_at=_utc_text(record.created_at),
        )


def _verify_record(
    record: PublishedSnapshotRecord,
    *,
    selection: PublicationSelection,
    payload: PublicationPayload,
    files: tuple[PublishedFile, ...],
    manifest: tuple[PublishedFileManifestEntry, ...],
) -> None:
    if len(files) != 3 or len(manifest) != 3:
        raise PublishedSnapshotIntegrityError("published snapshot file set is incomplete")
    for item, expected in zip(files, manifest, strict=True):
        digest = hashlib.sha256(item.content).hexdigest()
        if (
            item.position != expected.position
            or item.relative_path != expected.relative_path
            or item.media_type != expected.media_type
            or item.byte_size != len(item.content)
            or item.byte_size != expected.byte_size
            or item.sha256 != digest
            or item.sha256 != expected.sha256
        ):
            raise PublishedSnapshotIntegrityError("published snapshot file hash mismatch")
    data_file = _require_file(files, "data.json")
    manifest_file = _require_file(files, "manifest.json")
    expected_payload_bytes = canonical_json_bytes(payload.model_dump(mode="json")) + b"\n"
    if data_file.content != expected_payload_bytes:
        raise PublishedSnapshotIntegrityError("published payload bytes do not match payload JSON")
    expected_snapshot = _snapshot_sha256(
        preview_fingerprint=record.preview_fingerprint,
        selection=selection,
        payload_sha256=data_file.sha256,
        manifest_sha256=manifest_file.sha256,
        file_manifest=manifest,
    )
    if (
        record.payload_sha256 != data_file.sha256
        or record.manifest_sha256 != manifest_file.sha256
        or record.snapshot_sha256 != expected_snapshot
    ):
        raise PublishedSnapshotIntegrityError("published snapshot envelope hash mismatch")


def _snapshot_sha256(
    *,
    preview_fingerprint: str,
    selection: PublicationSelection,
    payload_sha256: str,
    manifest_sha256: str,
    file_manifest: tuple[PublishedFileManifestEntry, ...],
) -> str:
    value = {
        "format_version": "paper-reader-published-snapshot-v1",
        "preview_fingerprint": preview_fingerprint,
        "selection": selection.model_dump(mode="json"),
        "payload_sha256": payload_sha256,
        "manifest_sha256": manifest_sha256,
        "files": [item.model_dump(mode="json") for item in file_manifest],
    }
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _require_file(files: tuple[PublishedFile, ...], path: str) -> PublishedFile:
    try:
        return next(item for item in files if item.relative_path == path)
    except StopIteration as error:
        raise PublishedSnapshotIntegrityError(f"published snapshot is missing {path}") from error


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("publication clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat().replace("+00:00", "Z")
