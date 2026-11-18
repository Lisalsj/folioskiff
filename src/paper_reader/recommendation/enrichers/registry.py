"""Runtime registry for built-in and future metadata enrichers."""

from __future__ import annotations

import httpx

from paper_reader.recommendation.enrichers.crossref import CrossrefEnricher
from paper_reader.recommendation.enrichers.openalex import OpenAlexEnricher
from paper_reader.recommendation.enrichers.semantic_scholar import SemanticScholarEnricher
from paper_reader.recommendation.enrichment import MetadataEnricher
from paper_reader.settings import SourceCredentials


class EnricherRegistry:
    def __init__(self, enrichers: tuple[MetadataEnricher, ...] = ()) -> None:
        self._items: dict[str, MetadataEnricher] = {}
        for enricher in enrichers:
            self.register(enricher)
        self._builtin_items = dict(self._items)

    def register(self, enricher: MetadataEnricher) -> None:
        enricher_id = enricher.capabilities.enricher_id
        if enricher_id in self._items:
            raise ValueError(f"Duplicate metadata enricher: {enricher_id}")
        self._items[enricher_id] = enricher

    def replace_extensions(
        self,
        enrichers: tuple[MetadataEnricher, ...],
    ) -> tuple[str, ...]:
        extensions: dict[str, MetadataEnricher] = {}
        conflicts: list[str] = []
        for enricher in enrichers:
            enricher_id = enricher.capabilities.enricher_id
            if enricher_id in self._builtin_items or enricher_id in extensions:
                conflicts.append(enricher_id)
                continue
            extensions[enricher_id] = enricher
        self._items = {**self._builtin_items, **extensions}
        return tuple(sorted(conflicts))

    def replace_builtins(
        self,
        enrichers: tuple[MetadataEnricher, ...],
    ) -> tuple[str, ...]:
        replacements: dict[str, MetadataEnricher] = {}
        for enricher in enrichers:
            enricher_id = enricher.capabilities.enricher_id
            if enricher_id in replacements:
                raise ValueError(f"Duplicate metadata enricher: {enricher_id}")
            replacements[enricher_id] = enricher
        extensions = {
            enricher_id: enricher
            for enricher_id, enricher in self._items.items()
            if enricher_id not in self._builtin_items
        }
        conflicts = tuple(sorted(set(replacements) & set(extensions)))
        self._builtin_items = replacements
        self._items = {
            **replacements,
            **{key: value for key, value in extensions.items() if key not in replacements},
        }
        return conflicts

    def get(self, enricher_id: str) -> MetadataEnricher | None:
        return self._items.get(enricher_id)

    def all(self) -> tuple[MetadataEnricher, ...]:
        return tuple(self._items.values())


def build_enricher_registry(
    *,
    client: httpx.AsyncClient,
    credentials: SourceCredentials,
) -> EnricherRegistry:
    return EnricherRegistry(
        (
            OpenAlexEnricher(client=client, api_key=credentials.openalex_api_key),
            SemanticScholarEnricher(
                client=client,
                api_key=credentials.semantic_scholar_api_key,
            ),
            CrossrefEnricher(
                client=client,
                contact_email=credentials.crossref_contact_email,
                plus_api_token=credentials.crossref_plus_api_token,
            ),
        )
    )
