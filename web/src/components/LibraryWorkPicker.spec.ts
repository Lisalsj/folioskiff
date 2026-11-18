import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import LibraryWorkPicker from './LibraryWorkPicker.vue'

function result(ids: string[], total = ids.length, offset = 0): Response {
  return Response.json({
    items: ids.map((id) => ({ id, title: id, authors: [] })),
    total, limit: 20, offset,
  })
}

describe('LibraryWorkPicker', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('ignores a late response after another query and retries the failed query', async () => {
    let finishInitial!: (value: Response) => void
    const fetch = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { finishInitial = resolve }))
      .mockResolvedValueOnce(new Response('', { status: 503 }))
      .mockResolvedValueOnce(result(['matched']))
    vi.stubGlobal('fetch', fetch)
    const wrapper = mount(LibraryWorkPicker, {
      props: { selectedIds: [], maximum: 50, optionTestId: (work) => work.id },
    })
    await wrapper.get('input[type="search"]').setValue('model')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('重试')
    finishInitial(result(['outdated']))
    await flushPromises()
    expect(wrapper.find('[data-test="outdated"]').exists()).toBe(false)
    await wrapper.get('[role="alert"] button').trigger('click')
    await flushPromises()
    expect(fetch.mock.calls[2]![0]).toBe(fetch.mock.calls[1]![0])
    expect(wrapper.find('[data-test="matched"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('keeps selected rows available for removal after reaching the selection limit', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(result(['chosen', 'another'])))
    const wrapper = mount(LibraryWorkPicker, {
      props: { selectedIds: ['chosen'], maximum: 1, optionTestId: (work) => work.id },
    })
    await flushPromises()
    expect(wrapper.get<HTMLInputElement>('[data-test="another"]').element.disabled).toBe(true)
    await wrapper.get('[data-test="chosen"]').setValue(false)
    expect(wrapper.emitted('toggle')).toEqual([[expect.objectContaining({ id: 'chosen' }), false]])
    wrapper.unmount()
  })
})
