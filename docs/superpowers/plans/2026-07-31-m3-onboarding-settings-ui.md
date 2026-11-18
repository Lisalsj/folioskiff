# First-Run Wizard and Settings Information Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a skippable four-step first-run wizard and replace the monolithic settings page with an accessible, routed configuration center that exposes accurate capability diagnostics.

**Architecture:** Build onboarding and settings as feature modules that consume generated OpenAPI types through small typed clients. Keep the existing quiet-library tokens and Reka UI primitives, use route metadata for the onboarding gate, and reuse existing settings components behind focused pages rather than cloning forms.

**Tech Stack:** Vue 3, TypeScript 5.9, Vue Router, Pinia, Reka UI, Vitest, Vue Test Utils, Playwright, axe-core.

## Global Constraints

- Work on local `main`, use TDD and Conventional Commits, and do not push.
- Preserve the “quiet library” palette and typography; do not introduce another component library.
- Data directory is the only required onboarding step; Agent, source, and preference steps are skippable.
- Never render, retain, log, or return complete credentials.
- Capability probes must not call model generation or paper-search endpoints.
- Every interactive element must be keyboard reachable and pass WCAG AA contrast/focus checks.
- Desktop uses the existing main navigation; mobile uses four labeled items: 简报、计划、论文库、更多.
- Settings forms save only their own section and warn before leaving with unsaved changes.

---

## File Map

**Create**

- `web/src/features/onboarding/api.ts`: typed onboarding transport.
- `web/src/features/onboarding/store.ts`: wizard state and probe lifecycle.
- `web/src/features/onboarding/components/OnboardingProgress.vue`: accessible step progress.
- `web/src/features/onboarding/components/StoragePrivacyStep.vue`: path and privacy confirmation.
- `web/src/features/onboarding/components/AgentSetupStep.vue`: Codex/Ollama status and actions.
- `web/src/features/onboarding/components/SourceSetupStep.vue`: source status and optional credentials.
- `web/src/features/onboarding/components/ReadingPreferencesStep.vue`: language, Agent, theme, timezone.
- `web/src/pages/OnboardingPage.vue`: wizard shell.
- `web/src/pages/settings/SettingsLayoutPage.vue`: routed settings navigation.
- `web/src/pages/settings/SettingsOverviewPage.vue`: capability summary and issue links.
- `web/src/pages/settings/AgentSettingsPage.vue`: Agent models and Ollama Cloud credential.
- `web/src/pages/settings/SourcesSettingsPage.vue`: connectors and enrichers.
- `web/src/pages/settings/ReadingSettingsPage.vue`: theme, timezone, translation defaults.
- `web/src/pages/settings/RecommendationSettingsPage.vue`: scores, ratings, and retrieval.
- `web/src/pages/settings/StorageSettingsPage.vue`: storage, models, cache, backups.
- `web/src/pages/settings/ExtensionsSettingsPage.vue`: plugins and publishing boundary.
- `web/src/pages/settings/AboutSettingsPage.vue`: update, diagnostics, versions, licenses.
- Matching `*.spec.ts` files for every page/store.
- `web/e2e/onboarding.spec.ts`: first-run and skip behavior.
- `web/e2e/settings-navigation.spec.ts`: routed settings and responsive navigation.

**Modify**

- `web/src/router/index.ts`: lazy routes, onboarding gate, nested settings.
- `web/src/components/AppShell.vue`: mobile four-item navigation and “More” menu.
- `web/src/pages/SourceSettingsPage.vue`: reduce to a temporary compatibility redirect, then remove.
- `web/src/components/GeneralSettings.vue`: move into reading settings.
- `web/src/components/BackupSettings.vue`: move into storage settings.
- `web/src/components/ModelAssetSettings.vue`: move into storage settings.
- `web/src/components/PluginSettings.vue`: move into extensions settings.
- `web/src/components/ReaderStorageSettings.vue`: move into storage settings.
- `web/src/components/ScoreRuleEditor.vue`: move into recommendation settings.
- `web/src/components/UpdateSettings.vue`: move into about settings.
- `web/src/App.spec.ts`
- `web/src/components/AppShell.spec.ts`
- `web/src/styles/base.css`

