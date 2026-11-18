"""Evidence-bound multi-paper comparison orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal, cast

from pydantic import ValidationError

from paper_reader.agents.comparisons import (
    ComparisonEvidenceError,
    ComparisonItemSnapshot,
    ComparisonPaperExtraction,
    ComparisonProvenance,
    ComparisonRepository,
    StoredComparisonReport,
    StructuredComparisonReport,
    comparison_extraction_json_schema,
    comparison_key,
    comparison_report_json_schema,
    compose_batch_comparison_prompt,
    compose_direct_comparison_prompt,
    compose_final_comparison_prompt,
    compose_paper_extraction_prompt,
    validate_comparison_report,
    validate_paper_extraction,
)
from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentErrorCode,
    AgentFragment,
    AgentLocation,
    AgentRequest,
    AgentResult,
    AgentTaskKind,
    ReasoningEffort,
    ServiceTier,
)
from paper_reader.agents.evidence import (
    EvidenceBundle,
    EvidenceBundleBuilder,
    EvidenceUnavailableError,
    canonical_json,
)
from paper_reader.agents.preflight import (
    ComparisonNotReadyError,
    ComparisonPreflight,
    ComparisonPreflightItem,
    ComparisonSelectionConflict,
    PreflightModel,
    comparison_request_fingerprint,
    discover_model_catalog,
    model_selection_status,
)
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.agents.selections import (
    ComparisonJobCommand,
    ComparisonSelection,
)
from paper_reader.agents.templates import AgentSettingsRepository
from paper_reader.jobs.models import JobRecord
from paper_reader.jobs.repository import JobRepository

_COMPARISON_JOB_SCHEMA_VERSION = "comparison-generate-job-v1"
_TERMINAL_JOB_STATES = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class _PreparedComparisonItem:
    position: int
    requested_work_id: str
    work_id: str
    title: str
    evidence: EvidenceBundle | None
    error: str | None


def _fingerprint_items(
    items: tuple[_PreparedComparisonItem, ...] | list[_PreparedComparisonItem],
) -> tuple[tuple[str, str | None, str | None], ...]:
    return tuple(
        (
            item.work_id,
            (
                item.evidence.document.document_version_id
                if item.evidence is not None and item.evidence.document is not None
                else None
            ),
            item.evidence.evidence_sha256 if item.evidence is not None else None,
        )
        for item in items
    )


@dataclass(frozen=True, slots=True)
class _PreparedComparison:
    preflight: ComparisonPreflight
    items: tuple[_PreparedComparisonItem, ...]
    root_prompt: str


class ComparisonService:
    """Run explicit direct or staged comparisons without mutating paper state."""

    def __init__(
        self,
        *,
        jobs: JobRepository,
        comparisons: ComparisonRepository,
        settings: AgentSettingsRepository,
        evidence: EvidenceBundleBuilder,
        backends: AgentBackendRegistry,
    ) -> None:
        self.jobs = jobs
        self.comparisons = comparisons
        self.settings = settings
        self.evidence = evidence
        self.backends = backends

    async def preflight(self, selection: ComparisonSelection) -> ComparisonPreflight:
        return (await self._prepare(selection)).preflight

    async def create_job(
        self,
        command: ComparisonJobCommand,
        *,
        idempotency_key: str,
    ) -> JobRecord:
        key = idempotency_key.strip()
        if not key or len(key) > 160:
            raise ValueError("idempotency_key must contain 1-160 characters")
        existing = self.jobs.get_by_idempotency_key(key)
        if existing is not None:
            if (
                existing.kind == "comparison.generate"
                and existing.payload.get("preflight_fingerprint") == command.preflight_fingerprint
            ):
                return existing
            raise ComparisonSelectionConflict(
                "idempotency key is bound to another comparison request"
            )

        prepared = await self._prepare(command.selection)
        if prepared.preflight.preflight_fingerprint != command.preflight_fingerprint:
            raise ComparisonSelectionConflict(
                "Comparison evidence or settings changed after preflight"
            )
        if not prepared.preflight.ready:
            raise ComparisonNotReadyError(
                "At least two usable papers and one ready Agent backend are required"
            )
        preflight = prepared.preflight
        payload_items: list[dict[str, Any]] = []
        for item in prepared.items:
            payload_items.append(
                {
                    "position": item.position,
                    "requested_work_id": item.requested_work_id,
                    "work_id": item.work_id,
                    "title": item.title,
                    "evidence": (
                        item.evidence.model_dump(mode="json") if item.evidence is not None else None
                    ),
                    "error": item.error,
                }
            )
        return self.jobs.create(
            kind="comparison.generate",
            payload={
                "schema_version": _COMPARISON_JOB_SCHEMA_VERSION,
                "preflight_fingerprint": preflight.preflight_fingerprint,
                "comparison_key": preflight.comparison_key,
                "mode": preflight.mode,
                "backend_id": preflight.backend_id,
                "model_id": preflight.model_id,
                "reasoning_effort": preflight.reasoning_effort.value,
                "service_tier": preflight.service_tier.value,
                "language": preflight.language,
                "user_instruction": command.selection.user_instruction,
                "root_prompt": prepared.root_prompt,
                "prompt_sha256": preflight.prompt_sha256,
                "items": payload_items,
            },
            idempotency_key=key,
        )

    def resumable_job_ids(self) -> tuple[str, ...]:
        return self.jobs.list_resumable_ids(kind="comparison.generate")

    async def execute(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.kind != "comparison.generate":
            raise ValueError("Job is not a comparison.generate Job")
        if job.status in _TERMINAL_JOB_STATES:
            return
        existing = self.comparisons.get_for_job(job_id)
        if existing is not None:
            self._finish_success(job_id, existing)
            return
        if job.cancel_requested:
            self._finish_cancelled(job_id)
            return

        self.jobs.update_state(job_id, status="running", progress=3)
        self.jobs.append_event(
            job_id=job_id,
            event_type="comparison.generate.started",
            progress=3,
            payload={"mode": job.payload.get("mode")},
        )
        try:
            payload = self._validated_payload(job.payload)
            items = self._payload_items(payload)
            self._validate_frozen_request(payload, items)
            if payload["mode"] == "direct":
                report = await self._execute_direct(job_id, payload, items)
            else:
                report = await self._execute_staged(job_id, payload, items)
        except AgentBackendError as error:
            if error.code is AgentErrorCode.CANCELLED or self._cancel_requested(job_id):
                self._finish_cancelled(job_id)
                return
            self._finish_failure(
                job_id,
                error_code=error.code.value,
                error_message=str(error),
            )
            return
        except (ValidationError, ComparisonEvidenceError):
            self._finish_failure(
                job_id,
                error_code="invalid_output",
                error_message="Comparison output failed its evidence contract.",
            )
            return
        except Exception:
            self._finish_failure(
                job_id,
                error_code="comparison_internal_error",
                error_message="Comparison execution failed safely; earlier versions are unchanged.",
            )
            return
        if self._cancel_requested(job_id):
            self._finish_cancelled(job_id)
            return
        self._finish_success(job_id, report)

    async def _prepare(self, selection: ComparisonSelection) -> _PreparedComparison:
        prepared_items: list[_PreparedComparisonItem] = []
        canonical_ids: set[str] = set()
        for position, selected in enumerate(selection.items):
            canonical_id = self.comparisons.canonical_work_id(selected.work_id)
            if canonical_id in canonical_ids:
                raise ComparisonSelectionConflict("Comparison contains a duplicate canonical Work")
            canonical_ids.add(canonical_id)
            title = self.comparisons.work_title(canonical_id)
            try:
                evidence = self.evidence.build(
                    canonical_id,
                    document_version_id=selected.document_version_id,
                )
                error = None
            except EvidenceUnavailableError as exception:
                evidence = None
                error = str(exception)
            prepared_items.append(
                _PreparedComparisonItem(
                    position=position,
                    requested_work_id=selected.work_id,
                    work_id=canonical_id,
                    title=title,
                    evidence=evidence,
                    error=error,
                )
            )

        settings = self.settings.get()
        backend_id = selection.backend_id or settings.default_backend_id
        try:
            backend = self.backends.require(backend_id)
        except KeyError as error:
            raise ComparisonNotReadyError("Unknown Agent backend") from error
        capability = backend.capability
        warnings: list[str] = list(capability.degradation_reasons)
        catalog, catalog_warning = await discover_model_catalog(self.backends, backend_id)
        warnings.extend(catalog.degradation_reasons)
        if catalog_warning is not None:
            warnings.append(catalog_warning)
        model_id = selection.model_id or settings.comparison_model_id
        model_ready, model_warnings = model_selection_status(catalog, model_id)
        warnings.extend(model_warnings)
        backend_ready = all(
            (
                capability.installed,
                capability.authenticated,
                capability.available,
                capability.supports_schema,
            )
        )
        if not backend_ready:
            warnings.append("backend_unavailable")

        available = tuple(item.evidence for item in prepared_items if item.evidence is not None)
        if len(available) < len(prepared_items):
            warnings.append("partial_evidence")
        if len(available) < 2:
            warnings.append("fewer_than_two_usable_papers")
        mode: Literal["direct", "staged"] = "direct" if len(prepared_items) <= 10 else "staged"
        if mode == "staged":
            warnings.append("staged_comparison")
        language = cast(
            Literal["zh-CN", "en", "bilingual"],
            selection.language or settings.report_language,
        )
        reasoning = selection.reasoning_effort or ReasoningEffort(settings.comparison_reasoning)
        tier = selection.service_tier or ServiceTier(settings.service_tier)
        if mode == "direct":
            root_prompt = compose_direct_comparison_prompt(
                available,
                language=language,
                user_instruction=selection.user_instruction,
            )
        else:
            root_prompt = canonical_json(
                {
                    "pipeline": "staged-comparison-v1",
                    "language": language,
                    "user_instruction": selection.user_instruction,
                    "items": [
                        {
                            "position": item.position,
                            "work_id": item.work_id,
                            "evidence_sha256": (
                                item.evidence.evidence_sha256 if item.evidence is not None else None
                            ),
                        }
                        for item in prepared_items
                    ],
                }
            )
        prompt_sha = sha256(root_prompt.encode()).hexdigest()
        key = comparison_key([item.work_id for item in prepared_items])
        fingerprint = comparison_request_fingerprint(
            comparison_key_value=key,
            ordered_items=_fingerprint_items(prepared_items),
            mode=mode,
            backend_id=backend_id,
            model_id=model_id,
            reasoning_effort=reasoning.value,
            service_tier=tier.value,
            language=language,
            prompt_sha256=prompt_sha,
        )
        preflight_items = tuple(
            ComparisonPreflightItem(
                position=item.position,
                requested_work_id=item.requested_work_id,
                work_id=item.work_id,
                title=item.title,
                document_version_id=(
                    item.evidence.document.document_version_id
                    if item.evidence is not None and item.evidence.document is not None
                    else None
                ),
                document_content_sha256=(
                    item.evidence.document.content_sha256
                    if item.evidence is not None and item.evidence.document is not None
                    else None
                ),
                evidence_level=(
                    item.evidence.evidence_level.value if item.evidence is not None else None
                ),
                evidence_sha256=(
                    item.evidence.evidence_sha256 if item.evidence is not None else None
                ),
                coverage=(item.evidence.coverage.percentage if item.evidence is not None else 0),
                status="available" if item.evidence is not None else "unavailable",
                warnings=(
                    item.evidence.warnings
                    if item.evidence is not None
                    else ("evidence_unavailable",)
                ),
            )
            for item in prepared_items
        )
        coverage = round(sum(item.coverage for item in preflight_items) / len(preflight_items))
        preflight = ComparisonPreflight(
            comparison_key=key,
            mode=mode,
            items=preflight_items,
            item_count=len(preflight_items),
            available_count=len(available),
            coverage=coverage,
            backend_id=backend_id,
            backend_display_name=capability.display_name,
            backend_location=capability.location,
            cloud_backend=capability.location is AgentLocation.CLOUD,
            model_id=model_id,
            model_options=tuple(PreflightModel.model_validate(item) for item in catalog.models),
            manual_model_id_allowed=catalog.supports_manual_model_id,
            default_model_allowed=catalog.supports_default_model,
            reasoning_effort=reasoning,
            service_tier=tier,
            language=language,
            ready=len(available) >= 2 and backend_ready and model_ready,
            warnings=tuple(dict.fromkeys(warnings)),
            prompt_sha256=prompt_sha,
            preflight_fingerprint=fingerprint,
        )
        return _PreparedComparison(
            preflight=preflight,
            items=tuple(prepared_items),
            root_prompt=root_prompt,
        )

    async def _execute_direct(
        self,
        job_id: str,
        payload: dict[str, Any],
        items: tuple[_PreparedComparisonItem, ...],
    ) -> StoredComparisonReport:
        evidence = tuple(item.evidence for item in items if item.evidence is not None)
        result = await self._generate(
            job_id,
            backend_id=cast(str, payload["backend_id"]),
            model_id=cast(str | None, payload["model_id"]),
            reasoning=ReasoningEffort(cast(str, payload["reasoning_effort"])),
            tier=ServiceTier(cast(str, payload["service_tier"])),
            prompt=cast(str, payload["root_prompt"]),
            output_schema=comparison_report_json_schema(),
            stage="direct",
            item_position=None,
        )
        report = StructuredComparisonReport.model_validate(result.structured_output)
        evidence_by_work = {item.work.work_id: item for item in evidence}
        validate_comparison_report(report, evidence_by_work)
        snapshots = tuple(
            ComparisonItemSnapshot(
                position=item.position,
                work_id=item.work_id,
                evidence=item.evidence,
                extraction_status=("direct" if item.evidence is not None else "unavailable"),
            )
            for item in items
        )
        coverage = self._coverage(snapshots)
        return self.comparisons.save_formal(
            self._provenance(
                job_id,
                payload,
                result=result,
                prompt=cast(str, payload["root_prompt"]),
                coverage=coverage,
                snapshots=snapshots,
                batch_count=1,
            ),
            report,
        )

    async def _execute_staged(
        self,
        job_id: str,
        payload: dict[str, Any],
        items: tuple[_PreparedComparisonItem, ...],
    ) -> StoredComparisonReport:
        completed: dict[int, Any] = {}
        backend_id = cast(str, payload["backend_id"])
        model_id = cast(str | None, payload["model_id"])
        reasoning = ReasoningEffort(cast(str, payload["reasoning_effort"]))
        tier = ServiceTier(cast(str, payload["service_tier"]))
        language = cast(str, payload["language"])
        instruction = cast(str | None, payload["user_instruction"])
        available_count = sum(item.evidence is not None for item in items)
        extraction_number = 0
        for item in items:
            if item.evidence is None:
                continue
            if self._cancel_requested(job_id):
                raise AgentBackendError(
                    AgentErrorCode.CANCELLED,
                    "Comparison was cancelled",
                )
            extraction_number += 1
            existing = self.comparisons.paper_evidence_at(job_id, item.position)
            if existing is not None:
                if existing.status == "complete":
                    completed[item.position] = existing
                continue
            extraction_prompt = compose_paper_extraction_prompt(
                item.evidence,
                language=language,
                user_instruction=instruction,
            )
            extraction_prompt_sha = sha256(extraction_prompt.encode()).hexdigest()
            reusable = (
                self.comparisons.find_reusable_paper_evidence(
                    source_job_id=job_id,
                    evidence_sha256=item.evidence.evidence_sha256,
                    backend_id=backend_id,
                    requested_model_id=model_id,
                    reasoning_effort=reasoning.value,
                    service_tier=tier.value,
                    prompt_sha256=extraction_prompt_sha,
                )
                if model_id is not None
                else None
            )
            if reusable is not None and reusable.extraction is not None:
                stored = self.comparisons.save_paper_evidence(
                    source_job_id=job_id,
                    position=item.position,
                    evidence=item.evidence,
                    backend_id=backend_id,
                    requested_model_id=model_id,
                    resolved_model_id=reusable.resolved_model_id,
                    reasoning_effort=reasoning.value,
                    service_tier=tier.value,
                    prompt_sha256=extraction_prompt_sha,
                    extraction=reusable.extraction,
                    reused_from_id=reusable.id,
                )
                completed[item.position] = stored
            else:
                try:
                    result = await self._generate(
                        job_id,
                        backend_id=backend_id,
                        model_id=model_id,
                        reasoning=reasoning,
                        tier=tier,
                        prompt=extraction_prompt,
                        output_schema=comparison_extraction_json_schema(),
                        stage="extraction",
                        item_position=item.position,
                    )
                    extraction = ComparisonPaperExtraction.model_validate(result.structured_output)
                    validate_paper_extraction(extraction, item.evidence)
                    stored = self.comparisons.save_paper_evidence(
                        source_job_id=job_id,
                        position=item.position,
                        evidence=item.evidence,
                        backend_id=backend_id,
                        requested_model_id=model_id,
                        resolved_model_id=result.resolved_model_id,
                        reasoning_effort=reasoning.value,
                        service_tier=tier.value,
                        prompt_sha256=extraction_prompt_sha,
                        extraction=extraction,
                    )
                    completed[item.position] = stored
                except AgentBackendError as error:
                    if error.code is AgentErrorCode.CANCELLED or self._cancel_requested(job_id):
                        raise
                    self.comparisons.save_paper_evidence(
                        source_job_id=job_id,
                        position=item.position,
                        evidence=item.evidence,
                        backend_id=backend_id,
                        requested_model_id=model_id,
                        resolved_model_id=None,
                        reasoning_effort=reasoning.value,
                        service_tier=tier.value,
                        prompt_sha256=extraction_prompt_sha,
                        error_category=error.code.value,
                        error_message=str(error),
                    )
                except (ValidationError, ComparisonEvidenceError):
                    self.comparisons.save_paper_evidence(
                        source_job_id=job_id,
                        position=item.position,
                        evidence=item.evidence,
                        backend_id=backend_id,
                        requested_model_id=model_id,
                        resolved_model_id=None,
                        reasoning_effort=reasoning.value,
                        service_tier=tier.value,
                        prompt_sha256=extraction_prompt_sha,
                        error_category="invalid_output",
                        error_message="Per-paper extraction failed its evidence contract.",
                    )
            progress = min(
                65,
                5 + round(55 * extraction_number / max(1, available_count)),
            )
            checkpoint = {
                "stage": "extraction",
                "completed_positions": sorted(completed),
                "processed": extraction_number,
                "available": available_count,
            }
            self.jobs.update_state(
                job_id,
                status="running",
                progress=progress,
                checkpoint=checkpoint,
            )
            self.jobs.append_event(
                job_id=job_id,
                event_type="comparison.paper.processed",
                progress=progress,
                payload={
                    "position": item.position,
                    "work_id": item.work_id,
                    "status": ("complete" if item.position in completed else "failed"),
                },
            )

        successful = [
            (item, completed[item.position]) for item in items if item.position in completed
        ]
        if len(successful) < 2:
            raise ComparisonEvidenceError("Fewer than two papers produced valid staged evidence")
        batches: list[StructuredComparisonReport] = []
        batch_prompts: list[str] = []
        batch_results: list[AgentResult] = []
        for batch_index, start in enumerate(range(0, len(successful), 10), start=1):
            chunk = successful[start : start + 10]
            extractions = tuple(stored.extraction for _, stored in chunk)
            if any(extraction is None for extraction in extractions):
                raise ComparisonEvidenceError("A completed extraction has no evidence")
            typed_extractions = tuple(
                cast(ComparisonPaperExtraction, extraction) for extraction in extractions
            )
            batch_prompt = compose_batch_comparison_prompt(
                typed_extractions,
                language=language,
                user_instruction=instruction,
            )
            result = await self._generate(
                job_id,
                backend_id=backend_id,
                model_id=model_id,
                reasoning=reasoning,
                tier=tier,
                prompt=batch_prompt,
                output_schema=comparison_report_json_schema(),
                stage="batch",
                item_position=None,
            )
            batch = StructuredComparisonReport.model_validate(result.structured_output)
            chunk_evidence = {
                item.work_id: cast(EvidenceBundle, item.evidence) for item, _ in chunk
            }
            validate_comparison_report(batch, chunk_evidence, minimum_papers=1)
            batches.append(batch)
            batch_prompts.append(batch_prompt)
            batch_results.append(result)
            progress = min(88, 65 + batch_index * 8)
            self.jobs.append_event(
                job_id=job_id,
                event_type="comparison.batch.succeeded",
                progress=progress,
                payload={"batch": batch_index, "paper_count": len(chunk)},
            )

        included_ids = [item.work_id for item, _ in successful]
        excluded_ids = [item.work_id for item in items if item.position not in completed]
        if len(batches) == 1:
            final_report = batches[0]
            final_prompt = batch_prompts[0]
            final_result = batch_results[0]
        else:
            final_prompt = compose_final_comparison_prompt(
                batches,
                work_ids=included_ids,
                excluded_work_ids=excluded_ids,
                language=language,
                user_instruction=instruction,
            )
            final_result = await self._generate(
                job_id,
                backend_id=backend_id,
                model_id=model_id,
                reasoning=reasoning,
                tier=tier,
                prompt=final_prompt,
                output_schema=comparison_report_json_schema(),
                stage="final",
                item_position=None,
            )
            final_report = StructuredComparisonReport.model_validate(final_result.structured_output)
        included_evidence = {
            item.work_id: cast(EvidenceBundle, item.evidence) for item, _ in successful
        }
        validate_comparison_report(final_report, included_evidence)

        evidence_records = {
            item.position: self.comparisons.paper_evidence_at(job_id, item.position)
            for item in items
        }
        snapshots: list[ComparisonItemSnapshot] = []
        for item in items:
            record = evidence_records[item.position]
            if item.evidence is None:
                extraction_status: Literal["unavailable", "failed", "extracted", "reused"] = (
                    "unavailable"
                )
            elif record is None or record.status == "failed":
                extraction_status = "failed"
            elif record.reused_from_id is not None:
                extraction_status = "reused"
            else:
                extraction_status = "extracted"
            snapshots.append(
                ComparisonItemSnapshot(
                    position=item.position,
                    work_id=item.work_id,
                    evidence=item.evidence,
                    extraction_status=extraction_status,
                    extraction_id=(
                        record.id if record is not None and record.status == "complete" else None
                    ),
                )
            )
        frozen_snapshots = tuple(snapshots)
        coverage = self._coverage(frozen_snapshots)
        return self.comparisons.save_formal(
            self._provenance(
                job_id,
                payload,
                result=final_result,
                prompt=final_prompt,
                coverage=coverage,
                snapshots=frozen_snapshots,
                batch_count=len(batches),
            ),
            final_report,
        )

    async def _generate(
        self,
        job_id: str,
        *,
        backend_id: str,
        model_id: str | None,
        reasoning: ReasoningEffort,
        tier: ServiceTier,
        prompt: str,
        output_schema: dict[str, Any],
        stage: Literal["direct", "extraction", "batch", "final"],
        item_position: int | None,
    ) -> AgentResult:
        backend = self.backends.require(backend_id)
        capability = backend.capability
        if not all(
            (
                capability.installed,
                capability.authenticated,
                capability.available,
                capability.supports_schema,
            )
        ):
            raise AgentBackendError(
                AgentErrorCode.UNAVAILABLE,
                "Selected comparison backend is unavailable",
                retryable=True,
            )
        sequence = len(self.comparisons.list_fragments(job_id))

        async def on_fragment(fragment: AgentFragment) -> None:
            nonlocal sequence
            current_sequence = sequence
            sequence += 1
            self.comparisons.append_fragment(
                job_id=job_id,
                sequence=current_sequence,
                stage=stage,
                item_position=item_position,
                kind=fragment.kind,
                text=fragment.text,
            )
            self.jobs.append_event(
                job_id=job_id,
                event_type="comparison.generate.fragment",
                progress=min(94, 10 + current_sequence),
                payload={
                    "sequence": current_sequence,
                    "stage": stage,
                    "item_position": item_position,
                    "kind": fragment.kind,
                    "text": fragment.text,
                },
            )

        request = AgentRequest(
            task_id=job_id,
            task_kind=AgentTaskKind.COMPARISON,
            prompt=prompt,
            output_schema=output_schema,
            model_id=model_id,
            reasoning_effort=reasoning,
            service_tier=tier,
            external_research=False,
        )
        result = await self.backends.generate(
            backend_id,
            request,
            on_fragment=on_fragment,
            cancelled=lambda: self._cancel_requested(job_id),
        )
        if self._cancel_requested(job_id):
            raise AgentBackendError(
                AgentErrorCode.CANCELLED,
                "Comparison was cancelled before its result could be committed",
            )
        return result

    def _provenance(
        self,
        job_id: str,
        payload: dict[str, Any],
        *,
        result: AgentResult,
        prompt: str,
        coverage: int,
        snapshots: tuple[ComparisonItemSnapshot, ...],
        batch_count: int,
    ) -> ComparisonProvenance:
        included = [
            item.work_id
            for item in snapshots
            if item.extraction_status in {"direct", "extracted", "reused"}
        ]
        excluded = [item.work_id for item in snapshots if item.work_id not in included]
        details = {
            "selected_count": len(snapshots),
            "included_count": len(included),
            "excluded_count": len(excluded),
            "items": [
                {
                    "position": item.position,
                    "work_id": item.work_id,
                    "status": item.extraction_status,
                    "coverage": (
                        item.coverage
                        if item.extraction_status in {"direct", "extracted", "reused"}
                        else 0
                    ),
                }
                for item in snapshots
            ],
        }
        return ComparisonProvenance(
            job_id=job_id,
            comparison_key=cast(str, payload["comparison_key"]),
            mode=cast(Literal["direct", "staged"], payload["mode"]),
            backend_id=cast(str, payload["backend_id"]),
            requested_model_id=cast(str | None, payload["model_id"]),
            resolved_model_id=result.resolved_model_id,
            reasoning_effort=cast(str, payload["reasoning_effort"]),
            service_tier=cast(str, payload["service_tier"]),
            language=cast(Literal["zh-CN", "en", "bilingual"], payload["language"]),
            complete_prompt=prompt,
            request_fingerprint=cast(str, payload["preflight_fingerprint"]),
            coverage=coverage,
            coverage_details=details,
            provenance={
                "pipeline_version": _COMPARISON_JOB_SCHEMA_VERSION,
                "root_prompt_sha256": payload["prompt_sha256"],
                "batch_count": batch_count,
                "included_work_ids": included,
                "excluded_work_ids": excluded,
            },
            items=snapshots,
        )

    @staticmethod
    def _coverage(snapshots: tuple[ComparisonItemSnapshot, ...]) -> int:
        values = [
            item.coverage if item.extraction_status in {"direct", "extracted", "reused"} else 0
            for item in snapshots
        ]
        return round(sum(values) / len(values))

    @staticmethod
    def _validated_payload(payload: dict[str, Any]) -> dict[str, Any]:
        required = {
            "schema_version",
            "preflight_fingerprint",
            "comparison_key",
            "mode",
            "backend_id",
            "model_id",
            "reasoning_effort",
            "service_tier",
            "language",
            "user_instruction",
            "root_prompt",
            "prompt_sha256",
            "items",
        }
        if (
            set(payload) != required
            or payload.get("schema_version") != _COMPARISON_JOB_SCHEMA_VERSION
            or payload.get("mode") not in {"direct", "staged"}
        ):
            raise ValueError("Comparison Job payload has an unsupported shape")
        if (
            sha256(cast(str, payload["root_prompt"]).encode()).hexdigest()
            != payload["prompt_sha256"]
        ):
            raise ValueError("Frozen comparison prompt hash does not match")
        return payload

    @staticmethod
    def _payload_items(
        payload: dict[str, Any],
    ) -> tuple[_PreparedComparisonItem, ...]:
        raw_items = payload["items"]
        if not isinstance(raw_items, list) or not 2 <= len(raw_items) <= 50:
            raise ValueError("Comparison Job items are invalid")
        items: list[_PreparedComparisonItem] = []
        for expected_position, raw in enumerate(raw_items):
            if not isinstance(raw, dict) or set(raw) != {
                "position",
                "requested_work_id",
                "work_id",
                "title",
                "evidence",
                "error",
            }:
                raise ValueError("Comparison Job item has an unsupported shape")
            if raw["position"] != expected_position:
                raise ValueError("Comparison Job item order is not contiguous")
            evidence = (
                EvidenceBundle.model_validate(raw["evidence"])
                if raw["evidence"] is not None
                else None
            )
            if evidence is not None and evidence.work.work_id != raw["work_id"]:
                raise ValueError("Comparison evidence belongs to another Work")
            if (evidence is None) == (raw["error"] is None):
                raise ValueError("Comparison evidence availability is internally inconsistent")
            items.append(
                _PreparedComparisonItem(
                    position=expected_position,
                    requested_work_id=cast(str, raw["requested_work_id"]),
                    work_id=cast(str, raw["work_id"]),
                    title=cast(str, raw["title"]),
                    evidence=evidence,
                    error=cast(str | None, raw["error"]),
                )
            )
        if len({item.work_id for item in items}) != len(items):
            raise ValueError("Comparison Job contains duplicate canonical Works")
        return tuple(items)

    def _validate_frozen_request(
        self,
        payload: dict[str, Any],
        items: tuple[_PreparedComparisonItem, ...],
    ) -> None:
        for item in items:
            if item.evidence is None:
                continue
            evidence_payload = item.evidence.model_dump(
                mode="json",
                exclude={"evidence_sha256"},
            )
            actual_hash = sha256(canonical_json(evidence_payload).encode()).hexdigest()
            if actual_hash != item.evidence.evidence_sha256:
                raise ValueError("Frozen comparison evidence hash does not match")
        expected_key = comparison_key([item.work_id for item in items])
        if expected_key != payload["comparison_key"]:
            raise ValueError("Frozen comparison Work set does not match")
        expected_fingerprint = comparison_request_fingerprint(
            comparison_key_value=expected_key,
            ordered_items=_fingerprint_items(items),
            mode=cast(str, payload["mode"]),
            backend_id=cast(str, payload["backend_id"]),
            model_id=cast(str | None, payload["model_id"]),
            reasoning_effort=cast(str, payload["reasoning_effort"]),
            service_tier=cast(str, payload["service_tier"]),
            language=cast(str, payload["language"]),
            prompt_sha256=cast(str, payload["prompt_sha256"]),
        )
        if expected_fingerprint != payload["preflight_fingerprint"]:
            raise ValueError("Frozen comparison request fingerprint does not match")

    def _cancel_requested(self, job_id: str) -> bool:
        current = self.jobs.get(job_id)
        return current is None or current.cancel_requested

    def _finish_success(self, job_id: str, report: StoredComparisonReport) -> None:
        result = {
            "report_id": report.id,
            "comparison_key": report.comparison_key,
            "version": report.version,
        }
        self.jobs.append_event(
            job_id=job_id,
            event_type="comparison.generate.succeeded",
            progress=100,
            payload=result,
        )
        self.jobs.update_state(
            job_id,
            status="succeeded",
            progress=100,
            result=result,
        )

    def _finish_failure(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        self.jobs.append_event(
            job_id=job_id,
            event_type="comparison.generate.failed",
            progress=100,
            payload={"error_code": error_code},
        )
        self.jobs.update_state(
            job_id,
            status="failed",
            progress=100,
            error_code=error_code,
            error_message=error_message,
        )

    def _finish_cancelled(self, job_id: str) -> None:
        self.jobs.append_event(
            job_id=job_id,
            event_type="comparison.generate.cancelled",
            progress=100,
            payload={},
        )
        self.jobs.update_state(
            job_id,
            status="cancelled",
            progress=100,
            error_code="cancelled",
            error_message="Comparison was cancelled",
        )
