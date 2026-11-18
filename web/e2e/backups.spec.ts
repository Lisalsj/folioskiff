import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Route } from '@playwright/test'

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

const policy = {
  automatic_interval_days: 7,
  automatic_retention: 4,
  check_interval_hours: 6,
  latest_automatic_at: '2026-07-13T08:00:00Z',
  next_automatic_at: '2026-07-20T08:00:00Z',
}

const preview = {
  id: 'preview-e2e',
  validation_job_id: 'restore-validation-e2e',
  fingerprint: 'f'.repeat(64),
  package_sha256: 'a'.repeat(64),
  manifest_sha256: 'b'.repeat(64),
  encrypted: true,
  source_format_version: 'paper-reader-backup-v1',
  source_app_version: '0.5.0',
  source_schema_revision: '0023_backup_archives',
  object_count: 3,
  payload_bytes: 18_874_368,
  included: ['SQLite database', 'referenced lawful PDF objects'],
  excluded: ['cache', 'credentials', 'existing backups', 'logs', 'models', 'temporary files'],
  created_at: '2026-07-14T08:00:00Z',
  expires_at: '2026-07-14T08:10:00Z',
}

function backupJob(
  id: string,
  kind: string,
  status: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    id,
    kind,
    status,
    progress: ['succeeded', 'failed', 'pending_restart'].includes(status) ? 100 : 0,
    payload: {},
    checkpoint: null,
    result: null,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    created_at: '2026-07-14T08:00:00Z',
    updated_at: '2026-07-14T08:00:00Z',
    ...overrides,
  }
}

async function installCommonRoutes(
  page: Page,
  backupHandler: (route: Route, path: string, method: string) => Promise<boolean>,
): Promise<void> {
  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()

    if (await fulfillDefaultUserPreferences(route)) return
    if (await backupHandler(route, path, method)) return
    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
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
    if (path === '/api/v1/settings/ocr' && method === 'GET') {
      return json(route, {
        available: false,
        runtime_available: false,
        models_installed: false,
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
        degradation_reasons: ['models_missing'],
      })
    }
    if (path === '/api/v1/backups/policy' && method === 'GET') return json(route, policy)
    if (path === '/api/v1/backups' && method === 'GET') return json(route, [])
    if (path === '/api/v1/plugins' && method === 'GET') return json(route, [])
    throw new Error(`Unexpected API request: ${method} ${path}`)
  })
}

