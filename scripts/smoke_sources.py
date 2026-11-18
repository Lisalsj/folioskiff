"""Run one explicit, read-only live smoke against an official paper source."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import httpx

from paper_reader.discovery.connectors import ConnectorRequestError, SourceQuery
from paper_reader.discovery.keywords import (
    ConceptGroup,
    KeywordSetDefinition,
    compile_definition,
)
from paper_reader.discovery.source_registry import build_source_registry
from paper_reader.discovery.sources.utils import month_bounds
from paper_reader.settings import SourceCredentials

SCHEMA_VERSION = "paper-reader-source-live-smoke-v1"
SOURCE_IDS = (
    "arxiv",
    "openreview",
    "europe_pmc",
    "ieee",
    "springer_nature",
)


class LiveSmokeFailure(RuntimeError):
    """A stable live-smoke failure that is safe to serialize."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _clean_month(value: str) -> str:
    if re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value) is None:
        raise LiveSmokeFailure("invalid_month", retryable=False)
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError as error:
        raise LiveSmokeFailure("invalid_month", retryable=False) from error
    return value


def _clean_term(value: str) -> str:
    cleaned = " ".join(value.split())
    if not 1 <= len(cleaned) <= 120 or not all(character.isprintable() for character in cleaned):
        raise LiveSmokeFailure("invalid_term", retryable=False)
    return cleaned


def _query(*, source: str, month: str, term: str) -> SourceQuery:
    compiled = compile_definition(
        "live-smoke",
        KeywordSetDefinition(
            required=(
                ConceptGroup(
                    key="live-smoke",
                    label="Live smoke",
                    terms=(term,),
                ),
            )
        ),
    )
    fingerprint = hashlib.sha256(f"{source}\0{month}\0{term}".encode()).hexdigest()
    return SourceQuery(
        connector_id=source,
        months=(month,),
        compiled_keywords=compiled,
        filters={},
        fingerprint=fingerprint,
    )


async def run_live_smoke(
    *,
    source: str,
    month: str,
    term: str,
    limit: int,
    credentials: SourceCredentials,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Return minimal public evidence without serializing requests or credentials."""

    if source not in SOURCE_IDS:
        raise LiveSmokeFailure("unknown_source", retryable=False)
    if not 1 <= limit <= 10:
        raise LiveSmokeFailure("invalid_limit", retryable=False)
    clean_month = _clean_month(month)
    clean_term = _clean_term(term)
    registry = build_source_registry(client=client, credentials=credentials)
    connector = registry.get(source)
    if connector is None:
        raise LiveSmokeFailure("unknown_source", retryable=False)
    capabilities = connector.capabilities
    if not capabilities.enabled:
        raise LiveSmokeFailure(
            capabilities.disabled_reason or "source_disabled",
            retryable=False,
        )
    query = _query(source=source, month=clean_month, term=clean_term)
    try:
        estimate = await connector.estimate(query)
        page = await connector.fetch_page(query, cursor=None, limit=50)
    except ConnectorRequestError as error:
        raise LiveSmokeFailure(error.code, retryable=error.retryable) from None
    if not page.records:
        raise LiveSmokeFailure("empty_result", retryable=False)
    start, end = month_bounds(query.months)
    matched = [
        record
        for record in page.records
        if record.updated_at is not None
        and start <= record.updated_at.date().isoformat() <= end
        and query.compiled_keywords.matches(
            title=record.title,
            abstract=record.abstract,
        )
    ][:limit]
    if not matched:
        raise LiveSmokeFailure("empty_local_match", retryable=False)
    records = [
        {
            "external_id": record.external_id,
            "doi": record.doi,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }
        for record in matched
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "source": source,
        "month": clean_month,
        "term": clean_term,
        "credential_configured": capabilities.credential_configured,
        "estimate_total": estimate.total,
        "estimate_exact": estimate.exact,
        "warnings": list(estimate.warnings),
        "remote_record_count": len(page.records),
        "local_filter_applied": True,
        "record_count": len(records),
        "records": records,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a read-only live smoke against one official paper source.",
    )
    parser.add_argument("--source", required=True, choices=SOURCE_IDS)
    parser.add_argument("--month", required=True, help="One calendar month in YYYY-MM form.")
    parser.add_argument("--term", required=True, help="One plain research term.")
    parser.add_argument("--limit", type=int, default=3, help="Sample size from 1 to 10.")
    return parser


async def _run_from_arguments(arguments: argparse.Namespace) -> dict[str, Any]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30, connect=10),
        follow_redirects=True,
    ) as client:
        return await run_live_smoke(
            source=str(arguments.source),
            month=str(arguments.month),
            term=str(arguments.term),
            limit=int(arguments.limit),
            credentials=SourceCredentials.from_environment(),
            client=client,
        )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        evidence = asyncio.run(_run_from_arguments(arguments))
        exit_code = 0
    except LiveSmokeFailure as failure:
        evidence = {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "source": str(arguments.source),
            "month": str(arguments.month),
            "error_code": failure.code,
            "retryable": failure.retryable,
        }
        exit_code = 1
    except Exception:
        evidence = {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "source": str(arguments.source),
            "month": str(arguments.month),
            "error_code": "unexpected_failure",
            "retryable": False,
        }
        exit_code = 1
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
