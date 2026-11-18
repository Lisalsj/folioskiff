import { describe, expect, it } from 'vitest'

import {
  canonicalRectFromDisplay,
  displayRectFromCanonical,
  type PdfRect,
} from './annotation-geometry'

describe('annotation geometry', () => {
  const canonical = { width: 400, height: 600 }
  const rect: PdfRect = { x0: 40, y0: 120, x1: 160, y1: 180 }

  it.each([
    [0, { x0: 40, y0: 120, x1: 160, y1: 180 }],
    [90, { x0: 420, y0: 40, x1: 480, y1: 160 }],
    [180, { x0: 240, y0: 420, x1: 360, y1: 480 }],
    [270, { x0: 120, y0: 240, x1: 180, y1: 360 }],
  ] as const)('round-trips canonical rectangles at %i degrees', (rotation, displayed) => {
    expect(displayRectFromCanonical(rect, canonical, rotation)).toEqual(displayed)
    expect(canonicalRectFromDisplay(displayed, canonical, rotation)).toEqual(rect)
  })

  it('normalizes floating point noise and rejects clipped or invalid rectangles', () => {
    expect(canonicalRectFromDisplay(
      { x0: -0.00001, y0: 10, x1: 100.00000001, y1: 20 },
      canonical,
      0,
    )).toEqual({ x0: 0, y0: 10, x1: 100, y1: 20 })
    expect(() => canonicalRectFromDisplay(
      { x0: -10, y0: 0, x1: 20, y1: 10 },
      canonical,
      0,
    )).toThrow(/page bounds/)
    expect(() => displayRectFromCanonical(
      { x0: 20, y0: 10, x1: 20, y1: 30 },
      canonical,
      0,
    )).toThrow(/positive area/)
  })
})
