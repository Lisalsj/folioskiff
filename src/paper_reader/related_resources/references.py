"""Bounded extraction of cached reference identities from source metadata."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import islice

from sqlalchemy import select
from sqlalchemy.orm import Session

from paper_reader.discovery.identity import normalize_doi
from paper_reader.discovery.models import SourceRecord

MAX_REFERENCE_DOIS = 50
MAX_REFERENCE_SOURCE_IDS = 50
MAX_REFERENCE_NODES = 200
MAX_REFERENCE_CHARACTERS = 50_000

_DOI = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_SAFE_CONNECTOR_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_ARXIV_REFERENCE = re.compile(
    r"https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/([^?#\s]+)",
    re.IGNORECASE,
)
_OPENREVIEW_REFERENCE = re.compile(
    r"https?://openreview\.net/(?:forum|pdf)\?[^\s#]*\bid=([^&#\s]+)",
    re.IGNORECASE,
)
_EUROPE_PMC_REFERENCE = re.compile(
    r"https?://(?:www\.)?europepmc\.org/article/([^/\s?#]+)/([^\s?#]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ReferenceIdentities:
    """DOI and connector identities already present in the current paper metadata."""

    dois: tuple[str, ...]
    source_ids: tuple[tuple[str, str], ...]


def reference_identities(session: Session, work_id: str) -> ReferenceIdentities:
    records = session.scalars(
        select(SourceRecord)
        .where(SourceRecord.work_id == work_id)
        .order_by(SourceRecord.connector_id, SourceRecord.external_id)
        .limit(20)
    )
    dois: list[str] = []
    source_ids: list[tuple[str, str]] = []
    for record in records:
        for connector_id, external_id in _bounded_structured_source_ids(record.raw_payload):
            identity = (connector_id, external_id)
            if identity not in source_ids:
                source_ids.append(identity)
            if len(source_ids) == MAX_REFERENCE_SOURCE_IDS:
                break
        for text_value in _bounded_text_values(record.raw_payload):
            for match in _DOI.finditer(text_value):
                doi = normalize_doi(match.group(0).rstrip(".,;:)]}"))
                if doi is not None and doi not in dois:
                    dois.append(doi)
                if len(dois) == MAX_REFERENCE_DOIS:
                    break
            for identity in _source_ids_from_urls(text_value):
                if identity not in source_ids:
                    source_ids.append(identity)
                if len(source_ids) == MAX_REFERENCE_SOURCE_IDS:
                    break
            if len(dois) == MAX_REFERENCE_DOIS and len(source_ids) == MAX_REFERENCE_SOURCE_IDS:
                break
        if len(dois) == MAX_REFERENCE_DOIS and len(source_ids) == MAX_REFERENCE_SOURCE_IDS:
            break
    return ReferenceIdentities(dois=tuple(dois), source_ids=tuple(source_ids))


def _source_ids_from_urls(value: str) -> tuple[tuple[str, str], ...]:
    identities: list[tuple[str, str]] = []
    for match in _ARXIV_REFERENCE.finditer(value):
        external_id = match.group(1).removesuffix(".pdf").rstrip(".,;:)]}")
        if _safe_external_id(external_id):
            identities.append(("arxiv", external_id))
    for match in _OPENREVIEW_REFERENCE.finditer(value):
        external_id = match.group(1).rstrip(".,;:)]}")
        if _safe_external_id(external_id):
            identities.append(("openreview", external_id))
    for match in _EUROPE_PMC_REFERENCE.finditer(value):
        source = match.group(1).rstrip(".,;:)]}")
        identifier = match.group(2).rstrip(".,;:)]}")
        external_id = f"{source}/{identifier}"
        if _safe_external_id(external_id):
            identities.append(("europe_pmc", external_id))
    return tuple(identities)


def _bounded_structured_source_ids(payload: object) -> Iterable[tuple[str, str]]:
    stack = [payload]
    nodes = 0
    while stack and nodes < MAX_REFERENCE_NODES:
        value = stack.pop()
        nodes += 1
        if isinstance(value, Mapping):
            connector_value = value.get("connector_id")
            external_value = value.get("external_id")
            if isinstance(connector_value, str) and isinstance(external_value, str):
                connector_id = connector_value.strip().casefold()
                external_id = external_value.strip()
                if _SAFE_CONNECTOR_ID.fullmatch(connector_id) and _safe_external_id(external_id):
                    yield connector_id, external_id
            stack.extend(reversed(tuple(islice(value.values(), 100))))
        elif isinstance(value, (list, tuple)):
            stack.extend(reversed(tuple(islice(value, 100))))


def _safe_external_id(value: str) -> bool:
    return bool(value) and len(value) <= 500 and not any(ord(char) < 32 for char in value)


def _bounded_text_values(payload: object) -> Iterable[str]:
    stack = [payload]
    nodes = 0
    characters = 0
    while stack and nodes < MAX_REFERENCE_NODES and characters < MAX_REFERENCE_CHARACTERS:
        value = stack.pop()
        nodes += 1
        if isinstance(value, str):
            remaining = MAX_REFERENCE_CHARACTERS - characters
            bounded = value[:remaining]
            characters += len(bounded)
            yield bounded
        elif isinstance(value, Mapping):
            stack.extend(reversed(tuple(islice(value.values(), 100))))
        elif isinstance(value, (list, tuple)):
            stack.extend(reversed(tuple(islice(value, 100))))


__all__ = [
    "MAX_REFERENCE_CHARACTERS",
    "MAX_REFERENCE_DOIS",
    "MAX_REFERENCE_NODES",
    "MAX_REFERENCE_SOURCE_IDS",
    "ReferenceIdentities",
    "reference_identities",
]
