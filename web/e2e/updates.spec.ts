import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Route } from '@playwright/test'

import { fulfillCompletedOnboarding, fulfillDefaultUserPreferences } from './support'

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

const release = {
  version: '0.8.0',
  tag_name: 'v0.8.0',
  published_at: '2026-07-14T08:00:00Z',
  html_url: 'https://github.com/Lisalsj/folioskiff/releases/tag/v0.8.0',
  changelog: '新增只读更新检查。\n完善 Windows 便携版的启动恢复。',
  migration_notes: '升级前建议创建便携备份；首次启动会自动迁移数据库。',
  capability_matrix: {
    windows_x64: 'supported',
    installer: 'supported',
    portable: 'supported',
  },
  assets: [
    { name: 'release-manifest.json', size_bytes: 2048, sha256: 'a'.repeat(64) },
    { name: 'SHA256SUMS.txt', size_bytes: 1024, sha256: 'b'.repeat(64) },
  ],
  unsigned: true,
}

function updateSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    current_version: '0.6.0',
    automatic_checks_enabled: true,
    status: 'update_available',
    checked: false,
    last_checked_at: '2026-07-14T09:30:00Z',
    next_automatic_check_at: '2026-07-15T09:30:00Z',
    error_code: null,
    release,
    ...overrides,
  }
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

test('update review is read-only, consent-first, responsive, and accessible', async ({
  page,
}, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.addInitScript(() => {
    window.open = ((url?: string | URL, target?: string, features?: string) => {
      ;(window as unknown as { __paperReaderOpened?: unknown }).__paperReaderOpened = {
        url: String(url), target, features,
      }
      return null
    }) as typeof window.open
  })

  let current = updateSnapshot()
  const mutations: string[] = []
  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (await fulfillDefaultUserPreferences(route)) return
    if (path === '/api/v1/settings/updates' && method === 'GET') return json(route, current)
    if (path === '/api/v1/settings/updates/check' && method === 'POST') {
      mutations.push('check')
      return json(route, current)
    }
    if (path === '/api/v1/settings/updates' && method === 'PATCH') {
      mutations.push('toggle')
      current = updateSnapshot({ automatic_checks_enabled: false, status: 'disabled' })
      return json(route, current)
    }
    if (path === '/api/v1/settings/updates/cache' && method === 'DELETE') {
      mutations.push('clear')
      current = updateSnapshot({
        automatic_checks_enabled: false,
        status: 'disabled',
        release: null,
        last_checked_at: null,
        next_automatic_check_at: null,
      })
      return json(route, current)
    }
    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === '/api/v1/settings/model-assets' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/model-assets/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/sources' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/enrichers' && method === 'GET') return json(route, [])
    if (path === '/api/v1/scores/rating-catalogs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/scores/rating-catalogs/ccf' && method === 'GET') {
      return json(route, {
        catalog_id: 'ccf-2022-sixth-2024-rename', edition: 6, rating_year: 2022,
        rename_revision_date: '2024-06-28', entry_count: 649, journal_count: 284,
        conference_count: 365, source_url: 'https://www.ccf.org.cn/',
        source_pdf_sha256: 'c'.repeat(64),
      })
    }
    if (path === '/api/v1/scores/rules/quality' && method === 'GET') {
      return json(route, qualityRule)
    }
    if (path === '/api/v1/backups/policy' && method === 'GET') {
      return json(route, {
        automatic_interval_days: 7, automatic_retention: 4, check_interval_hours: 6,
        latest_automatic_at: null, next_automatic_at: null,
      })
    }
    if (path === '/api/v1/backups' && method === 'GET') return json(route, [])
    if (path === '/api/v1/plugins' && method === 'GET') return json(route, [])
    throw new Error(`Unexpected API request: ${method} ${path}`)
  })

  await page.goto('/settings/about')
  const section = page.locator('.update-settings')
  await expect(page.getByRole('heading', { level: 2, name: '应用更新' })).toBeVisible()
  await expect(section).toContainText('当前版本')
  await expect(section).toContainText('0.6.0')
  await expect(section).toContainText('0.8.0')
  await expect(section).toContainText('此版本尚未进行代码签名')
  expect(mutations).toEqual([])

  await page.locator('[data-test="review-update"]').focus()
  await page.keyboard.press('Enter')
  const dialog = page.locator('[data-test="update-confirm-dialog"]')
  await expect(dialog).toBeVisible()
  expect(await page.evaluate(() => (
    window as unknown as { __paperReaderOpened?: unknown }
  ).__paperReaderOpened)).toBeUndefined()
  await expect(dialog).toContainText('应用不会下载、执行安装器')
  await page.locator('[data-test="confirm-open-release"]').click()
  expect(await page.evaluate(() => (
    window as unknown as { __paperReaderOpened?: unknown }
  ).__paperReaderOpened)).toEqual({
    url: release.html_url,
    target: '_blank',
    features: 'noopener,noreferrer',
  })

  current = updateSnapshot({
    release: { ...release, html_url: 'https://evil.example/paper-reader.exe' },
  })
  await page.reload()
  await expect(section.getByText('发布页地址未通过本地安全校验')).toBeVisible()
  await expect(page.locator('[data-test="review-update"]')).toHaveCount(0)

  current = updateSnapshot({
    status: 'offline',
    checked: true,
    error_code: 'offline',
    release: null,
  })
  await page.locator('[data-test="check-updates"]').click()
  await expect(section.getByText('当前无法连接 GitHub')).toBeVisible()
  await expect(section).toContainText('稍后可以手动重试')
  expect(mutations).toEqual(['check'])

  await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
  expect(await section.evaluate((element) => element.scrollWidth - element.clientWidth))
    .toBeLessThanOrEqual(1)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('update-settings.png'), fullPage: true })

  await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' })
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})