## Task 1: Add a Typed Onboarding Client and Store

**Files:**

- Create: `web/src/features/onboarding/api.ts`
- Create: `web/src/features/onboarding/store.ts`
- Create: `web/src/features/onboarding/store.spec.ts`

**Interfaces:**

- Consumes: generated `OnboardingOverview`, `OnboardingStep`, `OnboardingStepUpdate`, and `DataResetRequest`.
- Produces: `useOnboardingStore()` with `load`, `probe`, `saveStep`, and `requestDataReset`.

- [ ] **Step 1: Write the failing Pinia store test**

```typescript
it('loads setup state without invoking a probe or generation endpoint', async () => {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    calls.push(String(input))
    return json(onboardingOverview({ current_step: 'storage_privacy' }))
  }))
  setActivePinia(createPinia())

  const store = useOnboardingStore()
  await store.load()

  expect(store.currentStep).toBe('storage_privacy')
  expect(store.blockingIssues).toEqual([])
  expect(calls).toEqual(['/api/v1/onboarding'])
})

it('uses a fresh idempotency key for explicit probes', async () => {
  const request = captureRequest(onboardingOverview())
  vi.stubGlobal('fetch', request.fetch)
  setActivePinia(createPinia())

  await useOnboardingStore().probe()

  expect(request.last?.url).toBe('/api/v1/onboarding/probe')
  expect(new Headers(request.last?.init.headers).get('Idempotency-Key')).toBeTruthy()
})
```

- [ ] **Step 2: Run the store test**

Run:

```powershell
npx vitest run web/src/features/onboarding/store.spec.ts
```

Expected: module-not-found failure.

- [ ] **Step 3: Implement the typed API boundary**

```typescript
type OnboardingOverview = components['schemas']['OnboardingOverview']
type OnboardingStep = components['schemas']['OnboardingStep']
type OnboardingStepUpdate = components['schemas']['OnboardingStepUpdate']

export const onboardingApi = {
  overview: () => apiJson<OnboardingOverview>('/api/v1/onboarding'),
  probe: (idempotencyKey: string) => apiJson<OnboardingOverview>(
    '/api/v1/onboarding/probe',
    { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey } },
  ),
  saveStep: (step: OnboardingStep, payload: OnboardingStepUpdate) =>
    apiJson<OnboardingOverview>(`/api/v1/onboarding/steps/${step}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
}
```

The store must expose stable loading, probing, saving, error, and overview state; it must not translate backend errors by string matching. Map stable API codes to Chinese messages in one function.

- [ ] **Step 4: Verify store behavior and strict types**

Run:

```powershell
npx vitest run web/src/features/onboarding/store.spec.ts
npm run typecheck
```

Expected: tests and strict typecheck pass.

- [ ] **Step 5: Commit the onboarding client**

```powershell
git add -- web/src/features/onboarding/api.ts web/src/features/onboarding/store.ts web/src/features/onboarding/store.spec.ts
git commit -m "feat(web): add typed onboarding state"
```

## Task 2: Build the Wizard Shell and Route Gate

**Files:**

- Create: `web/src/features/onboarding/components/OnboardingProgress.vue`
- Create: `web/src/features/onboarding/components/OnboardingProgress.spec.ts`
- Create: `web/src/pages/OnboardingPage.vue`
- Create: `web/src/pages/OnboardingPage.spec.ts`
- Modify: `web/src/router/index.ts`
- Modify: `web/src/App.spec.ts`

**Interfaces:**

- Produces: route `/onboarding`.
- Route meta: `{ title: '首次配置', publicSetup: true }`.
- Gate behavior: incomplete onboarding redirects application routes to `/onboarding`; complete onboarding redirects `/onboarding` to `/`.

- [ ] **Step 1: Write failing route and progress semantics tests**

```typescript
it('announces the current wizard step without exposing skipped steps as errors', async () => {
  const wrapper = mount(OnboardingProgress, {
    props: {
      current: 'agents',
      steps: {
        storage_privacy: 'completed',
        agents: 'pending',
        sources: 'pending',
        reading_preferences: 'pending',
      },
    },
  })

  expect(wrapper.get('ol').attributes('aria-label')).toBe('首次配置进度')
  expect(wrapper.get('[aria-current="step"]').text()).toContain('AI 能力')
})

