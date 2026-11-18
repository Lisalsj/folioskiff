import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Route } from '@playwright/test'
import { resolve } from 'node:path'

import {
  fulfillCompletedOnboarding,
  fulfillDefaultUserPreferences,
  installRelatedResourcesFixture,
  installTranslationFixture,
  selectFixturePdfText,
  showReaderAssistant,
} from './support'

const FIXTURE_TIME = '2026-08-01T08:00:00Z'
const SCREENSHOT_STYLE = resolve('web/e2e/visual-regression.css')
const visualWorkId = 'work-visual-library'
const visualDocumentId = 'document-visual-library'

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

const visualDocument = {
  id: visualDocumentId,
  work_id: visualWorkId,
  content_sha256: '5'.repeat(64),
  access_status: 'open_access',
  media_type: 'application/pdf',
  completeness: 100,
  extraction_status: 'complete',
  available: true,
  byte_size: 482_190,
  original_filename: 'reliable-research-agents.pdf',
  acquisition_kind: 'source_download',
  last_accessed_at: FIXTURE_TIME,
  created_at: FIXTURE_TIME,
  is_representative: true,
}

const visualWorks = [{
  id: visualWorkId,
  title: 'Reliable Research Agents with Verifiable Evidence',
  authors: ['Lin Chen', 'Avery Singh', 'Mina Zhao'],
  abstract: 'A local-first workflow for evidence-bound research agents.',
  doi: '10.1000/visual.research',
  published_at: '2026-07-18T00:00:00Z',
  added_via: 'discovery',
  added_at: FIXTURE_TIME,
  favorite: true,
  feedback: 'like',
  pinned: true,
  reading_progress: 68,
  analysis_state: 'analyzed',
  document_count: 1,
  representative_document: visualDocument,
}, {
  id: 'work-visual-diffusion',
  title: 'Diffusion Models for Scientific Discovery',
  authors: ['Rina Park', 'David Okafor'],
  abstract: 'Evaluation protocols for diffusion-based scientific discovery.',
  doi: '10.1000/visual.diffusion',
  published_at: '2026-06-10T00:00:00Z',
  added_via: 'doi',
  added_at: '2026-07-31T08:00:00Z',
  favorite: false,
  feedback: 'none',
  pinned: false,
  reading_progress: 12,
  analysis_state: 'not_analyzed',
  document_count: 0,
  representative_document: null,
}]

const visualBriefing = {
  generated_at: FIXTURE_TIME,
  empty_state: {
    active: false,
    reason: null,
    title: null,
    description: null,
    primary_action: null,
  },
  plans: [{
    id: 'plan-visual',
    name: '可验证研究智能体',
    research_intent: '追踪有明确证据链和可复现实验的研究智能体。',
    enabled: true,
    schedule_frequency: 'weekly',
    sources: ['arxiv', 'openreview', 'ieee'],
    latest_run: {
      id: 'run-visual',
      status: 'partial',
      months: ['2026-07'],
      created_at: '2026-08-01T07:00:00Z',
      completed_at: FIXTURE_TIME,
      recommendation_set_id: 'recommendations-visual',
      recommendation_set_version: 1,
      recommendation_count: 3,
    },
  }],
  new_papers: [{
    work_id: visualWorkId,
    scored_work_id: visualWorkId,
    title: visualWorks[0]?.title,
    authors: visualWorks[0]?.authors,
    plan_id: 'plan-visual',
    run_id: 'run-visual',
    recommendation_set_id: 'recommendations-visual',
    position: 1,
    channel: 'exploitation',
    composite_score: 89,
    relevance_score: 94,
    quality_score: 84,
    coverage: 88,
    confidence: 91,
    warnings: [],
  }, {
    work_id: 'work-visual-cancer',
    scored_work_id: 'work-visual-cancer',
    title: 'Agentic Evidence Synthesis for Cancer Research',
    authors: ['Sofia Alvarez', 'Noah Williams'],
    plan_id: 'plan-visual',
    run_id: 'run-visual',
    recommendation_set_id: 'recommendations-visual',
    position: 2,
    channel: 'exploration',
    composite_score: 81,
    relevance_score: 86,
    quality_score: 78,
    coverage: 72,
    confidence: 76,
    warnings: ['机构影响数据未知，未按低质量处理'],
  }],
  important_revisions: [{
    id: 'revision-visual',
    run_id: 'run-visual',
    state: 'unread',
    work_id: 'work-visual-revision',
    title: 'Planning Agents under Distribution Shift',
    authors: ['Yuki Tanaka'],
    source: 'arxiv',
    changed_fields: ['abstract', 'document'],
    source_updated_at: '2026-07-31T00:00:00Z',
    detected_at: FIXTURE_TIME,
    has_readable_document: true,
  }],
  pending_catchups: [{
    id: 'catchup-visual',
    plan_id: 'plan-visual',
    scheduled_for: '2026-07-31T06:00:00Z',
    created_at: FIXTURE_TIME,
  }],
  active_jobs: [{
    id: 'job-visual-active',
    kind: 'metadata.enrich',
    status: 'running',
    progress: 64,
    error_code: null,
    created_at: '2026-08-01T07:55:00Z',
    updated_at: FIXTURE_TIME,
  }],
  failed_jobs: [{
    id: 'job-visual-failed',
    kind: 'discovery.run',
    status: 'failed',
    progress: 67,
    error_code: 'source_rate_limited',
    created_at: '2026-08-01T07:00:00Z',
    updated_at: FIXTURE_TIME,
  }],
}

