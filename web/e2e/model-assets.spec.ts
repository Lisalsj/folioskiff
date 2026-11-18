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

const semanticCurrent = {
  asset_id: 'asset-e5-current',
  capability: 'semantic',
  bundle_id: 'multilingual-e5-small',
  display_name: 'Multilingual E5 Small',
  purpose: '为中英文研究意图与论文摘要建立语义相似度；缺失时继续使用词法检索。',
  version: '1.0.0',
  revision: 'a'.repeat(40),
  current_release: true,
  download_size_bytes: 139_460_608,
  license_name: 'MIT',
  license_url: 'https://opensource.org/license/mit/?private=hidden#terms',
  copyright_notice: 'Microsoft and contributors',
  source_url: 'https://huggingface.co/intfloat/multilingual-e5-small?download=true#files',
  status: 'missing',
  integrity: 'missing',
  active: false,
  runtime_available: true,
  available: false,
  degradation_reasons: ['model_missing'],
}

const semanticOld = {
  ...semanticCurrent,
  asset_id: 'asset-e5-old',
  version: '0.9.0',
  revision: 'b'.repeat(40),
  current_release: false,
  status: 'ready',
  integrity: 'verified',
  degradation_reasons: ['model_not_enabled'],
}

const ocrCurrent = {
  asset_id: 'asset-ocr-current',
  capability: 'ocr',
  bundle_id: 'rapidocr-ppocrv5',
  display_name: 'RapidOCR 中英文移动版',
  purpose: '只识别疑似扫描页；普通文字版 PDF 不调用 OCR。',
  version: '5.0.0',
  revision: 'c'.repeat(40),
  current_release: true,
  download_size_bytes: 22_469_390,
  license_name: 'Apache-2.0',
  license_url: 'https://www.apache.org/licenses/LICENSE-2.0',
  copyright_notice: 'Baidu and RapidOCR contributors',
  source_url: 'https://www.modelscope.cn/models/RapidAI/RapidOCR',
  status: 'missing',
  integrity: 'missing',
  active: false,
  runtime_available: false,
  available: false,
  degradation_reasons: ['runtime_unavailable', 'model_missing'],
}

function jobDetail(status: string, progress: number) {
  return {
    id: 'job-model-e5',
    kind: 'models.install',
    status,
    progress,
    cancel_requested: status === 'cancelled',
    pause_requested: status === 'paused',
    payload: { asset_id: semanticCurrent.asset_id },
    result: null as Record<string, unknown> | null,
    checkpoint: { received_bytes: 18_874_368, total_bytes: semanticCurrent.download_size_bytes },
    error_code: null as string | null,
    error_message: null as string | null,
    created_at: '2026-07-14T08:00:00Z',
    updated_at: '2026-07-14T08:01:00Z',
  }
}

