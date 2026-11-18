import { createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PublishingPage from './PublishingPage.vue'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const works = [
  {
    id: 'work-1',
    title: 'Controllable Diffusion Agents',
    authors: ['Lin', 'Zhao'],
    abstract: 'A public abstract for the first paper.',
    doi: '10.1000/diffusion',
    published_at: '2026-06-12T00:00:00Z',
    added_via: 'recommendation',
    added_at: '2026-07-13T08:00:00Z',
    favorite: true,
    feedback: 'liked',
    pinned: false,
    reading_progress: 72,
    analysis_state: 'available',
    document_count: 1,
    representative_document: null,
  },
  {
    id: 'work-2',
    title: 'Evidence-Grounded Research Agents',
    authors: ['Wang'],
    abstract: 'A public abstract for the second paper.',
    doi: null,
    published_at: '2026-07-01T00:00:00Z',
    added_via: 'manual',
    added_at: '2026-07-13T08:00:00Z',
    favorite: false,
    feedback: 'neutral',
    pinned: false,
    reading_progress: 10,
    analysis_state: 'not_analyzed',
    document_count: 0,
    representative_document: null,
  },
]

const report = {
  id: 'report-formal-1',
  work_id: 'work-1',
  document_version_id: 'document-1',
  document_content_sha256: 'a'.repeat(64),
  evidence_sha256: 'b'.repeat(64),
  version: 3,
  job_id: 'report-job-1',
  backend_id: 'codex_cli',
  requested_model_id: 'gpt-fixture',
  resolved_model_id: 'gpt-fixture',
  reasoning_effort: 'high',
  language: 'zh-CN',
  evidence_level: 'full_text',
  schema_version: 'analysis-report-v1',
  created_at: '2026-07-13T08:00:00Z',
}

const git = {
  enabled: true,
  reason: null,
  git_available: true,
  repository_detected: true,
  repository_root: 'D:\\research\\paper-reader',
  remote_name: 'origin',
  remote_url: 'https://github.com/Lisalsj/folioskiff.git',
  branch: 'published',
  site_root: 'site',
  workflow_path: '.github/workflows/pages.yml',
  target_fingerprint: 'c'.repeat(64),
  stale_worktree_count: 0,
}

const included = [
  'curated title and public introduction',
  'canonical paper title, authors, abstract, DOI and publication date',
  'latest objective quality score summary when available',
  'explicitly selected formal analysis report when available',
]
const excluded = [
  'PDF and extracted full text',
  'annotations, private notes and reading progress',
  'paper conversations and preference events',
  'credentials',
  'complete prompts, failure drafts and model diagnostics',
  'private score evidence and source raw payloads',
  'local filesystem paths',
]
const files = [
  '.github/workflows/pages.yml',
  'site/data.json',
  'site/index.html',
  'site/manifest.json',
]

function preview(selection: Record<string, unknown>) {
  const requested = selection.works as Array<{ work_id: string, report_id?: string | null }>
  return {
    preview: {
      selection,
      fingerprint: 'd'.repeat(64),
      payload: {
        schema_version: 'paper-reader-publication-v1',
        slug: selection.slug,
        title: selection.title,
        public_intro: selection.public_intro,
        works: requested.map((item) => {
          const work = works.find((candidate) => candidate.id === item.work_id)!
          return {
            canonical_work_id: work.id,
            title: work.title,
            authors: work.authors,
            abstract: work.abstract,
            doi: work.doi,
            published_at: work.published_at,
            quality_score: null,
            report: item.report_id ? {
              id: item.report_id,
              version: 3,
              evidence_level: 'full_text',
              language: 'zh-CN',
              schema_version: 'analysis-report-v1',
              report_sha256: 'e'.repeat(64),
              created_at: report.created_at,
              content: {},
            } : null,
          }
        }),
      },
    },
    git,
    files,
    included_fields: included,
    excluded_fields: excluded,
  }
}

function job(
  id: string,
  status: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    id,
    kind: 'publishing.publish',
    status,
    progress: status === 'queued' ? 0 : status === 'running' ? 5 : 100,
    payload: {},
    result: null,
    checkpoint: status === 'running' ? { phase: 'worktree' } : null,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    created_at: '2026-07-14T08:00:00Z',
    updated_at: '2026-07-14T08:00:00Z',
    ...overrides,
  }
}