it('redirects an incomplete profile before mounting protected pages', async () => {
  const router = createAppRouter({ onboardingStatus: async () => false })
  await router.push('/library')
  await router.isReady()
  expect(router.currentRoute.value.path).toBe('/onboarding')
})
```

- [ ] **Step 2: Run the focused tests**

Run:

```powershell
npx vitest run web/src/features/onboarding/components/OnboardingProgress.spec.ts web/src/pages/OnboardingPage.spec.ts web/src/App.spec.ts
```

Expected: failures for missing components and route guard.

- [ ] **Step 3: Implement lazy routing and one asynchronous gate**

Convert all page imports to lazy functions:

```typescript
const ReadingBriefingPage = () => import('@/pages/ReadingBriefingPage.vue')
const OnboardingPage = () => import('@/pages/OnboardingPage.vue')
```

Add a guard that:

- Loads onboarding state once through Pinia.
- Allows bootstrap/session endpoints to complete before routing.
- Never loops when the overview request fails; show a recoverable onboarding error page.
- Redirects only after state is known.

- [ ] **Step 4: Implement the quiet wizard shell**

`OnboardingPage.vue` contains:

- Product name and short privacy statement.
- `OnboardingProgress`.
- One active step component in a `<main>` landmark.
- Back, skip, and continue controls.
- No global sidebar during first-run.
- A visible “稍后配置” action for optional steps.
- Loading, API error, and retry states.

Use one paper background, forest accent, wine only for warnings, and no decorative animation.

- [ ] **Step 5: Verify route, keyboard, and reduced-motion behavior**

Run:

```powershell
npx vitest run web/src/features/onboarding/components/OnboardingProgress.spec.ts web/src/pages/OnboardingPage.spec.ts web/src/App.spec.ts
npm run typecheck
```

Expected: route gate, focus movement, retry, and progress semantics pass.

- [ ] **Step 6: Commit the wizard shell**

```powershell
git add -- web/src/features/onboarding/components/OnboardingProgress.vue web/src/features/onboarding/components/OnboardingProgress.spec.ts web/src/pages/OnboardingPage.vue web/src/pages/OnboardingPage.spec.ts web/src/router/index.ts web/src/App.spec.ts
git commit -m "feat(web): add accessible first run wizard"
```

## Task 3: Implement the Four Setup Steps

**Files:**

- Create: `web/src/features/onboarding/components/StoragePrivacyStep.vue`
- Create: `web/src/features/onboarding/components/StoragePrivacyStep.spec.ts`
- Create: `web/src/features/onboarding/components/AgentSetupStep.vue`
- Create: `web/src/features/onboarding/components/AgentSetupStep.spec.ts`
- Create: `web/src/features/onboarding/components/SourceSetupStep.vue`
- Create: `web/src/features/onboarding/components/SourceSetupStep.spec.ts`
- Create: `web/src/features/onboarding/components/ReadingPreferencesStep.vue`
- Create: `web/src/features/onboarding/components/ReadingPreferencesStep.spec.ts`
- Modify: `web/src/pages/OnboardingPage.vue`

**Interfaces:**

- Each step emits `complete(payload)` and optional steps emit `skip`.
- Storage step emits `RuntimeConfigUpdate` and privacy acknowledgement.
- Agent step consumes `AgentSetupCapability[]`.
- Source step consumes `SourceSetupCapability[]`.
- Reading step consumes existing user-preference store plus Agent defaults.

- [ ] **Step 1: Write failing behavior tests for all four steps**

```typescript
it('does not allow the required storage step to be skipped', () => {
  const wrapper = mount(StoragePrivacyStep, { props: { capability: writableStorage } })
  expect(wrapper.find('[data-test="skip-step"]').exists()).toBe(false)
  expect(wrapper.get('[data-test="confirm-storage"]').attributes('disabled')).toBeDefined()
})

