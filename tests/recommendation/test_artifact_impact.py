from __future__ import annotations

import pytest

from tests.recommendation.test_integrity_rules import observed_result


def test_unlinked_popularity_metrics_are_ignored_not_scored() -> None:
    from paper_reader.recommendation.artifact_impact import (
        ArtifactImpactMetric,
        score_code_data_impact,
    )
    from paper_reader.recommendation.relevance import EvidenceStatus

    result = score_code_data_impact(
        links=(),
        metrics=(
            ArtifactImpactMetric(
                url="https://github.com/famous-lab/paper-reader",
                impact_score=99,
                metrics={"stars": 100_000, "active": True},
            ),
        ),
    )

    assert result.status is EvidenceStatus.UNKNOWN
    assert result.reason == "explicit_artifact_link_missing"
    assert result.evidence["rule_version"] == "artifact-impact-v1"
    assert result.evidence["ignored_metric_count"] == 1


def test_only_metric_for_the_explicitly_linked_repository_is_scored() -> None:
    from paper_reader.recommendation.artifact_impact import (
        ArtifactEvidenceSource,
        ArtifactImpactMetric,
        ArtifactKind,
        ExplicitArtifactLink,
        score_code_data_impact,
    )
    from paper_reader.recommendation.relevance import EvidenceStatus

    result = score_code_data_impact(
        links=(
            ExplicitArtifactLink(
                kind=ArtifactKind.CODE,
                url="https://github.com/reliable-lab/reader-agent#readme",
                evidence_source=ArtifactEvidenceSource.PAPER_CONTENT,
                evidence_path="page:1:url:2",
                confidence=90,
            ),
        ),
        metrics=(
            ArtifactImpactMetric(
                url="https://github.com/reliable-lab/reader-agent/tree/main",
                impact_score=82,
                metrics={"stars": 4200, "active": True},
            ),
            ArtifactImpactMetric(
                url="https://github.com/reliable-lab/reader-agents",
                impact_score=100,
                metrics={"stars": 200_000, "active": True},
            ),
        ),
    )

    assert result.status is EvidenceStatus.OBSERVED
    assert result.value == 82
    assert result.confidence == 90
    assert result.evidence["aggregation"] == "mean_linked_artifact_impact"
    assert result.evidence["matched_artifacts"] == [
        {
            "kind": "code",
            "provider": "github",
            "identity": "github:reliable-lab/reader-agent",
            "url": "https://github.com/reliable-lab/reader-agent#readme",
            "evidence_source": "paper_content",
            "evidence_path": "page:1:url:2",
            "impact_score": 82.0,
            "metrics": {"stars": 4200, "active": True},
        }
    ]
    assert result.evidence["ignored_metric_count"] == 1


def test_hugging_face_model_metrics_require_the_same_explicit_model_link() -> None:
    from paper_reader.recommendation.artifact_impact import (
        ArtifactEvidenceSource,
        ArtifactImpactMetric,
        ArtifactKind,
        ExplicitArtifactLink,
        score_code_data_impact,
    )

    result = score_code_data_impact(
        links=(
            ExplicitArtifactLink(
                kind=ArtifactKind.MODEL,
                url="https://huggingface.co/reliable-lab/reader-e5",
                evidence_source=ArtifactEvidenceSource.PUBLISHER_METADATA,
                evidence_path="relations[0]",
                confidence=80,
            ),
        ),
        metrics=(
            ArtifactImpactMetric(
                url="https://huggingface.co/reliable-lab/reader-e5",
                impact_score=76,
                metrics={"downloads": 120_000, "likes": 800},
            ),
        ),
    )

    assert result.value == 76
    assert result.confidence == 80
    assert result.evidence["matched_artifacts"][0]["provider"] == "hugging_face"


