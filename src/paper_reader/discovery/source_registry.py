"""Composition root for the five built-in official paper sources."""

from __future__ import annotations

import httpx

from paper_reader.discovery.connectors import ConnectorRegistry
from paper_reader.discovery.sources import (
    ArxivConnector,
    EuropePmcConnector,
    IeeeConnector,
    OpenReviewConnector,
    SpringerNatureConnector,
)
from paper_reader.settings import SourceCredentials


def build_source_registry(
    *,
    client: httpx.AsyncClient,
    credentials: SourceCredentials,
) -> ConnectorRegistry:
    return ConnectorRegistry(
        (
            ArxivConnector(client=client),
            OpenReviewConnector(
                client=client,
                access_token=credentials.openreview_access_token,
            ),
            EuropePmcConnector(client=client),
            IeeeConnector(client=client, api_key=credentials.ieee_api_key),
            SpringerNatureConnector(
                client=client,
                api_key=credentials.springer_nature_api_key,
            ),
        )
    )