test('encrypted export and preview-first restore remain calm, explicit, and accessible', async ({
  page,
}, testInfo) => {
  let backupPassword: string | undefined
  let restorePassword: string | undefined
  let restoreConfirmed = false

  await page.emulateMedia({ reducedMotion: 'reduce' })
  await installCommonRoutes(page, async (route, path, method) => {
    const request = route.request()
    if (path === '/api/v1/backups' && method === 'POST') {
      const body = request.postDataJSON()
      backupPassword = body.password
      expect(body).toEqual({
        kind: 'manual',
        encrypted: true,
        password: 'portable e2e secret',
      })
      expect(request.headers()['idempotency-key']).toMatch(/^manual-backup-/)
      await json(
        route,
        backupJob('backup-e2e', 'backup.create', 'succeeded', {
          result: { backup_id: 'download-e2e', encrypted: true, byte_size: 12_582_912 },
        }),
        202,
      )
      return true
    }
    if (path === '/api/v1/backups/restore-packages' && method === 'POST') {
      const multipart = request.postData() ?? ''
      expect(multipart).toContain('filename="portable.paper-reader-backup.enc"')
      expect(multipart).toContain('restore e2e secret')
      restorePassword = multipart.includes('restore e2e secret')
        ? 'restore e2e secret'
        : undefined
      expect(request.headers()['idempotency-key']).toMatch(/^restore-preview-/)
      await json(
        route,
        backupJob('restore-validation-e2e', 'backup.restore.validate', 'succeeded', {
          result: { preview_id: preview.id },
        }),
        202,
      )
      return true
    }
    if (path === `/api/v1/backups/restore-previews/${preview.id}` && method === 'GET') {
      await json(route, preview)
      return true
    }
    if (path === '/api/v1/backups/restores' && method === 'POST') {
      expect(request.postDataJSON()).toEqual({
        preview_id: preview.id,
        fingerprint: preview.fingerprint,
      })
      restoreConfirmed = true
      await json(
        route,
        backupJob('restore-confirm-e2e', 'backup.restore.confirm', 'pending_restart', {
          result: { pending_restart: true, restore_id: 'restore-e2e' },
        }),
        202,
      )
      return true
    }
    return false
  })

  await page.goto('/settings/storage')
  await expect(page.getByRole('heading', { level: 2, name: '备份与恢复' })).toBeVisible()
  await expect(page.getByText('每 7 天', { exact: true })).toBeVisible()
  await expect(page.getByText('最近 4 份', { exact: true })).toBeVisible()
  await expect(page.locator('[data-test="backup-privacy-boundary"]')).toContainText(
    '阅读进度、批注、分析报告、完整提示词、论文聊天和偏好',
  )
  await expect(page.locator('[data-test="backup-privacy-boundary"]')).toContainText(
    '提取文本和 OCR 全文',
  )
  await expect(page.locator('[data-test="backup-privacy-boundary"]')).toContainText(
    '脱敏失败诊断',
  )
  await expect(page.locator('[data-test="backup-encryption-notice"]')).toContainText(
    '任何拿到文件的人都能读取其中的私密研究资料',
  )

  await page.getByRole('checkbox', { name: '用密码加密便携包' }).check()
  await expect(page.locator('[data-test="backup-encryption-notice"]')).toContainText(
    '将使用你输入的密码加密',
  )
  await page.locator('[data-test="backup-password"]').fill('portable e2e secret')
  await page.locator('[data-test="backup-password-confirm"]').fill('portable e2e secret')
  await page.getByRole('button', { name: '生成便携备份' }).click()
  await expect(page.getByText('便携备份已完成。下载链接只指向本机生成的文件。')).toBeVisible()
  await expect(page.locator('[data-test="download-backup"]')).toHaveAttribute(
    'href',
    '/api/v1/backups/download-e2e/download',
  )
  await expect(page.locator('[data-test="backup-password"]')).toHaveValue('')
  await expect(page.locator('[data-test="backup-password-confirm"]')).toHaveValue('')
  expect(backupPassword).toBe('portable e2e secret')

  await page.locator('[data-test="restore-file"]').setInputFiles({
    name: 'portable.paper-reader-backup.enc',
    mimeType: 'application/octet-stream',
    buffer: Buffer.from('encrypted portable backup'),
  })
  await page.locator('[data-test="restore-password"]').fill('restore e2e secret')
  await page.getByRole('button', { name: '验证恢复包' }).click()

  const previewHeading = page.locator('[data-test="restore-preview-heading"]')
  const restorePreview = page.locator('.restore-preview')
  await expect(previewHeading).toBeFocused()
  await expect(restorePreview.getByText('数据库与 3 个 PDF 对象')).toBeVisible()
  await expect(restorePreview.getByText(/提取文本和 OCR 全文/)).toBeVisible()
  await expect(
    restorePreview.getByText(/阅读进度、批注、分析报告、完整提示词、论文聊天和偏好/),
  ).toBeVisible()
  await expect(restorePreview.getByText(/来源版本 0\.5\.0/)).toBeVisible()
  await expect(
    restorePreview.getByText('凭据、日志、模型、缓存、临时文件和旧备份'),
  ).toBeVisible()
  await expect(page.locator('[data-test="restore-password"]')).toHaveValue('')
  await expect(page.locator('[data-test="restore-file"]')).toHaveValue('')
  expect(restorePassword).toBe('restore e2e secret')

  const confirmButton = page.locator('[data-test="confirm-restore"]')
  await expect(confirmButton).toBeDisabled()
  await page.getByRole('checkbox', { name: /我已核对上述范围/ }).check()
  await expect(confirmButton).toBeEnabled()
  await confirmButton.click()
  await expect(page.getByText(/请关闭并重新打开 paper-reader/)).toBeVisible()
  await expect(confirmButton).toHaveCount(0)
  expect(restoreConfirmed).toBe(true)

  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  const horizontalOverflow = await page.locator('.backup-settings').evaluate(
    (element) => element.scrollWidth - element.clientWidth,
  )
  expect(horizontalOverflow).toBeLessThanOrEqual(1)

  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
  await page.screenshot({
    path: testInfo.outputPath('quiet-library-backup-restore.png'),
    fullPage: true,
  })
})

test('invalid restore package never reveals the destructive confirmation or private diagnostics', async ({
  page,
}) => {
  await installCommonRoutes(page, async (route, path, method) => {
    if (path === '/api/v1/backups/restore-packages' && method === 'POST') {
      await json(
        route,
        backupJob('invalid-restore-e2e', 'backup.restore.validate', 'failed', {
          error_code: 'invalid_password',
          error_message: 'D:\\private\\secret.package',
        }),
        202,
      )
      return true
    }
    return false
  })

  await page.goto('/settings/storage')
  const fileInput = page.locator('[data-test="restore-file"]')
  await fileInput.setInputFiles({
    name: 'private.paper-reader-backup.enc',
    mimeType: 'application/octet-stream',
    buffer: Buffer.from('not a valid package'),
  })
  await page.getByRole('button', { name: '验证恢复包' }).click()

  await expect(page.getByText('密码不正确，未读取或更改现有论文库。')).toBeVisible()
  await expect(page.locator('[data-test="confirm-restore"]')).toHaveCount(0)
  await expect(page.locator('body')).not.toContainText('D:\\private')
  await expect(fileInput).toBeFocused()
})
