# Frontend Modularization, Performance, and Maintainability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the initial bundle and oversized component/service risk while preserving the quiet-library experience, responsive reader state, accessibility, and all completed product behavior.

**Architecture:** Extract feature-owned composables and components along stable state boundaries instead of splitting by arbitrary line count. Make every top-level route lazy, keep PDF/OCR/Agent code in on-demand chunks, replace duplicated page-local UI with small shared primitives, and separate legacy Agent/OCR service responsibilities only after characterization tests lock current behavior.

**Tech Stack:** Vue 3, TypeScript, Pinia, Vue Router, Vite, Vitest, Playwright, CSS custom properties, Python 3.12, pytest, FastAPI.

## Global Constraints

- Work on local `main`, use TDD and Conventional Commits, and do not push.
- Preserve the quiet-library tokens: warm paper, forest green, restrained wine, editorial headings, readable sans-serif body.
- Design variance stays near 4/10, motion near 2/10, density near 5/10.
- Do not introduce another UI framework or a marketing-page visual language.
- Reader page, selection, annotations, Assistant context, and scroll positions must survive responsive pane changes.
- Mobile navigation and reader controls must not overlap.
- Initial application JavaScript target is at most 350,000 raw bytes; PDF.js, OCR, reader, comparison, publishing, and settings stay lazy.
- Every refactor begins with characterization tests and changes no public API behavior unless stated.
- Remove legacy paper-conversation code only after the unified Assistant has equivalent tested coverage.

---

## File Map

**Create**

- `web/src/components/ui/PageHeader.vue`
- `web/src/components/ui/StatusNotice.vue`
- `web/src/components/ui/EmptyState.vue`
- `web/src/components/ui/AsyncBoundary.vue`
- `web/src/components/ui/SectionNav.vue`
- Matching component specs.
- `web/src/features/reader/composables/useReaderWorkspace.ts`
- `web/src/features/reader/composables/useReadingPosition.ts`
- `web/src/features/reader/composables/useAnnotations.ts`
- `web/src/features/reader/composables/useResponsiveReader.ts`
- `web/src/features/reader/components/ReaderLibraryPane.vue`
- `web/src/features/reader/components/ReaderDocumentPane.vue`
- `web/src/features/reader/components/ReaderResearchSidebar.vue`
- `web/src/features/reader/components/ReaderMobileTabs.vue`
- `web/src/features/reader/components/ContextActionBar.vue`
- Matching reader specs.
- `web/src/features/pdf/composables/usePdfDocument.ts`
- `web/src/features/pdf/composables/usePdfSearch.ts`
- `web/src/features/pdf/composables/usePdfSelection.ts`
- `web/src/features/pdf/components/PdfToolbar.vue`
- `web/src/features/pdf/components/PdfPageSurface.vue`
- Matching PDF specs.
- `web/src/features/comparisons/components/ComparisonSelection.vue`
- `web/src/features/comparisons/components/ComparisonPreflight.vue`
- `web/src/features/comparisons/components/ComparisonResult.vue`
- `web/src/features/comparisons/components/ComparisonHistory.vue`
- `web/src/features/publishing/components/PublishingSelection.vue`
- `web/src/features/publishing/components/PublishingPreview.vue`
- `web/src/features/publishing/components/PublishingHistory.vue`
- `web/src/features/publishing/components/PublishingSettings.vue`
- Matching specs.
- `scripts/check_vite_budget.mjs`
- `tests/foundation/test_frontend_budget_contract.py`
- `src/paper_reader/agents/analysis_service.py`
- `src/paper_reader/agents/comparison_service.py`
- `src/paper_reader/agents/selections.py`
- `src/paper_reader/agents/preflight.py`
- `src/paper_reader/reader/ocr_contracts.py`
- `src/paper_reader/reader/ocr_detection.py`
- `src/paper_reader/reader/ocr_runtime.py`
- `src/paper_reader/reader/ocr_service.py`
- `src/paper_reader/assistant/compat.py`
- `src/paper_reader/migrations/versions/0038_remove_legacy_conversations.py`

**Modify**

