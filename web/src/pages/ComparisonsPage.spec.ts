import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ComparisonsPage from './ComparisonsPage.vue'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const works = Array.from({ length: 12 }, (_, index) => ({
  id: `work-${index + 1}`,
  title: `Paper ${index + 1}`,
  authors: [`Author ${index + 1}`],
  abstract: `Abstract ${index + 1}`,
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

const capabilities = [{
  backend_id: 'codex_cli',
  display_name: 'Codex CLI',
  location: 'cloud',
  installed: true,
  authenticated: true,
  available: true,
  supports_schema: true,
  supports_streaming: true,
  supports_vision: true,
  supports_external_research: true,
  credential_required: false,
  credential_configured: true,
  supports_manual_model_id: true,
  supports_default_model: true,
  models: [{
    model_id: 'codex-detected-model',
    display_name: 'Detected Codex model',
    supports_vision: true,
    context_window: 200_000,
  }],
  degradation_reasons: [],
}]

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
      coverage: position === 1 ? 72 : 100,
      status: 'available',
      warnings: position === 1 ? ['full_text_unavailable'] : [],
    })),
    item_count: workIds.length,
    available_count: workIds.length,
    coverage: 86,
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

function reportSummary() {
  return {
    id: 'comparison-report-1',
    comparison_key: 'a'.repeat(64),
    version: 1,
    job_id: 'comparison-job-1',
    title: 'Agent systems comparison',
    mode: 'direct',
    backend_id: 'codex_cli',
    requested_model_id: 'fixture-model',
    resolved_model_id: 'fixture-model',
    reasoning_effort: 'high',
    language: 'zh-CN',
    coverage: 86,
    schema_version: 'comparison-report-v1',
    created_at: '2026-07-13T08:00:00Z',
    items: [],
  }
}

function report() {
  const reference = {
    work_id: 'work-2',
    evidence_id: 'abstract:work',
    kind: 'abstract',
    page_number: null,
    locator: null,
    url: null,
    external_title: null,
  }
  const statement = { text: 'The methods use different evidence controls.', references: [reference] }
  return {
    ...reportSummary(),
    service_tier: 'flex',
    complete_prompt: 'frozen',
    prompt_sha256: 'b'.repeat(64),
    content: {
      title: 'Agent systems comparison',
      overview: statement,
      axes: [{ name: 'Method', findings: [statement] }],
      agreements: [statement],
      differences: [statement],
      tradeoffs: [statement],
      limitations: [statement],
      recommendations: [statement],
    },
    rendered_markdown: '# report',
    coverage_details: { selected_count: 2, included_count: 2, excluded_count: 0, items: [] },
    provenance: { batch_count: 1 },
    request_fingerprint: 'c'.repeat(64),
    report_sha256: 'd'.repeat(64),
    items: [
      {
        id: 'item-1',
        report_id: 'comparison-report-1',
        position: 0,
        work_id: 'work-2',
        document_version_id: null,
        document_content_sha256: null,
        evidence_level: 'abstract_only',
        evidence_sha256: '1'.repeat(64),
        evidence_manifest: {},
        extraction_status: 'direct',
        extraction_id: null,
        coverage: 100,
        created_at: '2026-07-13T08:00:00Z',
      },
      {
        id: 'item-2',
        report_id: 'comparison-report-1',
        position: 1,
        work_id: 'work-1',
        document_version_id: null,
        document_content_sha256: null,
        evidence_level: 'abstract_only',
        evidence_sha256: '2'.repeat(64),
        evidence_manifest: {},
        extraction_status: 'direct',
        extraction_id: null,
        coverage: 72,
        created_at: '2026-07-13T08:00:00Z',
      },
    ],
  }
}

async function mountPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/comparisons', component: ComparisonsPage }],
  })
  await router.push('/comparisons')
  await router.isReady()
  return mount(ComparisonsPage, { global: { plugins: [router] } })
}

