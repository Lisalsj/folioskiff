import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import RelatedIntentEditor from './RelatedIntentEditor.vue'

const intent = {
  set_id: 'set-1',
  summary: '扩散规划方法',
  required_groups: [['diffusion model', '扩散模型'], ['planning agent']],
  optional_terms: ['benchmark'],
  excluded_terms: ['survey'],
  recent_months: 24,
  source_ids: ['arxiv', 'openreview'],
  revision: 1,
  confirmed: false,
  confirmed_at: null,
  diff: {
    added_terms: ['planning agent'], removed_terms: [], moved_terms: [],
    added_source_ids: [], removed_source_ids: [], summary_changed: false,
    recent_months_changed: false,
  },
}

describe('RelatedIntentEditor', () => {
  it('edits bilingual terms, regrouping, sources and month range', async () => {
    const wrapper = mount(RelatedIntentEditor, { props: { modelValue: intent } })

    expect(wrapper.get('[data-test="intent-editor"]').text()).toContain('必需概念组')
    expect(wrapper.get('[data-test="intent-diff"]').text()).toContain('planning agent')
    await wrapper.get('[data-test="term-required-0-0"]').setValue('diffusion policy')
    await wrapper.get('[data-test="move-required-0-1"]').setValue('optional')
    await wrapper.get('[data-test="add-optional-input"]').setValue('规划评测')
    await wrapper.get('[data-test="add-optional"]').trigger('click')
    await wrapper.get('[data-test="source-ieee"]').setValue(true)
    await wrapper.get('[data-test="recent-months"]').setValue('18')

    const updates = wrapper.emitted('update:modelValue') as Array<[typeof intent]>
    expect(updates.length).toBeGreaterThan(4)
    expect(updates.at(-1)?.[0].recent_months).toBe(18)
    expect(updates.some(([value]) => value.optional_terms.includes('扩散模型'))).toBe(true)
    expect(updates.some(([value]) => value.optional_terms.includes('规划评测'))).toBe(true)
    expect(updates.some(([value]) => value.source_ids.includes('ieee'))).toBe(true)
  })
})
