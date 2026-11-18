import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AssistantComposer from './AssistantComposer.vue'

describe('AssistantComposer', () => {
  it('keeps the draft visible and submits once through the keyboard shortcut', async () => {
    const wrapper = mount(AssistantComposer, {
      props: {
        modelValue: '',
        mode: 'ask',
        canSend: true,
        active: false,
        submitting: false,
        statusMessage: '',
      },
    })
    const input = wrapper.get('[data-test="assistant-input"]')
    await input.setValue('作者如何验证这个结论？')
    await input.trigger('keydown', { key: 'Enter', ctrlKey: true })

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['作者如何验证这个结论？'])
    expect(wrapper.emitted('send')).toHaveLength(1)
  })
})
