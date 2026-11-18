import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SearchPage from './SearchPage.vue'

const result = {
  id: 'work-1', title: 'Reliable Agents', authors: ['Alice Chen'],
  abstract: 'Evidence first.', doi: '10.1000/reader', published_at: '2026-07-01T00:00:00Z',
  added_via: 'doi', added_at: '2026-07-02T00:00:00Z', favorite: false, feedback: 'neutral',
  pinned: false, reading_progress: 0, analysis_state: 'not_analyzed', document_count: 0,
  representative_document: null,
}

describe('SearchPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends the natural-language query and explainable filters', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input))
      const available = { ...result, id: 'another-work', representative_document: { id: 'paper-v2', available: true } }
      return new Response(JSON.stringify({ items: [result, available], total: 2, limit: 20, offset: 0 }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    }))
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/search', component: SearchPage }] })
    await router.push('/search')
    await router.isReady()
    const wrapper = mount(SearchPage, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.get('#search-query').setValue('long context')
    await wrapper.get('select').setValue('arxiv')
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(calls.at(-1)).toContain('query=long+context')
    expect(calls.at(-1)).toContain('source=arxiv')
    expect(calls.at(-1)).toContain('favorite=true')
    expect(wrapper.text()).toContain('Reliable Agents')
    expect(wrapper.findAll('article a').map((link) => link.attributes('href'))).toEqual([
      '/library?work=work-1', '/library/another-work/read/paper-v2',
    ])
  })
})
