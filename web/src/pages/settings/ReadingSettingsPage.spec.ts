import { shallowMount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import GeneralSettings from '@/components/GeneralSettings.vue'

import ReadingSettingsPage from './ReadingSettingsPage.vue'

describe('ReadingSettingsPage', () => {
  it('composes only the reading preference form', () => {
    const wrapper = shallowMount(ReadingSettingsPage)
    expect(wrapper.findComponent(GeneralSettings).exists()).toBe(true)
    expect(wrapper.get('[data-settings-heading]').text()).toBe('阅读与语言')
  })
})
