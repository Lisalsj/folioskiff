# Related Papers and Knowledge Resources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let readers explicitly request verified related papers and trustworthy knowledge resources, with user-confirmed search intent, separate result lists, visible networking, and no automatic model calls.

**Architecture:** Persist a related-resource set that begins as a local, editable search-intent draft. After user confirmation, query configured academic connectors through a bounded adapter, normalize candidates into the existing Work identity pipeline, and optionally run a separate Codex external-research task for public knowledge resources. Validate every DOI/source ID/URL and retain field-level provenance before displaying results.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, httpx, existing source connectors and Agent backends, Vue 3, TypeScript, Vitest, Playwright.

## Global Constraints

- Work on local `main`, use TDD and Conventional Commits, and do not push.
- Opening, scrolling, selecting, or annotating a paper must not call a model or network.
- A lightweight local exploration hint may use only local metadata, FTS, preferences, and cached papers.
- The user must click “相关推荐” and confirm/edit the intent before academic retrieval begins.
- Academic network retrieval and external-web research are visibly distinct.
- External-web research defaults off until the user explicitly enables it; the setting is remembered but always visible.
- Related papers and knowledge resources are stored and rendered separately.
- A result without a verifiable identity/link is excluded or labeled `pending_verification`.
- Adding a result to the library uses the existing Work identity/merge-review path.
- Related results never change score, ranking, feedback, or recommendation weights.

---

## File Map

**Create**

- `src/paper_reader/related_resources/__init__.py`
- `src/paper_reader/related_resources/contracts.py`: intent, result, verification, and command types.
- `src/paper_reader/related_resources/models.py`: set, paper candidate, and web-resource tables.
- `src/paper_reader/related_resources/repository.py`: immutable run/result persistence.
- `src/paper_reader/related_resources/local_hints.py`: no-model cached exploration hints.
- `src/paper_reader/related_resources/intent.py`: deterministic draft and optional Agent refinement.
- `src/paper_reader/related_resources/academic.py`: bounded connector search and normalization.
- `src/paper_reader/related_resources/web_policy.py`: SSRF-safe public URL policy.
- `src/paper_reader/related_resources/web_research.py`: Codex external-resource schema and validation.
- `src/paper_reader/related_resources/service.py`: staged confirmation and execution.
- `src/paper_reader/related_resources/workers.py`: academic/external Jobs.
- `src/paper_reader/related_resources/api.py`: `/api/v1/related-resources`.
- `src/paper_reader/migrations/versions/0037_related_resources.py`
- `tests/related_resources/conftest.py`
- `tests/related_resources/test_contracts.py`
- `tests/related_resources/test_repository.py`
- `tests/related_resources/test_local_hints.py`
- `tests/related_resources/test_intent.py`
- `tests/related_resources/test_academic.py`
- `tests/related_resources/test_web_policy.py`
- `tests/related_resources/test_web_research.py`
- `tests/related_resources/test_service.py`
- `tests/related_resources/test_api.py`
- `web/src/features/related-resources/api.ts`
- `web/src/features/related-resources/store.ts`
- `web/src/features/related-resources/components/RelatedIntentEditor.vue`
- `web/src/features/related-resources/components/RelatedTaskCard.vue`
- `web/src/features/related-resources/components/RelatedPaperList.vue`
- `web/src/features/related-resources/components/KnowledgeResourceList.vue`
- Matching frontend specs.
- `web/e2e/related-resources.spec.ts`

**Modify**

- `src/paper_reader/agents/contracts.py`: add related-intent and external-research task kinds.
- `src/paper_reader/discovery/keywords.py`: reuse canonical concept-group compilation.
- `src/paper_reader/discovery/identity.py`: expose bounded candidate ingestion service.
- `src/paper_reader/app.py`: related service/router.
- `src/paper_reader/migrations/versions/__init__.py`: migration head.
- `tests/foundation/test_migrations.py`
- `openapi/openapi.json`
- `web/src/api/schema.d.ts`
- `web/src/features/assistant/components/TaskModeSelector.vue`
- `web/src/features/assistant/components/ResearchAssistant.vue`
- `web/src/pages/ReaderWorkspacePage.vue`
- `web/src/pages/ReadingBriefingPage.vue`: local exploration hint only.

