"""Deterministic venue evidence composition without turning absence into a penalty."""

from __future__ import annotations

from typing import Any

from paper_reader.recommendation.ccf import (
    CCFCatalog,
    VenueType,
    ccf_rating_evidence,
    load_builtin_ccf_catalog,
)
from paper_reader.recommendation.rating_catalogs import (
    RatingCatalogKind,
    RatingCatalogRepository,
)
from paper_reader.recommendation.relevance import EvidenceStatus, MetricEvidence

JOURNAL_RATING_WEIGHTS: dict[RatingCatalogKind, float] = {
    RatingCatalogKind.JCR: 0.34,
    RatingCatalogKind.CAS: 0.33,
    RatingCatalogKind.EMERGING: 0.33,
}


class VenueRatingService:
    def __init__(
        self,
        catalogs: RatingCatalogRepository,
        *,
        ccf_catalog: CCFCatalog | None = None,
    ) -> None:
        self._catalogs = catalogs
        self._ccf = ccf_catalog or load_builtin_ccf_catalog()

    def score(
        self,
        venue: str,
        *,
        venue_type: VenueType,
        is_computer_science: bool,
    ) -> MetricEvidence:
        if venue_type == "conference":
            return ccf_rating_evidence(
                venue,
                venue_type=venue_type,
                is_computer_science=is_computer_science,
                catalog=self._ccf,
            )
        if venue_type != "journal":  # pragma: no cover - protected by Literal typing
            raise ValueError("venue_type must be journal or conference")

        components: list[dict[str, Any]] = []
        weighted_score = 0.0
        observed_weight = 0.0
        for kind, configured_weight in JOURNAL_RATING_WEIGHTS.items():
            catalog = self._catalogs.active(kind)
            if catalog is None:
                continue
            rating = self._catalogs.find_in_catalog(catalog.id, venue)
            if rating is None:
                continue
            observed_weight += configured_weight
            weighted_score += rating.score * configured_weight
            components.append(
                {
                    "kind": kind.value,
                    "catalog_id": catalog.id,
                    "catalog_name": catalog.catalog_name,
                    "catalog_version": catalog.catalog_version,
                    "file_sha256": catalog.file_sha256,
                    "rating_label": rating.rating_label,
                    "score": rating.score,
                    "configured_weight": configured_weight,
                    "row_number": rating.row_number,
                    "identifiers": dict(rating.identifiers),
                }
            )
        if observed_weight > 0:
            coverage = round(observed_weight * 100, 6)
            return MetricEvidence.observed(
                round(weighted_score / observed_weight, 6),
                confidence=coverage,
                evidence={
                    "rule_version": "journal-ratings-v1",
                    "weights": {
                        kind.value: weight for kind, weight in JOURNAL_RATING_WEIGHTS.items()
                    },
                    "coverage": coverage,
                    "components": components,
                },
            )

        ccf = ccf_rating_evidence(
            venue,
            venue_type="journal",
            is_computer_science=is_computer_science,
            catalog=self._ccf,
        )
        if ccf.status is EvidenceStatus.OBSERVED:
            return ccf
        return MetricEvidence.unknown(
            reason="venue_not_found_in_active_rating_catalogs",
            evidence={
                "rule_version": "journal-ratings-v1",
                "active_catalogs": [
                    kind.value
                    for kind in JOURNAL_RATING_WEIGHTS
                    if self._catalogs.active(kind) is not None
                ],
                "ccf_status": ccf.status.value,
                "ccf_reason": ccf.reason,
            },
        )
