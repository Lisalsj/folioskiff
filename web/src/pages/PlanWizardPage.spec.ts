import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PlanWizardPage from './PlanWizardPage.vue'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('PlanWizardPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('moves from research intent through confirmed keywords and preview to a running plan', async () => {
    const calls: Array<{ url: string; method: string; body?: unknown }> = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
      calls.push({ url, method, body })

      if (url === '/api/v1/settings/agents') {
        return response([{
          backend_id: 'ollama_local',
          display_name: 'Ollama Local',
          location: 'local',
          version: null,
          probe_code: 'ready',
          recommended_action: null,
          installed: true,
          authenticated: true,
          available: true,
          supports_schema: true,
          supports_streaming: true,
          supports_vision: true,
          supports_external_research: false,
          credential_required: false,
          credential_configured: false,
          supports_manual_model_id: true,
          supports_default_model: false,
          models: [{ model_id: 'qwen3.5:4b', display_name: 'qwen3.5:4b', supports_vision: false, context_window: 32768 }],
          degradation_reasons: [],
          desktop_app_running: null,
        }])
      }
      if (url === '/api/v1/settings/preferences') {
        return response({
          timezone_mode: 'custom',
          timezone_name: 'Europe/Berlin',
          system_timezone: 'America/New_York',
          system_timezone_fallback: false,
          resolved_timezone: 'Europe/Berlin',
          theme: 'system',
          default_agent_backend: 'codex_cli',
          default_agent_model_id: null,
          default_reasoning_effort: 'medium',
          report_language: 'zh-CN',
          translation_source_language: 'en',
          translation_target_language: 'zh-CN',
          updated_at: '2026-07-15T08:00:00Z',
        })
      }
      if (url === '/api/v1/settings/sources') {
        return response([
          {
            connector_id: 'arxiv',
            display_name: 'arXiv',
            credential_requirement: 'none',
            supports_exact_updated_date: false,
            enabled: true,
            disabled_reason: null,
            credential_configured: false,
            setup_url: 'https://info.arxiv.org/help/api/',
            protocol_version: '1',
          },
        ])
      }
      if (url === '/api/v1/keywords/preference-context') {
        return response({
          version: 'preference-context-v1',
          terms: [
            { term: 'diffusion model', weight: 100, sources: ['favorite', 'liked'] },
            { term: 'cancer imaging', weight: 82, sources: ['interested_author'] },
          ],
          source_categories: ['liked', 'favorite', 'interested_author'],
          contributing_work_count: 2,
          context_sha256: 'a'.repeat(64),
          empty: false,
        })
      }
      if (url === '/api/v1/keywords/suggestions') {
        return response(
          {
            id: 'keywords-1',
            research_intent: '可靠的论文阅读智能体',
            parent_version_id: null,
            definition: {
              required: [
                { key: 'agent', label: '研究智能体', terms: ['AI agent', '智能体'] },
              ],
              boost: [
                { key: 'reading', label: '论文阅读', terms: ['paper reading', '论文阅读'] },
              ],
              excluded: [],
            },
            change_summary: {},
            generator_info: { backend: 'fixture' },
            created_at: '2026-07-13T00:00:00Z',
            confirmed_at: null,
          },
          201,
        )
      }
      if (url === '/api/v1/keywords/versions/keywords-1/confirm') {
        return response({
          id: 'keywords-1',
          research_intent: '可靠的论文阅读智能体',
          parent_version_id: null,
          definition: {
            required: [
              { key: 'agent', label: '研究智能体', terms: ['AI agent', '智能体'] },
            ],
            boost: [
              { key: 'reading', label: '论文阅读', terms: ['paper reading', '论文阅读'] },
            ],
            excluded: [],
          },
          change_summary: {},
          generator_info: { backend: 'fixture' },
          created_at: '2026-07-13T00:00:00Z',
          confirmed_at: '2026-07-13T00:01:00Z',
        })
      }
      if (url === '/api/v1/plans/preview') {
        return response(
          { id: 'preview-job', kind: 'discovery.preview', status: 'queued', progress: 0, cancel_requested: false },
          202,
        )
      }
      if (url === '/api/v1/jobs/preview-job') {
        return response({
          id: 'preview-job',
          kind: 'discovery.preview',
          status: 'succeeded',
          progress: 100,
          cancel_requested: false,
          payload: {},
          checkpoint: null,
          error_code: null,
          error_message: null,
          created_at: '2026-07-13T00:02:00Z',
          updated_at: '2026-07-13T00:02:01Z',
          result: {
            plan_id: 'draft:fixture',
            keyword_version_id: 'keywords-1',
            months: ['2026-07'],
            warnings: [],
            sources: [
              {
                connector_id: 'arxiv',
                display_name: 'arXiv',
                status: 'ready',
                estimate: 12,
                estimate_exact: false,
                samples: [
                  {
                    connector_id: 'arxiv',
                    external_id: '2607.12345v1',
                    title: 'Reliable AI Agents for Scientific Paper Reading',
                    authors: ['Alice Researcher'],
                    abstract: null,
                    doi: null,
                    published_at: null,
                    updated_at: '2026-07-13T00:00:00Z',
                    landing_url: null,
                    field_provenance: {},
                    raw_payload: {},
                  },
                ],
                keyword_impact: {
                  remote_candidates: 15,
                  in_date_range: 13,
                  matched_confirmed_keywords: 12,
                },
                warnings: ['estimate_before_local_updated_filter'],
                error_code: null,
                error_message: null,
                retryable: false,
                setup_url: null,
              },
            ],
          },
        })
      }
      if (url === '/api/v1/plans' && method === 'POST') {
        return response(
          {
            id: 'plan-1',
            ...(body as object),
            created_at: '2026-07-13T00:03:00Z',
            warnings: [],
          },
          201,
        )
      }
      if (url === '/api/v1/plans/plan-1/runs') {
        return response(
          { id: 'run-job', kind: 'discovery.run', status: 'queued', progress: 0, cancel_requested: false },
          202,
        )
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/plans/new', component: PlanWizardPage },
        { path: '/plans', component: { template: '<h1>plans</h1>' } },
      ],
    })
    await router.push('/plans/new')
    await router.isReady()
    const wrapper = mount(PlanWizardPage, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    await wrapper.get('[data-test="plan-name"]').setValue('可靠智能体月报')
    await wrapper.get('[data-test="research-intent"]').setValue('可靠的论文阅读智能体')
    await wrapper.get('[data-test="keyword-agent-backend"]').setValue('ollama_local')
    await wrapper.get('[data-test="keyword-agent-model"]').setValue('qwen3.5:4b')
    expect(wrapper.get('[data-test="preference-context"]').text()).toContain('diffusion model')
    expect(wrapper.get('[data-test="preference-context"]').text()).toContain('cancer imaging')
    expect(
      (wrapper.get('[data-test="use-preference-context"]').element as HTMLInputElement).checked,
    ).toBe(true)
    await wrapper.get('[data-test="suggest-keywords"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('关键词工作台')
    expect(
      wrapper.findAll('input').some((input) => (input.element as HTMLInputElement).value === 'AI agent'),
    ).toBe(true)

    await wrapper.get('[data-test="confirm-keywords"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('月份与来源')
    expect(wrapper.get('[data-test="plan-timezone"]').text()).toContain('Europe/Berlin')
    expect(wrapper.get('[data-test="plan-timezone"]').text()).toContain('自选时区')

    await wrapper.get('[data-test="arxiv-categories"]').setValue('cs.AI, cs.CL')
    await wrapper.get('[data-test="schedule-frequency"]').setValue('weekly')
    await wrapper.get('[data-test="weekly-day"]').setValue('2')

    await wrapper.get('[data-test="preview-sources"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('来源预览')
    expect(wrapper.text()).toContain('Reliable AI Agents for Scientific Paper Reading')
    expect(wrapper.text()).toContain('12 篇')

    await wrapper.get('[data-test="save-and-run"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/plans')
    expect(calls.some((call) => call.url === '/api/v1/plans' && call.method === 'POST')).toBe(true)
    expect(calls.some((call) => call.url === '/api/v1/plans/plan-1/runs')).toBe(true)
    const previewCall = calls.find((call) => call.url === '/api/v1/plans/preview')
    expect(previewCall?.body).toMatchObject({
      date_range: { timezone: 'Europe/Berlin' },
      source_filters: { arxiv: { categories: ['cs.AI', 'cs.CL'] } },
      schedule: { frequency: 'weekly', weekly_day: 2 },
    })
    const suggestionCall = calls.find((call) => call.url === '/api/v1/keywords/suggestions')
    expect(suggestionCall?.body).toMatchObject({
      use_preference_context: true,
      preference_context_sha256: 'a'.repeat(64),
      agent_backend_id: 'ollama_local',
      agent_model_id: 'qwen3.5:4b',
      agent_reasoning_effort: 'medium',
    })
    expect(suggestionCall?.body).not.toHaveProperty('preference_terms')
  })

  it('lets the user visibly disable preference context before asking the agent', async () => {
    const calls: Array<{ url: string; method: string; body?: unknown }> = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
      calls.push({ url, method, body })
      if (url === '/api/v1/settings/sources') return response([])
      if (url === '/api/v1/keywords/preference-context') {
        return response({
          version: 'preference-context-v1',
          terms: [{ term: 'tool-using agents', weight: 100, sources: ['favorite'] }],
          source_categories: ['favorite'],
          contributing_work_count: 1,
          context_sha256: 'b'.repeat(64),
          empty: false,
        })
      }
      if (url === '/api/v1/keywords/suggestions') {
        return response(
          {
            id: 'keywords-without-preferences',
            research_intent: '可靠智能体',
            parent_version_id: null,
            definition: {
              required: [
                { key: 'agent', label: '智能体', terms: ['AI agent', '智能体'] },
              ],
              boost: [],
              excluded: [],
            },
            change_summary: {},
            generator_info: {
              mode: 'agent',
              preference_context: { enabled: false, sha256: null, term_count: 0 },
            },
            created_at: '2026-07-15T00:00:00Z',
            confirmed_at: null,
          },
          201,
        )
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/plans/new', component: PlanWizardPage },
        { path: '/plans', component: { template: '<h1>plans</h1>' } },
      ],
    })
    await router.push('/plans/new')
    await router.isReady()
    const wrapper = mount(PlanWizardPage, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    await wrapper.get('[data-test="plan-name"]').setValue('不使用偏好的计划')
    await wrapper.get('[data-test="research-intent"]').setValue('可靠智能体')
    await wrapper.get('[data-test="use-preference-context"]').setValue(false)
    expect(wrapper.get('[data-test="preference-context"]').text()).toContain('本次不会发送')
    await wrapper.get('[data-test="suggest-keywords"]').trigger('click')
    await flushPromises()

    const suggestion = calls.find((call) => call.url === '/api/v1/keywords/suggestions')
    expect(suggestion?.body).toMatchObject({ use_preference_context: false })
    expect(suggestion?.body).not.toHaveProperty('preference_terms')
  })

  it('fails closed when the visible preference context cannot be loaded', async () => {
    const calls: Array<{ url: string; body?: Record<string, unknown> }> = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
      calls.push({ url, body })
      if (url === '/api/v1/settings/sources') return response([])
      if (url === '/api/v1/keywords/preference-context') {
        return response({ detail: 'context unavailable' }, 503)
      }
      if (url === '/api/v1/keywords/suggestions') {
        return response(
          {
            id: 'keywords-without-unseen-context',
            research_intent: '可靠智能体',
            parent_version_id: null,
            definition: {
              required: [{ key: 'agent', label: '智能体', terms: ['AI agent', '智能体'] }],
              boost: [],
              excluded: [],
            },
            change_summary: {},
            generator_info: {
              mode: 'agent',
              preference_context: { enabled: false, sha256: null, term_count: 0 },
            },
            created_at: '2026-07-15T00:00:00Z',
            confirmed_at: null,
          },
          201,
        )
      }
      throw new Error(`Unexpected request: ${init?.method ?? 'GET'} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/plans/new', component: PlanWizardPage },
        { path: '/plans', component: { template: '<h1>plans</h1>' } },
      ],
    })
    await router.push('/plans/new')
    await router.isReady()
    const wrapper = mount(PlanWizardPage, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    expect(wrapper.get('[data-test="preference-context"]').text()).toContain(
      '仍可只依据研究意图继续',
    )
    await wrapper.get('[data-test="plan-name"]').setValue('关闭式偏好测试')
    await wrapper.get('[data-test="research-intent"]').setValue('可靠智能体')
    await wrapper.get('[data-test="suggest-keywords"]').trigger('click')
    await flushPromises()

    const suggestion = calls.find((call) => call.url === '/api/v1/keywords/suggestions')
    expect(suggestion?.body).toMatchObject({
      use_preference_context: false,
      preference_context_sha256: null,
    })
  })

  it('reloads a changed preference context instead of silently sending stale terms', async () => {
    let contextReads = 0
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/v1/settings/sources') return response([])
      if (url === '/api/v1/keywords/preference-context') {
        contextReads += 1
        const refreshed = contextReads > 1
        return response({
          version: 'preference-context-v1',
          terms: [
            {
              term: refreshed ? 'refreshed cancer imaging' : 'older cancer imaging',
              weight: 100,
              sources: ['liked'],
            },
          ],
          source_categories: ['liked'],
          contributing_work_count: 1,
          context_sha256: (refreshed ? 'd' : 'c').repeat(64),
          empty: false,
        })
      }
      if (url === '/api/v1/keywords/suggestions' && init?.method === 'POST') {
        return response(
          {
            detail: {
              code: 'preference_context_stale',
              message: 'Preference context changed',
            },
          },
          409,
        )
      }
      throw new Error(`Unexpected request: ${init?.method ?? 'GET'} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/plans/new', component: PlanWizardPage },
        { path: '/plans', component: { template: '<h1>plans</h1>' } },
      ],
    })
    await router.push('/plans/new')
    await router.isReady()
    const wrapper = mount(PlanWizardPage, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    await wrapper.get('[data-test="plan-name"]').setValue('偏好更新测试')
    await wrapper.get('[data-test="research-intent"]').setValue('癌症影像')
    await wrapper.get('[data-test="suggest-keywords"]').trigger('click')
    await flushPromises()

    expect(contextReads).toBe(2)
    expect(wrapper.get('[data-test="preference-context"]').text()).toContain(
      'refreshed cancer imaging',
    )
    expect(wrapper.text()).toContain('阅读偏好刚刚发生变化')
  })
})
