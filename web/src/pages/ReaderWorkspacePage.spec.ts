import { defineComponent, h, onMounted, onUnmounted, ref, type PropType } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ReaderWorkspacePage from './ReaderWorkspacePage.vue'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const documentOne = {
  id: 'document-1',
  work_id: 'work-1',
  content_sha256: 'a'.repeat(64),
  access_status: 'user_provided',
  media_type: 'application/pdf',
  completeness: 100,
  extraction_status: 'complete',
  available: true,
  byte_size: 2048,
  original_filename: 'reader-v1.pdf',
  acquisition_kind: 'manual_import',
  last_accessed_at: null,
  created_at: '2026-07-13T08:00:00Z',
  is_representative: true,
}

const documentTwo = {
  ...documentOne,
  id: 'document-2',
  content_sha256: 'b'.repeat(64),
  original_filename: 'reader-v0.pdf',
  completeness: 80,
  is_representative: false,
}

const work = {
  id: 'work-1',
  title: 'Reliable Agents for Paper Reading',
  authors: ['Alice Chen', 'Ravi Kumar'],
  abstract: 'Evidence-first research reading.',
  doi: '10.1000/reader',
  published_at: '2026-07-01T00:00:00Z',
  added_via: 'pdf',
  added_at: '2026-07-13T08:00:00Z',
  favorite: true,
  feedback: 'like',
  pinned: false,
  reading_progress: 33,
  analysis_state: 'not_analyzed',
  document_count: 2,
  representative_document: documentOne,
}

const agentCapabilities = [{
  backend_id: 'codex_cli',
  display_name: 'Codex CLI',
  location: 'cloud',
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
  models: [],
  degradation_reasons: [],
}]

const migrationCandidate = {
  id: 'migration-candidate-1',
  batch_id: 'migration-preview-1',
  source_annotation_id: 'source-annotation-1',
  source_kind: 'highlight' as const,
  source_page_number: 1,
  source_quote_text: 'Evidence moved in the revised paper',
  source_note: 'Keep this argument',
  source_color: 'amber' as const,
  match_state: 'matched' as const,
  confidence: 'high' as const,
  score: 0.98,
  target_page_number: 3,
  target_page_width: 420,
  target_page_height: 595,
  target_quote_text: 'Evidence moved in the revised paper',
  target_context_before: 'before',
  target_context_after: 'after',
  target_rects: [{ x0: 20, y0: 30, x1: 190, y1: 48 }],
  text_source: 'native' as const,
  alternatives: [],
  decision: null,
  target_annotation_id: null,
  created_at: '2026-07-13T09:00:00Z',
  decided_at: null,
}

const migrationPreview = {
  id: 'migration-preview-1',
  algorithm_version: 'annotation-migration-v1',
  source_document_version_id: 'document-2',
  source_document_content_sha256: 'b'.repeat(64),
  target_document_version_id: 'document-1',
  target_document_content_sha256: 'a'.repeat(64),
  target_extraction_sha256: 'c'.repeat(64),
  candidates: [migrationCandidate],
  coverage: {
    total: 1,
    matched: 1,
    ambiguous: 0,
    orphaned: 0,
    pending: 1,
    accepted: 0,
    rejected: 0,
  },
  created_at: '2026-07-13T09:00:00Z',
}