## Task 1: Define Related-Resource Contracts and Tables

**Files:**

- Create: `src/paper_reader/related_resources/__init__.py`
- Create: `src/paper_reader/related_resources/contracts.py`
- Create: `src/paper_reader/related_resources/models.py`
- Create: `src/paper_reader/migrations/versions/0037_related_resources.py`
- Create: `tests/related_resources/test_contracts.py`
- Modify: `src/paper_reader/agents/contracts.py`
- Modify: `src/paper_reader/migrations/versions/__init__.py`
- Modify: `tests/foundation/test_migrations.py`

**Interfaces:**

- Produces: `RelatedSearchIntent`, `RelatedSearchCommand`, `PaperVerificationStatus`, and `ResourceVerificationStatus`.
- Adds `RELATED_INTENT` and `RELATED_EXTERNAL` to `AgentTaskKind`.
- Migration `0037_related_resources` revises `0036_translations`.

- [x] **Step 1: Write failing intent and network-policy tests**

```python
def test_related_intent_requires_user_confirmed_bilingual_terms() -> None:
    intent = RelatedSearchIntent(
        summary="Diffusion agents for scientific discovery",
        required_groups=(("diffusion model", "扩散模型"),),
        optional_terms=("agent", "智能体"),
        excluded_terms=("image editing",),
        recent_months=60,
        source_ids=("arxiv", "openreview"),
        confirmed=False,
    )
    with pytest.raises(ValidationError, match="confirmed"):
        RelatedSearchCommand(intent=intent, include_external_web=False)


def test_external_web_requires_explicit_consent() -> None:
    with pytest.raises(ValidationError, match="consent"):
        RelatedSearchCommand(
            intent=confirmed_intent(),
            include_external_web=True,
            external_web_consent=False,
        )
```

- [x] **Step 2: Run contract and migration tests**

Run:

```powershell
uv run pytest tests/related_resources/test_contracts.py tests/foundation/test_migrations.py -v
```

Expected: missing related-resource modules and migration head.

- [x] **Step 3: Implement bounded contracts**

`RelatedSearchIntent`:

- Summary 1 to 2,000 characters.
- 1 to 12 required concept groups.
- 1 to 20 terms per group.
- Up to 40 optional and 40 excluded terms.
- Terms 1 to 120 characters with control characters rejected.
- `recent_months` from 1 to 120, default 60.
- 1 to 10 source IDs.
- Immutable revision and `confirmed` flag.

`RelatedSearchCommand` separates:

- `include_academic_sources: bool = True`
- `include_external_web: bool = False`
- `external_web_consent: bool = False`

Add both closed Agent task kinds in this step:

```python
class AgentTaskKind(StrEnum):
    # Existing task kinds stay unchanged.
    RELATED_INTENT = "related_intent"
    RELATED_EXTERNAL = "related_external"
```

- [x] **Step 4: Create normalized persistence**

Migration creates:

- `related_resource_sets`: Work/document hash/context snapshot/intent revision/status/coverage/timestamps.
- `related_paper_candidates`: set/source/external ID/DOI/title/authors/year/reason/match evidence/verification/linked Work.
- `knowledge_resources`: set/title/URL/domain/type/reason/accessed time/verification.
- `related_run_sources`: set/source/status/count/cursor/error/progress.

Unique constraints:

- `(set_id, connector_id, external_id)`.
- `(set_id, normalized_doi)` when DOI is present.
- `(set_id, canonical_url)` for knowledge resources.

- [x] **Step 5: Verify schema and contracts**

Run:

```powershell
uv run pytest tests/related_resources/test_contracts.py tests/foundation/test_migrations.py -v
uv run ruff check src/paper_reader/related_resources tests/related_resources
uv run mypy src
```

Expected: migration head `0037_related_resources` and all contract checks pass.

- [x] **Step 6: Commit contracts and migration**

```powershell
git add -- src/paper_reader/related_resources/__init__.py src/paper_reader/related_resources/contracts.py src/paper_reader/related_resources/models.py src/paper_reader/agents/contracts.py src/paper_reader/migrations/versions/0037_related_resources.py src/paper_reader/migrations/versions/__init__.py tests/related_resources/test_contracts.py tests/foundation/test_migrations.py
git commit -m "feat(related): define verified resource sets"
```

