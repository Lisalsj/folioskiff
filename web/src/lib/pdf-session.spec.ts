import { describe, expect, it, vi } from 'vitest'

import {
  createPdfSession,
  PdfRenderCancelledError,
  PdfSafetyLimitError,
  type PdfSessionDependencies,
} from './pdf-session'

function fixtureDependencies() {
  const renderCancel = vi.fn()
  const textCancel = vi.fn()
  const loadingDestroy = vi.fn(async () => undefined)
  const createdWorkers: Array<{ id: number; destroy: ReturnType<typeof vi.fn> }> = []
  const createWorker = vi.fn(() => {
    const worker = { id: createdWorkers.length + 1, destroy: vi.fn() }
    createdWorkers.push(worker)
    return worker
  })
  const setLayerDimensions = vi.fn()
  const pageTexts = ['Agent methods improve agent reading.', 'Cancer evidence synthesis.']
  const pages = pageTexts.map((text) => ({
    getViewport: vi.fn(({ scale, rotation }: { scale: number; rotation: number }) => ({
      width: (rotation % 180 === 0 ? 400 : 600) * scale,
      height: (rotation % 180 === 0 ? 600 : 400) * scale,
    })),
    getTextContent: vi.fn(async () => ({
      items: text.split(' ').map((word) => ({ str: word })),
      styles: {},
      lang: null,
    })),
    render: vi.fn(() => ({
      promise: Promise.resolve(),
      cancel: renderCancel,
    })),
    cleanup: vi.fn(),
  }))
  const document = {
    numPages: pages.length,
    getPage: vi.fn(async (number: number) => pages[number - 1]),
  }
  const loadDocument = vi.fn((_options: Parameters<PdfSessionDependencies['loadDocument']>[0]) => ({
    promise: Promise.resolve(document),
    destroy: loadingDestroy,
  }))
  const createTextLayer = vi.fn(() => ({
    render: vi.fn(async () => undefined),
    cancel: textCancel,
  }))
  const dependencies: PdfSessionDependencies = {
    createWorker,
    loadDocument,
    createTextLayer,
    setLayerDimensions,
    pixelRatio: () => 2,
  }
  return {
    dependencies,
    document,
    pages,
    loadDocument,
    createWorker,
    createdWorkers,
    loadingDestroy,
    createTextLayer,
    setLayerDimensions,
    renderCancel,
    textCancel,
  }
}