- `web/src/router/index.ts`
- `web/src/components/AppShell.vue`
- `web/src/styles/base.css`
- `web/src/pages/ReaderWorkspacePage.vue`
- `web/src/pages/ReaderWorkspacePage.spec.ts`
- `web/src/components/PdfReader.vue`
- `web/src/components/PdfReader.spec.ts`
- `web/src/pages/ComparisonsPage.vue`
- `web/src/pages/ComparisonsPage.spec.ts`
- `web/src/pages/PublishingPage.vue`
- `web/src/pages/PublishingPage.spec.ts`
- `web/src/pages/ReadingBriefingPage.vue`
- `web/src/pages/LibraryPage.vue`
- `vite.config.ts`
- `package.json`
- `web/e2e/performance.spec.ts`
- `web/e2e/foundation.spec.ts`
- `web/e2e/reader.spec.ts`
- `src/paper_reader/agents/api.py`
- `src/paper_reader/agents/workers.py`
- `src/paper_reader/app.py`
- `src/paper_reader/reader/api.py`
- `src/paper_reader/reader/workers.py`
- `src/paper_reader/reader/native_worker.py`
- `src/paper_reader/migrations/versions/__init__.py`
- All existing Agent/report/comparison and OCR tests whose imports move.

**Remove after replacement tests pass**

- `web/src/components/PaperConversation.vue`
- `web/src/components/PaperConversation.spec.ts`
- Legacy storage-backed conversation sections from `src/paper_reader/agents/api.py`
- `src/paper_reader/agents/conversations.py`
- Conversation classes from `src/paper_reader/agents/service.py`
- `src/paper_reader/reader/ocr.py`

## Task 1: Establish Shared Quiet-Library UI Primitives

**Files:**

- Create: `web/src/components/ui/PageHeader.vue`
- Create: `web/src/components/ui/PageHeader.spec.ts`
- Create: `web/src/components/ui/StatusNotice.vue`
- Create: `web/src/components/ui/StatusNotice.spec.ts`
- Create: `web/src/components/ui/EmptyState.vue`
- Create: `web/src/components/ui/EmptyState.spec.ts`
- Create: `web/src/components/ui/AsyncBoundary.vue`
- Create: `web/src/components/ui/AsyncBoundary.spec.ts`
- Create: `web/src/components/ui/SectionNav.vue`
- Create: `web/src/components/ui/SectionNav.spec.ts`
- Modify: `web/src/styles/base.css`
- Modify: `web/src/pages/ReadingBriefingPage.vue`
- Modify: `web/src/pages/LibraryPage.vue`

**Interfaces:**

- Produces one semantic implementation for page headings, status/error messages, empty states, async state, and section navigation.
- Components accept slots and semantic props; they do not fetch data or own feature state.

- [x] **Step 1: Write failing semantics and focus tests**

```typescript
it('renders one page heading and optional actions without decorative metadata', () => {
  const wrapper = mount(PageHeader, {
    props: { title: '我的论文库', description: '保存、阅读与标注论文。' },
    slots: { actions: '<button>导入论文</button>' },
  })
  expect(wrapper.findAll('h1')).toHaveLength(1)
  expect(wrapper.get('h1').text()).toBe('我的论文库')
  expect(wrapper.get('button').text()).toBe('导入论文')
})

it('uses alert only for errors and status for progress', () => {
  expect(mount(StatusNotice, { props: { kind: 'error', message: '加载失败' } })
    .get('[role="alert"]').exists()).toBe(true)
  expect(mount(StatusNotice, { props: { kind: 'progress', message: '正在加载' } })
    .get('[role="status"]').exists()).toBe(true)
})
```

- [x] **Step 2: Run shared-component tests**

Run:

```powershell
npx vitest run web/src/components/ui
```

Expected: missing components.

- [x] **Step 3: Implement semantic primitives with existing tokens**

Requirements:

- No component defines a new color literal; use CSS variables.
- `PageHeader` exposes title, description, status, and actions slots.
- `StatusNotice` supports `info`, `success`, `warning`, `error`, `progress`.
- `EmptyState` limits primary and secondary actions to one each.
- `AsyncBoundary` has loading/error/empty/content slots and retry emit.
- `SectionNav` uses `aria-current` and horizontal overflow on narrow screens.

- [x] **Step 4: Replace duplicated briefing and library page states**

Use the primitives without changing data fetching. Remove duplicate local CSS only after matching snapshot/semantics tests.

- [x] **Step 5: Verify focused pages and accessibility**

Run:

```powershell
npx vitest run web/src/components/ui web/src/pages/ReadingBriefingPage.spec.ts web/src/pages/LibraryPage.spec.ts
npm run typecheck
```

Expected: component and existing page tests pass.

- [x] **Step 6: Commit shared primitives**

```powershell
git add -- web/src/components/ui web/src/styles/base.css web/src/pages/ReadingBriefingPage.vue web/src/pages/LibraryPage.vue
git commit -m "refactor(web): share quiet library interface primitives"
```

## Task 2: Split Reader Workspace State by Responsibility

**Files:**