def test_explicit_link_without_collected_metrics_stays_unknown() -> None:
    from paper_reader.recommendation.artifact_impact import (
        ArtifactEvidenceSource,
        ArtifactKind,
        ExplicitArtifactLink,
        score_code_data_impact,
    )
    from paper_reader.recommendation.relevance import EvidenceStatus

    result = score_code_data_impact(
        links=(
            ExplicitArtifactLink(
                kind=ArtifactKind.DATA,
                url="https://huggingface.co/datasets/reliable-lab/reader-data",
                evidence_source=ArtifactEvidenceSource.USER_CONFIRMED,
                evidence_path="library:artifact:1",
            ),
        ),
        metrics=(),
    )

    assert result.status is EvidenceStatus.UNKNOWN
    assert result.reason == "artifact_impact_metrics_missing"
    assert result.evidence["linked_artifact_count"] == 1


@pytest.mark.parametrize(
    "url",
    (
        "https://example.org/reliable-lab/reader-agent",
        "http://github.com/reliable-lab/reader-agent",
        "https://user@github.com/reliable-lab/reader-agent",
        "https://github.com:8443/reliable-lab/reader-agent",
        "https://github.com/reliable-lab/reader-agent?tab=readme",
        "https://github.com/reliable-lab",
        "https://github.com/reliable-lab/reader-agent/issues/1",
        "https://github.com/reliable lab/reader-agent",
        "https://huggingface.co/datasets/reliable-lab",
        "https://huggingface.co/datasets/reliable lab/reader-data",
    ),
)
def test_untrusted_or_noncanonical_artifact_links_are_rejected(url: str) -> None:
    from paper_reader.recommendation.artifact_impact import (
        ArtifactEvidenceSource,
        ArtifactKind,
        ExplicitArtifactLink,
    )

    with pytest.raises(ValueError, match="artifact URL"):
        ExplicitArtifactLink(
            kind=ArtifactKind.CODE,
            url=url,
            evidence_source=ArtifactEvidenceSource.SOURCE_RECORD,
            evidence_path="metadata:code",
        )


def test_agent_generated_text_cannot_be_declared_as_link_evidence() -> None:
    from paper_reader.recommendation.artifact_impact import ArtifactEvidenceSource

    with pytest.raises(ValueError):
        ArtifactEvidenceSource("agent_report")


def test_artifact_contract_rejects_wrong_kind_blank_path_and_invalid_scores() -> None:
    from paper_reader.recommendation.artifact_impact import (
        ArtifactEvidenceSource,
        ArtifactImpactMetric,
        ArtifactKind,
        ExplicitArtifactLink,
    )

    cases = (
        (ArtifactKind.CODE, "https://huggingface.co/lab/model"),
        (ArtifactKind.MODEL, "https://github.com/lab/model"),
        (ArtifactKind.DATA, "https://huggingface.co/lab/model"),
    )
    for kind, url in cases:
        with pytest.raises(ValueError, match="artifact URL"):
            ExplicitArtifactLink(
                kind=kind,
                url=url,
                evidence_source=ArtifactEvidenceSource.SOURCE_RECORD,
                evidence_path="metadata:artifact",
            )
    with pytest.raises(ValueError, match="evidence_path"):
        ExplicitArtifactLink(
            kind=ArtifactKind.CODE,
            url="https://github.com/lab/repo",
            evidence_source=ArtifactEvidenceSource.SOURCE_RECORD,
            evidence_path=" ",
        )
    with pytest.raises(ValueError, match="confidence"):
        ExplicitArtifactLink(
            kind=ArtifactKind.CODE,
            url="https://github.com/lab/repo",
            evidence_source=ArtifactEvidenceSource.SOURCE_RECORD,
            evidence_path="metadata:artifact",
            confidence=float("nan"),
        )
    with pytest.raises(ValueError, match="impact_score"):
        ArtifactImpactMetric(
            url="https://github.com/lab/repo",
            impact_score=101,
            metrics={},
        )


