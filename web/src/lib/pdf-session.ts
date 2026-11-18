export type PdfRotation = 0 | 90 | 180 | 270

export interface PdfReaderState {
  page: number
  pageCount: number
  scale: number
  rotation: PdfRotation
  query: string
}

export interface PdfSearchResult {
  pageNumber: number
  occurrences: number
  snippet: string
}

export interface PdfPageSize {
  width: number
  height: number
}

export interface PdfRenderTarget {
  canvas: HTMLCanvasElement
  textLayer: HTMLDivElement
  scale: number
  rotation: PdfRotation
}

export interface PdfDocumentHandle {
  readonly pageCount: number
  renderPage(pageNumber: number, target: PdfRenderTarget): Promise<PdfPageSize>
  getPageSize(pageNumber: number, rotation: PdfRotation): Promise<PdfPageSize>
  search(query: string): Promise<PdfSearchResult[]>
}

export interface PdfSession {
  open(documentUrl: string): Promise<PdfDocumentHandle>
  close(): Promise<void>
}

interface PdfViewportLike {
  width: number
  height: number
}

interface PdfTextContentLike {
  items: Array<{ str?: string }>
  styles: Record<string, unknown>
  lang: string | null
}

interface PdfRenderTaskLike {
  promise: Promise<void>
  cancel(): void
}

interface PdfTextLayerTaskLike {
  render(): Promise<unknown>
  cancel(): void
}

interface PdfPageLike {
  getViewport(options: { scale: number; rotation: number }): PdfViewportLike
  getTextContent(): Promise<PdfTextContentLike>
  render(options: {
    canvas: HTMLCanvasElement
    viewport: PdfViewportLike
    transform?: number[]
    background: string
  }): PdfRenderTaskLike
  cleanup?(): boolean | void
}

interface PdfDocumentLike {
  numPages: number
  getPage(pageNumber: number): Promise<PdfPageLike>
}

interface PdfLoadingTaskLike {
  promise: Promise<PdfDocumentLike>
  destroy(): Promise<void>
}

interface PdfWorkerLike {
  destroy(): void
}

export interface PdfSessionDependencies {
  createWorker(): PdfWorkerLike | Promise<PdfWorkerLike>
  loadDocument(options: {
    url: string
    withCredentials: boolean
    maxImageSize: number
    isEvalSupported: boolean
    enableScripting: boolean
    enableXfa: boolean
    stopAtErrors: boolean
    worker: PdfWorkerLike
  }): PdfLoadingTaskLike | Promise<PdfLoadingTaskLike>
  createTextLayer(options: {
    textContentSource: PdfTextContentLike
    container: HTMLDivElement
    viewport: PdfViewportLike
  }): PdfTextLayerTaskLike
  setLayerDimensions(container: HTMLDivElement, viewport: PdfViewportLike): void
  pixelRatio(): number
}

const MAX_IMAGE_PIXELS = 25_000_000
const MAX_CANVAS_PIXELS = 25_000_000
const MAX_PDF_PAGES = 2_000
const MAX_PAGE_DIMENSION = 14_400
const MAX_PAGE_AREA = 100_000_000
const MAX_TEXT_ITEMS_PER_PAGE = 20_000
const MAX_TEXT_CHARACTERS_PER_PAGE = 2_000_000
const MAX_TEXT_CHARACTERS_TOTAL = 20_000_000

type PdfSafetyLimitCode = 'too_many_pages' | 'page_too_large' | 'too_much_text'

export class PdfSafetyLimitError extends Error {
  readonly code: PdfSafetyLimitCode

  constructor(code: PdfSafetyLimitCode) {
    super('The PDF exceeds a safe local reader resource limit')
    this.name = 'PdfSafetyLimitError'
    this.code = code
  }
}

export class PdfRenderCancelledError extends Error {
  constructor() {
    super('PDF page rendering was superseded')
    this.name = 'PdfRenderCancelledError'
  }
}

class PdfDocumentAdapter implements PdfDocumentHandle {
  readonly pageCount: number

  private renderGeneration = 0
  private renderTask: PdfRenderTaskLike | undefined
  private textLayerTask: PdfTextLayerTaskLike | undefined
  private activePage: PdfPageLike | undefined
  private readonly textCache = new Map<number, Promise<PdfTextContentLike>>()

