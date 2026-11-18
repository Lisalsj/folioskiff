import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import StatusNotice from './StatusNotice.vue'

describe('StatusNotice', () => {
  it('uses alert only for errors and status for progress', () => {
    const error = mount(StatusNotice, { props: { kind: 'error', message: '加载失败' } })
    const progress = mount(StatusNotice, {
      props: { kind: 'progress', message: '正在加载' },
    })
    const warning = mount(StatusNotice, { props: { kind: 'warning', message: '数据不完整' } })

    expect(error.get('[role="alert"]').text()).toContain('加载失败')
    expect(error.find('[role="status"]').exists()).toBe(false)
    expect(progress.get('[role="status"]').text()).toContain('正在加载')
    expect(warning.find('[role="alert"]').exists()).toBe(false)
  })

  it('renders one optional action without owning the command', () => {
    const wrapper = mount(StatusNotice, {
      props: { kind: 'info', title: '离线降级', message: '仍可使用词法检索。' },
      slots: { action: '<button type="button">查看设置</button>' },
    })

    expect(wrapper.text()).toContain('离线降级')
    expect(wrapper.get('button').text()).toBe('查看设置')
  })
})
