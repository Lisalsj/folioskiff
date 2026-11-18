import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ComparisonResult from './ComparisonResult.vue'

describe('ComparisonResult', () => {
  it('does not imply that preflight already generated a report', () => {
    const wrapper = mount(ComparisonResult, {
      props: { report: null, loading: false },
    })
    expect(wrapper.text()).toContain('报告只会在你点击生成后出现')
    expect(wrapper.find('[data-test="export-comparison-markdown"]').exists()).toBe(false)
  })
})
