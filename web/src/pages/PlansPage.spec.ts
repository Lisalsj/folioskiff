import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PlansPage from './PlansPage.vue'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const plan = {
  id: 'plan-1',
  name: '可靠智能体月报',
  research_intent: '追踪论文阅读智能体',
  keyword_version_id: 'keywords-1',
  date_range: { mode: 'fixed', start_month: '2026-07', end_month: '2026-07', rolling_months: null },
  sources: ['arxiv', 'openreview'],
  source_filters: {},
  schedule: { frequency: 'weekly', weekly_day: 2, monthly_day: null },
  quota: { recommendation_limit: 20, candidate_cap: 50 },
  enabled: true,
  warnings: [],
  created_at: '2026-07-15T03:00:00Z',
}

const run = {
  id: 'run-1',
  plan_id: 'plan-1',
  job_id: 'job-1',
  keyword_version_id: 'keywords-1',
  status: 'partial_success',
  months: ['2026-07'],
  connector_versions: { arxiv: 'v1', openreview: 'v2' },
  total_batches: 2,
  succeeded_batches: 1,
  failed_batches: 1,
  cancelled_batches: 0,
  created_at: '2026-07-15T04:00:00Z',
  started_at: '2026-07-15T04:00:01Z',
  completed_at: '2026-07-15T04:03:00Z',
  batches: [
    {
      id: 'batch-ok', month: '2026-07', connector_id: 'arxiv', status: 'succeeded',
      query_fingerprint: 'a'.repeat(64), checkpoint: {}, fetched_count: 10, accepted_count: 8,
      ingested_count: 8, new_work_count: 7, update_count: 1, duplicate_count: 1,
      scanned_count: 10, attempt_count: 1, error_code: null, error_message: null,
      retryable: false, retry_after_seconds: null, completed_at: '2026-07-15T04:02:00Z',
    },
    {
      id: 'batch-failed', month: '2026-07', connector_id: 'openreview', status: 'failed',
      query_fingerprint: 'b'.repeat(64), checkpoint: {}, fetched_count: 0, accepted_count: 0,
      ingested_count: 0, new_work_count: 0, update_count: 0, duplicate_count: 0,
      scanned_count: 0, attempt_count: 1, error_code: 'rate_limited', error_message: 'retry later',
      retryable: true, retry_after_seconds: 60, completed_at: '2026-07-15T04:03:00Z',
    },
  ],
}

describe('PlansPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('restores recent partial runs, exposes results and retries only retryable batches', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      if (url === '/api/v1/plans') return json([plan])
      if (url === '/api/v1/plans/catchups') return json([])
      if (url === '/api/v1/plans/plan-1/runs?limit=3&offset=0') {
        return json({ items: [run], total: 1, limit: 3, offset: 0 })
      }
      if (url === '/api/v1/runs/run-1/retry' && init?.method === 'POST') {
        return json({ id: 'job-1', kind: 'discovery.run', status: 'queued', progress: 50, cancel_requested: false }, 202)
      }
      if (url === '/api/v1/jobs') return json([])
      throw new Error(`Unexpected request ${url}`)
    }))
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/plans', component: PlansPage },
        { path: '/plans/new', component: { template: '<p>new</p>' } },
        { path: '/runs/:runId/results', component: { template: '<p>results</p>' } },
      ],
    })
    await router.push('/plans')
    await router.isReady()
    const wrapper = mount(PlansPage, { global: { plugins: [createPinia(), router] } })
    await flushPromises()

    expect(calls.some((call) => call.includes('runs?'))).toBe(false)
    await wrapper.get('[data-test="toggle-runs-plan-1"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="recent-runs-plan-1"]').text()).toContain('部分成功')
    expect(wrapper.get('[data-test="recent-runs-plan-1"]').text()).toContain('arxiv 成功')
    expect(wrapper.get('[data-test="recent-runs-plan-1"]').text()).toContain('openreview 待重试')
    expect(wrapper.get('[data-test="recent-runs-plan-1"]').text()).toContain('获取 10 · 匹配 8')
    expect(wrapper.get('[data-test="recent-runs-plan-1"]').text()).toContain('来源限制请求频率')
    expect(wrapper.get('[data-test="view-results-run-1"]').attributes('href')).toBe(
      '/runs/run-1/results',
    )
    await wrapper.get('[data-test="retry-run-1"]').trigger('click')
    await flushPromises()
    expect(calls).toContain('POST /api/v1/runs/run-1/retry')
  })
})
