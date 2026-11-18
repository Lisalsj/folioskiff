import { ref, type Ref } from 'vue'

import { canonicalRectFromDisplay, type PdfTextSelection } from '@/lib/annotation-geometry'
import type { PdfRotation } from '@/lib/pdf-session'

interface UsePdfSelectionOptions {
  page: Readonly<Ref<number>>
  pageCount: Readonly<Ref<number>>
  scale: Readonly<Ref<number>>
  rotation: Readonly<Ref<PdfRotation>>
  canonicalPageSize: Readonly<Ref<{ width: number, height: number }>>
  onChange: (selection: PdfTextSelection | null) => void
}

export function usePdfSelection(options: UsePdfSelectionOptions) {
  const activeSelection = ref<PdfTextSelection | null>(null)

  function captureSelection(layer: HTMLDivElement, shell: HTMLElement): void {
    const browserSelection = window.getSelection()
    const canonical = options.canonicalPageSize.value
    if (
      !browserSelection
      || browserSelection.rangeCount !== 1
      || browserSelection.isCollapsed
      || canonical.width <= 0
      || canonical.height <= 0
    ) {
      clearSelection(false)
      return
    }
    const range = browserSelection.getRangeAt(0)
    const commonNode = range.commonAncestorContainer
    const commonElement = commonNode instanceof Element ? commonNode : commonNode.parentElement
    if (commonElement !== layer && !layer.contains(commonElement)) {
      clearSelection(false)
      return
    }
    const quoteText = browserSelection.toString()
    if (!quoteText.trim()) {
      clearSelection(false)
      return
    }
    const shellBounds = shell.getBoundingClientRect()
    const rects = Array.from(range.getClientRects()).flatMap((rect) => {
      if (rect.width <= 0 || rect.height <= 0) return []
      try {
        return [canonicalRectFromDisplay(
          {
            x0: (rect.left - shellBounds.left) / options.scale.value,
            y0: (rect.top - shellBounds.top) / options.scale.value,
            x1: (rect.right - shellBounds.left) / options.scale.value,
            y1: (rect.bottom - shellBounds.top) / options.scale.value,
          },
          canonical,
          options.rotation.value,
        )]
      } catch {
        return []
      }
    })
    if (rects.length === 0) {
      clearSelection(false)
      return
    }
    const pageText = layer.textContent ?? ''
    const quoteOffset = pageText.indexOf(quoteText)
    activeSelection.value = {
      pageNumber: options.page.value,
      pageCount: options.pageCount.value,
      pageWidth: canonical.width,
      pageHeight: canonical.height,
      quoteText,
      contextBefore: quoteOffset >= 0
        ? pageText.slice(Math.max(0, quoteOffset - 500), quoteOffset)
        : null,
      contextAfter: quoteOffset >= 0
        ? pageText.slice(quoteOffset + quoteText.length, quoteOffset + quoteText.length + 500)
        : null,
      rects,
      verified: quoteOffset >= 0,
    }
    options.onChange(activeSelection.value)
  }

  function clearSelection(clearBrowserSelection = true): void {
    if (clearBrowserSelection) window.getSelection()?.removeAllRanges()
    if (activeSelection.value === null) return
    activeSelection.value = null
    options.onChange(null)
  }

  return { activeSelection, captureSelection, clearSelection }
}
