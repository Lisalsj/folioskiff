import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PdfReader from './PdfReader.vue'
import {
  PdfSafetyLimitError,
  type PdfDocumentHandle,
  type PdfSearchResult,
  type PdfSession,
} from '@/lib/pdf-session'

function readerFixture(overrides: Partial<PdfDocumentHandle> = {}) {
  const handle: PdfDocumentHandle = {
    pageCount: 3,
    renderPage: vi.fn(async () => ({ width: 420, height: 595 })),
    getPageSize: vi.fn(async () => ({ width: 420, height: 595 })),
    search: vi.fn(async () => [
      { pageNumber: 2, occurrences: 1, snippet: 'Agent evidence on page two' },
    ]),
    ...overrides,
  }
  const session: PdfSession = {
    open: vi.fn(async () => handle),
    close: vi.fn(async () => undefined),
  }
  return { handle, session, sessionFactory: () => session }
}

describe('PdfReader', () => {
  afterEach(() => vi.restoreAllMocks())

  it('renders the first page and supports page, search, zoom, rotation and fit controls', async () => {
    const fixture = readerFixture()
    const wrapper = mount(PdfReader, {
      props: {
        documentUrl: '/api/v1/documents/document-1/content',
        title: 'Reliable paper reader',
        sessionFactory: fixture.sessionFactory,
      },
    })
    await flushPromises()

    expect(fixture.session.open).toHaveBeenCalledWith(
      '/api/v1/documents/document-1/content',
    )
    expect(fixture.handle.renderPage).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ scale: 1.1, rotation: 0 }),
    )
    expect(wrapper.get('canvas').attributes('aria-label')).toContain('第 1 页')
    expect(wrapper.get('[data-test="page-status"]').text()).toContain('1 / 3')

    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="page-status"]').text()).toContain('2 / 3')

    await wrapper.get('[data-test="page-input"]').setValue('3')
    await wrapper.get('[data-test="page-form"]').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[data-test="page-status"]').text()).toContain('3 / 3')

    await wrapper.get('[data-test="search-input"]').setValue('agent')
    await wrapper.get('[data-test="search-form"]').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('Agent evidence on page two')
    await wrapper.get('[data-test="search-result-2"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="page-status"]').text()).toContain('2 / 3')

    await wrapper.get('button[aria-label="放大"]').trigger('click')
    await wrapper.get('button[aria-label="顺时针旋转"]').trigger('click')
    await flushPromises()
    expect(fixture.handle.renderPage).toHaveBeenLastCalledWith(
      2,
      expect.objectContaining({ scale: 1.25, rotation: 90 }),
    )

    const viewport = wrapper.get('[data-test="pdf-viewport"]').element
    Object.defineProperty(viewport, 'clientWidth', { configurable: true, value: 900 })
    await wrapper.get('button[aria-label="适合宽度"]').trigger('click')
    await flushPromises()
    expect(fixture.handle.getPageSize).toHaveBeenCalledWith(2, 90)
    expect(fixture.handle.renderPage).toHaveBeenLastCalledWith(
      2,
      expect.objectContaining({ scale: 2.03, rotation: 90 }),
    )

    wrapper.unmount()
    expect(fixture.session.close).toHaveBeenCalledTimes(1)
  })

  it('fits the initial page to the available width only when explicitly requested', async () => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(360)
    const fixture = readerFixture()
    const wrapper = mount(PdfReader, {
      props: {
        documentUrl: '/narrow-reader.pdf',
        fitInitialWidth: true,
        sessionFactory: fixture.sessionFactory,
      },
    })
    await flushPromises()

    expect(fixture.handle.getPageSize).toHaveBeenCalledWith(1, 0)
    expect(fixture.handle.renderPage).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ scale: 0.74, rotation: 0 }),
    )
    wrapper.unmount()
  })

  it('closes the previous PDF session when the document changes and closes the replacement on unmount', async () => {
    const fixture = readerFixture()
    const wrapper = mount(PdfReader, {
      props: { documentUrl: '/first.pdf', sessionFactory: fixture.sessionFactory },
    })
    await flushPromises()

    await wrapper.setProps({ documentUrl: '/second.pdf' })
    await flushPromises()

    expect(fixture.session.open).toHaveBeenNthCalledWith(1, '/first.pdf')
    expect(fixture.session.open).toHaveBeenNthCalledWith(2, '/second.pdf')
    expect(fixture.session.close).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    expect(fixture.session.close).toHaveBeenCalledTimes(2)
  })

  it('does not let an older repeated-query search replace the newest result', async () => {
    const pending: Array<(matches: PdfSearchResult[]) => void> = []
    const fixture = readerFixture({
      search: vi.fn(() => new Promise<PdfSearchResult[]>((resolve) => pending.push(resolve))),
    })
    const wrapper = mount(PdfReader, {
      props: { documentUrl: '/paper.pdf', sessionFactory: fixture.sessionFactory },
    })
    await flushPromises()

    const search = async (query: string) => {
      await wrapper.get('[data-test="search-input"]').setValue(query)
      await wrapper.get('[data-test="search-form"]').trigger('submit')
    }
    await search('agent')
    await search('paper')
    await search('agent')

    pending[2]?.([{ pageNumber: 3, occurrences: 1, snippet: 'newest agent result' }])
    await flushPromises()
    pending[0]?.([{ pageNumber: 1, occurrences: 1, snippet: 'stale agent result' }])
    pending[1]?.([{ pageNumber: 2, occurrences: 1, snippet: 'stale paper result' }])
    await flushPromises()

    expect(wrapper.text()).toContain('newest agent result')
    expect(wrapper.text()).not.toContain('stale agent result')
    expect(wrapper.text()).not.toContain('stale paper result')
    wrapper.unmount()
  })

  it('provides keyboard navigation and focus labels without hijacking inputs', async () => {
    const fixture = readerFixture()
    const wrapper = mount(PdfReader, {
      attachTo: document.body,
      props: { documentUrl: '/paper.pdf', sessionFactory: fixture.sessionFactory },
    })
    await flushPromises()
    const reader = wrapper.get('[data-test="pdf-reader"]')
    const viewport = wrapper.get('[data-test="pdf-viewport"]')

    expect(viewport.attributes('tabindex')).toBe('0')
    expect(viewport.attributes('aria-label')).toBe('PDF 页面滚动区域')

    await reader.trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(wrapper.get('[data-test="page-status"]').text()).toContain('2 / 3')
    await reader.trigger('keydown', { key: '+', shiftKey: true })
    await reader.trigger('keydown', { key: 'r' })
    await reader.trigger('keydown', { key: 'f', ctrlKey: true })
    await flushPromises()

    expect(document.activeElement).toBe(wrapper.get('[data-test="search-input"]').element)
    expect(wrapper.get('button[aria-label="上一页"]').attributes('aria-keyshortcuts')).toBe(
      'ArrowLeft PageUp',
    )
    expect(fixture.handle.renderPage).toHaveBeenLastCalledWith(
      2,
      expect.objectContaining({ scale: 1.25, rotation: 90 }),
    )

    await wrapper.get('[data-test="page-input"]').trigger('keydown', { key: 'ArrowRight' })
    expect(wrapper.get('[data-test="page-status"]').text()).toContain('2 / 3')
    wrapper.unmount()
  })

  it('renders loading, empty and recoverable error states', async () => {
    let resolveOpen: ((handle: PdfDocumentHandle) => void) | undefined
    const fixture = readerFixture()
    const pendingSession: PdfSession = {
      open: vi.fn(() => new Promise<PdfDocumentHandle>((resolve) => { resolveOpen = resolve })),
      close: vi.fn(async () => undefined),
    }
    const loading = mount(PdfReader, {
      props: { documentUrl: '/pending.pdf', sessionFactory: () => pendingSession },
    })
    await loading.vm.$nextTick()
    expect(loading.get('[role="status"]').text()).toContain('正在打开 PDF')
    resolveOpen?.(fixture.handle)
    await flushPromises()
    expect(loading.find('[role="status"]').exists()).toBe(false)
    loading.unmount()

    const empty = mount(PdfReader, {
      props: { documentUrl: '', sessionFactory: fixture.sessionFactory },
    })
    expect(empty.get('[data-test="pdf-empty"]').text()).toContain('尚未选择')
    expect(fixture.session.open).not.toHaveBeenCalled()
    empty.unmount()

    let attempts = 0
    const recovering = readerFixture()
    recovering.session.open = vi.fn(async () => {
      attempts += 1
      if (attempts === 1) throw new Error('local path must not be shown')
      return recovering.handle
    })
    const failed = mount(PdfReader, {
      props: { documentUrl: '/recover.pdf', sessionFactory: recovering.sessionFactory },
    })
    await flushPromises()
    expect(failed.get('[role="alert"]').text()).toContain('无法打开这份 PDF')
    expect(failed.text()).not.toContain('local path')
    await failed.get('[data-test="retry-pdf"]').trigger('click')
    await flushPromises()
    expect(recovering.session.open).toHaveBeenCalledTimes(2)
    expect(failed.find('[role="alert"]').exists()).toBe(false)
    failed.unmount()

    const limitedSession: PdfSession = {
      open: vi.fn(async () => {
        throw new PdfSafetyLimitError('too_many_pages')
      }),
      close: vi.fn(async () => undefined),
    }
    const limited = mount(PdfReader, {
      props: { documentUrl: '/hostile.pdf', sessionFactory: () => limitedSession },
    })
    await flushPromises()
    expect(limited.get('[role="alert"]').text()).toContain('安全处理上限')
    expect(limited.text()).not.toContain('/hostile.pdf')
    limited.unmount()
  })

  it('replays a citation page requested while the PDF is still opening', async () => {
    let resolveOpen: ((handle: PdfDocumentHandle) => void) | undefined
    const fixture = readerFixture()
    const pendingSession: PdfSession = {
      open: vi.fn(() => new Promise<PdfDocumentHandle>((resolve) => { resolveOpen = resolve })),
      close: vi.fn(async () => undefined),
    }
    const wrapper = mount(PdfReader, {
      props: { documentUrl: '/pending-citation.pdf', sessionFactory: () => pendingSession },
    })
    await wrapper.vm.$nextTick()

    await (wrapper.vm as unknown as { goToPage: (page: number) => Promise<void> }).goToPage(3)
    resolveOpen?.(fixture.handle)
    await flushPromises()

    expect(wrapper.get('[data-test="page-status"]').text()).toContain('3 / 3')
    expect(fixture.handle.renderPage).toHaveBeenCalledWith(
      3,
      expect.objectContaining({ scale: 1.1, rotation: 0 }),
    )
    wrapper.unmount()
  })

  it('restores view state and anchors exact text selections and overlays in canonical page coordinates', async () => {
    const fixture = readerFixture({
      renderPage: vi.fn(async (_page, target) => ({
        width: (target.rotation === 90 ? 595 : 420) * target.scale,
        height: (target.rotation === 90 ? 420 : 595) * target.scale,
      })),
      getPageSize: vi.fn(async (_page, rotation) => rotation === 90
        ? { width: 595, height: 420 }
        : { width: 420, height: 595 }),
    })
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
    const wrapper = mount(PdfReader, {
      props: {
        documentUrl: '/paper.pdf',
        sessionFactory: fixture.sessionFactory,
        initialPage: 2,
        initialScale: 1.4,
        initialRotation: 0,
        annotations: [annotation],
        migrationPreview: {
          id: 'migration-candidate-1',
          pageNumber: 2,
          pageWidth: 420,
          pageHeight: 595,
          rects: [{ x0: 40, y0: 50, x1: 160, y1: 70 }],
        },
      },
    })
    await flushPromises()
    expect(fixture.handle.renderPage).toHaveBeenCalledWith(
      2,
      expect.objectContaining({ scale: 1.4, rotation: 0 }),
    )
    const overlay = wrapper.get('[data-test="annotation-overlay-annotation-1"]')
    expect(overlay.attributes('style')).toContain('left: 28px')
    expect(overlay.attributes('style')).toContain('top: 28px')
    expect(overlay.attributes('style')).toContain('width: 140px')
    await overlay.trigger('click')
    expect(wrapper.emitted('annotation-select')?.[0]).toEqual([annotation])
    const migrationOverlay = wrapper.get(
      '[data-test="migration-preview-overlay-migration-candidate-1"]',
    )
    expect(migrationOverlay.attributes('style')).toContain('left: 56px')
    expect(migrationOverlay.attributes('style')).toContain('top: 70px')
    expect(migrationOverlay.attributes('style')).toContain('width: 168px')
    expect(migrationOverlay.attributes('aria-hidden')).toBe('true')

    const shell = wrapper.get('[data-test="pdf-page-shell"]').element
    const layer = wrapper.get('[data-test="pdf-text-layer"]').element
    layer.textContent = 'context before selected evidence context after'
    Object.defineProperty(shell, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 100, top: 200, right: 688, bottom: 1033, width: 588, height: 833 }),
    })
    const range = {
      commonAncestorContainer: layer,
      getClientRects: () => [{
        left: 128,
        top: 228,
        right: 268,
        bottom: 256,
        width: 140,
        height: 28,
      }],
    }
    const selection = {
      rangeCount: 1,
      isCollapsed: false,
      toString: () => 'selected evidence',
      getRangeAt: () => range,
      removeAllRanges: vi.fn(),
    }
    vi.spyOn(window, 'getSelection').mockReturnValue(selection as unknown as Selection)

    await wrapper.get('[data-test="pdf-text-layer"]').trigger('mouseup')
    expect(wrapper.emitted('selection-change')?.at(-1)).toEqual([{
      pageNumber: 2,
      pageCount: 3,
      pageWidth: 420,
      pageHeight: 595,
      quoteText: 'selected evidence',
      contextBefore: 'context before ',
      contextAfter: ' context after',
      rects: [{ x0: 20, y0: 20, x1: 120, y1: 40 }],
      verified: true,
    }])
    await wrapper.get('[data-test="pdf-reader"]').trigger('keydown', {
      key: 'H',
      shiftKey: true,
    })
    expect(wrapper.emitted('quick-highlight')?.at(-1)).toEqual([
      wrapper.emitted('selection-change')?.at(-1)?.[0],
    ])
    await wrapper.get('[data-test="bookmark-page"]').trigger('click')
    expect(wrapper.emitted('bookmark-request')?.at(-1)).toEqual([{
      pageNumber: 2,
      pageCount: 3,
      pageWidth: 420,
      pageHeight: 595,
    }])
    ;(wrapper.vm as unknown as { requestBookmark: () => void }).requestBookmark()
    expect(wrapper.emitted('bookmark-request')).toHaveLength(2)
    wrapper.unmount()
  })
})
