import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PublishingSettings from './PublishingSettings.vue'

describe('PublishingSettings', () => {
  it('accepts public copy only and exposes no credential or path fields', async () => {
    const wrapper = mount(PublishingSettings, {
      attachTo: document.body,
      props: {
        slug: 'paper-reader-picks', title: '我的论文精选', intro: '',
      },
    })
    expect(wrapper.find('input[type="password"]').exists()).toBe(false)
    expect(wrapper.find('input[type="file"]').exists()).toBe(false)
    await wrapper.get('[data-test="publishing-title"]').setValue('公开标题')
    expect(wrapper.emitted('update:title')?.[0]).toEqual(['公开标题'])
    ;(wrapper.vm as unknown as { focusHeading: () => void }).focusHeading()
    expect(document.activeElement).toBe(wrapper.get('[data-test="publishing-composer-heading"]').element)
    wrapper.unmount()
  })
})
