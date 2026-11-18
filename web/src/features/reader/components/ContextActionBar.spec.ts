import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { PdfTextSelection } from '@/lib/annotation-geometry'
import ContextActionBar from './ContextActionBar.vue'

const verifiedSelection: PdfTextSelection = {
  pageNumber: 2, pageCount: 8, pageWidth: 420, pageHeight: 595,
  quoteText: 'verified evidence', contextBefore: 'before', contextAfter: 'after',
  rects: [{ x0: 10, y0: 20, x1: 180, y1: 38 }], verified: true,
}

describe('ContextActionBar', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows exactly four verified-selection actions and retains action focus', async () => {
    vi.stubGlobal('getSelection', vi.fn(() => ({
      rangeCount: 1,
      getRangeAt: () => ({
        getBoundingClientRect: () => ({ left: 120, top: 80, right: 260, bottom: 110, width: 140, height: 30 }),
      }),
    })))
    const wrapper = mount(ContextActionBar, {
      attachTo: document.body,
      props: { selection: verifiedSelection },
    })
    await nextTick()

    expect(wrapper.findAll('button').map((button) => button.text())).toEqual([
      '解释', '翻译', '引用到提问', '保存标注',
    ])
    const explain = wrapper.get<HTMLButtonElement>('button')
    explain.element.focus()
    await explain.trigger('click')
    await nextTick()
    expect(wrapper.emitted('explain')?.[0]).toEqual([verifiedSelection])
    expect(document.activeElement).toBe(explain.element)
    const actionBar = wrapper.get<HTMLElement>('[data-test="context-action-bar"]')
    const left = Number.parseFloat(actionBar.element.style.left)
    expect(left).toBeGreaterThanOrEqual(8)
    expect(left).toBeLessThan(window.innerWidth)
    wrapper.unmount()
  })

  it('hides unverified evidence and returns focus after Escape', async () => {
    const returnTarget = document.createElement('button')
    document.body.append(returnTarget)
    returnTarget.focus()
    const wrapper = mount(ContextActionBar, {
      attachTo: document.body,
      props: { selection: verifiedSelection },
    })
    await nextTick()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(wrapper.find('[data-test="context-action-bar"]').exists()).toBe(false)
    expect(document.activeElement).toBe(returnTarget)

    await wrapper.setProps({ selection: { ...verifiedSelection, verified: false } })
    expect(wrapper.find('[data-test="context-action-bar"]').exists()).toBe(false)
    wrapper.unmount()
    returnTarget.remove()
  })
})
