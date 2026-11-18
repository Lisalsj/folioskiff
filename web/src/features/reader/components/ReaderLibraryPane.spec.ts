import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { components } from '@/api/schema'
import ReaderLibraryPane from './ReaderLibraryPane.vue'

type LibraryDocument = components['schemas']['LibraryDocumentResponse']
type LibraryWork = components['schemas']['LibraryWorkResponse']

const first = {
  id: 'document-1', work_id: 'work-1', content_sha256: 'a'.repeat(64),
  access_status: 'user_provided', media_type: 'application/pdf', completeness: 100,
  extraction_status: 'complete', available: true, byte_size: 2048,
  original_filename: 'reader-v1.pdf', acquisition_kind: 'manual_import',
  last_accessed_at: null, created_at: '2026-07-13T08:00:00Z', is_representative: true,
} satisfies LibraryDocument
const second = {
  ...first, id: 'document-2', content_sha256: 'b'.repeat(64),
  original_filename: 'reader-v0.pdf', is_representative: false,
} satisfies LibraryDocument
const work = {
  id: 'work-1', title: 'Reliable Agents', authors: ['Alice'], abstract: 'Evidence first.',
  doi: '10.1000/reader', published_at: null, added_via: 'pdf',
  added_at: '2026-07-13T08:00:00Z', favorite: false, feedback: 'neutral', pinned: false,
  reading_progress: 0, analysis_state: 'not_analyzed', document_count: 2,
  representative_document: first,
} satisfies LibraryWork

describe('ReaderLibraryPane', () => {
  it('renders canonical versions and emits selection/close intents without fetching', async () => {
    const wrapper = mount(ReaderLibraryPane, {
      props: {
        work, documents: [first, second], selectedDocumentId: 'document-1',
        open: false, compact: true,
      },
    })

    expect(wrapper.get('[aria-current="page"]').text()).toContain('reader-v1.pdf')
    expect(wrapper.get('#reader-library-column').attributes('aria-hidden')).toBe('true')
    expect(wrapper.get('#reader-library-column').attributes('inert')).toBeDefined()
    await wrapper.get('a[href="/library/work-1/read/document-2"]').trigger('click')
    expect(wrapper.emitted('select-document')).toEqual([['document-2']])
    await wrapper.get('[aria-label="关闭论文与版本列表"]').trigger('click')
    expect(wrapper.emitted('close')).toEqual([[]])
  })
})
