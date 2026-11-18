from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.assistant.models import (
    AssistantContextSnapshotRecord,
    AssistantSessionRecord,
    AssistantTaskRecord,
)
from paper_reader.database import create_session_factory, create_sqlite_engine
from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.jobs.models import JobRecord
from paper_reader.migrations import upgrade_database
from paper_reader.reader.models import DocumentPageTextRecord
from paper_reader.reader.storage import ContentStore
from paper_reader.translations.contracts import TranslationScope
from paper_reader.translations.segmenter import SourceSegment, segment_context


@dataclass(frozen=True, slots=True)
class _Snapshot:
    scope: str
    evidence: dict[str, object]


@dataclass(slots=True)
class TranslationRepositoryContext:
    engine: Engine
    sessions: sessionmaker[Session]
    store: ContentStore
    repository: Any
    draft: Any
    segments: tuple[SourceSegment, ...]
    document_hash: str


def _page(page_number: int, text: str) -> dict[str, object]:
    return {
        "evidence_id": f"page:{page_number}",
        "page_number": page_number,
        "source": "native",
        "text": text,
        "source_text_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "page_width": 600,
        "page_height": 800,
        "confidence": None,
        "truncated": False,
    }


@pytest.fixture
def translation_repository_context(tmp_path: Path) -> TranslationRepositoryContext:
    from paper_reader.translations.repository import (
        TranslationArtifactDraft,
        TranslationRepository,
    )

    database = tmp_path / "paper-reader.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    store = ContentStore(tmp_path / "library")
    stored = store.ingest(
        (b"%PDF-1.7\nTranslation evidence fixture\n%%EOF\n",),
        media_type="application/pdf",
        max_bytes=2_048,
    )
    document_hash = stored.content_sha256
    page_text = (
        "The agent follows a locked policy and preserves $x_i$.\n\n"
        "A separate baseline reports independent results."
    )
    evidence = {
        "evidence_level": "full_text",
        "pages": [_page(1, page_text)],
        "selection_excerpt": None,
    }
    with sessions() as session:
        session.add(
            WorkRecord(
                id="work-1",
                canonical_title="Agent Translation Fixture",
                normalized_title="agent translation fixture",
                authors=["Alice"],
                abstract="A stable translation repository fixture.",
            )
        )
        session.flush()
        session.add(
            DocumentVersionRecord(
                id="document-1",
                work_id="work-1",
                source_record_id=None,
                content_sha256=document_hash,
                access_status="user_provided",
                media_type="application/pdf",
                completeness=100,
                extraction_status="complete",
                relative_path=stored.relative_path,
                byte_size=stored.byte_size,
                acquisition_kind="manual_import",
            )
        )
        session.add(
            DocumentPageTextRecord(
                document_version_id="document-1",
                page_number=1,
                page_width=600,
                page_height=800,
                native_text=page_text,
                native_blocks=[
                    {
                        "x0": 20.0,
                        "y0": 20.0,
                        "x1": 560.0,
                        "y1": 60.0,
                        "text": page_text,
                        "block_number": 1,
                    }
                ],
                native_character_count=len(page_text),
                likely_scanned=False,
                ocr_state="not_needed",
                ocr_text=None,
                ocr_blocks=[],
                ocr_confidence=None,
                ocr_engine_version=None,
                ocr_model_bundle_id=None,
                extractor_version="fixture-v1",
            )
        )
        session.add(AssistantSessionRecord(id="assistant-session-1", work_id="work-1"))
        session.add(
            JobRecord(
                id="job-1",
                kind="assistant.reply",
                status="queued",
                progress=0,
                payload={"mode": "translate"},
                idempotency_key="translate-fixture-1",
            )
        )
        session.flush()
        session.add(
            AssistantContextSnapshotRecord(
                id="snapshot-1",
                session_id="assistant-session-1",
                work_id="work-1",
                document_version_id="document-1",
                document_content_sha256=document_hash,
                scope="current_page",
                page_numbers=[1],
                selection=None,
                annotation_ids=[],
                section_fallback=False,
                extraction_version="fixture-v1",
                evidence=evidence,
                evidence_sha256="e" * 64,
                warnings=[],
                snapshot_sha256="f" * 64,
            )
        )
        session.flush()
        session.add(
            AssistantTaskRecord(
                id="task-1",
                session_id="assistant-session-1",
                context_snapshot_id="snapshot-1",
                retry_of_task_id=None,
                mode="translate",
                status="queued",
                network_policy="local_only",
                backend_id="codex_cli",
                requested_model_id="fixture-model",
                reasoning_effort="medium",
                service_tier="flex",
                output_language="zh-CN",
                idempotency_key="assistant-translate-fixture-1",
                command_sha256="c" * 64,
                job_id="job-1",
            )
        )
        session.commit()

    segments = segment_context(_Snapshot(scope="current_page", evidence=evidence), 256)
    context = TranslationRepositoryContext(
        engine=engine,
        sessions=sessions,
        store=store,
        repository=TranslationRepository(sessions),
        draft=TranslationArtifactDraft(
            work_id="work-1",
            document_version_id="document-1",
            document_content_sha256=document_hash,
            context_snapshot_id="snapshot-1",
            task_id="task-1",
            job_id="job-1",
            source_language="en",
            target_language="zh-CN",
            scope=TranslationScope.PAGE,
            evidence_level="native_text",
            prompt_version="translation-v1",
        ),
        segments=segments,
        document_hash=document_hash,
    )
    try:
        yield context
    finally:
        engine.dispose()


__all__ = ["TranslationRepositoryContext"]
