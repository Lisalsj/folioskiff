import { defineComponent, h, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import type { components } from '@/api/schema'
import type { PdfTextSelection } from '@/lib/annotation-geometry'
import { useAnnotations } from './useAnnotations'

type Annotation = components['schemas']['AnnotationResponse']

const selection: PdfTextSelection = {
  pageNumber: 2, pageCount: 8, pageWidth: 420, pageHeight: 595,
  quoteText: 'selected evidence', contextBefore: 'before', contextAfter: 'after',
  rects: [{ x0: 10, y0: 20, x1: 180, y1: 38 }], verified: true,
}

function annotation(id: string, page = 2): Annotation {
  return {
    id, document_version_id: 'document-1', document_content_sha256: 'a'.repeat(64),
    kind: 'highlight', page_number: page, page_width: 420, page_height: 595,
    quote_text: 'selected evidence', context_before: 'before', context_after: 'after',
    rects: [{ x0: 10, y0: 20, x1: 180, y1: 38 }], color: 'amber', note: null,
    created_at: '2026-07-13T08:00:00Z', updated_at: '2026-07-13T08:00:00Z',
  }
}

describe('useAnnotations', () => {
  it('owns loading, highlight and bookmark persistence without a PDF component dependency', async () => {
    const created: unknown[] = []
    const api = {
      list: vi.fn(async () => [annotation('existing', 1)]),
      create: vi.fn(async (_documentId: string, payload: unknown) => {
        created.push(payload)
        return annotation(`created-${created.length}`, created.length + 1)
      }),
      update: vi.fn(), remove: vi.fn(), previewMigration: vi.fn(),
      decideMigration: vi.fn(), getMigration: vi.fn(),
    }
    let state!: ReturnType<typeof useAnnotations>
    const wrapper = mount(defineComponent({
      setup() {
        state = useAnnotations(ref('document-1'), ref('a'.repeat(64)), {
          pageCount: ref(8), availableDocumentIds: ref(['document-1']), api,
        })
        return () => h('div')
      },
    }))
    await flushPromises()
    expect(state.annotations.value.map((item) => item.id)).toEqual(['existing'])

    state.setSelection(selection)
    expect(state.beginCreate({ kind: 'highlight', color: 'amber', note: null })).toBe(false)
    await flushPromises()
    expect(created[0]).toMatchObject({
      document_content_sha256: 'a'.repeat(64), kind: 'highlight', page_number: 2,
      quote_text: 'selected evidence',
    })
    expect(state.selection.value).toBeNull()

    expect(state.beginCreate({ kind: 'bookmark', color: 'green', note: 'Return here' })).toBe(true)
    await state.commitBookmark({ pageNumber: 4, pageCount: 8, pageWidth: 420, pageHeight: 595 })
    expect(created[1]).toMatchObject({
      kind: 'bookmark', color: 'green', page_number: 4, quote_text: null, rects: [],
    })
    wrapper.unmount()
  })

  it('keeps migration preview and decisions immutable until the API confirms them', async () => {
    const candidate = {
      id: 'candidate-1', batch_id: 'preview-1', source_annotation_id: 'old-1',
      source_kind: 'highlight' as const, source_page_number: 1, source_quote_text: 'evidence',
      source_note: null, source_color: 'amber' as const, match_state: 'matched' as const,
      confidence: 'high' as const, score: 0.98, target_page_number: 3,
      target_page_width: 420, target_page_height: 595, target_quote_text: 'evidence',
      target_context_before: null, target_context_after: null, target_rects: [],
      text_source: 'native' as const, alternatives: [], decision: null,
      target_annotation_id: null, created_at: '2026-07-13T09:00:00Z', decided_at: null,
    }
    const preview = {
      id: 'preview-1', algorithm_version: 'annotation-migration-v1',
      source_document_version_id: 'document-2', source_document_content_sha256: 'b'.repeat(64),
      target_document_version_id: 'document-1', target_document_content_sha256: 'a'.repeat(64),
      target_extraction_sha256: 'c'.repeat(64), candidates: [candidate],
      coverage: { total: 1, matched: 1, ambiguous: 0, orphaned: 0, pending: 1, accepted: 0, rejected: 0 },
      created_at: '2026-07-13T09:00:00Z',
    }
    const accepted = { ...candidate, decision: 'accept' as const, target_annotation_id: 'new-1', decided_at: '2026-07-13T09:05:00Z' }
    const refreshed = { ...preview, candidates: [accepted], coverage: { ...preview.coverage, pending: 0, accepted: 1 } }
    const api = {
      list: vi.fn(async () => [] as Annotation[]), create: vi.fn(), update: vi.fn(), remove: vi.fn(),
      previewMigration: vi.fn(async () => preview),
      decideMigration: vi.fn(async () => accepted),
      getMigration: vi.fn(async () => refreshed),
    }
    let state!: ReturnType<typeof useAnnotations>
    const wrapper = mount(defineComponent({
      setup() {
        state = useAnnotations(ref('document-1'), ref('a'.repeat(64)), {
          pageCount: ref(8),
          availableDocumentIds: ref(['document-1', 'document-2']),
          api,
        })
        return () => h('div')
      },
    }))
    await flushPromises()

    await state.previewMigration('document-2')
    expect(state.migrationPreview.value?.id).toBe('preview-1')
    expect(state.previewCandidate(candidate)).toBe(3)
    expect(state.migrationPreviewMark.value?.id).toBe('candidate-1')
    await state.decideMigration(candidate, 'accept')
    expect(api.decideMigration).toHaveBeenCalledWith('candidate-1', 'accept')
    expect(state.migrationPreview.value?.coverage.accepted).toBe(1)
    expect(state.migrationPreviewMark.value).toBeNull()
    wrapper.unmount()
  })

  it('keeps annotation read failures non-fatal and exposes retry', async () => {
    let fail = true
    const api = {
      list: vi.fn(async () => {
        if (fail) throw new Error('D:\\private\\annotations.db')
        return [annotation('recovered')]
      }),
      create: vi.fn(), update: vi.fn(), remove: vi.fn(), previewMigration: vi.fn(),
      decideMigration: vi.fn(), getMigration: vi.fn(),
    }
    let state!: ReturnType<typeof useAnnotations>
    const wrapper = mount(defineComponent({
      setup() {
        state = useAnnotations(ref('document-1'), ref('a'.repeat(64)), {
          pageCount: ref(8), availableDocumentIds: ref(['document-1']), api,
        })
        return () => h('div')
      },
    }))
    await flushPromises()

    expect(state.errorMessage.value).toContain('暂时无法读取批注')
    expect(state.errorMessage.value).not.toContain('private')
    fail = false
    await state.retry()
    expect(state.errorMessage.value).toBe('')
    expect(state.annotations.value.map((item) => item.id)).toEqual(['recovered'])
    wrapper.unmount()
  })
})