## Task 2: Generate Local Hints Without Model or Network Calls

**Files:**

- Create: `src/paper_reader/related_resources/local_hints.py`
- Create: `tests/related_resources/test_local_hints.py`
- Modify: `src/paper_reader/recommendation/briefing.py`
- Modify: `tests/recommendation/test_briefing.py`
- Regenerate: `openapi/openapi.json`
- Regenerate: `web/src/api/schema.d.ts`
- Synchronize: API freeze, release layout, Windows smoke, and their contract tests

**Interfaces:**

- Produces: `LocalExplorationHintService.for_work(work_id) -> LocalExplorationHint | None`.
- Produces at most one hint per Work/briefing request.

- [x] **Step 1: Write failing no-network/no-Agent tests**

```python
def test_local_hint_uses_cached_library_and_never_calls_agent_or_connector(context) -> None:
    hint = context.service.for_work("work-diffusion")

    assert hint is not None
    assert hint.related_work_ids == ("work-agent",)
    assert "agent" in hint.suggested_terms
    assert context.agent.calls == 0
    assert context.connector.calls == 0


def test_local_hint_is_absent_below_minimum_signal(context) -> None:
    assert context.service.for_work("work-with-no-neighbors") is None
```

- [x] **Step 2: Run local-hint tests**

Run:

```powershell
uv run pytest tests/related_resources/test_local_hints.py tests/recommendation/test_briefing.py -v
```

Expected: missing local hint service.

- [x] **Step 3: Implement bounded local matching**

Signals:

- Confirmed plan keywords.
- Work title/abstract terms.
- FTS overlap with existing library.
- Existing related citations/DOIs parsed from metadata.
- Explicit user interests.

Requirements:

- Query at most 50 local candidates.
- Return at most five suggested terms and three cached Works.
- Explain the local matching reason.
- Require a minimum lexical score.
- Do not change ranking or feedback.

- [x] **Step 4: Add a quiet briefing hint**

The briefing response may include:

```python
class LocalExplorationHint(BaseModel):
    work_id: str
    suggested_terms: tuple[str, ...]
    related_work_ids: tuple[str, ...]
    reason: str
```

Copy: “基于本地论文库，可继续探索 ……” with an explicit “相关推荐” button. Do not display a notification badge or auto-open the Assistant.

- [x] **Step 5: Verify local-only behavior**

Run:

```powershell
uv run pytest tests/related_resources/test_local_hints.py tests/recommendation/test_briefing.py -v
```

Expected: local matching, low-signal suppression, query bounds, and no-call spies pass.

- [x] **Step 6: Commit local exploration hints**

```powershell
git add -- src/paper_reader/related_resources/local_hints.py src/paper_reader/recommendation/briefing.py tests/related_resources/test_local_hints.py tests/recommendation/test_briefing.py
git commit -m "feat(related): suggest local exploration without model calls"
```

## Task 3: Draft and Confirm a Search Intent

**Files:**

- Create: `src/paper_reader/related_resources/intent.py`
- Create: `src/paper_reader/related_resources/repository.py`
- Create: `tests/related_resources/test_intent.py`
- Create: `tests/related_resources/test_repository.py`
- Create: `tests/related_resources/conftest.py`
- Modify: `src/paper_reader/related_resources/contracts.py`

**Interfaces:**

- Produces: `RelatedIntentService.draft(work_id, context_snapshot_id) -> RelatedSearchIntent`.
- Produces: `RelatedIntentService.refine(set_id, instruction, agent_selection) -> RelatedSearchIntent`.
- Produces: `RelatedIntentService.confirm(set_id, revision, intent) -> RelatedSearchIntent`.

- [x] **Step 1: Write failing draft, diff, and confirmation tests**

