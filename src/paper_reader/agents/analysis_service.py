"""Evidence-bound analysis preflight and durable report execution."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, cast

from pydantic import ValidationError

from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentErrorCode,
    AgentFragment,
    AgentLocation,
    AgentRequest,
    AgentTaskKind,
    ReasoningEffort,
    ServiceTier,
)
from paper_reader.agents.evidence import EvidenceBundle, EvidenceBundleBuilder, canonical_json
from paper_reader.agents.preflight import (
    AnalysisNotReadyError,
    AnalysisPreflight,
    AnalysisPreflightConflict,
    PreflightModel,
    discover_model_catalog,
    model_selection_status,
)
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.agents.report_schema import (
    ReportEvidenceError,
    StructuredAnalysisReport,
    analysis_report_json_schema,
)
from paper_reader.agents.reports import (
    AnalysisProvenance,
    AnalysisReportRepository,
    ReportLanguage,
    StoredAnalysisReport,
    analysis_request_fingerprint,
)
from paper_reader.agents.selections import AnalysisJobCommand, AnalysisSelection
from paper_reader.agents.templates import (
    AgentSettingsRepository,
    ComposedPrompt,
    PromptComposer,
    PromptTemplateRepository,
)
from paper_reader.agents.vision import VisualPageSelection, select_visual_pages
from paper_reader.jobs.models import JobRecord
from paper_reader.jobs.repository import JobRepository
from paper_reader.recommendation.preferences import PreferenceRepository

_DEFAULT_TEMPLATE_ID = "builtin-general-v1"
_JOB_SCHEMA_VERSION = "analysis-job-v1"
_TERMINAL_JOB_STATES = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class _PreparedAnalysis:
    preflight: AnalysisPreflight
    evidence: EvidenceBundle
    prompt: ComposedPrompt
    visual_pages: tuple[VisualPageSelection, ...]
    model_supports_vision: bool


def _visual_manifest(
    evidence: EvidenceBundle,
    pages: tuple[VisualPageSelection, ...],
) -> tuple[dict[str, Any], ...]:
    document = evidence.document
    if not pages or document is None:
        return ()
    return tuple(
        {
            "page_number": page.page_number,
            "evidence_id": page.evidence_id,
            "primary": page.primary,
            "reference_kinds": list(page.reference_kinds),
            "document_content_sha256": document.content_sha256,
            "filename": (f"visual-{document.content_sha256[:16]}-p{page.page_number:04d}.png"),
            "media_type": "image/png",
        }
        for page in pages
    )


class AnalysisService:
    def __init__(
        self,
        *,
        jobs: JobRepository,
        reports: AnalysisReportRepository,
        preferences: PreferenceRepository,
        settings: AgentSettingsRepository,
        templates: PromptTemplateRepository,
        evidence: EvidenceBundleBuilder,
        backends: AgentBackendRegistry,
    ) -> None:
        self.jobs = jobs
        self.reports = reports
        self.preferences = preferences
        self.settings = settings
        self.templates = templates
        self.evidence = evidence
        self.backends = backends
        self._composer = PromptComposer()

    async def preflight(
        self,
        work_id: str,
        selection: AnalysisSelection,
    ) -> AnalysisPreflight:
        return (await self._prepare(work_id, selection)).preflight

    async def create_job(
        self,
        work_id: str,
        command: AnalysisJobCommand,
        *,
        idempotency_key: str,
    ) -> JobRecord:
        key = idempotency_key.strip()
        if not key or len(key) > 160:
            raise ValueError("idempotency_key must contain 1-160 characters")
        existing = self.jobs.get_by_idempotency_key(key)
        if existing is not None:
            if (
                existing.kind == "analysis.report"
                and existing.payload.get("work_id") == work_id
                and existing.payload.get("preflight_fingerprint") == command.preflight_fingerprint
            ):
                return existing
            raise AnalysisPreflightConflict(
                "idempotency key is already bound to a different analysis request"
            )
        prepared = await self._prepare(work_id, command.selection)
        preflight = prepared.preflight
        if preflight.preflight_fingerprint != command.preflight_fingerprint:
            raise AnalysisPreflightConflict("Analysis evidence or settings changed after preflight")
        if not preflight.ready:
            reason = (
                "An analysis model is required"
                if "model_required" in preflight.warnings
                else "The selected Agent backend is not ready"
            )
            raise AnalysisNotReadyError(reason)
        payload = {
            "schema_version": _JOB_SCHEMA_VERSION,
            "preflight_fingerprint": preflight.preflight_fingerprint,
            "work_id": preflight.work_id,
            "document_version_id": preflight.document_version_id,
            "evidence": prepared.evidence.model_dump(mode="json"),
            "backend_id": preflight.backend_id,
            "model_id": preflight.model_id,
            "reasoning_effort": preflight.reasoning_effort.value,
            "service_tier": preflight.service_tier.value,
            "language": preflight.language,
            "template_version_id": preflight.template_version_id,
            "complete_prompt": prepared.prompt.prompt,
            "prompt_sha256": prepared.prompt.prompt_sha256,
            "external_research": preflight.external_research,
            "include_visual_evidence": command.selection.include_visual_evidence,
            "model_supports_vision": prepared.model_supports_vision,
            "visual_pages": list(_visual_manifest(prepared.evidence, prepared.visual_pages)),
        }
        job = self.jobs.create(
            kind="analysis.report",
            payload=payload,
            idempotency_key=key,
        )
        if job.kind != "analysis.report" or canonical_json(job.payload) != canonical_json(payload):
            raise AnalysisPreflightConflict(
                "idempotency key is already bound to a different analysis request"
            )
        return job

    def resumable_job_ids(self) -> tuple[str, ...]:
        return self.jobs.list_resumable_ids(kind="analysis.report")

    async def execute(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.kind != "analysis.report":
            raise ValueError("Job is not an analysis.report Job")
        if job.status in _TERMINAL_JOB_STATES:
            return
        formal = self.reports.report_for_job(job_id)
        if formal is not None:
            self._finish_success(job_id, formal)
            return
        failure = self.reports.failure_for_job(job_id)
        if failure is not None:
            self._finish_failure(
                job_id,
                error_code=failure.error_category,
                error_message=failure.error_message,
            )
            return
        if job.cancel_requested:
            self._finish_cancelled(job_id)
            return

        self.jobs.update_state(job_id, status="running", progress=5)
        self.jobs.append_event(
            job_id=job_id,
            event_type="analysis.report.started",
            progress=5,
            payload={"work_id": job.payload.get("work_id")},
        )
        raw_response = ""
        try:
            payload = self._validated_job_payload(job.payload)
            evidence = EvidenceBundle.model_validate(payload["evidence"])
            evidence_payload = evidence.model_dump(
                mode="json",
                exclude={"evidence_sha256"},
            )
            if sha256(canonical_json(evidence_payload).encode("utf-8")).hexdigest() != (
                evidence.evidence_sha256
            ):
                raise ValueError("Frozen analysis evidence hash does not match")
            backend_id = cast(str, payload["backend_id"])
            complete_prompt = cast(str, payload["complete_prompt"])
            if sha256(complete_prompt.encode("utf-8")).hexdigest() != payload["prompt_sha256"]:
                raise ValueError("Frozen analysis prompt hash does not match")
            expected_fingerprint = analysis_request_fingerprint(
                work_id=cast(str, payload["work_id"]),
                document_version_id=cast(
                    str | None,
                    payload["document_version_id"],
                ),
                evidence=evidence,
                backend_id=backend_id,
                requested_model_id=cast(str | None, payload["model_id"]),
                reasoning_effort=ReasoningEffort(cast(str, payload["reasoning_effort"])),
                service_tier=ServiceTier(cast(str, payload["service_tier"])),
                language=cast(ReportLanguage, payload["language"]),
                template_version_id=cast(str, payload["template_version_id"]),
                complete_prompt=complete_prompt,
                external_research=cast(bool, payload["external_research"]),
            )
            if expected_fingerprint != payload["preflight_fingerprint"]:
                raise ValueError("Frozen analysis request fingerprint does not match")
            backend = self.backends.require(backend_id)
            capability = backend.capability
            if not capability.installed:
                raise AgentBackendError(
                    AgentErrorCode.UNAVAILABLE,
                    "Selected Agent backend is not installed",
                    retryable=True,
                )
            if not capability.authenticated:
                raise AgentBackendError(
                    AgentErrorCode.AUTHENTICATION,
                    "Selected Agent backend is not authenticated",
                    retryable=True,
                )
            if not capability.available or not capability.supports_schema:
                raise AgentBackendError(
                    AgentErrorCode.UNAVAILABLE,
                    "Selected Agent backend is unavailable",
                    retryable=True,
                )
            if (
                cast(bool, payload["external_research"])
                and not capability.supports_external_research
            ):
                raise AgentBackendError(
                    AgentErrorCode.PROTOCOL,
                    "Selected Agent backend does not support external research",
                )

            expected_visual_pages = select_visual_pages(
                evidence,
                supports_vision=cast(bool, payload["model_supports_vision"]),
                policy=None,
            )
            if not cast(bool, payload["include_visual_evidence"]):
                expected_visual_pages = ()
            if canonical_json(list(_visual_manifest(evidence, expected_visual_pages))) != (
                canonical_json(payload["visual_pages"])
            ):
                raise ValueError("Frozen visual evidence manifest does not match")

            fragment_offset = len(self.reports.list_fragments(job_id))

            async def on_fragment(fragment: AgentFragment) -> None:
                sequence = fragment_offset + fragment.index
                existing = self.reports.fragment(job_id, sequence)
                self.reports.append_fragment(
                    job_id=job_id,
                    sequence=sequence,
                    kind=fragment.kind,
                    text=fragment.text,
                )
                if existing is None:
                    progress = min(89, 20 + sequence)
                    self.jobs.append_event(
                        job_id=job_id,
                        event_type="analysis.report.fragment",
                        progress=progress,
                        payload={
                            "sequence": sequence,
                            "kind": fragment.kind,
                            "text": fragment.text,
                        },
                    )

            def cancelled() -> bool:
                current = self.jobs.get(job_id)
                return current is None or current.cancel_requested

            visual_batch = self.evidence.render_visual_evidence(
                evidence,
                supports_vision=(
                    capability.supports_vision and cast(bool, payload["model_supports_vision"])
                ),
                enabled=cast(bool, payload["include_visual_evidence"]),
                cancelled=cancelled,
            )
            request = AgentRequest(
                task_id=job_id,
                task_kind=AgentTaskKind.REPORT,
                prompt=complete_prompt,
                output_schema=analysis_report_json_schema(),
                model_id=cast(str | None, payload["model_id"]),
                reasoning_effort=ReasoningEffort(cast(str, payload["reasoning_effort"])),
                service_tier=ServiceTier(cast(str, payload["service_tier"])),
                external_research=cast(bool, payload["external_research"]),
                attachments=visual_batch.attachments,
            )
            result = await self.backends.generate(
                backend_id,
                request,
                on_fragment=on_fragment,
                cancelled=cancelled,
            )
            raw_response = result.raw_output
            if cancelled():
                self._finish_cancelled(job_id)
                return
            content = StructuredAnalysisReport.model_validate(result.structured_output)
            provenance = AnalysisProvenance.create(
                job_id=job_id,
                work_id=cast(str, payload["work_id"]),
                document_version_id=cast(
                    str | None,
                    payload["document_version_id"],
                ),
                evidence=evidence,
                backend_id=backend_id,
                requested_model_id=cast(str | None, payload["model_id"]),
                resolved_model_id=result.resolved_model_id,
                reasoning_effort=request.reasoning_effort,
                service_tier=request.service_tier,
                language=cast(ReportLanguage, payload["language"]),
                template_version_id=cast(str, payload["template_version_id"]),
                complete_prompt=request.prompt,
                external_research=request.external_research,
            )
            if provenance.request_fingerprint != payload["preflight_fingerprint"]:
                raise AnalysisPreflightConflict(
                    "Frozen analysis Job does not match its preflight fingerprint"
                )
            formal = self.reports.save_formal(provenance, content)
        except AgentBackendError as error:
            if error.code is AgentErrorCode.CANCELLED or self._cancel_requested(job_id):
                self._finish_cancelled(job_id)
                return
            self._record_failure(
                job,
                error_code=error.code.value,
                error_message=str(error),
                raw_response=raw_response,
                validation_errors=(error.diagnostic,) if error.diagnostic else (),
            )
            return
        except (ValidationError, ReportEvidenceError) as error:
            self._record_failure(
                job,
                error_code="invalid_output",
                error_message="Agent output did not satisfy the report contract",
                raw_response=raw_response,
                validation_errors=(str(error),),
            )
            return
        except Exception as error:
            self._record_failure(
                job,
                error_code="analysis_internal_error",
                error_message="Analysis execution failed safely",
                raw_response=raw_response,
                validation_errors=(type(error).__name__,),
            )
            return

        self._finish_success(job_id, formal)

    async def _prepare(
        self,
        work_id: str,
        selection: AnalysisSelection,
    ) -> _PreparedAnalysis:
        settings = self.settings.get()
        backend_id = selection.backend_id or settings.default_backend_id
        try:
            backend = self.backends.require(backend_id)
        except KeyError as error:
            raise AnalysisNotReadyError("Unknown Agent backend") from error
        capability = backend.capability
        catalog, catalog_warning = await discover_model_catalog(self.backends, backend_id)
        model_id = selection.model_id or settings.report_model_id
        reasoning = selection.reasoning_effort or ReasoningEffort(settings.report_reasoning)
        tier = selection.service_tier or ServiceTier(settings.service_tier)
        language = selection.language or cast(ReportLanguage, settings.report_language)
        template_id = selection.template_version_id or _DEFAULT_TEMPLATE_ID
        external_research = (
            settings.external_research_default
            if selection.external_research is None
            else selection.external_research
        )
        template = self.templates.get(template_id)
        evidence = self.evidence.build(
            work_id,
            document_version_id=selection.document_version_id,
            research_intent=selection.research_intent,
        )
        selected_model = next(
            (item for item in catalog.models if item.model_id == model_id),
            None,
        )
        model_supports_vision = bool(
            capability.supports_vision
            and selected_model is not None
            and selected_model.supports_vision is True
        )
        visual_pages = select_visual_pages(
            evidence,
            supports_vision=(model_supports_vision and selection.include_visual_evidence),
        )
        prompt = self._composer.compose(
            template=template,
            evidence=evidence,
            output_schema=analysis_report_json_schema(),
            temporary_instruction=selection.temporary_instruction,
            visual_manifest=_visual_manifest(evidence, visual_pages),
        )
        fingerprint = analysis_request_fingerprint(
            work_id=evidence.work.work_id,
            document_version_id=(
                evidence.document.document_version_id if evidence.document is not None else None
            ),
            evidence=evidence,
            backend_id=backend_id,
            requested_model_id=model_id,
            reasoning_effort=reasoning,
            service_tier=tier,
            language=language,
            template_version_id=template.id,
            complete_prompt=prompt.prompt,
            external_research=external_research,
        )
        warnings = list(evidence.warnings)
        warnings.extend(capability.degradation_reasons)
        warnings.extend(catalog.degradation_reasons)
        ready = True
        if not capability.installed:
            warnings.append("backend_not_installed")
            ready = False
        if not capability.authenticated:
            warnings.append("backend_not_authenticated")
            ready = False
        if not capability.available:
            warnings.append("backend_unavailable")
            ready = False
        if not capability.supports_schema:
            warnings.append("structured_output_unsupported")
            ready = False
        if catalog_warning is not None:
            warnings.append(catalog_warning)
        model_ready, model_warnings = model_selection_status(catalog, model_id)
        warnings.extend(model_warnings)
        ready = ready and model_ready
        if external_research and not capability.supports_external_research:
            warnings.append("external_research_unsupported")
            ready = False
        document = evidence.document
        preflight = AnalysisPreflight(
            work_id=evidence.work.work_id,
            document_version_id=(document.document_version_id if document is not None else None),
            document_content_sha256=(document.content_sha256 if document is not None else None),
            evidence_level=evidence.evidence_level.value,
            evidence_sha256=evidence.evidence_sha256,
            coverage=evidence.coverage.percentage,
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
            template_version_id=template.id,
            template_name=template.name,
            external_research=external_research,
            scope=(
                f"pages:{len(evidence.pages)}",
                f"annotations:{len(evidence.annotations)}",
                f"evidence:{evidence.evidence_level.value}",
                f"visual-pages:{len(visual_pages)}",
            ),
            warnings=tuple(dict.fromkeys(warnings)),
            ready=ready,
            prompt_sha256=prompt.prompt_sha256,
            preflight_fingerprint=fingerprint,
        )
        return _PreparedAnalysis(
            preflight=preflight,
            evidence=evidence,
            prompt=prompt,
            visual_pages=visual_pages,
            model_supports_vision=model_supports_vision,
        )

    @staticmethod
    def _validated_job_payload(payload: dict[str, Any]) -> dict[str, Any]:
        required = {
            "schema_version",
            "preflight_fingerprint",
            "work_id",
            "document_version_id",
            "evidence",
            "backend_id",
            "model_id",
            "reasoning_effort",
            "service_tier",
            "language",
            "template_version_id",
            "complete_prompt",
            "prompt_sha256",
            "external_research",
            "include_visual_evidence",
            "model_supports_vision",
            "visual_pages",
        }
        if set(payload) != required or payload.get("schema_version") != _JOB_SCHEMA_VERSION:
            raise ValueError("Analysis Job payload has an unsupported shape")
        if not isinstance(payload.get("include_visual_evidence"), bool):
            raise ValueError("Analysis Job visual policy is invalid")
        if not isinstance(payload.get("model_supports_vision"), bool):
            raise ValueError("Analysis Job model vision capability is invalid")
        if not isinstance(payload.get("visual_pages"), list):
            raise ValueError("Analysis Job visual manifest is invalid")
        return payload

    def _record_failure(
        self,
        job: JobRecord,
        *,
        error_code: str,
        error_message: str,
        raw_response: str,
        validation_errors: tuple[str, ...],
    ) -> None:
        payload = job.payload
        self.reports.save_failure(
            job_id=job.id,
            work_id=cast(str, payload["work_id"]),
            request_fingerprint=cast(str, payload["preflight_fingerprint"]),
            error_category=error_code,
            error_message=error_message,
            raw_response=raw_response,
            validation_errors=validation_errors,
            repair_count=(
                1
                if payload.get("backend_id") == "ollama_cloud"
                and error_code == AgentErrorCode.INVALID_OUTPUT.value
                else 0
            ),
        )
        self._finish_failure(
            job.id,
            error_code=error_code,
            error_message=error_message,
        )

    def _finish_success(self, job_id: str, report: StoredAnalysisReport) -> None:
        self.preferences.record_analysis_report_generated(
            report.work_id,
            report_id=report.id,
        )
        self.jobs.append_event(
            job_id=job_id,
            event_type="analysis.report.succeeded",
            progress=100,
            payload={"report_id": report.id, "version": report.version},
        )
        self.jobs.update_state(
            job_id,
            status="succeeded",
            progress=100,
            result={"report_id": report.id, "version": report.version},
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
            event_type="analysis.report.failed",
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
            event_type="analysis.report.cancelled",
            progress=100,
            payload={},
        )
        self.jobs.update_state(
            job_id,
            status="cancelled",
            progress=100,
            error_code="cancelled",
            error_message="Analysis was cancelled",
        )

    def _cancel_requested(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        return job is None or job.cancel_requested
