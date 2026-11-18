import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AnnotationPanel from './AnnotationPanel.vue'

const highlight = {
  id: 'annotation-highlight',
  document_version_id: 'document-1',
  document_content_sha256: 'a'.repeat(64),
  kind: 'highlight' as const,
  page_number: 2,
  page_width: 420,
  page_height: 595,
  quote_text: 'Exact quoted evidence',
  context_before: 'before',
  context_after: 'after',
  rects: [{ x0: 12, y0: 20, x1: 180, y1: 38 }],
  color: 'amber' as const,
  note: null,
  created_at: '2026-07-13T08:00:00Z',
  updated_at: '2026-07-13T08:00:00Z',
}

const bookmark = {
  ...highlight,
  id: 'annotation-bookmark',
  kind: 'bookmark' as const,
  page_number: 1,
  quote_text: null,
  context_before: null,
  context_after: null,
  rects: [],
  color: 'green' as const,
  note: 'Return to the main result',
}

const selection = {
  pageNumber: 3,
  pageCount: 5,
  pageWidth: 420,
  pageHeight: 595,
  quoteText: '  selected evidence\nkeeps spacing  ',
  contextBefore: 'context before',
  contextAfter: 'context after',
  rects: [{ x0: 20, y0: 30, x1: 190, y1: 48 }],
  verified: true,
}

const matchedMigrationCandidate = {
  id: 'migration-candidate-matched',
  batch_id: 'migration-preview-1',
  source_annotation_id: 'source-annotation-1',
  source_kind: 'highlight' as const,
  source_page_number: 1,
  source_quote_text: 'Exact quoted evidence',
  source_note: null,
  source_color: 'amber' as const,
  match_state: 'matched' as const,
  confidence: 'high' as const,
  score: 0.97,
  target_page_number: 3,
  target_page_width: 420,
  target_page_height: 595,
  target_quote_text: 'Exact quoted evidence',
  target_context_before: 'before',
  target_context_after: 'after',
  target_rects: [{ x0: 20, y0: 30, x1: 190, y1: 48 }],
  text_source: 'native' as const,
  alternatives: [{
    page_number: 3,
    page_width: 420,
    page_height: 595,
    quote_text: 'Exact quoted evidence',
    rects: [{ x0: 20, y0: 30, x1: 190, y1: 48 }],
    text_source: 'native' as const,
    score: 0.97,
  }],
  decision: null,
  target_annotation_id: null,
  created_at: '2026-07-13T09:00:00Z',
  decided_at: null,
}

const orphanedMigrationCandidate = {
  ...matchedMigrationCandidate,
  id: 'migration-candidate-orphaned',
  source_annotation_id: 'source-annotation-2',
  source_page_number: 2,
  source_quote_text: 'Passage deleted from the new version',
  match_state: 'orphaned' as const,
  confidence: 'none' as const,
  score: 0,
  target_page_number: null,
  target_page_width: null,
  target_page_height: null,
  target_quote_text: null,
  target_context_before: null,
  target_context_after: null,
  target_rects: [],
  text_source: null,
  alternatives: [],
}

const migrationPreview = {
  id: 'migration-preview-1',
  algorithm_version: 'annotation-migration-v1',
  source_document_version_id: 'document-old',
  source_document_content_sha256: 'b'.repeat(64),
  target_document_version_id: 'document-current',
  target_document_content_sha256: 'a'.repeat(64),
  target_extraction_sha256: 'c'.repeat(64),
  candidates: [matchedMigrationCandidate, orphanedMigrationCandidate],
  coverage: {
    total: 2,
    matched: 1,
    ambiguous: 0,
    orphaned: 1,
    pending: 2,
    accepted: 0,
    rejected: 0,
  },
  created_at: '2026-07-13T09:00:00Z',
}

