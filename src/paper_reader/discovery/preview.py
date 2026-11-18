"""Read-only per-source estimate and sample preview for a discovery plan."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from paper_reader.discovery.connectors import (
    ConnectorRegistry,
    ConnectorRequestError,
    SourceQuery,
)
from paper_reader.discovery.filtering import filter_source_records
from paper_reader.discovery.identity import SourcePaper
from paper_reader.discovery.keywords import CompiledKeywordQuery, KeywordRepository
from paper_reader.discovery.plans import DiscoveryPlan, DiscoveryPlanRepository, ResolvedDateRange


@dataclass(frozen=True, slots=True)
class SourcePreview:
    connector_id: str
    display_name: str
    status: str
    estimate: int | None = None
    estimate_exact: bool = False
    samples: tuple[SourcePaper, ...] = ()
    keyword_impact: dict[str, int] | None = None
    warnings: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    setup_url: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryPreview:
    plan_id: str
    keyword_version_id: str
    months: tuple[str, ...]
    warnings: tuple[str, ...]
    sources: tuple[SourcePreview, ...]


class DiscoveryPreviewService:
    def __init__(
        self,
        *,
        plans: DiscoveryPlanRepository,
        keywords: KeywordRepository,
        connectors: ConnectorRegistry,
    ) -> None:
        self._plans = plans
        self._keywords = keywords
        self._connectors = connectors

    async def preview(
        self,
        plan_id: str,
        *,
        now: datetime | None = None,
    ) -> DiscoveryPreview:
        plan = self._plans.get(plan_id)
        return await self.preview_plan(plan, now=now)

    async def preview_plan(
        self,
        plan: DiscoveryPlan,
        *,
        now: datetime | None = None,
    ) -> DiscoveryPreview:
        resolved = plan.date_range.resolve(now or datetime.now(UTC))
        compiled = self._keywords.compile(plan.keyword_version_id)
        sources = await asyncio.gather(
            *(
                self._preview_source(
                    plan=plan,
                    resolved=resolved,
                    connector_id=connector_id,
                    compiled=compiled,
                )
                for connector_id in plan.sources
            )
        )
        return DiscoveryPreview(
            plan_id=plan.id,
            keyword_version_id=plan.keyword_version_id,
            months=resolved.months,
            warnings=resolved.warnings,
            sources=tuple(sources),
        )

    async def _preview_source(
        self,
        *,
        plan: DiscoveryPlan,
        resolved: ResolvedDateRange,
        connector_id: str,
        compiled: CompiledKeywordQuery,
    ) -> SourcePreview:
        connector = self._connectors.get(connector_id)
        if connector is None:
            return SourcePreview(
                connector_id=connector_id,
                display_name=connector_id,
                status="disabled",
                error_code="connector_not_installed",
            )
        capabilities = connector.capabilities
        if not capabilities.enabled:
            return SourcePreview(
                connector_id=connector_id,
                display_name=capabilities.display_name,
                status="disabled",
                error_code=capabilities.disabled_reason or "connector_disabled",
                setup_url=capabilities.setup_url,
            )

        query = SourceQuery(
            connector_id=connector_id,
            months=resolved.months,
            compiled_keywords=compiled,
            filters=dict(plan.source_filters.get(connector_id, {})),
            fingerprint=self._fingerprint(plan, connector_id, resolved, compiled),
        )
        try:
            estimate = await connector.estimate(query)
            page = await connector.fetch_page(query, cursor=None, limit=50)
        except ConnectorRequestError as error:
            return SourcePreview(
                connector_id=connector_id,
                display_name=capabilities.display_name,
                status="error",
                error_code=error.code,
                error_message=str(error),
                retryable=error.retryable,
            )
        except Exception as error:
            return SourcePreview(
                connector_id=connector_id,
                display_name=capabilities.display_name,
                status="error",
                error_code="connector_internal_error",
                error_message=type(error).__name__,
                retryable=False,
            )

        matched, counts = filter_source_records(page.records, resolved=resolved, compiled=compiled)
        warnings = [*estimate.warnings]
        if counts.missing_updated_at_count:
            warnings.append(f"missing_updated_at:{counts.missing_updated_at_count}")
        if not capabilities.supports_exact_updated_date:
            warnings.append("source_date_filter_is_approximate")
        return SourcePreview(
            connector_id=connector_id,
            display_name=capabilities.display_name,
            status="ready",
            estimate=estimate.total,
            estimate_exact=estimate.exact,
            samples=matched[:10],
            keyword_impact={
                "remote_candidates": len(page.records),
                "in_date_range": counts.in_date_range_count,
                "matched_confirmed_keywords": len(matched),
            },
            warnings=tuple(warnings),
        )

    @staticmethod
    def _fingerprint(
        plan: DiscoveryPlan,
        connector_id: str,
        resolved: ResolvedDateRange,
        compiled: CompiledKeywordQuery,
    ) -> str:
        payload = json.dumps(
            {
                "plan_id": plan.id,
                "connector_id": connector_id,
                "months": resolved.months,
                "keyword_version_id": compiled.version_id,
                "expression": compiled.boolean_expression,
                "filters": plan.source_filters.get(connector_id, {}),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