let mountedReaders = 0
let unmountedReaders = 0
const PdfReaderStub = defineComponent({
  name: 'PdfReader',
  props: {
    initialPage: { type: Number, default: 1 },
    initialScale: { type: Number, default: 1.1 },
    initialRotation: { type: Number, default: 0 },
    annotations: { type: Array as PropType<unknown[]>, default: () => [] },
    migrationPreview: {
      type: Object as PropType<{ id: string, pageNumber: number } | null>,
      default: null,
    },
  },
  emits: [
    'state-change',
    'selection-change',
    'quick-highlight',
    'bookmark-request',
    'annotation-select',
  ],
  setup(props, { emit, expose }) {
    const page = ref(props.initialPage)
    const selection = {
      pageNumber: 2,
      pageCount: 3,
      pageWidth: 420,
      pageHeight: 595,
      quoteText: 'selected workspace evidence',
      contextBefore: 'before',
      contextAfter: 'after',
      rects: [{ x0: 10, y0: 20, x1: 180, y1: 38 }],
      verified: true,
    }
    onMounted(() => {
      mountedReaders += 1
      emit('state-change', {
        page: page.value,
        pageCount: 3,
        scale: props.initialScale,
        rotation: props.initialRotation,
        query: '',
      })
    })
    onUnmounted(() => { unmountedReaders += 1 })
    function goToPage(value: number) {
      page.value = value
      emit('state-change', {
        page: page.value,
        pageCount: 3,
        scale: props.initialScale,
        rotation: props.initialRotation,
        query: '',
      })
    }
    function next() { goToPage(page.value + 1) }
    function requestBookmark() {
      emit('bookmark-request', {
        pageNumber: page.value,
        pageCount: 3,
        pageWidth: 420,
        pageHeight: 595,
      })
    }
    function clearSelection() { emit('selection-change', null) }
    expose({ goToPage, requestBookmark, clearSelection })
    return () => h('div', { 'data-test': 'stub-pdf-reader' }, [
      h('span', {
        'data-test': 'stub-page',
        'data-scale': props.initialScale,
        'data-rotation': props.initialRotation,
        'data-annotations': props.annotations.length,
        'data-migration-preview': props.migrationPreview?.id ?? '',
        'data-migration-page': props.migrationPreview?.pageNumber ?? '',
      }, String(page.value)),
      h('button', { 'data-test': 'stub-next', onClick: next }, 'next'),
      h('button', {
        'data-test': 'stub-select',
        onClick: () => emit('selection-change', selection),
      }, 'select'),
      h('button', {
        'data-test': 'stub-quick-highlight',
        onClick: () => emit('quick-highlight', selection),
      }, 'quick'),
      h('button', { 'data-test': 'stub-bookmark', onClick: requestBookmark }, 'bookmark'),
    ])
  },
})

async function mountWorkspace(
  attachTo?: HTMLElement,
  initialPath = '/library/work-1/read/document-1',
) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/library', component: { template: '<p>library</p>' } },
      {
        path: '/library/:workId/read/:documentVersionId',
        component: ReaderWorkspacePage,
      },
      {
        path: '/library/:workId/translations/:artifactId',
        name: 'bilingual-reader',
        component: { template: '<p>translation</p>' },
      },
    ],
  })
  await router.push(initialPath)
  await router.isReady()
  return mount(ReaderWorkspacePage, {
    attachTo,
    global: {
      plugins: [createPinia(), router],
      stubs: { PdfReader: PdfReaderStub },
    },
  })
}