describe('AnnotationPanel', () => {
  it('creates highlights, notes and page bookmarks from an exact PDF selection', async () => {
    const wrapper = mount(AnnotationPanel, {
      props: {
        annotations: [bookmark, highlight],
        selection,
        currentPage: 3,
        loading: false,
        saving: false,
      },
    })

    expect(wrapper.get('[data-test="selection-quote"]').text()).toContain(
      'selected evidence',
    )
    expect(wrapper.get('[data-test="create-highlight"]').attributes('aria-keyshortcuts')).toBe(
      'Shift+H',
    )
    await wrapper.get('[data-test="composer-note"]').setValue('My interpretation')
    await wrapper.get('[data-test="color-rose"]').trigger('click')
    await wrapper.get('[data-test="create-note"]').trigger('click')
    expect(wrapper.emitted('create')?.[0]).toEqual([{
      kind: 'note',
      color: 'rose',
      note: 'My interpretation',
    }])

    await wrapper.get('[data-test="create-highlight"]').trigger('click')
    expect(wrapper.emitted('create')?.[1]).toEqual([{
      kind: 'highlight',
      color: 'rose',
      note: 'My interpretation',
    }])
    await wrapper.get('[data-test="create-bookmark"]').trigger('click')
    expect(wrapper.emitted('create')?.[2]).toEqual([{
      kind: 'bookmark',
      color: 'rose',
      note: 'My interpretation',
    }])
    await wrapper.get('[data-test="clear-selection"]').trigger('click')
    expect(wrapper.emitted('clear-selection')).toHaveLength(1)
  })

  it('filters, navigates, edits and safely confirms deletion with focus return', async () => {
    const wrapper = mount(AnnotationPanel, {
      attachTo: document.body,
      props: {
        annotations: [bookmark, highlight],
        selection: null,
        currentPage: 1,
        loading: false,
        saving: false,
        statusMessage: '批注已保存',
      },
    })
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('批注已保存')
    await wrapper.get('[data-test="filter-bookmark"]').trigger('click')
    expect(wrapper.get('[data-test="filter-bookmark"]').attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('[data-test="annotation-annotation-highlight"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="annotation-annotation-bookmark"]').exists()).toBe(true)

    await wrapper.get('[data-test="navigate-annotation-bookmark"]').trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual([1])
    await wrapper.get('[data-test="edit-annotation-bookmark"]').trigger('click')
    await wrapper.get('[data-test="edit-note-annotation-bookmark"]').setValue('Revised bookmark')
    await wrapper.get('[data-test="edit-color-annotation-bookmark-blue"]').trigger('click')
    await wrapper.get('[data-test="save-annotation-bookmark"]').trigger('click')
    expect(wrapper.emitted('update')?.[0]).toEqual([
      bookmark,
      { color: 'blue', note: 'Revised bookmark' },
    ])

    await wrapper.get('[data-test="delete-annotation-bookmark"]').trigger('click')
    expect(wrapper.find('[data-test="confirm-delete-annotation-bookmark"]').exists()).toBe(true)
    await wrapper.get('[data-test="confirm-delete-annotation-bookmark"]').trigger('click')
    expect(wrapper.emitted('delete')?.[0]).toEqual([bookmark])
    expect(document.activeElement).toBe(wrapper.get('[data-test="annotation-heading"]').element)
    wrapper.unmount()
  })

  it('exposes loading, failure and empty states without enabling selection actions', async () => {
    const loading = mount(AnnotationPanel, {
      props: {
        annotations: [],
        selection: null,
        currentPage: 1,
        loading: true,
        saving: false,
      },
    })
    expect(loading.get('[role="status"]').text()).toContain('正在读取批注')
    loading.unmount()

    const failed = mount(AnnotationPanel, {
      props: {
        annotations: [],
        selection: null,
        currentPage: 1,
        loading: false,
        saving: false,
        errorMessage: '无法读取批注',
      },
    })
    expect(failed.get('[role="alert"]').text()).toContain('无法读取批注')
    expect(failed.get('[data-test="create-highlight"]').attributes('disabled')).toBeDefined()
    expect(failed.get('[data-test="create-note"]').attributes('disabled')).toBeDefined()
    expect(failed.text()).toContain('还没有批注或书签')
    await failed.get('[data-test="retry-annotations"]').trigger('click')
    expect(failed.emitted('retry')).toHaveLength(1)
  })

  it('reviews migration candidates one at a time without changing source annotations', async () => {
    const wrapper = mount(AnnotationPanel, {
      props: {
        annotations: [highlight],
        selection: null,
        currentPage: 1,
        loading: false,
        saving: false,
        versionOptions: [
          { id: 'document-current', label: '当前版本', available: true },
          { id: 'document-old', label: '旧版本', available: true },
        ],
        currentDocumentId: 'document-current',
        migrationPreview,
        migrationLoading: false,
        migrationSaving: false,
      },
    })

    expect(wrapper.get('[data-test="migration-source"]').element).toHaveProperty(
      'value',
      'document-old',
    )
    await wrapper.get('[data-test="start-migration-preview"]').trigger('click')
    expect(wrapper.emitted('preview-migration')?.[0]).toEqual(['document-old'])
    expect(wrapper.get('[data-test="migration-coverage"]').text()).toContain('1 可迁移')
    expect(wrapper.get('[data-test="migration-candidate-position"]').text()).toContain('1 / 2')
    expect(wrapper.get('[data-test="migration-source-quote"]').text()).toContain(
      'Exact quoted evidence',
    )
    expect(wrapper.text()).toContain('原批注始终保留')

    await wrapper.get('[data-test="preview-migration-candidate"]').trigger('click')
    expect(wrapper.emitted('preview-candidate')?.[0]).toEqual([matchedMigrationCandidate])
    await wrapper.get('[data-test="accept-migration-candidate"]').trigger('click')
    expect(wrapper.emitted('decide-migration')?.[0]).toEqual([
      matchedMigrationCandidate,
      'accept',
    ])

    await wrapper.get('[data-test="next-migration-candidate"]').trigger('click')
    expect(wrapper.get('[data-test="migration-candidate-position"]').text()).toContain('2 / 2')
    expect(wrapper.text()).toContain('目标版本没有找到可迁移位置')
    expect(
      wrapper.get('[data-test="accept-migration-candidate"]').attributes('disabled'),
    ).toBeDefined()
    await wrapper.get('[data-test="reject-migration-candidate"]').trigger('click')
    expect(wrapper.emitted('decide-migration')?.[1]).toEqual([
      orphanedMigrationCandidate,
      'reject',
    ])
  })
})
