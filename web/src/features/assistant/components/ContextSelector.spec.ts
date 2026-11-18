import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ContextSelector from './ContextSelector.vue'

const selection = {
  pageNumber: 2,
  pageCount: 8,
  pageWidth: 420,
  pageHeight: 595,
  quoteText: 'selected evidence',
  contextBefore: 'before',
  contextAfter: 'after',
  rects: [{ x0: 10, y0: 20, x1: 160, y1: 38 }],
  verified: true,
}

describe('ContextSelector', () => {
  it('offers bounded paper scopes and explicit annotation selection', async () => {
    const wrapper = mount(ContextSelector, {
      props: {
        modelValue: 'selection',
        currentPage: 2,
        pageCount: 8,
        selection,
        annotations: [
          { id: 'annotation-1', pageNumber: 2, label: '核心假设' },
          { id: 'annotation-2', pageNumber: 4, label: '实验限制' },
        ],
        selectedAnnotationIds: [],
        sectionFallback: true,
      },
    })

    expect(wrapper.text()).toContain('选中文本')
    expect(wrapper.text()).toContain('当前页')
    expect(wrapper.text()).toContain('当前章节')
    expect(wrapper.text()).toContain('全文')
    expect(wrapper.text()).toContain('批注')
    expect(wrapper.find('.fallback-note').exists()).toBe(false)

    await wrapper.get('[data-test="assistant-context-choice"]').setValue('current_section')
    await wrapper.setProps({ modelValue: 'current_section' })
    expect(wrapper.find('.fallback-note').exists()).toBe(true)
    expect(wrapper.text()).toContain('最近一次章节范围识别已降级为当前页证据')

    await wrapper.get('[data-test="assistant-context-choice"]').setValue('annotations')
    await wrapper.setProps({ modelValue: 'annotations' })
    await wrapper.get('[data-test="assistant-annotation-annotation-1"]').setValue(true)
    expect(wrapper.emitted('update:selectedAnnotationIds')?.at(-1)).toEqual([
      ['annotation-1'],
    ])
  })
})
