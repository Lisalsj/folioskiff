import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import GlossaryEditor from './GlossaryEditor.vue'

describe('GlossaryEditor', () => {
  it('emits an explicit locked terminology update', async () => {
    const wrapper = mount(GlossaryEditor, {
      props: {
        terms: [{
          id: 'term-1',
          artifact_id: 'translation-1',
          normalized_source_term: 'diffusion model',
          source_term: 'Diffusion model',
          target_term: '扩散模型',
          origin: 'agent',
          locked: false,
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-01T00:00:00Z',
        }],
        savingTermId: '',
        immutable: false,
      },
    })
    await wrapper.get('[data-test="term-target-term-1"]').setValue('扩散生成模型')
    await wrapper.get('[data-test="term-lock-term-1"]').setValue(true)
    await wrapper.get('[data-test="term-save-term-1"]').trigger('click')
    expect(wrapper.emitted('updateTerm')).toEqual([['term-1', '扩散生成模型', true]])
    expect(wrapper.text()).toContain('保存后受影响段落会标记为待重新翻译')
  })
})
