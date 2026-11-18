"""Rights-gated CSV/XLSX import for separately sourced venue rating lists."""

from __future__ import annotations

import csv
import hashlib
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

from openpyxl import load_workbook  # type: ignore[import-untyped]
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import (
    RatingCatalogRecord,
    RatingCatalogSelectionRecord,
    VenueRatingRecord,
)
from paper_reader.jobs.models import utc_now
from paper_reader.recommendation.ccf import normalize_venue_key

MAX_CATALOG_BYTES = 10 * 1024 * 1024
MAX_CATALOG_ROWS = 20_000


class RatingCatalogKind(StrEnum):
    JCR = "jcr"
    CAS = "cas"
    EMERGING = "emerging"


@dataclass(frozen=True, slots=True)
class VenueRating:
    id: str
    catalog_id: str
    venue_name: str
    aliases: tuple[str, ...]
    rating_label: str
    score: float
    identifiers: dict[str, str]
    row_number: int


@dataclass(frozen=True, slots=True)
class RatingCatalog:
    id: str
    kind: RatingCatalogKind
    catalog_name: str
    catalog_version: str
    source_note: str
    filename: str
    file_sha256: str
    row_count: int
    imported_at: datetime
    active: bool


@dataclass(frozen=True, slots=True)
class _ParsedRating:
    venue_name: str
    aliases: tuple[str, ...]
    rating_label: str
    score: float
    identifiers: dict[str, str]
    row_number: int


_HEADER_ALIASES: dict[str, set[str]] = {
    "venue": {"venue", "venue_name", "journal", "期刊名称", "刊物名称", "场所名称"},
    "rating": {"rating", "rank", "quartile", "分区", "等级"},
    "score": {"score", "分数"},
    "aliases": {"aliases", "alias", "别名"},
    "issn": {"issn"},
    "eissn": {"eissn", "e-issn"},
}

_LABEL_SCORES: dict[RatingCatalogKind, dict[str, float]] = {
    RatingCatalogKind.JCR: {"Q1": 100, "Q2": 75, "Q3": 50, "Q4": 25},
    RatingCatalogKind.CAS: {
        "1": 100,
        "1区": 100,
        "一区": 100,
        "2": 75,
        "2区": 75,
        "二区": 75,
        "3": 50,
        "3区": 50,
        "三区": 50,
        "4": 25,
        "4区": 25,
        "四区": 25,
    },
    RatingCatalogKind.EMERGING: {"A": 100, "B": 75, "C": 50, "D": 25},
}


class RatingCatalogRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def import_bytes(
        self,
        *,
        kind: RatingCatalogKind,
        catalog_name: str,
        catalog_version: str,
        source_note: str,
        filename: str,
        content: bytes,
        rights_confirmed: bool,
    ) -> RatingCatalog:
        if not rights_confirmed:
            raise ValueError("rights confirmation is required before importing a rating catalog")
        name = self._required(catalog_name, field="catalog_name", maximum=160)
        version = self._required(catalog_version, field="catalog_version", maximum=80)
        note = self._required(source_note, field="source_note", maximum=2_000)
        safe_filename = self._required(Path(filename).name, field="filename", maximum=255)
        if not content:
            raise ValueError("rating catalog file is empty")
        if len(content) > MAX_CATALOG_BYTES:
            raise ValueError("rating catalog file exceeds the 10 MiB limit")
        fingerprint = hashlib.sha256(content).hexdigest()
        rows = self._parse(kind, safe_filename, content)

        with self._sessions() as session:
            record = session.scalar(
                select(RatingCatalogRecord).where(
                    RatingCatalogRecord.kind == kind.value,
                    RatingCatalogRecord.file_sha256 == fingerprint,
                )
            )
            if record is None:
                record = RatingCatalogRecord(
                    kind=kind.value,
                    catalog_name=name,
                    catalog_version=version,
                    source_note=note,
                    filename=safe_filename,
                    file_sha256=fingerprint,
                    row_count=len(rows),
                    rights_confirmed=True,
                    imported_at=utc_now(),
                )
                session.add(record)
                session.flush([record])
                session.add_all(
                    VenueRatingRecord(
                        catalog_id=record.id,
                        venue_key=normalize_venue_key(row.venue_name),
                        venue_name=row.venue_name,
                        aliases=list(row.aliases),
                        rating_label=row.rating_label,
                        score=row.score,
                        identifiers=row.identifiers,
                        row_number=row.row_number,
                    )
                    for row in rows
                )
            selection = session.get(RatingCatalogSelectionRecord, kind.value)
            selected_at = utc_now()
            if selection is None:
                session.add(
                    RatingCatalogSelectionRecord(
                        kind=kind.value,
                        catalog_id=record.id,
                        selected_at=selected_at,
                    )
                )
            else:
                selection.catalog_id = record.id
                selection.selected_at = selected_at
            session.commit()
            return self._catalog_domain(record, active=True)

    def list_catalogs(self) -> tuple[RatingCatalog, ...]:
        with self._sessions() as session:
            selected = {
                item.catalog_id
                for item in session.scalars(select(RatingCatalogSelectionRecord))
            }
            records = session.scalars(
                select(RatingCatalogRecord).order_by(
                    RatingCatalogRecord.imported_at.desc(),
                    RatingCatalogRecord.id.desc(),
                )
            )
            return tuple(
                self._catalog_domain(record, active=record.id in selected) for record in records
            )

    def active(self, kind: RatingCatalogKind) -> RatingCatalog | None:
        with self._sessions() as session:
            selection = session.get(RatingCatalogSelectionRecord, kind.value)
            if selection is None:
                return None
            record = session.get(RatingCatalogRecord, selection.catalog_id)
            if record is None:  # pragma: no cover - protected by foreign key
                raise RuntimeError("active rating catalog is missing")
            return self._catalog_domain(record, active=True)

    def deactivate(self, kind: RatingCatalogKind) -> None:
        with self._sessions() as session:
            session.execute(
                delete(RatingCatalogSelectionRecord).where(
                    RatingCatalogSelectionRecord.kind == kind.value
                )
            )
            session.commit()

    def find_active(self, kind: RatingCatalogKind, venue: str) -> VenueRating | None:
        active = self.active(kind)
        return self.find_in_catalog(active.id, venue) if active is not None else None

    def find_in_catalog(self, catalog_id: str, venue: str) -> VenueRating | None:
        key = normalize_venue_key(venue)
        if not key:
            return None
        with self._sessions() as session:
            catalog = session.get(RatingCatalogRecord, catalog_id)
            if catalog is None:
                raise KeyError(catalog_id)
            direct = session.scalar(
                select(VenueRatingRecord).where(
                    VenueRatingRecord.catalog_id == catalog_id,
                    VenueRatingRecord.venue_key == key,
                )
            )
            if direct is not None:
                return self._rating_domain(direct)
            records = session.scalars(
                select(VenueRatingRecord).where(VenueRatingRecord.catalog_id == catalog_id)
            )
            for record in records:
                if key in {normalize_venue_key(alias) for alias in record.aliases}:
                    return self._rating_domain(record)
            return None

    @classmethod
    def _parse(
        cls,
        kind: RatingCatalogKind,
        filename: str,
        content: bytes,
    ) -> tuple[_ParsedRating, ...]:
        extension = Path(filename).suffix.casefold()
        if extension == ".csv":
            header, rows = cls._csv_rows(content)
        elif extension == ".xlsx":
            header, rows = cls._xlsx_rows(content)
        else:
            raise ValueError("rating catalog must be a .csv or .xlsx file")
        return cls._table_rows(kind, header, rows)

    @staticmethod
    def _csv_rows(
        content: bytes,
    ) -> tuple[tuple[Any, ...], tuple[tuple[int, tuple[Any, ...]], ...]]:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = content.decode("gb18030")
            except UnicodeDecodeError as error:
                raise ValueError("CSV must use UTF-8 or GB18030 encoding") from error
        reader = csv.reader(StringIO(text, newline=""))
        try:
            header = tuple(next(reader))
        except StopIteration as error:
            raise ValueError("rating catalog has no header row") from error
        rows = tuple((number, tuple(row)) for number, row in enumerate(reader, start=2))
        return header, rows

    @staticmethod
    def _xlsx_rows(
        content: bytes,
    ) -> tuple[tuple[Any, ...], tuple[tuple[int, tuple[Any, ...]], ...]]:
        try:
            workbook = load_workbook(BytesIO(content), read_only=True, data_only=False)
        except Exception as error:
            raise ValueError("invalid XLSX rating catalog") from error
        try:
            values = workbook.active.iter_rows(values_only=True)
            try:
                header = tuple(next(values))
            except StopIteration as error:
                raise ValueError("rating catalog has no header row") from error
            rows = tuple((number, tuple(row)) for number, row in enumerate(values, start=2))
            return header, rows
        finally:
            workbook.close()

    @classmethod
    def _table_rows(
        cls,
        kind: RatingCatalogKind,
        header: tuple[Any, ...],
        rows: tuple[tuple[int, tuple[Any, ...]], ...],
    ) -> tuple[_ParsedRating, ...]:
        columns: dict[str, int] = {}
        for index, raw_header in enumerate(header):
            normalized = str(raw_header or "").strip().casefold()
            for canonical, header_aliases in _HEADER_ALIASES.items():
                if normalized in {alias.casefold() for alias in header_aliases}:
                    columns[canonical] = index
                    break
        if "venue" not in columns:
            raise ValueError("a venue column is required")
        if "rating" not in columns and "score" not in columns:
            raise ValueError("a rating or score column is required")
        if len(rows) > MAX_CATALOG_ROWS:
            raise ValueError("rating catalog exceeds the 20,000 row limit")

        parsed: list[_ParsedRating] = []
        occupied_keys: set[str] = set()
        for row_number, values in rows:
            if not any(value is not None and str(value).strip() for value in values):
                continue
            if any(isinstance(value, str) and value.lstrip().startswith("=") for value in values):
                raise ValueError(f"XLSX/CSV formula is not allowed at row {row_number}")
            venue = cls._cell(values, columns.get("venue"))
            if not venue:
                raise ValueError(f"venue is required at row {row_number}")
            label = cls._cell(values, columns.get("rating"))
            raw_score = cls._cell(values, columns.get("score"))
            score = cls._score(kind, label, raw_score, row_number=row_number)
            row_aliases = tuple(
                item.strip()
                for item in re.split(r"[|;；]", cls._cell(values, columns.get("aliases")))
                if item.strip()
            )
            keys = {
                normalize_venue_key(venue),
                *(normalize_venue_key(alias) for alias in row_aliases),
            }
            keys.discard("")
            if keys & occupied_keys:
                raise ValueError(f"duplicate venue or alias at row {row_number}")
            occupied_keys.update(keys)
            identifiers = {
                field: value
                for field in ("issn", "eissn")
                if (value := cls._cell(values, columns.get(field)))
            }
            parsed.append(
                _ParsedRating(
                    venue_name=venue,
                    aliases=row_aliases,
                    rating_label=label or str(score),
                    score=score,
                    identifiers=identifiers,
                    row_number=row_number,
                )
            )
        if not parsed:
            raise ValueError("rating catalog contains no data rows")
        return tuple(parsed)

    @staticmethod
    def _cell(values: tuple[Any, ...], index: int | None) -> str:
        if index is None or index >= len(values) or values[index] is None:
            return ""
        return " ".join(str(values[index]).split())

    @staticmethod
    def _score(
        kind: RatingCatalogKind,
        label: str,
        raw_score: str,
        *,
        row_number: int,
    ) -> float:
        if raw_score:
            try:
                score = float(raw_score)
            except ValueError as error:
                raise ValueError(f"score must be numeric at row {row_number}") from error
            if not math.isfinite(score) or not 0 <= score <= 100:
                raise ValueError(f"score must be between 0 and 100 at row {row_number}")
            return score
        normalized_label = label.strip().upper().replace(" ", "")
        mapped_score = _LABEL_SCORES[kind].get(normalized_label)
        if mapped_score is None:
            raise ValueError(
                f"rating label has no default score at row {row_number}; provide score"
            )
        return float(mapped_score)

    @staticmethod
    def _required(value: str, *, field: str, maximum: int) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError(f"{field} must not be blank")
        if len(cleaned) > maximum:
            raise ValueError(f"{field} exceeds {maximum} characters")
        return cleaned

    @classmethod
    def _catalog_domain(
        cls,
        record: RatingCatalogRecord,
        *,
        active: bool,
    ) -> RatingCatalog:
        return RatingCatalog(
            id=record.id,
            kind=RatingCatalogKind(record.kind),
            catalog_name=record.catalog_name,
            catalog_version=record.catalog_version,
            source_note=record.source_note,
            filename=record.filename,
            file_sha256=record.file_sha256,
            row_count=record.row_count,
            imported_at=cls._utc(record.imported_at),
            active=active,
        )

    @staticmethod
    def _rating_domain(record: VenueRatingRecord) -> VenueRating:
        return VenueRating(
            id=record.id,
            catalog_id=record.catalog_id,
            venue_name=record.venue_name,
            aliases=tuple(record.aliases),
            rating_label=record.rating_label,
            score=record.score,
            identifiers=dict(record.identifiers),
            row_number=record.row_number,
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
