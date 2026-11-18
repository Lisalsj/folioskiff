from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.agents.contracts import (
    AgentCapability,
    AgentFragment,
    AgentLocation,
    AgentModel,
    AgentProbeCode,
    AgentRequest,
    AgentResult,
    AgentUsage,
    ModelCatalog,
    ReasoningEffort,
    ServiceTier,
)
from paper_reader.agents.evidence import (
    EvidenceBundle,
    EvidenceCoverage,
    EvidenceDocument,
    EvidenceLevel,
    EvidencePage,
    EvidenceWork,
)
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.assistant.contracts import (
    AgentSelection,
    AssistantMode,
    AssistantTaskCommand,
    ContextScope,
    ContextSelection,
    NetworkPolicy,
)
from paper_reader.assistant.repository import AssistantRepository, ContextSnapshotDraft
from paper_reader.database import create_session_factory, create_sqlite_engine
from paper_reader.discovery.keywords import (
    ConceptGroup,
    KeywordRepository,
    KeywordSetDefinition,
)
from paper_reader.discovery.models import DocumentVersionRecord, SourceRecord, WorkRecord
from paper_reader.discovery.plans import (
    DateRangeSpec,
    DiscoveryPlanCreate,
    DiscoveryPlanRepository,
)
from paper_reader.migrations import upgrade_database

NOW = datetime(2026, 8, 1, 9, tzinfo=UTC)


class RecordingRelatedBackend:
    def __init__(self) -> None:
        self.capability = AgentCapability(
            backend_id="codex_cli",
            display_name="Related intent fixture",
            location=AgentLocation.CLOUD,
            probe_code=AgentProbeCode.READY,
            installed=True,
            authenticated=True,
            available=True,
            supports_schema=True,
            supports_streaming=False,
            supports_vision=False,
            supports_external_research=False,
        )
        self.calls = 0
        self.discovery_calls = 0
        self.requests: list[AgentRequest] = []
        self.output: Mapping[str, Any] = {
            "summary": "Diffusion agents with explicit planning mechanisms",
            "required_groups": [
                ["diffusion model", "扩散模型"],
                ["planning agent", "规划智能体"],
            ],
            "optional_terms": [
                "hierarchical planning",
                "分层规划",
                "scientific discovery",
                "科学发现",
            ],
            "excluded_terms": [],
            "recent_months": 24,
            "source_ids": ["arxiv", "openreview"],
        }

    async def discover_models(self) -> ModelCatalog:
        self.discovery_calls += 1
        return ModelCatalog(
            models=(AgentModel(model_id="fixture-model", display_name="Fixture model"),),
            supports_manual_model_id=False,
            supports_default_model=False,
        )

    async def generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: Callable[[AgentFragment], Awaitable[None]],
        cancelled: Callable[[], bool],
    ) -> AgentResult:
        self.calls += 1
        self.requests.append(request)
        assert cancelled() is False
        await on_fragment(AgentFragment(index=0, kind="text", text="ignored fixture fragment"))
        return AgentResult(
            raw_output='{"fixture":"related-intent"}',
            structured_output=self.output,
            resolved_model_id=request.model_id,
            usage=AgentUsage(input_tokens=120, output_tokens=45),
        )


@dataclass(slots=True)
class RelatedIntentTestContext:
    engine: Engine
    sessions: sessionmaker[Session]
    assistant: AssistantRepository
    snapshot_id: str
    work_id: str
    backend: RecordingRelatedBackend
    repository: Any
    service: Any

    @property
    def agent_selection(self) -> AgentSelection:
        return AgentSelection(
            backend_id="codex_cli",
            model_id="fixture-model",
            reasoning_effort=ReasoningEffort.MEDIUM,
            service_tier=ServiceTier.FLEX,
        )


