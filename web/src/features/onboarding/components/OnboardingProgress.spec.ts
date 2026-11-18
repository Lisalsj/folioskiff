import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import OnboardingProgress from './OnboardingProgress.vue'

describe('OnboardingProgress', () => {
  it('announces the current step and treats skipped steps as neutral', () => {
    const wrapper = mount(OnboardingProgress, {
      props: {
        current: 'agents',
        steps: {
          storage_privacy: 'completed',
          agents: 'pending',
          sources: 'skipped',
          reading_preferences: 'pending',
        },
      },
    })

    expect(wrapper.get('ol').attributes('aria-label')).toBe('首次配置进度')
    expect(wrapper.get('[aria-current="step"]').text()).toContain('AI 能力')
    const skipped = wrapper.get('[data-step="sources"]')
    expect(skipped.text()).toContain('已跳过')
    expect(skipped.classes()).not.toContain('is-error')
  })

  it('uses four concise, human-readable step names', () => {
    const wrapper = mount(OnboardingProgress, {
      props: {
        current: 'storage_privacy',
        steps: {
          storage_privacy: 'pending',
          agents: 'pending',
          sources: 'pending',
          reading_preferences: 'pending',
        },
      },
    })

    expect(wrapper.findAll('.step-copy strong').map((item) => item.text())).toEqual([
      '存储与隐私',
      'AI 能力',
      '论文来源',
      '阅读偏好',
    ])
    expect(wrapper.findAll('.step-copy > span').map((item) => item.text())).toEqual([
      '当前步骤',
      '待配置',
      '待配置',
      '待配置',
    ])
  })
})