- Create: `web/src/features/reader/composables/useReaderWorkspace.ts`
- Create: `web/src/features/reader/composables/useReaderWorkspace.spec.ts`
- Create: `web/src/features/reader/composables/useReadingPosition.ts`
- Create: `web/src/features/reader/composables/useReadingPosition.spec.ts`
- Create: `web/src/features/reader/composables/useAnnotations.ts`
- Create: `web/src/features/reader/composables/useAnnotations.spec.ts`
- Create: `web/src/features/reader/composables/useResponsiveReader.ts`
- Create: `web/src/features/reader/composables/useResponsiveReader.spec.ts`
- Modify: `web/src/pages/ReaderWorkspacePage.vue`
- Modify: `web/src/pages/ReaderWorkspacePage.spec.ts`

**Interfaces:**

- `useReaderWorkspace(workId, documentVersionId)` owns Work/document loading.
- `useReadingPosition(documentVersionId)` owns debounced position persistence/flush.
- `useAnnotations(documentVersionId, contentSha256)` owns annotations and migration.
- `useResponsiveReader()` owns drawer/mobile-pane state and scroll restoration.

- [x] **Step 1: Add characterization tests before moving code**

```typescript
it('keeps PDF page, selected Agent tab, and tab scroll after compact layout changes', async () => {
  const harness = mountReaderHarness()
  harness.reader.updateState({ page: 7, scale: 1.25, rotation: 0, query: '' })
  harness.sidebar.select('assistant')
  harness.sidebar.setScrollTop(180)

  harness.media.setCompact(true)
  harness.media.setCompact(false)

  expect(harness.reader.page).toBe(7)
  expect(harness.sidebar.active).toBe('assistant')
  expect(harness.sidebar.scrollTop).toBe(180)
})
```

Retain characterization for annotations, bookmarks, version changes, migration previews, document errors, and position flush on visibility change.

- [x] **Step 2: Run characterization tests**

Run:

```powershell
npx vitest run web/src/pages/ReaderWorkspacePage.spec.ts
```

Expected: current tests pass; the new characterization fails only if it exposes an existing defect, which must be fixed before extraction.

- [x] **Step 3: Extract data and persistence composables**

Each composable:

- Has one typed public return interface.
- Accepts dependencies or uses the shared typed API, not raw `fetch` in tests.
- Owns cleanup for watchers/listeners/timers.
- Exposes loading/error/retry.
- Does not import a Vue page component.

- [x] **Step 4: Extract responsive state and scroll restoration**

Use `matchMedia`, not scroll listeners. Persist per-pane scroll positions keyed by `annotations`, `assistant`, and `report`. Mobile view values are `pdf`, `assistant`, `annotations`, `report`.

- [x] **Step 5: Reduce the page to composition and routing**

`ReaderWorkspacePage.vue` should:

- Read route IDs.
- Instantiate four composables.
- Compose child panes.
- Handle page navigation emitted from annotations/Assistant/report.
- Contain no direct API calls.

- [x] **Step 6: Verify state preservation**

Run:

```powershell
npx vitest run web/src/features/reader/composables web/src/pages/ReaderWorkspacePage.spec.ts
npm run typecheck
```

Expected: all reader behavior remains unchanged.

- [x] **Step 7: Commit reader state extraction**

```powershell
git add -- web/src/features/reader/composables web/src/pages/ReaderWorkspacePage.vue web/src/pages/ReaderWorkspacePage.spec.ts
git commit -m "refactor(reader): isolate workspace state"
```

## Task 3: Compose Reader Panes and Selection Actions

**Files:**

- Create: `web/src/features/reader/components/ReaderLibraryPane.vue`
- Create: `web/src/features/reader/components/ReaderLibraryPane.spec.ts`
- Create: `web/src/features/reader/components/ReaderDocumentPane.vue`
- Create: `web/src/features/reader/components/ReaderDocumentPane.spec.ts`
- Create: `web/src/features/reader/components/ReaderResearchSidebar.vue`
- Create: `web/src/features/reader/components/ReaderResearchSidebar.spec.ts`
- Create: `web/src/features/reader/components/ReaderMobileTabs.vue`
- Create: `web/src/features/reader/components/ReaderMobileTabs.spec.ts`
- Create: `web/src/features/reader/components/ContextActionBar.vue`
- Create: `web/src/features/reader/components/ContextActionBar.spec.ts`
- Modify: `web/src/pages/ReaderWorkspacePage.vue`

**Interfaces:**

- `ContextActionBar` emits `explain`, `translate`, `ask`, and `annotate`.
- `ReaderResearchSidebar` tabs: annotations, assistant, report.
- Pane components receive state and emit intent; they do not call APIs.

- [x] **Step 1: Write failing component-contract tests**

