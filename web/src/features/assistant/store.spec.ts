import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAssistantStore, type AssistantReaderContext } from './store'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
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
  default_agent_model_id: 'fixture-model',
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
  models: [{
    model_id: 'fixture-model',
    display_name: 'Fixture model',
    context_window: 120_000,
    supports_vision: false,
  }],
  degradation_reasons: [],
  recommended_action: null,
}

const emptyWork = {
  work_id: 'work-1',
  session_id: null,
  tasks: [],
  messages: [],
  latest_checkpoint: null,
}

const readerContext: AssistantReaderContext = {
  currentPage: 2,
  pageCount: 8,
  selection: {
    pageNumber: 2,
    pageCount: 8,
    pageWidth: 420,
    pageHeight: 595,
    quoteText: 'selected evidence',
    contextBefore: 'before',
    contextAfter: 'after',
    rects: [{ x0: 10, y0: 20, x1: 160, y1: 38 }],
    verified: true,
  },
  annotations: [],
}

describe('assistant store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.stubGlobal('crypto', { randomUUID: () => 'assistant-test-uuid' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    window.sessionStorage.clear()
  })

  it('loads durable history and defaults without creating a task', async () => {
    const calls: Array<{ url: string, method: string }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push({ url, method })
      if (url === '/api/v1/assistant/works/work-1') return json(emptyWork)
      if (url === '/api/v1/settings/agents') return json([capability])
      if (url === '/api/v1/settings/preferences') return json(preferences)
      throw new Error(`Unexpected request ${method} ${url}`)
    }))

    const store = useAssistantStore()
    await store.initialize({
      workId: 'work-1',
      documentVersionId: 'document-1',
      documentContentSha256: 'a'.repeat(64),
    })

    expect(store.loading).toBe(false)
    expect(store.backendId).toBe('codex_cli')
    expect(store.modelId).toBe('fixture-model')
    expect(store.reasoningEffort).toBe('medium')
    expect(calls.some(({ url, method }) => method === 'POST' && url.endsWith('/tasks'))).toBe(false)
  })

  it('freezes the explicit PDF selection and uses a fresh idempotency key only on send', async () => {
    const calls: Array<{ url: string, init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/v1/assistant/works/work-1' && (!init?.method || init.method === 'GET')) {
        return json(emptyWork)
      }
      if (url === '/api/v1/settings/agents') return json([capability])
      if (url === '/api/v1/settings/preferences') return json(preferences)
      if (url === '/api/v1/assistant/works/work-1/tasks' && init?.method === 'POST') {
        return json({
          id: 'job-1',
          task_id: 'task-1',
          kind: 'assistant.reply',
          status: 'queued',
          progress: 0,
          cancel_requested: false,
          error_code: null,
          error_message: null,
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-01T00:00:00Z',
        }, 202)
      }
      throw new Error(`Unexpected request ${init?.method ?? 'GET'} ${url}`)
    }))

    const store = useAssistantStore()
    await store.initialize({
      workId: 'work-1',
      documentVersionId: 'document-1',
      documentContentSha256: 'a'.repeat(64),
    })
    store.mode = 'explain'
    store.scope = 'selection'
    store.setDraft('解释这段证据如何支持作者结论。')
    expect(window.sessionStorage.getItem('paper-reader.assistant-draft.work-1')).toContain('解释')

    await store.send(readerContext)

    const create = calls.find(({ url, init }) => url.endsWith('/tasks') && init?.method === 'POST')
    expect(new Headers(create?.init?.headers).get('Idempotency-Key')).toBe(
      'assistant-ui-assistant-test-uuid',
    )
    expect(JSON.parse(String(create?.init?.body))).toEqual({
      mode: 'explain',
      message: '解释这段证据如何支持作者结论。',
      context: {
        document_version_id: 'document-1',
        scope: 'selection',
        selection: {
          page_number: 2,
          page_width: 420,
          page_height: 595,
          quote_text: 'selected evidence',
          context_before: 'before',
          context_after: 'after',
          rects: [{ x0: 10, y0: 20, x1: 160, y1: 38 }],
        },
        annotation_ids: [],
      },
      agent: {
        backend_id: 'codex_cli',
        model_id: 'fixture-model',
        reasoning_effort: 'medium',
        service_tier: 'flex',
      },
      network_policy: 'local_only',
      output_language: 'zh-CN',
    })
    expect(store.activeJobId).toBe('job-1')
    expect(store.draft).toBe('')
  })

  it('reattaches a running task, batches fragments and polls only without SSE', async () => {
    vi.useFakeTimers()
    const frames: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => {
      frames.push(callback)
      return frames.length
    }))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
    const calls: string[] = []
    const context = {
      id: 'snapshot-1',
      work_id: 'work-1',
      document_version_id: 'document-1',
      document_content_sha256: 'a'.repeat(64),
      scope: 'current_page',
      page_numbers: [2],
      selection: null,
      annotation_ids: [],
      section_fallback: false,
      extraction_version: 'fixture',
      warnings: [],
      evidence_manifest: {},
      evidence_sha256: 'b'.repeat(64),
      snapshot_sha256: 'c'.repeat(64),
      created_at: '2026-08-01T00:00:00Z',
    }
    const queuedWork = {
      ...emptyWork,
      session_id: 'session-1',
      tasks: [{
        id: 'task-running',
        session_id: 'session-1',
        retry_of_task_id: null,
        mode: 'ask',
        status: 'running',
        network_policy: 'local_only',
        backend_id: 'codex_cli',
        requested_model_id: 'fixture-model',
        reasoning_effort: 'medium',
        service_tier: 'flex',
        output_language: 'zh-CN',
        job_id: 'job-running',
        error_code: null,
        diagnostic: null,
        retryable: false,
        context,
        latest_run: null,
        created_at: '2026-08-01T00:00:00Z',
        started_at: '2026-08-01T00:00:01Z',
        completed_at: null,
        updated_at: '2026-08-01T00:00:01Z',
      }],
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      if (url === '/api/v1/assistant/works/work-1') return json(queuedWork)
      if (url === '/api/v1/settings/agents') return json([capability])
      if (url === '/api/v1/settings/preferences') return json(preferences)
      if (url === '/api/v1/jobs') {
        return json([{
          id: 'job-running',
          kind: 'assistant.reply',
          status: 'running',
          progress: 35,
          cancel_requested: false,
          pause_requested: false,
        }])
      }
      if (url.includes('/api/v1/assistant/tasks/task-running/fragments')) {
        return json([
          {
            task_id: 'task-running',
            sequence: 0,
            run_id: 'run-1',
            kind: 'text',
            text: 'first',
            created_at: '2026-08-01T00:00:02Z',
          },
          {
            task_id: 'task-running',
            sequence: 1,
            run_id: 'run-1',
            kind: 'text',
            text: ' second',
            created_at: '2026-08-01T00:00:03Z',
          },
        ])
      }
      throw new Error(`Unexpected request ${url}`)
    }))

    const store = useAssistantStore()
    await store.initialize({
      workId: 'work-1',
      documentVersionId: 'document-1',
      documentContentSha256: 'a'.repeat(64),
    })

    expect(store.activeJobId).toBe('job-running')
    expect(store.fragments).toEqual([])
    await vi.waitFor(() => expect(frames).toHaveLength(1))
    frames.shift()?.(0)
    expect(store.fragments.map((item) => item.text).join('')).toBe('first second')

    const jobsBeforePoll = calls.filter((url) => url === '/api/v1/jobs').length
    await vi.advanceTimersByTimeAsync(1_500)
    expect(calls.filter((url) => url === '/api/v1/jobs').length).toBeGreaterThan(jobsBeforePoll)

    const jobStore = (await import('@/stores/jobs')).useJobStore()
    jobStore.connection = 'connected'
    await vi.advanceTimersByTimeAsync(1_500)
    const jobsWithSse = calls.filter((url) => url === '/api/v1/jobs').length
    await vi.advanceTimersByTimeAsync(3_000)
    expect(calls.filter((url) => url === '/api/v1/jobs')).toHaveLength(jobsWithSse)

    store.dispose()
    await vi.advanceTimersByTimeAsync(3_000)
    expect(calls.filter((url) => url === '/api/v1/jobs')).toHaveLength(jobsWithSse)
  })

  it('discards late fragments after the reader switches to another work', async () => {
    const frames: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => {
      frames.push(callback)
      return frames.length
    }))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())

    let fragmentRequested = false
    let resolveFragments: (response: Response) => void = () => undefined
    const fragmentResponse = new Promise<Response>((resolve) => {
      resolveFragments = resolve
    })
    const context = {
      id: 'snapshot-1',
      work_id: 'work-1',
      document_version_id: 'document-1',
      document_content_sha256: 'a'.repeat(64),
      scope: 'current_page',
      page_numbers: [2],
      selection: null,
      annotation_ids: [],
      section_fallback: false,
      extraction_version: 'fixture',
      warnings: [],
      evidence_manifest: {},
      evidence_sha256: 'b'.repeat(64),
      snapshot_sha256: 'c'.repeat(64),
      created_at: '2026-08-01T00:00:00Z',
    }
    const runningWork = {
      ...emptyWork,
      session_id: 'session-1',
      tasks: [{
        id: 'task-running',
        session_id: 'session-1',
        retry_of_task_id: null,
        mode: 'ask',
        status: 'running',
        network_policy: 'local_only',
        backend_id: 'codex_cli',
        requested_model_id: 'fixture-model',
        reasoning_effort: 'medium',
        service_tier: 'flex',
        output_language: 'zh-CN',
        job_id: 'job-running',
        error_code: null,
        diagnostic: null,
        retryable: false,
        context,
        latest_run: null,
        created_at: '2026-08-01T00:00:00Z',
        started_at: '2026-08-01T00:00:01Z',
        completed_at: null,
        updated_at: '2026-08-01T00:00:01Z',
      }],
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/v1/assistant/works/work-1') return json(runningWork)
      if (url === '/api/v1/assistant/works/work-2') {
        return json({ ...emptyWork, work_id: 'work-2' })
      }
      if (url === '/api/v1/settings/agents') return json([capability])
      if (url === '/api/v1/settings/preferences') return json(preferences)
      if (url === '/api/v1/jobs') {
        return json([{
          id: 'job-running',
          kind: 'assistant.reply',
          status: 'running',
          progress: 35,
          cancel_requested: false,
          pause_requested: false,
        }])
      }
      if (url.includes('/api/v1/assistant/tasks/task-running/fragments')) {
        fragmentRequested = true
        return fragmentResponse
      }
      throw new Error(`Unexpected request ${url}`)
    }))

    const store = useAssistantStore()
    const firstInitialization = store.initialize({
      workId: 'work-1',
      documentVersionId: 'document-1',
      documentContentSha256: 'a'.repeat(64),
    })
    await vi.waitFor(() => expect(fragmentRequested).toBe(true))

    await store.initialize({
      workId: 'work-2',
      documentVersionId: 'document-2',
      documentContentSha256: 'd'.repeat(64),
    })
    resolveFragments(json([{
      task_id: 'task-running',
      sequence: 0,
      run_id: 'run-1',
      kind: 'text',
      text: 'stale fragment',
      created_at: '2026-08-01T00:00:02Z',
    }]))
    await firstInitialization
    await new Promise((resolve) => window.setTimeout(resolve, 0))

    expect(frames).toEqual([])
    expect(store.fragments).toEqual([])
    expect(store.tasks).toEqual([])
  })
})
