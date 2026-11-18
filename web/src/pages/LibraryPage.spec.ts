import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import LibraryPage from './LibraryPage.vue'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const availableDocument = {
  id: 'document-1',
  work_id: 'work-1',
  content_sha256: 'a'.repeat(64),
  access_status: 'user_provided',
  media_type: 'application/pdf',
  completeness: 100,
  extraction_status: 'complete',
  available: true,
  byte_size: 1024,
  original_filename: 'reader.pdf',
  acquisition_kind: 'manual_import',
  last_accessed_at: null,
  created_at: '2026-07-13T08:00:00Z',
  is_representative: true,
}

const works = [
  {
    id: 'work-1',
    title: 'Reliable Agents for Paper Reading',
    authors: ['Alice Chen', 'Ravi Kumar'],
    abstract: 'A local evidence workflow.',
    doi: '10.1000/reader',
    published_at: '2026-07-01T00:00:00Z',
    added_via: 'pdf',
    added_at: '2026-07-13T08:00:00Z',
    favorite: true,
    feedback: 'like',
    pinned: false,
    reading_progress: 42,
    analysis_state: 'not_analyzed',
    document_count: 2,
    representative_document: availableDocument,
  },
  {
    id: 'work-2',
    title: 'Metadata Only Study',
    authors: ['Bo Li'],
    abstract: null,
    doi: null,
    published_at: null,
    added_via: 'doi',
    added_at: '2026-07-12T08:00:00Z',
    favorite: false,
    feedback: 'neutral',
    pinned: false,
    reading_progress: 0,
    analysis_state: 'not_analyzed',
    document_count: 0,
    representative_document: null,
  },
]

async function mountLibrary() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/library', component: LibraryPage },
      { path: '/library/:workId/read/:documentVersionId', component: { template: '<p>reader</p>' } },
    ],
  })
  await router.push('/library')
  await router.isReady()
  return mount(LibraryPage, { global: { plugins: [router] } })
}