```typescript
it('shows exactly four verified-selection actions and returns focus after action', async () => {
  const wrapper = mount(ContextActionBar, { props: { selection: verifiedSelection } })
  expect(wrapper.findAll('button').map((button) => button.text()))
    .toEqual(['解释', '翻译', '引用到提问', '保存标注'])
  await wrapper.get('button').trigger('click')
  expect(wrapper.emitted('explain')?.[0]).toEqual([verifiedSelection])
})

it('keeps formal reports separate from Assistant history', () => {
  const wrapper = mount(ReaderResearchSidebar, { props: sidebarProps() })
  expect(wrapper.get('[role="tab"][data-tab="assistant"]').exists()).toBe(true)
  expect(wrapper.get('[role="tab"][data-tab="report"]').exists()).toBe(true)
})
```

- [x] **Step 2: Run pane tests**

Run:

```powershell
npx vitest run web/src/features/reader/components
```

Expected: missing components.

- [x] **Step 3: Implement pane components**

Requirements:

- Reader library becomes a drawer at medium width.
- Mobile tabs are visible labels, not icon-only controls.
- `ContextActionBar` positions relative to the verified selection, clamps inside viewport, and closes on Escape.
- Assistant/report components lazy-mount on first visit.
- Switching tabs preserves each tab’s scroll position.

- [x] **Step 4: Compose the page and remove duplicated markup/style**

Keep page-level grid CSS only in the page. Move pane-specific styles beside their components. Avoid repeating button/input rules already provided by base tokens.

- [x] **Step 5: Verify reader component and page suites**

Run:

```powershell
npx vitest run web/src/features/reader/components web/src/pages/ReaderWorkspacePage.spec.ts
npm run typecheck
```

Expected: panes, selection actions, responsive behavior, and types pass.

- [x] **Step 6: Commit reader components**

```powershell
git add -- web/src/features/reader/components web/src/pages/ReaderWorkspacePage.vue
git commit -m "refactor(reader): compose focused workspace panes"
```

## Task 4: Split PDF Rendering, Search, and Selection

**Files:**

- Create: `web/src/features/pdf/composables/usePdfDocument.ts`
- Create: `web/src/features/pdf/composables/usePdfDocument.spec.ts`
- Create: `web/src/features/pdf/composables/usePdfSearch.ts`
- Create: `web/src/features/pdf/composables/usePdfSearch.spec.ts`
- Create: `web/src/features/pdf/composables/usePdfSelection.ts`
- Create: `web/src/features/pdf/composables/usePdfSelection.spec.ts`
- Create: `web/src/features/pdf/components/PdfToolbar.vue`
- Create: `web/src/features/pdf/components/PdfToolbar.spec.ts`
- Create: `web/src/features/pdf/components/PdfPageSurface.vue`
- Create: `web/src/features/pdf/components/PdfPageSurface.spec.ts`
- Modify: `web/src/components/PdfReader.vue`
- Modify: `web/src/components/PdfReader.spec.ts`

**Interfaces:**

- `usePdfDocument(url)` owns PDF.js load, page render, scale, rotation, and cleanup.
- `usePdfSearch(document)` owns query/results/navigation.
- `usePdfSelection(pageSurface)` owns text selection and normalized rectangles.
- `PdfReader` remains the stable parent public API.

- [x] **Step 1: Add characterization for cleanup, error, and keyboard commands**

```typescript
it('destroys the previous PDF session when the document changes or component unmounts', async () => {
  const sessions = fakePdfSessions()
  const wrapper = mount(PdfReader, { props: { documentUrl: '/first.pdf' } })
  await wrapper.setProps({ documentUrl: '/second.pdf' })
  wrapper.unmount()
  expect(sessions.destroyCounts).toEqual([1, 1])
})
```

Keep tests for page clamp, zoom, rotate, search, selection rectangles, quick highlight, bookmark, retry, and keyboard shortcuts.

- [x] **Step 2: Run existing and new PDF tests**

Run:

```powershell
npx vitest run web/src/components/PdfReader.spec.ts
```

Expected: characterization passes or reveals an existing cleanup defect to fix before extraction.

- [x] **Step 3: Extract composables with explicit cleanup**

Every PDF.js task and listener must have cleanup. Do not add `window` scroll listeners. Search cancellation invalidates older query generations. Selection verifies quote text against the rendered text layer before marking `verified=true`.

- [x] **Step 4: Extract toolbar and page surface**

Toolbar owns semantic controls and emits intents. Page surface owns canvas, text layer, annotation overlays, migration preview, selection capture, and rendering states.

- [x] **Step 5: Keep `PdfReader` as a compatibility facade**

