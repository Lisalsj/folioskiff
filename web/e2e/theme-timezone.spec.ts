import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Route } from '@playwright/test'

import { fulfillCompletedOnboarding } from './support'

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

const dimensions = [
  ['citation_performance', 25],
  ['venue_rating', 15],
  ['author_impact', 10],
  ['institution_impact', 10],
  ['code_data_impact', 20],
  ['research_transparency', 10],
  ['publication_maturity', 10],
] as const

const qualityRule = {
  id: 'quality-default-v1',
  rule_version: 'quality-v1',
  revision: 0,
  name: '默认客观质量规则',
  builtin: true,
  active: true,
  dimensions: dimensions.map(([key, weight]) => ({
    key,
    label: key,
    description: key,
    enabled: true,
    weight_percent: weight,
  })),
  rule_sha256: 'd'.repeat(64),
  parent_version_id: null,
  created_at: null,
  preview: {
    score: 50,
    coverage: 0,
    confidence: 0,
    missing_policy: 'unknown_is_neutral_prior',
    components: dimensions.map(([key, weight]) => ({
      key,
      label: key,
      enabled: true,
      weight_percent: weight,
      effective_weight_percent: weight,
      status: 'unknown',
      observed_value: null,
      imputed_value: 50,
      reason: 'evidence_not_loaded',
    })),
  },
}

const ccfCatalog = {
  catalog_id: 'ccf-2022-sixth-2024-rename',
  edition: 6,
  rating_year: 2022,
  rename_revision_date: '2024-06-28',
  entry_count: 649,
  journal_count: 284,
  conference_count: 365,
  source_url: 'https://www.ccf.org.cn/',
  source_pdf_sha256: 'c'.repeat(64),
}

const updateSnapshot = {
  current_version: '0.9.0',
  automatic_checks_enabled: false,
  status: 'never_checked',
  checked: false,
  last_checked_at: null,
  next_automatic_check_at: null,
  error_code: null,
  release: null,
}

test('theme cache and timezone facts stay truthful across save and reload', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' })
  await page.addInitScript(() => {
    if (!window.sessionStorage.getItem('paper-reader.e2e-theme-seeded')) {
      window.localStorage.setItem('paper-reader.theme', 'dark')
      window.sessionStorage.setItem('paper-reader.e2e-theme-seeded', 'true')
    }
  })

  const agentDefaults = {
    default_agent_backend: 'codex_cli',
    default_agent_model_id: null,
    default_reasoning_effort: 'medium',
    report_language: 'zh-CN',
    translation_source_language: 'en',
    translation_target_language: 'zh-CN',
  } as const
  let preferences = {
    timezone_mode: 'system' as 'system' | 'custom',
    timezone_name: null as string | null,
    system_timezone: 'America/New_York',
    system_timezone_fallback: false,
    resolved_timezone: 'America/New_York',
    theme: 'system' as 'system' | 'light' | 'dark',
    ...agentDefaults,
    updated_at: '2026-07-15T08:00:00Z',
  }
  let releaseInitialResponse: (() => void) | undefined
  let markInitialRequest: (() => void) | undefined
  const initialRequest = new Promise<void>((resolve) => { markInitialRequest = resolve })
  let firstPreferencesRequest = true
  const writes: unknown[] = []

  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()

    if (path === '/api/v1/settings/preferences' && method === 'GET') {
      if (firstPreferencesRequest) {
        firstPreferencesRequest = false
        markInitialRequest?.()
        await new Promise<void>((resolve) => { releaseInitialResponse = resolve })
      }
      return json(route, preferences)
    }
    if (path === '/api/v1/settings/preferences' && method === 'PUT') {
      const body = request.postDataJSON() as {
        timezone_mode: 'system' | 'custom'
        timezone_name: string | null
        theme: 'system' | 'light' | 'dark'
      } & typeof agentDefaults
      writes.push(body)
      preferences = {
        ...preferences,
        ...body,
        resolved_timezone: body.timezone_mode === 'custom'
          ? String(body.timezone_name)
          : preferences.system_timezone,
        updated_at: '2026-07-15T08:01:00Z',
      }
      return json(route, preferences)
    }
    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === '/api/v1/settings/model-assets' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/model-assets/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/sources' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/enrichers' && method === 'GET') return json(route, [])
    if (path === '/api/v1/scores/rating-catalogs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/scores/rating-catalogs/ccf' && method === 'GET') {
      return json(route, ccfCatalog)
    }
    if (path === '/api/v1/scores/rules/quality' && method === 'GET') {
      return json(route, qualityRule)
    }
    if (path === '/api/v1/backups/policy' && method === 'GET') {
      return json(route, {
        automatic_interval_days: 7,
        automatic_retention: 4,
        check_interval_hours: 6,
        latest_automatic_at: null,
        next_automatic_at: null,
      })
    }
    if (path === '/api/v1/backups' && method === 'GET') return json(route, [])
    if (path === '/api/v1/plugins' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/updates' && method === 'GET') {
      return json(route, updateSnapshot)
    }
    throw new Error(`Unexpected API request: ${method} ${path}`)
  })

  await page.goto('/settings/reading')
  await initialRequest
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme))
    .toBe('dark')
  expect(await page.evaluate(() => window.localStorage.getItem('paper-reader.theme'))).toBe('dark')

  releaseInitialResponse?.()
  await expect(page.getByRole('heading', { level: 2, name: '时区与主题' })).toBeVisible()
  await expect(page.locator('.general-settings')).toContainText('America/New_York')
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme))
    .toBe('system')

  await page.locator('[data-test="timezone-custom"]').check()
  await page.locator('[data-test="timezone-name"]').fill('Europe/Berlin')
  await page.locator('[data-test="theme-light"]').check()
  await page.locator('[data-test="save-general-settings"]').click()
  await expect(page.locator('[data-test="general-settings-status"]')).toContainText('已保存')
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe('light')
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.body).backgroundColor))
    .toBe('rgb(248, 247, 242)')

  await page.locator('[data-test="theme-dark"]').check()
  await page.locator('[data-test="save-general-settings"]').click()
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe('dark')
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.body).backgroundColor))
    .toBe('rgb(24, 32, 25)')

  await page.locator('[data-test="timezone-system"]').check()
  await page.locator('[data-test="theme-system"]').check()
  await page.locator('[data-test="save-general-settings"]').click()
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe('system')
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.body).backgroundColor))
    .toBe('rgb(24, 32, 25)')
  expect(writes).toEqual([
    {
      timezone_mode: 'custom',
      timezone_name: 'Europe/Berlin',
      theme: 'light',
      ...agentDefaults,
    },
    {
      timezone_mode: 'custom',
      timezone_name: 'Europe/Berlin',
      theme: 'dark',
      ...agentDefaults,
    },
    {
      timezone_mode: 'system',
      timezone_name: null,
      theme: 'system',
      ...agentDefaults,
    },
  ])

  await page.reload()
  await expect(page.getByRole('heading', { level: 2, name: '时区与主题' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('paper-reader.theme')))
    .toBe('system')
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe('system')
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})
