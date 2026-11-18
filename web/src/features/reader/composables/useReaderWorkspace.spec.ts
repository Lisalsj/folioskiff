import { defineComponent, h, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import type { components } from '@/api/schema'
import { useReaderWorkspace } from './useReaderWorkspace'

type LibraryWork = components['schemas']['LibraryWorkResponse']
type LibraryDocument = components['schemas']['LibraryDocumentResponse']

const documentOne = {
  id: 'document-1', work_id: 'work-1', content_sha256: 'a'.repeat(64),
  access_status: 'user_provided', media_type: 'application/pdf', completeness: 100,
  extraction_status: 'complete', available: true, byte_size: 2048,
  original_filename: 'reader-v1.pdf', acquisition_kind: 'manual_import',
  last_accessed_at: null, created_at: '2026-07-13T08:00:00Z', is_representative: true,
} satisfies LibraryDocument
const documentTwo = {
  ...documentOne,
  id: 'document-2',
  content_sha256: 'b'.repeat(64),
  original_filename: 'reader-v0.pdf',
  is_representative: false,
} satisfies LibraryDocument
const work = {
  id: 'work-1', title: 'Reliable Agents', authors: ['Alice'], abstract: null,
  doi: null, published_at: null, added_via: 'pdf', added_at: '2026-07-13T08:00:00Z',
  favorite: false, feedback: 'neutral', pinned: false, reading_progress: 0,
  analysis_state: 'not_analyzed', document_count: 2, representative_document: documentOne,
} satisfies LibraryWork

describe('useReaderWorkspace', () => {
  it('loads one canonical Work and selects only a document that belongs to it', async () => {
    const workId = ref('work-1')
    const documentId = ref('document-1')
    const api = {
      getWork: vi.fn(async () => work),
      listDocuments: vi.fn(async () => [documentOne, documentTwo]),
    }
    let state!: ReturnType<typeof useReaderWorkspace>
    const wrapper = mount(defineComponent({
      setup() {
        state = useReaderWorkspace(workId, documentId, api)
        return () => h('div')
      },
    }))
    await flushPromises()

    expect(state.loading.value).toBe(false)
    expect(state.work.value?.id).toBe('work-1')
    expect(state.selectedDocument.value?.id).toBe('document-1')
    expect(state.contentUrl.value).toBe('/api/v1/documents/document-1/content')

    documentId.value = 'document-2'
    await flushPromises()
    expect(state.selectedDocument.value?.id).toBe('document-2')
    expect(api.getWork).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('fails closed for an unrelated document and exposes a retry command', async () => {
    let includeDocument = false
    const api = {
      getWork: vi.fn(async () => work),
      listDocuments: vi.fn(async () => includeDocument ? [documentOne] : []),
    }
    let state!: ReturnType<typeof useReaderWorkspace>
    const wrapper = mount(defineComponent({
      setup() {
        state = useReaderWorkspace(ref('work-1'), ref('document-1'), api)
        return () => h('div')
      },
    }))
    await flushPromises()

    expect(state.errorMessage.value).toContain('暂时无法打开阅读工作台')
    expect(state.work.value).toBeUndefined()
    includeDocument = true
    await state.retry()
    expect(state.errorMessage.value).toBe('')
    expect(state.selectedDocument.value?.id).toBe('document-1')
    wrapper.unmount()
  })
})