describe('PDF.js session adapter', () => {
  it('loads authenticated ranges, renders one page and searches cached page text', async () => {
    const fixture = fixtureDependencies()
    const session = createPdfSession(fixture.dependencies)
    const handle = await session.open('/api/v1/documents/document-1/content')
    const canvas = document.createElement('canvas')
    const textLayer = document.createElement('div')

    await handle.renderPage(1, {
      canvas,
      textLayer,
      scale: 1,
      rotation: 0,
    })
    const matches = await handle.search('agent')
    const size = await handle.getPageSize(2, 90)
    await session.close()

    expect(fixture.loadDocument).toHaveBeenCalledWith({
      url: '/api/v1/documents/document-1/content',
      withCredentials: true,
      maxImageSize: 25_000_000,
      isEvalSupported: false,
      enableScripting: false,
      enableXfa: false,
      stopAtErrors: true,
      worker: fixture.createdWorkers[0],
    })
    expect(handle.pageCount).toBe(2)
    expect(canvas.width).toBe(800)
    expect(canvas.height).toBe(1200)
    expect(canvas.style.width).toBe('400px')
    expect(fixture.pages[0].render).toHaveBeenCalledWith(
      expect.objectContaining({
        canvas,
        transform: [2, 0, 0, 2, 0, 0],
        background: '#ffffff',
      }),
    )
    expect(fixture.setLayerDimensions).toHaveBeenCalledWith(
      textLayer,
      expect.objectContaining({ width: 400, height: 600 }),
    )
    expect(matches).toEqual([
      expect.objectContaining({ pageNumber: 1, occurrences: 2 }),
    ])
    expect(matches[0]?.snippet).toContain('Agent methods')
    expect(size).toEqual({ width: 600, height: 400 })
    expect(fixture.pages[0].getTextContent).toHaveBeenCalledTimes(1)
    expect(fixture.loadingDestroy).toHaveBeenCalledTimes(1)
  })

  it('gives consecutive loading tasks isolated workers so closing one cannot poison the next', async () => {
    const fixture = fixtureDependencies()
    const session = createPdfSession(fixture.dependencies)

    await session.open('/first.pdf')
    await session.open('/second.pdf')

    expect(fixture.createWorker).toHaveBeenCalledTimes(2)
    expect(fixture.createdWorkers).toHaveLength(2)
    expect(fixture.createdWorkers[0]).not.toBe(fixture.createdWorkers[1])
    expect(fixture.loadDocument.mock.calls[0]![0].worker).toBe(fixture.createdWorkers[0])
    expect(fixture.loadDocument.mock.calls[1]![0].worker).toBe(fixture.createdWorkers[1])
    expect(fixture.loadingDestroy).toHaveBeenCalledTimes(1)

    await session.close()
    expect(fixture.loadingDestroy).toHaveBeenCalledTimes(2)
  })

  it('cancels an in-flight loading task before opening the replacement document', async () => {
    const fixture = fixtureDependencies()
    let rejectFirst: ((reason: unknown) => void) | undefined
    const firstPromise = new Promise<typeof fixture.document>((_, reject) => {
      rejectFirst = reject
    })
    const firstDestroy = vi.fn(async () => {
      rejectFirst?.(new Error('first loading task destroyed'))
      return undefined
    })
    fixture.loadDocument
      .mockReturnValueOnce({ promise: firstPromise, destroy: firstDestroy })
      .mockReturnValueOnce({
        promise: Promise.resolve(fixture.document),
        destroy: vi.fn(async () => undefined),
      })
    const session = createPdfSession(fixture.dependencies)
    const firstOpen = session.open('/slow.pdf').catch((error: unknown) => error)
    await vi.waitFor(() => expect(fixture.loadDocument).toHaveBeenCalledTimes(1))

    const replacement = await session.open('/replacement.pdf')

    await expect(firstOpen).resolves.toBeInstanceOf(Error)
    expect(firstDestroy).toHaveBeenCalledTimes(1)
    expect(replacement.pageCount).toBe(2)
    expect(fixture.loadDocument.mock.calls[0]![0].worker)
      .not.toBe(fixture.loadDocument.mock.calls[1]![0].worker)
    await session.close()
  })

  it('cancels stale canvas and text work before rendering the next page', async () => {
    const fixture = fixtureDependencies()
    let rejectFirst: ((reason: unknown) => void) | undefined
    const firstPromise = new Promise<void>((_, reject) => {
      rejectFirst = reject
    })
    const firstCancel = vi.fn(() => rejectFirst?.(new PdfRenderCancelledError()))
    fixture.pages[0].render.mockReturnValueOnce({
      promise: firstPromise,
      cancel: firstCancel,
    })
    const firstTextCancel = vi.fn()
    fixture.createTextLayer.mockReturnValueOnce({
      render: vi.fn(async () => undefined),
      cancel: firstTextCancel,
    })
    const session = createPdfSession(fixture.dependencies)
    const handle = await session.open('/paper.pdf')
    const target = {
      canvas: document.createElement('canvas'),
      textLayer: document.createElement('div'),
      scale: 1,
      rotation: 0 as const,
    }

    const first = handle.renderPage(1, target)
    await Promise.resolve()
    await Promise.resolve()
    const second = handle.renderPage(2, target)

    await expect(first).rejects.toBeInstanceOf(PdfRenderCancelledError)
    await expect(second).resolves.toEqual({ width: 400, height: 600 })
    expect(firstCancel).toHaveBeenCalledTimes(1)
    expect(firstTextCancel).toHaveBeenCalledTimes(1)
    await session.close()
  })

  it('caps the backing canvas for unusually large PDF page dimensions', async () => {
    const fixture = fixtureDependencies()
    fixture.pages[0].getViewport.mockReturnValueOnce({ width: 10_000, height: 10_000 })
    const session = createPdfSession(fixture.dependencies)
    const handle = await session.open('/oversized-page.pdf')
    const canvas = document.createElement('canvas')

    await handle.renderPage(1, {
      canvas,
      textLayer: document.createElement('div'),
      scale: 1,
      rotation: 0,
    })

    expect(canvas.width * canvas.height).toBeLessThanOrEqual(25_000_000)
    expect(canvas.style.width).toBe('10000px')
    await session.close()
  })

  it('rejects a document whose page graph exceeds the reader limit', async () => {
    const fixture = fixtureDependencies()
    fixture.document.numPages = 2_001
    const session = createPdfSession(fixture.dependencies)

    await expect(session.open('/too-many-pages.pdf')).rejects.toBeInstanceOf(
      PdfSafetyLimitError,
    )
    expect(fixture.loadingDestroy).toHaveBeenCalledTimes(1)
    expect(fixture.document.getPage).not.toHaveBeenCalled()
  })

  it('rejects absurd page geometry before allocating a canvas', async () => {
    const fixture = fixtureDependencies()
    fixture.pages[0].getViewport.mockReturnValueOnce({
      width: 100_000,
      height: 100_000,
    })
    const session = createPdfSession(fixture.dependencies)
    const handle = await session.open('/absurd-page.pdf')
    const canvas = document.createElement('canvas')

    await expect(handle.renderPage(1, {
      canvas,
      textLayer: document.createElement('div'),
      scale: 1,
      rotation: 0,
    })).rejects.toBeInstanceOf(PdfSafetyLimitError)

    expect(canvas.width).toBe(300)
    expect(canvas.height).toBe(150)
    expect(fixture.pages[0].render).not.toHaveBeenCalled()
    await session.close()
  })

  it('rejects hostile text layers and cancels the in-flight canvas render', async () => {
    const fixture = fixtureDependencies()
    fixture.pages[0].getTextContent.mockResolvedValueOnce({
      items: Array.from({ length: 20_001 }, () => ({ str: 'x' })),
      styles: {},
      lang: null,
    })
    const session = createPdfSession(fixture.dependencies)
    const handle = await session.open('/hostile-text-layer.pdf')

    await expect(handle.renderPage(1, {
      canvas: document.createElement('canvas'),
      textLayer: document.createElement('div'),
      scale: 1,
      rotation: 0,
    })).rejects.toBeInstanceOf(PdfSafetyLimitError)

    expect(fixture.renderCancel).toHaveBeenCalledTimes(1)
    expect(fixture.createTextLayer).not.toHaveBeenCalled()
    await session.close()
  })

  it('bounds document-wide search text without retaining every page cache', async () => {
    const fixture = fixtureDependencies()
    const repeatedPage = fixture.pages[0]
    const largeText = 'x'.repeat(2_000_000)
    repeatedPage.getTextContent.mockResolvedValue({
      items: [{ str: largeText }],
      styles: {},
      lang: null,
    })
    fixture.document.numPages = 11
    fixture.document.getPage.mockImplementation(async () => repeatedPage)
    const session = createPdfSession(fixture.dependencies)
    const handle = await session.open('/wide-search.pdf')

    await expect(handle.search('not-present')).rejects.toBeInstanceOf(
      PdfSafetyLimitError,
    )

    expect(repeatedPage.getTextContent).toHaveBeenCalledTimes(11)
    expect(repeatedPage.cleanup).toHaveBeenCalledTimes(11)
    await session.close()
  })

  it('abandons the canvas cleanly when page text extraction fails', async () => {
    const fixture = fixtureDependencies()
    const textFailure = new Error('text layer unavailable')
    fixture.pages[0].getTextContent.mockRejectedValueOnce(textFailure)
    const renderCancel = vi.fn()
    fixture.pages[0].render.mockReturnValueOnce({
      promise: Promise.resolve(),
      cancel: renderCancel,
    })
    const session = createPdfSession(fixture.dependencies)
    const handle = await session.open('/text-failure.pdf')

    await expect(handle.renderPage(1, {
      canvas: document.createElement('canvas'),
      textLayer: document.createElement('div'),
      scale: 1,
      rotation: 0,
    })).rejects.toBe(textFailure)

    expect(renderCancel).toHaveBeenCalledTimes(1)
    await expect(handle.renderPage(2, {
      canvas: document.createElement('canvas'),
      textLayer: document.createElement('div'),
      scale: 1,
      rotation: 0,
    })).resolves.toEqual({ width: 400, height: 600 })
    await session.close()
  })
})
