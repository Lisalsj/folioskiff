import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ComparisonSelection from './ComparisonSelection.vue'

const works = Array.from({ length: 11 }, (_, index) => ({
  id: `work-${index + 1}`,
  title: `Paper ${index + 1}`,
  authors: [`Author ${index + 1}`],
  abstract: null,
  doi: null,
  published_at: null,
  added_via: 'reference',
  added_at: '2026-08-01T00:00:00Z',
  favorite: false,
  feedback: 'neutral',
  pinned: false,
  reading_progress: 0,
  analysis_state: 'not_analyzed',
  document_count: 0,
  representative_document: null,
}))

describe('ComparisonSelection', () => {
  it('shows empty and staged paths while emitting only selection intents', async () => {
    const wrapper = mount(ComparisonSelection, {
      props: { selectedWorks: [], selectedIds: [], maximum: 50 },
      global: { stubs: { LibraryWorkPicker: true } },
    })
    expect(wrapper.text()).toContain('尚未选择论文')
    await wrapper.setProps({ selectedWorks: works, selectedIds: works.map((work) => work.id) })
    expect(wrapper.get('[data-test="comparison-mode-note"]').text()).toContain('逐篇提取证据')
    await wrapper.get('[data-test="move-work-2-up"]').trigger('click')
    expect(wrapper.emitted('move')?.[0]).toEqual(['work-2', -1])
  })
})
