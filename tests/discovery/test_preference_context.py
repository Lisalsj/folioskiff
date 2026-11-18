from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.settings import AppPaths

NOW = datetime(2026, 7, 15, 9, tzinfo=UTC)


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


async def bootstrap(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/session/bootstrap",
        json={"token": "secret"},
        headers={"Origin": "http://127.0.0.1"},
    )
    assert response.status_code == 204
    return {
        "Origin": "http://127.0.0.1",
        "X-CSRF-Token": client.cookies["paper_reader_csrf"],
    }


def preference_app(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.app import create_app

    return create_app(
        paths=AppPaths.from_overrides(
            state_root=tmp_path / "state",
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "library",
        ),
        bootstrap_token="secret",
    )


def seed_preferences(app: Any) -> dict[str, Any]:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.models import PreferenceAggregateRecord
    from paper_reader.recommendation.preferences import (
        AnalysisState,
        LibraryFeedback,
        LibraryStateInput,
        PreferenceRepository,
    )

    identities = IdentityRepository(
        app.state.sessions,
        work_index=app.state.lexical_index,
    )
    diffusion = identities.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.21001v1",
            title="Diffusion models for cancer imaging",
            authors=("Alice Researcher",),
            abstract=(
                "Latent diffusion improves tumor segmentation and medical image synthesis."
            ),
            updated_at=NOW,
        ),
        now=NOW,
    ).work
    pathology = identities.ingest(
        SourcePaper(
            connector_id="europe_pmc",
            external_id="PMC1234567",
            title="Agentic pathology diagnosis with multimodal evidence",
            authors=("Bob Pathologist",),
            abstract="Tool-using agents support precision oncology diagnosis.",
            updated_at=NOW,
        ),
        now=NOW,
    ).work
    ignored = identities.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.21003v1",
            title="Unrelated compiler optimization",
            authors=("Nobody Followed",),
            abstract="This paper must not influence the preference context.",
            updated_at=NOW,
        ),
        now=NOW,
    ).work
    preferences = PreferenceRepository(app.state.sessions, clock=lambda: NOW)
    preferences.set_library_state(
        diffusion.id,
        LibraryStateInput(
            favorite=True,
            feedback=LibraryFeedback.LIKE,
            feedback_reasons=("private-feedback-reason-must-not-leak",),
            pinned=False,
            reading_progress=25,
            analysis_state=AnalysisState.NOT_ANALYZED,
        ),
    )
    preferences.set_interested_author(
        author_key="bob pathologist",
        display_name="Bob Pathologist",
        active=True,
    )
    with app.state.sessions() as session:
        session.add(
            PreferenceAggregateRecord(
                work_id=diffusion.id,
                total_weight=7.5,
                aggregated_through=NOW,
                updated_at=NOW,
            )
        )
        session.commit()
    return {
        "diffusion": diffusion,
        "pathology": pathology,
        "ignored": ignored,
    }


def test_preference_context_is_deterministic_bounded_and_privacy_reduced(
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.preference_context import PreferenceContextService

    app = preference_app(tmp_path)
    seed_preferences(app)
    service = PreferenceContextService(app.state.sessions)

    first = service.current()
    repeated = service.current()
    serialized = json.dumps(first.as_dict(), ensure_ascii=False).casefold()

    assert first.context_sha256 == repeated.context_sha256
    assert len(first.context_sha256) == 64
    assert 1 <= len(first.terms) <= 12
    assert any("diffusion" in item.term.casefold() for item in first.terms)
    assert any("cancer" in item.term.casefold() for item in first.terms)
    assert {"favorite", "liked", "interested_author", "aggregated_behavior"}.issubset(
        set(first.source_categories)
    )
    assert all(0 < item.weight <= 100 for item in first.terms)
    assert "private-feedback-reason-must-not-leak" not in serialized
    assert "event_key" not in serialized
    assert "unrelated compiler" not in serialized
    assert "bob pathologist" not in {item.term.casefold() for item in first.terms}


def test_preference_context_is_explicitly_empty_without_supported_preferences(
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.preference_context import PreferenceContextService
    from paper_reader.recommendation.preferences import (
        ImplicitEventInput,
        ImplicitEventType,
        PreferenceRepository,
    )

    app = preference_app(tmp_path)
    work = IdentityRepository(app.state.sessions).ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.21888v1",
            title="Raw behavior must not become prompt context",
            authors=("Unfollowed Author",),
            updated_at=NOW,
        ),
        now=NOW,
    ).work
    PreferenceRepository(app.state.sessions, clock=lambda: NOW).record_implicit_event(
        work.id,
        ImplicitEventInput(
            event_key="private-raw-event-key",
            event_type=ImplicitEventType.DETAIL_OPEN,
        ),
    )
    context = PreferenceContextService(app.state.sessions).current()

    assert context.empty is True
    assert context.terms == ()
    assert context.source_categories == ()
    assert len(context.context_sha256) == 64


