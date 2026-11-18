import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import RelatedPaperList from './RelatedPaperList.vue'

describe('RelatedPaperList', () => {
  it('renders verified paper identity, relation evidence and library state', async () => {
    const paper = {
      id: 'candidate-1', connector_id: 'arxiv', external_id: '2608.00001',
      normalized_doi: '10.1234/example', title: 'Verified Diffusion Agent',
      authors: ['Alice', 'Bo'], publication_year: 2026, reason: '匹配扩散模型与规划智能体',
      match_evidence: {}, confidence: 87, verification_status: 'verified',
      linked_work_id: 'work-related', merge_review_id: null,
      field_provenance: { title: 'arxiv:title' }, saved: false,
    }
    const wrapper = mount(RelatedPaperList, { props: { papers: [paper], savingId: '' } })

    expect(wrapper.text()).toContain('Verified Diffusion Agent')
    expect(wrapper.text()).toContain('87%')
    expect(wrapper.text()).toContain('10.1234/example')
    expect(wrapper.text()).toContain('已核验')
    await wrapper.get('[data-test="save-related-candidate-1"]').trigger('click')
    expect(wrapper.emitted('save')).toEqual([['candidate-1']])
  })
})
