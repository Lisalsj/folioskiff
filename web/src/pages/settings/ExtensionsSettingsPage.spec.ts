import { shallowMount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PluginSettings from '@/components/PluginSettings.vue'

import ExtensionsSettingsPage from './ExtensionsSettingsPage.vue'

describe('ExtensionsSettingsPage', () => {
  it('keeps plugin permissions beside the publishing privacy boundary', () => {
    const wrapper = shallowMount(ExtensionsSettingsPage, {
      global: { stubs: { RouterLink: { template: '<a href="/publishing"><slot /></a>' } } },
    })
    expect(wrapper.findComponent(PluginSettings).exists()).toBe(true)
    expect(wrapper.text()).toContain('PDF、私有笔记、聊天、行为数据、凭据和完整提示词不会进入')
    expect(wrapper.get('a').attributes('href')).toBe('/publishing')
  })
})
