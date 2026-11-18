"""Manual paper and document import contracts."""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import PurePath, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.identity import IdentityRepository, MatchKind, SourcePaper, WorkIndex
from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.jobs.models import utc_now
from paper_reader.reader.models import LibraryEntryRecord
from paper_reader.reader.storage import ContentStore

MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_BIBTEX_CHARACTERS = 100_000
MAX_REFERENCE_URL_CHARACTERS = 2_048
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
BIBTEX_START = re.compile(r"^@\s*([A-Za-z][A-Za-z0-9_-]*)\s*([({])")
BIBTEX_FIELD = re.compile(r"([A-Za-z][A-Za-z0-9_-]*)\s*=")


class PdfImportError(ValueError):
    """Base class for rejected manual document imports."""


class RightsConfirmationRequiredError(PdfImportError):
    """The user has not confirmed their right to use the supplied PDF."""


class WorkNotFoundError(PdfImportError):
    """The requested canonical Work does not exist."""


class ReferenceImportError(ValueError):
    """A DOI, BibTeX entry, or URL cannot be normalized safely."""


class ReferenceKind(StrEnum):
    DOI = "doi"
    BIBTEX = "bibtex"
    URL = "url"


@dataclass(frozen=True, slots=True)
class PdfImportInput:
    work_id: str | None
    title: str | None
    authors: tuple[str, ...]
    doi: str | None
    original_filename: str
    rights_confirmed: bool


@dataclass(frozen=True, slots=True)
class PdfImportResult:
    work_id: str
    document_version_id: str
    content_sha256: str
    created_work: bool
    created_document: bool


@dataclass(frozen=True, slots=True)
class ReferenceImportInput:
    kind: ReferenceKind
    value: str
    title: str | None = None
    authors: tuple[str, ...] = ()
    rights_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class NormalizedReference:
    kind: ReferenceKind
    external_id: str
    title: str
    authors: tuple[str, ...]
    doi: str | None
    landing_url: str | None
    normalized_value: str
    raw_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReferenceImportResult:
    work_id: str
    created_work: bool
    kind: ReferenceKind
    normalized_value: str


def normalize_reference(value: ReferenceImportInput) -> NormalizedReference:
    try:
        kind = ReferenceKind(value.kind)
    except ValueError as error:
        raise ReferenceImportError(f"Unsupported reference kind: {value.kind}") from error
    if kind is ReferenceKind.DOI:
        return _normalize_doi_reference(value)
    if kind is ReferenceKind.BIBTEX:
        return _normalize_bibtex_reference(value)
    return _normalize_url_reference(value)


def _normalize_doi_reference(value: ReferenceImportInput) -> NormalizedReference:
    doi = _strict_doi(value.value)
    title = _normalize_title(value.title) if value.title is not None else f"DOI {doi}"
    authors = _normalize_authors(value.authors)
    landing_url = f"https://doi.org/{doi}"
    return NormalizedReference(
        kind=ReferenceKind.DOI,
        external_id=doi,
        title=title,
        authors=authors,
        doi=doi,
        landing_url=landing_url,
        normalized_value=doi,
        raw_payload={"doi": doi, "landing_url": landing_url},
    )


def _normalize_bibtex_reference(value: ReferenceImportInput) -> NormalizedReference:
    entry_type, citation_key, fields, canonical = _parse_bibtex(value.value)
    field_title = fields.get("title")
    if field_title is None:
        raise ReferenceImportError("BibTeX entry requires a title field")
    title = (
        _normalize_title(value.title)
        if value.title is not None
        else _normalize_title(field_title)
    )
    if value.authors:
        authors = _normalize_authors(value.authors)
    else:
        authors = _normalize_authors(
            tuple(re.split(r"\s+and\s+", fields.get("author", ""), flags=re.IGNORECASE))
        )
    doi = _strict_doi(fields["doi"]) if fields.get("doi") else None
    landing_url = _normalize_public_url(fields["url"])[0] if fields.get("url") else None
    return NormalizedReference(
        kind=ReferenceKind.BIBTEX,
        external_id=sha256(canonical.encode("utf-8")).hexdigest(),
        title=title,
        authors=authors,
        doi=doi,
        landing_url=landing_url,
        normalized_value=citation_key,
        raw_payload={
            "entry_type": entry_type,
            "citation_key": citation_key,
            "fields": fields,
        },
    )