  constructor(
    private readonly document: PdfDocumentLike,
    private readonly dependencies: PdfSessionDependencies,
  ) {
    this.pageCount = document.numPages
  }

  async renderPage(pageNumber: number, target: PdfRenderTarget): Promise<PdfPageSize> {
    this.validatePage(pageNumber)
    this.cancelCurrentRender()
    const generation = ++this.renderGeneration
    const page = await this.document.getPage(pageNumber)
    if (generation !== this.renderGeneration) {
      page.cleanup?.()
      throw new PdfRenderCancelledError()
    }

    let viewport: PdfViewportLike
    try {
      viewport = page.getViewport({
        scale: target.scale,
        rotation: target.rotation,
      })
      validateViewport(viewport)
    } catch (error) {
      page.cleanup?.()
      throw error
    }
    const outputScale = boundedPixelRatio(
      this.dependencies.pixelRatio(),
      viewport.width,
      viewport.height,
    )
    target.canvas.width = Math.max(1, Math.floor(viewport.width * outputScale))
    target.canvas.height = Math.max(1, Math.floor(viewport.height * outputScale))
    target.canvas.style.width = `${Math.round(viewport.width * 100) / 100}px`
    target.canvas.style.height = `${Math.round(viewport.height * 100) / 100}px`
    target.textLayer.replaceChildren()
    this.dependencies.setLayerDimensions(target.textLayer, viewport)

    this.activePage = page
    this.renderTask = page.render({
      canvas: target.canvas,
      viewport,
      transform: outputScale === 1
        ? undefined
        : [outputScale, 0, 0, outputScale, 0, 0],
      background: '#ffffff',
    })
    const renderPromise = this.renderTask.promise
    void renderPromise.catch(() => undefined)

    try {
      const textContent = this.validatedPageText(
        pageNumber,
        await this.pageText(pageNumber, page),
      )
      if (generation !== this.renderGeneration) {
        throw new PdfRenderCancelledError()
      }
      this.textLayerTask = this.dependencies.createTextLayer({
        textContentSource: textContent,
        container: target.textLayer,
        viewport,
      })
      await Promise.all([
        renderPromise,
        this.textLayerTask.render(),
      ])
      if (generation !== this.renderGeneration) {
        throw new PdfRenderCancelledError()
      }
      this.renderTask = undefined
      this.textLayerTask = undefined
    } catch (error) {
      const cancelled = (
        generation !== this.renderGeneration
        || error instanceof PdfRenderCancelledError
        || (error instanceof Error && error.name === 'RenderingCancelledException')
      )
      this.abandonRender(generation)
      if (cancelled) throw new PdfRenderCancelledError()
      throw error
    }
    return { width: viewport.width, height: viewport.height }
  }

  async getPageSize(
    pageNumber: number,
    rotation: PdfRotation,
  ): Promise<PdfPageSize> {
    this.validatePage(pageNumber)
    const page = await this.document.getPage(pageNumber)
    let viewport: PdfViewportLike
    try {
      viewport = page.getViewport({ scale: 1, rotation })
      validateViewport(viewport)
    } catch (error) {
      if (page !== this.activePage) page.cleanup?.()
      throw error
    }
    if (page !== this.activePage) page.cleanup?.()
    return { width: viewport.width, height: viewport.height }
  }

  async search(query: string): Promise<PdfSearchResult[]> {
    const needle = normalizeSearchText(query)
    if (!needle) return []
    const matches: PdfSearchResult[] = []
    let characterTotal = 0
    for (let pageNumber = 1; pageNumber <= this.pageCount; pageNumber += 1) {
      const page = await this.document.getPage(pageNumber)
      try {
        const content = this.validatedPageText(
          pageNumber,
          await this.pageText(pageNumber, page),
        )
        characterTotal += textCharacterCount(content)
        if (characterTotal > MAX_TEXT_CHARACTERS_TOTAL) {
          throw new PdfSafetyLimitError('too_much_text')
        }
        const text = normalizePageText(content)
        const normalized = text.toLocaleLowerCase()
        const occurrences = countOccurrences(normalized, needle)
        if (occurrences > 0) {
          matches.push({
            pageNumber,
            occurrences,
            snippet: searchSnippet(text, normalized.indexOf(needle), needle.length),
          })
        }
      } finally {
        if (page !== this.activePage) {
          page.cleanup?.()
          this.textCache.delete(pageNumber)
        }
      }
    }
    return matches
  }

