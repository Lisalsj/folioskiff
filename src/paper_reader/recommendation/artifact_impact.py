"""Code, model, and data impact gated by explicit paper-to-artifact links."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from paper_reader.recommendation.enrichment import EnrichmentResult, EnrichmentStatus
from paper_reader.recommendation.relevance import MetricEvidence

ARTIFACT_IMPACT_RULE_VERSION = "artifact-impact-v1"


class ArtifactKind(StrEnum):
    CODE = "code"
    MODEL = "model"
    DATA = "data"


class ArtifactProvider(StrEnum):
    GITHUB = "github"
    HUGGING_FACE = "hugging_face"


class ArtifactEvidenceSource(StrEnum):
    PAPER_CONTENT = "paper_content"
    PUBLISHER_METADATA = "publisher_metadata"
    CROSSREF_RELATION = "crossref_relation"
    SOURCE_RECORD = "source_record"
    USER_CONFIRMED = "user_confirmed"


@dataclass(frozen=True, slots=True)
class ExplicitArtifactLink:
    kind: ArtifactKind
    url: str
    evidence_source: ArtifactEvidenceSource
    evidence_path: str
    confidence: float = 100.0
    provider: ArtifactProvider = field(init=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        kind = ArtifactKind(self.kind)
        source = ArtifactEvidenceSource(self.evidence_source)
        provider, identity = _artifact_identity(self.url)
        if kind is ArtifactKind.CODE and provider is not ArtifactProvider.GITHUB:
            raise ValueError("code artifact URL must point to GitHub")
        if kind is ArtifactKind.MODEL and not identity.startswith("hugging_face:model:"):
            raise ValueError("model artifact URL must point to a Hugging Face model")
        if kind is ArtifactKind.DATA and not identity.startswith("hugging_face:dataset:"):
            raise ValueError("data artifact URL must point to a Hugging Face dataset")
        if not self.evidence_path.strip():
            raise ValueError("artifact evidence_path must not be blank")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 100:
            raise ValueError("artifact confidence must be between 0 and 100")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "evidence_source", source)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "identity", identity)


@dataclass(frozen=True, slots=True)
class ArtifactImpactMetric:
    url: str
    impact_score: float
    metrics: Mapping[str, Any]
    provider: ArtifactProvider = field(init=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        provider, identity = _artifact_identity(self.url)
        if not math.isfinite(self.impact_score) or not 0 <= self.impact_score <= 100:
            raise ValueError("artifact impact_score must be between 0 and 100")
        object.__setattr__(self, "impact_score", float(self.impact_score))
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "identity", identity)


def extract_explicit_artifact_links(
    results: Iterable[EnrichmentResult],
) -> tuple[ExplicitArtifactLink, ...]:
    """Extract conservative artifact links from publisher-asserted Crossref relations."""

    crossref = _latest_crossref_result(results)
    if crossref is None:
        return ()
    raw_relations = crossref.fields.get("relations")
    if not isinstance(raw_relations, dict):
        return ()
    relations = {
        _normalize_relation_type(key): value
        for key, value in raw_relations.items()
        if isinstance(key, str)
    }
    base_path = crossref.field_provenance.get("relations", "crossref:relation")
    links: dict[str, ExplicitArtifactLink] = {}
    for relation_type in ("is-supplemented-by", "references"):
        values = relations.get(relation_type)
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                continue
            id_type = value.get("id-type")
            asserted_by = value.get("asserted-by")
            url = value.get("id")
            if (
                not isinstance(id_type, str)
                or id_type.casefold() != "uri"
                or not isinstance(asserted_by, str)
                or asserted_by.casefold() != "subject"
                or not isinstance(url, str)
            ):
                continue
            try:
                _, identity = _artifact_identity(url)
                kind = _kind_from_identity(identity)
                link = ExplicitArtifactLink(
                    kind=kind,
                    url=url,
                    evidence_source=ArtifactEvidenceSource.CROSSREF_RELATION,
                    evidence_path=f"{base_path}.{relation_type}[{index}]",
                    confidence=85,
                )
            except ValueError:
                continue
            links.setdefault(link.identity, link)
    return tuple(links.values())


def score_code_data_impact(
    *,
    links: Sequence[ExplicitArtifactLink],
    metrics: Sequence[ArtifactImpactMetric],
) -> MetricEvidence:
    """Use popularity evidence only when it exactly matches an explicit link."""

    if not links:
        return MetricEvidence.unknown(
            reason="explicit_artifact_link_missing",
            evidence={
                "rule_version": ARTIFACT_IMPACT_RULE_VERSION,
                "ignored_metric_count": len(metrics),
                "ignored_metric_identities": [metric.identity for metric in metrics],
            },
        )
    unique_links = _unique_links(links)
    metric_by_identity = _unique_metrics(metrics)
    matched: list[dict[str, Any]] = []
    matched_identities: set[str] = set()
    scores: list[float] = []
    confidences: list[float] = []
    for link in unique_links:
        metric = metric_by_identity.get(link.identity)
        if metric is None:
            continue
        matched_identities.add(link.identity)
        scores.append(metric.impact_score)
        confidences.append(link.confidence)
        matched.append(
            {
                "kind": link.kind.value,
                "provider": link.provider.value,
                "identity": link.identity,
                "url": link.url,
                "evidence_source": link.evidence_source.value,
                "evidence_path": link.evidence_path,
                "impact_score": metric.impact_score,
                "metrics": dict(metric.metrics),
            }
        )
    ignored_metric_count = sum(
        metric.identity not in matched_identities for metric in metrics
    )
    if not matched:
        return MetricEvidence.unknown(
            reason="artifact_impact_metrics_missing",
            evidence={
                "rule_version": ARTIFACT_IMPACT_RULE_VERSION,
                "linked_artifact_count": len(unique_links),
                "linked_artifacts": [_link_evidence(link) for link in unique_links],
                "ignored_metric_count": ignored_metric_count,
            },
        )
    return MetricEvidence.observed(
        sum(scores) / len(scores),
        confidence=min(confidences),
        evidence={
            "rule_version": ARTIFACT_IMPACT_RULE_VERSION,
            "aggregation": "mean_linked_artifact_impact",
            "matched_artifacts": matched,
            "ignored_metric_count": ignored_metric_count,
        },
    )


def _unique_links(
    links: Sequence[ExplicitArtifactLink],
) -> tuple[ExplicitArtifactLink, ...]:
    unique: dict[str, ExplicitArtifactLink] = {}
    for link in links:
        existing = unique.get(link.identity)
        if existing is None or link.confidence > existing.confidence:
            unique[link.identity] = link
    return tuple(unique.values())


def _latest_crossref_result(
    results: Iterable[EnrichmentResult],
) -> EnrichmentResult | None:
    latest: EnrichmentResult | None = None
    for result in results:
        if (
            result.enricher_id != "crossref"
            or result.status is not EnrichmentStatus.OBSERVED
        ):
            continue
        if latest is None or result.fetched_at > latest.fetched_at:
            latest = result
    return latest


def _normalize_relation_type(value: str) -> str:
    return "-".join(value.strip().casefold().replace("_", "-").split("-"))


def _kind_from_identity(identity: str) -> ArtifactKind:
    if identity.startswith("github:"):
        return ArtifactKind.CODE
    if identity.startswith("hugging_face:model:"):
        return ArtifactKind.MODEL
    if identity.startswith("hugging_face:dataset:"):
        return ArtifactKind.DATA
    raise ValueError("unsupported artifact identity")


def _unique_metrics(
    metrics: Sequence[ArtifactImpactMetric],
) -> dict[str, ArtifactImpactMetric]:
    unique: dict[str, ArtifactImpactMetric] = {}
    for metric in metrics:
        if metric.identity in unique:
            raise ValueError(f"duplicate artifact metrics for {metric.identity}")
        unique[metric.identity] = metric
    return unique


def _link_evidence(link: ExplicitArtifactLink) -> dict[str, Any]:
    return {
        "kind": link.kind.value,
        "provider": link.provider.value,
        "identity": link.identity,
        "url": link.url,
        "evidence_source": link.evidence_source.value,
        "evidence_path": link.evidence_path,
        "confidence": link.confidence,
    }


def _artifact_identity(url: str) -> tuple[ArtifactProvider, str]:
    parsed = urlsplit(url.strip())
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
    ):
        raise ValueError("artifact URL must be a canonical HTTPS URL")
    host = (parsed.hostname or "").casefold()
    segments = [segment for segment in parsed.path.split("/") if segment]
    if host == "github.com":
        return _github_identity(segments)
    if host == "huggingface.co":
        return _hugging_face_identity(segments)
    raise ValueError("artifact URL must use an approved provider")


def _github_identity(segments: list[str]) -> tuple[ArtifactProvider, str]:
    if len(segments) < 2:
        raise ValueError("artifact URL must identify a GitHub repository")
    if len(segments) > 2 and (
        len(segments) < 4 or segments[2].casefold() not in {"tree", "blob"}
    ):
        raise ValueError("artifact URL must identify a GitHub repository")
    owner = segments[0]
    repository = segments[1].removesuffix(".git")
    if not _valid_slug(owner) or not _valid_slug(repository):
        raise ValueError("artifact URL must identify a GitHub repository")
    return (
        ArtifactProvider.GITHUB,
        f"github:{owner.casefold()}/{repository.casefold()}",
    )


def _hugging_face_identity(
    segments: list[str],
) -> tuple[ArtifactProvider, str]:
    if len(segments) == 2:
        kind = "model"
        owner, name = segments
    elif len(segments) == 3 and segments[0].casefold() == "datasets":
        kind = "dataset"
        owner, name = segments[1:]
    else:
        raise ValueError("artifact URL must identify a Hugging Face model or dataset")
    if not _valid_slug(owner) or not _valid_slug(name):
        raise ValueError("artifact URL must identify a Hugging Face model or dataset")
    return (
        ArtifactProvider.HUGGING_FACE,
        f"hugging_face:{kind}:{owner.casefold()}/{name.casefold()}",
    )


def _valid_slug(value: str) -> bool:
    return (
        value not in {".", ".."}
        and re.fullmatch(r"[A-Za-z0-9_.-]+", value) is not None
    )