def _normalize_url_reference(value: ReferenceImportInput) -> NormalizedReference:
    normalized_url, path = _normalize_public_url(value.value)
    title = (
        _normalize_title(value.title)
        if value.title is not None
        else _title_from_url(path, normalized_url)
    )
    return NormalizedReference(
        kind=ReferenceKind.URL,
        external_id=sha256(normalized_url.encode("utf-8")).hexdigest(),
        title=title,
        authors=_normalize_authors(value.authors),
        doi=None,
        landing_url=normalized_url,
        normalized_value=normalized_url,
        raw_payload={"url": normalized_url},
    )


def _strict_doi(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = unquote(normalized)
    normalized = re.sub(
        r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
        "",
        normalized,
        flags=re.IGNORECASE,
    ).strip()
    normalized = normalized.rstrip(".").casefold()
    if len(normalized) > 255 or DOI_PATTERN.fullmatch(normalized) is None:
        raise ReferenceImportError("DOI must use the canonical 10.<registrant>/<suffix> form")
    return normalized


def _parse_bibtex(value: str) -> tuple[str, str, dict[str, str], str]:
    if len(value) > MAX_BIBTEX_CHARACTERS:
        raise ReferenceImportError("BibTeX input exceeds 100000 characters")
    canonical = unicodedata.normalize("NFKC", value).strip()
    match = BIBTEX_START.match(canonical)
    if match is None:
        raise ReferenceImportError("BibTeX input must contain exactly one entry")
    entry_type = match.group(1).casefold()
    opener = match.group(2)
    closer = "}" if opener == "{" else ")"
    opening_index = match.end() - 1
    closing_index = _matching_delimiter(canonical, opening_index, opener, closer)
    if canonical[closing_index + 1 :].strip():
        raise ReferenceImportError("BibTeX input must contain exactly one entry")

    body = canonical[opening_index + 1 : closing_index]
    comma = _top_level_comma(body)
    if comma is None:
        raise ReferenceImportError("BibTeX entry requires a citation key and fields")
    citation_key = body[:comma].strip()
    if not citation_key or len(citation_key) > 255:
        raise ReferenceImportError("BibTeX citation key must contain 1-255 characters")
    fields = _parse_bibtex_fields(body[comma + 1 :])
    return entry_type, citation_key, fields, canonical


def _matching_delimiter(text: str, start: int, opener: str, closer: str) -> int:
    depth = 0
    in_quote = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if character == opener:
            depth += 1
        elif character == closer:
            depth -= 1
            if depth == 0:
                return index
    raise ReferenceImportError("BibTeX entry has unbalanced delimiters")


def _top_level_comma(value: str) -> int | None:
    brace_depth = 0
    in_quote = False
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == '"':
            in_quote = not in_quote
        elif not in_quote and character == "{":
            brace_depth += 1
        elif not in_quote and character == "}":
            brace_depth -= 1
        elif not in_quote and brace_depth == 0 and character == ",":
            return index
    return None


def _parse_bibtex_fields(value: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    index = 0
    while index < len(value):
        while index < len(value) and (value[index].isspace() or value[index] == ","):
            index += 1
        if index >= len(value):
            break
        match = BIBTEX_FIELD.match(value, index)
        if match is None:
            raise ReferenceImportError("BibTeX field syntax is invalid")
        field_name = match.group(1).casefold()
        if field_name in fields:
            raise ReferenceImportError(f"BibTeX field is duplicated: {field_name}")
        index = match.end()
        while index < len(value) and value[index].isspace():
            index += 1
        if index >= len(value):
            raise ReferenceImportError(f"BibTeX field has no value: {field_name}")
        field_value, index = _read_bibtex_value(value, index)
        fields[field_name] = _clean_bibtex_text(field_value)
        while index < len(value) and value[index].isspace():
            index += 1
        if index < len(value) and value[index] != ",":
            raise ReferenceImportError("BibTeX fields must be comma-separated")
    return fields


def _read_bibtex_value(value: str, start: int) -> tuple[str, int]:
    if value[start] == "{":
        end = _matching_delimiter(value, start, "{", "}")
        return value[start + 1 : end], end + 1
    if value[start] == '"':
        escaped = False
        for index in range(start + 1, len(value)):
            character = value[index]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                return value[start + 1 : index], index + 1
        raise ReferenceImportError("BibTeX quoted field is unbalanced")
    end = value.find(",", start)
    end = len(value) if end == -1 else end
    return value[start:end], end


def _clean_bibtex_text(value: str) -> str:
    without_grouping = value.replace("{", "").replace("}", "")
    return " ".join(without_grouping.split())


def _normalize_public_url(value: str) -> tuple[str, str]:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized or len(normalized) > MAX_REFERENCE_URL_CHARACTERS:
        raise ReferenceImportError("URL must contain 1-2048 characters")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as error:
        raise ReferenceImportError("URL authority is invalid") from error
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or hostname is None:
        raise ReferenceImportError("URL must use HTTP or HTTPS and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ReferenceImportError("URL must not contain embedded credentials")
    try:
        ascii_hostname = hostname.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as error:
        raise ReferenceImportError("URL host cannot be normalized") from error
    if not ascii_hostname or ascii_hostname == "localhost" or ascii_hostname.endswith(
        (".localhost", ".local")
    ):
        raise ReferenceImportError("URL host is not a public destination")
    try:
        address = ipaddress.ip_address(ascii_hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ReferenceImportError("URL host is not a public destination")

    default_port = (scheme == "http" and port in {None, 80}) or (
        scheme == "https" and port in {None, 443}
    )
    rendered_host = f"[{ascii_hostname}]" if ":" in ascii_hostname else ascii_hostname
    netloc = rendered_host if default_port else f"{rendered_host}:{port}"
    path = quote(unquote(parsed.path or "/"), safe="/!$&'()*+,;=:@-._~")
    return urlunsplit((scheme, netloc, path, parsed.query, "")), path


def _title_from_url(path: str, normalized_url: str) -> str:
    filename = unquote(PurePosixPath(path).name)
    title = " ".join(PurePosixPath(filename).stem.split()) if filename else ""
    if title:
        return _normalize_title(title)
    hostname = urlsplit(normalized_url).hostname
    if hostname is None:  # pragma: no cover - normalized URL always has a host
        raise ReferenceImportError("URL title cannot be derived")
    return _normalize_title(hostname)


class LibraryImportService:
    """Attach user-provided lawful documents to canonical Works."""

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        store: ContentStore,
        work_index: WorkIndex | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._store = store
        self._work_index = work_index
        self._clock = clock

    def import_reference(self, value: ReferenceImportInput) -> ReferenceImportResult:
        normalized = normalize_reference(value)
        observed_at = _aware_utc(self._clock())
        ingested = IdentityRepository(self._sessions, work_index=self._work_index).ingest(
            SourcePaper(
                connector_id=f"manual-{normalized.kind.value}",
                external_id=normalized.external_id,
                title=normalized.title,
                authors=normalized.authors,
                doi=normalized.doi,
                updated_at=observed_at,
                landing_url=normalized.landing_url,
                field_provenance={
                    "title": f"manual:{normalized.kind.value}",
                    "authors": f"manual:{normalized.kind.value}",
                    "doi": f"manual:{normalized.kind.value}",
                    "landing_url": f"manual:{normalized.kind.value}",
                },
                raw_payload=dict(normalized.raw_payload),
            )
        )
        with self._sessions() as session:
            library_entry = session.get(LibraryEntryRecord, ingested.work.id)
            if library_entry is None:
                session.add(
                    LibraryEntryRecord(
                        work_id=ingested.work.id,
                        added_via=normalized.kind.value,
                        created_at=observed_at,
                        updated_at=observed_at,
                    )
                )
            else:
                library_entry.updated_at = observed_at
            session.commit()
        return ReferenceImportResult(
            work_id=ingested.work.id,
            created_work=ingested.match_kind is MatchKind.NEW,
            kind=normalized.kind,
            normalized_value=normalized.normalized_value,
        )

    def import_pdf(
        self,
        value: PdfImportInput,
        chunks: Iterable[bytes],
    ) -> PdfImportResult:
        if not value.rights_confirmed:
            raise RightsConfirmationRequiredError(
                "Confirm that this PDF was obtained lawfully before importing it"
            )
        filename = _normalize_filename(value.original_filename)
        authors = _normalize_authors(value.authors)
        requested_work_id = self._resolve_requested_work(value.work_id)
        observed_at = _aware_utc(self._clock())
        manual_title: str | None = None
        if requested_work_id is None:
            manual_title = (
                _normalize_title(value.title)
                if value.title is not None
                else _title_from_filename(filename)
            )

        stored = self._store.ingest(
            chunks,
            media_type="application/pdf",
            max_bytes=MAX_PDF_BYTES,
        )

        created_work = False
        source_record_id: str | None = None
        if requested_work_id is None:
            assert manual_title is not None
            ingested = IdentityRepository(
                self._sessions,
                work_index=self._work_index,
            ).ingest(
                SourcePaper(
                    connector_id="manual",
                    external_id=stored.content_sha256,
                    title=manual_title,
                    authors=authors,
                    doi=value.doi,
                    updated_at=observed_at,
                    field_provenance={
                        "title": "manual:pdf",
                        "authors": "manual:pdf",
                        "doi": "manual:pdf",
                    },
                    raw_payload={
                        "original_filename": filename,
                        "rights_confirmed": True,
                    },
                )
            )
            work_id = ingested.work.id
            source_record_id = ingested.source_record.id
            created_work = ingested.match_kind is MatchKind.NEW
        else:
            work_id = requested_work_id

        with self._sessions() as session:
            document = session.scalar(
                select(DocumentVersionRecord).where(
                    DocumentVersionRecord.work_id == work_id,
                    DocumentVersionRecord.content_sha256 == stored.content_sha256,
                )
            )
            created_document = document is None
            if document is None:
                document = DocumentVersionRecord(
                    work_id=work_id,
                    source_record_id=source_record_id,
                    content_sha256=stored.content_sha256,
                    access_status="user_provided",
                    media_type=stored.media_type,
                    completeness=100,
                    source_updated_at=observed_at,
                    extraction_status="pending",
                    relative_path=stored.relative_path,
                    byte_size=stored.byte_size,
                    original_filename=filename,
                    acquisition_kind="manual_import",
                    acquisition_url=None,
                    last_accessed_at=observed_at,
                    created_at=observed_at,
                )
                session.add(document)
                session.flush()
            else:
                document.relative_path = stored.relative_path
                document.byte_size = stored.byte_size
                document.original_filename = filename
                document.last_accessed_at = observed_at
                if document.access_status not in {"open", "user_provided"}:
                    document.access_status = "user_provided"
                if document.acquisition_kind == "source":
                    document.acquisition_kind = "manual_import"

            library_entry = session.get(LibraryEntryRecord, work_id)
            if library_entry is None:
                session.add(
                    LibraryEntryRecord(
                        work_id=work_id,
                        added_via="pdf",
                        created_at=observed_at,
                        updated_at=observed_at,
                    )
                )
            else:
                library_entry.updated_at = observed_at
            session.commit()
            document_id = document.id

        return PdfImportResult(
            work_id=work_id,
            document_version_id=document_id,
            content_sha256=stored.content_sha256,
            created_work=created_work,
            created_document=created_document,
        )

    def _resolve_requested_work(self, work_id: str | None) -> str | None:
        if work_id is None:
            return None
        normalized_id = work_id.strip()
        if not normalized_id:
            raise WorkNotFoundError("Work not found")
        with self._sessions() as session:
            work = session.get(WorkRecord, normalized_id)
            if work is None:
                raise WorkNotFoundError(f"Work not found: {normalized_id}")
            visited: set[str] = set()
            while work.status == "merged":
                if work.id in visited or work.merged_into_work_id is None:
                    raise RuntimeError("Invalid Work merge chain")
                visited.add(work.id)
                target = session.get(WorkRecord, work.merged_into_work_id)
                if target is None:
                    raise RuntimeError("Merged Work target is missing")
                work = target
            return work.id


def _normalize_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).replace("\\", "/")
    filename = PurePath(normalized.split("/")[-1]).name
    safe_characters = "".join(
        character
        for character in filename
        if not unicodedata.category(character).startswith("C")
    )
    filename = " ".join(safe_characters.split())
    if not filename or len(filename) > 255:
        raise PdfImportError("original_filename must contain 1-255 safe characters")
    return filename


def _normalize_title(value: str) -> str:
    title = " ".join(unicodedata.normalize("NFKC", value).split())
    if not title or len(title) > 2_000:
        raise PdfImportError("title must contain 1-2000 characters")
    return title


def _title_from_filename(filename: str) -> str:
    title = " ".join(PurePath(filename).stem.split())
    if not title:
        raise PdfImportError("A title or descriptive PDF filename is required")
    return title


def _normalize_authors(values: tuple[str, ...]) -> tuple[str, ...]:
    authors: list[str] = []
    seen: set[str] = set()
    for value in values:
        author = " ".join(unicodedata.normalize("NFKC", value).split())
        if not author:
            continue
        if len(author) > 255:
            raise PdfImportError("author names must contain at most 255 characters")
        key = author.casefold()
        if key not in seen:
            authors.append(author)
            seen.add(key)
    return tuple(authors)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC)
