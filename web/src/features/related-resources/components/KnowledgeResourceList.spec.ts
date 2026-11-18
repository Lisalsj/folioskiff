import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import KnowledgeResourceList from './KnowledgeResourceList.vue'

describe('KnowledgeResourceList', () => {
  it('uses a plain verified website list with safe external links and no paper action', () => {
    const wrapper = mount(KnowledgeResourceList, {
      props: {
        resources: [{
          id: 'resource-1', title: 'Official diffusion guide',
          canonical_url: 'https://docs.example.org/diffusion',
          final_url: 'https://docs.example.org/diffusion', domain: 'docs.example.org',
          resource_type: 'official_docs', reason: '解释关键调度算法', publisher: 'Example Lab',
          verification_status: 'verified', http_status: 200,
          accessed_at: '2026-08-01T01:00:00Z', field_provenance: { title: 'agent' },
        }],
      },
    })

    const link = wrapper.get('a')
    expect(wrapper.text()).toContain('知识网站，不是论文')
    expect(wrapper.text()).toContain('docs.example.org')
    expect(wrapper.text()).not.toContain('保存到论文库')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toContain('noopener')
  })

  it('does not make an unverified or non-HTTPS resource clickable', () => {
    const wrapper = mount(KnowledgeResourceList, {
      props: {
        resources: [{
          id: 'resource-unsafe', title: 'Unverified mirror',
          canonical_url: 'http://example.org/mirror',
          final_url: 'http://example.org/mirror', domain: 'example.org',
          resource_type: 'review', reason: '尚未通过安全核验', publisher: null,
          verification_status: 'pending_verification', http_status: null,
          accessed_at: null, field_provenance: {},
        }],
      },
    })

    expect(wrapper.text()).toContain('Unverified mirror')
    expect(wrapper.find('a').exists()).toBe(false)
  })
})