```python
def test_initial_draft_is_local_and_unconfirmed(context) -> None:
    intent = context.service.draft("work-diffusion", context.snapshot.id)
    assert intent.confirmed is False
    assert intent.required_groups
    assert context.backend.calls == 0


@pytest.mark.asyncio
async def test_refinement_occurs_only_after_explicit_request_and_keeps_diff(context) -> None:
    draft = context.service.draft("work-diffusion", context.snapshot.id)
    refined = await context.service.refine(
        draft.set_id,
        "Focus on planning agents",
        agent_selection(),
    )
    assert context.backend.calls == 1
    assert refined.revision == draft.revision + 1
    assert refined.diff.added_terms


def test_confirm_rejects_stale_revision(context) -> None:
    with pytest.raises(RelatedIntentConflict, match="revision"):
        context.service.confirm("set-1", revision=1, intent=edited_revision_two())
```

- [x] **Step 2: Run intent/repository tests**

Run:

```powershell
uv run pytest tests/related_resources/test_intent.py tests/related_resources/test_repository.py -v
```

Expected: missing intent and repository.

- [x] **Step 3: Implement deterministic local draft**

Build the first draft from:

- Current title, abstract, keywords, and confirmed plan keywords.
- Selected text/page/section when supplied.
- Local controlled vocabulary and bilingual term map.

The local draft has 6 to 20 suggested terms grouped into required, optional, and excluded sets. It is never executable until confirmed.

- [x] **Step 4: Implement optional Agent refinement**

Refinement:

- Uses `external_research=False`.
- Treats paper evidence as untrusted.
- Returns only the closed intent schema.
- Persists an immutable revision and term-level diff.
- Does not query connectors.

Use the previously defined `RELATED_INTENT` task kind for optional Agent refinement.

- [x] **Step 5: Implement optimistic confirmation**

Confirmation requires the current revision and saves `confirmed_at`. Editing a confirmed intent creates a new unconfirmed revision; an executed revision remains immutable.

- [x] **Step 6: Verify intent behavior**

Run:

```powershell
uv run pytest tests/related_resources/test_intent.py tests/related_resources/test_repository.py tests/assistant/test_prompt_injection.py -v
```

Expected: no-call draft, explicit refinement, diff, revision conflict, and injection tests pass.

- [x] **Step 7: Commit intent workflow**

```powershell
git add -- src/paper_reader/related_resources/intent.py src/paper_reader/related_resources/repository.py tests/related_resources/test_intent.py tests/related_resources/test_repository.py src/paper_reader/agents/contracts.py
git commit -m "feat(related): confirm editable search intent"
```

## Task 4: Search Academic Connectors and Reuse Work Identity

**Files:**

- Create: `src/paper_reader/related_resources/academic.py`
- Create: `src/paper_reader/related_resources/references.py`
- Create: `tests/related_resources/test_academic.py`
- Modify: `src/paper_reader/discovery/keywords.py`
- Modify: `src/paper_reader/discovery/identity.py`
- Modify: `src/paper_reader/related_resources/local_hints.py`

**Interfaces:**

- Produces: `AcademicRelatedSearch.search(intent, on_page, cancelled) -> AcademicSearchSummary`.
- Uses existing `ConnectorRegistry`, `CompiledKeywordQuery`, `SourcePage`, and Work identity review.

- [x] **Step 1: Write failing pagination, partial-success, and identity tests**

```python
@pytest.mark.asyncio
async def test_academic_search_is_bounded_and_partial_source_failure_is_preserved(context) -> None:
    context.openreview.fail_with_rate_limit()
    summary = await context.search.search(
        confirmed_intent(source_ids=("arxiv", "openreview")),
        on_page=context.persist,
        cancelled=lambda: False,
    )

    assert context.arxiv.fetch_calls <= 3
    assert summary.sources["arxiv"].status == "succeeded"
    assert summary.sources["openreview"].status == "failed"
    assert summary.partial is True


@pytest.mark.asyncio
async def test_cross_source_duplicate_links_to_one_work_or_review(context) -> None:
    summary = await context.search.search(duplicate_fixture_intent(), context.persist, lambda: False)
    assert len(summary.display_candidates) == 1
    assert summary.display_candidates[0].linked_work_id == "canonical-work"
```

- [x] **Step 2: Run academic-search tests**

Run:

```powershell
uv run pytest tests/related_resources/test_academic.py -v
```

Expected: missing academic adapter.

