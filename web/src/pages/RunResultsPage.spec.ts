import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import RunResultsPage from './RunResultsPage.vue'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const score = (value: number, coverage: number, confidence: number, id: string) => ({
  snapshot_id: id,
  rule_version: `${id}-v1`,
  score: value,
  coverage,
  confidence,
})

function paper(id: string, historical = false) {
  return {
    item_id: `item-${id}`,
    work_id: `work-${id}`,
    scored_work_id: `scored-${id}`,
    title: historical ? 'A Previously Read Agent Paper' : 'Reliable Agent Recommendations',
    authors: ['Alice Chen'],
    doi: null,
    sources: ['arxiv'],
    source_updated_at: '2026-07-14T00:00:00Z',
    default_position: historical ? null : 1,
    selected: !historical,
    historical_match: historical,
    eligible_by_default: true,
    channel: historical ? null : 'exploitation',
    scores: {
      relevance: score(91, 100, 90, `rel-${id}`),
      quality: score(74, 65, 68, `quality-${id}`),
      freshness: score(80, 100, 100, `fresh-${id}`),
      preference: score(66, 70, 61, `pref-${id}`),
      composite: score(82, 74, 72, `composite-${id}`),
    },
    evidence_summary: {
      keyword_eligible: true,
      preference_suppressed: false,
      eligible_by_default: true,
      historical_match: historical,
      ineligibility_reasons: [],
      exploration_reasons: [],
      integrity_status: 'clear',
      integrity_coverage: 100,
      integrity_warning_codes: [],
    },
    warnings: [],
    library_state: {
      work_id: `work-${id}`,
      favorite: false,
      feedback: 'neutral',
      feedback_reasons: [],
      pinned: false,
      reading_progress: historical ? 100 : 0,
      analysis_state: 'not_analyzed',
      persisted: historical,
      created_at: historical ? '2026-06-01T00:00:00Z' : null,
      updated_at: null,
    },
  }
}

function recommendation(view: string) {
  return {
    run_id: 'run-1',
    keyword_version_id: 'keywords-1',
    recommendation_set_id: 'set-1',
    recommendation_set_version: 2,
    set_sha256: 'a'.repeat(64),
    view,
    available_views: ['composite', 'relevance', 'quality', 'newest', 'preference'],
    fixed_historical_months: false,
    requested_limit: 20,
    candidate_cap: 50,
    candidate_count: 28,
    recommendation_count: 1,
    exploration_target: 4,
    exploration_count: 0,
    degradation_reasons: ['semantic_index_unavailable'],
    created_at: '2026-07-15T04:00:00Z',
    items: [paper('1')],
    historical_matches: [paper('history', true)],
  }
}

async function mountPage(fetcher: typeof fetch) {
  vi.stubGlobal('fetch', fetcher)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/runs/:runId/results', component: RunResultsPage },
      { path: '/plans', component: { template: '<p>plans</p>' } },
      { path: '/plans/new', component: { template: '<p>new plan</p>' } },
    ],
  })
  await router.push('/runs/run-1/results')
  await router.isReady()
  return mount(RunResultsPage, {
    global: {
      plugins: [createPinia(), router],
      stubs: {
        AnalysisPanel: {
          props: ['workId', 'documentVersionId', 'documentContentSha256', 'autoOpenPreflight'],
          template: '<div data-test="analysis-panel-stub">analysis {{ workId }}</div>',
        },
      },
    },
  })
}