async function installLibraryFixture(page: Page): Promise<void> {
  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    if (await fulfillDefaultUserPreferences(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/briefing' && method === 'GET') return json(route, visualBriefing)
    if (path === '/api/v1/library/works' && method === 'GET') {
      return json(route, { items: visualWorks, total: visualWorks.length, limit: 20, offset: 0 })
    }
    return json(route, { detail: `Unhandled visual fixture route: ${method} ${path}` }, 500)
  })
}

function onboardingOverview(completed: boolean) {
  return {
    state: {
      completed,
      current_step: completed ? 'complete' : 'storage_privacy',
      privacy_acknowledged_at: completed ? FIXTURE_TIME : null,
      steps: {
        storage_privacy: completed ? 'completed' : 'pending',
        agents: completed ? 'skipped' : 'pending',
        sources: completed ? 'skipped' : 'pending',
        reading_preferences: completed ? 'completed' : 'pending',
      },
      updated_at: FIXTURE_TIME,
    },
    storage: {
      data_root: 'C:\\Users\\Researcher\\Documents\\paper-reader',
      writable: true,
      state_writable: true,
      cache_writable: true,
      library_writable: true,
      state_root: 'C:\\Users\\Researcher\\Documents\\paper-reader\\state',
      cache_root: 'C:\\Users\\Researcher\\Documents\\paper-reader\\cache',
      library_root: 'C:\\Users\\Researcher\\Documents\\paper-reader\\library',
      backups_root: 'C:\\Users\\Researcher\\Documents\\paper-reader\\library\\backups',
    },
    agents: [{
      backend_id: 'codex_cli',
      display_name: 'Codex CLI',
      location: 'cloud',
      version: '0.101.0',
      probe_code: 'ready',
      recommended_action: null,
      installed: true,
      authenticated: true,
      available: true,
      supports_manual_model_id: true,
      supports_default_model: true,
      models: [{ model_id: 'fixture-model', display_name: 'Fixture model' }],
      degradation_reasons: [],
    }],
    sources: [{
      connector_id: 'arxiv',
      display_name: 'arXiv',
      enabled: true,
      credential_requirement: 'none',
      credential_configured: false,
      disabled_reason: null,
      setup_url: null,
    }, {
      connector_id: 'ieee',
      display_name: 'IEEE Xplore',
      enabled: false,
      credential_requirement: 'api_key',
      credential_configured: false,
      disabled_reason: 'missing_api_key',
      setup_url: 'https://developer.ieee.org/',
    }],
    retrieval: {
      lexical_enabled: true,
      semantic_enabled: false,
      model_state: 'not_installed',
      degradation_reasons: ['embedding_model_not_enabled'],
    },
    blocking_issues: [],
  }
}