- [x] **Step 3: Compile intent into existing connector queries**

Convert concept groups to `CompiledKeywordQuery`. Generate month strings for `recent_months` ending in the current configured timezone. Cap:

- 120 months.
- 3 pages per source.
- 50 candidates total.
- 20 displayed papers.

Referenced DOI/source IDs already present in the current paper metadata can enter outside the recent-month window and are labeled `cited_by_current_paper`.

- [x] **Step 4: Normalize and verify candidates**

Verification:

- Connector ID and external ID are required.
- Normalize DOI with existing identity rules.
- Preserve raw source title/authors/date.
- Use DOI/source ID/title-author matching.
- Low-confidence merge enters merge review.
- Never let Agent text create a Work identity.

- [x] **Step 5: Verify all connector fixtures and identity regression**

Run:

```powershell
uv run pytest tests/related_resources/test_academic.py tests/discovery/test_free_connectors.py tests/discovery/test_keyed_connectors.py tests/discovery/test_identity_models.py tests/discovery/test_merge_reviews.py -v
```

Expected: bounded search and existing connector/identity tests pass.

- [x] **Step 6: Commit academic search**

```powershell
git add -- src/paper_reader/related_resources/academic.py src/paper_reader/discovery/keywords.py src/paper_reader/discovery/identity.py tests/related_resources/test_academic.py
git commit -m "feat(related): retrieve verified academic neighbors"
```

## Task 5: Validate Public Knowledge Resources Safely

**Files:**

- Create: `src/paper_reader/related_resources/web_policy.py`
- Create: `src/paper_reader/related_resources/web_research.py`
- Create: `tests/related_resources/test_web_policy.py`
- Create: `tests/related_resources/test_web_research.py`
- Modify: `tests/security/test_release_boundaries.py`
- Modify: `CHANGELOG.md`
- Modify: `docs/agent-analysis.md`
- Modify: `docs/connectors.md`
- Modify: `docs/security-privacy.md`
- Modify: `docs/testing-release.md`
- Modify: `docs/implementation-status.md`

**Interfaces:**

- Produces: async `PublicUrlPolicy.validate(url) -> ValidatedPublicUrl` and `PublicUrlVerifier.verify(url) -> VerifiedPublicUrl`.
- Produces: `KnowledgeResearchService.run(intent, snapshot, agent_selection) -> KnowledgeResearchResult`, preserving verified resources, bounded rejections and reproducibility metadata separately from Agent proposals.
- Agent task kind: `RELATED_EXTERNAL`.

- [x] **Step 1: Write failing SSRF and structured-output tests**

```python
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/secret",
        "https://localhost/admin",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pass@example.org/",
        "file:///C:/private.txt",
    ],
)
def test_public_url_policy_rejects_local_or_credentialed_targets(url: str) -> None:
    with pytest.raises(PublicUrlRejected):
        PublicUrlPolicy(fake_public_dns()).validate(url)


@pytest.mark.asyncio
async def test_external_research_requires_consent_and_external_capability(context) -> None:
    with pytest.raises(ExternalResearchUnavailable):
        await context.service.run(
            confirmed_intent(),
            context.snapshot,
            agent_without_external_research(),
        )
```

- [x] **Step 2: Run web-policy tests**

Run:

```powershell
uv run pytest tests/related_resources/test_web_policy.py tests/related_resources/test_web_research.py -v
```

Expected: missing policy and service.

- [x] **Step 3: Implement fail-closed URL validation**

Allow only:

- `https`.
- No username/password.
- Default port 443.
- DNS host with every resolved address public.
- Maximum URL length 2,048.
- Maximum five redirects, validating every hop.

Deny:

- IP literals.
- Loopback, private, link-local, multicast, reserved, and unspecified addresses.
- `.local`, `localhost`, and single-label hosts.
- Redirects that change to a denied address.

Do not fetch arbitrary page bodies for validation. Use bounded HEAD, then a range-limited GET only when HEAD is unsupported; cap response to 64 KiB.

- [x] **Step 4: Implement external-research schema and source quality**

Structured item:

