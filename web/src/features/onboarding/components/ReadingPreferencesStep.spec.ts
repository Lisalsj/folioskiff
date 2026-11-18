import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ReadingPreferencesStep from './ReadingPreferencesStep.vue'

describe('ReadingPreferencesStep', () => {
  it('starts from safe Chinese-reading defaults', () => {
    const wrapper = mount(ReadingPreferencesStep)

    expect(wrapper.get<HTMLSelectElement>('[data-test="report-language"]').element.value)
      .toBe('zh-CN')
    expect(wrapper.get<HTMLSelectElement>('[data-test="translation-source"]').element.value)
      .toBe('en')
    expect(wrapper.get<HTMLSelectElement>('[data-test="translation-target"]').element.value)
      .toBe('zh-CN')
    expect(wrapper.get<HTMLSelectElement>('[data-test="default-agent"]').element.value)
      .toBe('codex_cli')
  })

  it('emits one closed preference payload without saving on its own', async () => {
    const wrapper = mount(ReadingPreferencesStep)
    await wrapper.get('[data-test="theme"]').setValue('dark')
    await wrapper.get('[data-test="reasoning-effort"]').setValue('high')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('complete')?.[0]).toEqual([{
      report_language: 'zh-CN',
      translation_source_language: 'en',
      translation_target_language: 'zh-CN',
      default_agent_backend: 'codex_cli',
      reasoning_effort: 'high',
      theme: 'dark',
      timezone_mode: 'system',
      timezone_name: null,
    }])
  })

  it('keeps translation direction valid when either language changes', async () => {
    const wrapper = mount(ReadingPreferencesStep)

    await wrapper.get('[data-test="translation-source"]').setValue('zh-CN')
    expect(wrapper.get<HTMLSelectElement>('[data-test="translation-target"]').element.value)
      .toBe('en')

    await wrapper.get('[data-test="translation-target"]').setValue('zh-CN')
    expect(wrapper.get<HTMLSelectElement>('[data-test="translation-source"]').element.value)
      .toBe('en')
  })
})