async function installOnboardingFixture(page: Page): Promise<void> {
  const overview = onboardingOverview(false)
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (path === '/api/v1/events') return route.abort()
    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/onboarding' && method === 'GET') return json(route, overview)
    return json(route, { detail: `Unhandled onboarding visual route: ${method} ${path}` }, 500)
  })
}

async function installSettingsFixture(page: Page): Promise<void> {
  const overview = onboardingOverview(true)
  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillDefaultUserPreferences(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (path === '/api/v1/events') return route.abort()
    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/onboarding' && method === 'GET') return json(route, overview)
    if (path === '/api/v1/backups' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/updates' && method === 'GET') {
      return json(route, {
        current_version: '1.0.0',
        automatic_checks_enabled: false,
        status: 'never_checked',
        checked: false,
        last_checked_at: null,
        next_automatic_check_at: null,
        error_code: null,
        release: null,
      })
    }
    return json(route, { detail: `Unhandled settings visual route: ${method} ${path}` }, 500)
  })
}

async function settle(page: Page): Promise<void> {
  await page.evaluate(async () => { await document.fonts.ready })
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
  }))
}

async function expectReviewedSnapshot(page: Page, name: string): Promise<void> {
  await settle(page)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  const immersiveWorkspace = (
    await page.locator('[data-test="reader-workspace"], [data-test="bilingual-reader"]').count()
  ) > 0
  await expect(page).toHaveScreenshot(name, {
    animations: 'disabled',
    caret: 'hide',
    fullPage: !immersiveWorkspace,
    maxDiffPixelRatio: 0.002,
    scale: 'css',
    stylePath: SCREENSHOT_STYLE,
  })
}

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' })
})

test('reading briefing visual hierarchy remains stable', async ({ page }) => {
  await installLibraryFixture(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: '阅读简报' })).toBeVisible()
  await expect(page.locator('[data-test="briefing-attention"]')).toContainText('source_rate_limited')
  await expectReviewedSnapshot(page, 'briefing.png')
})

test('briefing controls fit journal layouts in both languages', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium', 'Exercises six explicit widths')
  await installLibraryFixture(page)
  for (const language of ['zh-CN', 'en']) {
    await page.addInitScript((value) => localStorage.setItem('paper-reader.locale', value), language)
    await page.goto('/')
    await expect(page.locator('h1')).toHaveText(language === 'en' ? 'Reading briefing' : '阅读简报')
    for (const width of [1440, 1024, 820, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 900 })
      await settle(page)
      const overflow = await page.evaluate(() =>
        document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(overflow, `${language} briefing at ${width}px`).toBeLessThanOrEqual(1)
      await expect(page.locator('label[for="briefing-quick-search"]')).toBeVisible()
      if (width > 640) continue
      const navigation = page.locator('[data-test="mobile-primary-nav"]')
      const positions = await navigation.locator('a, button').evaluateAll((items) =>
        items.map((item) => {
          const box = item.getBoundingClientRect()
          return { top: box.top, bottom: box.bottom, height: box.height }
        }))
      expect(positions).toHaveLength(4)
      for (const position of positions) {
        expect(position.top).toBe(positions[0]!.top)
        expect(position.bottom).toBeLessThanOrEqual(900)
        expect(position.height).toBeGreaterThanOrEqual(44)
      }
      const jobDetails = page.locator('.job-line summary')
      expect((await jobDetails.boundingBox())!.height).toBeGreaterThanOrEqual(44)
      const utilityButton = page.locator('[data-test="theme-toggle"]')
      await page.evaluate(() => window.scrollTo({ top: 160, behavior: 'instant' }))
      expect((await utilityButton.boundingBox())!.y, 'mobile utility buttons must scroll clear of the papers')
        .toBeLessThan(0)
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
    }
  }
})