`PdfReader` composes the new pieces and preserves its existing props, emits, and exposed methods. This prevents reader and annotation consumers from changing together.

- [x] **Step 6: Verify PDF behavior and lazy chunk**

Run:

```powershell
npx vitest run web/src/features/pdf web/src/components/PdfReader.spec.ts
npm run typecheck
npm run build
```

Expected: all tests pass and PDF.js remains outside the initial application chunk.

- [x] **Step 7: Commit PDF modularization**

```powershell
git add -- web/src/features/pdf web/src/components/PdfReader.vue web/src/components/PdfReader.spec.ts
git commit -m "refactor(reader): split pdf rendering concerns"
```

## Task 5: Split Comparison and Publishing Workflows

**Files:**

- Create: `web/src/features/comparisons/components/ComparisonSelection.vue`
- Create: `web/src/features/comparisons/components/ComparisonPreflight.vue`
- Create: `web/src/features/comparisons/components/ComparisonResult.vue`
- Create: `web/src/features/comparisons/components/ComparisonHistory.vue`
- Create: matching specs.
- Create: `web/src/features/publishing/components/PublishingSelection.vue`
- Create: `web/src/features/publishing/components/PublishingPreview.vue`
- Create: `web/src/features/publishing/components/PublishingHistory.vue`
- Create: `web/src/features/publishing/components/PublishingSettings.vue`
- Create: matching specs.
- Modify: `web/src/pages/ComparisonsPage.vue`
- Modify: `web/src/pages/ComparisonsPage.spec.ts`
- Modify: `web/src/pages/PublishingPage.vue`
- Modify: `web/src/pages/PublishingPage.spec.ts`

**Interfaces:**

- Pages own route/query orchestration only.
- Components emit commands and receive immutable typed view state.

- [x] **Step 1: Add characterization tests for current workflow states**

Comparison states:

- Empty selection.
- 2 to 10 direct comparison.
- More than 10 staged comparison.
- Preflight unavailable item.
- Running/cancelled/failed/succeeded.
- Versioned report history.

Publishing states:

- Selection and privacy exclusions.
- Preview.
- Publishing Job and failure recovery.
- Published history.
- No private note/chat/PDF in payload.

- [x] **Step 2: Run page tests before extraction**

Run:

```powershell
npx vitest run web/src/pages/ComparisonsPage.spec.ts web/src/pages/PublishingPage.spec.ts
```

Expected: current characterization passes.

- [x] **Step 3: Extract comparison components**

Move selection, preflight, result, and history markup/state helpers into focused components. Keep API orchestration in the page or a feature store. Each component has its own error/empty/loading state.

- [x] **Step 4: Extract publishing components**

Keep privacy allowlist logic visible in `PublishingPreview`. `PublishingSettings` may configure branch/site metadata but never accepts credentials or private file paths.

- [x] **Step 5: Verify workflows and accessibility**

Run:

```powershell
npx vitest run web/src/features/comparisons web/src/features/publishing web/src/pages/ComparisonsPage.spec.ts web/src/pages/PublishingPage.spec.ts
npm run typecheck
npm run build
```

Expected: all workflow states and production build pass.

- [x] **Step 6: Commit workflow components**

```powershell
git add -- web/src/features/comparisons web/src/features/publishing web/src/pages/ComparisonsPage.vue web/src/pages/ComparisonsPage.spec.ts web/src/pages/PublishingPage.vue web/src/pages/PublishingPage.spec.ts
git commit -m "refactor(web): split comparison and publishing workflows"
```

## Task 6: Replace Legacy Conversation Storage with an API Compatibility Adapter

**Files:**

- Create: `src/paper_reader/migrations/versions/0038_remove_legacy_conversations.py`
- Create: `src/paper_reader/assistant/compat.py`
- Modify: `src/paper_reader/agents/api.py`
- Modify: `src/paper_reader/agents/service.py`
- Modify: `src/paper_reader/app.py`
- Modify: `src/paper_reader/migrations/versions/__init__.py`
- Modify: `tests/foundation/test_migrations.py`
- Modify: `tests/agents/test_conversation_api.py`
- Remove: `src/paper_reader/agents/conversations.py`
- Remove: `tests/agents/test_conversations.py`
- Remove: `web/src/components/PaperConversation.vue`
- Remove: `web/src/components/PaperConversation.spec.ts`

**Interfaces:**

- `/api/v1/assistant` is the canonical paper-conversation API and storage model.
- Existing `/api/v1/conversations` paths remain as documented, storage-free adapters for API v1 compatibility.
- Formal analysis and comparison routes remain unchanged.
- Migration `0038_remove_legacy_conversations` revises `0037_related_resources`.