  dispose(): void {
    this.cancelCurrentRender()
    this.activePage?.cleanup?.()
    this.activePage = undefined
    this.textCache.clear()
  }

  private pageText(
    pageNumber: number,
    page: PdfPageLike,
  ): Promise<PdfTextContentLike> {
    let pending = this.textCache.get(pageNumber)
    if (!pending) {
      pending = page.getTextContent()
      this.textCache.set(pageNumber, pending)
      void pending.catch(() => {
        if (this.textCache.get(pageNumber) === pending) {
          this.textCache.delete(pageNumber)
        }
      })
    }
    return pending
  }

  private validatedPageText(
    pageNumber: number,
    content: PdfTextContentLike,
  ): PdfTextContentLike {
    try {
      return validateTextContent(content)
    } catch (error) {
      this.textCache.delete(pageNumber)
      throw error
    }
  }

  private abandonRender(generation: number): void {
    if (generation !== this.renderGeneration) return
    this.renderTask?.cancel()
    this.textLayerTask?.cancel()
    this.renderTask = undefined
    this.textLayerTask = undefined
    this.activePage?.cleanup?.()
    this.activePage = undefined
  }

  private cancelCurrentRender(): void {
    this.renderGeneration += 1
    this.renderTask?.cancel()
    this.textLayerTask?.cancel()
    this.renderTask = undefined
    this.textLayerTask = undefined
    this.activePage?.cleanup?.()
    this.activePage = undefined
  }

  private validatePage(pageNumber: number): void {
    if (!Number.isInteger(pageNumber) || pageNumber < 1 || pageNumber > this.pageCount) {
      throw new RangeError(`PDF page must be between 1 and ${this.pageCount}`)
    }
  }
}

class PdfSessionAdapter implements PdfSession {
  private loadingTask: PdfLoadingTaskLike | undefined
  private handle: PdfDocumentAdapter | undefined
  private openGeneration = 0

  constructor(private readonly dependencies: PdfSessionDependencies) {}

  async open(documentUrl: string): Promise<PdfDocumentHandle> {
    const url = documentUrl.trim()
    if (!url) throw new TypeError('A PDF document URL is required')
    const generation = ++this.openGeneration
    await this.releaseActiveDocument()
    this.assertCurrent(generation)

    const worker = await this.dependencies.createWorker()
    if (generation !== this.openGeneration) {
      destroyWorker(worker)
      throw new PdfDocumentOpenCancelledError()
    }

    let loadingTask: PdfLoadingTaskLike
    try {
      loadingTask = await this.dependencies.loadDocument({
        url,
        withCredentials: true,
        maxImageSize: MAX_IMAGE_PIXELS,
        isEvalSupported: false,
        enableScripting: false,
        enableXfa: false,
        stopAtErrors: true,
        worker,
      })
    } catch (error) {
      destroyWorker(worker)
      throw error
    }
    if (generation !== this.openGeneration) {
      await loadingTask.destroy().catch(() => undefined)
      throw new PdfDocumentOpenCancelledError()
    }
    this.loadingTask = loadingTask
    try {
      const document = await loadingTask.promise
      this.assertCurrent(generation, loadingTask)
      if (
        !Number.isInteger(document.numPages)
        || document.numPages < 1
        || document.numPages > MAX_PDF_PAGES
      ) {
        throw new PdfSafetyLimitError('too_many_pages')
      }
      const handle = new PdfDocumentAdapter(document, this.dependencies)
      this.handle = handle
      return handle
    } catch (error) {
      if (this.loadingTask === loadingTask) {
        this.loadingTask = undefined
        await loadingTask.destroy().catch(() => undefined)
      }
      throw error
    }
  }

  async close(): Promise<void> {
    this.openGeneration += 1
    await this.releaseActiveDocument()
  }

  private assertCurrent(
    generation: number,
    loadingTask?: PdfLoadingTaskLike,
  ): void {
    if (
      generation !== this.openGeneration
      || (loadingTask !== undefined && loadingTask !== this.loadingTask)
    ) {
      throw new PdfDocumentOpenCancelledError()
    }
  }

  private async releaseActiveDocument(): Promise<void> {
    this.handle?.dispose()
    this.handle = undefined
    const loadingTask = this.loadingTask
    this.loadingTask = undefined
    if (loadingTask) await loadingTask.destroy().catch(() => undefined)
  }
}

