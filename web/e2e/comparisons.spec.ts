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

const works = Array.from({ length: 12 }, (_, index) => ({
  id: `comparison-work-${index + 1}`,
  title: `Evidence Paper ${index + 1}`,
  authors: [`Author ${index + 1}`],
  abstract: `Frozen comparison evidence ${index + 1}.`,
  doi: null,
  published_at: '2026-07-01T00:00:00Z',
  added_via: 'reference',
  added_at: '2026-07-13T08:00:00Z',
  favorite: false,
  feedback: 'neutral',
  pinned: false,
  reading_progress: 0,
  analysis_state: 'not_analyzed',
  document_count: 0,
  representative_document: null,
}))

function preflight(workIds: string[]) {
  return {
    comparison_key: 'a'.repeat(64),
    mode: workIds.length > 10 ? 'staged' : 'direct',
    items: workIds.map((workId, position) => ({
      position,
      requested_work_id: workId,
      work_id: workId,
      title: works.find((work) => work.id === workId)?.title ?? workId,
      document_version_id: null,
      document_content_sha256: null,
      evidence_level: 'abstract_only',
      evidence_sha256: String(position + 1).padStart(64, '0'),
      coverage: position === 1 ? 74 : 100,
      status: 'available',
      warnings: ['full_text_unavailable'],
    })),
    item_count: workIds.length,
    available_count: workIds.length,
    coverage: 87,
    backend_id: 'codex_cli',
    backend_display_name: 'Codex CLI',
    backend_location: 'cloud',
    cloud_backend: true,
    model_id: 'fixture-model',
    model_options: [],
    manual_model_id_allowed: true,
    default_model_allowed: true,
    reasoning_effort: 'high',
    service_tier: 'flex',
    language: 'zh-CN',
    ready: true,
    warnings: workIds.length > 10 ? ['staged_comparison'] : [],
    prompt_sha256: 'b'.repeat(64),
    preflight_fingerprint: 'c'.repeat(64),
  }
}

const reference = {
  work_id: 'comparison-work-2',
  evidence_id: 'abstract:work',
  kind: 'abstract',
  page_number: null,
  locator: null,
  url: null,
  external_title: null,
}
const statement = {
  text: '两篇论文采用不同的证据约束，但都明确披露摘要范围。',
  references: [reference],
}
const reportItems = [2, 1].map((index, position) => ({
  id: `comparison-item-${index}`,
  report_id: 'comparison-report-e2e',
  position,
  work_id: `comparison-work-${index}`,
  document_version_id: null,
  document_content_sha256: null,
  evidence_level: 'abstract_only',
  evidence_sha256: String(index).repeat(64),
  evidence_manifest: {},
  extraction_status: 'direct',
  extraction_id: null,
  coverage: position === 1 ? 74 : 100,
  created_at: '2026-07-13T08:00:00Z',
}))
const summary = {
  id: 'comparison-report-e2e',
  comparison_key: 'a'.repeat(64),
  version: 1,
  job_id: 'comparison-job-e2e',
  title: '可靠论文 Agent 对比',
  mode: 'direct',
  backend_id: 'codex_cli',
  requested_model_id: 'fixture-model',
  resolved_model_id: 'fixture-model',
  reasoning_effort: 'high',
  language: 'zh-CN',
  coverage: 87,
  schema_version: 'comparison-report-v1',
  created_at: '2026-07-13T08:00:00Z',
  items: reportItems,
}
const report = {
  ...summary,
  service_tier: 'flex',
  complete_prompt: 'frozen comparison prompt',
  prompt_sha256: 'b'.repeat(64),
  content: {
    title: '可靠论文 Agent 对比',
    overview: statement,
    axes: [{ name: '证据边界', findings: [statement] }],
    agreements: [statement],
    differences: [statement],
    tradeoffs: [statement],
    limitations: [statement],
    recommendations: [statement],
  },
  rendered_markdown: '# 可靠论文 Agent 对比',
  coverage_details: { selected_count: 2, included_count: 2, excluded_count: 0 },
  provenance: { batch_count: 1 },
  request_fingerprint: 'c'.repeat(64),
  report_sha256: 'd'.repeat(64),
}

