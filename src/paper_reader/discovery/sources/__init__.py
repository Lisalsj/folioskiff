"""Built-in official paper source connectors."""

from paper_reader.discovery.sources.arxiv import ArxivConnector
from paper_reader.discovery.sources.europe_pmc import EuropePmcConnector
from paper_reader.discovery.sources.ieee import IeeeConnector
from paper_reader.discovery.sources.openreview import OpenReviewConnector
from paper_reader.discovery.sources.springer_nature import SpringerNatureConnector

__all__ = [
    "ArxivConnector",
    "EuropePmcConnector",
    "IeeeConnector",
    "OpenReviewConnector",
    "SpringerNatureConnector",
]
