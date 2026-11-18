from __future__ import annotations

import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.related_resources.local_hints import (
    MAX_LOCAL_CANDIDATES,
    MAX_WORK_TEXT_CHARACTERS,
    LocalExplorationHintService,
    _explicit_preference_terms,
    _extract_terms,
)
from tests.recommendation.test_result_api import recommendation_app


class RecordingLexicalIndex:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, limit: int = 50) -> list[Any]:
        self.calls.append((query, limit))
        return self._delegate.search(query, limit=limit)


def seed_neighboring_works(app: Any) -> tuple[str, str]:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.reader.models import LibraryEntryRecord

    identities = IdentityRepository(
        app.state.sessions,
        work_index=app.state.lexical_index,
    )
    source = identities.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2608.01001v1",
            title="Diffusion methods for scientific discovery",
            authors=("Alice Researcher",),
            abstract="Diffusion priors improve scientific hypothesis generation.",
        ),
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )
    neighbor = identities.ingest(
        SourcePaper(
            connector_id="openreview",
            external_id="planning-diffusion-2026",
            title="Agent planning for diffusion methods",
            authors=("Bob Researcher",),
            abstract="An agent coordinates diffusion experiments and evaluation.",
        ),
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )
    with app.state.sessions() as session:
        session.add(LibraryEntryRecord(work_id=neighbor.work.id, added_via="save"))
        session.commit()
    return source.work.id, neighbor.work.id


def test_local_hint_uses_cached_library_without_agent_or_network_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = recommendation_app(tmp_path)
    source_id, neighbor_id = seed_neighboring_works(app)
    index = RecordingLexicalIndex(app.state.lexical_index)
    forbidden_calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> None:
        forbidden_calls.append("external")
        raise AssertionError("local hints must not use a model, subprocess, or network")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)

    hint = LocalExplorationHintService(
        app.state.sessions,
        lexical_index=index,
    ).for_work(source_id)

    assert hint is not None
    assert hint.work_id == source_id
    assert hint.related_work_ids == (neighbor_id,)
    assert "agent" in hint.suggested_terms
    assert "本地论文库" in hint.reason
    assert index.calls and index.calls[0][1] == MAX_LOCAL_CANDIDATES == 50
    assert forbidden_calls == []


def test_local_hint_is_absent_below_minimum_signal(tmp_path: Path) -> None:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper

    app = recommendation_app(tmp_path)
    isolated = IdentityRepository(
        app.state.sessions,
        work_index=app.state.lexical_index,
    ).ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2608.09999v1",
            title="A note",
            authors=("Solo Author",),
            abstract=None,
        ),
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )

    hint = LocalExplorationHintService(
        app.state.sessions,
        lexical_index=app.state.lexical_index,
    ).for_work(isolated.work.id)

    assert hint is None


def test_local_hint_recognizes_bounded_cached_doi_references(tmp_path: Path) -> None:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.reader.models import LibraryEntryRecord

    app = recommendation_app(tmp_path)
    identities = IdentityRepository(
        app.state.sessions,
        work_index=app.state.lexical_index,
    )
    source = identities.ingest(
        SourcePaper(
            connector_id="europe_pmc",
            external_id="MED-RELATED-1",
            title="Rare cancer cohort response",
            authors=("Clinical Group",),
            abstract="Longitudinal oncology evidence.",
            raw_payload={"references": ["doi:10.1234/cached-resource"]},
        ),
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )
    cited = identities.ingest(
        SourcePaper(
            connector_id="springer_nature",
            external_id="CACHED-RESOURCE-1",
            title="Mechanistic biomarkers for resistant tumors",
            authors=("Biomarker Lab",),
            doi="10.1234/cached-resource",
        ),
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )
    with app.state.sessions() as session:
        session.add(LibraryEntryRecord(work_id=cited.work.id, added_via="doi"))
        session.commit()

    hint = LocalExplorationHintService(
        app.state.sessions,
        lexical_index=app.state.lexical_index,
    ).for_work(source.work.id)

    assert hint is not None
    assert hint.related_work_ids == (cited.work.id,)
    assert "引用" in hint.reason


def test_local_hint_returns_at_most_five_terms_and_three_cached_works(
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.reader.models import LibraryEntryRecord

    app = recommendation_app(tmp_path)
    identities = IdentityRepository(
        app.state.sessions,
        work_index=app.state.lexical_index,
    )
    source = identities.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2608.02000v1",
            title="Diffusion methods for scientific discovery",
            authors=("Source Author",),
        ),
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )
    neighbor_ids: list[str] = []
    for index in range(8):
        neighbor = identities.ingest(
            SourcePaper(
                connector_id="openreview",
                external_id=f"bounded-neighbor-{index}",
                title=f"Diffusion methods with agent topic{index} planning",
                authors=(f"Author {index}",),
            ),
            now=datetime(2026, 8, 1, tzinfo=UTC),
        )
        neighbor_ids.append(neighbor.work.id)
    with app.state.sessions() as session:
        session.add_all(
            LibraryEntryRecord(work_id=work_id, added_via="save") for work_id in neighbor_ids
        )
        session.commit()

    hint = LocalExplorationHintService(
        app.state.sessions,
        lexical_index=app.state.lexical_index,
    ).for_work(source.work.id)

    assert hint is not None
    assert len(hint.related_work_ids) == 3
    assert set(hint.related_work_ids).issubset(neighbor_ids)
    assert 1 <= len(hint.suggested_terms) <= 5


def test_local_hint_uses_only_explicit_preference_terms() -> None:
    from paper_reader.discovery.preference_context import (
        PreferenceContext,
        PreferenceContextTerm,
    )

    context = PreferenceContext(
        version="preference-context-v1",
        terms=(
            PreferenceContextTerm(
                term="implicit-only",
                weight=100.0,
                sources=("aggregated_behavior",),
            ),
            PreferenceContextTerm(
                term="explicit-interest",
                weight=90.0,
                sources=("liked", "aggregated_behavior"),
            ),
        ),
        source_categories=("liked", "aggregated_behavior"),
        contributing_work_count=2,
        context_sha256="0" * 64,
    )

    assert _explicit_preference_terms(context) == ("explicit-interest",)


def test_local_term_extraction_has_a_hard_text_budget() -> None:
    oversized = "diffusion agent " + (" " * MAX_WORK_TEXT_CHARACTERS) + "unboundedtail"

    terms = _extract_terms(oversized, limit=10)

    assert terms == ("diffusion", "agent")
