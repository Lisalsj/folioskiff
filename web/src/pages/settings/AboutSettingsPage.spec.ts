import { shallowMount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import UpdateSettings from '@/components/UpdateSettings.vue'

import AboutSettingsPage from './AboutSettingsPage.vue'

describe('AboutSettingsPage', () => {
  it('shows open-source, diagnostic, and explicit update boundaries', () => {
    const wrapper = shallowMount(AboutSettingsPage)
    expect(wrapper.findComponent(UpdateSettings).exists()).toBe(true)
    expect(wrapper.text()).toContain('Apache-2.0')
    expect(wrapper.text()).toContain('不会自动上传日志')
    expect(wrapper.get('[data-test="export-redacted-diagnostics"]').text())
      .toContain('导出脱敏诊断摘要')
  })
})
