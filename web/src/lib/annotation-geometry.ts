import type { PdfRotation } from '@/lib/pdf-session'

export interface PdfRect {
  x0: number
  y0: number
  x1: number
  y1: number
}

export interface PdfPageDimensions {
  width: number
  height: number
}

export interface PdfTextSelection {
  pageNumber: number
  pageCount: number
  pageWidth: number
  pageHeight: number
  quoteText: string
  contextBefore: string | null
  contextAfter: string | null
  rects: PdfRect[]
  verified: boolean
}

const EPSILON = 0.0001

export function displayRectFromCanonical(
  rect: PdfRect,
  page: PdfPageDimensions,
  rotation: PdfRotation,
): PdfRect {
  const canonical = validatedRect(rect, page)
  const { width, height } = validatedPage(page)
  if (rotation === 0) return canonical
  if (rotation === 90) {
    return roundedRect({
      x0: height - canonical.y1,
      y0: canonical.x0,
      x1: height - canonical.y0,
      y1: canonical.x1,
    })
  }
  if (rotation === 180) {
    return roundedRect({
      x0: width - canonical.x1,
      y0: height - canonical.y1,
      x1: width - canonical.x0,
      y1: height - canonical.y0,
    })
  }
  return roundedRect({
    x0: canonical.y0,
    y0: width - canonical.x1,
    x1: canonical.y1,
    y1: width - canonical.x0,
  })
}

export function canonicalRectFromDisplay(
  rect: PdfRect,
  page: PdfPageDimensions,
  rotation: PdfRotation,
): PdfRect {
  const canonicalPage = validatedPage(page)
  const displayPage = rotation === 90 || rotation === 270
    ? { width: canonicalPage.height, height: canonicalPage.width }
    : canonicalPage
  const displayed = validatedRect(rect, displayPage)
  let result: PdfRect
  if (rotation === 0) {
    result = displayed
  } else if (rotation === 90) {
    result = {
      x0: displayed.y0,
      y0: canonicalPage.height - displayed.x1,
      x1: displayed.y1,
      y1: canonicalPage.height - displayed.x0,
    }
  } else if (rotation === 180) {
    result = {
      x0: canonicalPage.width - displayed.x1,
      y0: canonicalPage.height - displayed.y1,
      x1: canonicalPage.width - displayed.x0,
      y1: canonicalPage.height - displayed.y0,
    }
  } else {
    result = {
      x0: canonicalPage.width - displayed.y1,
      y0: displayed.x0,
      x1: canonicalPage.width - displayed.y0,
      y1: displayed.x1,
    }
  }
  return validatedRect(roundedRect(result), canonicalPage)
}

function validatedPage(page: PdfPageDimensions): PdfPageDimensions {
  if (
    !Number.isFinite(page.width)
    || !Number.isFinite(page.height)
    || page.width <= 0
    || page.height <= 0
  ) {
    throw new RangeError('PDF page dimensions must be finite and positive')
  }
  return page
}

function validatedRect(rect: PdfRect, page: PdfPageDimensions): PdfRect {
  const bounds = validatedPage(page)
  const values = [rect.x0, rect.y0, rect.x1, rect.y1]
  if (!values.every(Number.isFinite)) {
    throw new RangeError('PDF rectangle coordinates must be finite')
  }
  const normalized = roundedRect({
    x0: clampEdge(rect.x0, 0, bounds.width),
    y0: clampEdge(rect.y0, 0, bounds.height),
    x1: clampEdge(rect.x1, 0, bounds.width),
    y1: clampEdge(rect.y1, 0, bounds.height),
  })
  if (
    normalized.x0 < 0
    || normalized.y0 < 0
    || normalized.x1 > bounds.width
    || normalized.y1 > bounds.height
  ) {
    throw new RangeError('PDF rectangle must remain inside page bounds')
  }
  if (normalized.x1 <= normalized.x0 || normalized.y1 <= normalized.y0) {
    throw new RangeError('PDF rectangle must have positive area')
  }
  return normalized
}

function clampEdge(value: number, minimum: number, maximum: number): number {
  if (value < minimum && value >= minimum - EPSILON) return minimum
  if (value > maximum && value <= maximum + EPSILON) return maximum
  return value
}

function roundedRect(rect: PdfRect): PdfRect {
  return {
    x0: rounded(rect.x0),
    y0: rounded(rect.y0),
    x1: rounded(rect.x1),
    y1: rounded(rect.y1),
  }
}

function rounded(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000
}
