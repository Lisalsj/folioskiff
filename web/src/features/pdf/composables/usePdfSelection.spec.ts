import { ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { usePdfSelection } from './usePdfSelection'

describe('usePdfSelection', () => {
  afterEach(() => vi.restoreAllMocks())

  it('marks a selection verified only when its exact quote exists in the rendered text layer', () => {
    const changes: unknown[] = []
    const selection = usePdfSelection({
      page: ref(2),
      pageCount: ref(4),
      scale: ref(1.4),
      rotation: ref(0),
      canonicalPageSize: ref({ width: 420, height: 595 }),
      onChange: (value) => changes.push(value),
    })
    const layer = document.createElement('div')
    const shell = document.createElement('div')
    layer.textContent = 'context before selected evidence context after'
    shell.append(layer)
    vi.spyOn(shell, 'getBoundingClientRect').mockReturnValue({
      left: 100, top: 200, right: 688, bottom: 1033, width: 588, height: 833,
      x: 100, y: 200, toJSON: () => ({}),
    })
    vi.spyOn(window, 'getSelection').mockReturnValue({
      rangeCount: 1,
      isCollapsed: false,
      toString: () => 'selected evidence',
      getRangeAt: () => ({
        commonAncestorContainer: layer,
        getClientRects: () => [{
          left: 128, top: 228, right: 268, bottom: 256, width: 140, height: 28,
        }],
      }),
      removeAllRanges: vi.fn(),
    } as unknown as Selection)

    selection.captureSelection(layer, shell)
    expect(selection.activeSelection.value).toMatchObject({
      pageNumber: 2,
      quoteText: 'selected evidence',
      verified: true,
      rects: [{ x0: 20, y0: 20, x1: 120, y1: 40 }],
    })

    layer.textContent = 'different page text'
    selection.captureSelection(layer, shell)
    expect(selection.activeSelection.value?.verified).toBe(false)
    expect(changes).toHaveLength(2)
  })
})
