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
  capabilities: [
    {
      capability_id: 'fixture.catalog.source',
      kind: 'source_connector',
      methods: ['estimate', 'fetch_page', 'probe'],
      timeout_seconds: 30,
      ready: false,
      permissions: [
        {
          kind: 'network',
          resource: 'api.example.org',
          purpose: '读取公开的论文元数据',
          granted: false,
        },
      ],
    },
  ],
}

async function installRoutes(
  page: Page,
  onPermission: (body: unknown) => void,
): Promise<void> {
  let plugin = structuredClone(basePlugin)
  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (await fulfillDefaultUserPreferences(route)) return

    if (path === '/api/v1/plugins' && method === 'GET') return json(route, [plugin])
    if (path === '/api/v1/plugins/fixture.catalog/permissions' && method === 'POST') {
      const body = request.postDataJSON()
      onPermission(body)
      plugin = {
        ...plugin,
        status: 'ready',
        health_reason: null,
        capabilities: plugin.capabilities.map((capability) => ({
          ...capability,
          ready: true,
          permissions: capability.permissions.map((permission) => ({
            ...permission,
            granted: true,
          })),
        })),
      }
      return json(route, plugin)
    }
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
    throw new Error(`Unexpected API request: ${method} ${path}`)
  })
}

test('exact plugin permission stays keyboard-operable, responsive, and accessible', async ({
  page,
}, testInfo) => {
  let permissionBody: unknown
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await installRoutes(page, (body) => { permissionBody = body })

  await page.goto('/settings/extensions')
  const heading = page.getByRole('heading', { level: 2, name: '扩展能力' })
  await expect(heading).toBeVisible()
  await expect(page.getByText('paper-reader-fixture-catalog · 2.1.0')).toBeVisible()
  await expect(page.getByText('等待授权')).toBeVisible()
  await expect(page.getByText('api.example.org')).toBeVisible()

  const grant = page.locator('[data-test="grant-fixture.catalog-network-api.example.org"]')
  await grant.focus()
  await expect(grant).toBeFocused()
  await page.keyboard.press('Enter')

  const dialog = page.getByRole('alertdialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('只批准这一项。插件仍不能访问未列出的网络、文件或凭据。')).toBeVisible()
  await expect(dialog.getByText('读取公开的论文元数据')).toBeVisible()
  const focusIsInsideDialog = await page.evaluate(() => {
    const active = document.activeElement
    const currentDialog = document.querySelector('[role="alertdialog"]')
    return active instanceof HTMLElement && currentDialog?.contains(active) === true
  })
  expect(focusIsInsideDialog).toBe(true)

  const confirm = page.locator('[data-test="confirm-plugin-permission"]')
  await confirm.focus()
  await page.keyboard.press('Enter')
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText('已批准 api.example.org。')).toBeVisible()
  await expect(page.getByText('可以使用')).toBeVisible()
  expect(permissionBody).toEqual({
    kind: 'network',
    resource: 'api.example.org',
    decision: 'grant',
  })

  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  const horizontalOverflow = await page.locator('.plugin-settings').evaluate(
    (element) => element.scrollWidth - element.clientWidth,
  )
  expect(horizontalOverflow).toBeLessThanOrEqual(1)

  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
  await expect(page.locator('body')).not.toContainText('stderr')

  await page.screenshot({
    path: testInfo.outputPath('plugin-settings.png'),
    fullPage: true,
  })
  await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' })
  const highContrastAccessibility = await new AxeBuilder({ page }).analyze()
  expect(highContrastAccessibility.violations).toEqual([])
  await page.screenshot({
    path: testInfo.outputPath('plugin-settings-high-contrast.png'),
    fullPage: true,
  })
})
