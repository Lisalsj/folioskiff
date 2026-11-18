import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Route } from '@playwright/test'

import { fulfillCompletedOnboarding, fulfillDefaultUserPreferences } from './support'

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
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

const qualityRule = {
  id: 'quality-default-v1',
  rule_version: 'quality-v1',
  revision: 0,
  name: '默认客观质量规则',
  builtin: true,
  active: true,
  dimensions: dimensions.map(([key, label, weight]) => ({
    key,
    label,
    description: `${label}的证据说明`,
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
    components: dimensions.map(([key, label, weight]) => ({
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

const ccfCatalog = {
  catalog_id: 'ccf-2022-sixth-2024-rename',
  edition: 6,
  rating_year: 2022,
  rename_revision_date: '2024-06-28',
  entry_count: 649,
  journal_count: 284,
  conference_count: 365,
  source_url: 'https://www.ccf.org.cn/Academic_Evaluation/By_category/2024-06-28/825349.shtml',
  source_pdf_sha256: 'c'.repeat(64),
}

test('reader operations keep preview-first cache cleanup and cancelled acquisition states', async ({
  page,
}, testInfo) => {
  const previewToken = 'a'.repeat(64)
  let cleanupApplied = false

  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (await fulfillDefaultUserPreferences(route)) return

    if (path === '/api/v1/jobs' && method === 'GET') {
      return json(route, [
        {
          id: 'cancelled-acquisition',
          kind: 'reader.acquire_document',
          status: 'cancelled',
          progress: 35,
          cancel_requested: true,
        },
      ])
    }
    if (path === '/api/v1/settings/model-assets' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/model-assets/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === '/api/v1/settings/sources' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/updates' && method === 'GET') return json(route, {
      current_version: '0.6.0', automatic_checks_enabled: true, status: 'never_checked',
      checked: false, last_checked_at: null, next_automatic_check_at: null,
      error_code: null, release: null,
    })
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
    if (path === '/api/v1/library/cache-cleanup/preview' && method === 'POST') {
      expect(request.postDataJSON()).toEqual({ retention_days: 90 })
      return json(route, {
        preview_token: previewToken,
        generated_at: '2026-07-13T08:00:00Z',
        retention_days: 90,
        eligible_count: 1,
        protected_count: 1,
        reclaimable_bytes: 4_194_304,
        candidates: [
          {
            content_sha256: 'b'.repeat(64),
            byte_size: 4_194_304,
            work_ids: ['work-removable'],
            age_days: 120,
            protected_reasons: [],
            eligible: true,
          },
          {
            content_sha256: 'c'.repeat(64),
            byte_size: 8_388_608,
            work_ids: ['work-protected'],
            age_days: 160,
            protected_reasons: ['favorite', 'annotation'],
            eligible: false,
          },
        ],
      })
    }
    if (path === '/api/v1/library/cache-cleanup/apply' && method === 'POST') {
      expect(request.postDataJSON()).toEqual({ preview_token: previewToken })
      cleanupApplied = true
      return json(route, {
        preview_token: previewToken,
        deleted_objects: 1,
        cleared_document_versions: 1,
        missing_objects: 0,
        reclaimed_bytes: 4_194_304,
        cancelled: false,
      })
    }
    throw new Error(`Unexpected API request: ${method} ${path}`)
  })

  await page.goto('/settings/storage')
  await expect(page.getByRole('heading', { level: 2, name: '论文全文缓存' })).toBeVisible()

  await page.getByRole('button', { name: '查看任务进度' }).click()
  await expect(page.getByText('获取论文全文')).toBeVisible()
  await expect(page.getByText('已取消')).toBeVisible()
  await expect(page.getByText('正在取消')).toHaveCount(0)
  await page.getByRole('button', { name: '关闭任务进度' }).click()

  await page.getByRole('button', { name: '先查看清理预览' }).click()
  await expect(page.getByText('1 个可移除', { exact: true })).toBeVisible()
  await expect(page.getByText('1 个受保护', { exact: true })).toBeVisible()
  await expect(page.locator('[data-test="cleanup-protected"]')).toContainText('收藏、带标注')
  await page.getByRole('button', { name: '清理 1 个可移除文件' }).click()
  await expect(page.getByText('已删除 1 个缓存文件，释放 4.0 MB')).toBeVisible()
  expect(cleanupApplied).toBe(true)

  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('reader-storage-settings.png'), fullPage: true })
})
