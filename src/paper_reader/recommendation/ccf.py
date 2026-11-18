"""Versioned lookup for the official CCF 2022 sixth-edition venue catalog."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType
from typing import Any, Literal, cast

from paper_reader.recommendation.relevance import MetricEvidence

VenueType = Literal["journal", "conference"]
CCFRank = Literal["A", "B", "C"]
CCF_SCORE_MAPPING: dict[CCFRank, float] = {"A": 100.0, "B": 75.0, "C": 50.0}


def normalize_venue_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


@dataclass(frozen=True, slots=True)
class CCFEntry:
    abbreviation: str
    name: str
    venue_type: VenueType
    category: str
    category_label: str
    rank: CCFRank
    publisher: str
    url: str
    aliases: tuple[str, ...]
    footnotes: tuple[str, ...]
    source_page: int


@dataclass(frozen=True, slots=True)
class CCFCatalog:
    catalog_id: str
    title: str
    edition: int
    rating_year: int
    rename_revision_date: str
    source_url: str
    source_pdf_sha256: str
    entries: tuple[CCFEntry, ...]
    _index: dict[str, tuple[CCFEntry, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.edition != 6 or self.rating_year != 2022:
            raise ValueError("unexpected CCF catalog edition")
        if len(self.source_pdf_sha256) != 64:
            raise ValueError("invalid CCF source PDF hash")
        index: dict[str, list[CCFEntry]] = {}
        for entry in self.entries:
            for value in (entry.abbreviation, entry.name, *entry.aliases):
                key = normalize_venue_key(value)
                if not key:
                    continue
                bucket = index.setdefault(key, [])
                if entry not in bucket:
                    bucket.append(entry)
        object.__setattr__(
            self,
            "_index",
            {key: tuple(values) for key, values in index.items()},
        )

    def find(
        self,
        value: str,
        *,
        venue_type: VenueType | None = None,
        category: str | None = None,
    ) -> CCFEntry | None:
        matches = self._index.get(normalize_venue_key(value), ())
        if venue_type is not None:
            matches = tuple(entry for entry in matches if entry.venue_type == venue_type)
        if category is not None:
            matches = tuple(entry for entry in matches if entry.category == category)
        return matches[0] if len(matches) == 1 else None


def _entry(raw: dict[str, Any]) -> CCFEntry:
    venue_type = str(raw["venue_type"])
    rank = str(raw["rank"])
    if venue_type not in {"journal", "conference"}:
        raise ValueError(f"invalid CCF venue type: {venue_type}")
    if rank not in {"A", "B", "C"}:
        raise ValueError(f"invalid CCF rank: {rank}")
    return CCFEntry(
        abbreviation=str(raw["abbreviation"]),
        name=str(raw["name"]),
        venue_type=cast(VenueType, venue_type),
        category=str(raw["category"]),
        category_label=str(raw["category_label"]),
        rank=cast(CCFRank, rank),
        publisher=str(raw["publisher"]),
        url=str(raw["url"]),
        aliases=tuple(str(value) for value in raw.get("aliases", [])),
        footnotes=tuple(str(value) for value in raw.get("footnotes", [])),
        source_page=int(raw["source_page"]),
    )


@lru_cache(maxsize=1)
def load_builtin_ccf_catalog() -> CCFCatalog:
    resource = files("paper_reader.data").joinpath("ccf-2022-sixth-2024-rename.json")
    raw = cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))
    entries = tuple(_entry(cast(dict[str, Any], item)) for item in raw["entries"])
    if len(entries) != 649:
        raise ValueError("bundled CCF catalog is incomplete")
    return CCFCatalog(
        catalog_id=str(raw["catalog_id"]),
        title=str(raw["title"]),
        edition=int(raw["edition"]),
        rating_year=int(raw["rating_year"]),
        rename_revision_date=str(raw["rename_revision_date"]),
        source_url=str(raw["source_url"]),
        source_pdf_sha256=str(raw["source_pdf_sha256"]),
        entries=entries,
    )


def ccf_rating_evidence(
    venue: str,
    *,
    venue_type: VenueType,
    is_computer_science: bool,
    catalog: CCFCatalog | None = None,
) -> MetricEvidence:
    if not is_computer_science:
        return MetricEvidence.not_applicable(
            reason="ccf_not_applicable_outside_computer_science",
            evidence={"scope": "computer_science_only"},
        )
    selected = catalog or load_builtin_ccf_catalog()
    match = selected.find(venue, venue_type=venue_type)
    if match is None:
        return MetricEvidence.unknown(
            reason="venue_not_listed_in_ccf",
            evidence={"catalog_id": selected.catalog_id, "query": venue},
        )
    return MetricEvidence.observed(
        CCF_SCORE_MAPPING[match.rank],
        confidence=80,
        evidence={
            "catalog_id": selected.catalog_id,
            "catalog_title": selected.title,
            "source_url": selected.source_url,
            "source_pdf_sha256": selected.source_pdf_sha256,
            "rank": match.rank,
            "mapping_version": "ccf-abc-v1",
            "category": match.category,
            "venue": match.name or match.abbreviation,
            "publisher": match.publisher,
            "source_page": match.source_page,
        },
    )


CCF_CATALOG_METADATA = MappingProxyType(
    {
        "catalog_id": "ccf-2022-sixth-2024-rename",
        "source_url": (
            "https://www.ccf.org.cn/Academic_Evaluation/By_category/2024-06-28/825349.shtml"
        ),
    }
)
