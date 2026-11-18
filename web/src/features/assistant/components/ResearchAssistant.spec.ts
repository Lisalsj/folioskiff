import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ResearchAssistant from './ResearchAssistant.vue'

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

const preferences = {
  timezone_mode: 'system',
  timezone_name: null,
  system_timezone: 'Asia/Shanghai',
  resolved_timezone: 'Asia/Shanghai',
  system_timezone_fallback: false,
  theme: 'system',
  default_agent_backend: 'codex_cli',
  default_agent_model_id: null,
  default_reasoning_effort: 'medium',
  report_language: 'zh-CN',
  translation_source_language: 'en',
  translation_target_language: 'zh-CN',
  updated_at: '2026-08-01T00:00:00Z',
}

const capability = {
  backend_id: 'codex_cli',
  display_name: 'Codex CLI',
  location: 'cloud',
  probe_code: 'ready',
  installed: true,
  authenticated: true,
  available: true,
  supports_schema: true,
  supports_streaming: true,
  supports_vision: false,
  supports_external_research: false,
  credential_required: false,
  credential_configured: false,
  supports_manual_model_id: true,
  supports_default_model: true,
  version: 'fixture',
  models: [],
  degradation_reasons: [],
  recommended_action: null,
}

describe('ResearchAssistant', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows explicit modes and selected evidence without calling a model on mount', async () => {
    const requests: Array<{ url: string, method: string }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push({ url, method })
      if (url === '/api/v1/assistant/works/work-1') {
        return json({
          work_id: 'work-1',
          session_id: null,
          tasks: [],
          messages: [],
          latest_checkpoint: null,
        })
      }
      if (url === '/api/v1/settings/agents') return json([capability])
      if (url === '/api/v1/settings/preferences') return json(preferences)
      throw new Error(`Unexpected request ${method} ${url}`)
    }))

    const wrapper = mount(ResearchAssistant, {
      props: {
        workId: 'work-1',
        documentVersionId: 'document-1',
        documentContentSha256: 'a'.repeat(64),
        readerContext: {
          currentPage: 2,
          pageCount: 8,
          selection: {
            pageNumber: 2,
            pageCount: 8,
            pageWidth: 420,
            pageHeight: 595,
            quoteText: 'selected workspace evidence',
            contextBefore: 'before',
            contextAfter: 'after',
            rects: [{ x0: 10, y0: 20, x1: 180, y1: 38 }],
            verified: true,
          },
          annotations: [],
        },
      },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    expect(wrapper.get('h2').text()).toBe('论文 Assistant')
    expect(wrapper.get('[data-test="assistant-mode"]').text()).toContain('提问')
    expect(wrapper.get('[data-test="assistant-mode"]').text()).toContain('解释')
    expect(wrapper.get('[data-test="assistant-context"]').text()).toContain('选中文本')
    expect(wrapper.text()).toContain('仅使用当前论文与本地资料')
    expect(wrapper.find('input[type="checkbox"][name="external-research"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="assistant-model-trigger"]').attributes('aria-expanded')).toBe(
      'false',
    )
    expect(requests.some(({ url, method }) => method === 'POST' && url.endsWith('/tasks'))).toBe(
      false,
    )
  })

  it('keeps an externally requested explanation mode aligned with the submitted task mode', async () => {
    const requests: Array<{ url: string, method: string }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push({ url, method })
      if (url === '/api/v1/assistant/works/work-1') {
        return json({
          work_id: 'work-1',
          session_id: null,
          tasks: [],
          messages: [],
          latest_checkpoint: null,
        })
      }
      if (url === '/api/v1/settings/agents') return json([capability])
      if (url === '/api/v1/settings/preferences') return json(preferences)
      throw new Error(`Unexpected request ${method} ${url}`)
    }))

    const wrapper = mount(ResearchAssistant, {
      props: {
        workId: 'work-1',
        documentVersionId: 'document-1',
        documentContentSha256: 'a'.repeat(64),
        readerContext: {
          currentPage: 2,
          pageCount: 8,
          selection: {
            pageNumber: 2,
            pageCount: 8,
            pageWidth: 420,
            pageHeight: 595,
            quoteText: 'selected workspace evidence',
            contextBefore: 'before',
            contextAfter: 'after',
            rects: [{ x0: 10, y0: 20, x1: 180, y1: 38 }],
            verified: true,
          },
          annotations: [],
        },
      },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    await wrapper.setProps({ initialMode: 'explain' })
    await flushPromises()

    expect(wrapper.get('[data-test="assistant-mode-explain"]').attributes('data-state')).toBe('on')
    expect(wrapper.get('label[for="assistant-input"]').text()).toBe('说明你希望解释什么')
    expect(wrapper.get<HTMLSelectElement>('[data-test="assistant-context-choice"]').element.value)
      .toBe('selection')
    expect(requests.some(({ url, method }) => method === 'POST' && url.endsWith('/tasks'))).toBe(
      false,
    )
  })

  it('prepares an English to Chinese selection translation without network controls', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/v1/assistant/works/work-1') {
        return json({
          work_id: 'work-1',
          session_id: null,
          tasks: [],
          messages: [],
          latest_checkpoint: null,
        })
      }
      if (url === '/api/v1/settings/agents') return json([capability])
      if (url === '/api/v1/settings/preferences') return json(preferences)
      throw new Error(`Unexpected request GET ${url}`)
    }))

    const wrapper = mount(ResearchAssistant, {
      props: {
        workId: 'work-1',
        documentVersionId: 'document-1',
        documentContentSha256: 'a'.repeat(64),
        readerContext: {
          currentPage: 2,
          pageCount: 8,
          selection: {
            pageNumber: 2,
            pageCount: 8,
            pageWidth: 420,
            pageHeight: 595,
            quoteText: 'Diffusion model evidence',
            contextBefore: 'before',
            contextAfter: 'after',
            rects: [{ x0: 10, y0: 20, x1: 180, y1: 38 }],
            verified: true,
          },
          annotations: [],
        },
      },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    await wrapper.get('[data-mode="translate"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="language-pair"]').text()).toContain('英文')
    expect(wrapper.get('[data-test="language-pair"]').text()).toContain('简体中文')
    expect(wrapper.find('[data-test="external-research"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="assistant-context"]').text()).toContain('选中文本')
    expect(wrapper.get('[data-test="translation-evidence-boundary"]').text()).toContain(
      '仅使用当前论文与本地资料',
    )
  })

  it('does not draft or retrieve related work until explicit start and confirmation', async () => {
    const requests: Array<{ url: string, method: string }> = []
    const relatedDraft = {
      set_id: 'related-set-1',
      summary: '围绕扩散模型证据寻找规划与评测论文',
      required_groups: [['diffusion model', '扩散模型']],
      optional_terms: ['planning agent', '规划智能体'],
      excluded_terms: ['survey'],
      recent_months: 36,
      source_ids: ['arxiv', 'openreview'],
      revision: 1,
      confirmed: false,
      confirmed_at: null,
      diff: {
        added_terms: [], removed_terms: [], moved_terms: [],
        added_source_ids: [], removed_source_ids: [],
        summary_changed: false, recent_months_changed: false,
      },
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push({ url, method })
      if (url === '/api/v1/assistant/works/work-1') {
        return json({
          work_id: 'work-1', session_id: null, tasks: [], messages: [], latest_checkpoint: null,
        })
      }
      if (url === '/api/v1/settings/agents') return json([capability])
      if (url === '/api/v1/settings/preferences') return json(preferences)
      if (url.endsWith('/related-resources/works/work-1/draft') && method === 'POST') {
        return json(relatedDraft)
      }
      if (url.endsWith('/related-resources/related-set-1/intent') && method === 'PUT') {
        return json({
          ...relatedDraft,
          confirmed: true,
          confirmed_at: '2026-08-01T01:00:00Z',
        })
      }
      throw new Error(`Unexpected request ${method} ${url}`)
    }))

    const wrapper = mount(ResearchAssistant, {
      props: {
        workId: 'work-1',
        documentVersionId: 'document-1',
        documentContentSha256: 'a'.repeat(64),
        readerContext: {
          currentPage: 2,
          pageCount: 8,
          selection: null,
          annotations: [],
        },
      },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    await wrapper.get('[data-mode="related"]').trigger('click')
    await flushPromises()
    expect(requests.filter(({ url }) => url.includes('/related-resources'))).toEqual([])
    expect(wrapper.get('[data-test="related-evidence-boundary"]').text()).toContain('切换模式不会联网')

    await wrapper.get('[data-test="start-related"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="term-required-0-1"]').element).toHaveProperty(
      'value',
      '扩散模型',
    )
    expect(wrapper.find('[data-test="run-related"]').exists()).toBe(false)
    const draftCall = requests.find(({ url }) => url.endsWith('/draft'))
    expect(draftCall).toBeDefined()

    await wrapper.get('[data-test="confirm-related"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="run-related"]').attributes('disabled')).toBeUndefined()
    expect(requests.some(({ url }) => url.endsWith('/run'))).toBe(false)
  })

  it('submits only the verified selection and opens the resulting artifact', async () => {
    let submitted: { body: Record<string, unknown>, key: string } | undefined
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/v1/assistant/works/work-1') {
        return json({
          work_id: 'work-1',
          session_id: null,
          tasks: [],
          messages: [],
          latest_checkpoint: null,
        })
      }
      if (url === '/api/v1/settings/agents') return json([capability])
      if (url === '/api/v1/settings/preferences') return json(preferences)
      if (url === '/api/v1/translations' && init?.method === 'POST') {
        submitted = {
          body: JSON.parse(String(init.body)) as Record<string, unknown>,
          key: new Headers(init.headers).get('Idempotency-Key') ?? '',
        }
        return json({
          artifact_id: 'translation-1',
          task_id: 'task-translation',
          job_id: 'job-translation',
          kind: 'translation.generate',
          status: 'queued',
          progress: 0,
          quick: true,
          cancel_requested: false,
          error_code: null,
          error_message: null,
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-01T00:00:00Z',
        })
      }
      if (url === '/api/v1/translations/translation-1') {
        return json({
          id: 'translation-1',
          work_id: 'work-1',
          version: 1,
          document_version_id: 'document-1',
          document_content_sha256: 'a'.repeat(64),
          context_snapshot_id: 'context-translation',
          task_id: 'task-translation',
          job_id: 'job-translation',
          source_language: 'en',
          target_language: 'zh-CN',
          scope: 'selection',
          status: 'succeeded',
          evidence_level: 'native_text',
          prompt_version: 'translation-v1',
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-01T00:01:00Z',
          completed_at: '2026-08-01T00:01:00Z',
          terms: [],
        })
      }
      if (url === '/api/v1/translations/translation-1/segments') {
        return json([{
          segment_id: 'segment-1',
          segment_index: 0,
          page_number: 2,
          block_numbers: [1],
          source_text: 'Diffusion model evidence',
          source_sha256: 'b'.repeat(64),
          translated_text: '扩散模型证据',
          source_kind: 'native_text',
          ocr_confidence: null,
          status: 'succeeded',
          diagnostic: null,
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-01T00:01:00Z',
        }])
      }
      if (url === '/api/v1/assistant/tasks/task-translation') {
        return json({ backend_id: 'codex_cli', requested_model_id: null })
      }
      if (url === '/api/v1/jobs') return json([])
      throw new Error(`Unexpected request ${init?.method ?? 'GET'} ${url}`)
    }))

    const wrapper = mount(ResearchAssistant, {
      props: {
        workId: 'work-1',
        documentVersionId: 'document-1',
        documentContentSha256: 'a'.repeat(64),
        readerContext: {
          currentPage: 2,
          pageCount: 8,
          selection: {
            pageNumber: 2,
            pageCount: 8,
            pageWidth: 420,
            pageHeight: 595,
            quoteText: 'Diffusion model evidence',
            contextBefore: 'before',
            contextAfter: 'after',
            rects: [{ x0: 10, y0: 20, x1: 180, y1: 38 }],
            verified: true,
          },
          annotations: [],
        },
      },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()
    await wrapper.get('[data-mode="translate"]').trigger('click')
    await wrapper.get('[data-test="translation-start"]').trigger('click')
    await flushPromises()

    const body = submitted?.body as {
      context: { scope: string, selection: { quote_text: string } }
      network_policy: string
      agent: { backend_id: string }
    }
    expect(submitted?.key).toMatch(/^translation-ui-/)
    expect(body.context.scope).toBe('selection')
    expect(body.context.selection.quote_text).toBe('Diffusion model evidence')
    expect(body.network_policy).toBe('local_only')
    expect(body.agent.backend_id).toBe('codex_cli')
    await wrapper.get('[data-test="open-bilingual"]').trigger('click')
    expect(wrapper.emitted('openTranslation')).toEqual([['translation-1']])
  })
})
