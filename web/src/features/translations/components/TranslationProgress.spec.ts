import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import TranslationProgress from './TranslationProgress.vue'

describe('TranslationProgress', () => {
  it('keeps failure diagnostics and offers resume', async () => {
    const wrapper = mount(TranslationProgress, {
      props: {
        status: 'partial',
        completed: 3,
        total: 5,
        percent: 60,
        active: false,
        cancelRequested: false,
        diagnostics: ['第 4 页：结构化输出校验失败'],
      },
    })
    expect(wrapper.text()).toContain('3 / 5')
    expect(wrapper.text()).toContain('结构化输出校验失败')
    await wrapper.get('[data-test="translation-resume"]').trigger('click')
    expect(wrapper.emitted('resume')).toHaveLength(1)
  })
})
