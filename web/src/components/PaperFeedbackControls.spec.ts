import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PaperFeedbackControls from './PaperFeedbackControls.vue'

const initialState = {
  work_id: 'work-1',
  favorite: false,
  feedback: 'neutral',
  feedback_reasons: [],
  pinned: true,
  reading_progress: 37,
  analysis_state: 'not_analyzed',
  persisted: false,
  created_at: null,
  updated_at: null,
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('PaperFeedbackControls', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('persists the complete server fact before announcing save and favorite changes', async () => {
    const requests: Array<Record<string, unknown>> = []
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>
      requests.push(body)
      return json({
        ...initialState,
        ...body,
        persisted: true,
        historical_match: false,
        created_at: '2026-07-15T05:00:00Z',
        updated_at: '2026-07-15T05:00:00Z',
      })
    }))
    const wrapper = mount(PaperFeedbackControls, {
      props: { workId: 'work-1', state: initialState },
    })

    await wrapper.get('[data-test="save-work"]').trigger('click')
    await flushPromises()
    expect(requests[0]).toEqual({
      favorite: false,
      feedback: 'neutral',
      feedback_reasons: [],
      pinned: true,
      reading_progress: 37,
      analysis_state: 'not_analyzed',
    })
    expect(wrapper.get('[data-test="feedback-status"]').text()).toContain('已保存到论文库')
    expect(wrapper.emitted('updated')?.[0]?.[0]).toMatchObject({ persisted: true })

    await wrapper.get('[data-test="favorite-work"]').trigger('click')
    await flushPromises()
    expect(requests[1]).toMatchObject({ favorite: true, pinned: true, reading_progress: 37 })
    expect(wrapper.get('[data-test="favorite-work"]').attributes('aria-pressed')).toBe('true')
  })

  it('saves an explicit like or dislike with bounded reasons and keeps old facts on failure', async () => {
    let fail = false
    const requests: Array<Record<string, unknown>> = []
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>
      requests.push(body)
      if (fail) return json({ detail: 'write failed' }, 500)
      return json({
        ...initialState,
        ...body,
        persisted: true,
        historical_match: false,
        created_at: '2026-07-15T05:00:00Z',
        updated_at: '2026-07-15T05:01:00Z',
      })
    }))
    const wrapper = mount(PaperFeedbackControls, {
      props: { workId: 'work-1', state: initialState },
    })

    await wrapper.get('[data-test="feedback-dislike"]').trigger('click')
    expect(wrapper.get('[data-test="feedback-reasons"]').attributes('aria-label')).toContain(
      '不喜欢原因',
    )
    await wrapper.get('[data-test="feedback-reason-off_topic"]').setValue(true)
    await wrapper.get('[data-test="feedback-reason-weak_evidence"]').setValue(true)
    await wrapper.get('[data-test="save-feedback"]').trigger('click')
    await flushPromises()
    expect(requests[0]).toMatchObject({
      feedback: 'dislike',
      feedback_reasons: ['off_topic', 'weak_evidence'],
    })
    expect(wrapper.get('[data-test="feedback-dislike"]').attributes('aria-pressed')).toBe('true')

    fail = true
    await wrapper.get('[data-test="favorite-work"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('没有修改原有记录')
    expect(wrapper.get('[data-test="favorite-work"]').attributes('aria-pressed')).toBe('false')
  })
})