def test_irreversible_aggregate_can_contribute_without_explicit_library_state(
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.models import PreferenceAggregateRecord
    from paper_reader.discovery.preference_context import PreferenceContextService

    app = preference_app(tmp_path)
    work = IdentityRepository(app.state.sessions).ingest(
        SourcePaper(
            connector_id="europe_pmc",
            external_id="PMC7654321",
            title="Proteomics graph discovery for rare cancers",
            authors=("Historical Reader",),
            abstract="Protein interaction networks reveal oncology targets.",
            updated_at=NOW,
        ),
        now=NOW,
    ).work
    with app.state.sessions() as session:
        session.add(
            PreferenceAggregateRecord(
                work_id=work.id,
                total_weight=8.0,
                aggregated_through=NOW,
                updated_at=NOW,
            )
        )
        session.commit()

    context = PreferenceContextService(app.state.sessions).current()

    assert context.empty is False
    assert context.contributing_work_count == 1
    assert context.source_categories == ("aggregated_behavior",)
    assert any("proteomics" in item.term for item in context.terms)
    assert all(item.sources == ("aggregated_behavior",) for item in context.terms)


class RecordingBackend:
    backend_id = "recording-agent"

    def __init__(self) -> None:
        self.contexts: list[Any] = []

    async def suggest(self, context):  # type: ignore[no-untyped-def]
        from paper_reader.discovery.keywords import ConceptGroup, KeywordSetDefinition

        self.contexts.append(context)
        return KeywordSetDefinition(
            required=(
                ConceptGroup(
                    key="diffusion",
                    label="扩散模型",
                    terms=(
                        "diffusion model",
                        "扩散模型",
                        "latent diffusion",
                        "潜空间扩散",
                        "medical imaging",
                        "医学影像",
                        "cancer imaging",
                        "癌症影像",
                        "tumor segmentation",
                        "肿瘤分割",
                        "image synthesis",
                        "图像生成",
                    ),
                ),
            ),
        )


@pytest.mark.asyncio
async def test_keyword_api_resolves_visible_context_server_side_and_rejects_forgery(
    tmp_path: Path,
) -> None:
    app = preference_app(tmp_path)
    seed_preferences(app)
    backend = RecordingBackend()
    app.state.keyword_suggestion_backend = backend

    async with local_client(app) as client:
        headers = await bootstrap(client)
        context_response = await client.get("/api/v1/keywords/preference-context")
        context = context_response.json()
        suggested = await client.post(
            "/api/v1/keywords/suggestions",
            json={
                "research_intent": "研究扩散模型在癌症影像中的应用",
                "mode": "manual",
                "target_count": 18,
                "use_preference_context": True,
                "preference_context_sha256": context["context_sha256"],
            },
            headers=headers,
        )
        forged = await client.post(
            "/api/v1/keywords/suggestions",
            json={
                "research_intent": "研究扩散模型",
                "mode": "manual",
                "preference_terms": [
                    {"term": "fabricated-history", "translation": "伪造历史"}
                ],
            },
            headers=headers,
        )
        disabled = await client.post(
            "/api/v1/keywords/suggestions",
            json={
                "research_intent": "研究扩散模型在癌症影像中的应用",
                "mode": "manual",
                "use_preference_context": False,
            },
            headers=headers,
        )

    assert context_response.status_code == 200
    assert context["empty"] is False
    assert len(context["terms"]) <= 12
    assert suggested.status_code == 201
    assert backend.contexts[0].preference_terms
    assert suggested.json()["generator_info"]["preference_context"] == {
        "enabled": True,
        "sha256": context["context_sha256"],
        "term_count": len(context["terms"]),
    }
    assert forged.status_code == 422
    assert disabled.status_code == 201
    assert backend.contexts[-1].preference_terms == ()
    assert disabled.json()["generator_info"]["preference_context"] == {
        "enabled": False,
        "sha256": None,
        "term_count": 0,
    }


@pytest.mark.asyncio
async def test_keyword_api_rejects_a_stale_preference_context_hash(tmp_path: Path) -> None:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.recommendation.preferences import (
        AnalysisState,
        LibraryFeedback,
        LibraryStateInput,
        PreferenceRepository,
    )

    app = preference_app(tmp_path)
    seed_preferences(app)
    backend = RecordingBackend()
    app.state.keyword_suggestion_backend = backend

    async with local_client(app) as client:
        headers = await bootstrap(client)
        old_context = (await client.get("/api/v1/keywords/preference-context")).json()
        new_work = IdentityRepository(
            app.state.sessions,
            work_index=app.state.lexical_index,
        ).ingest(
            SourcePaper(
                connector_id="arxiv",
                external_id="2607.21999v1",
                title="Diffusion transformers for radiology",
                authors=("New Researcher",),
                abstract="Preference context changes after this paper is liked.",
                updated_at=NOW,
            ),
            now=NOW,
        ).work
        PreferenceRepository(app.state.sessions, clock=lambda: NOW).set_library_state(
            new_work.id,
            LibraryStateInput(
                favorite=False,
                feedback=LibraryFeedback.LIKE,
                feedback_reasons=("new-interest",),
                pinned=False,
                reading_progress=0,
                analysis_state=AnalysisState.NOT_ANALYZED,
            ),
        )
        stale = await client.post(
            "/api/v1/keywords/suggestions",
            json={
                "research_intent": "研究医学影像扩散模型",
                "mode": "manual",
                "use_preference_context": True,
                "preference_context_sha256": old_context["context_sha256"],
            },
            headers=headers,
        )

    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "preference_context_stale"
    assert backend.contexts == []