function installBaseRoutes(
  requests: Array<{ url: string, method: string, body?: unknown }>,
  extra?: (
    url: string,
    method: string,
    init: RequestInit | undefined,
  ) => Response | Promise<Response> | undefined,
): void {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
    requests.push({ url, method, body })
    const handled = await extra?.(url, method, init)
    if (handled) return handled
    if (url.startsWith('/api/v1/library/works?') && method === 'GET') {
      return response({ items: works, total: works.length, limit: 100, offset: 0 })
    }
    if (url === '/api/v1/publishing/git' && method === 'GET') return response(git)
    if (url === '/api/v1/jobs' && method === 'GET') return response([])
    if (url === '/api/v1/analysis/works/work-1/reports' && method === 'GET') {
      return response([report])
    }
    if (url === '/api/v1/analysis/works/work-2/reports' && method === 'GET') {
      return response([])
    }
    throw new Error(`Unexpected request: ${method} ${url}`)
  }))
}

function mountPage() {
  return mount(PublishingPage, {
    attachTo: document.body,
    global: { plugins: [createPinia()] },
  })
}

describe('PublishingPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
    document.cookie = 'paper_reader_csrf=; Max-Age=0; path=/'
  })

  it('freezes an ordered public-only preview and discards it without creating a snapshot job', async () => {
    const requests: Array<{ url: string, method: string, body?: unknown }> = []
    installBaseRoutes(requests, (url, method, init) => {
      if (url.startsWith('/api/v1/library/works?')) {
        const library = [
          works[0]!,
          ...Array.from({ length: 99 }, (_, index) => ({
            ...works[0]!, id: `other-${index}`, title: `Other ${index}`,
          })),
          works[1]!,
        ]
        const params = new URL(url, 'http://localhost').searchParams
        const offset = Number(params.get('offset'))
        const limit = Number(params.get('limit'))
        return response({ items: library.slice(offset, offset + limit), total: library.length, limit, offset })
      }
      if (url === '/api/v1/publishing/previews' && method === 'POST') {
        return response(preview(JSON.parse(String(init?.body))))
      }
      return undefined
    })
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('精选发布')
    expect(wrapper.text()).toContain('批注、聊天、失败草稿和完整提示词没有发布入口')
    expect(wrapper.find('[data-test*="annotation"]').exists()).toBe(false)
    expect(wrapper.find('[data-test*="conversation"]').exists()).toBe(false)
    expect(wrapper.find('[data-test*="failure-draft"]').exists()).toBe(false)

    await wrapper.get('[data-test="select-publishing-work-1"]').setValue(true)
    for (let page = 2; page <= 6; page += 1) {
      const next = wrapper.get<HTMLButtonElement>('nav[aria-label="可选论文分页"] button:last-child')
      next.element.focus()
      await next.trigger('click')
      await flushPromises()
      expect(document.activeElement).toBe(wrapper.get('input[type="checkbox"]').element)
    }
    expect(wrapper.get('input[type="checkbox"]').element.closest('label')?.textContent).toContain(works[1]!.title)
    await wrapper.get('[data-test="select-publishing-work-1"]').setValue(true)
    await flushPromises()
    await wrapper.get('[data-test="publishing-report-work-1"]').setValue('report-formal-1')
    await wrapper.get('[data-test="move-publishing-work-2-up"]').trigger('click')
    await wrapper.get('[data-test="publishing-slug"]').setValue('agent-reading-notes')
    await wrapper.get('[data-test="publishing-title"]').setValue('Agent 阅读精选')
    await wrapper.get('[data-test="publishing-intro"]').setValue('只公开经过核对的论文与正式报告。')
    await wrapper.get('[data-test="create-publishing-preview"]').trigger('click')
    await flushPromises()

    const previewRequest = requests.find(
      (request) => request.url === '/api/v1/publishing/previews' && request.method === 'POST',
    )
    expect(previewRequest?.body).toEqual({
      slug: 'agent-reading-notes',
      title: 'Agent 阅读精选',
      public_intro: '只公开经过核对的论文与正式报告。',
      display_language: 'zh-CN',
      works: [
        { work_id: 'work-2', report_id: null },
        { work_id: 'work-1', report_id: 'report-formal-1' },
      ],
    })
    expect(wrapper.get('[data-test="publishing-preview"]').text()).toContain(
      'Evidence-Grounded Research Agents',
    )
    expect(wrapper.get('[data-test="publishing-preview"]').text()).toContain(
      '.github/workflows/pages.yml',
    )
    expect(wrapper.get('[data-test="publishing-preview"]').text()).toContain(
      'annotations, private notes and reading progress',
    )
    expect(wrapper.get('[data-test="publishing-preview"]').text()).toContain(
      'https://github.com/Lisalsj/folioskiff.git',
    )
    expect(wrapper.get('[data-test="publishing-preview"]').text()).toContain('published')
    expect(document.activeElement).toBe(
      wrapper.get('[data-test="publishing-preview-heading"]').element,
    )
    expect(requests.filter((request) => request.url === '/api/v1/publishing/jobs')).toHaveLength(0)
    expect(requests.filter((request) => request.url.endsWith('/settings/confirm'))).toHaveLength(0)

    await wrapper.get('[data-test="discard-publishing-preview"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="publishing-preview"]').exists()).toBe(false)
    expect(document.activeElement).toBe(
      wrapper.get('[data-test="publishing-composer-heading"]').element,
    )
    expect(wrapper.get<HTMLInputElement>('[data-test="publishing-title"]').element.value).toBe(
      'Agent 阅读精选',
    )
    expect(requests.filter((request) => request.url === '/api/v1/publishing/jobs')).toHaveLength(0)
  })

  it('requires a second confirmation, preserves an older success, and retries one frozen snapshot', async () => {
    document.cookie = 'paper_reader_csrf=csrf; path=/'
    const requests: Array<{ url: string, method: string, body?: unknown }> = []
    const privateCanary = 'D:\\private\\publication-worktree\\secret.txt'
    const oldSuccess = job('publish-old', 'succeeded', {
      result: {
        snapshot_id: 'snapshot-old',
        snapshot_sha256: '1'.repeat(64),
        remote_name: 'origin',
        remote_url: git.remote_url,
        branch: 'published',
        commit_sha: '2'.repeat(40),
        workflow_path: git.workflow_path,
        site_root: git.site_root,
      },
    })
    const failed = job('publish-failed', 'failed', {
      result: { snapshot_id: 'snapshot-frozen', retryable: true, worktree_path: privateCanary },
      checkpoint: { phase: 'failed', snapshot_id: 'snapshot-frozen' },
      error_code: 'push_rejected',
      error_message: privateCanary,
    })
    const retried = job('publish-retry', 'succeeded', {
      payload: { retry_of_job_id: 'publish-failed' },
      result: {
        snapshot_id: 'snapshot-frozen',
        snapshot_sha256: '3'.repeat(64),
        remote_name: 'origin',
        remote_url: git.remote_url,
        branch: 'published',
        commit_sha: '4'.repeat(40),
        workflow_path: git.workflow_path,
        site_root: git.site_root,
        worktree_path: privateCanary,
      },
    })
    installBaseRoutes(requests, (url, method, init) => {
      if (url === '/api/v1/jobs' && method === 'GET') {
        return response([{
          id: oldSuccess.id,
          kind: oldSuccess.kind,
          status: oldSuccess.status,
          progress: oldSuccess.progress,
          cancel_requested: false,
        }])
      }
      if (url === '/api/v1/jobs/publish-old' && method === 'GET') return response(oldSuccess)
      if (url === '/api/v1/publishing/previews' && method === 'POST') {
        return response(preview(JSON.parse(String(init?.body))))
      }
      if (url === '/api/v1/publishing/settings/confirm' && method === 'POST') {
        expect(JSON.parse(String(init?.body))).toEqual({
          target_fingerprint: git.target_fingerprint,
        })
        return response({ confirmed: true, git })
      }
      if (url === '/api/v1/publishing/jobs' && method === 'POST') return response(failed, 202)
      if (url === '/api/v1/publishing/jobs/publish-failed/retry' && method === 'POST') {
        return response(retried, 202)
      }
      return undefined
    })
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.text()).toContain('snapshot-old')
    expect(wrapper.text()).toContain('2'.repeat(40))

    await wrapper.get('[data-test="select-publishing-work-1"]').setValue(true)
    await flushPromises()
    await wrapper.get('[data-test="create-publishing-preview"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="open-publish-confirmation"]').trigger('click')
    await flushPromises()

    expect(document.querySelector('[data-test="publishing-confirmation"]')).not.toBeNull()
    expect(requests.filter((request) => request.url === '/api/v1/publishing/jobs')).toHaveLength(0)
    document.querySelector<HTMLButtonElement>('[data-test="confirm-publishing"]')?.click()
    await flushPromises()

    expect(requests.filter((request) => request.url.endsWith('/settings/confirm'))).toHaveLength(1)
    expect(requests.filter((request) => request.url === '/api/v1/publishing/jobs')).toHaveLength(1)
    expect(wrapper.text()).toContain('远端发布分支已经变化')
    expect(wrapper.text()).toContain('snapshot-frozen')
    expect(wrapper.text()).toContain('snapshot-old')
    expect(wrapper.text()).not.toContain(privateCanary)

    await wrapper.get('[data-test="retry-publication-publish-failed"]').trigger('click')
    await flushPromises()
    expect(requests.filter((request) => request.url.endsWith('/retry'))).toHaveLength(1)
    expect(wrapper.text()).toContain('snapshot-frozen')
    expect(wrapper.text()).toContain('4'.repeat(40))
    expect(wrapper.text()).toContain('published')
    expect(wrapper.text()).not.toContain(privateCanary)
  })

  it('sends a visible cancellation request for a running publication', async () => {
    vi.useFakeTimers()
    const requests: Array<{ url: string, method: string, body?: unknown }> = []
    const running = job('publish-running', 'running')
    const cancelled = job('publish-running', 'running', {
      cancel_requested: true,
    })
    installBaseRoutes(requests, (url, method) => {
      if (url === '/api/v1/jobs' && method === 'GET') {
        return response([{
          id: running.id,
          kind: running.kind,
          status: running.status,
          progress: running.progress,
          cancel_requested: false,
        }])
      }
      if (url === '/api/v1/jobs/publish-running' && method === 'GET') return response(running)
      if (url === '/api/v1/jobs/publish-running/cancel' && method === 'POST') {
        return response({
          id: cancelled.id,
          kind: cancelled.kind,
          status: cancelled.status,
          progress: cancelled.progress,
          cancel_requested: true,
        })
      }
      return undefined
    })
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('在临时工作树构建并推送')
    await wrapper.get('[data-test="cancel-publication-publish-running"]').trigger('click')
    await flushPromises()

    expect(requests).toContainEqual({
      url: '/api/v1/jobs/publish-running/cancel',
      method: 'POST',
      body: undefined,
    })
    expect(wrapper.text()).toContain('正在取消')
    wrapper.unmount()
    vi.useRealTimers()
  })
})
