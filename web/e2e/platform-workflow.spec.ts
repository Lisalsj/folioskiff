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
  rule_sha256: 'a'.repeat(64),
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
  source_pdf_sha256: 'b'.repeat(64),
}

const policy = {
  automatic_interval_days: 7,
  automatic_retention: 4,
  check_interval_hours: 6,
  latest_automatic_at: '2026-07-13T08:00:00Z',
  next_automatic_at: '2026-07-20T08:00:00Z',
}

function durableJob(
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
    result: null,
    checkpoint: null,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    created_at: '2026-07-14T08:00:00Z',
    updated_at: '2026-07-14T08:00:00Z',
    ...overrides,
  }
}

const restorePreview = {
  id: 'legal-preview',
  validation_job_id: 'legal-validation',
  fingerprint: 'c'.repeat(64),
  package_sha256: 'd'.repeat(64),
  manifest_sha256: 'e'.repeat(64),
  encrypted: true,
  source_format_version: 'paper-reader-backup-v1',
  source_app_version: '0.5.0',
  source_schema_revision: '0023_backup_archives',
  object_count: 2,
  payload_bytes: 8_388_608,
  included: ['SQLite database', 'referenced lawful PDF objects'],
  excluded: ['cache', 'credentials', 'existing backups', 'logs', 'models', 'temporary files'],
  created_at: '2026-07-14T08:00:00Z',
  expires_at: '2026-07-14T08:10:00Z',
}

const basePlugin = {
  plugin_id: 'fixture.catalog',
  display_name: 'Fixture Catalog',
  distribution_name: 'paper-reader-fixture-catalog',
  distribution_version: '2.1.0',
  license: 'Apache-2.0',
  protocol_minimum: '1.0',
  protocol_maximum: '1.0',
  compatible: true,
  compatibility_reason: null,
  enabled: true,
  status: 'permissions_required',
  health_reason: 'permissions_required',
  consecutive_failures: 0,
  circuit_open_reason: null,
  last_called_at: null,
  sandbox_available: true,
  sandbox_reason: null,
  capabilities: [{
    capability_id: 'fixture.catalog.source',
    kind: 'source_connector',
    methods: ['estimate', 'fetch_page', 'probe'],
    timeout_seconds: 30,
    ready: false,
    permissions: [{
      kind: 'network',
      resource: 'api.example.org',
      purpose: '读取公开论文元数据',
      granted: false,
    }],
  }],
}

const git = {
  enabled: true,
  reason: null,
  git_available: true,
  repository_detected: true,
  repository_root: 'D:\\research\\paper-reader',
  remote_name: 'origin',
  remote_url: 'https://github.com/Lisalsj/folioskiff.git',
  branch: 'published',
  site_root: 'site',
  workflow_path: '.github/workflows/pages.yml',
  target_fingerprint: 'f'.repeat(64),
  stale_worktree_count: 0,
}

const work = {
  id: 'work-platform',
  title: 'Evidence-Grounded Research Agents',
  authors: ['Wang', 'Lin'],
  abstract: 'A public abstract.',
  doi: '10.1000/platform',
  published_at: '2026-07-01T00:00:00Z',
  added_via: 'recommendation',
  added_at: '2026-07-13T08:00:00Z',
  favorite: true,
  feedback: 'liked',
  pinned: false,
  reading_progress: 88,
  analysis_state: 'available',
  document_count: 1,
  representative_document: null,
}

const formalReport = {
  id: 'report-platform',
  work_id: work.id,
  document_version_id: 'document-platform',
  document_content_sha256: '1'.repeat(64),
  evidence_sha256: '2'.repeat(64),
  version: 2,
  job_id: 'analysis-platform',
  backend_id: 'codex_cli',
  requested_model_id: 'fixture-model',
  resolved_model_id: 'fixture-model',
  reasoning_effort: 'high',
  language: 'zh-CN',
  evidence_level: 'full_text',
  schema_version: 'analysis-report-v1',
  created_at: '2026-07-13T08:00:00Z',
}

