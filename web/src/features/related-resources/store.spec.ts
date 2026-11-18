import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { RelatedSearchIntent, RelatedResourceSet } from './api'
import { useRelatedResourcesStore } from './store'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const draft: RelatedSearchIntent = {
  set_id: 'related-set-1',
  summary: '围绕扩散模型与规划智能体寻找方法和评测工作',
  required_groups: [['diffusion model', '扩散模型'], ['planning agent', '规划智能体']],
  optional_terms: ['evaluation', '评测'],
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

const results: RelatedResourceSet = {
  id: 'related-set-1',
  work_id: 'work-1',
  document_version_id: 'document-1',
  document_content_sha256: 'a'.repeat(64),
  context_snapshot_id: 'snapshot-1',
  intent_revision: 2,
  intent: { ...draft, revision: 2, confirmed: true, confirmed_at: '2026-08-01T01:00:00Z' },
  status: 'partial',
  include_academic_sources: true,
  include_external_web: true,
  external_web_consent: true,
  coverage: 80,
  job_id: 'related-job-1',
  sources: [],
  papers: [],
  knowledge_resources: [],
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T01:02:00Z',
  confirmed_at: '2026-08-01T01:00:00Z',
  completed_at: '2026-08-01T01:02:00Z',
}

describe('related resources store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.localStorage.clear()
    vi.stubGlobal('crypto', { randomUUID: () => 'related-test-uuid' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    window.localStorage.clear()
  })

  it('stays local and idle until the reader explicitly starts a draft', async () => {
    const calls: Array<{ url: string, init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/v1/related-resources/works/work-1/draft') return json(draft, 201)
      throw new Error(`Unexpected request ${init?.method ?? 'GET'} ${url}`)
    }))

    const store = useRelatedResourcesStore()
    store.initialize({
      workId: 'work-1',
      documentVersionId: 'document-1',
      documentContentSha256: 'a'.repeat(64),
    })
    expect(store.phase).toBe('idle')
    expect(calls).toEqual([])

    await store.beginDraft({
      document_version_id: 'document-1',
      scope: 'current_page',
      page_number: 3,
      annotation_ids: [],
    })

    expect(store.phase).toBe('draft')
    expect(store.intent?.set_id).toBe('related-set-1')
    expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({
      context: {
        document_version_id: 'document-1',
        scope: 'current_page',
        page_number: 3,
        annotation_ids: [],
      },
    })
    expect(calls.some(({ url }) => url.endsWith('/refine') || url.endsWith('/run'))).toBe(false)
  })

  it('requires confirmation and per-run external consent before separate retrieval', async () => {
    const requests: Array<{ url: string, init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      requests.push({ url, init })
      if (url.endsWith('/draft')) return json(draft, 201)
      if (url.endsWith('/intent') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { intent: RelatedSearchIntent }
        return json({
          ...body.intent,
          revision: 2,
          confirmed: true,
          confirmed_at: '2026-08-01T01:00:00Z',
        })
      }
      if (url.endsWith('/run') && init?.method === 'POST') {
        return json({
          set_id: 'related-set-1', job_id: 'related-job-1', status: 'queued',
          progress: 0, replayed: false,
        }, 202)
      }
      if (url === '/api/v1/related-resources/related-set-1') return json(results)
      throw new Error(`Unexpected request ${init?.method ?? 'GET'} ${url}`)
    }))

    const store = useRelatedResourcesStore()
    store.initialize({
      workId: 'work-1', documentVersionId: 'document-1', documentContentSha256: 'a'.repeat(64),
    })
    await store.beginDraft({
      document_version_id: 'document-1', scope: 'current_page', page_number: 1,
      annotation_ids: [],
    })
    store.editIntent({ ...draft, optional_terms: [...draft.optional_terms, 'benchmark'] })
    expect(store.canRun).toBe(false)
    await store.confirmIntent()
    expect(store.phase).toBe('confirm')

    store.includeExternalWeb = true
    expect(store.canRun).toBe(false)
    store.externalWebConsent = true
    expect(store.canRun).toBe(true)
    await store.run({
      backend_id: 'codex_cli', model_id: 'fixture-model',
      reasoning_effort: 'medium', service_tier: 'flex',
    })

    expect(store.phase).toBe('running')
    const runRequest = requests.find(({ url }) => url.endsWith('/run'))
    expect(new Headers(runRequest?.init?.headers).get('Idempotency-Key')).toBe(
      'related-ui-related-test-uuid',
    )
    expect(JSON.parse(String(runRequest?.init?.body))).toMatchObject({
      include_academic_sources: true,
      include_external_web: true,
      external_web_consent: true,
      agent: { backend_id: 'codex_cli' },
    })
    expect(window.localStorage.getItem('paper-reader.related.external-web-enabled')).toBe('true')

    await store.refreshResults()
    expect(store.phase).toBe('results')
    expect(store.resourceSet?.status).toBe('partial')
  })

  it('persists local edits before an explicit Agent refinement', async () => {
    const calls: Array<{ url: string, init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url.endsWith('/draft')) return json(draft, 201)
      if (url.endsWith('/intent') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { intent: RelatedSearchIntent, confirm: boolean }
        expect(body.confirm).toBe(false)
        expect(body.intent.optional_terms).toContain('reproducibility')
        return json({ ...body.intent, revision: 2, confirmed: false })
      }
      if (url.endsWith('/refine') && init?.method === 'POST') {
        return json({
          ...draft,
          revision: 3,
          optional_terms: [...draft.optional_terms, 'reproducibility', 'benchmark suite'],
          diff: {
            ...draft.diff,
            added_terms: ['benchmark suite'],
          },
        })
      }
      throw new Error(`Unexpected request ${init?.method ?? 'GET'} ${url}`)
    }))

    const store = useRelatedResourcesStore()
    store.initialize({
      workId: 'work-1', documentVersionId: 'document-1', documentContentSha256: 'a'.repeat(64),
    })
    await store.beginDraft({
      document_version_id: 'document-1', scope: 'current_page', page_number: 1,
      annotation_ids: [],
    })
    store.editIntent({ ...draft, optional_terms: [...draft.optional_terms, 'reproducibility'] })
    await store.refine('再加入可复现基准。', {
      backend_id: 'codex_cli', model_id: null, reasoning_effort: 'medium', service_tier: 'flex',
    })

    const relatedMutations = calls.filter(({ url }) => !url.endsWith('/draft'))
    expect(relatedMutations.map(({ url }) => url.split('/').at(-1))).toEqual(['intent', 'refine'])
    expect(store.intent?.revision).toBe(3)
    expect(store.intent?.diff?.added_terms).toContain('benchmark suite')
  })

  it('blocks confirmation while required local intent fields are blank', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/draft')) return json(draft, 201)
      throw new Error(`Unexpected request ${url}`)
    }))

    const store = useRelatedResourcesStore()
    store.initialize({
      workId: 'work-1', documentVersionId: 'document-1', documentContentSha256: 'a'.repeat(64),
    })
    await store.beginDraft({
      document_version_id: 'document-1', scope: 'current_page', page_number: 1,
      annotation_ids: [],
    })
    expect(store.canConfirm).toBe(true)

    store.editIntent({ ...draft, required_groups: [['   ']] })
    expect(store.canConfirm).toBe(false)
  })

  it('offers explicit reload recovery without making a related request during initialize', async () => {
    window.localStorage.setItem(
      `paper-reader.related.last.work-1.document-1.${'a'.repeat(64)}`,
      'related-set-1',
    )
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      if (url === '/api/v1/related-resources/related-set-1') return json(results)
      throw new Error(`Unexpected request ${url}`)
    }))

    const store = useRelatedResourcesStore()
    store.initialize({
      workId: 'work-1', documentVersionId: 'document-1', documentContentSha256: 'a'.repeat(64),
    })
    expect(store.resumeAvailable).toBe(true)
    expect(calls).toEqual([])

    await store.resume()
    expect(calls).toEqual(['/api/v1/related-resources/related-set-1'])
    expect(store.phase).toBe('results')
  })

  it('restores the visible external consent boundary for an active run', async () => {
    window.localStorage.setItem(
      `paper-reader.related.last.work-1.document-1.${'a'.repeat(64)}`,
      'related-set-1',
    )
    const running: RelatedResourceSet = {
      ...results,
      status: 'running',
      completed_at: null,
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/v1/related-resources/related-set-1') return json(running)
      if (url === '/api/v1/jobs') {
        return json([{
          id: 'related-job-1', kind: 'related.search', status: 'running', progress: 35,
          cancel_requested: false, pause_requested: false,
        }])
      }
      throw new Error(`Unexpected request ${url}`)
    }))

    const store = useRelatedResourcesStore()
    store.initialize({
      workId: 'work-1', documentVersionId: 'document-1', documentContentSha256: 'a'.repeat(64),
    })
    await store.resume()

    expect(store.phase).toBe('running')
    expect(store.includeExternalWeb).toBe(true)
    expect(store.externalWebConsent).toBe(true)
  })
})
