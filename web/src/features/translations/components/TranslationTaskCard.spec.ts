import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import TranslationTaskCard from './TranslationTaskCard.vue'

describe('TranslationTaskCard', () => {
  it('summarizes the evidence-bound task and opens its bilingual artifact', async () => {
    const wrapper = mount(TranslationTaskCard, {
      props: {
        artifactId: 'translation-1',
        sourceLanguage: 'en',
        targetLanguage: 'zh-CN',
        scope: 'full_document',
        evidenceLevel: 'native_text',
        agentLabel: 'codex_cli / gpt-test',
        status: 'succeeded',
        pageRange: '第 1 至 8 页',
        completed: 8,
        total: 8,
        percent: 100,
        active: false,
        cancelRequested: false,
        diagnostics: [],
      },
    })
    expect(wrapper.get('[data-test="language-pair"]').text()).toContain('英文')
    expect(wrapper.text()).toContain('codex_cli / gpt-test')
    expect(wrapper.text()).toContain('全文')
    await wrapper.get('[data-test="open-bilingual"]').trigger('click')
    expect(wrapper.emitted('open')).toEqual([['translation-1']])
  })
})