test('platform safety loop survives refusal, corruption, push failure and retry', async ({
  page,
}, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  let plugin = structuredClone(basePlugin)
  let permissionCalls = 0
  let resetCalls = 0
  let publicationCalls = 0
  let retryCalls = 0
  let targetConfirmations = 0
  let restoreConfirmed = false
  const privateCanary = 'D:\\private\\temporary-worktree\\do-not-render.txt'

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
        bundle_id: 'rapidocr-platform',
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
    if (path === '/api/v1/backups' && method === 'POST') {
      expect(request.postDataJSON()).toEqual({
        kind: 'manual',
        encrypted: true,
        password: 'platform secret',
      })
      return json(route, durableJob('backup-platform', 'backup.create', 'succeeded', {
        result: { backup_id: 'backup-download', encrypted: true, byte_size: 4_194_304 },
      }), 202)
    }
    if (path === '/api/v1/backups/restore-packages' && method === 'POST') {
      const body = request.postData() ?? ''
      if (body.includes('corrupt.paper-reader-backup.enc')) {
        return json(route, durableJob('corrupt-validation', 'backup.restore.validate', 'failed', {
          error_code: 'invalid_package',
          error_message: privateCanary,
        }), 202)
      }
      expect(body).toContain('legal.paper-reader-backup.enc')
      return json(route, durableJob('legal-validation', 'backup.restore.validate', 'succeeded', {
        result: { preview_id: restorePreview.id },
      }), 202)
    }
    if (path === `/api/v1/backups/restore-previews/${restorePreview.id}` && method === 'GET') {
      return json(route, restorePreview)
    }
    if (path === '/api/v1/backups/restores' && method === 'POST') {
      restoreConfirmed = true
      return json(route, durableJob('restore-platform', 'backup.restore.confirm', 'pending_restart', {
        result: { pending_restart: true, restore_id: 'restore-platform-id' },
      }), 202)
    }

    if (path === '/api/v1/plugins' && method === 'GET') return json(route, [plugin])
    if (path === '/api/v1/plugins/fixture.catalog/permissions' && method === 'POST') {
      permissionCalls += 1
      expect(request.postDataJSON()).toEqual({
        kind: 'network',
        resource: 'api.example.org',
        decision: 'grant',
      })
      plugin = {
        ...plugin,
        status: 'circuit_open',
        health_reason: 'protocol_error',
        consecutive_failures: 3,
        circuit_open_reason: 'protocol_error',
        capabilities: plugin.capabilities.map((capability) => ({
          ...capability,
          ready: false,
          permissions: capability.permissions.map((permission) => ({
            ...permission,
            granted: true,
          })),
        })),
      }
      return json(route, plugin)
    }
    if (path === '/api/v1/plugins/fixture.catalog/reset' && method === 'POST') {
      resetCalls += 1
      plugin = {
        ...plugin,
        status: 'ready',
        health_reason: null,
        consecutive_failures: 0,
        circuit_open_reason: null,
        capabilities: plugin.capabilities.map((capability) => ({
          ...capability,
          ready: true,
        })),
      }
      return json(route, plugin)
    }

    if (path === '/api/v1/library/works' && method === 'GET') {
      return json(route, { items: [work], total: 1, limit: 100, offset: 0 })
    }
    if (path === '/api/v1/analysis/works/work-platform/reports' && method === 'GET') {
      return json(route, [formalReport])
    }
    if (path === '/api/v1/publishing/git' && method === 'GET') return json(route, git)
    if (path === '/api/v1/publishing/previews' && method === 'POST') {
      const selection = request.postDataJSON()
      return json(route, {
        preview: {
          selection,
          fingerprint: '3'.repeat(64),
          payload: {
            schema_version: 'paper-reader-publication-v1',
            slug: selection.slug,
            title: selection.title,
            public_intro: selection.public_intro,
            works: [{
              canonical_work_id: work.id,
              title: work.title,
              authors: work.authors,
              abstract: work.abstract,
              doi: work.doi,
              published_at: work.published_at,
              quality_score: null,
              report: {
                id: formalReport.id,
                version: formalReport.version,
                evidence_level: formalReport.evidence_level,
                language: formalReport.language,
                schema_version: formalReport.schema_version,
                report_sha256: '4'.repeat(64),
                created_at: formalReport.created_at,
                content: {},
              },
            }],
          },
        },
        git,
        files: [
          '.github/workflows/pages.yml',
          'site/data.json',
          'site/index.html',
          'site/manifest.json',
        ],
        included_fields: [
          'curated title and public introduction',
          'canonical paper title, authors, abstract, DOI and publication date',
          'latest objective quality score summary when available',
          'explicitly selected formal analysis report when available',
        ],
        excluded_fields: [
          'PDF and extracted full text',
          'annotations, private notes and reading progress',
          'paper conversations and preference events',
          'credentials',
          'complete prompts, failure drafts and model diagnostics',
          'private score evidence and source raw payloads',
          'local filesystem paths',
        ],
      })
    }
    if (path === '/api/v1/publishing/settings/confirm' && method === 'POST') {
      targetConfirmations += 1
      expect(request.postDataJSON()).toEqual({ target_fingerprint: git.target_fingerprint })
      return json(route, { confirmed: true, git })
    }
    if (path === '/api/v1/publishing/jobs' && method === 'POST') {
      publicationCalls += 1
      expect(request.headers()['idempotency-key']).toMatch(/^publication-/)
      return json(route, durableJob('publish-platform-failed', 'publishing.publish', 'failed', {
        result: {
          snapshot_id: 'snapshot-platform',
          retryable: true,
          worktree_path: privateCanary,
        },
        checkpoint: { phase: 'failed', snapshot_id: 'snapshot-platform' },
        error_code: 'push_rejected',
        error_message: privateCanary,
      }), 202)
    }
    if (path === '/api/v1/publishing/jobs/publish-platform-failed/retry' && method === 'POST') {
      retryCalls += 1
      expect(request.headers()['idempotency-key']).toMatch(/^publication-retry-/)
      return json(route, durableJob('publish-platform-success', 'publishing.publish', 'succeeded', {
        payload: { retry_of_job_id: 'publish-platform-failed' },
        result: {
          snapshot_id: 'snapshot-platform',
          snapshot_sha256: '5'.repeat(64),
          remote_name: 'origin',
          remote_url: git.remote_url,
          branch: 'published',
          commit_sha: '6'.repeat(40),
          workflow_path: git.workflow_path,
          site_root: git.site_root,
          worktree_path: privateCanary,
        },
      }), 202)
    }
    throw new Error(`Unexpected API request: ${method} ${path}`)
  })

  await page.goto('/settings/storage')
  await expect(page.getByRole('heading', { level: 2, name: '备份与恢复' })).toBeVisible()

  await page.locator('[data-test="encrypt-backup"]').check()
  await page.locator('[data-test="backup-password"]').fill('platform secret')
  await page.locator('[data-test="backup-password-confirm"]').fill('platform secret')
  await page.getByRole('button', { name: '生成便携备份' }).click()
  await expect(page.getByText('便携备份已完成。下载链接只指向本机生成的文件。')).toBeVisible()

  const restoreFile = page.locator('[data-test="restore-file"]')
  await restoreFile.setInputFiles({
    name: 'corrupt.paper-reader-backup.enc',
    mimeType: 'application/octet-stream',
    buffer: Buffer.from('corrupt'),
  })
  await page.getByRole('button', { name: '验证恢复包' }).click()
  await expect(page.getByText('恢复包已损坏或不是有效的 paper-reader 备份，不能继续恢复。')).toBeVisible()
  await expect(page.locator('[data-test="confirm-restore"]')).toHaveCount(0)
  await expect(page.locator('body')).not.toContainText(privateCanary)

  await restoreFile.setInputFiles({
    name: 'legal.paper-reader-backup.enc',
    mimeType: 'application/octet-stream',
    buffer: Buffer.from('legal encrypted backup'),
  })
  await page.getByRole('button', { name: '验证恢复包' }).click()
  await expect(page.locator('[data-test="restore-preview-heading"]')).toBeFocused()
  await page.locator('[data-test="restore-acknowledgement"]').check()
  await page.locator('[data-test="confirm-restore"]').click()
  await expect(page.getByText(/请关闭并重新打开 paper-reader/)).toBeVisible()
  expect(restoreConfirmed).toBe(true)

  await page.goto('/settings/extensions')
  await expect(page.getByRole('heading', { level: 2, name: '扩展能力' })).toBeVisible()
  const grantPermission = page.locator('[data-test="grant-fixture.catalog-network-api.example.org"]')
  await grantPermission.focus()
  await page.keyboard.press('Enter')
  let permissionDialog = page.getByRole('alertdialog')
  await expect(permissionDialog).toBeVisible()
  await permissionDialog.getByRole('button', { name: '取消' }).click()
  await expect(permissionDialog).toHaveCount(0)
  expect(permissionCalls).toBe(0)

  await grantPermission.focus()
  await page.keyboard.press('Enter')
  permissionDialog = page.getByRole('alertdialog')
  await permissionDialog.locator('[data-test="confirm-plugin-permission"]').click()
  await expect(page.getByText('已暂停保护')).toBeVisible()
  await expect(page.getByText('连续失败 3 次')).toBeVisible()
  expect(permissionCalls).toBe(1)
  await page.locator('[data-test="reset-fixture.catalog"]').click()
  await expect(page.getByText('已恢复调用')).toBeVisible()
  await expect(page.getByText('可以使用')).toBeVisible()
  expect(resetCalls).toBe(1)

  await page.goto('/publishing')
  await expect(page.getByRole('heading', { level: 1, name: '精选发布' })).toBeVisible()
  await page.locator('[data-test="select-publishing-work-1"]').check()
  await page.locator('[data-test="publishing-report-work-platform"]').selectOption(formalReport.id)
  await page.locator('[data-test="publishing-title"]').fill('平台安全精选')
  await page.locator('[data-test="publishing-intro"]').fill('只公开核对后的论文与正式报告。')
  const previewButton = page.locator('[data-test="create-publishing-preview"]')
  await previewButton.focus()
  await page.keyboard.press('Enter')

  const publicPreview = page.locator('[data-test="publishing-preview"]')
  await expect(page.locator('[data-test="publishing-preview-heading"]')).toBeFocused()
  await expect(publicPreview).toContainText('确切文件')
  await expect(publicPreview).toContainText('.github/workflows/pages.yml')
  await expect(publicPreview).toContainText('annotations, private notes and reading progress')
  await expect(publicPreview).toContainText('https://github.com/Lisalsj/folioskiff.git')
  expect(publicationCalls).toBe(0)
  expect(targetConfirmations).toBe(0)

  await page.locator('[data-test="open-publish-confirmation"]').focus()
  await page.keyboard.press('Enter')
  const publishingDialog = page.getByRole('alertdialog')
  await expect(publishingDialog).toContainText('4 个白名单文件')
  expect(publicationCalls).toBe(0)
  await publishingDialog.locator('[data-test="confirm-publishing"]').click()

  await expect(page.getByText('远端发布分支已经变化')).toBeVisible()
  await expect(page.getByText('snapshot-platform')).toBeVisible()
  await expect(page.locator('body')).not.toContainText(privateCanary)
  expect(publicationCalls).toBe(1)
  expect(targetConfirmations).toBe(1)

  const retry = page.locator('[data-test="retry-publication-publish-platform-failed"]')
  await retry.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('6'.repeat(40))).toBeVisible()
  await expect(page.getByText('已发布到 published')).toBeVisible()
  await expect(page.locator('body')).not.toContainText(privateCanary)
  expect(retryCalls).toBe(1)
  expect(targetConfirmations).toBe(2)

  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  const horizontalOverflow = await page.locator('.publishing-page').evaluate(
    (element) => element.scrollWidth - element.clientWidth,
  )
  expect(horizontalOverflow).toBeLessThanOrEqual(1)

  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
  await page.screenshot({
    path: testInfo.outputPath('platform-publication-success.png'),
    fullPage: true,
  })
})