export function createPdfSession(
  dependencies: PdfSessionDependencies = createDefaultDependencies(),
): PdfSession {
  return new PdfSessionAdapter(dependencies)
}

function createDefaultDependencies(): PdfSessionDependencies {
  let runtime: typeof import('pdfjs-dist') | undefined
  let runtimePromise: Promise<typeof import('pdfjs-dist')> | undefined

  async function loadRuntime(): Promise<typeof import('pdfjs-dist')> {
    runtimePromise ??= Promise.all([
      import('pdfjs-dist'),
      import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
    ]).then(([loadedRuntime, workerModule]) => {
      loadedRuntime.GlobalWorkerOptions.workerSrc = workerModule.default
      runtime = loadedRuntime
      return loadedRuntime
    })
    return runtimePromise
  }

  return {
    createWorker: async () => new (await loadRuntime()).PDFWorker(),
    loadDocument: async (options) => {
      const loadedRuntime = await loadRuntime()
      return loadedRuntime.getDocument(
        options as unknown as Parameters<typeof loadedRuntime.getDocument>[0],
      ) as unknown as PdfLoadingTaskLike
    },
    createTextLayer: (options) => {
      if (!runtime) throw new Error('PDF.js runtime is not loaded')
      return new runtime.TextLayer(
        options as unknown as ConstructorParameters<typeof runtime.TextLayer>[0],
      )
    },
    setLayerDimensions: (container, viewport) => {
      if (!runtime) throw new Error('PDF.js runtime is not loaded')
      runtime.setLayerDimensions(
        container,
        viewport as Parameters<typeof runtime.setLayerDimensions>[1],
      )
    },
    pixelRatio: () => Math.max(1, Math.min(window.devicePixelRatio || 1, 2)),
  }
}

class PdfDocumentOpenCancelledError extends Error {
  constructor() {
    super('PDF document opening was superseded')
    this.name = 'PdfDocumentOpenCancelledError'
  }
}

function destroyWorker(worker: PdfWorkerLike): void {
  try {
    worker.destroy()
  } catch {
    // The loading failure remains the primary diagnostic.
  }
}

function boundedPixelRatio(value: number, width: number, height: number): number {
  const finite = Number.isFinite(value) ? Math.max(1, Math.min(value, 2)) : 1
  const pixels = width * height * finite * finite
  if (pixels <= MAX_CANVAS_PIXELS) return finite
  return Math.min(
    finite,
    Math.sqrt(MAX_CANVAS_PIXELS / Math.max(1, width * height)),
  )
}

function validateViewport(viewport: PdfViewportLike): PdfViewportLike {
  const { width, height } = viewport
  if (
    !Number.isFinite(width)
    || !Number.isFinite(height)
    || width <= 0
    || height <= 0
    || width > MAX_PAGE_DIMENSION
    || height > MAX_PAGE_DIMENSION
    || width * height > MAX_PAGE_AREA
  ) {
    throw new PdfSafetyLimitError('page_too_large')
  }
  return viewport
}

function validateTextContent(content: PdfTextContentLike): PdfTextContentLike {
  if (content.items.length > MAX_TEXT_ITEMS_PER_PAGE) {
    throw new PdfSafetyLimitError('too_much_text')
  }
  if (textCharacterCount(content) > MAX_TEXT_CHARACTERS_PER_PAGE) {
    throw new PdfSafetyLimitError('too_much_text')
  }
  return content
}

function textCharacterCount(content: PdfTextContentLike): number {
  let characters = 0
  for (const item of content.items) {
    characters += typeof item.str === 'string' ? item.str.length : 0
  }
  return characters
}

function normalizeSearchText(value: string): string {
  return value.trim().replace(/\s+/g, ' ').toLocaleLowerCase()
}

function normalizePageText(content: PdfTextContentLike): string {
  return content.items
    .map((item) => item.str ?? '')
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function countOccurrences(text: string, needle: string): number {
  let count = 0
  let offset = 0
  while ((offset = text.indexOf(needle, offset)) >= 0) {
    count += 1
    offset += Math.max(needle.length, 1)
  }
  return count
}

function searchSnippet(text: string, index: number, length: number): string {
  const start = Math.max(0, index - 44)
  const end = Math.min(text.length, index + length + 72)
  return `${start > 0 ? '…' : ''}${text.slice(start, end)}${end < text.length ? '…' : ''}`
}
