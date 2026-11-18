"""Versioned keyword groups, deterministic diffs, confirmation, and filtering."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import KeywordConfirmationRecord, KeywordSetVersionRecord


class ConceptGroup(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=120)
    terms: tuple[str, ...] = Field(min_length=1)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("label cannot be blank")
        return cleaned

    @field_validator("terms")
    @classmethod
    def clean_terms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            term = " ".join(value.split())
            identity = unicodedata.normalize("NFKC", term).casefold()
            if not identity:
                raise ValueError("terms cannot contain blanks")
            if identity not in seen:
                cleaned.append(term)
                seen.add(identity)
        if not cleaned:
            raise ValueError("at least one term is required")
        return tuple(cleaned)


class KeywordSetDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    required: tuple[ConceptGroup, ...] = Field(min_length=1)
    boost: tuple[ConceptGroup, ...] = ()
    excluded: tuple[ConceptGroup, ...] = ()

    @model_validator(mode="after")
    def unique_group_keys(self) -> Self:
        keys = [group.key for group in (*self.required, *self.boost, *self.excluded)]
        if len(keys) != len(set(keys)):
            raise ValueError("concept group keys must be unique across all sections")
        return self


class UnconfirmedKeywordVersionError(RuntimeError):
    pass


class KeywordVersionNotFoundError(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class KeywordVersion:
    id: str
    research_intent: str
    parent_version_id: str | None
    definition: KeywordSetDefinition
    change_summary: dict[str, Any]
    generator_info: dict[str, Any]
    created_at: datetime
    confirmed_at: datetime | None


def _normalized_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


@dataclass(frozen=True, slots=True)
class CompiledKeywordQuery:
    version_id: str
    boolean_expression: str
    required_groups: tuple[tuple[str, ...], ...]
    boost_terms: tuple[str, ...]
    excluded_groups: tuple[tuple[str, ...], ...]

    def matches(self, *, title: str, abstract: str | None = None) -> bool:
        haystack = _normalized_text(f"{title}\n{abstract or ''}")

        def contains(term: str) -> bool:
            return _normalized_text(term) in haystack

        required_match = all(
            any(contains(term) for term in group) for group in self.required_groups
        )
        excluded_match = any(
            any(contains(term) for term in group) for group in self.excluded_groups
        )
        return required_match and not excluded_match


EMPTY_DIFF: dict[str, Any] = {
    "added_groups": [],
    "removed_groups": [],
    "added_terms": {},
    "removed_terms": {},
}


def diff_definitions(
    parent: KeywordSetDefinition,
    child: KeywordSetDefinition,
) -> dict[str, Any]:
    def group_map(definition: KeywordSetDefinition) -> dict[str, ConceptGroup]:
        result: dict[str, ConceptGroup] = {}
        for section in ("required", "boost", "excluded"):
            for group in getattr(definition, section):
                result[f"{section}:{group.key}"] = group
        return result

    old = group_map(parent)
    new = group_map(child)
    common = sorted(old.keys() & new.keys())
    added_terms: dict[str, list[str]] = {}
    removed_terms: dict[str, list[str]] = {}
    for key in common:
        old_terms = {_normalized_text(term): term for term in old[key].terms}
        new_terms = {_normalized_text(term): term for term in new[key].terms}
        added = [new_terms[item] for item in sorted(new_terms.keys() - old_terms.keys())]
        removed = [old_terms[item] for item in sorted(old_terms.keys() - new_terms.keys())]
        if added:
            added_terms[key] = added
        if removed:
            removed_terms[key] = removed
    return {
        "added_groups": sorted(new.keys() - old.keys()),
        "removed_groups": sorted(old.keys() - new.keys()),
        "added_terms": added_terms,
        "removed_terms": removed_terms,
    }


def _quote_term(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def compile_concept_groups(
    version_id: str,
    *,
    required_groups: tuple[tuple[str, ...], ...],
    boost_terms: tuple[str, ...] = (),
    excluded_groups: tuple[tuple[str, ...], ...] = (),
) -> CompiledKeywordQuery:
    """Compile already validated concept groups through the canonical query path."""

    required_groups = tuple(tuple(group) for group in required_groups)
    boost_terms = tuple(boost_terms)
    excluded_groups = tuple(tuple(group) for group in excluded_groups)
    required_parts = [
        f"({' OR '.join(_quote_term(term) for term in group)})"
        for group in required_groups
    ]
    excluded_parts = [
        f"NOT ({' OR '.join(_quote_term(term) for term in group)})"
        for group in excluded_groups
    ]
    expression = " AND ".join((*required_parts, *excluded_parts))
    return CompiledKeywordQuery(
        version_id=version_id,
        boolean_expression=expression,
        required_groups=required_groups,
        boost_terms=boost_terms,
        excluded_groups=excluded_groups,
    )


def compile_definition(version_id: str, definition: KeywordSetDefinition) -> CompiledKeywordQuery:
    return compile_concept_groups(
        version_id,
        required_groups=tuple(tuple(group.terms) for group in definition.required),
        boost_terms=tuple(term for group in definition.boost for term in group.terms),
        excluded_groups=tuple(tuple(group.terms) for group in definition.excluded),
    )


class KeywordRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create_version(
        self,
        *,
        research_intent: str,
        definition: KeywordSetDefinition,
        parent_version_id: str | None = None,
        generator_info: dict[str, Any] | None = None,
    ) -> KeywordVersion:
        intent = " ".join(research_intent.split())
        if not intent:
            raise ValueError("research_intent is required")

        with self._sessions() as session:
            change_summary = dict(EMPTY_DIFF)
            if parent_version_id is not None:
                parent_record = session.get(KeywordSetVersionRecord, parent_version_id)
                if parent_record is None:
                    raise KeywordVersionNotFoundError(parent_version_id)
                change_summary = diff_definitions(
                    self._definition(parent_record),
                    definition,
                )
            payload = definition.model_dump(mode="json")
            record = KeywordSetVersionRecord(
                parent_version_id=parent_version_id,
                research_intent=intent,
                required_groups=payload["required"],
                boost_groups=payload["boost"],
                excluded_groups=payload["excluded"],
                change_summary=change_summary,
                generator_info=dict(generator_info or {}),
            )
            session.add(record)
            session.commit()
            return self._to_version(record, None)

    def confirm(self, version_id: str) -> KeywordVersion:
        with self._sessions() as session:
            record = session.get(KeywordSetVersionRecord, version_id)
            if record is None:
                raise KeywordVersionNotFoundError(version_id)
            confirmation = session.get(KeywordConfirmationRecord, version_id)
            if confirmation is None:
                confirmation = KeywordConfirmationRecord(keyword_version_id=version_id)
                session.add(confirmation)
                session.commit()
            return self._to_version(record, confirmation)

    def get(self, version_id: str) -> KeywordVersion:
        with self._sessions() as session:
            record = session.get(KeywordSetVersionRecord, version_id)
            if record is None:
                raise KeywordVersionNotFoundError(version_id)
            confirmation = session.get(KeywordConfirmationRecord, version_id)
            return self._to_version(record, confirmation)

    def compile(self, version_id: str) -> CompiledKeywordQuery:
        version = self.get(version_id)
        if version.confirmed_at is None:
            raise UnconfirmedKeywordVersionError(
                f"Keyword version {version_id} must be confirmed before it can be compiled"
            )
        return compile_definition(version.id, version.definition)

    @staticmethod
    def _definition(record: KeywordSetVersionRecord) -> KeywordSetDefinition:
        return KeywordSetDefinition.model_validate(
            {
                "required": record.required_groups,
                "boost": record.boost_groups,
                "excluded": record.excluded_groups,
            }
        )

    @classmethod
    def _to_version(
        cls,
        record: KeywordSetVersionRecord,
        confirmation: KeywordConfirmationRecord | None,
    ) -> KeywordVersion:
        created_at = (
            record.created_at.replace(tzinfo=UTC)
            if record.created_at.tzinfo is None
            else record.created_at.astimezone(UTC)
        )
        confirmed_at = confirmation.confirmed_at if confirmation is not None else None
        if confirmed_at is not None:
            confirmed_at = (
                confirmed_at.replace(tzinfo=UTC)
                if confirmed_at.tzinfo is None
                else confirmed_at.astimezone(UTC)
            )
        return KeywordVersion(
            id=record.id,
            research_intent=record.research_intent,
            parent_version_id=record.parent_version_id,
            definition=cls._definition(record),
            change_summary=dict(record.change_summary),
            generator_info=dict(record.generator_info),
            created_at=created_at,
            confirmed_at=confirmed_at,
        )
