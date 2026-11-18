"""Assemble seven honest quality dimensions from frozen, provenanced evidence."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import SourceRecord, WorkRecord
from paper_reader.recommendation.artifact_impact import (
    ArtifactImpactMetric,
    ExplicitArtifactLink,
    extract_explicit_artifact_links,
    score_code_data_impact,
)
from paper_reader.recommendation.enrichment import EnrichmentStatus
from paper_reader.recommendation.integrity import IntegrityAssessment, assess_integrity
from paper_reader.recommendation.observations import (
    MetadataObservationRepository,
    StoredEnrichment,
)
from paper_reader.recommendation.quality import (
    QUALITY_RULE_VERSION,
    QualityMetrics,
    QualityScore,
    score_quality,
)
from paper_reader.recommendation.relevance import EvidenceStatus, MetricEvidence
from paper_reader.recommendation.venue_rating import VenueRatingService


class ArtifactMetricProvider(Protocol):
    def metrics_for(
        self,
        links: Sequence[ExplicitArtifactLink],
    ) -> Sequence[ArtifactImpactMetric]: ...


class NullArtifactMetricProvider:
    def metrics_for(
        self,
        links: Sequence[ExplicitArtifactLink],
    ) -> tuple[ArtifactImpactMetric, ...]:
        del links
        return ()


@dataclass(frozen=True, slots=True)
class QualityEvidence:
    work_id: str
    metrics: QualityMetrics
    quality: QualityScore
    integrity: IntegrityAssessment
    observation_ids: tuple[str, ...]
    degradation_reasons: tuple[str, ...]


class QualityEvidenceBuilder:
    """Map only explicit source fields to the v1 objective-quality rubric."""

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        observations: MetadataObservationRepository,
        venue_ratings: VenueRatingService,
        artifact_metrics: ArtifactMetricProvider | None = None,
    ) -> None:
        self._sessions = sessions
        self._observations = observations
        self._venue_ratings = venue_ratings
        self._artifact_metrics = artifact_metrics or NullArtifactMetricProvider()

    def freeze_observation_ids(
        self,
        work_ids: Sequence[str],
    ) -> dict[str, tuple[str, ...]]:
        latest = self._observations.latest_by_enricher_many(work_ids)
        return {
            work_id: tuple(
                observation.id
                for _, observation in sorted(
                    latest.get(work_id, {}).items(),
                    key=lambda item: item[0],
                )
            )
            for work_id in dict.fromkeys(work_ids)
        }

    def build(
        self,
        work_id: str,
        *,
        observation_ids: Sequence[str] | None = None,
        rule_version: str = QUALITY_RULE_VERSION,
        weights: Mapping[str, float] | None = None,
    ) -> QualityEvidence:
        work, sources = self._work_facts(work_id)
        stored = self._stored_observations(work.id, observation_ids)
        latest = self._latest(stored)
        results = tuple(observation.result for observation in latest.values())
        links = extract_explicit_artifact_links(results)
        artifact_metrics = tuple(self._artifact_metrics.metrics_for(links))
        metrics = QualityMetrics(
            citation_performance=self._citation_performance(latest),
            venue_rating=self._venue_rating(latest),
            author_impact=self._author_impact(latest),
            institution_impact=self._institution_impact(latest),
            code_data_impact=score_code_data_impact(
                links=links,
                metrics=artifact_metrics,
            ),
            research_transparency=self._research_transparency(links),
            publication_maturity=self._publication_maturity(latest, sources),
        )
        quality = score_quality(
            metrics,
            weights=weights,
            rule_version=rule_version,
        )
        integrity = assess_integrity(results)
        observation_manifest = tuple(
            observation.id
            for _, observation in sorted(latest.items(), key=lambda item: item[0])
        )
        degradation_reasons = tuple(
            dict.fromkeys(
                (
                    *quality.degradation_reasons,
                    *(
                        ("integrity_evidence_incomplete",)
                        if integrity.evidence_coverage < 100
                        else ()
                    ),
                )
            )
        )
        return QualityEvidence(
            work_id=work.id,
            metrics=metrics,
            quality=quality,
            integrity=integrity,
            observation_ids=observation_manifest,
            degradation_reasons=degradation_reasons,
        )

    def _stored_observations(
        self,
        work_id: str,
        observation_ids: Sequence[str] | None,
    ) -> tuple[StoredEnrichment, ...]:
        if observation_ids is None:
            return tuple(self._observations.latest_by_enricher(work_id).values())
        stored = self._observations.get_many(observation_ids)
        if any(observation.result.work_id != work_id for observation in stored):
            raise ValueError("frozen observation belongs to a different Work")
        return stored

    def _work_facts(self, work_id: str) -> tuple[WorkRecord, tuple[SourceRecord, ...]]:
        with self._sessions() as session:
            work = session.get(WorkRecord, work_id)
            if work is None:
                raise KeyError(work_id)
            records = tuple(
                session.scalars(
                    select(SourceRecord)
                    .where(SourceRecord.work_id == work.id)
                    .order_by(SourceRecord.connector_id, SourceRecord.external_id)
                )
            )
            session.expunge(work)
            for record in records:
                session.expunge(record)
        return work, records

    @staticmethod
    def _latest(
        stored: Sequence[StoredEnrichment],
    ) -> dict[str, StoredEnrichment]:
        latest: dict[str, StoredEnrichment] = {}
        for observation in sorted(
            stored,
            key=lambda item: (item.result.fetched_at, item.id),
            reverse=True,
        ):
            latest.setdefault(observation.result.enricher_id, observation)
        return latest

    @classmethod
    def _citation_performance(
        cls,
        latest: Mapping[str, StoredEnrichment],
    ) -> MetricEvidence:
        openalex = cls._observed(latest.get("openalex"))
        semantic = cls._observed(latest.get("semantic_scholar"))
        cross_validation = cls._semantic_cross_validation(semantic)
        if openalex is None:
            return MetricEvidence.unknown(
                reason="openalex_normalized_citation_unavailable",
                evidence={
                    "primary_source": "openalex",
                    "raw_citation_counts_not_scored": True,
                    "semantic_scholar_cross_validation": cross_validation,
                },
            )

        fields = openalex.result.fields
        percentile = cls._percentile_value(fields.get("citation_normalized_percentile"))
        percentile_path = "citation_normalized_percentile"
        if percentile is None:
            percentile = cls._year_percentile(fields.get("citation_percentile_year"))
            percentile_path = "citation_percentile_year"
        fwci = cls._number(fields.get("fwci"))
        if percentile is not None:
            return MetricEvidence.observed(
                percentile,
                confidence=95,
                evidence={
                    "primary_source": "openalex",
                    "observation_id": openalex.id,
                    "field_path": openalex.result.field_provenance.get(
                        percentile_path,
                        f"openalex:{percentile_path}",
                    ),
                    "normalized_percentile": percentile,
                    "fwci": fwci,
                    "semantic_scholar_cross_validation": cross_validation,
                },
            )
        if fwci is not None:
            return MetricEvidence.observed(
                cls._fwci_score(fwci),
                confidence=80,
                evidence={
                    "primary_source": "openalex",
                    "observation_id": openalex.id,
                    "field_path": openalex.result.field_provenance.get(
                        "fwci",
                        "openalex:fwci",
                    ),
                    "fwci": fwci,
                    "mapping_version": "fwci-log2-v1",
                    "semantic_scholar_cross_validation": cross_validation,
                },
            )
        return MetricEvidence.unknown(
            reason="openalex_normalized_citation_unavailable",
            evidence={
                "primary_source": "openalex",
                "observation_id": openalex.id,
                "raw_citation_counts_not_scored": True,
                "semantic_scholar_cross_validation": cross_validation,
            },
        )

    def _venue_rating(
        self,
        latest: Mapping[str, StoredEnrichment],
    ) -> MetricEvidence:
        venue_name, venue_type, venue_evidence = self._venue_facts(latest)
        if venue_name is None:
            return MetricEvidence.unknown(
                reason="publication_venue_unavailable",
                evidence=venue_evidence,
            )
        if venue_type is None:
            return MetricEvidence.unknown(
                reason="publication_venue_type_unavailable",
                evidence={**venue_evidence, "venue": venue_name},
            )
        is_computer_science = self._computer_science_scope(latest)
        if venue_type == "conference" and is_computer_science is None:
            return MetricEvidence.unknown(
                reason="computer_science_scope_unknown",
                evidence={**venue_evidence, "venue": venue_name},
            )
        rating = self._venue_ratings.score(
            venue_name,
            venue_type=venue_type,
            is_computer_science=bool(is_computer_science),
        )
        return self._merge_metric_evidence(
            rating,
            {
                **venue_evidence,
                "venue": venue_name,
                "venue_type": venue_type,
                "computer_science_scope": is_computer_science,
            },
        )

    @classmethod
    def _author_impact(
        cls,
        latest: Mapping[str, StoredEnrichment],
    ) -> MetricEvidence:
        openalex = cls._observed(latest.get("openalex"))
        raw_authors = openalex.result.fields.get("authors") if openalex else None
        author_ids = (
            [
                item.get("id")
                for item in raw_authors
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ]
            if isinstance(raw_authors, list)
            else []
        )
        return MetricEvidence.unknown(
            reason="public_author_impact_metrics_unavailable",
            evidence={
                "known_public_author_ids": author_ids,
                "name_based_inference_prohibited": True,
            },
        )

    @classmethod
    def _institution_impact(
        cls,
        latest: Mapping[str, StoredEnrichment],
    ) -> MetricEvidence:
        openalex = cls._observed(latest.get("openalex"))
        raw_authors = openalex.result.fields.get("authors") if openalex else None
        institution_ids: list[str] = []
        if isinstance(raw_authors, list):
            for author in raw_authors:
                if not isinstance(author, dict):
                    continue
                institutions = author.get("institutions")
                if not isinstance(institutions, list):
                    continue
                for institution in institutions:
                    if isinstance(institution, dict) and isinstance(
                        institution.get("id"), str
                    ):
                        institution_ids.append(institution["id"])
        return MetricEvidence.unknown(
            reason="versioned_institution_impact_rule_unavailable",
            evidence={
                "known_public_institution_ids": list(dict.fromkeys(institution_ids)),
                "name_based_inference_prohibited": True,
            },
        )

    @staticmethod
    def _research_transparency(
        links: Sequence[ExplicitArtifactLink],
    ) -> MetricEvidence:
        if not links:
            return MetricEvidence.unknown(
                reason="explicit_transparency_evidence_unavailable",
                evidence={"absence_is_not_negative": True},
            )
        kinds = tuple(dict.fromkeys(link.kind.value for link in links))
        score = min(90.0, 60.0 + (15.0 * len(kinds)))
        return MetricEvidence.observed(
            score,
            confidence=min(link.confidence for link in links),
            evidence={
                "rule_version": "research-transparency-v1",
                "explicit_artifact_kinds": kinds,
                "links": [
                    {
                        "kind": link.kind.value,
                        "identity": link.identity,
                        "evidence_source": link.evidence_source.value,
                        "evidence_path": link.evidence_path,
                    }
                    for link in links
                ],
                "absence_is_not_negative": True,
            },
        )

    @classmethod
    def _publication_maturity(
        cls,
        latest: Mapping[str, StoredEnrichment],
        sources: Sequence[SourceRecord],
    ) -> MetricEvidence:
        candidates: list[dict[str, Any]] = []
        openalex = cls._observed(latest.get("openalex"))
        if openalex is not None:
            venue = openalex.result.fields.get("venue")
            version = venue.get("version") if isinstance(venue, dict) else None
            mapped = cls._maturity_version(version)
            if mapped is not None:
                candidates.append(
                    {
                        "score": mapped,
                        "source": "openalex",
                        "observation_id": openalex.id,
                        "field": "primary_location.version",
                        "value": version,
                        "confidence": 90,
                    }
                )
        crossref = cls._observed(latest.get("crossref"))
        if crossref is not None:
            value = crossref.result.fields.get("work_type")
            mapped = cls._maturity_type(value)
            if mapped is not None:
                candidates.append(
                    {
                        "score": mapped,
                        "source": "crossref",
                        "observation_id": crossref.id,
                        "field": "work_type",
                        "value": value,
                        "confidence": 90,
                    }
                )
        semantic = cls._observed(latest.get("semantic_scholar"))
        if semantic is not None:
            values = semantic.result.fields.get("publication_types")
            if isinstance(values, list):
                mapped_values = [cls._maturity_type(value) for value in values]
                mapped = max(
                    (value for value in mapped_values if value is not None),
                    default=None,
                )
                if mapped is not None:
                    candidates.append(
                        {
                            "score": mapped,
                            "source": "semantic_scholar",
                            "observation_id": semantic.id,
                            "field": "publication_types",
                            "value": values,
                            "confidence": 80,
                        }
                    )
        source_scores = {
            "ieee": 95.0,
            "springer_nature": 95.0,
            "europe_pmc": 90.0,
            "openreview": 70.0,
            "arxiv": 50.0,
        }
        for source in sources:
            mapped = source_scores.get(source.connector_id)
            if mapped is not None:
                candidates.append(
                    {
                        "score": mapped,
                        "source": "source_record",
                        "connector_id": source.connector_id,
                        "source_record_id": source.id,
                        "confidence": 70,
                    }
                )
        if not candidates:
            return MetricEvidence.unknown(
                reason="publication_maturity_unavailable",
                evidence={"absence_is_not_negative": True},
            )
        selected = max(
            candidates,
            key=lambda item: (float(item["score"]), int(item["confidence"])),
        )
        return MetricEvidence.observed(
            float(selected["score"]),
            confidence=float(selected["confidence"]),
            evidence={
                "rule_version": "publication-maturity-v1",
                "selected": selected,
                "candidates": candidates,
            },
        )

    @classmethod
    def _venue_facts(
        cls,
        latest: Mapping[str, StoredEnrichment],
    ) -> tuple[
        str | None,
        Literal["journal", "conference"] | None,
        dict[str, Any],
    ]:
        venue_name: str | None = None
        venue_source: dict[str, Any] = {}
        for enricher_id in ("openalex", "semantic_scholar", "crossref"):
            stored = cls._observed(latest.get(enricher_id))
            if stored is None:
                continue
            raw_venue = stored.result.fields.get("venue")
            name = raw_venue.get("name") if isinstance(raw_venue, dict) else raw_venue
            if isinstance(name, str) and name.strip():
                venue_name = name.strip()
                venue_source = {
                    "venue_source": enricher_id,
                    "venue_observation_id": stored.id,
                    "venue_field_path": stored.result.field_provenance.get(
                        "venue",
                        f"{enricher_id}:venue",
                    ),
                }
                break

        venue_type: Literal["journal", "conference"] | None = None
        semantic = cls._observed(latest.get("semantic_scholar"))
        if semantic is not None:
            raw_venue = semantic.result.fields.get("venue")
            raw_type = raw_venue.get("type") if isinstance(raw_venue, dict) else None
            venue_type = cls._venue_type(raw_type)
            if venue_type is None:
                publication_types = semantic.result.fields.get("publication_types")
                if isinstance(publication_types, list):
                    venue_type = next(
                        (
                            mapped
                            for value in publication_types
                            if (mapped := cls._venue_type(value)) is not None
                        ),
                        None,
                    )
        crossref = cls._observed(latest.get("crossref"))
        if venue_type is None and crossref is not None:
            venue_type = cls._venue_type(crossref.result.fields.get("work_type"))
        return venue_name, venue_type, venue_source

    @classmethod
    def _computer_science_scope(
        cls,
        latest: Mapping[str, StoredEnrichment],
    ) -> bool | None:
        semantic = cls._observed(latest.get("semantic_scholar"))
        if semantic is not None:
            values = semantic.result.fields.get("fields_of_study")
            if isinstance(values, list) and values:
                names = {cls._normalized(str(value)) for value in values}
                return any("computer science" in name for name in names)
        openalex = cls._observed(latest.get("openalex"))
        if openalex is not None:
            for field in ("primary_topic", "topics"):
                value = openalex.result.fields.get(field)
                texts = tuple(cls._nested_text(value))
                if texts:
                    normalized = {cls._normalized(text) for text in texts}
                    if any("computer science" in text for text in normalized):
                        return True
                    if field == "primary_topic":
                        return False
        return None

    @staticmethod
    def _observed(value: StoredEnrichment | None) -> StoredEnrichment | None:
        if value is None or value.result.status is not EnrichmentStatus.OBSERVED:
            return None
        return value

    @staticmethod
    def _semantic_cross_validation(
        semantic: StoredEnrichment | None,
    ) -> dict[str, Any]:
        if semantic is None:
            return {}
        return {
            key: semantic.result.fields[key]
            for key in ("citation_count", "influential_citation_count")
            if key in semantic.result.fields
        }

    @classmethod
    def _percentile_value(cls, value: object) -> float | None:
        raw = value.get("value") if isinstance(value, dict) else value
        number = cls._number(raw)
        if number is None:
            return None
        if 0 <= number <= 1:
            return round(number * 100, 6)
        return round(number, 6) if 0 <= number <= 100 else None

    @classmethod
    def _year_percentile(cls, value: object) -> float | None:
        if isinstance(value, dict):
            minimum = cls._number(value.get("min"))
            maximum = cls._number(value.get("max"))
            if minimum is not None and maximum is not None:
                return round((minimum + maximum) / 2, 6)
        return cls._percentile_value(value)

    @staticmethod
    def _fwci_score(value: float) -> float:
        if value <= 0:
            return 0.0
        return round(min(100.0, max(0.0, 50.0 + (25.0 * math.log2(value)))), 6)

    @staticmethod
    def _number(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None

    @staticmethod
    def _venue_type(value: object) -> Literal["journal", "conference"] | None:
        if not isinstance(value, str):
            return None
        normalized = "_".join(value.strip().casefold().replace("-", " ").split())
        if "conference" in normalized or "proceedings" in normalized:
            return "conference"
        if "journal" in normalized:
            return "journal"
        return None

    @staticmethod
    def _maturity_version(value: object) -> float | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().casefold()
        return {
            "publishedversion": 100.0,
            "acceptedversion": 80.0,
            "submittedversion": 50.0,
        }.get(normalized.replace("_", ""))

    @classmethod
    def _maturity_type(cls, value: object) -> float | None:
        if not isinstance(value, str):
            return None
        normalized = cls._normalized(value).replace("-", " ").replace("_", " ")
        if "journal" in normalized:
            return 100.0
        if "conference" in normalized or "proceedings" in normalized:
            return 90.0
        if "book chapter" in normalized:
            return 85.0
        if "preprint" in normalized or "posted content" in normalized:
            return 50.0
        return None

    @staticmethod
    def _merge_metric_evidence(
        metric: MetricEvidence,
        extra: Mapping[str, Any],
    ) -> MetricEvidence:
        evidence = {**dict(metric.evidence), **dict(extra)}
        if metric.status is EvidenceStatus.OBSERVED:
            assert metric.value is not None
            return MetricEvidence.observed(
                metric.value,
                confidence=metric.confidence,
                evidence=evidence,
            )
        if metric.status is EvidenceStatus.NOT_APPLICABLE:
            return MetricEvidence.not_applicable(
                reason=metric.reason or "not_applicable",
                evidence=evidence,
            )
        return MetricEvidence.unknown(
            reason=metric.reason or "unknown",
            evidence=evidence,
        )

    @classmethod
    def _nested_text(cls, value: object) -> Sequence[str]:
        texts: list[str] = []
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, dict):
            for nested in value.values():
                texts.extend(cls._nested_text(nested))
        elif isinstance(value, list):
            for nested in value:
                texts.extend(cls._nested_text(nested))
        return texts

    @staticmethod
    def _normalized(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
