import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Route } from '@playwright/test'

import { fulfillCompletedOnboarding, fulfillDefaultUserPreferences } from './support'

async function json(route: Route, body: unknown): Promise<void> {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
}

const dimensions = [
  ['citation_performance', '归一化引用表现', 25],
  ['venue_rating', '发表场所与评级', 15],
  ['author_impact', '作者公开学术影响', 10],
  ['institution_impact', '机构与实验室影响', 10],
  ['code_data_impact', '代码与数据影响', 20],
  ['research_transparency', '研究透明度', 10],
  ['publication_maturity', '发表成熟度', 10],
] as const

function scoreRule(custom: boolean) {
  const configured = dimensions.map(([key, label, weight]) => ({
    key,
    label,
    description: `${label}的证据说明`,
    enabled: custom ? key !== 'institution_impact' : true,
    weight_percent: custom
      ? key === 'citation_performance'
        ? 35
        : key === 'institution_impact'
          ? 0
          : weight
      : weight,
  }))
  return {
    id: custom ? 'custom-rule' : 'quality-default-v1',
    rule_version: custom ? 'quality-v1:custom' : 'quality-v1',
    revision: custom ? 1 : 0,
    name: custom ? '偏重归一化引用' : '默认客观质量规则',
    builtin: !custom,
    active: true,
    dimensions: configured,
    rule_sha256: 'a'.repeat(64),
    parent_version_id: null,
    created_at: custom ? '2026-07-13T08:00:00Z' : null,
    preview: {
      score: 50,
      coverage: 0,
      confidence: 0,
      missing_policy: 'unknown_is_neutral_prior',
      components: configured.map((item) => ({
        key: item.key,
        label: item.label,
        enabled: item.enabled,
        weight_percent: item.weight_percent,
        effective_weight_percent: item.weight_percent,
        status: 'unknown',
        observed_value: null,
        imputed_value: 50,
        reason: 'evidence_not_loaded',
      })),
    },
  }
}

test('quality rule evidence stays server-authored and accessible', async ({ page }, testInfo) => {
  let custom = false
  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (await fulfillDefaultUserPreferences(route)) return
    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/model-assets' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/model-assets/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === '/api/v1/settings/ocr' && method === 'GET') {
      return json(route, {
        available: true,
        runtime_available: true,
        models_installed: true,
        bundle_id: 'rapidocr-e2e',
        display_name: 'RapidOCR 中英文模型',
        version: '1.0.0',
        total_size: 22_469_390,
        license_name: 'Apache-2.0',
        license_url: 'https://www.apache.org/licenses/LICENSE-2.0',
        copyright_notice: 'RapidOCR contributors',
        source_url: 'https://github.com/RapidAI/RapidOCR',
        source_revision: 'v1.0.0',
        files: [],
        degradation_reasons: [],
      })
    }
    if (path === '/api/v1/settings/sources' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/updates' && method === 'GET') return json(route, {
      current_version: '0.6.0', automatic_checks_enabled: true, status: 'never_checked',
      checked: false, last_checked_at: null, next_automatic_check_at: null,
      error_code: null, release: null,
    })
    if (path === '/api/v1/settings/enrichers' && method === 'GET') return json(route, [])
    if (path === '/api/v1/scores/rating-catalogs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/scores/rating-catalogs/ccf' && method === 'GET') {
      return json(route, {
        catalog_id: 'ccf-2022-sixth-2024-rename',
        edition: 6,
        rating_year: 2022,
        rename_revision_date: '2024-06-28',
        entry_count: 649,
        journal_count: 284,
        conference_count: 365,
        source_url: 'https://example.test/ccf',
        source_pdf_sha256: 'b'.repeat(64),
      })
    }
    if (path === '/api/v1/scores/rules/quality' && method === 'GET') {
      return json(route, scoreRule(custom))
    }
    if (path === '/api/v1/scores/rules/quality' && method === 'PUT') {
      const payload = request.postDataJSON()
      expect(payload.dimensions).toEqual(
        expect.arrayContaining([
          { key: 'citation_performance', enabled: true, weight_percent: 35 },
          { key: 'institution_impact', enabled: false, weight_percent: 0 },
        ]),
      )
      custom = true
      return json(route, scoreRule(true))
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
    throw new Error(`Unexpected API request: ${method} ${path}`)
  })

  await page.goto('/settings/recommendation')
  await expect(page.getByRole('heading', { level: 2, name: '客观质量规则' })).toBeVisible()
  await expect(page.getByText('未知证据使用中性 50，不等于低质量')).toBeVisible()
  await expect(page.locator('[data-test^="score-rule-row-"]')).toHaveCount(7)

  await page.locator('[data-test="score-rule-name"]').fill('偏重归一化引用')
  await page.locator('[data-test="score-rule-enabled-institution_impact"]').uncheck()
  await page.locator('[data-test="score-rule-weight-citation_performance"]').fill('35')
  await expect(page.locator('[data-test="score-rule-total"]')).toContainText('100%')
  await page.getByRole('button', { name: '保存为新修订' }).click()

  await expect(page.getByText('自定义修订 1')).toBeVisible()
  await expect(page.getByText('规则已保存为不可变修订')).toBeVisible()
  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('score-rule-evidence.png'), fullPage: true })
})
