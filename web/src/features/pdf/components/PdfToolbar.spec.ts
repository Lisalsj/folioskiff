import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PdfToolbar from './PdfToolbar.vue'

describe('PdfToolbar', () => {
  it('keeps labelled controls and emits navigation, search and bookmark intents', async () => {
    const wrapper = mount(PdfToolbar, {
      attachTo: document.body,
      props: {
        page: 2,
        pageCount: 3,
        pageInput: '2',
        zoomPercent: '125%',
        query: 'agent',
        ready: true,
        bookmarkReady: true,
      },
    })

    await wrapper.get('button[aria-label="上一页"]').trigger('click')
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await wrapper.get('[data-test="search-form"]').trigger('submit')
    await wrapper.get('[data-test="bookmark-page"]').trigger('click')
    expect(wrapper.emitted('previous')).toHaveLength(1)
    expect(wrapper.emitted('next')).toHaveLength(1)
    expect(wrapper.emitted('search')).toHaveLength(1)
    expect(wrapper.emitted('bookmark')).toHaveLength(1)

    ;(wrapper.vm as unknown as { focusSearch: () => void }).focusSearch()
    expect(document.activeElement).toBe(wrapper.get('[data-test="search-input"]').element)
    wrapper.unmount()
  })
})
