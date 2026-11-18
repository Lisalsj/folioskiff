import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { TranslationCommand } from './api'
import { useTranslationStore } from './store'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const artifact = {
  id: 'translation-1',
  work_id: 'work-1',
  version: 1,
  document_version_id: 'document-1',
  document_content_sha256: 'a'.repeat(64),
  context_snapshot_id: 'context-1',
  task_id: 'task-1',
  job_id: 'job-1',
  source_language: 'en',
  target_language: 'zh-CN',
  scope: 'full_document',
  status: 'partial',
  evidence_level: 'mixed_text',
  prompt_version: 'translation-v1',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:01:00Z',
  completed_at: '2026-08-01T00:01:00Z',
  terms: [],
} as const

const segments = [
  {
    segment_id: 'segment-000001',
    segment_index: 0,
    page_number: 1,
    block_numbers: [1],
    source_text: 'Diffusion model',
    source_sha256: 'b'.repeat(64),
    translated_text: '扩散模型',
    source_kind: 'native_text',
    ocr_confidence: null,
    status: 'succeeded',
    diagnostic: null,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:01:00Z',
  },
  {
    segment_id: 'segment-000002',
    segment_index: 1,
    page_number: 2,
    block_numbers: [2],
    source_text: 'Noisy evidence',
    source_sha256: 'c'.repeat(64),
    translated_text: null,
    source_kind: 'ocr_text',
    ocr_confidence: 0.72,
    status: 'failed',
    diagnostic: 'agent output was invalid',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:01:00Z',
  },
]

const job = {
  artifact_id: artifact.id,
  task_id: artifact.task_id,
  job_id: artifact.job_id,
  kind: 'translation.generate',
  status: 'queued',
  progress: 0,
  quick: false,
  cancel_requested: false,
  error_code: null,
  error_message: null,
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
}

describe('translation store', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => vi.unstubAllGlobals())

  it('reattaches an artifact and keeps partial diagnostics visible', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === `/api/v1/translations/${artifact.id}`) return json(artifact)
      if (url === `/api/v1/translations/${artifact.id}/segments`) return json(segments)
      if (url === `/api/v1/assistant/tasks/${artifact.task_id}`) {
        return json({ backend_id: 'codex_cli', requested_model_id: 'gpt-test' })
      }
      if (url === '/api/v1/jobs') {
        return json([{
          id: job.job_id,
          kind: job.kind,
          status: 'failed',
          progress: 50,
          cancel_requested: false,
          pause_requested: false,
        }])
      }
      throw new Error(`Unexpected request ${url}`)
    }))

    const store = useTranslationStore()
    expect(await store.load(artifact.id)).toBe(true)
    expect(store.progress).toEqual({ completed: 1, total: 2, percent: 50 })
    expect(store.pageRange).toBe('第 1 至 2 页')
    expect(store.diagnostics).toEqual(['第 2 页：agent output was invalid'])
    expect(store.agentLabel).toBe('codex_cli / gpt-test')
  })

  it('uses a fresh idempotency key for every create command', async () => {
    const keys: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/v1/translations' && init?.method === 'POST') {
        keys.push(new Headers(init.headers).get('Idempotency-Key') ?? '')
        return json(job, 202)
      }
      if (url === `/api/v1/translations/${artifact.id}`) return json(artifact)
      if (url === `/api/v1/translations/${artifact.id}/segments`) return json(segments)
      if (url === `/api/v1/assistant/tasks/${artifact.task_id}`) return json({}, 404)
      if (url === '/api/v1/jobs') return json([])
      throw new Error(`Unexpected request ${url}`)
    }))
    const command: TranslationCommand = {
      context: {
        document_version_id: 'document-1',
        scope: 'current_page',
        page_number: 1,
        selection: null,
        annotation_ids: [],
      },
      source_language: 'en',
      target_language: 'zh-CN',
      agent: {
        backend_id: 'codex_cli',
        model_id: null,
        reasoning_effort: 'medium',
        service_tier: 'flex',
      },
      user_terminology: [],
      network_policy: 'local_only',
    }

    const store = useTranslationStore()
    await store.create(command)
    await store.create(command)
    expect(keys).toHaveLength(2)
    expect(keys[0]).not.toBe(keys[1])
    expect(keys.every((key) => key.startsWith('translation-ui-'))).toBe(true)
  })

  it('resumes committed segments and confirms cancellation through the durable Job', async () => {
    let resumed = false
    let cancelled = false
    let resumeKey = ''
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === `/api/v1/translations/${artifact.id}/resume` && init?.method === 'POST') {
        resumed = true
        resumeKey = new Headers(init.headers).get('Idempotency-Key') ?? ''
        return json({ ...job, status: 'running', progress: 50 }, 202)
      }
      if (url === `/api/v1/jobs/${job.job_id}/cancel` && init?.method === 'POST') {
        cancelled = true
        return json({
          id: job.job_id,
          kind: job.kind,
          status: 'running',
          progress: 50,
          cancel_requested: true,
          pause_requested: false,
        })
      }
      if (url === `/api/v1/translations/${artifact.id}`) return json(artifact)
      if (url === `/api/v1/translations/${artifact.id}/segments`) return json(segments)
      if (url === `/api/v1/assistant/tasks/${artifact.task_id}`) return json({}, 404)
      if (url === '/api/v1/jobs') {
        return json([{
          id: job.job_id,
          kind: job.kind,
          status: resumed ? 'running' : 'failed',
          progress: resumed ? 50 : 0,
          cancel_requested: cancelled,
          pause_requested: false,
        }])
      }
      throw new Error(`Unexpected request ${init?.method ?? 'GET'} ${url}`)
    }))

    const store = useTranslationStore()
    await store.load(artifact.id)
    expect(await store.resume()).toMatchObject({ artifact_id: artifact.id, status: 'running' })
    expect(resumeKey).toMatch(/^translation-ui-/)
    expect(store.active).toBe(true)
    await store.cancel()
    expect(cancelled).toBe(true)
    expect(store.statusMessage).toContain('已请求取消')
  })
})