describe('LibraryPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it('lists saved works, opens lawful representative PDFs and filters by query', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      const query = new URL(url, 'http://local.test').searchParams.get('query')
      return json({
        items: query ? works.filter((work) => work.title.includes('Agents')) : works,
        total: query ? 1 : 2,
        limit: 20,
        offset: 0,
      })
    }))

    const wrapper = await mountLibrary()
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('我的论文库')
    expect(wrapper.text()).toContain('Reliable Agents for Paper Reading')
    expect(wrapper.text()).toContain('Alice Chen、Ravi Kumar')
    expect(wrapper.get('[data-test="read-work-1"]').attributes('href')).toBe(
      '/library/work-1/read/document-1',
    )
    expect(wrapper.get('[data-test="unavailable-work-2"]').text()).toContain('暂无可读 PDF')
    expect(wrapper.text()).toContain('42%')

    await wrapper.get('[data-test="library-search"]').setValue('Agents')
    await wrapper.get('[data-test="library-search-form"]').trigger('submit')
    await flushPromises()

    expect(calls.at(-1)).toContain('query=Agents')
    expect(wrapper.findAll('[data-test^="library-work-"]')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('Metadata Only Study')
  })

  it('shows honest loading, empty and retryable error states', async () => {
    let resolveRequest: ((response: Response) => void) | undefined
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => {
      resolveRequest = resolve
    })))
    const loading = await mountLibrary()
    expect(loading.get('[role="status"]').text()).toContain('正在整理本地论文库')
    resolveRequest?.(json({ items: [], total: 0, limit: 20, offset: 0 }))
    await flushPromises()
    expect(loading.get('[data-test="library-empty"]').text()).toContain('论文库还是空的')
    loading.unmount()

    let shouldFail = true
    vi.stubGlobal('fetch', vi.fn(async () => {
      if (shouldFail) return json({ detail: 'failure' }, 500)
      return json({ items: works.slice(0, 1), total: 1, limit: 20, offset: 0 })
    }))
    const failed = await mountLibrary()
    await flushPromises()
    expect(failed.get('[role="alert"]').text()).toContain('暂时无法读取论文库')
    shouldFail = false
    await failed.get('[data-test="retry-library"]').trigger('click')
    await flushPromises()
    expect(failed.find('[role="alert"]').exists()).toBe(false)
    expect(failed.text()).toContain('Reliable Agents')
  })

  it('opens lawful import entry points, refreshes the library and targets existing versions', async () => {
    const importedWork = {
      ...works[0],
      id: 'work-imported',
      title: 'Imported Local Paper',
      representative_document: {
        ...availableDocument,
        id: 'document-imported',
        work_id: 'work-imported',
        original_filename: 'imported.pdf',
      },
    }
    let libraryWorks: unknown[] = [works[0]]
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/v1/library/works?')) {
        return json({
          items: libraryWorks,
          total: libraryWorks.length,
          limit: 20,
          offset: 0,
        })
      }
      if (url === '/api/v1/library/imports/pdf' && init?.method === 'POST') {
        libraryWorks = [importedWork, ...libraryWorks]
        return json({
          work_id: 'work-imported',
          document_version_id: 'document-imported',
          content_sha256: 'c'.repeat(64),
          created_work: true,
          created_document: true,
        }, 201)
      }
      throw new Error(`Unexpected request ${url}`)
    }))
    const wrapper = await mountLibrary()
    await flushPromises()

    await wrapper.get('[data-test="open-library-import"]').trigger('click')
    await flushPromises()
    const file = document.querySelector<HTMLInputElement>('[data-test="pdf-import-file"]')
    if (!file) throw new Error('Missing PDF import input')
    Object.defineProperty(file, 'files', {
      configurable: true,
      value: [new File(['%PDF-1.4\nfixture'], 'imported.pdf', { type: 'application/pdf' })],
    })
    file.dispatchEvent(new Event('change', { bubbles: true }))
    document.querySelector<HTMLInputElement>('[data-test="pdf-import-rights"]')?.click()
    await flushPromises()
    document.querySelector<HTMLButtonElement>('[data-test="submit-pdf-import"]')?.click()
    await flushPromises()

    expect(wrapper.text()).toContain('Imported Local Paper')
    expect(wrapper.text()).toContain('论文和 PDF 已加入本地库')
    expect(wrapper.get('[data-test="read-work-imported"]').attributes('href')).toBe(
      '/library/work-imported/read/document-imported',
    )

    await wrapper.get('[data-test="add-version-work-1"]').trigger('click')
    await flushPromises()
    expect(document.body.textContent).toContain('作为新版本加入《Reliable Agents for Paper Reading》')
  })

  it('reuses server-backed feedback facts without changing the library optimistically', async () => {
    const putBodies: unknown[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/v1/library/works?')) {
        return json({ items: works.slice(0, 1), total: 1, limit: 20, offset: 0 })
      }
      if (url === '/api/v1/library/works/work-1' && !init?.method) {
        return json({
          work_id: 'work-1', favorite: true, feedback: 'like',
          feedback_reasons: ['strong_evidence'], pinned: false, reading_progress: 42,
          analysis_state: 'not_analyzed', persisted: true,
          created_at: '2026-07-13T08:00:00Z', updated_at: '2026-07-15T08:00:00Z',
        })
      }
      if (url === '/api/v1/library/works/work-1' && init?.method === 'PUT') {
        putBodies.push(JSON.parse(String(init.body)))
        return json({
          work_id: 'work-1', favorite: false, feedback: 'dislike',
          feedback_reasons: ['off_topic'], pinned: false, reading_progress: 42,
          analysis_state: 'not_analyzed', persisted: true,
          created_at: '2026-07-13T08:00:00Z', updated_at: '2026-07-15T08:01:00Z',
        })
      }
      throw new Error(`Unexpected request ${url}`)
    }))
    const wrapper = await mountLibrary()
    await flushPromises()
    const row = wrapper.get('[data-test="library-work-work-1"]')

    await row.get('[data-test="feedback-dislike"]').trigger('click')
    expect(putBodies).toHaveLength(0)
    await row.get('[data-test="feedback-reason-off_topic"]').setValue(true)
    await row.get('[data-test="save-feedback"]').trigger('click')
    await flushPromises()

    expect(putBodies).toEqual([{
      favorite: true,
      feedback: 'dislike',
      feedback_reasons: ['off_topic'],
      pinned: false,
      reading_progress: 42,
      analysis_state: 'not_analyzed',
    }])
    expect(row.get('[data-test="favorite-work"]').attributes('aria-pressed')).toBe('false')
    expect(row.get('[data-test="feedback-status"]').text()).toContain('评价已保存')
  })
})
