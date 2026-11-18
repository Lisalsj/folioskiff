import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ReaderResearchSidebar from './ReaderResearchSidebar.vue'

describe('ReaderResearchSidebar', () => {
  it('keeps formal reports separate from Assistant history and lazy mounts visited panes', async () => {
    const wrapper = mount(ReaderResearchSidebar, {
      attachTo: document.body,
      props: {
        activeTab: 'annotations', mobileActive: false,
        reportVisited: false, assistantVisited: false,
      },
      slots: {
        annotations: '<section data-test="annotations-slot">annotations</section>',
        report: '<section data-test="report-slot">formal report</section>',
        assistant: '<section data-test="assistant-slot">assistant history</section>',
      },
    })

    expect(wrapper.get('[role="tab"][data-tab="assistant"]').attributes('role')).toBe('tab')
    expect(wrapper.get('[role="tab"][data-tab="report"]').attributes('role')).toBe('tab')
    expect(wrapper.find('[data-test="report-slot"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="assistant-slot"]').exists()).toBe(false)

    await wrapper.setProps({ reportVisited: true, assistantVisited: true })
    expect(wrapper.get('[data-test="report-slot"]').text()).toBe('formal report')
    expect(wrapper.get('[data-test="assistant-slot"]').text()).toBe('assistant history')
    await wrapper.get('[data-tab="assistant"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('update:activeTab')).toEqual([['assistant']])
    wrapper.unmount()
  })
})