test('model lifecycle stays consent-first, durable, responsive, and accessible', async ({
  page,
}, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  let assets = [structuredClone(semanticCurrent), structuredClone(semanticOld), structuredClone(ocrCurrent)]
  let currentJob: ReturnType<typeof jobDetail> | undefined
  const mutations: string[] = []
  const privateCanary = 'C:\\Users\\reader\\models\\private.onnx?token=secret\nTraceback'

  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (await fulfillDefaultUserPreferences(route)) return

    if (path === '/api/v1/settings/model-assets' && method === 'GET') {
      return json(route, assets)
    }
    if (path === '/api/v1/settings/model-assets/jobs' && method === 'GET') {
      if (!currentJob) return json(route, [])
      return json(route, [{
        id: currentJob.id,
        kind: currentJob.kind,
        status: currentJob.status,
        progress: currentJob.progress,
        cancel_requested: currentJob.cancel_requested,
        pause_requested: currentJob.pause_requested,
        asset_id: String(currentJob.payload.asset_id),
        retryable: typeof currentJob.result?.retryable === 'boolean'
          ? currentJob.result.retryable
          : null,
        error_code: currentJob.error_code,
        created_at: currentJob.created_at,
        updated_at: currentJob.updated_at,
      }])
    }
    if (path === '/api/v1/jobs' && method === 'GET') {
      if (!currentJob) return json(route, [])
      const { payload, result, checkpoint, error_code, error_message, created_at, updated_at, ...summary } = currentJob
      return json(route, [summary])
    }
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

    if (
      path === '/api/v1/settings/model-assets/multilingual-e5-small/install' &&
      method === 'POST'
    ) {
      mutations.push('install')
      expect(request.headers()['idempotency-key']).toMatch(/^model-install-/)
      currentJob = jobDetail('running', 18)
      assets = assets.map((asset) => asset.asset_id === semanticCurrent.asset_id
        ? { ...asset, status: 'downloading', integrity: 'partial', degradation_reasons: ['model_download_incomplete'] }
        : asset)
      const { payload, result, checkpoint, error_code, error_message, created_at, updated_at, ...summary } = currentJob
      return json(route, summary, 202)
    }
    if (path.endsWith('/jobs/job-model-e5/pause') && method === 'POST' && currentJob) {
      mutations.push('pause')
      currentJob = { ...currentJob, status: 'paused', pause_requested: true }
      const { payload, result, checkpoint, error_code, error_message, created_at, updated_at, ...summary } = currentJob
      return json(route, summary)
    }
    if (path.endsWith('/jobs/job-model-e5/resume') && method === 'POST' && currentJob) {
      mutations.push('resume')
      const retrying = currentJob.status === 'failed'
      currentJob = {
        ...currentJob,
        status: retrying ? 'queued' : 'running',
        progress: retrying ? currentJob.progress : 63,
        pause_requested: false,
        cancel_requested: false,
        error_code: null,
        error_message: null,
        result: null,
      }
      const { payload, result, checkpoint, error_code, error_message, created_at, updated_at, ...summary } = currentJob
      return json(route, summary, 202)
    }
    if (path.endsWith('/jobs/job-model-e5/cancel') && method === 'POST' && currentJob) {
      mutations.push('cancel')
      currentJob = { ...currentJob, status: 'cancelled', cancel_requested: true, pause_requested: false }
      const { payload, result, checkpoint, error_code, error_message, created_at, updated_at, ...summary } = currentJob
      return json(route, summary)
    }
    if (
      path.endsWith(`/multilingual-e5-small/revisions/${semanticOld.revision}/deletion-preview`) &&
      method === 'GET'
    ) {
      return json(route, {
        asset_id: semanticOld.asset_id,
        bundle_id: semanticOld.bundle_id,
        revision: semanticOld.revision,
        reclaimable_bytes: 104_857_600,
        blocking_references: [],
        can_delete: true,
      })
    }
    if (
      path.endsWith(`/multilingual-e5-small/revisions/${semanticOld.revision}`) &&
      method === 'DELETE'
    ) {
      mutations.push('delete-old')
      assets = assets.filter((asset) => asset.asset_id !== semanticOld.asset_id)
      return json(route, {
        asset_id: semanticOld.asset_id,
        bundle_id: semanticOld.bundle_id,
        revision: semanticOld.revision,
        reclaimable_bytes: 104_857_600,
        blocking_references: [],
        can_delete: true,
      })
    }
    throw new Error(`Unexpected API request: ${method} ${path}`)
  })

  await page.goto('/settings/storage')
  const section = page.locator('.model-settings')
  await expect(page.getByRole('heading', { level: 2, name: '本地 AI 模型' })).toBeVisible()
  await expect(section.getByText('133.0 MB')).toBeVisible()
  await expect(section.getByText('当前继续使用词法检索')).toBeVisible()
  await expect(section.getByText('当前运行包未包含所需运行时')).toBeVisible()
  await expect(page.locator('[data-test="source-asset-e5-current"]')).toHaveAttribute(
    'href',
    'https://huggingface.co/intfloat/multilingual-e5-small',
  )
  expect(mutations).toEqual([])

  const install = page.locator('[data-test="install-asset-e5-current"]')
  await install.focus()
  await expect(install).toBeFocused()
  await page.keyboard.press('Enter')
  const installDialog = page.locator('[data-test="model-install-dialog"]')
  await expect(installDialog).toBeVisible()
  await expect(installDialog).toContainText('133.0 MB')
  await expect(installDialog).toContainText('MIT')
  expect(mutations).toEqual([])
  const confirmInstall = page.locator('[data-test="confirm-model-install"]')
  await confirmInstall.focus()
  await page.keyboard.press('Enter')
  await expect(section.getByText('进行中 · 18%')).toBeVisible()

  await page.locator('[data-test="pause-job-model-e5"]').click()
  await expect(section.getByText('已暂停 · 18%')).toBeVisible()
  await page.reload()
  await expect(page.locator('[data-test="resume-job-model-e5"]')).toBeVisible()
  await expect(page.locator('.model-settings').getByText('已暂停 · 18%')).toBeVisible()

  await page.locator('[data-test="resume-job-model-e5"]').click()
  await expect(page.locator('.model-settings').getByText('进行中 · 63%')).toBeVisible()
  await page.locator('[data-test="cancel-job-model-e5"]').click()
  await expect(page.locator('.model-settings').getByText('已取消 · 63%')).toBeVisible()

  currentJob = {
    ...jobDetail('failed', 54),
    result: { retryable: true },
    error_code: 'disk_full',
    error_message: privateCanary,
  }
  await page.reload()
  await expect(page.locator('.model-settings').getByText('可用空间不足')).toBeVisible()
  await expect(page.locator('.model-settings')).not.toContainText('private.onnx')
  await expect(page.locator('.model-settings')).not.toContainText('Traceback')
  await expect(page.locator('.model-settings')).not.toContainText('token=secret')
  await page.locator('[data-test="retry-job-model-e5"]').click()
  await expect(page.locator('.model-settings').getByText('等待下载 · 54%')).toBeVisible()

  await page.locator('[data-test="preview-delete-asset-e5-old"]').click()
  const deleteDialog = page.locator('[data-test="model-delete-dialog"]')
  await expect(deleteDialog).toContainText('100.0 MB')
  expect(mutations).not.toContain('delete-old')
  await page.locator('[data-test="confirm-model-delete"]').click()
  await expect(page.locator('[data-test="asset-asset-e5-old"]')).toHaveCount(0)
  expect(mutations).toEqual(['install', 'pause', 'resume', 'cancel', 'resume', 'delete-old'])

  await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
  const horizontalOverflow = await page.locator('.model-settings').evaluate(
    (element) => element.scrollWidth - element.clientWidth,
  )
  expect(horizontalOverflow).toBeLessThanOrEqual(1)
  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('model-assets-settings.png'), fullPage: true })

  await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' })
  const highContrastAccessibility = await new AxeBuilder({ page }).analyze()
  expect(highContrastAccessibility.violations).toEqual([])
  await page.screenshot({
    path: testInfo.outputPath('model-assets-settings-high-contrast.png'),
    fullPage: true,
  })
})