describe('ReaderWorkspacePage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    mountedReaders = 0
    unmountedReaders = 0
  })

  it('opens the quiet related mode from a local hint without drafting or retrieving', async () => {
    const requests: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      requests.push(url)
      if (url === '/api/v1/works/work-1') return json(work)
      if (url === '/api/v1/works/work-1/documents') return json([documentOne])
      if (url === '/api/v1/documents/document-1/reading-position') return json(null)
      if (url === '/api/v1/documents/document-1/annotations') return json([])
      if (url === '/api/v1/assistant/works/work-1') {
        return json({
          work_id: 'work-1', session_id: null, tasks: [], messages: [], latest_checkpoint: null,
        })
      }
      if (url === '/api/v1/settings/agents') return json(agentCapabilities)
      if (url === '/api/v1/settings/preferences') {
        return json({
          timezone_mode: 'system', timezone_name: null, system_timezone: 'Asia/Shanghai',
          resolved_timezone: 'Asia/Shanghai', system_timezone_fallback: false, theme: 'system',
          default_agent_backend: 'codex_cli', default_agent_model_id: null,
          default_reasoning_effort: 'medium', report_language: 'zh-CN',
          translation_source_language: 'en', translation_target_language: 'zh-CN',
          updated_at: '2026-08-01T00:00:00Z',
        })
      }
      throw new Error(`Unexpected request ${url}`)
    }))

    const wrapper = await mountWorkspace(
      undefined,
      '/library/work-1/read/document-1?task=related',
    )
    await flushPromises()

    expect(wrapper.get('[data-test="agent-assistant-tab"]').attributes('aria-selected')).toBe('true')
    expect(wrapper.get('[data-test="assistant-mode-related"]').attributes('data-state')).toBe('on')
    expect(wrapper.get('[data-test="start-related"]').text()).toContain('开始准备检索词')
    expect(requests.filter((url) => url.includes('/related-resources'))).toEqual([])
    wrapper.unmount()
    await flushPromises()
  })

  it('routes verified PDF selections into explanation and English-to-Chinese translation', async () => {
    const requests: Array<{ url: string, method: string }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push({ url, method })
      if (url === '/api/v1/works/work-1') return json(work)
      if (url === '/api/v1/works/work-1/documents') return json([documentOne])
      if (url === '/api/v1/documents/document-1/reading-position') return json(null)
      if (url === '/api/v1/documents/document-1/annotations') return json([])
      if (url === '/api/v1/assistant/works/work-1') {
        return json({
          work_id: 'work-1', session_id: null, tasks: [], messages: [], latest_checkpoint: null,
        })
      }
      if (url === '/api/v1/settings/agents') return json(agentCapabilities)
      if (url === '/api/v1/settings/preferences') {
        return json({
          timezone_mode: 'system', timezone_name: null, system_timezone: 'Asia/Shanghai',
          resolved_timezone: 'Asia/Shanghai', system_timezone_fallback: false, theme: 'system',
          default_agent_backend: 'codex_cli', default_agent_model_id: null,
          default_reasoning_effort: 'medium', report_language: 'zh-CN',
          translation_source_language: 'en', translation_target_language: 'zh-CN',
          updated_at: '2026-08-01T00:00:00Z',
        })
      }
      throw new Error(`Unexpected request ${method} ${url}`)
    }))

    const wrapper = await mountWorkspace(document.body)
    await flushPromises()
    await wrapper.get('[data-test="stub-select"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="context-action-bar"]').exists()).toBe(true)
    await wrapper.get('[data-test="context-explain"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="agent-assistant-tab"]').attributes('aria-selected')).toBe('true')
    expect(wrapper.get('[data-test="assistant-mode-explain"]').attributes('data-state')).toBe('on')
    expect(wrapper.get('label[for="assistant-input"]').text()).toBe('说明你希望解释什么')
    expect(wrapper.get('[data-test="assistant-context"]').text()).toContain(
      'selected workspace evidence',
    )
    expect(requests.some(({ url, method }) => method === 'POST' && url.endsWith('/tasks'))).toBe(
      false,
    )

    await wrapper.get('[data-test="context-translate"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="assistant-mode-translate"]').attributes('data-state')).toBe('on')
    expect(wrapper.get('[data-test="language-pair"]').text()).toContain('英文')
    expect(wrapper.get('[data-test="language-pair"]').text()).toContain('简体中文')
    expect(requests.some(({ url, method }) => method === 'POST' && url.includes('/translations')))
      .toBe(false)
    wrapper.unmount()
    await flushPromises()
  })

  it('provides three columns, a medium drawer and persistent mobile PDF/Agent tabs', async () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      matches: true,
      media: '(max-width: 1280px)',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })))
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/v1/works/work-1') return json(work)
      if (url === '/api/v1/works/work-1/documents') return json([documentOne, documentTwo])
      if (url === '/api/v1/documents/document-1/reading-position') return json(null)
      if (url === '/api/v1/documents/document-1/annotations') return json([])
      if (url === '/api/v1/analysis/works/work-1/reports') return json([])
      if (url === '/api/v1/assistant/works/work-1') {
        return json({
          work_id: 'work-1',
          session_id: null,
          tasks: [],
          messages: [],
          latest_checkpoint: null,
        })
      }
      if (url === '/api/v1/settings/agents') return json(agentCapabilities)
      if (url === '/api/v1/settings/preferences') {
        return json({
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
        })
      }
      if (url === '/api/v1/jobs') return json([])
      throw new Error(`Unexpected request ${url}`)
    }))
    const wrapper = await mountWorkspace(document.body)
    await flushPromises()

    expect(wrapper.get('[data-test="reader-workspace"]').attributes('data-layout')).toBe(
      'three-column',
    )
    expect(wrapper.text()).toContain('Reliable Agents for Paper Reading')
    expect(wrapper.text()).toContain('reader-v1.pdf')
    expect(wrapper.text()).toContain('reader-v0.pdf')
    expect(wrapper.get('[data-test="reader-content-url"]').attributes('data-url')).toBe(
      '/api/v1/documents/document-1/content',
    )
    expect(wrapper.get('[data-test="library-drawer-toggle"]').attributes('aria-expanded')).toBe(
      'false',
    )
    expect(wrapper.get('#reader-library-column').attributes('aria-hidden')).toBe('true')
    expect(wrapper.get('#reader-library-column').attributes('inert')).toBeDefined()
    await wrapper.get('[data-test="library-drawer-toggle"]').trigger('click')
    expect(wrapper.get('[data-test="library-drawer-toggle"]').attributes('aria-expanded')).toBe(
      'true',
    )
    expect(wrapper.get('#reader-library-column').attributes('aria-hidden')).toBe('false')

    const mobilePdfTab = wrapper.get<HTMLButtonElement>('[data-test="mobile-pdf-tab"]')
    const mobileAnnotationsTab = wrapper.get<HTMLButtonElement>(
      '[data-test="mobile-annotations-tab"]',
    )
    mobilePdfTab.element.focus()
    await flushPromises()
    expect(mobilePdfTab.attributes('tabindex')).toBe('0')
    expect(mobileAnnotationsTab.attributes('tabindex')).toBe('-1')
    await mobilePdfTab.trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(document.activeElement).toBe(mobileAnnotationsTab.element)

    const annotationsTab = wrapper.get<HTMLButtonElement>('[data-test="agent-annotations-tab"]')
    const reportTab = wrapper.get<HTMLButtonElement>('[data-test="agent-report-tab"]')
    annotationsTab.element.focus()
    await annotationsTab.trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(document.activeElement).toBe(reportTab.element)
    expect(reportTab.attributes('aria-selected')).toBe('true')
    await annotationsTab.trigger('click')
    await flushPromises()

    await wrapper.get('[data-test="stub-next"]').trigger('click')
    expect(wrapper.get('[data-test="reader-position"]').text()).toContain('2 / 3')
    await wrapper.get('[data-test="mobile-report-tab"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="agent-column"]').attributes('data-mobile-active')).toBe('true')
    expect(wrapper.get('[data-test="analysis-empty"]').text()).toContain('尚未分析这篇论文')
    const analysisPanel = wrapper.get('[data-test="analysis-panel"]').element
    const agentColumn = wrapper.get('[data-test="agent-column"]').element
    agentColumn.scrollTop = 41
    await wrapper.get('[data-test="mobile-assistant-tab"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="assistant-empty"]').text()).toContain('从正在阅读的证据开始')
    const assistantPanel = wrapper.get('[data-test="research-assistant"]').element
    agentColumn.scrollTop = 3
    await wrapper.get('[data-test="mobile-report-tab"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="analysis-panel"]').element).toBe(analysisPanel)
    expect(agentColumn.scrollTop).toBe(41)
    await wrapper.get('[data-test="mobile-assistant-tab"]').trigger('click')
    expect(wrapper.get('[data-test="research-assistant"]').element).toBe(assistantPanel)
    expect(agentColumn.scrollTop).toBe(3)
    await wrapper.get('[data-test="stub-select"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="agent-assistant-tab"]').attributes('aria-selected')).toBe('true')
    expect(wrapper.get('[data-test="assistant-context"]').text()).toContain('选中文本')
    expect(wrapper.get('[data-test="assistant-context"]').text()).toContain(
      'selected workspace evidence',
    )
    await wrapper.get('[data-test="mobile-pdf-tab"]').trigger('click')
    expect(wrapper.get('[data-test="pdf-column"]').attributes('data-mobile-active')).toBe('true')
    expect(wrapper.get('[data-test="stub-page"]').text()).toBe('2')
    expect(mountedReaders).toBe(1)
    expect(unmountedReaders).toBe(0)
    wrapper.unmount()
    await flushPromises()
  })

  it('keeps the PDF page, selected Agent tab and tab scroll across compact layout changes', async () => {
    let compactListener: ((event: MediaQueryListEvent) => void) | undefined
    const compactQuery = {
      matches: false,
      media: '(max-width: 1280px)',
      onchange: null,
      addEventListener: vi.fn((_type: string, listener: (event: MediaQueryListEvent) => void) => {
        compactListener = listener
      }),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }
    vi.stubGlobal('matchMedia', vi.fn(() => compactQuery))
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/v1/works/work-1') return json(work)
      if (url === '/api/v1/works/work-1/documents') return json([documentOne])
      if (url === '/api/v1/documents/document-1/reading-position') return json(null)
      if (url === '/api/v1/documents/document-1/annotations') return json([])
      if (url === '/api/v1/assistant/works/work-1') {
        return json({
          work_id: 'work-1', session_id: null, tasks: [], messages: [], latest_checkpoint: null,
        })
      }
      if (url === '/api/v1/settings/agents') return json(agentCapabilities)
      if (url === '/api/v1/settings/preferences') {
        return json({
          timezone_mode: 'system', timezone_name: null, system_timezone: 'Asia/Shanghai',
          resolved_timezone: 'Asia/Shanghai', system_timezone_fallback: false, theme: 'system',
          default_agent_backend: 'codex_cli', default_agent_model_id: null,
          default_reasoning_effort: 'medium', report_language: 'zh-CN',
          translation_source_language: 'en', translation_target_language: 'zh-CN',
          updated_at: '2026-08-01T00:00:00Z',
        })
      }
      throw new Error(`Unexpected request ${url}`)
    }))

    const wrapper = await mountWorkspace(document.body)
    await flushPromises()
    await wrapper.get('[data-test="stub-next"]').trigger('click')
    await wrapper.get('[data-test="mobile-assistant-tab"]').trigger('click')
    await flushPromises()
    const agentColumn = wrapper.get<HTMLElement>('[data-test="agent-column"]')
    agentColumn.element.scrollTop = 180

    compactQuery.matches = true
    compactListener?.({ matches: true } as MediaQueryListEvent)
    compactQuery.matches = false
    compactListener?.({ matches: false } as MediaQueryListEvent)
    await flushPromises()

    expect(wrapper.get('[data-test="stub-page"]').text()).toBe('2')
    expect(wrapper.get('[data-test="agent-assistant-tab"]').attributes('aria-selected')).toBe('true')
    expect(agentColumn.element.scrollTop).toBe(180)
    wrapper.unmount()
    await flushPromises()
  })

  it('reloads document-owned state when switching versions without recreating the page', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      if (url === '/api/v1/works/work-1') return json(work)
      if (url === '/api/v1/works/work-1/documents') return json([documentOne, documentTwo])
      if (url === '/api/v1/documents/document-1/reading-position') return json(null)
      if (url === '/api/v1/documents/document-1/annotations') return json([])
      if (url === '/api/v1/documents/document-2/reading-position') return json(null)
      if (url === '/api/v1/documents/document-2/annotations') return json([])
      throw new Error(`Unexpected request ${url}`)
    }))
    const wrapper = await mountWorkspace()
    await flushPromises()

    await wrapper.get('a[href="/library/work-1/read/document-2"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="reader-content-url"]').attributes('data-url')).toBe(
      '/api/v1/documents/document-2/content',
    )
    expect(calls).toContain('/api/v1/documents/document-2/reading-position')
    expect(calls).toContain('/api/v1/documents/document-2/annotations')
    expect(mountedReaders).toBe(2)
    expect(unmountedReaders).toBe(1)
    wrapper.unmount()
    await flushPromises()
  })

  it('shows loading and a safe retry path when work metadata cannot be read', async () => {
    let resolveWork: ((response: Response) => void) | undefined
    let attempt = 0
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (attempt === 0 && url === '/api/v1/works/work-1') {
        return new Promise<Response>((resolve) => { resolveWork = resolve })
      }
      if (attempt === 0) return Promise.resolve(json([documentOne]))
      if (url === '/api/v1/works/work-1') return Promise.resolve(json(work))
      if (url === '/api/v1/documents/document-1/reading-position') return Promise.resolve(json(null))
      if (url === '/api/v1/documents/document-1/annotations') return Promise.resolve(json([]))
      return Promise.resolve(json([documentOne]))
    }))
    const wrapper = await mountWorkspace()
    expect(wrapper.get('[role="status"]').text()).toContain('正在准备阅读工作台')
    resolveWork?.(json({ detail: 'D:\\private\\paper.pdf' }, 500))
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('暂时无法打开阅读工作台')
    expect(wrapper.text()).not.toContain('private')

    attempt = 1
    await wrapper.get('[data-test="retry-workspace"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Reliable Agents')
    wrapper.unmount()
    await flushPromises()
  })

  it('restores a document position, throttles writes and persists annotation intents', async () => {
    vi.useFakeTimers()
    const position = {
      document_version_id: 'document-1',
      document_content_sha256: 'a'.repeat(64),
      page_number: 2,
      page_count: 3,
      scale: 1.25,
      rotation: 90,
      progress: 66.6667,
      created_at: '2026-07-13T08:00:00Z',
      updated_at: '2026-07-13T08:00:00Z',
    }
    const existingAnnotation = {
      id: 'existing-annotation',
      document_version_id: 'document-1',
      document_content_sha256: 'a'.repeat(64),
      kind: 'bookmark',
      page_number: 1,
      page_width: 420,
      page_height: 595,
      quote_text: null,
      context_before: null,
      context_after: null,
      rects: [],
      color: 'green',
      note: 'Existing bookmark',
      created_at: '2026-07-13T08:00:00Z',
      updated_at: '2026-07-13T08:00:00Z',
    }
    const calls: Array<{ url: string, init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (!init?.method || init.method === 'GET') {
        if (url === '/api/v1/works/work-1') return json(work)
        if (url === '/api/v1/works/work-1/documents') return json([documentOne])
        if (url === '/api/v1/documents/document-1/reading-position') return json(position)
        if (url === '/api/v1/documents/document-1/annotations') return json([existingAnnotation])
      }
      if (url.endsWith('/reading-position') && init?.method === 'PUT') {
        const payload = JSON.parse(String(init.body))
        return json({
          ...position,
          page_number: payload.page_number,
          scale: payload.scale,
          rotation: payload.rotation,
          progress: payload.page_number / payload.page_count * 100,
          updated_at: '2026-07-13T08:05:00Z',
        })
      }
      if (url.endsWith('/annotations') && init?.method === 'POST') {
        const payload = JSON.parse(String(init.body))
        return json({
          id: `created-${calls.length}`,
          document_version_id: 'document-1',
          ...payload,
          created_at: '2026-07-13T08:06:00Z',
          updated_at: '2026-07-13T08:06:00Z',
        }, 201)
      }
      throw new Error(`Unexpected request ${url} ${init?.method ?? 'GET'}`)
    }))
    const wrapper = await mountWorkspace()
    await flushPromises()

    const stub = wrapper.get('[data-test="stub-page"]')
    expect(stub.text()).toBe('2')
    expect(stub.attributes('data-scale')).toBe('1.25')
    expect(stub.attributes('data-rotation')).toBe('90')
    expect(stub.attributes('data-annotations')).toBe('1')
    expect(wrapper.text()).toContain('Existing bookmark')

    await wrapper.get('[data-test="stub-next"]').trigger('click')
    await vi.advanceTimersByTimeAsync(699)
    expect(calls.filter((call) => call.init?.method === 'PUT')).toHaveLength(0)
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    visibility.mockRestore()
    const positionCall = calls.find((call) => call.url.endsWith('/reading-position') && call.init?.method === 'PUT')
    expect(JSON.parse(String(positionCall?.init?.body))).toEqual({
      document_content_sha256: 'a'.repeat(64),
      page_number: 3,
      page_count: 3,
      scale: 1.25,
      rotation: 90,
    })

    await wrapper.get('[data-test="stub-select"]').trigger('click')
    await wrapper.get('[data-test="create-highlight"]').trigger('click')
    await flushPromises()
    const createCall = calls.find((call) => call.url.endsWith('/annotations') && call.init?.method === 'POST')
    expect(JSON.parse(String(createCall?.init?.body))).toMatchObject({
      document_content_sha256: 'a'.repeat(64),
      kind: 'highlight',
      page_number: 2,
      page_count: 3,
      quote_text: 'selected workspace evidence',
      rects: [{ x0: 10, y0: 20, x1: 180, y1: 38 }],
    })
    expect(wrapper.text()).toContain('批注已保存')

    await wrapper.get('[data-test="create-bookmark"]').trigger('click')
    await flushPromises()
    const annotationCalls = calls.filter(
      (call) => call.url.endsWith('/annotations') && call.init?.method === 'POST',
    )
    expect(annotationCalls).toHaveLength(2)
    expect(JSON.parse(String(annotationCalls[1]?.init?.body))).toMatchObject({
      document_content_sha256: 'a'.repeat(64),
      kind: 'bookmark',
      page_number: 3,
      page_count: 3,
      quote_text: null,
      rects: [],
    })
    await wrapper.get('[data-test="stub-quick-highlight"]').trigger('click')
    await flushPromises()
    const quickCalls = calls.filter(
      (call) => call.url.endsWith('/annotations') && call.init?.method === 'POST',
    )
    expect(quickCalls).toHaveLength(3)
    expect(JSON.parse(String(quickCalls[2]?.init?.body))).toMatchObject({
      kind: 'highlight',
      color: 'amber',
      quote_text: 'selected workspace evidence',
    })
    wrapper.unmount()
    await flushPromises()
  })

  it('previews and explicitly accepts one cross-version annotation migration', async () => {
    const acceptedCandidate = {
      ...migrationCandidate,
      decision: 'accept' as const,
      target_annotation_id: 'migrated-annotation-1',
      decided_at: '2026-07-13T09:05:00Z',
    }
    const acceptedPreview = {
      ...migrationPreview,
      candidates: [acceptedCandidate],
      coverage: {
        ...migrationPreview.coverage,
        pending: 0,
        accepted: 1,
      },
    }
    const migratedAnnotation = {
      id: 'migrated-annotation-1',
      document_version_id: 'document-1',
      document_content_sha256: 'a'.repeat(64),
      kind: 'highlight' as const,
      page_number: 3,
      page_width: 420,
      page_height: 595,
      quote_text: 'Evidence moved in the revised paper',
      context_before: 'before',
      context_after: 'after',
      rects: [{ x0: 20, y0: 30, x1: 190, y1: 48 }],
      color: 'amber' as const,
      note: 'Keep this argument',
      created_at: '2026-07-13T09:05:00Z',
      updated_at: '2026-07-13T09:05:00Z',
    }
    const calls: Array<{ url: string, init?: RequestInit }> = []
    let annotationReads = 0
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/v1/works/work-1') return json(work)
      if (url === '/api/v1/works/work-1/documents') return json([documentOne, documentTwo])
      if (url === '/api/v1/documents/document-1/reading-position') return json(null)
      if (url === '/api/v1/documents/document-1/annotations') {
        annotationReads += 1
        return json(annotationReads === 1 ? [] : [migratedAnnotation])
      }
      if (
        url === '/api/v1/documents/document-2/annotation-migrations/preview'
        && init?.method === 'POST'
      ) return json(migrationPreview, 201)
      if (
        url === '/api/v1/annotation-migrations/candidates/migration-candidate-1/decision'
        && init?.method === 'PUT'
      ) return json(acceptedCandidate)
      if (url === '/api/v1/annotation-migrations/migration-preview-1') {
        return json(acceptedPreview)
      }
      throw new Error(`Unexpected request ${url} ${init?.method ?? 'GET'}`)
    }))

    const wrapper = await mountWorkspace()
    await flushPromises()
    expect(wrapper.get('[data-test="migration-source"]').element).toHaveProperty(
      'value',
      'document-2',
    )

    await wrapper.get('[data-test="start-migration-preview"]').trigger('click')
    await flushPromises()
    const previewCall = calls.find((call) => call.url.endsWith('/annotation-migrations/preview'))
    expect(previewCall?.init?.method).toBe('POST')
    expect(JSON.parse(String(previewCall?.init?.body))).toEqual({
      target_document_version_id: 'document-1',
    })
    expect(wrapper.text()).toContain('Evidence moved in the revised paper')

    await wrapper.get('[data-test="preview-migration-candidate"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="stub-page"]').text()).toBe('3')
    expect(wrapper.get('[data-test="stub-page"]').attributes('data-migration-preview')).toBe(
      'migration-candidate-1',
    )
    expect(wrapper.get('[data-test="stub-page"]').attributes('data-migration-page')).toBe('3')

    await wrapper.get('[data-test="accept-migration-candidate"]').trigger('click')
    await flushPromises()
    const decisionCall = calls.find((call) => call.url.endsWith('/decision'))
    expect(decisionCall?.init?.method).toBe('PUT')
    expect(JSON.parse(String(decisionCall?.init?.body))).toEqual({ decision: 'accept' })
    expect(wrapper.text()).toContain('已接受迁移')
    expect(wrapper.find('[data-test="annotation-migrated-annotation-1"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="stub-page"]').attributes('data-annotations')).toBe('1')
    expect(wrapper.get('[data-test="stub-page"]').attributes('data-migration-preview')).toBe('')
    wrapper.unmount()
    await flushPromises()
  })
})
