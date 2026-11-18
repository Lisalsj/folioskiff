import { defineComponent, h, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import { useResponsiveReader } from './useResponsiveReader'

describe('useResponsiveReader', () => {
  it('uses matchMedia and restores scroll independently for every Agent pane', async () => {
    let compactListener: ((event: MediaQueryListEvent) => void) | undefined
    const media = {
      matches: false,
      media: '(max-width: 1280px)',
      onchange: null,
      addEventListener: vi.fn((_type: string, listener: (event: MediaQueryListEvent) => void) => {
        compactListener = listener
      }),
      removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }
    let state!: ReturnType<typeof useResponsiveReader>
    const wrapper = mount(defineComponent({
      setup() {
        state = useResponsiveReader({ matchMedia: () => media as MediaQueryList })
        return () => h('aside', { ref: state.agentColumn })
      },
    }), { attachTo: document.body })
    await nextTick()
    if (!state.agentColumn.value) throw new Error('Missing Agent column')

    state.agentColumn.value.scrollTop = 180
    await state.showAgentTab('report')
    expect(state.scrollPositions.annotations).toBe(180)
    state.agentColumn.value.scrollTop = 41
    await state.showAgentTab('assistant')
    expect(state.scrollPositions.report).toBe(41)
    await state.showAgentTab('annotations')
    expect(state.agentColumn.value.scrollTop).toBe(180)

    compactListener?.({ matches: true } as MediaQueryListEvent)
    compactListener?.({ matches: false } as MediaQueryListEvent)
    expect(state.isCompactLayout.value).toBe(false)
    expect(state.agentTab.value).toBe('annotations')
    expect(state.agentColumn.value.scrollTop).toBe(180)
    wrapper.unmount()
    expect(media.removeEventListener).toHaveBeenCalledOnce()
  })

  it('uses explicit mobile values and closes the library drawer when returning wide', async () => {
    let compactListener: ((event: MediaQueryListEvent) => void) | undefined
    const media = {
      matches: true, media: '(max-width: 1280px)', onchange: null,
      addEventListener: vi.fn((_type: string, listener: (event: MediaQueryListEvent) => void) => {
        compactListener = listener
      }),
      removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }
    let state!: ReturnType<typeof useResponsiveReader>
    const wrapper = mount(defineComponent({
      setup() {
        state = useResponsiveReader({ matchMedia: () => media as MediaQueryList })
        return () => h('div')
      },
    }))
    state.libraryDrawerOpen.value = true
    state.selectMobilePanel('assistant')
    await nextTick()
    expect(state.mobilePanelView.value).toBe('assistant')
    expect(state.assistantVisited.value).toBe(true)
    state.selectMobilePanel('pdf')
    expect(state.mobilePanelView.value).toBe('pdf')

    compactListener?.({ matches: false } as MediaQueryListEvent)
    expect(state.libraryDrawerOpen.value).toBe(false)
    wrapper.unmount()
  })
})