def test_duplicate_metrics_are_rejected_and_duplicate_links_keep_best_evidence() -> None:
    from paper_reader.recommendation.artifact_impact import (
        ArtifactEvidenceSource,
        ArtifactImpactMetric,
        ArtifactKind,
        ExplicitArtifactLink,
        score_code_data_impact,
    )

    metric = ArtifactImpactMetric(
        url="https://github.com/lab/repo",
        impact_score=60,
        metrics={"stars": 50},
    )
    low = ExplicitArtifactLink(
        kind=ArtifactKind.CODE,
        url="https://github.com/lab/repo",
        evidence_source=ArtifactEvidenceSource.SOURCE_RECORD,
        evidence_path="metadata:code",
        confidence=60,
    )
    high = ExplicitArtifactLink(
        kind=ArtifactKind.CODE,
        url="https://github.com/lab/repo.git",
        evidence_source=ArtifactEvidenceSource.USER_CONFIRMED,
        evidence_path="library:artifact:1",
        confidence=95,
    )

    result = score_code_data_impact(links=(low, high), metrics=(metric,))
    assert result.confidence == 95
    assert result.evidence["matched_artifacts"][0]["evidence_source"] == "user_confirmed"

    with pytest.raises(ValueError, match="duplicate artifact metrics"):
        score_code_data_impact(links=(high,), metrics=(metric, metric))


def test_link_extraction_without_usable_crossref_relations_is_empty() -> None:
    from paper_reader.recommendation.artifact_impact import extract_explicit_artifact_links

    assert extract_explicit_artifact_links(()) == ()
    assert extract_explicit_artifact_links(
        (observed_result("semantic_scholar", {"relations": {}}),)
    ) == ()
    assert extract_explicit_artifact_links(
        (observed_result("crossref", {"relations": "invalid"}),)
    ) == ()


def test_only_explicit_subject_asserted_crossref_relations_create_links() -> None:
    from paper_reader.recommendation.artifact_impact import (
        ArtifactEvidenceSource,
        ArtifactKind,
        extract_explicit_artifact_links,
    )

    links = extract_explicit_artifact_links(
        (
            observed_result(
                "crossref",
                {
                    "relations": {
                        "is-supplemented-by": [
                            {
                                "id-type": "uri",
                                "id": "https://github.com/reliable-lab/reader-agent",
                                "asserted-by": "subject",
                            },
                            {
                                "id-type": "uri",
                                "id": "https://huggingface.co/datasets/reliable-lab/reader-data",
                                "asserted-by": "subject",
                            },
                            {
                                "id-type": "uri",
                                "id": "https://huggingface.co/datasets/reliable-lab/private-data",
                                "asserted-by": "object",
                            },
                            {
                                "id-type": "doi",
                                "id": "https://github.com/unrelated/doi-is-not-a-uri",
                                "asserted-by": "subject",
                            },
                            "invalid-row",
                            {
                                "id-type": "uri",
                                "id": "https://example.org/not-an-artifact",
                                "asserted-by": "subject",
                            },
                        ],
                        "references": [
                            {
                                "id-type": "uri",
                                "id": "https://huggingface.co/reliable-lab/reader-e5",
                                "asserted-by": "subject",
                            }
                        ],
                        "is-preprint-of": [
                            {
                                "id-type": "uri",
                                "id": "https://github.com/unrelated/popular-repo",
                                "asserted-by": "subject",
                            }
                        ],
                    }
                },
            ),
        )
    )

    assert len(links) == 3
    assert links[0].kind is ArtifactKind.CODE
    assert links[0].identity == "github:reliable-lab/reader-agent"
    assert links[0].evidence_source is ArtifactEvidenceSource.CROSSREF_RELATION
    assert links[0].evidence_path == "crossref:relations.is-supplemented-by[0]"
    assert links[0].confidence == 85
    assert links[1].kind is ArtifactKind.DATA
    assert links[1].identity == "hugging_face:dataset:reliable-lab/reader-data"
    assert links[2].kind is ArtifactKind.MODEL
    assert links[2].identity == "hugging_face:model:reliable-lab/reader-e5"
