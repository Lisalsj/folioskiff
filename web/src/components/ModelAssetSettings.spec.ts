import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ModelAssetSettings from './ModelAssetSettings.vue'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const semanticAsset = {
  asset_id: 'asset-e5-current',
  capability: 'semantic',
  bundle_id: 'multilingual-e5-small',
  display_name: 'Multilingual E5 Small',
  purpose: '为中英文研究意图与论文摘要建立语义相似度；缺失时继续使用词法检索。',
  version: '1.0.0',
  revision: 'a'.repeat(40),
  current_release: true,
  download_size_bytes: 139_460_608,
  license_name: 'MIT',
  license_url: 'https://opensource.org/license/mit/?private=hidden#terms',
  copyright_notice: 'Microsoft and contributors',
  source_url: 'https://huggingface.co/intfloat/multilingual-e5-small?download=true#files',
  status: 'missing',
  integrity: 'missing',
  active: false,
  runtime_available: true,
  available: false,
  degradation_reasons: ['model_missing'],
}

const ocrAsset = {
  asset_id: 'asset-ocr-current',
  capability: 'ocr',
  bundle_id: 'rapidocr-ppocrv5',
  display_name: 'RapidOCR 中英文移动版',
  purpose: '只识别疑似扫描页；普通文字版 PDF 不调用 OCR。',
  version: '5.0.0',
  revision: 'b'.repeat(40),
  current_release: true,
  download_size_bytes: 22_469_390,
  license_name: 'Apache-2.0',
  license_url: 'https://www.apache.org/licenses/LICENSE-2.0',
  copyright_notice: 'Baidu and RapidOCR contributors',
  source_url: 'https://www.modelscope.cn/models/RapidAI/RapidOCR',
  status: 'ready',
  integrity: 'verified',
  active: true,
  runtime_available: true,
  available: true,
  degradation_reasons: [],
}

function mountSettings() {
  return mount(ModelAssetSettings, { attachTo: document.body })
}

