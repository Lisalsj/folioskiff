"""Shared, authoritative local date and confirmed-keyword filtering."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from paper_reader.discovery.identity import SourcePaper
from paper_reader.discovery.keywords import CompiledKeywordQuery
from paper_reader.discovery.plans import ResolvedDateRange


@dataclass(frozen=True, slots=True)
class SourceFilterCounts:
    evaluated_count: int = 0
    missing_updated_at_count: int = 0
    outside_date_range_count: int = 0
    in_date_range_count: int = 0
    keyword_mismatch_count: int = 0
    accepted_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    def add(self, other: SourceFilterCounts) -> SourceFilterCounts:
        other_counts = other.to_dict()
        return SourceFilterCounts(
            **{key: value + other_counts[key] for key, value in self.to_dict().items()}
        )

    @classmethod
    def from_checkpoint(cls, checkpoint: dict[str, object]) -> SourceFilterCounts | None:
        value = checkpoint.get("filter_counts")
        if not isinstance(value, dict):
            return None
        keys = cls().to_dict()
        if any(type(value.get(key)) is not int or value[key] < 0 for key in keys):
            return None
        return cls(**{key: value[key] for key in keys})


def filter_source_records(
    records: tuple[SourcePaper, ...],
    *,
    resolved: ResolvedDateRange,
    compiled: CompiledKeywordQuery,
) -> tuple[tuple[SourcePaper, ...], SourceFilterCounts]:
    accepted: list[SourcePaper] = []
    missing_dates = outside_dates = keyword_mismatches = 0
    for paper in records:
        if paper.updated_at is None:
            missing_dates += 1
        elif not resolved.contains_updated_at(paper.updated_at):
            outside_dates += 1
        elif not compiled.matches(title=paper.title, abstract=paper.abstract):
            keyword_mismatches += 1
        else:
            accepted.append(paper)
    return tuple(accepted), SourceFilterCounts(
        evaluated_count=len(records),
        missing_updated_at_count=missing_dates,
        outside_date_range_count=outside_dates,
        in_date_range_count=keyword_mismatches + len(accepted),
        keyword_mismatch_count=keyword_mismatches,
        accepted_count=len(accepted),
    )
