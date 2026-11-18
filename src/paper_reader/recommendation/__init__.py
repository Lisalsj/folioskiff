"""Deterministic retrieval, scoring, ranking, and preference services."""

from paper_reader.recommendation.artifact_impact import (
    ArtifactEvidenceSource,
    ArtifactImpactMetric,
    ArtifactKind,
    ArtifactProvider,
    ExplicitArtifactLink,
    extract_explicit_artifact_links,
    score_code_data_impact,
)
from paper_reader.recommendation.ccf import CCFCatalog, CCFEntry, ccf_rating_evidence
from paper_reader.recommendation.enrichment import (
    EnricherCapabilities,
    EnrichmentResult,
    MetadataEnricher,
    WorkLookup,
)
from paper_reader.recommendation.enrichment_service import (
    MetadataEnrichmentService,
    WorkLookupRepository,
)
from paper_reader.recommendation.evidence_builder import (
    QualityEvidence,
    QualityEvidenceBuilder,
)
from paper_reader.recommendation.integrity import (
    IntegrityAssessment,
    IntegrityStatus,
    assess_integrity,
)
from paper_reader.recommendation.lexical import LexicalHit, LexicalIndex
from paper_reader.recommendation.observations import (
    MetadataObservationRepository,
    StoredEnrichment,
)
from paper_reader.recommendation.orchestration import (
    DynamicRunRelevanceScorer,
    RecommendationOrchestrator,
)
from paper_reader.recommendation.preferences import (
    AnalysisState,
    ImplicitEventInput,
    ImplicitEventType,
    InterestedAuthor,
    LibraryFeedback,
    LibraryState,
    LibraryStateInput,
    PreferenceAggregationResult,
    PreferenceRepository,
    PreferenceScore,
    ReadingDurationBucket,
    score_preference,
)
from paper_reader.recommendation.quality import QualityMetrics, QualityScore, score_quality
from paper_reader.recommendation.ranking import (
    CompositeScore,
    ExplorationReason,
    RankedCandidate,
    RankingCandidate,
    RankingSignal,
    RankingView,
    RecommendationBatch,
    RecommendationBatchItem,
    RecommendationChannel,
    build_recommendation_batch,
    rank_candidates,
    score_composite,
)
from paper_reader.recommendation.rating_catalogs import (
    RatingCatalog,
    RatingCatalogKind,
    RatingCatalogRepository,
    VenueRating,
)
from paper_reader.recommendation.relevance import MetricEvidence, WeightedScore, score_relevance
from paper_reader.recommendation.results import (
    CandidateSnapshotRefs,
    RecommendationSet,
    RecommendationSetRepository,
    RecommendationViewResult,
)
from paper_reader.recommendation.score_rules import (
    QualityDimensionSpec,
    ScoreRuleDimension,
    ScoreRuleDimensionInput,
    ScoreRuleRepository,
    ScoreRuleVersion,
    default_quality_dimensions,
    validate_quality_dimensions,
)
from paper_reader.recommendation.score_snapshots import ScoreSnapshot, ScoreSnapshotRepository
from paper_reader.recommendation.semantic import SemanticHit, SemanticIndex, VectorCapability
from paper_reader.recommendation.service import (
    CandidateRelevance,
    RelevanceService,
    RunRelevanceResult,
)
from paper_reader.recommendation.venue_rating import VenueRatingService

__all__ = [
    "AnalysisState",
    "ArtifactEvidenceSource",
    "ArtifactImpactMetric",
    "ArtifactKind",
    "ArtifactProvider",
    "CCFCatalog",
    "CCFEntry",
    "CandidateRelevance",
    "CompositeScore",
    "CandidateSnapshotRefs",
    "DynamicRunRelevanceScorer",
    "EnricherCapabilities",
    "EnrichmentResult",
    "ExplicitArtifactLink",
    "ExplorationReason",
    "IntegrityAssessment",
    "IntegrityStatus",
    "ImplicitEventInput",
    "ImplicitEventType",
    "InterestedAuthor",
    "LexicalHit",
    "LexicalIndex",
    "LibraryFeedback",
    "LibraryState",
    "LibraryStateInput",
    "MetricEvidence",
    "MetadataEnricher",
    "MetadataEnrichmentService",
    "MetadataObservationRepository",
    "PreferenceAggregationResult",
    "PreferenceRepository",
    "PreferenceScore",
    "QualityMetrics",
    "QualityEvidence",
    "QualityEvidenceBuilder",
    "QualityScore",
    "QualityDimensionSpec",
    "RankedCandidate",
    "RankingCandidate",
    "RankingSignal",
    "RankingView",
    "RatingCatalog",
    "RatingCatalogKind",
    "RatingCatalogRepository",
    "ReadingDurationBucket",
    "RelevanceService",
    "RecommendationBatch",
    "RecommendationBatchItem",
    "RecommendationChannel",
    "RecommendationOrchestrator",
    "RecommendationSet",
    "RecommendationSetRepository",
    "RecommendationViewResult",
    "RunRelevanceResult",
    "SemanticHit",
    "SemanticIndex",
    "StoredEnrichment",
    "ScoreSnapshot",
    "ScoreSnapshotRepository",
    "ScoreRuleDimension",
    "ScoreRuleDimensionInput",
    "ScoreRuleRepository",
    "ScoreRuleVersion",
    "VectorCapability",
    "VenueRating",
    "VenueRatingService",
    "WeightedScore",
    "WorkLookup",
    "WorkLookupRepository",
    "ccf_rating_evidence",
    "default_quality_dimensions",
    "assess_integrity",
    "build_recommendation_batch",
    "extract_explicit_artifact_links",
    "rank_candidates",
    "score_code_data_impact",
    "score_composite",
    "score_relevance",
    "score_quality",
    "score_preference",
    "validate_quality_dimensions",
]
