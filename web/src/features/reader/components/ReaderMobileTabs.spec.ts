import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ReaderMobileTabs from './ReaderMobileTabs.vue'

describe('ReaderMobileTabs', () => {
  it('keeps four visible labels and emits explicit mobile pane values', async () => {
    const wrapper = mount(ReaderMobileTabs, {
      attachTo: document.body,
      props: { modelValue: 'pdf' },
    })

    expect(wrapper.findAll('button').map((button) => button.text().trim())).toEqual([
      'PDF', '批注', '报告', '助理',
    ])
    ;(wrapper.element as HTMLElement).style.display = 'flex'
    const pdf = wrapper.get<HTMLButtonElement>('[data-test="mobile-pdf-tab"]')
    const annotations = wrapper.get<HTMLButtonElement>('[data-test="mobile-annotations-tab"]')
    pdf.element.focus()
    await pdf.trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(document.activeElement).toBe(annotations.element)
    expect(wrapper.emitted('update:modelValue')).toEqual([['annotations']])
    wrapper.unmount()
  })
})
