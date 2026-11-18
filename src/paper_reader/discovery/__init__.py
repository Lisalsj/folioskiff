"""Discovery domain: source ingestion, canonical paper identity, and research plans."""

from paper_reader.discovery.identity import (
    DiscoveryKind,
    IdentityRepository,
    MatchKind,
    SourcePaper,
)
from paper_reader.discovery.paper_updates import PaperUpdateRepository, PaperUpdateState

__all__ = [
    "DiscoveryKind",
    "IdentityRepository",
    "MatchKind",
    "PaperUpdateRepository",
    "PaperUpdateState",
    "SourcePaper",
]
