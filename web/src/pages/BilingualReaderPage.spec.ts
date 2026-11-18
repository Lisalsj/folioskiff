import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import BilingualReaderPage from './BilingualReaderPage.vue'

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('BilingualReaderPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders immutable segment alignment and preserves view state in the URL', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/v1/translations/translation-1') {
        return json({
          id: 'translation-1',
          work_id: 'work-1',
          version: 1,
          document_version_id: 'document-1',
          document_content_sha256: 'a'.repeat(64),
          context_snapshot_id: 'context-1',
          task_id: 'task-1',
          job_id: 'job-1',
          source_language: 'en',
          target_language: 'zh-CN',
          scope: 'full_document',
          status: 'succeeded',
          evidence_level: 'native_text',
          prompt_version: 'translation-v1',
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-01T00:01:00Z',
          completed_at: '2026-08-01T00:01:00Z',
          terms: [],
        })
      }
      if (url === '/api/v1/translations/translation-1/segments') {
        return json([{
          segment_id: 'segment-000001',
          segment_index: 1,
          page_number: 1,
          block_numbers: [1],
          source_text: 'Diffusion model',
          source_sha256: 'b'.repeat(64),
          translated_text: '扩散模型',
          source_kind: 'native_text',
          ocr_confidence: null,
          status: 'succeeded',
          diagnostic: null,
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-01T00:01:00Z',
        }, {
          segment_id: 'segment-000002',
          segment_index: 2,
          page_number: 2,
          block_numbers: [1],
          source_text: 'Related work',
          source_sha256: 'c'.repeat(64),
          translated_text: '相关工作',
          source_kind: 'native_text',
          ocr_confidence: null,
          status: 'succeeded',
          diagnostic: null,
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-01T00:01:00Z',
        }])
      }
      if (url === '/api/v1/assistant/tasks/task-1') {
        return json({ backend_id: 'codex_cli', requested_model_id: 'gpt-test' })
      }
      if (url === '/api/v1/jobs') return json([])
      throw new Error(`Unexpected request ${url}`)
    }))

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{
        path: '/library/:workId/translations/:artifactId',
        component: BilingualReaderPage,
      }, {
        path: '/library/:workId/read/:documentVersionId',
        name: 'reader-workspace',
        component: { template: '<p>reader</p>' },
      }],
    })
    await router.push('/library/work-1/translations/translation-1?mode=parallel&page=1')
    await router.isReady()
    const wrapper = mount(BilingualReaderPage, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    expect(wrapper.get('[data-segment="segment-000001"] [data-side="source"]').text())
      .toContain('Diffusion model')
    expect(wrapper.get('[data-segment="segment-000001"] [data-side="target"]').text())
      .toContain('扩散模型')
    expect(wrapper.get('[data-segment="segment-000001"] .segment-anchor').text())
      .toBe('段落 1')
    expect(wrapper.find('main').exists()).toBe(false)
    expect(wrapper.get('[aria-label="双语译文内容"]').element.tagName).toBe('SECTION')
    await wrapper.get('[data-test="translation-mode-target"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.mode).toBe('target')
    expect(router.currentRoute.value.query.page).toBe('1')

    await router.replace({ query: { mode: 'target', page: '2', segment: 'segment-000002' } })
    await flushPromises()
    expect(wrapper.find('[data-segment="segment-000001"]').exists()).toBe(false)
    expect(wrapper.get('[data-segment="segment-000002"] [data-side="target"]').text())
      .toContain('相关工作')
  })
})
