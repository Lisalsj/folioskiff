import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Route } from '@playwright/test'

import { fulfillCompletedOnboarding, fulfillDefaultUserPreferences } from './support'

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

const ccfCatalog = {
  catalog_id: 'ccf-2022-sixth-2024-rename',
  edition: 6,
  rating_year: 2022,
  rename_revision_date: '2024-06-28',
  entry_count: 649,
  journal_count: 284,
  conference_count: 365,
  source_url: 'https://www.ccf.org.cn/Academic_Evaluation/By_category/2024-06-28/825349.shtml',
  source_pdf_sha256: '9390786c231888ad441b639bc5ae734b5809a8b86602bd85ef220f2073913bca',
}

const qualityDimensions = [
  ['citation_performance', '归一化引用表现', 25],
  ['venue_rating', '发表场所与评级', 15],
  ['author_impact', '作者公开学术影响', 10],
  ['institution_impact', '机构与实验室影响', 10],
  ['code_data_impact', '代码与数据影响', 20],
  ['research_transparency', '研究透明度', 10],
  ['publication_maturity', '发表成熟度', 10],
] as const

const qualityRule = {
  id: 'quality-default-v1',
  rule_version: 'quality-v1',
  revision: 0,
  name: '默认客观质量规则',
  builtin: true,
  active: true,
  dimensions: qualityDimensions.map(([key, label, weight]) => ({
    key,
    label,
    description: `${label}的证据说明`,
    enabled: true,
    weight_percent: weight,
  })),
  rule_sha256: 'a'.repeat(64),
  parent_version_id: null,
  created_at: null,
  preview: {
    score: 50,
    coverage: 0,
    confidence: 0,
    missing_policy: 'unknown_is_neutral_prior',
    components: qualityDimensions.map(([key, label, weight]) => ({
      key,
      label,
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

test('local rating catalogs stay source-separated and keyboard accessible', async ({ page }, testInfo) => {
  let imported = false
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
      current_version: '0.6.0', automatic_checks_enabled: false, status: 'never_checked',
      checked: false, last_checked_at: null, next_automatic_check_at: null,
      error_code: null, release: null,
    })
    if (path === '/api/v1/settings/enrichers' && method === 'GET') return json(route, [])
    if (path === '/api/v1/scores/rules/quality' && method === 'GET') {
      return json(route, qualityRule)
    }
    if (path === '/api/v1/scores/rating-catalogs/ccf' && method === 'GET') {
      return json(route, ccfCatalog)
    }
    if (path === '/api/v1/scores/rating-catalogs' && method === 'GET') return json(route, [])
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
    if (path === '/api/v1/scores/rating-catalogs/import' && method === 'POST') {
      imported = true
      return json(
        route,
        {
          id: 'catalog-jcr-e2e',
          kind: 'jcr',
          catalog_name: 'JCR local export',
          catalog_version: '2025',
          source_note: 'Licensed institutional export',
          filename: 'jcr.csv',
          file_sha256: 'a'.repeat(64),
          row_count: 1,
          imported_at: '2026-07-13T08:00:00Z',
          active: true,
        },
        201,
      )
    }
    throw new Error(`Unexpected API request: ${method} ${path}`)
  })

  await page.goto('/settings/recommendation')
  await expect(page.getByRole('heading', { level: 3, name: '本地评级目录' })).toBeVisible()
  await expect(page.getByText('CCF 2022 第六版')).toBeVisible()
  await expect(page.getByText('JCR、CAS 与新锐榜单始终独立。')).toBeVisible()

  await page.locator('[data-test="rating-name"]').fill('JCR local export')
  await page.locator('[data-test="rating-version"]').fill('2025')
  await page.locator('[data-test="rating-source-note"]').fill('Licensed institutional export')
  await page.locator('[data-test="rating-file"]').setInputFiles({
    name: 'jcr.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('venue,rating\nJournal One,Q1\n'),
  })
  await page.locator('[data-test="rating-rights"]').check()
  await page.getByRole('button', { name: '验证并导入' }).click()

  await expect(page.getByText('JCR local export', { exact: true })).toBeVisible()
  expect(imported).toBe(true)
  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('rating-catalog-settings.png'), fullPage: true })
})