- [x] **Step 1: Add parity assertions to Assistant tests**

```python
@pytest.mark.parametrize(
    "capability",
    [
        "one_session_per_canonical_work",
        "backend_switching",
        "immutable_document_hash",
        "persist_before_stream",
        "idempotent_submit",
        "cancellation",
        "retry",
        "checkpoint_without_message_deletion",
    ],
)
def test_assistant_parity_matrix(capability: str, assistant_parity_receipt) -> None:
    assert assistant_parity_receipt[capability] is True
```

- [x] **Step 2: Run parity and legacy tests**

Run:

```powershell
uv run pytest tests/assistant tests/agents/test_conversations.py tests/agents/test_conversation_api.py -v
npx vitest run web/src/features/assistant web/src/components/PaperConversation.spec.ts
```

Expected: both implementations pass before the legacy storage is removed.

- [x] **Step 3: Write the failing compatibility-adapter test**

Replace storage-specific assertions in `tests/agents/test_conversation_api.py` with an API compatibility contract:

```python
def test_legacy_conversation_routes_delegate_without_legacy_storage(
    openapi_document: dict[str, object],
    client,
    seeded_assistant_session,
) -> None:
    path = "/api/v1/conversations/works/{work_id}"
    operation = openapi_document["paths"][path]["get"]
    assert operation["x-paper-reader-compatibility-adapter"] is True

    response = client.get(
        f"/api/v1/conversations/works/{seeded_assistant_session.work_id}"
    )
    assert response.status_code == 200
    assert response.json()["work_id"] == seeded_assistant_session.work_id
```

Also cover reply submission, lookup by legacy conversation ID, and fragment replay. Each assertion must observe data written to `AssistantRepository`, never a legacy table.

- [x] **Step 4: Replace legacy API/service/component wiring**

Remove:

- `ConversationService` construction/state.
- Legacy Vue component imports.
- Conversation-only workers and models.

Move the existing compatibility path declarations to `paper_reader.assistant.compat`. Map legacy request/response fields at the HTTP boundary and delegate all reads, submissions, retries, and fragment replay to `AssistantService`/`AssistantRepository`. Mark every compatibility operation with `x-paper-reader-compatibility-adapter: true` in OpenAPI.

Do not remove shared Agent backends, evidence, report, comparison, template, process-policy code, or any documented `/api/v1/conversations` path.

- [x] **Step 5: Drop legacy tables in the clean schema**

Migration drops legacy fragment, checkpoint, message, and conversation tables in foreign-key-safe order. Because the approved release uses a data rebuild, no message migration is required; the reset snapshot remains recoverable.

- [x] **Step 6: Verify no stale storage/import remains**

Run:

```powershell
Get-ChildItem src,tests,web -Recurse -File | Select-String -Pattern 'PaperConversation|ConversationService|paper_conversations|conversation_fragments|conversation_checkpoints'
uv run pytest tests/assistant tests/agents tests/foundation/test_migrations.py -v
npm test
npm run typecheck
```

Expected: search returns no executable legacy storage or component references; compatibility API, current Assistant, report, and comparison tests pass.

- [x] **Step 7: Commit storage replacement**

```powershell
git add -A -- src/paper_reader/agents src/paper_reader/assistant/compat.py src/paper_reader/app.py src/paper_reader/migrations tests/agents tests/assistant tests/foundation/test_migrations.py web/src/components web/src/features/assistant
git commit -m "refactor(assistant): replace legacy conversation storage"
```

## Task 7: Split Analysis, Comparison, and OCR Backend Services

**Files:**

- Create: `src/paper_reader/agents/analysis_service.py`
- Create: `src/paper_reader/agents/comparison_service.py`
- Create: `src/paper_reader/agents/selections.py`
- Create: `src/paper_reader/agents/preflight.py`
- Create: `src/paper_reader/reader/ocr_contracts.py`
- Create: `src/paper_reader/reader/ocr_detection.py`
- Create: `src/paper_reader/reader/ocr_runtime.py`
- Create: `src/paper_reader/reader/ocr_service.py`
- Modify: `src/paper_reader/agents/api.py`
- Modify: `src/paper_reader/agents/workers.py`
- Modify: `src/paper_reader/app.py`
- Modify: `src/paper_reader/reader/api.py`
- Modify: `src/paper_reader/reader/workers.py`
- Modify: `src/paper_reader/reader/native_worker.py`
- Remove: `src/paper_reader/agents/service.py`
- Remove: `src/paper_reader/reader/ocr.py`

**Interfaces:**

