import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ReaderStorageSettings from './ReaderStorageSettings.vue'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('ReaderStorageSettings', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it('keeps PDF cache cleanup separate and owns no legacy OCR download path', async () => {
    const fetcher = vi.fn(async () => {
      throw new Error('No request is expected before an explicit preview')
    })
    vi.stubGlobal('fetch', fetcher)

    const wrapper = mount(ReaderStorageSettings)
    await flushPromises()

    expect(wrapper.text()).toContain('论文全文缓存')
    expect(wrapper.text()).toContain('清理可移除的全文缓存')
    expect(wrapper.text()).not.toContain('下载并校验 OCR 模型')
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('previews protected and removable files before applying exactly that cleanup', async () => {
    const token = 'a'.repeat(64)
    const requests: Array<{ url: string; method: string; body?: unknown }> = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
      requests.push({ url, method, body })
      if (url === '/api/v1/library/cache-cleanup/preview' && method === 'POST') {
        expect(body).toEqual({ retention_days: 90 })
        return response({
          preview_token: token,
          generated_at: '2026-07-13T08:00:00Z',
          retention_days: 90,
          eligible_count: 1,
          protected_count: 1,
          reclaimable_bytes: 4_194_304,
          candidates: [
            {
              content_sha256: 'b'.repeat(64),
              byte_size: 4_194_304,
              work_ids: ['work-removable'],
              age_days: 120,
              protected_reasons: [],
              eligible: true,
            },
            {
              content_sha256: 'c'.repeat(64),
              byte_size: 8_388_608,
              work_ids: ['work-protected'],
              age_days: 160,
              protected_reasons: ['favorite', 'annotation'],
              eligible: false,
            },
          ],
        })
      }
      if (url === '/api/v1/library/cache-cleanup/apply' && method === 'POST') {
        expect(body).toEqual({ preview_token: token })
        return response({
          preview_token: token,
          deleted_objects: 1,
          cleared_document_versions: 1,
          missing_objects: 0,
          reclaimed_bytes: 4_194_304,
          cancelled: false,
        })
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)

    const wrapper = mount(ReaderStorageSettings)
    await flushPromises()
    expect(wrapper.find('[data-test="apply-cleanup"]').exists()).toBe(false)

    await wrapper.get('[data-test="preview-cleanup"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('1 个可移除')
    expect(wrapper.text()).toContain('1 个受保护')
    expect(wrapper.text()).toContain('可回收 4.0 MB')
    expect(wrapper.get('[data-test="cleanup-removable"]').text()).toContain('可以删除')
    expect(wrapper.get('[data-test="cleanup-protected"]').text()).toContain('收藏、带标注')

    await wrapper.get('[data-test="apply-cleanup"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('已删除 1 个缓存文件，释放 4.0 MB')
    expect(requests.filter((request) => request.url.endsWith('/apply'))).toHaveLength(1)
  })

  it('uses a safe message when an operational response contains a private path', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/v1/library/cache-cleanup/preview' && method === 'POST') {
        return response({ detail: 'D:\\private\\papers\\secret.pdf' }, 409)
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)

    const wrapper = mount(ReaderStorageSettings)
    await flushPromises()
    await wrapper.get('[data-test="preview-cleanup"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('无法生成安全的清理预览')
    expect(wrapper.text()).not.toContain('secret.pdf')
  })
})