test('comparison stays explicit, ordered, coverage-aware and accessible', async ({ page }, testInfo) => {
  let createCalls = 0
  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === '/api/v1/settings/agents' && method === 'GET') {
      return json(route, [{
        backend_id: 'codex_cli', display_name: 'Codex CLI', location: 'cloud',
        installed: true, authenticated: true, available: true, supports_schema: true,
        supports_streaming: true, supports_vision: true, supports_external_research: true,
        credential_required: false, credential_configured: true,
        supports_manual_model_id: true, supports_default_model: true,
        models: [{ model_id: 'fixture-model', display_name: 'Fixture model', supports_vision: true, context_window: 200000 }],
        degradation_reasons: [],
      }])
    }
    if (path === '/api/v1/library/works' && method === 'GET') {
      return json(route, { items: works, total: works.length, limit: 100, offset: 0 })
    }
    if (path === '/api/v1/comparisons/reports' && method === 'GET') {
      return json(route, createCalls > 0 ? [summary] : [])
    }
    if (path === '/api/v1/comparisons/preflight' && method === 'POST') {
      const payload = request.postDataJSON() as { items: Array<{ work_id: string }> }
      return json(route, preflight(payload.items.map((item) => item.work_id)))
    }
    if (path === '/api/v1/comparisons/reports' && method === 'POST') {
      createCalls += 1
      return json(route, {
        id: 'comparison-job-e2e',
        kind: 'comparison.generate',
        status: 'queued',
        progress: 0,
        payload: {},
        result: null,
        checkpoint: null,
        error_code: null,
        error_message: null,
        cancel_requested: false,
        created_at: '2026-07-13T08:00:00Z',
        updated_at: '2026-07-13T08:00:00Z',
      }, 202)
    }
    if (path === '/api/v1/jobs/comparison-job-e2e' && method === 'GET') {
      return json(route, {
        id: 'comparison-job-e2e',
        kind: 'comparison.generate',
        status: 'succeeded',
        progress: 100,
        payload: {},
        result: { report_id: 'comparison-report-e2e', version: 1 },
        checkpoint: null,
        error_code: null,
        error_message: null,
        cancel_requested: false,
        created_at: '2026-07-13T08:00:00Z',
        updated_at: '2026-07-13T08:00:01Z',
      })
    }
    if (path === '/api/v1/comparisons/reports/comparison-report-e2e') {
      return json(route, report)
    }
    return json(route, { detail: `Unhandled fixture route: ${method} ${path}` }, 500)
  })

  await page.goto('/comparisons')
  await expect(page.getByRole('heading', { level: 1, name: '多论文比较' })).toBeVisible()
  await page.locator('[data-test="select-comparison-work-1"]').check()
  await page.locator('[data-test="select-comparison-work-2"]').check()
  await expect(page.locator('[data-test="comparison-mode-note"]')).toContainText('一次综合')
  await page.locator('[data-test="move-comparison-work-2-up"]').click()
  await expect(page.locator('[data-test^="selected-comparison-work-"]').first()).toContainText(
    'Evidence Paper 2',
  )
  expect(createCalls).toBe(0)

  await page.locator('[data-test="prepare-comparison"]').click()
  await expect(page.getByRole('heading', { name: '证据预检' })).toBeVisible()
  await expect(page.getByText('证据覆盖 87%')).toBeVisible()
  await expect(page.getByText('云端后端', { exact: false })).toBeVisible()
  expect(createCalls).toBe(0)

  await page.locator('[data-test="generate-comparison"]').click()
  await expect(page.getByRole('heading', { name: '可靠论文 Agent 对比' })).toBeVisible()
  await expect(page.getByText('版本 1', { exact: false }).first()).toBeVisible()
  await expect(page.locator('[data-test="export-comparison-markdown"]')).toHaveAttribute(
    'href',
    '/api/v1/comparisons/reports/comparison-report-e2e/markdown',
  )
  expect(createCalls).toBe(1)
  await page.screenshot({
    path: testInfo.outputPath('comparison-direct-report.png'),
    fullPage: true,
  })

  for (let index = 3; index <= 11; index += 1) {
    await page.locator(`[data-test="select-comparison-work-${index}"]`).check()
  }
  await expect(page.locator('[data-test="comparison-mode-note"]')).toContainText('逐篇提取证据')
  await expect(page.locator('[data-test="comparison-mode-note"]')).toContainText('每批最多 10 篇')
  await page.screenshot({
    path: testInfo.outputPath('comparison-staged-selection.png'),
    fullPage: true,
  })

  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
})
