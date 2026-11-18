import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import TaskModeSelector from './TaskModeSelector.vue'

describe('TaskModeSelector', () => {
  it('switches all four reader tasks with roving keyboard focus', async () => {
    let update: ((value: 'ask' | 'explain' | 'translate' | 'related') => void) | undefined
    const wrapper = mount(TaskModeSelector, {
      attachTo: document.body,
      props: {
        modelValue: 'ask',
        'onUpdate:modelValue': (value) => update?.(value),
      },
    })
    update = (value) => { void wrapper.setProps({ modelValue: value }) }
    const ask = wrapper.get<HTMLButtonElement>('[data-test="assistant-mode-ask"]')
    const explain = wrapper.get<HTMLButtonElement>('[data-test="assistant-mode-explain"]')
    const translate = wrapper.get<HTMLButtonElement>('[data-mode="translate"]')
    const related = wrapper.get<HTMLButtonElement>('[data-mode="related"]')

    ask.element.focus()
    await ask.trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(document.activeElement).toBe(explain.element)
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['explain'])

    await explain.trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(document.activeElement).toBe(translate.element)
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['translate'])

    await translate.trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(document.activeElement).toBe(related.element)
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['related'])
    wrapper.unmount()
  })
})
