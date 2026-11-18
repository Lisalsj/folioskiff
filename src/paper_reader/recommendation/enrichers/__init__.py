"""Built-in official metadata enrichers."""

from paper_reader.recommendation.enrichers.crossref import CrossrefEnricher
from paper_reader.recommendation.enrichers.openalex import OpenAlexEnricher
from paper_reader.recommendation.enrichers.registry import EnricherRegistry
from paper_reader.recommendation.enrichers.semantic_scholar import SemanticScholarEnricher

__all__ = [
    "CrossrefEnricher",
    "EnricherRegistry",
    "OpenAlexEnricher",
    "SemanticScholarEnricher",
]
