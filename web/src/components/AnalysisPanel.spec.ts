import { createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { components } from '@/api/schema'
import { useJobStore } from '@/stores/jobs'

import AnalysisPanel from './AnalysisPanel.vue'

type Capability = components['schemas']['AgentCapabilityResponse']
type Preflight = components['schemas']['AnalysisPreflight']
type Report = components['schemas']['AnalysisReportResponse']
type ReportSummary = components['schemas']['AnalysisReportSummaryResponse']

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const capability: Capability = {
  backend_id: 'codex_cli',
  display_name: 'Codex CLI',
  location: 'cloud',
  probe_code: 'ready',
  recommended_action: null,
  installed: true,
  authenticated: true,
  available: true,
  supports_schema: true,
  supports_streaming: true,
  supports_vision: true,
  supports_external_research: true,
  credential_required: false,
  credential_configured: false,
  supports_manual_model_id: true,
  supports_default_model: true,
  models: [{
    model_id: 'fixture-model',
    display_name: 'Fixture model',
    supports_vision: true,
    context_window: 128_000,
  }],
  degradation_reasons: [],
}

const preflight: Preflight = {
  work_id: 'work-1',
  document_version_id: 'document-1',
  document_content_sha256: 'a'.repeat(64),
  evidence_level: 'full_text',
  evidence_sha256: 'b'.repeat(64),
  coverage: 100,
  backend_id: 'codex_cli',
  backend_display_name: 'Codex CLI',
  backend_location: 'cloud',
  cloud_backend: true,
  model_id: 'fixture-model',
  model_options: capability.models,
  manual_model_id_allowed: true,
  default_model_allowed: true,
  reasoning_effort: 'high',
  service_tier: 'flex',
  language: 'zh-CN',
  template_version_id: 'builtin-general-v1',
  template_name: '通用论文分析',
  external_research: false,
  scope: ['pages:1', 'annotations:0', 'evidence:full_text'],
  warnings: [],
  ready: true,
  prompt_sha256: 'c'.repeat(64),
  preflight_fingerprint: 'd'.repeat(64),
}

function summary(id: string, version: number, jobId: string): ReportSummary {
  return {
    id,
    work_id: 'work-1',
    version,
    job_id: jobId,
    document_version_id: 'document-1',
    document_content_sha256: 'a'.repeat(64),
    evidence_level: 'full_text',
    evidence_sha256: 'b'.repeat(64),
    backend_id: 'codex_cli',
    requested_model_id: 'fixture-model',
    resolved_model_id: 'fixture-model',
    reasoning_effort: 'high',
    language: 'zh-CN',
    schema_version: 'analysis-report-v1',
    created_at: `2026-07-13T0${version}:00:00Z`,
  }
}

const reference = {
  evidence_id: 'page:1:native',
  kind: 'page' as const,
  page_number: 1,
  locator: null,
  url: null,
  external_title: null,
}

function report(id: string, version: number, jobId: string, title: string): Report {
  const statement = { text: `${title} 的证据结论。`, references: [reference] }
  return {
    ...summary(id, version, jobId),
    evidence_manifest: { pages: [{ evidence_id: 'page:1:native', page_number: 1 }] },
    service_tier: 'flex',
    template_version_id: 'builtin-general-v1',
    complete_prompt: 'frozen prompt',
    prompt_sha256: 'c'.repeat(64),
    network_policy: 'local_evidence_only',
    content: {
      title,
      overview: statement,
      research_question: statement,
      author_claims: [statement],
      contributions: [statement],
      method: [statement],
      assumptions: [statement],
      data_and_experiments: [statement],
      findings: [statement],
      limitations: [statement],
      reproducibility: [statement],
      relation_to_user_intent: [statement],
      agent_judgments: [statement],
    },
    rendered_markdown: `# ${title}`,
    external_citations: [],
    request_fingerprint: 'd'.repeat(64),
    report_sha256: 'e'.repeat(64),
  }
}

function analysisJob(status = 'queued') {
  return {
    id: 'analysis-job-1',
    kind: 'analysis.report',
    status,
    progress: status === 'succeeded' ? 100 : 5,
    payload: {
      work_id: 'work-1',
      document_version_id: 'document-1',
    },
    result: status === 'succeeded' ? { report_id: 'report-1', version: 1 } : null,
    checkpoint: null,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    created_at: '2026-07-13T08:00:00Z',
    updated_at: '2026-07-13T08:00:00Z',
  }
}

describe('AnalysisPanel', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    window.localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('loads an honest empty report state and makes zero create calls before confirmation', async () => {
    const calls: Array<{ url: string, init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/v1/analysis/works/work-1/reports') return json([])
      if (url === '/api/v1/jobs') return json([])
      if (url === '/api/v1/settings/agents') return json([capability])
      if (url === '/api/v1/analysis/works/work-1/preflight') return json(preflight)
      throw new Error(`Unexpected request ${url} ${init?.method ?? 'GET'}`)
    }))
    const wrapper = mount(AnalysisPanel, {
      attachTo: document.body,
      props: {
        workId: 'work-1',
        documentVersionId: 'document-1',
        documentContentSha256: 'a'.repeat(64),
      },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('尚未分析这篇论文')
    expect(wrapper.get('[data-test="open-analysis-preflight"]').text()).toContain(
      '分析这篇论文',
    )
    expect(calls.filter((call) => (
      call.url.endsWith('/reports') && call.init?.method === 'POST'
    ))).toHaveLength(0)

    await wrapper.get('[data-test="open-analysis-preflight"]').trigger('click')
    await flushPromises()
    expect(document.body.textContent).toContain('分析前预检')
    expect(calls.filter((call) => (
      call.url.endsWith('/reports') && call.init?.method === 'POST'
    ))).toHaveLength(0)
    wrapper.unmount()
  })

  it('auto-opens an abstract-only preflight after the user analysis click without creating a report', async () => {
    const calls: Array<{ url: string, init?: RequestInit }> = []
    const abstractPreflight: Preflight = {
      ...preflight,
      document_version_id: null,
      document_content_sha256: null,
      evidence_level: 'abstract_only',
      coverage: 35,
      scope: ['abstract', 'annotations:0', 'evidence:abstract'],
      warnings: ['全文尚不可用；报告只使用摘要证据。'],
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/v1/analysis/works/work-1/reports') return json([])
      if (url === '/api/v1/jobs') return json([])
      if (url === '/api/v1/settings/agents') return json([capability])
      if (url === '/api/v1/analysis/works/work-1/preflight' && init?.method === 'POST') {
        expect(JSON.parse(String(init.body))).toMatchObject({ document_version_id: null })
        return json(abstractPreflight)
      }
      throw new Error(`Unexpected request ${url} ${init?.method ?? 'GET'}`)
    }))
    const wrapper = mount(AnalysisPanel, {
      attachTo: document.body,
      props: { workId: 'work-1', autoOpenPreflight: true },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    expect(document.body.textContent).toContain('分析前预检')
    expect(document.body.textContent).toContain('仅摘要证据')
    expect(calls.filter((call) => (
      call.url.endsWith('/reports') && call.init?.method === 'POST'
    ))).toHaveLength(0)
    wrapper.unmount()
  })

  it('starts explicitly, streams persisted fragments, cancels, switches versions and exports', async () => {
    const pinia = createPinia()
    const calls: Array<{ url: string, init?: RequestInit }> = []
    const firstSummary = summary('report-1', 1, 'analysis-job-1')
    const secondSummary = summary('report-2', 2, 'analysis-job-2')
    let availableReports: ReportSummary[] = []
    let secondReportReadable = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/v1/analysis/works/work-1/reports' && init?.method === 'POST') {
        return json(analysisJob(), 202)
      }
      if (url === '/api/v1/analysis/works/work-1/reports') return json(availableReports)
      if (url === '/api/v1/jobs') return json([])
      if (url === '/api/v1/settings/agents') return json([capability])
      if (url === '/api/v1/analysis/works/work-1/preflight') return json(preflight)
      if (url === '/api/v1/analysis/jobs/analysis-job-1/fragments') {
        return json([{
          job_id: 'analysis-job-1',
          sequence: 0,
          kind: 'text',
          text: '正在核对论文证据',
          created_at: '2026-07-13T08:00:01Z',
        }])
      }
      if (url === '/api/v1/jobs/analysis-job-1/cancel' && init?.method === 'POST') {
        return json({ ...analysisJob('running'), cancel_requested: true })
      }
      if (url === '/api/v1/analysis/reports/report-1') {
        return json(report('report-1', 1, 'analysis-job-1', '第一版分析'))
      }
      if (url === '/api/v1/analysis/reports/report-2') {
        if (!secondReportReadable) return json({ detail: 'temporarily unavailable' }, 503)
        return json(report('report-2', 2, 'analysis-job-2', '第二版分析'))
      }
      throw new Error(`Unexpected request ${url} ${init?.method ?? 'GET'}`)
    }))
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    const wrapper = mount(AnalysisPanel, {
      attachTo: document.body,
      props: {
        workId: 'work-1',
        documentVersionId: 'document-1',
        documentContentSha256: 'a'.repeat(64),
      },
      global: { plugins: [pinia] },
    })
    await flushPromises()

    await wrapper.get('[data-test="open-analysis-preflight"]').trigger('click')
    await flushPromises()
    document.querySelector<HTMLButtonElement>('[data-test="start-analysis"]')?.click()
    await flushPromises()

    const createCall = calls.find((call) => (
      call.url.endsWith('/reports') && call.init?.method === 'POST'
    ))
    expect(createCall).toBeDefined()
    expect(new Headers(createCall?.init?.headers).get('Idempotency-Key')).toBeTruthy()
    expect(wrapper.text()).toContain('分析任务已开始')

    const jobs = useJobStore(pinia)
    jobs.jobs = [{
      id: 'analysis-job-1',
      kind: 'analysis.report',
      status: 'running',
      progress: 36,
      cancel_requested: false,
      pause_requested: false,
    }]
    await flushPromises()
    expect(wrapper.text()).toContain('正在核对论文证据')
    await wrapper.get('[data-test="cancel-analysis"]').trigger('click')
    await flushPromises()
    expect(calls.some((call) => call.url.endsWith('/cancel'))).toBe(true)

    availableReports = [secondSummary, firstSummary]
    jobs.jobs = [{
      id: 'analysis-job-1',
      kind: 'analysis.report',
      status: 'succeeded',
      progress: 100,
      cancel_requested: false,
      pause_requested: false,
    }]
    await flushPromises()
    expect(wrapper.text()).toContain('第一版分析')
    expect(wrapper.get('[data-test="report-markdown-export"]').attributes('href')).toBe(
      '/api/v1/analysis/reports/report-1/markdown',
    )
    expect(wrapper.get('[data-test="report-html-export"]').attributes('href')).toBe(
      '/api/v1/analysis/reports/report-1/html',
    )
    expect(wrapper.text()).toContain('页 1')

    await wrapper.get('[data-test="report-version"]').setValue('report-2')
    await flushPromises()
    expect(wrapper.text()).toContain('第一版分析')
    expect(wrapper.get('[data-test="report-load-warning"]').text()).toContain(
      '所选报告暂时无法读取',
    )
    secondReportReadable = true
    await wrapper.get('[data-test="report-version"]').setValue('report-1')
    await flushPromises()
    await wrapper.get('[data-test="report-version"]').setValue('report-2')
    await flushPromises()
    expect(wrapper.text()).toContain('第二版分析')
    expect(wrapper.find('[data-test="report-load-warning"]').exists()).toBe(false)
    await wrapper.get('[data-test="print-report"]').trigger('click')
    expect(open).toHaveBeenCalledWith(
      '/api/v1/analysis/reports/report-2/print',
      '_blank',
      'noopener,noreferrer',
    )
    wrapper.unmount()
  })

  it('keeps an old report while exposing a validation failure and retrying via preflight', async () => {
    const pinia = createPinia()
    const calls: Array<{ url: string, init?: RequestInit }> = []
    window.localStorage.setItem(
      'paper-reader.analysis-job.work-1.document-1',
      'analysis-job-failed',
    )
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/v1/analysis/works/work-1/reports') {
        return json([summary('report-old', 1, 'analysis-job-old')])
      }
      if (url === '/api/v1/analysis/reports/report-old') {
        return json(report('report-old', 1, 'analysis-job-old', '仍然可用的旧报告'))
      }
      if (url === '/api/v1/jobs') return json([])
      if (url === '/api/v1/jobs/analysis-job-failed') {
        return json({
          ...analysisJob('failed'),
          id: 'analysis-job-failed',
          progress: 100,
          error_code: 'invalid_output',
          error_message: 'Agent output did not satisfy the report contract',
        })
      }
      if (url === '/api/v1/analysis/jobs/analysis-job-failed/fragments') return json([])
      if (url === '/api/v1/analysis/jobs/analysis-job-failed/failure') {
        return json({
          job_id: 'analysis-job-failed',
          work_id: 'work-1',
          error_category: 'invalid_output',
          error_message: 'Agent output did not satisfy the report contract',
          raw_response: '',
          validation_errors: ['missing findings'],
          repair_count: 1,
          request_fingerprint: 'd'.repeat(64),
          created_at: '2026-07-13T09:00:00Z',
        })
      }
      if (url === '/api/v1/settings/agents') return json([capability])
      if (url === '/api/v1/analysis/works/work-1/preflight') return json(preflight)
      throw new Error(`Unexpected request ${url} ${init?.method ?? 'GET'}`)
    }))
    const wrapper = mount(AnalysisPanel, {
      attachTo: document.body,
      props: {
        workId: 'work-1',
        documentVersionId: 'document-1',
        documentContentSha256: 'a'.repeat(64),
      },
      global: { plugins: [pinia] },
    })
    await flushPromises()
    const jobs = useJobStore(pinia)
    jobs.jobs = [{
      id: 'analysis-job-failed',
      kind: 'analysis.report',
      status: 'failed',
      progress: 100,
      cancel_requested: false,
      pause_requested: false,
    }]
    await flushPromises()

    expect(wrapper.text()).toContain('结构化报告未通过验证')
    expect(wrapper.text()).toContain('仍然可用的旧报告')
    expect(wrapper.text()).toContain('已尝试自动修复 1 次')
    await wrapper.get('[data-test="retry-analysis"]').trigger('click')
    await flushPromises()
    expect(document.body.textContent).toContain('分析前预检')
    expect(calls.filter((call) => (
      call.url.endsWith('/reports') && call.init?.method === 'POST'
    ))).toHaveLength(0)
    wrapper.unmount()
  })
})
