import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ReadingBriefingPage from './ReadingBriefingPage.vue'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

async function mountPage(payload: unknown, localWork?: unknown) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/v1/briefing') return json(payload)
    if (url.startsWith('/api/v1/works/') && localWork) return json(localWork)
    throw new Error(`Unexpected request ${url}`)
  })
  vi.stubGlobal('fetch', fetcher)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: ReadingBriefingPage },
      { path: '/plans/new', component: { template: '<p>new plan</p>' } },
      { path: '/plans', component: { template: '<p>plans</p>' } },
      { path: '/runs/:runId/results', component: { template: '<p>results</p>' } },
      { path: '/library', component: { template: '<p>library</p>' } },
      {
        path: '/library/:workId/read/:documentVersionId',
        component: { template: '<p>reader</p>' },
      },
    ],
  })
  await router.push('/')
  await router.isReady()
  return {
    wrapper: mount(ReadingBriefingPage, { global: { plugins: [router] } }),
    fetcher,
    router,
  }
}

const base = {
  generated_at: '2026-07-15T05:00:00Z',
  empty_state: {
    active: false,
    reason: null,
    title: null,
    description: null,
    primary_action: null,
  },
  plans: [],
  new_papers: [],
  important_revisions: [],
  pending_catchups: [],
  active_jobs: [],
  failed_jobs: [],
}

describe('ReadingBriefingPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('uses the server empty state instead of a hard-coded plan count', async () => {
    const { wrapper, fetcher } = await mountPage({
      ...base,
      empty_state: {
        active: true,
        reason: 'no_plans',
        title: '建立第一条研究线索',
        description: '先描述研究意图，再确认关键词。',
        primary_action: 'create_plan',
      },
    })
    await flushPromises()

    expect(fetcher).toHaveBeenCalledWith('/api/v1/briefing', expect.anything())
    expect(wrapper.get('[data-test="briefing-empty"]').text()).toContain('建立第一条研究线索')
    expect(wrapper.text()).not.toContain('尚未创建')
  })

  it('separates new papers, important revisions, catchups and job attention', async () => {
    const { wrapper, fetcher } = await mountPage({
      ...base,
      plans: [{
        id: 'plan-1',
        name: 'Agent 研究',
        research_intent: '可靠智能体',
        enabled: true,
        schedule_frequency: 'weekly',
        sources: ['arxiv', 'openreview'],
        latest_run: {
          id: 'run-1',
          status: 'partial',
          months: ['2026-07'],
          created_at: '2026-07-15T04:00:00Z',
          completed_at: '2026-07-15T04:10:00Z',
          recommendation_set_id: 'set-1',
          recommendation_set_version: 1,
          recommendation_count: 1,
        },
      }],
      new_papers: [{
        work_id: 'work-1',
        scored_work_id: 'work-1',
        title: 'Reliable Agents for Research',
        authors: ['Alice Chen'],
        plan_id: 'plan-1',
        run_id: 'run-1',
        recommendation_set_id: 'set-1',
        position: 1,
        channel: 'exploitation',
        composite_score: 84,
        relevance_score: 92,
        quality_score: 76,
        coverage: 71,
        confidence: 74,
        warnings: [],
      }],
      important_revisions: [{
        id: 'update-1',
        run_id: 'run-1',
        state: 'unread',
        work_id: 'work-2',
        title: 'A Major Revised Paper',
        authors: ['Bo Li'],
        source: 'arxiv',
        changed_fields: ['abstract', 'document'],
        source_updated_at: '2026-07-14T00:00:00Z',
        detected_at: '2026-07-15T04:00:00Z',
        has_readable_document: true,
      }],
      pending_catchups: [{
        id: 'catchup-1',
        plan_id: 'plan-1',
        scheduled_for: '2026-07-15T03:00:00Z',
        created_at: '2026-07-15T04:00:00Z',
      }],
      active_jobs: [{
        id: 'job-1', kind: 'discovery.run', status: 'running', progress: 45,
        error_code: null, created_at: '2026-07-15T04:00:00Z', updated_at: '2026-07-15T04:01:00Z',
      }],
      failed_jobs: [{
        id: 'job-2', kind: 'metadata.enrich', status: 'failed', progress: 100,
        error_code: 'rate_limited', created_at: '2026-07-15T03:00:00Z', updated_at: '2026-07-15T03:01:00Z',
      }],
    })
    await flushPromises()

    expect(wrapper.get('[data-test="new-papers"]').text()).toContain('Reliable Agents for Research')
    expect(wrapper.get('[data-test="new-papers"]').get('a').attributes('href')).toBe(
      '/runs/run-1/results',
    )
    expect(wrapper.get('[data-test="important-revisions"]').text()).toContain('A Major Revised Paper')
    expect(wrapper.get('[data-test="briefing-attention"]').text()).toContain('1 项待补跑')
    expect(wrapper.get('[data-test="briefing-attention"]').text()).toContain('rate_limited')
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/analysis/'))).toBe(false)
  })

  it('turns an incomplete response into a retryable state instead of crashing', async () => {
    const { wrapper } = await mountPage([])
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('简报数据格式不完整')
    expect(wrapper.get('[role="alert"] button').text()).toBe('重新读取')
  })

  it('keeps the local exploration hint quiet, dismissible, and opens reader related mode', async () => {
    const payload = {
      ...base,
      plans: [{
        id: 'plan-1', name: 'Agent 研究', research_intent: '可靠智能体', enabled: true,
        schedule_frequency: 'weekly', sources: ['arxiv'], latest_run: null,
      }],
      exploration_hint: {
        work_id: 'work-source',
        suggested_terms: ['planning agent', '规划评测'],
        related_work_ids: ['work-local'],
        reason: '基于本地论文库，可继续探索规划评测。',
      },
    }
    const localWork = {
      id: 'work-local', title: 'Local Agent Paper', authors: [], abstract: null, doi: null,
      published_at: null, added_via: 'pdf', added_at: '2026-08-01T00:00:00Z',
      favorite: false, feedback: null, pinned: false, reading_progress: 0,
      analysis_state: 'not_analyzed', document_count: 1,
      representative_document: { id: 'document-local', available: true },
    }
    const { wrapper, fetcher, router } = await mountPage(payload, localWork)
    await flushPromises()

    expect(wrapper.get('[data-test="exploration-hint"]').text()).toContain('planning agent')
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/related-resources'))).toBe(false)
    await wrapper.get('[data-test="exploration-hint"] .open-hint').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe(
      '/library/work-local/read/document-local?task=related',
    )
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/related-resources'))).toBe(false)

    const dismissed = await mountPage(payload, localWork)
    await flushPromises()
    await dismissed.wrapper.get('[aria-label="暂时隐藏相关推荐"]').trigger('click')
    expect(dismissed.wrapper.find('[data-test="exploration-hint"]').exists()).toBe(false)
    expect(dismissed.fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })
})
