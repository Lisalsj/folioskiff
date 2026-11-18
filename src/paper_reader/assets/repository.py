"""Transactional model asset lifecycle and reference guards."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.assets.manifest import (
    ModelManifest,
    model_manifest_fingerprint,
    model_manifest_payload,
)
from paper_reader.assets.models import (
    ActiveModelAssetRecord,
    ModelAssetRecord,
    ModelAssetReferenceRecord,
)
from paper_reader.jobs.models import utc_now


class AssetInUseError(ValueError):
    """A model revision is protected by an active or durable reference."""


@dataclass(frozen=True, slots=True)
class DeletionPreview:
    asset_id: str
    bundle_id: str
    revision: str
    reclaimable_bytes: int
    blocking_references: tuple[str, ...]

    @property
    def can_delete(self) -> bool:
        return not self.blocking_references


class ModelAssetRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def register(self, manifest: ModelManifest) -> ModelAssetRecord:
        payload = model_manifest_payload(manifest)
        fingerprint = model_manifest_fingerprint(manifest)
        with self._sessions() as session:
            existing = session.scalar(
                select(ModelAssetRecord).where(
                    ModelAssetRecord.bundle_id == manifest.bundle_id,
                    ModelAssetRecord.revision == manifest.revision,
                )
            )
            if existing is not None:
                if existing.manifest_sha256 != fingerprint:
                    raise ValueError("registered model revision has a different manifest")
                session.expunge(existing)
                return existing
            record = ModelAssetRecord(
                capability=manifest.capability,
                bundle_id=manifest.bundle_id,
                display_name=manifest.display_name,
                purpose=manifest.purpose,
                version=manifest.version,
                revision=manifest.revision,
                manifest_sha256=fingerprint,
                manifest_json=payload,
                total_bytes=manifest.total_size_bytes,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record

    def get(self, asset_id: str) -> ModelAssetRecord | None:
        with self._sessions() as session:
            record = session.get(ModelAssetRecord, asset_id)
            if record is not None:
                session.expunge(record)
            return record

    def find(self, bundle_id: str, revision: str) -> ModelAssetRecord | None:
        with self._sessions() as session:
            record = session.scalar(
                select(ModelAssetRecord).where(
                    ModelAssetRecord.bundle_id == bundle_id,
                    ModelAssetRecord.revision == revision,
                )
            )
            if record is not None:
                session.expunge(record)
            return record

    def list_bundle(self, bundle_id: str) -> list[ModelAssetRecord]:
        with self._sessions() as session:
            records = list(
                session.scalars(
                    select(ModelAssetRecord)
                    .where(ModelAssetRecord.bundle_id == bundle_id)
                    .order_by(ModelAssetRecord.revision)
                )
            )
            for record in records:
                session.expunge(record)
            return records

    def list_all(self) -> list[ModelAssetRecord]:
        with self._sessions() as session:
            records = list(
                session.scalars(
                    select(ModelAssetRecord).order_by(
                        ModelAssetRecord.capability,
                        ModelAssetRecord.bundle_id,
                        ModelAssetRecord.revision,
                    )
                )
            )
            for record in records:
                session.expunge(record)
            return records

    def update_download(
        self,
        asset_id: str,
        *,
        status: str,
        integrity: str,
        received_bytes: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ModelAssetRecord:
        with self._sessions() as session:
            record = _required(session, asset_id)
            if not 0 <= received_bytes <= record.total_bytes:
                raise ValueError("model asset received bytes are invalid")
            record.status = status
            record.integrity = integrity
            record.received_bytes = received_bytes
            record.error_code = error_code
            record.error_message = error_message
            if status != "ready":
                record.verified_at = None
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record

    def mark_ready(self, asset_id: str) -> ModelAssetRecord:
        with self._sessions() as session:
            record = _required(session, asset_id)
            record.status = "ready"
            record.integrity = "verified"
            record.received_bytes = record.total_bytes
            record.error_code = None
            record.error_message = None
            record.verified_at = utc_now()
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record

    def activate(self, asset_id: str) -> ModelAssetRecord:
        with self._sessions() as session:
            record = _required(session, asset_id)
            if record.status != "ready" or record.integrity != "verified":
                raise ValueError("only a verified model revision can be activated")
            pointer = session.get(ActiveModelAssetRecord, record.bundle_id)
            if pointer is None:
                pointer = ActiveModelAssetRecord(bundle_id=record.bundle_id, asset_id=record.id)
                session.add(pointer)
            else:
                pointer.asset_id = record.id
                pointer.activated_at = utc_now()
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record

    def active_id(self, bundle_id: str) -> str | None:
        with self._sessions() as session:
            pointer = session.get(ActiveModelAssetRecord, bundle_id)
            return pointer.asset_id if pointer is not None else None

    def add_reference(self, asset_id: str, *, kind: str, reference_id: str) -> None:
        if kind not in {"job", "runtime", "index"}:
            raise ValueError("model asset reference kind is invalid")
        with self._sessions() as session:
            _required(session, asset_id)
            existing = session.scalar(
                select(ModelAssetReferenceRecord).where(
                    ModelAssetReferenceRecord.asset_id == asset_id,
                    ModelAssetReferenceRecord.kind == kind,
                    ModelAssetReferenceRecord.reference_id == reference_id,
                )
            )
            if existing is None:
                session.add(
                    ModelAssetReferenceRecord(
                        asset_id=asset_id,
                        kind=kind,
                        reference_id=reference_id,
                    )
                )
                session.commit()

    def remove_reference(self, asset_id: str, *, kind: str, reference_id: str) -> None:
        with self._sessions() as session:
            record = session.scalar(
                select(ModelAssetReferenceRecord).where(
                    ModelAssetReferenceRecord.asset_id == asset_id,
                    ModelAssetReferenceRecord.kind == kind,
                    ModelAssetReferenceRecord.reference_id == reference_id,
                )
            )
            if record is not None:
                session.delete(record)
                session.commit()

    def deletion_preview(self, asset_id: str) -> DeletionPreview:
        with self._sessions() as session:
            record = _required(session, asset_id)
            blocking: list[str] = []
            if (
                session.scalar(
                    select(ActiveModelAssetRecord.bundle_id).where(
                        ActiveModelAssetRecord.asset_id == asset_id
                    )
                )
                is not None
            ):
                blocking.append("active")
            references = list(
                session.scalars(
                    select(ModelAssetReferenceRecord)
                    .where(ModelAssetReferenceRecord.asset_id == asset_id)
                    .order_by(
                        ModelAssetReferenceRecord.kind,
                        ModelAssetReferenceRecord.reference_id,
                    )
                )
            )
            blocking.extend(f"{item.kind}:{item.reference_id}" for item in references)
            return DeletionPreview(
                asset_id=record.id,
                bundle_id=record.bundle_id,
                revision=record.revision,
                reclaimable_bytes=record.received_bytes,
                blocking_references=tuple(blocking),
            )

    def delete(self, asset_id: str) -> None:
        preview = self.deletion_preview(asset_id)
        if not preview.can_delete:
            raise AssetInUseError("model revision is active or referenced")
        with self._sessions() as session:
            record = _required(session, asset_id)
            session.delete(record)
            session.commit()


def _required(session: Session, asset_id: str) -> ModelAssetRecord:
    record = session.get(ModelAssetRecord, asset_id)
    if record is None:
        raise KeyError(asset_id)
    return record