describe('ModelAssetSettings', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('loads only public preflight facts and never downloads on first render', async () => {
    const requests: Array<{ method: string; url: string }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const url = String(input)
      requests.push({ method, url })
      if (url === '/api/v1/settings/model-assets' && method === 'GET') {
        return response([semanticAsset, ocrAsset])
      }
      if (url === '/api/v1/settings/model-assets/jobs' && method === 'GET') return response([])
      throw new Error(`Unexpected request: ${method} ${url}`)
    }))

    const wrapper = mountSettings()
    await flushPromises()

    expect(wrapper.text()).toContain('本地 AI 模型')
    expect(wrapper.text()).toContain('133.0 MB')
    expect(wrapper.text()).toContain(semanticAsset.purpose)
    expect(wrapper.text()).toContain('当前继续使用词法检索')
    expect(wrapper.text()).toContain('已启用并可使用')
    expect(wrapper.get('[data-test="license-asset-e5-current"]').attributes('href'))
      .toBe('https://opensource.org/license/mit/')
    expect(wrapper.get('[data-test="source-asset-e5-current"]').attributes('href'))
      .toBe('https://huggingface.co/intfloat/multilingual-e5-small')
    expect(requests.every((item) => item.method === 'GET')).toBe(true)
  })

  it('requires an explicit size and license confirmation before creating an install job', async () => {
    let started = false
    const requests: Array<{ method: string; url: string; key?: string | null }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const url = String(input)
      requests.push({
        method,
        url,
        key: new Headers(init?.headers).get('Idempotency-Key'),
      })
      if (url === '/api/v1/settings/model-assets' && method === 'GET') {
        return response([semanticAsset])
      }
      if (url === '/api/v1/settings/model-assets/jobs' && method === 'GET') {
        return response(started ? [{
          id: 'job-install-e5',
          kind: 'models.install',
          status: 'queued',
          progress: 0,
          cancel_requested: false,
          pause_requested: false,
          asset_id: semanticAsset.asset_id,
          retryable: null,
          error_code: null,
          created_at: '2026-07-14T08:00:00Z',
          updated_at: '2026-07-14T08:00:00Z',
        }] : [])
      }
      if (
        url === '/api/v1/settings/model-assets/multilingual-e5-small/install' &&
        method === 'POST'
      ) {
        started = true
        return response({
          id: 'job-install-e5',
          kind: 'models.install',
          status: 'queued',
          progress: 0,
          cancel_requested: false,
          pause_requested: false,
        }, 202)
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }))

    const wrapper = mountSettings()
    await flushPromises()
    await wrapper.get('[data-test="install-asset-e5-current"]').trigger('click')
    await flushPromises()

    expect(document.querySelector('[data-test="model-install-dialog"]')?.textContent)
      .toContain('133.0 MB')
    expect(document.querySelector('[data-test="model-install-dialog"]')?.textContent)
      .toContain('MIT')
    expect(requests.some((item) => item.method === 'POST')).toBe(false)

    document.querySelector<HTMLButtonElement>('[data-test="confirm-model-install"]')?.click()
    await flushPromises()

    const install = requests.find((item) => item.method === 'POST')
    expect(install?.url).toBe('/api/v1/settings/model-assets/multilingual-e5-small/install')
    expect(install?.key).toMatch(/^model-install-/)
    expect(wrapper.text()).toContain('等待下载')
  })

  it('restores a durable model job and uses server progress for pause resume and cancel', async () => {
    let status = 'running'
    let progress = 37
    let pauseRequested = false
    let cancelRequested = false
    const commands: string[] = []
    const partial = {
      ...semanticAsset,
      status: 'downloading',
      integrity: 'partial',
      degradation_reasons: ['model_download_incomplete'],
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const url = String(input)
      if (url === '/api/v1/settings/model-assets' && method === 'GET') return response([partial])
      if (url === '/api/v1/settings/model-assets/jobs' && method === 'GET') {
        return response([{
          id: 'job-durable', kind: 'models.install', status, progress,
          cancel_requested: cancelRequested, pause_requested: pauseRequested,
          asset_id: partial.asset_id, retryable: null,
          error_code: null,
          created_at: '2026-07-14T08:00:00Z', updated_at: '2026-07-14T08:01:00Z',
        }])
      }
      if (url.endsWith('/jobs/job-durable/pause') && method === 'POST') {
        commands.push('pause')
        status = 'paused'; pauseRequested = true
        return response({ id: 'job-durable', kind: 'models.install', status, progress,
          cancel_requested: false, pause_requested: true })
      }
      if (url.endsWith('/jobs/job-durable/resume') && method === 'POST') {
        commands.push('resume')
        status = 'running'; pauseRequested = false; progress = 41
        return response({ id: 'job-durable', kind: 'models.install', status, progress,
          cancel_requested: false, pause_requested: false }, 202)
      }
      if (url.endsWith('/jobs/job-durable/cancel') && method === 'POST') {
        commands.push('cancel')
        status = 'cancelled'; cancelRequested = true
        return response({ id: 'job-durable', kind: 'models.install', status, progress,
          cancel_requested: true, pause_requested: false })
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }))

    const wrapper = mountSettings()
    await flushPromises()
    expect(wrapper.text()).toContain('37%')

    await wrapper.get('[data-test="pause-job-durable"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已暂停 · 37%')

    await wrapper.get('[data-test="resume-job-durable"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('进行中 · 41%')

    await wrapper.get('[data-test="cancel-job-durable"]').trigger('click')
    await flushPromises()
    expect(commands).toEqual(['pause', 'resume', 'cancel'])
    expect(wrapper.text()).toContain('已取消')
    expect(wrapper.find('[data-test="install-asset-e5-current"]').exists()).toBe(true)
  })

  it('maps failure codes to stable Chinese guidance and never renders backend diagnostics', async () => {
    const privateCanary = 'C:\\Users\\reader\\models\\private.onnx?token=secret\nTraceback'
    let retried = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const url = String(input)
      if (url === '/api/v1/settings/model-assets' && method === 'GET') {
        return response([{ ...semanticAsset, status: 'failed', integrity: 'partial' }])
      }
      if (url === '/api/v1/settings/model-assets/jobs' && method === 'GET') {
        return response([{
          id: 'job-failed', kind: 'models.install', status: retried ? 'queued' : 'failed',
          progress: 54, cancel_requested: false, pause_requested: false,
          asset_id: semanticAsset.asset_id, retryable: true, error_code: 'disk_full',
          created_at: '2026-07-14T08:00:00Z', updated_at: '2026-07-14T08:01:00Z',
        }])
      }
      if (url.endsWith('/jobs/job-failed/resume') && method === 'POST') {
        retried = true
        return response({
          id: 'job-failed', kind: 'models.install', status: 'queued', progress: 54,
          cancel_requested: false, pause_requested: false,
        }, 202)
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }))

    const wrapper = mountSettings()
    await flushPromises()

    expect(wrapper.text()).toContain('可用空间不足')
    expect(wrapper.text()).not.toContain(privateCanary)
    expect(wrapper.text()).not.toContain('Traceback')
    expect(wrapper.text()).not.toContain('token=secret')

    await wrapper.get('[data-test="retry-job-failed"]').trigger('click')
    await flushPromises()
    expect(retried).toBe(true)
    expect(wrapper.text()).toContain('等待下载')
  })

  it('offers a fresh confirmed install after a non-retryable hash mismatch', async () => {
    let installed = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const url = String(input)
      if (url === '/api/v1/settings/model-assets' && method === 'GET') {
        return response([{ ...semanticAsset, status: 'failed', integrity: 'invalid' }])
      }
      if (url === '/api/v1/settings/model-assets/jobs' && method === 'GET') {
        return response([{
          id: 'job-hash', kind: 'models.install', status: 'failed', progress: 72,
          cancel_requested: false, pause_requested: false,
          asset_id: semanticAsset.asset_id, retryable: false, error_code: 'hash_mismatch',
          created_at: '2026-07-14T08:00:00Z', updated_at: '2026-07-14T08:01:00Z',
        }])
      }
      if (url.endsWith('/multilingual-e5-small/install') && method === 'POST') {
        installed = true
        return response({
          id: 'job-fresh', kind: 'models.install', status: 'queued', progress: 0,
          cancel_requested: false, pause_requested: false,
        }, 202)
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }))

    const wrapper = mountSettings()
    await flushPromises()
    expect(wrapper.text()).toContain('未通过 SHA-256 校验')
    await wrapper.get('[data-test="reinstall-job-hash"]').trigger('click')
    await flushPromises()
    expect(document.querySelector('[data-test="model-install-dialog"]')).not.toBeNull()
    expect(installed).toBe(false)

    document.querySelector<HTMLButtonElement>('[data-test="confirm-model-install"]')?.click()
    await flushPromises()
    expect(installed).toBe(true)
  })

  it('previews reclaimable bytes and requires a second explicit action to delete an old revision', async () => {
    const old = {
      ...semanticAsset,
      asset_id: 'asset-e5-old',
      version: '0.9.0',
      revision: 'c'.repeat(40),
      current_release: false,
      status: 'ready',
      integrity: 'verified',
      degradation_reasons: ['model_not_enabled'],
    }
    let deleted = false
    const methods: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const url = String(input)
      methods.push(`${method} ${url}`)
      if (url === '/api/v1/settings/model-assets' && method === 'GET') {
        return response(deleted ? [ocrAsset] : [ocrAsset, old])
      }
      if (url === '/api/v1/settings/model-assets/jobs' && method === 'GET') return response([])
      if (url.endsWith('/deletion-preview') && method === 'GET') {
        return response({
          asset_id: old.asset_id,
          bundle_id: old.bundle_id,
          revision: old.revision,
          reclaimable_bytes: 104_857_600,
          blocking_references: [],
          can_delete: true,
        })
      }
      if (
        url === `/api/v1/settings/model-assets/${old.bundle_id}/revisions/${old.revision}` &&
        method === 'DELETE'
      ) {
        deleted = true
        return response({
          asset_id: old.asset_id, bundle_id: old.bundle_id, revision: old.revision,
          reclaimable_bytes: 104_857_600, blocking_references: [], can_delete: true,
        })
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }))

    const wrapper = mountSettings()
    await flushPromises()
    await wrapper.get('[data-test="preview-delete-asset-e5-old"]').trigger('click')
    await flushPromises()

    expect(document.querySelector('[data-test="model-delete-dialog"]')?.textContent)
      .toContain('100.0 MB')
    expect(methods.some((item) => item.startsWith('DELETE'))).toBe(false)
    document.querySelector<HTMLButtonElement>('[data-test="confirm-model-delete"]')?.click()
    await flushPromises()

    expect(deleted).toBe(true)
    expect(wrapper.text()).toContain('旧版本已删除')
    expect(wrapper.find('[data-test="asset-asset-e5-old"]').exists()).toBe(false)
  })
})
