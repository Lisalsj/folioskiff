import { messageRef, readerText as t } from '@/stores/locale'
import {
  computed,
  nextTick,
  onMounted,
  onScopeDispose,
  ref,
  shallowRef,
  watch,
  type Ref,
} from 'vue'

import {
  createPdfSession,
  PdfRenderCancelledError,
  PdfSafetyLimitError,
  type PdfDocumentHandle,
  type PdfReaderState,
  type PdfRotation,
  type PdfSession,
} from '@/lib/pdf-session'

export interface PdfRenderSurface {
  canvas: HTMLCanvasElement
  textLayer: HTMLDivElement
  viewport: HTMLElement
}

interface UsePdfDocumentOptions {
  documentUrl: Readonly<Ref<string>>
  initialPage: Readonly<Ref<number>>
  initialScale: Readonly<Ref<number>>
  fitInitialWidth?: Readonly<Ref<boolean>>
  initialRotation: Readonly<Ref<PdfRotation>>
  sessionFactory?: () => PdfSession
  getSurface: () => PdfRenderSurface | null
  clearSelection: () => void
  onStateChange: () => void
}

export function usePdfDocument(options: UsePdfDocumentOptions) {
  const page = ref(1)
  const pageCount = ref(0)
  const pageInput = ref('1')
  const scale = ref(normalizeInitialScale(options.initialScale.value))
  const rotation = ref<PdfRotation>(options.initialRotation.value)
  const loading = ref(false)
  const rendering = ref(false)
  const errorMessage = messageRef(t)
  const pageSize = ref({ width: 0, height: 0 })
  const canonicalPageSize = ref({ width: 0, height: 0 })
  const handle = shallowRef<PdfDocumentHandle>()
  const session = (options.sessionFactory ?? createPdfSession)()

  let documentGeneration = 0
  let renderGeneration = 0
  let sessionNeedsClose = false
  let closingSession: Promise<void> | undefined
  let pendingNavigation: { documentUrl: string, page: number } | undefined

  const hasDocument = computed(() => options.documentUrl.value.trim().length > 0)
  const ready = computed(() => Boolean(handle.value))
  const zoomPercent = computed(() => `${Math.round(scale.value * 100)}%`)
  const viewState = computed<Omit<PdfReaderState, 'query'>>(() => ({
    page: page.value,
    pageCount: pageCount.value,
    scale: scale.value,
    rotation: rotation.value,
  }))

  async function closeSession(): Promise<void> {
    if (closingSession) {
      await closingSession
      return
    }
    if (!sessionNeedsClose) return
    sessionNeedsClose = false
    const pendingClose = session.close()
    closingSession = pendingClose
    try {
      await pendingClose
    } finally {
      if (closingSession === pendingClose) closingSession = undefined
    }
  }

  async function openDocument(): Promise<void> {
    const generation = ++documentGeneration
    const documentUrl = options.documentUrl.value
    renderGeneration += 1
    handle.value = undefined
    pageCount.value = 0
    errorMessage.value = ''
    pageSize.value = { width: 0, height: 0 }
    canonicalPageSize.value = { width: 0, height: 0 }
    options.clearSelection()
    loading.value = hasDocument.value

    try {
      await closeSession()
    } catch {
      if (generation === documentGeneration) {
        errorMessage.value = '无法关闭上一份 PDF。请重新打开阅读工作台。'
        loading.value = false
      }
      return
    }
    if (generation !== documentGeneration) return

    if (!hasDocument.value) {
      pendingNavigation = undefined
      loading.value = false
      options.onStateChange()
      return
    }

    sessionNeedsClose = true
    try {
      const opened = await session.open(documentUrl)
      if (generation !== documentGeneration) return
      if (!Number.isInteger(opened.pageCount) || opened.pageCount < 1) {
        throw new Error('PDF contains no readable pages')
      }
      handle.value = opened
      pageCount.value = opened.pageCount
      const requestedPage = pendingNavigation?.documentUrl === documentUrl
        ? pendingNavigation.page
        : options.initialPage.value
      pendingNavigation = undefined
      page.value = clampPage(requestedPage)
      scale.value = normalizeInitialScale(options.initialScale.value)
      rotation.value = options.initialRotation.value
      if (options.fitInitialWidth?.value) {
        await nextTick()
        const surface = options.getSurface()
        if (surface) {
          const size = await opened.getPageSize(page.value, rotation.value)
          if (generation !== documentGeneration) return
          scale.value = fitWidthScale(size.width, surface.viewport.clientWidth)
        }
      }
      pageInput.value = String(page.value)
      options.onStateChange()
      await renderCurrentPage()
    } catch (error) {
      if (generation === documentGeneration) {
        errorMessage.value = pdfFailureMessage(
          error,
          t('无法打开这份 PDF。文件可能已被移除、损坏或暂时不可用。'),
        )
      }
    } finally {
      if (generation === documentGeneration) loading.value = false
    }
  }

  async function renderCurrentPage(): Promise<void> {
    let surface = options.getSurface()
    if (!handle.value || !surface) {
      await nextTick()
      surface = options.getSurface()
    }
    const currentHandle = handle.value
    if (!currentHandle || !surface) return

    const generation = ++renderGeneration
    const requestedPage = page.value
    const requestedScale = scale.value
    const requestedRotation = rotation.value
    rendering.value = true
    try {
      const [size, canonicalSize] = await Promise.all([
        currentHandle.renderPage(requestedPage, {
          canvas: surface.canvas,
          textLayer: surface.textLayer,
          scale: requestedScale,
          rotation: requestedRotation,
        }),
        currentHandle.getPageSize(requestedPage, 0),
      ])
      if (generation === renderGeneration && handle.value === currentHandle) {
        pageSize.value = size
        canonicalPageSize.value = canonicalSize
      }
    } catch (error) {
      if (
        !(error instanceof PdfRenderCancelledError)
        && generation === renderGeneration
        && handle.value === currentHandle
      ) {
        errorMessage.value = pdfFailureMessage(
          error,
          t('这一页暂时无法呈现。你可以重试打开论文。'),
        )
      }
    } finally {
      if (generation === renderGeneration) rendering.value = false
    }
  }

  function normalizeRequestedPage(value: number): number {
    return Number.isFinite(value) ? Math.max(Math.round(value), 1) : 1
  }

  function clampPage(value: number): number {
    const integer = normalizeRequestedPage(value)
    return Math.min(Math.max(integer, 1), Math.max(pageCount.value, 1))
  }

  async function goToPage(value: number): Promise<void> {
    if (!handle.value) {
      if (hasDocument.value) {
        pendingNavigation = {
          documentUrl: options.documentUrl.value,
          page: normalizeRequestedPage(value),
        }
      }
      return
    }
    pendingNavigation = undefined
    const nextPage = clampPage(value)
    page.value = nextPage
    pageInput.value = String(nextPage)
    options.clearSelection()
    options.onStateChange()
    await renderCurrentPage()
  }

  async function submitPage(): Promise<void> {
    await goToPage(Number(pageInput.value))
  }

  async function zoomBy(delta: number): Promise<void> {
    scale.value = roundScale(Math.min(3, Math.max(0.5, scale.value + delta)))
    options.onStateChange()
    await renderCurrentPage()
  }

  async function rotateClockwise(): Promise<void> {
    rotation.value = ((rotation.value + 90) % 360) as PdfRotation
    options.onStateChange()
    await renderCurrentPage()
  }

  async function fitWidth(): Promise<void> {
    const currentHandle = handle.value
    const surface = options.getSurface()
    if (!currentHandle || !surface) return
    const size = await currentHandle.getPageSize(page.value, rotation.value)
    const containerWidth = surface.viewport.clientWidth || 720
    scale.value = fitWidthScale(size.width, containerWidth)
    options.onStateChange()
    await renderCurrentPage()
  }

  function setSafetyLimitError(): void {
    errorMessage.value = pdfFailureMessage(new PdfSafetyLimitError('too_much_text'), '')
  }

  async function retry(): Promise<void> {
    await openDocument()
  }

  function dispose(): void {
    documentGeneration += 1
    renderGeneration += 1
    pendingNavigation = undefined
    void closeSession().catch(() => undefined)
  }

  watch(options.documentUrl, () => void openDocument())
  onMounted(() => void openDocument())
  onScopeDispose(dispose)

  return {
    canonicalPageSize,
    errorMessage,
    fitWidth,
    goToPage,
    handle,
    hasDocument,
    loading,
    openDocument,
    page,
    pageCount,
    pageInput,
    pageSize,
    ready,
    renderCurrentPage,
    rendering,
    retry,
    rotateClockwise,
    rotation,
    scale,
    setSafetyLimitError,
    submitPage,
    viewState,
    zoomBy,
    zoomPercent,
  }
}

export function pdfFailureMessage(error: unknown, fallback: string): string {
  if (error instanceof PdfSafetyLimitError) {
    return '这份 PDF 的页数、页面尺寸或文本量超过本地安全处理上限。文件未被修改，你可以导入精简版本。'
  }
  return fallback
}

function roundScale(value: number): number {
  return Math.round(value * 100) / 100
}

function fitWidthScale(pageWidth: number, containerWidth: number): number {
  const resolvedContainerWidth = containerWidth || 720
  const availableWidth = Math.max(240, resolvedContainerWidth - 48)
  const requested = pageWidth > 0 ? availableWidth / pageWidth : 1.1
  return roundScale(Math.min(3, Math.max(0.5, requested)))
}

function normalizeInitialScale(value: number): number {
  const finite = Number.isFinite(value) ? value : 1.1
  return roundScale(Math.min(3, Math.max(0.5, finite)))
}
