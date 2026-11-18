from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.agents.contracts import ReasoningEffort, ServiceTier
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
from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.migrations import upgrade_database
from paper_reader.reader.models import AnnotationRecord, DocumentPageTextRecord
from paper_reader.reader.storage import ContentStore

if TYPE_CHECKING:
    from paper_reader.assistant.context import AssistantContextBuilder


@dataclass(slots=True)
class AssistantTestContext:
    engine: Engine
    sessions: sessionmaker[Session]
    repository: AssistantRepository
    builder: AssistantContextBuilder
    store: ContentStore
    snapshot: ContextSnapshotDraft


def ask_command(
    *,
    document_version_id: str = "document-1",
    message: str = "What evidence supports the main claim?",
) -> AssistantTaskCommand:
    return AssistantTaskCommand(
        mode=AssistantMode.ASK,
        message=message,
        context=ContextSelection(
            document_version_id=document_version_id,
            scope=ContextScope.CURRENT_PAGE,
            page_number=1,
        ),
        agent=AgentSelection(
            backend_id="codex_cli",
            model_id="fixture-model",
            reasoning_effort=ReasoningEffort.MEDIUM,
            service_tier=ServiceTier.FLEX,
        ),
        network_policy=NetworkPolicy.LOCAL_ONLY,
        output_language="zh-CN",
    )


@pytest.fixture
def assistant_context(tmp_path: Path) -> AssistantTestContext:
    from paper_reader.agents.evidence import EvidenceBundleBuilder
    from paper_reader.assistant.context import AssistantContextBuilder

    database = tmp_path / "paper-reader.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    store = ContentStore(tmp_path / "library")
    stored = store.ingest(
        (b"%PDF-1.7\nAssistant scoped evidence fixture\n%%EOF\n",),
        media_type="application/pdf",
        max_bytes=2_048,
    )
    with sessions() as session:
        canonical = WorkRecord(
            id="canonical-work",
            canonical_title="Canonical Assistant Fixture",
            normalized_title="canonical assistant fixture",
            authors=["Alice"],
            abstract="A stable abstract for Assistant repository tests.",
        )
        alias = WorkRecord(
            id="merged-work-alias",
            canonical_title="Merged Assistant Fixture",
            normalized_title="merged assistant fixture",
            authors=["Alice"],
            abstract="Alias abstract.",
            status="merged",
            merged_into_work_id=canonical.id,
        )
        session.add_all([canonical, alias])
        session.flush()
        session.add(
            DocumentVersionRecord(
                id="document-1",
                work_id=canonical.id,
                source_record_id=None,
                content_sha256=stored.content_sha256,
                access_status="user_provided",
                media_type="application/pdf",
                completeness=100,
                extraction_status="complete",
                relative_path=stored.relative_path,
                byte_size=stored.byte_size,
                acquisition_kind="manual_import",
            )
        )
        page_texts = (
            (
                1,
                "1 Introduction\nThe paper introduces a stable method.",
                [
                    _block(1, "1 Introduction", y0=20, y1=44),
                    _block(2, "The paper introduces a stable method.", y0=60, y1=70),
                ],
            ),
            (
                2,
                "2 Method\nThe method begins with a constrained objective.",
                [
                    _block(1, "2 Method", y0=20, y1=44),
                    _block(
                        2,
                        "The method begins with a constrained objective.",
                        y0=60,
                        y1=70,
                    ),
                ],
            ),
            (
                3,
                "Optimization details continue across this page.",
                [_block(1, "Optimization details continue across this page.", y0=20, y1=30)],
            ),
            (
                4,
                "Ablation evidence improves robustness.",
                [_block(1, "Ablation evidence improves robustness.", y0=20, y1=30)],
            ),
            (
                5,
                "3 Results\nResults are reported separately.",
                [
                    _block(1, "3 Results", y0=20, y1=44),
                    _block(2, "Results are reported separately.", y0=60, y1=70),
                ],
            ),
        )
        session.add_all(
            DocumentPageTextRecord(
                document_version_id="document-1",
                page_number=page_number,
                page_width=420,
                page_height=595,
                native_text=text,
                native_blocks=blocks,
                native_character_count=len(text),
                likely_scanned=False,
                ocr_state="not_needed",
                ocr_text=None,
                ocr_blocks=[],
                ocr_confidence=None,
                ocr_engine_version=None,
                ocr_model_bundle_id=None,
                extractor_version="fixture-v1",
            )
            for page_number, text, blocks in page_texts
        )
        session.add(
            AnnotationRecord(
                id="annotation-1",
                document_version_id="document-1",
                document_content_sha256=stored.content_sha256,
                kind="highlight",
                page_number=4,
                page_width=420,
                page_height=595,
                quote_text="Ablation evidence",
                context_before=None,
                context_after=" improves robustness.",
                rects=[{"x0": 20.0, "y0": 20.0, "x1": 150.0, "y1": 35.0}],
                color="amber",
                note="Use this evidence",
            )
        )
        session.commit()

    evidence_builder = EvidenceBundleBuilder(sessions=sessions, store=store)
    evidence = evidence_builder.build(
        "canonical-work",
        document_version_id="document-1",
        page_numbers=(1,),
        annotation_ids=(),
    )
    snapshot = ContextSnapshotDraft.create(
        work_id="canonical-work",
        document_version_id="document-1",
        document_content_sha256=stored.content_sha256,
        scope=ContextScope.CURRENT_PAGE,
        page_numbers=(1,),
        selection=None,
        annotation_ids=(),
        section_fallback=False,
        extraction_version="fixture-v1",
        evidence=evidence.model_dump(mode="json"),
        evidence_sha256=evidence.evidence_sha256,
        warnings=evidence.warnings,
    )
    repository = AssistantRepository(sessions)
    context = AssistantTestContext(
        engine=engine,
        sessions=sessions,
        repository=repository,
        builder=AssistantContextBuilder(
            sessions=sessions,
            evidence=evidence_builder,
            repository=repository,
        ),
        store=store,
        snapshot=snapshot,
    )
    try:
        yield context
    finally:
        engine.dispose()


__all__ = ["AssistantTestContext", "ask_command"]


def _block(
    block_number: int,
    text: str,
    *,
    y0: float,
    y1: float,
) -> dict[str, object]:
    return {
        "x0": 20.0,
        "y0": y0,
        "x1": 390.0,
        "y1": y1,
        "text": text,
        "block_number": block_number,
    }
