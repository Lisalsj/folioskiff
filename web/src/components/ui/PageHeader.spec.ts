import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PageHeader from './PageHeader.vue'

describe('PageHeader', () => {
  it('renders one page heading, description, status and optional actions', () => {
    const wrapper = mount(PageHeader, {
      props: {
        eyebrow: '本地收藏',
        title: '我的论文库',
        description: '保存、阅读与标注论文。',
      },
      slots: {
        status: '<span>12 篇论文</span>',
        actions: '<button type="button">导入论文</button>',
      },
    })

    expect(wrapper.findAll('h1')).toHaveLength(1)
    expect(wrapper.get('h1').text()).toBe('我的论文库')
    expect(wrapper.text()).toContain('保存、阅读与标注论文。')
    expect(wrapper.get('[data-test="page-header-status"]').text()).toBe('12 篇论文')
    expect(wrapper.get('button').text()).toBe('导入论文')
  })

  it('omits empty optional regions', () => {
    const wrapper = mount(PageHeader, { props: { title: '阅读简报' } })

    expect(wrapper.find('[data-test="page-header-status"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="page-header-actions"]').exists()).toBe(false)
  })
})