- `AnalysisService`, `Conversation`-free `ComparisonService`, and their public Pydantic selections keep existing signatures.
- OCR public classes retain import compatibility through re-exports in `paper_reader.reader.__init__`.

- [x] **Step 1: Run and extend characterization suites**

Run:

```powershell
uv run pytest tests/agents/test_analysis_service.py tests/agents/test_analysis_api.py tests/agents/test_comparisons.py tests/agents/test_comparison_api.py tests/reader/test_ocr.py tests/reader/test_ocr_api.py -v
```

Add one characterization assertion per public class/function imported outside its current module. Expected: all pass before file movement.

- [x] **Step 2: Extract selection and preflight contracts**

Move only Pydantic selections, job commands, and preflight models into focused modules. Re-export temporarily from old modules until all imports are updated in one commit.

- [x] **Step 3: Extract analysis and comparison services**

Analysis module owns analysis only. Comparison module owns comparison only. Shared backend/model resolution and fingerprint helpers move to `preflight.py`. No service imports a FastAPI route.

- [x] **Step 4: Extract OCR contracts, detection, runtime, and service**

Boundaries:

- `ocr_contracts.py`: immutable models/errors.
- `ocr_detection.py`: scanned-page detection and selection.
- `ocr_runtime.py`: model manifest/native process invocation.
- `ocr_service.py`: Jobs, persistence, and orchestration.

No module may import the API layer.

- [x] **Step 5: Remove compatibility re-exports and old files**

After all repository imports use the new modules, delete old service files. Keep package-level public imports only where third-party plugin contracts require them.

- [x] **Step 6: Verify all Agent, reader, typing, and lint suites**

Run:

```powershell
uv run pytest tests/agents tests/reader tests/security -v --basetemp=.tmp/pytest-m7-services
uv run mypy src scripts
uv run ruff check .
```

Expected: behavior remains stable with no circular imports.

- [x] **Step 7: Commit backend modularization**

```powershell
git add -A -- src/paper_reader/agents src/paper_reader/reader src/paper_reader/app.py tests/agents tests/reader tests/security
git commit -m "refactor(core): separate agent and ocr services"
```

## Task 8: Enforce Route Splitting and Bundle Budgets

**Files:**

- Create: `scripts/check_vite_budget.mjs`
- Create: `tests/foundation/test_frontend_budget_contract.py`
- Modify: `web/src/router/index.ts`
- Modify: `vite.config.ts`
- Modify: `package.json`
- Modify: `web/e2e/performance.spec.ts`

**Interfaces:**

- Produces: `npm run build:budget`.
- Reads Vite manifest from `dist/.vite/manifest.json`.
- Fails when initial entry JavaScript exceeds 350,000 raw bytes or imports forbidden lazy feature chunks.

- [x] **Step 1: Write failing route and budget contracts**

```python
def test_every_top_level_page_is_lazy_loaded() -> None:
    source = (ROOT / "web/src/router/index.ts").read_text(encoding="utf-8")
    assert "import ReadingBriefingPage from" not in source
    assert "import ComparisonsPage from" not in source
    assert "import PublishingPage from" not in source
    assert "() => import('@/pages/" in source


def test_vite_budget_script_has_closed_entry_limits() -> None:
    source = (ROOT / "scripts/check_vite_budget.mjs").read_text(encoding="utf-8")
    assert "350_000" in source
    assert "pdfjs" in source
    assert "throw new Error" in source
```

- [x] **Step 2: Run contract and current production build**

Run:

```powershell
uv run pytest tests/foundation/test_frontend_budget_contract.py -v
npm run build
```

Expected: contract fails before the budget script exists; current entry remains above target.

- [x] **Step 3: Make all routes lazy and configure stable chunks**

Use dynamic imports for every page. Configure `manualChunks` only for stable vendor boundaries:

- `vue-core`
- `pdfjs`
- `reka`

Do not create one shared “vendor” chunk that pulls reader-only dependencies into the entry.

- [x] **Step 4: Implement manifest-based budget checks**

The Node script:

- Locates the HTML entry in the manifest.
- Traverses static imports only.
- Sums raw JavaScript bytes once.
- Fails above 350,000 bytes.
- Fails if initial closure contains `pdfjs-dist`, OCR, reader, comparison, publishing, or settings chunks.
- Prints a deterministic sorted table for review.

Add:

```json
{
  "scripts": {
    "build:budget": "npm run build && node scripts/check_vite_budget.mjs"
  }
}
```

- [x] **Step 5: Add browser performance assertions**

Playwright asserts:

- Briefing interactive p95 proxy under 800 ms on seeded local data.
- No PDF worker request before reader route.
- PDF first page under 2.5 seconds with fixture.
- Layout shift score below 0.1 for briefing and reader.