```python
class KnowledgeResourceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    url: AnyHttpUrl
    resource_type: Literal["official_docs", "review", "course", "textbook", "database"]
    reason: str = Field(min_length=1, max_length=2_000)
    publisher: str | None = Field(default=None, max_length=200)
```

Prompt prioritizes official project/docs, universities, recognized scholarly databases, review articles, and textbook publishers. Exclude SEO farms, copied content, download mirrors, and unverifiable claims.

- [x] **Step 5: Validate and deduplicate results**

For each returned item:

1. Canonicalize URL.
2. Apply public URL policy.
3. Verify reachability with bounded request.
4. Record final URL, domain, HTTP time, title, type, and status.
5. Deduplicate by canonical URL/domain/title.
6. Keep at most 12 verified resources.

- [x] **Step 6: Verify security and research behavior**

Run:

```powershell
uv run pytest tests/related_resources/test_web_policy.py tests/related_resources/test_web_research.py tests/security/test_release_boundaries.py tests/agents/test_process_policy.py -v
```

Expected: SSRF, redirect, DNS rebinding fixture, consent, capability, schema, and source-quality tests pass.

- [x] **Step 7: Commit external resource validation**

```powershell
git add -- src/paper_reader/related_resources/web_policy.py src/paper_reader/related_resources/web_research.py tests/related_resources/test_web_policy.py tests/related_resources/test_web_research.py tests/security/test_release_boundaries.py
git commit -m "feat(related): validate external knowledge resources"
```

## Task 6: Orchestrate Related Jobs and API

**Files:**

- Create: `src/paper_reader/related_resources/service.py`
- Create: `src/paper_reader/related_resources/workers.py`
- Create: `src/paper_reader/related_resources/api.py`
- Create: `tests/related_resources/test_service.py`
- Create: `tests/related_resources/test_api.py`
- Modify: `src/paper_reader/app.py`
- Modify: `openapi/openapi.json`
- Modify: `web/src/api/schema.d.ts`

**Interfaces:**

- `POST /api/v1/related-resources/works/{work_id}/draft`
- `POST /api/v1/related-resources/{set_id}/refine`
- `PUT /api/v1/related-resources/{set_id}/intent`
- `POST /api/v1/related-resources/{set_id}/run`
- `GET /api/v1/related-resources/{set_id}`
- `POST /api/v1/related-resources/{set_id}/papers/{candidate_id}/save`
- Job kind: `related.search`.

- [x] **Step 1: Write failing staged-flow and no-auto-run tests**

```python
@pytest.mark.asyncio
async def test_draft_does_not_start_connector_or_agent_generation(api_context) -> None:
    response = await api_context.client.post(
        "/api/v1/related-resources/works/work-1/draft",
        json={"context_snapshot_id": api_context.snapshot.id},
        headers=api_context.mutation_headers("related-draft-1"),
    )
    assert response.status_code == 201
    assert api_context.agent.calls == 0
    assert api_context.connectors.calls == 0


@pytest.mark.asyncio
async def test_run_requires_current_confirmed_revision(api_context) -> None:
    response = await api_context.client.post(
        "/api/v1/related-resources/set-1/run",
        json={"intent_revision": 1, "include_external_web": False},
        headers=api_context.mutation_headers("related-run-1"),
    )
    assert response.status_code == 409
```

- [x] **Step 2: Run service/API tests**

Run:

```powershell
uv run pytest tests/related_resources/test_service.py tests/related_resources/test_api.py -v
```

Expected: missing service and routes.

- [x] **Step 3: Implement staged orchestration**

Run sequence:

1. Verify current confirmed intent revision.
2. Persist run-source rows and Job.
3. Execute academic sources with independent checkpoints.
4. Persist partial success.
5. If external web enabled and consented, execute separate Agent run with `external_research=True`.
6. Validate web results.
7. Compute coverage from requested/succeeded sources and verified items.
8. Finish Job even when one source failed, with `partial=true`.

- [x] **Step 4: Implement save-to-library safely**

Saving a paper candidate:

- Requires a verified connector identity or DOI.
- Calls the existing Work identity pipeline.
- Returns linked Work ID or merge-review ID.
- Is idempotent.
- Does not copy Agent-generated title/authors over official fields.

- [x] **Step 5: Generate API contracts and verify**

Run:

