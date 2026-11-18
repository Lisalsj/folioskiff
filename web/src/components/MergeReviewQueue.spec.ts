import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import MergeReviewQueue from './MergeReviewQueue.vue'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('MergeReviewQueue', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows both identities and records an explicit keep-separate decision', async () => {
    const decisions: unknown[] = []
    const review = {
      id: 'review-1',
      candidate_work: {
        id: 'candidate',
        title: 'Agentic Paper Reading',
        authors: ['Li Ming', 'Ada Chen'],
        doi: null,
        status: 'active',
        source_count: 1,
      },
      matched_work: {
        id: 'matched',
        title: 'Agentic Reading of Papers',
        authors: ['Ming Li', 'Ada Chen'],
        doi: '10.1000/example',
        status: 'active',
        source_count: 2,
      },
      similarity: 0.91,
      evidence: { title_similarity: 0.94, shared_authors: ['Ada Chen'] },
      status: 'pending',
      decision_note: null,
      decided_by: null,
      created_at: '2026-07-13T00:00:00Z',
      decided_at: null,
    }
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/v1/works/merge-reviews?status=pending' && method === 'GET') {
        return response([review])
      }
      if (url === '/api/v1/works/merge-reviews/review-1/decision' && method === 'POST') {
        const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
        decisions.push(body)
        return response({ ...review, status: 'kept_separate' })
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)

    const wrapper = mount(MergeReviewQueue)
    await flushPromises()

    expect(wrapper.text()).toContain('Agentic Paper Reading')
    expect(wrapper.text()).toContain('Agentic Reading of Papers')
    expect(wrapper.text()).toContain('91%')

    await wrapper.get('[data-test="note-review-1"]').setValue('作者顺序和 DOI 证据不足')
    await wrapper.get('[data-test="keep-review-1"]').trigger('click')
    await flushPromises()

    expect(decisions).toEqual([
      { decision: 'keep_separate', note: '作者顺序和 DOI 证据不足' },
    ])
    expect(wrapper.text()).not.toContain('Agentic Paper Reading')
    expect(wrapper.text()).toContain('暂无待确认的重复论文')
  })
})
