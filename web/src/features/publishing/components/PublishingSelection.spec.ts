import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PublishingSelection from './PublishingSelection.vue'

const work = {
  id: 'work-1', title: 'Public paper', authors: ['Lin'], abstract: 'Public abstract', doi: null,
  published_at: null, added_via: 'reference', added_at: '2026-08-01T00:00:00Z', favorite: true,
  feedback: 'liked', pinned: false, reading_progress: 50, analysis_state: 'available',
  document_count: 1, representative_document: null,
}

const earlierWork = { ...work, id: 'work-0', title: 'Earlier paper' }

describe('PublishingSelection', () => {
  it('states the private exclusions and emits ordered publication choices', async () => {
    const wrapper = mount(PublishingSelection, {
      props: {
        maximum: 100, selectedIds: ['work-0', 'work-1'], selectedWorks: [earlierWork, work], reportIds: {},
        reportsByWork: {}, reportLoading: {}, reportErrors: {},
      },
      global: { stubs: { LibraryWorkPicker: true } },
    })
    expect(wrapper.text()).toContain('批注、聊天、失败草稿和完整提示词没有发布入口')
    await wrapper.get('[data-test="move-publishing-work-1-up"]').trigger('click')
    expect(wrapper.emitted('move')?.[0]).toEqual(['work-1', -1])
    await wrapper.get('[aria-label="移除《Public paper》"]').trigger('click')
    expect(wrapper.emitted('toggle')?.[0]).toEqual([work, false])
  })
})