- [x] **Step 6: Verify budget and performance**

Run:

```powershell
uv run pytest tests/foundation/test_frontend_budget_contract.py -v
npm run build:budget
npx playwright test web/e2e/performance.spec.ts
```

Expected: budgets and deterministic browser performance checks pass.

- [x] **Step 7: Commit performance budgets**

```powershell
git add -- scripts/check_vite_budget.mjs tests/foundation/test_frontend_budget_contract.py web/src/router/index.ts vite.config.ts package.json web/e2e/performance.spec.ts
git commit -m "perf(web): enforce lazy loading budgets"
```

## Task 9: Add Visual and Accessibility Regression Coverage

**Files:**

- Modify: `web/e2e/foundation.spec.ts`
- Modify: `web/e2e/reader.spec.ts`
- Create: `web/e2e/visual-regression.spec.ts`
- Create: `web/e2e/visual-regression.css`
- Create: `web/e2e/visual-regression.spec.ts-snapshots/*` through Playwright.
- Modify responsive shell, onboarding path display, PDF initial fit, library action, bilingual header, and briefing landmark markup where the baseline review exposes clipping or invalid structure.

**Interfaces:**

- Produces stable screenshots for briefing, onboarding, settings overview, library, reader, Assistant, translation, and related results.
- Viewports: 1440×900, 900×900, and Pixel 7.

- [x] **Step 1: Add failing screenshot and axe assertions**

```typescript
test('quiet library shell remains stable and accessible', async ({ page }) => {
  await seedVisualFixture(page)
  await page.goto('/')
  await expect(page).toHaveScreenshot('briefing.png', {
    animations: 'disabled',
    caret: 'hide',
    maxDiffPixelRatio: 0.002,
  })
  await expect(new AxeBuilder({ page }).analyze()).resolves.toMatchObject({
    violations: [],
  })
})
```

- [x] **Step 2: Run visual tests to create reviewed baselines**

Run:

```powershell
npx playwright test web/e2e/visual-regression.spec.ts --update-snapshots
```

Expected: new snapshots generated locally for manual inspection.

- [x] **Step 3: Inspect every baseline at original resolution**

Check:

- No clipping or fixed-nav overlap.
- All mobile navigation labels visible.
- One visual theme per page.
- Focus, error, empty, partial, and loading states.
- PDF remains non-inverted in dark mode.
- Reduced-motion mode has no animated transitions.

Regenerate only snapshots whose implementation is intentionally accepted.

- [x] **Step 4: Run visual and accessibility tests without updates**

Run:

```powershell
npx playwright test web/e2e/visual-regression.spec.ts web/e2e/foundation.spec.ts web/e2e/reader.spec.ts
```

Expected: screenshots match and axe reports no violations.

Verified locally on all three projects: 24 reviewed screenshots pass without updates; the combined visual, foundation, and reader suite passes 42/42. Reader assertions use the unified Assistant contract and visible responsive controls rather than removed legacy conversation selectors.

- [x] **Step 5: Commit reviewed visual baselines**

```powershell
git add -- web/e2e/visual-regression.spec.ts web/e2e/visual-regression.spec.ts-snapshots web/e2e/foundation.spec.ts web/e2e/reader.spec.ts
git commit -m "test(web): lock responsive visual quality"
```

## Plan Completion Gate

Run:

```powershell
uv run pytest tests/assistant tests/agents tests/reader tests/foundation -v --basetemp=.tmp/pytest-m7
npm test
npm run typecheck
npm run build:budget
npx playwright test
uv run ruff check .
uv run mypy src scripts
git diff --check
git status --short
```

Expected:

- Initial JavaScript is at or below 350,000 bytes.
- PDF/OCR/reader/comparison/publishing/settings code is lazy.
- Responsive reader state is preserved.
- Legacy conversation code is absent.
- Agent and OCR modules have focused responsibilities.
- Visual and accessibility baselines pass.
- No push has occurred.

**Verified locally on 2026-08-01:**

- Pytest target set: 602 passed, 3 platform-dependent cases skipped.
- Vitest: 96 files and 242 tests passed.
- TypeScript, Ruff, and MyPy (264 source files) passed without errors.
- Initial JavaScript closure: 236,124 raw bytes; lazy-feature exclusions passed.
- Playwright: 112 passed and 8 non-desktop performance cases skipped by configuration; all 24 reviewed visual baselines matched.
- Briefing interactive p95 was 358.6 ms, PDF cold-context p95 was 630 ms, and measured layout shift was 0.
- Local `main` was used throughout and no remote push or GitHub Actions run was triggered.