@pytest.fixture
def related_intent_context(tmp_path: Path) -> RelatedIntentTestContext:
    from paper_reader.related_resources.intent import RelatedIntentService
    from paper_reader.related_resources.repository import RelatedIntentRepository

    database = tmp_path / "paper-reader.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    work_id = "work-diffusion-agent"
    document_id = "document-diffusion-agent"
    document_hash = "a" * 64
    with sessions() as session:
        session.add(
            WorkRecord(
                id=work_id,
                canonical_title="Diffusion agents for scientific discovery",
                normalized_title="diffusion agents for scientific discovery",
                authors=["Alice Researcher"],
                abstract=(
                    "A planning agent coordinates diffusion experiments for scientific "
                    "hypothesis generation."
                ),
            )
        )
        session.flush()
        session.add_all(
            (
                DocumentVersionRecord(
                    id=document_id,
                    work_id=work_id,
                    source_record_id=None,
                    content_sha256=document_hash,
                    access_status="user_provided",
                    media_type="application/pdf",
                    completeness=100,
                    extraction_status="complete",
                    relative_path="sha256/aa/fixture.pdf",
                    byte_size=128,
                    acquisition_kind="manual_import",
                ),
                SourceRecord(
                    work_id=work_id,
                    connector_id="arxiv",
                    external_id="2608.01001v1",
                    title="Diffusion agents for scientific discovery",
                    normalized_title="diffusion agents for scientific discovery",
                    authors=["Alice Researcher"],
                    abstract="Planning agents coordinate diffusion experiments.",
                    raw_payload={
                        "keywords": ["scientific machine learning", "hypothesis generation"]
                    },
                ),
            )
        )
        session.commit()

    keywords = KeywordRepository(sessions)
    keyword_version = keywords.create_version(
        research_intent="Track planning agents for scientific discovery",
        definition=KeywordSetDefinition(
            required=(
                ConceptGroup(
                    key="planning",
                    label="Planning",
                    terms=("planning agent", "规划智能体"),
                ),
            ),
            boost=(
                ConceptGroup(
                    key="causal",
                    label="Causal discovery",
                    terms=("causal discovery", "因果发现"),
                ),
            ),
        ),
    )
    keywords.confirm(keyword_version.id)
    DiscoveryPlanRepository(sessions).create(
        DiscoveryPlanCreate(
            name="Planning agent watch",
            research_intent="Track planning agents for scientific discovery",
            keyword_version_id=keyword_version.id,
            date_range=DateRangeSpec(mode="rolling", rolling_months=3),
            sources=("arxiv", "openreview"),
        ),
        now=NOW,
    )

    page_text = (
        "Planning agents coordinate iterative denoising and scientific hypothesis generation."
    )
    evidence = EvidenceBundle.create(
        work=EvidenceWork(
            work_id=work_id,
            title="Diffusion agents for scientific discovery",
            authors=("Alice Researcher",),
            abstract=(
                "A planning agent coordinates diffusion experiments for scientific "
                "hypothesis generation."
            ),
            doi=None,
            published_at=None,
        ),
        document=EvidenceDocument(
            document_version_id=document_id,
            content_sha256=document_hash,
            access_status="user_provided",
            media_type="application/pdf",
            completeness=100,
            extraction_status="complete",
        ),
        evidence_level=EvidenceLevel.FULL_TEXT,
        pages=(
            EvidencePage(
                evidence_id="page:1:native",
                page_number=1,
                source="native",
                text=page_text,
                source_text_sha256=sha256(page_text.encode("utf-8")).hexdigest(),
                page_width=420,
                page_height=595,
                confidence=None,
                truncated=False,
            ),
        ),
        annotations=(),
        coverage=EvidenceCoverage(
            total_pages=1,
            available_pages=1,
            included_pages=1,
            total_characters=len(page_text),
            included_characters=len(page_text),
            percentage=100,
            truncated=False,
        ),
    )
    snapshot = ContextSnapshotDraft.create(
        work_id=work_id,
        document_version_id=document_id,
        document_content_sha256=document_hash,
        scope=ContextScope.CURRENT_PAGE,
        page_numbers=(1,),
        selection=None,
        annotation_ids=(),
        section_fallback=False,
        extraction_version="fixture-v1",
        evidence=evidence.model_dump(mode="json"),
        evidence_sha256=evidence.evidence_sha256,
        warnings=(),
    )
    assistant = AssistantRepository(sessions)
    persisted = assistant.create_task(
        work_id,
        AssistantTaskCommand(
            mode=AssistantMode.ASK,
            message="Persist the selected local context without running a model.",
            context=ContextSelection(
                document_version_id=document_id,
                scope=ContextScope.CURRENT_PAGE,
                page_number=1,
            ),
            agent=AgentSelection(backend_id="codex_cli"),
            network_policy=NetworkPolicy.LOCAL_ONLY,
            output_language="zh-CN",
        ),
        snapshot,
        idempotency_key="related-intent-context-fixture",
    )
    backend = RecordingRelatedBackend()
    repository = RelatedIntentRepository(sessions, clock=lambda: NOW)
    service = RelatedIntentService(
        sessions,
        repository=repository,
        backends=AgentBackendRegistry((backend,)),
        clock=lambda: NOW,
    )
    context = RelatedIntentTestContext(
        engine=engine,
        sessions=sessions,
        assistant=assistant,
        snapshot_id=persisted.context_snapshot_id,
        work_id=work_id,
        backend=backend,
        repository=repository,
        service=service,
    )
    try:
        yield context
    finally:
        engine.dispose()


__all__ = ["NOW", "RelatedIntentTestContext", "RecordingRelatedBackend"]
