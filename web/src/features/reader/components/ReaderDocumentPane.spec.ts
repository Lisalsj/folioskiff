import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import type { components } from '@/api/schema'
import type { PdfTextSelection } from '@/lib/annotation-geometry'
import ReaderDocumentPane from './ReaderDocumentPane.vue'

type LibraryDocument = components['schemas']['LibraryDocumentResponse']

const documentVersion = {
  id: 'document-1', work_id: 'work-1', content_sha256: 'a'.repeat(64),
  access_status: 'user_provided', media_type: 'application/pdf', completeness: 100,
  extraction_status: 'complete', available: true, byte_size: 2048,
  original_filename: 'reader.pdf', acquisition_kind: 'manual_import',
  last_accessed_at: null, created_at: '2026-07-13T08:00:00Z', is_representative: true,
} satisfies LibraryDocument
const selection: PdfTextSelection = {
  pageNumber: 2, pageCount: 8, pageWidth: 420, pageHeight: 595,
  quoteText: 'verified evidence', contextBefore: 'before', contextAfter: 'after',
  rects: [{ x0: 10, y0: 20, x1: 180, y1: 38 }], verified: true,
}
const goToPage = vi.fn(async () => undefined)
const PdfReaderStub = defineComponent({
  name: 'PdfReader',
  emits: ['selection-change'],
  setup(_props, { emit, expose }) {
    expose({ goToPage, requestBookmark: vi.fn(), clearSelection: vi.fn() })
    return () => h('button', {
      'data-test': 'emit-selection',
      onClick: () => emit('selection-change', selection),
    }, 'select')
  },
})

describe('ReaderDocumentPane', () => {
  it('owns document rendering, proxies controls and emits selection actions', async () => {
    const controller = ref<InstanceType<typeof ReaderDocumentPane>>()
    const wrapper = mount(ReaderDocumentPane, {
      props: {
        document: documentVersion,
        documentUrl: '/api/v1/documents/document-1/content',
        title: 'Reliable Agents',
        position: null,
        annotations: [],
        migrationPreview: null,
        selection,
        mobileActive: true,
      },
      global: { stubs: { PdfReader: PdfReaderStub } },
      attrs: { ref: controller },
    })

    expect(wrapper.get('[data-test="reader-content-url"]').attributes('data-url')).toBe(
      '/api/v1/documents/document-1/content',
    )
    await wrapper.get('[data-test="emit-selection"]').trigger('click')
    expect(wrapper.emitted('selection-change')).toEqual([[selection]])
    await wrapper.get('[data-test="context-explain"]').trigger('click')
    expect(wrapper.emitted('explain')).toEqual([[selection]])
    await (wrapper.vm as unknown as { goToPage(value: number): Promise<void> }).goToPage(4)
    expect(goToPage).toHaveBeenCalledWith(4)
  })
})
