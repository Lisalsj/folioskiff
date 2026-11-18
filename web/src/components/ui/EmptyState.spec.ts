import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import EmptyState from './EmptyState.vue'

describe('EmptyState', () => {
  it('provides one primary and one secondary action region', () => {
    const wrapper = mount(EmptyState, {
      props: {
        eyebrow: '开始第一条线索',
        title: '论文库还是空的',
        description: '收藏论文或导入合法取得的 PDF。',
      },
      slots: {
        icon: '<span aria-hidden="true">书</span>',
        primary: '<button type="button">导入论文</button>',
        secondary: '<a href="/plans">建立计划</a>',
      },
    })

    expect(wrapper.get('h2').text()).toBe('论文库还是空的')
    expect(wrapper.get('[data-test="empty-primary"]').findAll('button')).toHaveLength(1)
    expect(wrapper.get('[data-test="empty-secondary"]').findAll('a')).toHaveLength(1)
    expect(wrapper.attributes('role')).toBeUndefined()
  })
})