```powershell
uv run pytest tests/related_resources/test_service.py tests/related_resources/test_api.py tests/foundation/test_security.py -v
uv run python -m paper_reader.openapi --output openapi/openapi.json
npm run api:types:from-schema
npm run typecheck
```

Expected: staged flow, partial success, save, security, and generated contracts pass.

- [x] **Step 6: Commit orchestration and API**

```powershell
git add -- src/paper_reader/related_resources/service.py src/paper_reader/related_resources/workers.py src/paper_reader/related_resources/api.py src/paper_reader/app.py tests/related_resources/test_service.py tests/related_resources/test_api.py openapi/openapi.json web/src/api/schema.d.ts
git commit -m "feat(related): run staged verified recommendations"
```

## Task 7: Build Separate Related-Paper and Knowledge-Resource UI

**Files:**

- Create: `web/src/features/related-resources/api.ts`
- Create: `web/src/features/related-resources/store.ts`
- Create: `web/src/features/related-resources/store.spec.ts`
- Create: `web/src/features/related-resources/components/RelatedIntentEditor.vue`
- Create: `web/src/features/related-resources/components/RelatedIntentEditor.spec.ts`
- Create: `web/src/features/related-resources/components/RelatedTaskCard.vue`
- Create: `web/src/features/related-resources/components/RelatedPaperList.vue`
- Create: `web/src/features/related-resources/components/RelatedPaperList.spec.ts`
- Create: `web/src/features/related-resources/components/KnowledgeResourceList.vue`
- Create: `web/src/features/related-resources/components/KnowledgeResourceList.spec.ts`
- Modify: `web/src/features/assistant/components/TaskModeSelector.vue`
- Modify: `web/src/features/assistant/components/ResearchAssistant.vue`
- Modify: `web/src/pages/ReaderWorkspacePage.vue`
- Modify: `web/src/pages/ReadingBriefingPage.vue`

**Interfaces:**

- Related mode phases: `idle`, `draft`, `confirm`, `running`, `results`.
- Result tabs: `papers`, `knowledge`.
- External-web consent is visible in confirm and running states.

- [x] **Step 1: Write failing no-auto-call, intent, and result-separation tests**

```typescript
it('does not draft or search until the user clicks related recommendations', async () => {
  const calls: string[] = []
  const wrapper = mount(ResearchAssistant, {
    props: assistantProps(),
    global: { plugins: [createPinia()] },
  })
  await flushPromises()
  expect(calls.filter((url) => url.includes('/related-resources'))).toEqual([])
})

it('requires intent confirmation and keeps papers separate from websites', async () => {
  const wrapper = await mountRelatedMode()
  await wrapper.get('[data-mode="related"]').trigger('click')
  await wrapper.get('[data-test="start-related"]').trigger('click')
  await flushPromises()
  expect(wrapper.get('[data-test="intent-editor"]').exists()).toBe(true)
  expect(wrapper.find('[data-test="run-related"]').attributes('disabled')).toBeDefined()
  await confirmIntent(wrapper)
  expect(wrapper.get('[data-tab="papers"]').exists()).toBe(true)
  expect(wrapper.get('[data-tab="knowledge"]').exists()).toBe(true)
})
```

- [x] **Step 2: Run related UI tests**

Run:

```powershell
npx vitest run web/src/features/related-resources web/src/features/assistant web/src/pages/ReadingBriefingPage.spec.ts
```

Expected: missing modules and related mode.

- [x] **Step 3: Implement explicit intent and consent flow**

UI must:

- Show local draft terms grouped as required/optional/excluded.
- Allow add, delete, regroup, bilingual editing, sources, and month range.
- Show an Agent-refinement button separately from confirm.
- Display a term-level diff after refinement.
- Require confirmation after every edit.
- Show academic network retrieval and external-web research as separate checkboxes.
- Remember external-web choice only after first consent, while keeping the checkbox visible.

- [x] **Step 4: Implement two result lists**

Related paper row shows title, authors, year, source, DOI/source ID, relation reason, verification, confidence, and library/save state.

Knowledge resource row shows title, domain, resource type, reason, access date, verification, and an external-link warning. Use a plain list, not a matching paper-card visual.