test('first-run privacy step remains calm and legible', async ({ page }) => {
  await installOnboardingFixture(page)
  await page.goto('/')
  await expect(page).toHaveURL(/\/onboarding$/)
  await expect(page.getByRole('heading', { level: 2, name: '确认存储与隐私边界' })).toBeVisible()
  const backupPath = page.getByLabel('备份目录')
  await expect(backupPath).toContainText('paper-reader\\library\\backups')
  expect(await backupPath.evaluate((element) => element.scrollWidth - element.clientWidth))
    .toBeLessThanOrEqual(1)
  await expectReviewedSnapshot(page, 'onboarding.png')
})

test('settings overview keeps capability degradation understandable', async ({ page }) => {
  await installSettingsFixture(page)
  await page.goto('/settings/overview')
  await expect(page.getByRole('heading', { level: 2, name: '配置总览' })).toBeVisible()
  await expect(page.getByText('IEEE Xplore 尚未启用', { exact: true })).toBeVisible()
  await expectReviewedSnapshot(page, 'settings-overview.png')
})

test('library preserves readable density across viewports', async ({ page }) => {
  await installLibraryFixture(page)
  await page.goto('/library')
  await expect(page.locator(`[data-test="library-work-${visualWorkId}"]`)).toBeVisible()
  expect(await page.locator('[data-test="open-library-import"]').evaluate(
    (button) => getComputedStyle(button).whiteSpace,
  ), 'library import action must remain on one line').toBe('nowrap')
  await expectReviewedSnapshot(page, 'library.png')
})

test('reader keeps PDF paper white in dark mode', async ({ page }, testInfo) => {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' })
  const fixture = await installTranslationFixture(page)
  await page.goto(`/library/${fixture.workId}/read/${fixture.documentVersionId}`)
  const canvas = page.locator('canvas[aria-label*="第 1 页"]')
  await expect(canvas).toHaveCSS('background-color', 'rgb(255, 255, 255)', { timeout: 20_000 })
  await expect(page.locator('[data-test="pdf-text-layer"]')).toContainText('Diffusion model')
  if (testInfo.project.name !== 'desktop-chromium') {
    await expect(page.locator('[data-test="desktop-primary-nav"]')).toBeHidden()
    await expect(page.locator('[data-test="mobile-primary-nav"]')).toBeHidden()
    const horizontalClip = await page.locator('[data-test="pdf-page-shell"]').evaluate((shell) => {
      const pageBounds = shell.getBoundingClientRect()
      const viewportBounds = shell.parentElement!.getBoundingClientRect()
      return Math.max(0, pageBounds.right - viewportBounds.right)
    })
    expect(horizontalClip, 'initial PDF page must fit the reading viewport').toBeLessThanOrEqual(1)
  }
  await expectReviewedSnapshot(page, 'reader-dark.png')
})

test('reader Assistant remains distinct from PDF evidence', async ({ page }, testInfo) => {
  const fixture = await installTranslationFixture(page)
  await page.goto(`/library/${fixture.workId}/read/${fixture.documentVersionId}`)
  await expect(page.locator('[data-test="pdf-text-layer"]')).toContainText('Diffusion model')
  await showReaderAssistant(page, testInfo.project.name)
  await expect(page.locator('[data-test="research-assistant"]')).toBeVisible()
  if (testInfo.project.name === 'mobile-chromium') {
    await expect(page.locator('[data-test="mobile-primary-nav"]')).toBeHidden()
    expect(await page.evaluate(() => window.scrollY), 'mobile reader shell must not scroll behind panels')
      .toBe(0)
    expect(await page.locator('[data-test="agent-column"]').evaluate((panel) => panel.scrollTop),
      'opening the Assistant must preserve its top-level navigation').toBe(0)
    const [mobileTab, assistantHeader] = await Promise.all([
      page.locator('[data-test="mobile-assistant-tab"]').boundingBox(),
      page.locator('.assistant-header').boundingBox(),
    ])
    expect(mobileTab?.y ?? -1, 'mobile reader tabs must remain inside the viewport').toBeGreaterThanOrEqual(0)
    expect(assistantHeader?.y ?? -1, 'Assistant identity must remain below the mobile reader tabs')
      .toBeGreaterThanOrEqual((mobileTab?.y ?? 0) + (mobileTab?.height ?? 0))
    const send = await page.getByRole('button', { name: '发送', exact: true }).boundingBox()
    expect(send?.y ?? 10_000).toBeLessThan(page.viewportSize()!.height)
    expect((send?.y ?? 0) + (send?.height ?? 10_000)).toBeLessThanOrEqual(page.viewportSize()!.height)
  }
  await expectReviewedSnapshot(page, 'assistant.png')
})