it('shows invalid Codex configuration separately from login', () => {
  const wrapper = mount(AgentSetupStep, {
    props: { agents: [codexCapability({ probe_code: 'configuration_invalid' })] },
  })
  expect(wrapper.text()).toContain('Codex 配置无效')
  expect(wrapper.text()).not.toContain('尚未登录')
})

it('marks free sources usable and keyed sources optional', () => {
  const wrapper = mount(SourceSetupStep, { props: { sources: sourceCapabilities } })
  expect(wrapper.get('[data-source="arxiv"]').text()).toContain('可用')
  expect(wrapper.get('[data-source="ieee"]').text()).toContain('可稍后配置')
})
```

- [ ] **Step 2: Run all step tests**

Run:

```powershell
npx vitest run web/src/features/onboarding/components/*Step.spec.ts
```

Expected: module-not-found failures.

- [ ] **Step 3: Implement storage/privacy and Agent setup**

Storage step:

- Show resolved state, cache, library, and backup directories.
- Allow changing cache/library roots through the typed API.
- Explain that a changed root applies after controlled restart.
- Require a checkbox acknowledging local/cloud data flow.
- Expose data reset only when `reset_required` is true and require typed confirmation.

Agent step:

- Display Codex CLI and Ollama Local first.
- Show `probe_code`, version, models, and fixed recommended action.
- Provide “重新检测”.
- Never show raw stderr or a home path.
- Label Codex as “本机 CLI，模型可能在云端运行”.

- [ ] **Step 4: Implement source and reading-preference setup**

Source step:

- List free sources as ready.
- Provide password fields only for keyed sources.
- Clear secret inputs after submit.
- Link official setup pages.
- Allow the whole step to be skipped.

Reading step defaults:

```typescript
{
  report_language: 'zh-CN',
  translation_source_language: 'en',
  translation_target_language: 'zh-CN',
  default_agent_backend: 'codex_cli',
  reasoning_effort: 'medium',
  theme: 'system',
  timezone_mode: 'system'
}
```

- [ ] **Step 5: Verify emitted payloads never expose secrets**

Run:

```powershell
npx vitest run web/src/features/onboarding/components/*Step.spec.ts web/src/pages/OnboardingPage.spec.ts
npm run typecheck
```

Expected: all components pass; tests assert secret values disappear from DOM and component state after save.

- [ ] **Step 6: Commit the setup steps**

```powershell
git add -- web/src/features/onboarding/components web/src/pages/OnboardingPage.vue
git commit -m "feat(web): complete four step local setup"
```

## Task 4: Replace the Monolithic Settings Page with Routed Sections

**Files:**

- Create: `web/src/pages/settings/SettingsLayoutPage.vue`
- Create: `web/src/pages/settings/SettingsLayoutPage.spec.ts`
- Create: `web/src/pages/settings/SettingsOverviewPage.vue`
- Create: `web/src/pages/settings/SettingsOverviewPage.spec.ts`
- Create: `web/src/pages/settings/AgentSettingsPage.vue`
- Create: `web/src/pages/settings/SourcesSettingsPage.vue`
- Create: `web/src/pages/settings/ReadingSettingsPage.vue`
- Create: `web/src/pages/settings/RecommendationSettingsPage.vue`
- Create: `web/src/pages/settings/StorageSettingsPage.vue`
- Create: `web/src/pages/settings/ExtensionsSettingsPage.vue`
- Create: `web/src/pages/settings/AboutSettingsPage.vue`
- Modify: `web/src/router/index.ts`
- Modify: `web/src/pages/SourceSettingsPage.spec.ts`

**Interfaces:**

- Parent route: `/settings`.
- Child routes: `overview`, `agents`, `sources`, `reading`, `recommendation`, `storage`, `extensions`, `about`.
- `/settings` redirects to `/settings/overview`.

- [ ] **Step 1: Write failing nested-route and overview tests**

```typescript
it('renders one settings section and identifies it in the side navigation', async () => {
  const wrapper = await mountSettings('/settings/agents')

  expect(wrapper.get('nav[aria-label="设置分类"]').exists()).toBe(true)
  expect(wrapper.get('[aria-current="page"]').text()).toBe('AI 与模型')
  expect(wrapper.findComponent(AgentSettingsPage).exists()).toBe(true)
  expect(wrapper.findComponent(SourcesSettingsPage).exists()).toBe(false)
})

it('links each blocking overview issue to its exact section', async () => {
  const wrapper = mount(SettingsOverviewPage, {
    props: { overview: onboardingOverviewWithInvalidCodex },
  })
  expect(wrapper.get('[data-issue="configuration_invalid"]').attributes('href'))
    .toBe('/settings/agents#codex_cli')
})
```

- [ ] **Step 2: Run routed settings tests**

Run:

```powershell
npx vitest run web/src/pages/settings/SettingsLayoutPage.spec.ts web/src/pages/settings/SettingsOverviewPage.spec.ts
```

Expected: missing-page failures.

- [ ] **Step 3: Implement settings layout and leave guard**

`SettingsLayoutPage` must:

- Use a real `<nav>` and `<RouterView>`.
- Collapse navigation to a select/menu below 760 px.
- Track dirty state through `provideSettingsDirtyState()`.
- Use `onBeforeRouteLeave` to show a native confirmation only when dirty.
- Move focus to the child page heading after navigation.

- [ ] **Step 4: Implement overview and focused pages**

The overview displays:

- Storage readiness.
- Agent readiness.
- Free/keyed source count.
- Retrieval mode.
- Backup status.
- Update status.
- Direct links for every warning.

Focused pages compose existing components:

- Reading: `GeneralSettings`.
- Recommendation: score rules and rating catalogs.
- Storage: reader storage, model assets, backup.
- Extensions: plugins and publishing boundary.
- About: update, versions, licenses, diagnostics export.

Move source/enricher forms from `SourceSettingsPage` into `SourcesSettingsPage`; remove duplicated CSS and data loading from the old page.

- [ ] **Step 5: Verify only the active section loads**

Run:

```powershell
npx vitest run web/src/pages/settings web/src/pages/SourceSettingsPage.spec.ts web/src/components/GeneralSettings.spec.ts web/src/components/BackupSettings.spec.ts web/src/components/PluginSettings.spec.ts
npm run typecheck
```

Expected: all settings tests pass, and fetch assertions prove inactive sections do not request data.

- [ ] **Step 6: Commit routed settings**

```powershell
git add -- web/src/pages/settings web/src/router/index.ts web/src/pages/SourceSettingsPage.vue web/src/pages/SourceSettingsPage.spec.ts
git commit -m "refactor(web): split settings into focused routes"
```

## Task 5: Expose Agent Credentials and Defaults in the UI

**Files:**

- Modify: `web/src/pages/settings/AgentSettingsPage.vue`
- Create: `web/src/pages/settings/AgentSettingsPage.spec.ts`
- Modify: `src/paper_reader/user_preferences.py`
- Modify: `src/paper_reader/user_preferences_api.py`
- Create: `src/paper_reader/migrations/versions/0034_agent_language_preferences.py`
- Modify: `src/paper_reader/migrations/versions/__init__.py`
- Modify: `tests/foundation/test_user_preferences.py`
- Modify: `tests/foundation/test_migrations.py`
- Modify: `openapi/openapi.json`
- Modify: `web/src/api/schema.d.ts`

**Interfaces:**

- Extends preferences with default backend, optional model ID, reasoning effort, report language, and translation language pair.
- Uses existing `PUT/DELETE /api/v1/settings/agents/ollama-cloud/credential`.

- [ ] **Step 1: Write failing preference and credential UI tests**

```python
def test_agent_and_translation_defaults_round_trip(sessions) -> None:
    service = UserPreferencesService(sessions)
    saved = service.save(
        UserPreferencesUpdate(
            timezone_mode="system",
            timezone_name=None,
            theme="system",
            default_agent_backend="codex_cli",
            default_agent_model_id=None,
            default_reasoning_effort="medium",
            report_language="zh-CN",
            translation_source_language="en",
            translation_target_language="zh-CN",
        )
    )
    assert saved.default_agent_backend == "codex_cli"
    assert saved.translation_target_language == "zh-CN"
```

```typescript
it('stores and clears an Ollama Cloud credential without rendering it', async () => {
  const wrapper = await mountAgentSettings()
  await wrapper.get('[data-test="ollama-cloud-key"]').setValue('private-key')
  await wrapper.get('[data-test="save-ollama-cloud"]').trigger('click')
  await flushPromises()

  expect(wrapper.text()).not.toContain('private-key')
  expect((wrapper.get('[data-test="ollama-cloud-key"]').element as HTMLInputElement).value)
    .toBe('')
})
```

- [ ] **Step 2: Run backend and frontend tests**

Run:

```powershell
uv run pytest tests/foundation/test_user_preferences.py -v
npx vitest run web/src/pages/settings/AgentSettingsPage.spec.ts
```

Expected: schema fields and page behavior are missing.

- [ ] **Step 3: Extend the singleton preference schema and API**

Add closed values:

```python
DefaultAgentBackend = Literal["codex_cli", "ollama_local", "ollama_cloud"]
DefaultReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh"]
ReportLanguage = Literal["zh-CN", "en", "bilingual"]
```

Validate language tags against the supported UI set, allow a null model ID, and never place credentials in preferences.

- [ ] **Step 4: Add the preference migration**

Create `0034_agent_language_preferences`, revising `0033_onboarding_state`, with nullable/default-safe columns for the five Agent and language defaults. Update the migration registry and assert that Alembic still has exactly one head.

- [ ] **Step 5: Implement Agent settings UI**

The page includes:

- Capability cards with exact diagnostic code.
- Default backend/model/reasoning controls.
- Default report and translation languages.
- Ollama Cloud credential set/delete.
- A clear cloud badge for Codex and Ollama Cloud.
- A “重新检测” button.

- [ ] **Step 6: Regenerate schemas and verify**

Run:

```powershell
uv run pytest tests/foundation/test_user_preferences.py tests/foundation/test_migrations.py tests/agents/test_agent_settings_api.py -v
uv run python -m paper_reader.openapi --output openapi/openapi.json
npm run api:types:from-schema
npx vitest run web/src/pages/settings/AgentSettingsPage.spec.ts
npm run typecheck
```

Expected: backend, generated types, and UI tests pass.

- [ ] **Step 7: Commit Agent configuration UI**

```powershell
git add -- src/paper_reader/user_preferences.py src/paper_reader/user_preferences_api.py src/paper_reader/migrations/versions/0034_agent_language_preferences.py src/paper_reader/migrations/versions/__init__.py tests/foundation/test_user_preferences.py tests/foundation/test_migrations.py web/src/pages/settings/AgentSettingsPage.vue web/src/pages/settings/AgentSettingsPage.spec.ts openapi/openapi.json web/src/api/schema.d.ts
git commit -m "feat(settings): expose agent and language defaults"
```

## Task 6: Verify First-Run and Responsive Settings End to End

**Files:**

- Create: `web/e2e/onboarding.spec.ts`
- Create: `web/e2e/settings-navigation.spec.ts`
- Modify: `web/src/components/AppShell.vue`
- Modify: `web/src/components/AppShell.spec.ts`
- Modify: `web/src/styles/base.css`

**Interfaces:**

- Produces: mobile navigation with exactly four labeled entries.
- Produces: complete first-run path that never requires an Agent or paid source.

- [ ] **Step 1: Write failing mobile shell and Playwright scenarios**

```typescript
it('shows four labeled mobile destinations and moves secondary destinations into More', async () => {
  setViewport(390)
  const wrapper = mount(AppShell, { global: routerPlugins() })

  const items = wrapper.findAll('[data-test="mobile-primary-nav"] a, [data-test="mobile-more"]')
  expect(items.map((item) => item.text())).toEqual(['简报', '计划', '论文库', '更多'])
})
```

Playwright scenario:

```typescript
test('first run can finish with no model and no paid source key', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/onboarding/)
  await page.getByLabel('我已了解本地与云端数据边界').check()
  await page.getByRole('button', { name: '继续' }).click()
  await page.getByRole('button', { name: '稍后配置 AI' }).click()
  await page.getByRole('button', { name: '稍后配置付费来源' }).click()
  await page.getByRole('button', { name: '完成配置' }).click()
  await expect(page).toHaveURL('/')
  await expect(page.getByRole('heading', { name: '阅读简报' })).toBeVisible()
})
```

- [ ] **Step 2: Run focused UI and e2e tests**

Run:

```powershell
npx vitest run web/src/components/AppShell.spec.ts
npx playwright test web/e2e/onboarding.spec.ts web/e2e/settings-navigation.spec.ts --project=mobile-chromium
```

Expected: failures for six-icon navigation and missing end-to-end flow.

- [ ] **Step 3: Implement mobile primary navigation and More drawer**

Use:

- Four fixed labeled controls.
- A Reka dialog or popover for comparison, publishing, settings, and diagnostics.
- `aria-current="page"` for active primary routes.
- Focus return to the More button after closing.
- Safe-area padding and no overlap with reader controls.

- [ ] **Step 4: Complete e2e fixtures and accessibility checks**

Add axe assertions on:

- Every wizard step.
- Settings overview.
- Agent settings.
- Mobile More menu.

Assert that no secret value appears in page text, trace attachments, or API response fixtures.

- [ ] **Step 5: Run the complete milestone UI gate**

Run:

```powershell
npm test
npm run typecheck
npm run build
npx playwright test web/e2e/onboarding.spec.ts web/e2e/settings-navigation.spec.ts
```

Expected: all tests pass in desktop, medium, and mobile projects.

- [ ] **Step 6: Commit the responsive first-run flow**

```powershell
git add -- web/src/components/AppShell.vue web/src/components/AppShell.spec.ts web/src/styles/base.css web/e2e/onboarding.spec.ts web/e2e/settings-navigation.spec.ts
git commit -m "feat(web): finish responsive setup experience"
```

## Plan Completion Gate

Run:

```powershell
npm test
npm run typecheck
npm run build
npx playwright test web/e2e/onboarding.spec.ts web/e2e/settings-navigation.spec.ts
uv run pytest tests/onboarding tests/foundation/test_user_preferences.py tests/agents/test_agent_settings_api.py -v --basetemp=.tmp/pytest-m3
git diff --check
git status --short
```

Expected:

- A new user can finish setup with no model and no paid key.
- Codex configuration errors are rendered accurately and safely.
- Settings no longer mount as one long page.
- Mobile navigation has four visible labels.
- No push has occurred.
