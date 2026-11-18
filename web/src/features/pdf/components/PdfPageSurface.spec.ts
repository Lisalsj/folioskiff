import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PdfPageSurface from './PdfPageSurface.vue'

const annotation = {
  id: 'annotation-1',
  document_version_id: 'document-1',
  document_content_sha256: 'a'.repeat(64),
  kind: 'highlight' as const,
  page_number: 2,
  page_width: 420,
  page_height: 595,
  quote_text: 'selected evidence',
  context_before: 'before',
  context_after: 'after',
  rects: [{ x0: 20, y0: 20, x1: 120, y1: 40 }],
  color: 'amber' as const,
  note: null,
  created_at: '2026-07-13T08:00:00Z',
  updated_at: '2026-07-13T08:00:00Z',
}

describe('PdfPageSurface', () => {
  it('owns the render surface, canonical overlays and search-result intent', async () => {
    const wrapper = mount(PdfPageSurface, {
      props: {
        title: 'Reliable paper reader',
        hasDocument: true,
        loading: false,
        errorMessage: '',
        rendering: false,
        page: 2,
        pageCount: 3,
        pageSize: { width: 588, height: 833 },
        canonicalPageSize: { width: 420, height: 595 },
        scale: 1.4,
        rotation: 0,
        annotations: [annotation],
        migrationPreview: null,
        query: 'agent',
        searching: false,
        matches: [{ pageNumber: 3, occurrences: 1, snippet: 'Agent evidence' }],
      },
    })

    const surface = (wrapper.vm as unknown as {
      getSurface: () => { canvas: HTMLCanvasElement, textLayer: HTMLDivElement } | null
    }).getSurface()
    expect(surface?.canvas).toBeInstanceOf(HTMLCanvasElement)
    expect(surface?.textLayer).toBeInstanceOf(HTMLDivElement)
    const overlay = wrapper.get('[data-test="annotation-overlay-annotation-1"]')
    expect(overlay.attributes('style')).toContain('left: 28px')
    await overlay.trigger('click')
    expect(wrapper.emitted('annotation-select')?.[0]).toEqual([annotation])
    await wrapper.get('[data-test="search-result-3"]').trigger('click')
    expect(wrapper.emitted('select-match')?.[0]).toEqual([
      { pageNumber: 3, occurrences: 1, snippet: 'Agent evidence' },
    ])
  })
})