describe('ComparisonsPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    document.cookie = 'paper_reader_csrf=; Max-Age=0; path=/'
  })

  it('preserves library order, preflights coverage and generates only after the explicit click', async () => {
    document.cookie = 'paper_reader_csrf=csrf; path=/'
    const calls: Array<{ url: string, method: string, body?: string }> = []
    const library = [
      works[0]!,
      ...Array.from({ length: 99 }, (_, index) => ({
        ...works[0]!, id: `other-${index}`, title: `Other ${index}`,
      })),
      works[1]!,
    ]
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
      if (url.startsWith('/api/v1/library/works?')) {
        const params = new URL(url, 'http://localhost').searchParams
        const query = params.get('query') ?? ''
        const offset = Number(params.get('offset'))
        const limit = Number(params.get('limit'))
        const matches = library.filter((work) => work.title.includes(query))
        return json({ items: matches.slice(offset, offset + limit), total: matches.length, limit, offset })
      }
      if (url === '/api/v1/settings/agents') return json(capabilities)
      if (url === '/api/v1/comparisons/reports' && method === 'GET') {
        return json(calls.filter((call) => call.url === '/api/v1/comparisons/reports' && call.method === 'POST').length
          ? [reportSummary()]
          : [])
      }
      if (url === '/api/v1/comparisons/preflight' && method === 'POST') {
        const payload = JSON.parse(String(init?.body)) as { items: Array<{ work_id: string }> }
        return json(preflight(payload.items.map((item) => item.work_id)))
      }
      if (url === '/api/v1/comparisons/reports' && method === 'POST') {
        return json({
          id: 'comparison-job-1', kind: 'comparison.generate', status: 'queued', progress: 0,
          payload: {}, result: null, checkpoint: null, error_code: null, error_message: null,
          cancel_requested: false, created_at: '2026-07-13T08:00:00Z', updated_at: '2026-07-13T08:00:00Z',
        }, 202)
      }
      if (url === '/api/v1/jobs/comparison-job-1') {
        return json({
          id: 'comparison-job-1', kind: 'comparison.generate', status: 'succeeded', progress: 100,
          payload: {}, result: { report_id: 'comparison-report-1', version: 1 }, checkpoint: null,
          error_code: null, error_message: null, cancel_requested: false,
          created_at: '2026-07-13T08:00:00Z', updated_at: '2026-07-13T08:00:01Z',
        })
      }
      if (url === '/api/v1/comparisons/reports/comparison-report-1') return json(report())
      throw new Error(`Unexpected request ${method} ${url}`)
    }))

    const wrapper = await mountPage()
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('多论文比较')
    expect(wrapper.text()).toContain('尚未选择论文')
    expect(calls.some((call) => call.url === '/api/v1/settings/agents')).toBe(true)
    expect(
      wrapper.get('[data-test="comparison-model-options"] option').attributes('value'),
    ).toBe('codex-detected-model')
    expect(calls.filter((call) => call.url === '/api/v1/comparisons/preflight')).toHaveLength(0)

    await wrapper.get('[data-test="select-work-1"]').setValue(true)
    expect(wrapper.find('[data-test="select-work-2"]').exists()).toBe(false)
    await wrapper.get('input[type="search"]').setValue('Paper 2')
    await wrapper.get('form[role="search"]').trigger('submit')
    await flushPromises()
    await wrapper.get('[data-test="select-work-2"]').setValue(true)
    expect(wrapper.get('[data-test="comparison-mode-note"]').text()).toContain('一次综合')
    await wrapper.get('[data-test="move-work-2-up"]').trigger('click')
    expect(wrapper.findAll('[data-test^="selected-work-"]').map((item) => item.text())).toEqual([
      expect.stringContaining('Paper 2'),
      expect.stringContaining('Paper 1'),
    ])

    await wrapper.get('[data-test="prepare-comparison"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('证据覆盖 86%')
    expect(wrapper.text()).toContain('Paper 1')
    expect(wrapper.text()).toContain('72%')
    expect(calls.filter((call) => call.url === '/api/v1/comparisons/reports' && call.method === 'POST')).toHaveLength(0)

    await wrapper.get('[data-test="generate-comparison"]').trigger('click')
    await flushPromises()
    expect(calls.filter((call) => call.url === '/api/v1/comparisons/reports' && call.method === 'POST')).toHaveLength(1)
    expect(wrapper.text()).toContain('Agent systems comparison')
    expect(wrapper.text()).toContain('版本 1')
    expect(wrapper.get('[data-test="export-comparison-markdown"]').attributes('href')).toBe(
      '/api/v1/comparisons/reports/comparison-report-1/markdown',
    )
  })

  it('explains the staged path before an eleven-paper comparison', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/v1/library/works?')) {
        return json({ items: works, total: works.length, limit: 100, offset: 0 })
      }
      if (url === '/api/v1/settings/agents') return json(capabilities)
      if (url === '/api/v1/comparisons/reports') return json([])
      throw new Error(`Unexpected request ${url}`)
    }))
    const wrapper = await mountPage()
    await flushPromises()
    for (let index = 1; index <= 11; index += 1) {
      await wrapper.get(`[data-test="select-work-${index}"]`).setValue(true)
    }
    expect(wrapper.get('[data-test="comparison-mode-note"]').text()).toContain('逐篇提取证据')
    expect(wrapper.get('[data-test="comparison-mode-note"]').text()).toContain('每批最多 10 篇')
    expect(wrapper.text()).toContain('已选择 11 篇')
  })
})