test('English-to-Chinese translation keeps both evidence columns readable', async ({ page }, testInfo) => {
  const fixture = await installTranslationFixture(page)
  await page.goto(`/library/${fixture.workId}/read/${fixture.documentVersionId}`)
  await selectFixturePdfText(page, 'Diffusion model')
  await showReaderAssistant(page, testInfo.project.name)
  const assistant = page.locator('[data-test="research-assistant"]')
  await assistant.locator('[data-test="assistant-mode-translate"]').click()
  await assistant.locator('[data-test="translation-start"]').click()
  await expect(assistant.locator('[data-test="translation-task-card"]')).toContainText('已完成')
  await assistant.locator('[data-test="open-bilingual"]').click()
  const source = page.locator('[data-segment="segment-000001"] [data-side="source"]')
  const target = page.locator('[data-segment="segment-000001"] [data-side="target"]')
  await expect(source).toContainText('Diffusion model')
  await expect(target).toContainText('扩散模型')
  for (const column of [source, target]) {
    expect(await column.evaluate((element) => {
      const bounds = element.getBoundingClientRect()
      const top = document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)
      return top === element || element.contains(top)
    }), 'translation evidence columns must not be covered').toBe(true)
  }
  if (testInfo.project.name !== 'mobile-chromium') {
    const meta = page.locator('.header-meta')
    const taskButton = page.getByRole('button', { name: '查看任务进度' })
    const [metaBounds, taskBounds] = await Promise.all([meta.boundingBox(), taskButton.boundingBox()])
    expect(await meta.evaluate((element) => getComputedStyle(element).whiteSpace)).toBe('nowrap')
    expect(metaBounds!.x + metaBounds!.width, 'translation metadata must clear the task button')
      .toBeLessThanOrEqual(taskBounds!.x - 4)
  }
  if (testInfo.project.name !== 'desktop-chromium') {
    await expect(page.locator('[data-test="desktop-primary-nav"]')).toBeHidden()
    await expect(page.locator('[data-test="mobile-primary-nav"]')).toBeHidden()
  }
  await expectReviewedSnapshot(page, 'translation.png')
})

test('partial related-paper results keep provenance and recovery visible', async ({ page }, testInfo) => {
  const fixture = await installRelatedResourcesFixture(page)
  await page.goto(`/library/${fixture.workId}/read/${fixture.documentVersionId}`)
  const pdfTextLayer = page.locator('[data-test="pdf-text-layer"]')
  await expect(pdfTextLayer).toContainText('Diffusion agents require reproducible evaluation')
  await showReaderAssistant(page, testInfo.project.name)
  const assistant = page.locator('[data-test="research-assistant"]')
  await assistant.locator('[data-test="assistant-mode-related"]').click()
  await assistant.locator('[data-test="start-related"]').click()
  await assistant.locator('[data-test="confirm-related"]').click()
  await assistant.locator('[data-test="run-related"]').click()
  await expect(assistant.getByText('Verified Diffusion Agent', { exact: true })).toBeVisible()
  await expect(assistant.getByText('覆盖 67%', { exact: true })).toBeVisible()
  if (testInfo.project.name !== 'desktop-chromium') {
    await expect(page.locator('[data-test="desktop-primary-nav"]')).toBeHidden()
    await expect(page.locator('[data-test="mobile-primary-nav"]')).toBeHidden()
  }
  await expectReviewedSnapshot(page, 'related-results.png')
})