- [x] **Step 5: Add quiet local hint entry**

The briefing hint links into Reader related mode with no API side effect before click. Dismissal is local UI state and does not become negative preference feedback.

- [x] **Step 6: Verify UI and production build**

Run:

```powershell
npx vitest run web/src/features/related-resources web/src/features/assistant web/src/pages/ReadingBriefingPage.spec.ts
npm run typecheck
npm run build
```

Expected: intent, consent, partial results, separate lists, save state, and build pass.

- [x] **Step 7: Commit related-resource UI**

```powershell
git add -- web/src/features/related-resources web/src/features/assistant web/src/pages/ReaderWorkspacePage.vue web/src/pages/ReadingBriefingPage.vue
git commit -m "feat(web): add explicit related research flow"
```

## Task 8: Verify Related Research End to End

**Files:**

- Create: `web/e2e/related-resources.spec.ts`
- Modify: `web/e2e/support.ts`
- Modify: `tests/related_resources/test_web_policy.py`

**Interfaces:**

- Produces: no-auto-call, academic partial-success, external-consent, verified-link, and library-save browser scenarios.

- [x] **Step 1: Write failing browser scenarios**

```typescript
test('related research starts only after click and confirmed intent', async ({ page }) => {
  const paper = await importRelatedFixture(page)
  await page.goto(`/library/${paper.workId}/read/${paper.documentVersionId}`)
  await expect.poll(() => relatedRequestCount(page)).toBe(0)
  await page.getByRole('tab', { name: 'AI 助手' }).click()
  await page.getByRole('button', { name: '相关推荐' }).click()
  await page.getByRole('button', { name: '开始准备检索词' }).click()
  await page.getByRole('button', { name: '确认检索意图' }).click()
  await page.getByRole('button', { name: '运行推荐' }).click()
  await expect(page.getByRole('tab', { name: '相关论文' })).toBeVisible()
})

test('external knowledge resources require visible consent', async ({ page }) => {
  await openConfirmedRelatedIntent(page)
  await page.getByLabel('同时查找外部知识资源').check()
  await expect(page.getByText('Codex 将联网访问公共网站')).toBeVisible()
  await page.getByLabel('我同意本次外部研究').check()
  await page.getByRole('button', { name: '运行推荐' }).click()
  await expect(page.getByRole('tab', { name: '知识资源' })).toBeVisible()
})
```

- [x] **Step 2: Run e2e and SSRF tests**

Run:

```powershell
npx playwright test web/e2e/related-resources.spec.ts --project=desktop-chromium
uv run pytest tests/related_resources/test_web_policy.py -v
```

Expected: e2e failure until deterministic source and external-research fixtures are complete.

- [x] **Step 3: Add deterministic connector and web-research fixtures**

Fixtures include:

- One duplicate paper across arXiv/OpenReview.
- One rate-limited source.
- One verified official documentation URL.
- One rejected loopback URL.
- One candidate requiring merge review.
- Request counters proving no call before click/confirm.

- [x] **Step 4: Run the complete milestone gate**

Run:

```powershell
uv run pytest tests/related_resources tests/discovery tests/security -v --basetemp=.tmp/pytest-m6
npx vitest run web/src/features/related-resources web/src/features/assistant
npx playwright test web/e2e/related-resources.spec.ts
npm run typecheck
npm run build
```

Expected: backend, security, UI, browser, and build checks pass.

- [x] **Step 5: Commit related-research acceptance**

```powershell
git add -- web/e2e/related-resources.spec.ts web/e2e/support.ts tests/related_resources/test_web_policy.py
git commit -m "test: verify explicit related research"
```

## Plan Completion Gate

Run:

```powershell
uv run pytest tests/related_resources tests/discovery tests/security -v --basetemp=.tmp/pytest-m6
npm test
npm run typecheck
npm run build
npx playwright test web/e2e/related-resources.spec.ts
git diff --check
git status --short
```

Expected:

- Zero related-model or network calls occur before explicit click and confirmation.
- Academic and web results remain separate and source-visible.
- Private/local URLs are rejected.
- Saving a related paper uses Work identity review.
- Scores, ranking, and feedback are unchanged.
- No push has occurred.