describe('RunResultsPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it.each([true, false])('explains zero candidates and source failures (detailed counts: %s)', async (detailed) => {
    const calls: string[] = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      if (url.startsWith('/api/v1/runs/run-1/recommendations?')) {
        return json({ ...recommendation('composite'), candidate_count: 0, recommendation_count: 0, items: [], historical_matches: [], degradation_reasons: ['insufficient_eligible_candidates'] })
      }
      if (url === '/api/v1/runs/run-1') {
        return json({
          id: 'run-1', plan_id: 'plan-1', status: 'partial_success', months: ['2026-09'], failed_batches: 1,
          batches: [
            {
              id: 'arxiv-1', month: '2026-09', connector_id: 'arxiv', status: 'succeeded',
              fetched_count: 32, accepted_count: 0, checkpoint: { stop_reason: 'source_exhausted' },
              filter_counts: detailed ? { evaluated_count: 32, missing_updated_at_count: 0, outside_date_range_count: 31, in_date_range_count: 1, keyword_mismatch_count: 1, accepted_count: 0 } : null,
            },
            {
              id: 'openreview-1', month: '2026-09', connector_id: 'openreview', status: 'failed',
              fetched_count: 0, accepted_count: 0, checkpoint: {}, filter_counts: null,
              error_code: 'permanent_request', error_message: 'Source rejected request: HTTP 400', retryable: false,
            },
          ],
        })
      }
      throw new Error(`Unexpected request ${url}`)
    })
    const wrapper = await mountPage(fetcher)
    await flushPromises()

    const details = wrapper.get('[data-test="discovery-details"]')
    expect(details.text()).toContain('部分成功')
    expect(details.text()).toContain('1 个来源批次失败')
    expect(details.text()).toContain('来源拒绝检索参数')
    expect(details.text()).toContain('重新评分只处理已有候选')
    expect(wrapper.text()).toContain('符合推荐条件的候选不足')
    expect(wrapper.text()).not.toContain('insufficient_eligible_candidates')
    const firstRow = details.findAll('tbody tr')[0]!
    expect(firstRow.text()).toContain('32')
    if (detailed) {
      expect(details.text()).toContain('日期外 31')
      expect(details.text()).toContain('关键词不符 1')
    } else {
      expect(details.text()).toContain('历史运行未保存详细过滤数量')
      expect(firstRow.text()).toContain('—')
    }
    expect(wrapper.get('[data-test="refresh-recommendations"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-test="refresh-recommendations"]').trigger('click')
    expect(calls.every((call) => call.startsWith('GET '))).toBe(true)
  })

  it('keeps the empty result usable when source diagnostics cannot be loaded', async () => {
    const wrapper = await mountPage(vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/recommendations?')) {
        return json({ ...recommendation('composite'), candidate_count: 0, items: [], historical_matches: [] })
      }
      return json({ detail: '暂时不可用' }, 503)
    }))
    await flushPromises()
    expect(wrapper.text()).toContain('当前视图没有可推荐的新论文')
    expect(wrapper.get('[data-test="discovery-details"]').text()).toContain('来源明细暂时不可用')
    expect(wrapper.get('[data-test="discovery-details"] a').attributes('href')).toBe('/plans')
  })

  it('loads a newly selected run instead of retaining the previous empty result', async () => {
    const calls: string[] = []
    const wrapper = await mountPage(vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      if (url.includes('/runs/run-1/recommendations?')) {
        return json({ ...recommendation('composite'), candidate_count: 0, items: [], historical_matches: [] })
      }
      if (url.includes('/runs/run-2/recommendations?')) {
        return json({ ...recommendation('composite'), run_id: 'run-2', items: [{ ...paper('2'), title: 'Fresh discovery result' }] })
      }
      return json({ detail: 'Historical diagnostics unavailable' }, 404)
    }))
    await flushPromises()
    expect(wrapper.find('[data-test="discovery-details"]').exists()).toBe(true)
    await wrapper.vm.$router.push('/runs/run-2/results')
    await flushPromises()
    expect(wrapper.text()).toContain('Fresh discovery result')
    expect(wrapper.find('[data-test="discovery-details"]').exists()).toBe(false)
    expect(calls.some((call) => call.includes('/runs/run-2/recommendations?'))).toBe(true)
  })

  it('loads the durable result, switches all five views and expands historical evidence', async () => {
    const urls: string[] = []
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      urls.push(url)
      if (url.startsWith('/api/v1/runs/run-1/recommendations?')) {
        const view = new URL(url, 'http://local.test').searchParams.get('view') ?? 'composite'
        return json(recommendation(view))
      }
      if (url === '/api/v1/scores/snapshots/composite-1') {
        return json({
          id: 'composite-1',
          work_id: 'work-1',
          score_type: 'composite',
          context_key: 'run:run-1',
          rule_version: 'composite-v1',
          components: [],
          evidence_observation_ids: [],
          hard_constraints: {},
          weights: { relevance: 0.4, quality: 0.4 },
          missing_policy: { strategy: 'neutral_prior' },
          score: 82,
          coverage: 74,
          confidence: 72,
          run_id: 'run-1',
          snapshot_sha256: 'b'.repeat(64),
          created_at: '2026-07-15T04:00:00Z',
        })
      }
      throw new Error(`Unexpected request ${url}`)
    })
    const wrapper = await mountPage(fetcher)
    await flushPromises()

    expect(wrapper.get('h1').text()).toContain('推荐结果')
    expect(wrapper.text()).toContain('Reliable Agent Recommendations')
    expect(wrapper.findAll('[data-test^="ranking-view-"]')).toHaveLength(5)
    await wrapper.get('[data-test="ranking-view-quality"]').trigger('click')
    await flushPromises()
    expect(urls.at(-1)).toContain('view=quality')
    expect(wrapper.get('[data-test="ranking-view-quality"]').attributes('aria-pressed')).toBe('true')

    const history = wrapper.get('[data-test="historical-matches"]')
    expect(history.attributes('open')).toBeUndefined()
    expect(history.text()).toContain('1 篇')

    await wrapper.get('[data-test="show-evidence"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="score-evidence"]').text()).toContain('中性参考值')
  })

  it('updates feedback from the server and opens analysis only after an explicit click', async () => {
    const urls: string[] = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      urls.push(`${init?.method ?? 'GET'} ${url}`)
      if (url.startsWith('/api/v1/runs/run-1/recommendations?')) {
        return json(recommendation('composite'))
      }
      if (url === '/api/v1/library/works/work-1' && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body))
        return json({
          work_id: 'work-1',
          ...body,
          persisted: true,
          historical_match: false,
          created_at: '2026-07-15T05:00:00Z',
          updated_at: '2026-07-15T05:00:00Z',
        })
      }
      throw new Error(`Unexpected request ${url}`)
    })
    const wrapper = await mountPage(fetcher)
    await flushPromises()

    expect(urls.some((url) => url.includes('/analysis/'))).toBe(false)
    await wrapper.get('[data-test="feedback-like"]').trigger('click')
    await wrapper.get('[data-test="save-feedback"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="feedback-like"]').attributes('aria-pressed')).toBe('true')
    expect(urls.some((url) => url.includes('/analysis/'))).toBe(false)

    await wrapper.get('[data-test="analyze-paper"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="analysis-drawer"]').text()).toContain('analysis work-1')
  })

  it('uses a new idempotency scope for every explicit recommendation refresh', async () => {
    const keys: string[] = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/v1/runs/run-1/recommendations?')) {
        return json(recommendation('composite'))
      }
      if (url === '/api/v1/runs/run-1/recommendations/refresh' && init?.method === 'POST') {
        const headers = new Headers(init.headers)
        keys.push(headers.get('Idempotency-Key') ?? '')
        return json({
          id: `job-${keys.length}`, kind: 'recommendation.run', status: 'queued', progress: 0,
          cancel_requested: false,
        }, 202)
      }
      if (url === '/api/v1/jobs') return json([])
      throw new Error(`Unexpected request ${url}`)
    })
    const wrapper = await mountPage(fetcher)
    await flushPromises()

    await wrapper.get('[data-test="refresh-recommendations"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="refresh-recommendations"]').trigger('click')
    await flushPromises()

    expect(keys).toHaveLength(2)
    expect(keys[0]).toMatch(/^recommendations-ui-/)
    expect(keys[1]).not.toBe(keys[0])
  })
})
