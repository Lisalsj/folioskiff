import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AsyncBoundary from './AsyncBoundary.vue'

describe('AsyncBoundary', () => {
  it('uses deterministic loading, error, empty and content precedence', async () => {
    const wrapper = mount(AsyncBoundary, {
      props: {
        loading: true,
        error: '读取失败',
        empty: true,
        loadingMessage: '正在读取',
        emptyTitle: '暂无内容',
      },
      slots: { default: '<article data-test="content">内容</article>' },
    })

    expect(wrapper.attributes('data-state')).toBe('loading')
    expect(wrapper.get('[role="status"]').text()).toContain('正在读取')

    await wrapper.setProps({ loading: false })
    expect(wrapper.attributes('data-state')).toBe('error')
    expect(wrapper.get('[role="alert"]').text()).toContain('读取失败')

    await wrapper.setProps({ error: '' })
    expect(wrapper.attributes('data-state')).toBe('empty')
    expect(wrapper.text()).toContain('暂无内容')

    await wrapper.setProps({ empty: false })
    expect(wrapper.attributes('data-state')).toBe('content')
    expect(wrapper.get('[data-test="content"]').text()).toBe('内容')
  })

  it('emits retry from the default error state', async () => {
    const wrapper = mount(AsyncBoundary, {
      props: { loading: false, error: '读取失败', retryLabel: '重新读取' },
    })

    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('retry')).toEqual([[]])
  })
})
