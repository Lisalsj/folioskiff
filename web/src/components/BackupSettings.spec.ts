import { createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import BackupSettings from './BackupSettings.vue'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const policy = {
  automatic_interval_days: 7,
  automatic_retention: 4,
  check_interval_hours: 6,
  latest_automatic_at: '2026-07-13T08:00:00Z',
  next_automatic_at: '2026-07-20T08:00:00Z',
}

function job(
  id: string,
  kind: string,
  status: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    id,
    kind,
    status,
    progress: status === 'succeeded' || status === 'pending_restart' ? 100 : 0,
    payload: {},
    checkpoint: null,
    result: null,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    created_at: '2026-07-14T08:00:00Z',
    updated_at: '2026-07-14T08:00:00Z',
    ...overrides,
  }
}

function mountSettings() {
  return mount(BackupSettings, {
    attachTo: document.body,
    global: { plugins: [createPinia()] },
  })
}

describe('BackupSettings', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it('shows the weekly policy and exports an optional-password backup without retaining secrets', async () => {
    const requests: Array<{ url: string; method: string; body?: unknown; headers: Headers }> = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const headers = new Headers(init?.headers)
      const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
      requests.push({ url, method, body, headers })
      if (url === '/api/v1/backups/policy' && method === 'GET') return response(policy)
      if (url === '/api/v1/backups' && method === 'GET') return response([])
      if (url === '/api/v1/backups' && method === 'POST') {
        expect(body).toEqual({
          kind: 'manual',
          encrypted: true,
          password: 'portable library secret',
        })
        expect(headers.get('Idempotency-Key')).toMatch(/^manual-backup-/)
        return response(
          job('backup-job', 'backup.create', 'succeeded', {
            result: {
              backup_id: 'backup-download-id',
              encrypted: true,
              byte_size: 12_582_912,
            },
          }),
          202,
        )
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)

    const wrapper = mountSettings()
    await flushPromises()

    expect(wrapper.text()).toContain('每 7 天')
    expect(wrapper.text()).toContain('最近 4 份')
    expect(wrapper.text()).toContain('2026年7月13日')
    const privacyBoundary = wrapper.get('[data-test="backup-privacy-boundary"]')
    expect(privacyBoundary.text()).toContain('提取文本和 OCR 全文')
    expect(privacyBoundary.text()).toContain('阅读进度、批注、分析报告、完整提示词、论文聊天和偏好')
    expect(privacyBoundary.text()).toContain('脱敏失败诊断')
    expect(privacyBoundary.text()).toContain('系统凭据、日志、模型、缓存、临时文件和旧备份')
    expect(wrapper.get('[data-test="backup-encryption-notice"]').text()).toContain(
      '任何拿到文件的人都能读取其中的私密研究资料',
    )

    await wrapper.get('[data-test="encrypt-backup"]').setValue(true)
    expect(wrapper.get('[data-test="backup-encryption-notice"]').text()).toContain(
      '将使用你输入的密码加密',
    )
    const password = wrapper.get<HTMLInputElement>('[data-test="backup-password"]')
    const confirmation = wrapper.get<HTMLInputElement>('[data-test="backup-password-confirm"]')
    expect(password.attributes('autocomplete')).toBe('new-password')
    await password.setValue('portable library secret')
    await confirmation.setValue('portable library secret')
    await wrapper.get('[data-test="create-backup"]').trigger('submit')
    await flushPromises()

    expect(password.element.value).toBe('')
    expect(confirmation.element.value).toBe('')
    expect(wrapper.text()).toContain('便携备份已完成')
    expect(wrapper.get('[data-test="download-backup"]').attributes('href')).toBe(
      '/api/v1/backups/backup-download-id/download',
    )
    expect(JSON.stringify(requests)).not.toContain('paper_reader_csrf')
  })

  it('validates a restore before exposing the second confirmation and explains the exact scope', async () => {
    const fingerprint = 'f'.repeat(64)
    const requests: Array<{ url: string; method: string; body?: unknown }> = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      let body: unknown
      if (init?.body instanceof FormData) {
        const uploaded = init.body.get('package')
        body = {
          filename: uploaded instanceof File ? uploaded.name : null,
          password: init.body.get('password'),
        }
      } else if (typeof init?.body === 'string') {
        body = JSON.parse(init.body)
      }
      requests.push({ url, method, body })
      if (url === '/api/v1/backups/policy' && method === 'GET') return response(policy)
      if (url === '/api/v1/backups' && method === 'GET') return response([])
      if (url === '/api/v1/backups/restore-packages' && method === 'POST') {
        expect(body).toEqual({
          filename: 'portable.paper-reader-backup.enc',
          password: 'restore-only secret',
        })
        return response(
          job('restore-preview-id', 'backup.restore.validate', 'succeeded', {
            result: { preview_id: 'restore-preview-id' },
          }),
          202,
        )
      }
      if (url === '/api/v1/backups/restore-previews/restore-preview-id' && method === 'GET') {
        return response({
          id: 'restore-preview-id',
          validation_job_id: 'restore-preview-id',
          fingerprint,
          package_sha256: 'a'.repeat(64),
          manifest_sha256: 'b'.repeat(64),
          encrypted: true,
          source_format_version: 'paper-reader-backup-v1',
          source_app_version: '0.5.0',
          source_schema_revision: '0023_backup_archives',
          object_count: 3,
          payload_bytes: 18_874_368,
          included: ['SQLite database', 'referenced lawful PDF objects'],
          excluded: ['cache', 'credentials', 'existing backups', 'logs', 'models', 'temporary files'],
          created_at: '2026-07-14T08:00:00Z',
          expires_at: '2026-07-14T08:10:00Z',
        })
      }
      if (url === '/api/v1/backups/restores' && method === 'POST') {
        expect(body).toEqual({ preview_id: 'restore-preview-id', fingerprint })
        return response(
          job('restore-confirmation', 'backup.restore.confirm', 'pending_restart', {
            result: { pending_restart: true, restore_id: 'restore-id' },
          }),
          202,
        )
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)

    const wrapper = mountSettings()
    await flushPromises()
    const fileInput = wrapper.get<HTMLInputElement>('[data-test="restore-file"]')
    const file = new File(['encrypted backup'], 'portable.paper-reader-backup.enc', {
      type: 'application/octet-stream',
    })
    Object.defineProperty(fileInput.element, 'files', { configurable: true, value: [file] })
    await fileInput.trigger('change')
    const password = wrapper.get<HTMLInputElement>('[data-test="restore-password"]')
    expect(password.attributes('autocomplete')).toBe('current-password')
    await password.setValue('restore-only secret')
    await wrapper.get('[data-test="validate-restore"]').trigger('submit')
    await flushPromises()

    expect(password.element.value).toBe('')
    expect(fileInput.element.value).toBe('')
    expect(wrapper.text()).toContain('数据库与 3 个 PDF 对象')
    expect(wrapper.text()).toContain('提取文本和 OCR 全文')
    expect(wrapper.text()).toContain('阅读进度、批注、分析报告、完整提示词、论文聊天和偏好')
    expect(wrapper.text()).toContain('脱敏失败诊断')
    expect(wrapper.text()).toContain('来源版本 0.5.0')
    expect(wrapper.text()).toContain('凭据、日志、模型、缓存')
    expect(wrapper.text()).toContain('运行中的任务会标记为已中断')
    const previewHeading = wrapper.get('[data-test="restore-preview-heading"]')
    expect(document.activeElement).toBe(previewHeading.element)

    const confirm = wrapper.get<HTMLButtonElement>('[data-test="confirm-restore"]')
    expect(confirm.element.disabled).toBe(true)
    await wrapper.get('[data-test="restore-acknowledgement"]').setValue(true)
    expect(confirm.element.disabled).toBe(false)
    await confirm.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('关闭并重新打开 paper-reader')
    expect(wrapper.find('[data-test="confirm-restore"]').exists()).toBe(false)
    expect(requests.filter((request) => request.url.endsWith('/restores'))).toHaveLength(1)
  })

  it.each([
    ['invalid_password', '密码不正确'],
    ['invalid_package', '恢复包已损坏或不是有效的 paper-reader 备份'],
    ['unsupported_version', '恢复包需要更新版本的 paper-reader'],
  ])(
    'never exposes confirmation for %s and restores focus to file selection',
    async (errorCode, safeMessage) => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/v1/backups/policy' && method === 'GET') return response(policy)
      if (url === '/api/v1/backups' && method === 'GET') return response([])
      if (url === '/api/v1/backups/restore-packages' && method === 'POST') {
        return response(
          job('invalid-restore', 'backup.restore.validate', 'failed', {
            progress: 100,
            error_code: errorCode,
            error_message: 'D:\\private\\secret.package',
          }),
          202,
        )
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)

    const wrapper = mountSettings()
    await flushPromises()
    const fileInput = wrapper.get<HTMLInputElement>('[data-test="restore-file"]')
    const file = new File(['broken'], 'broken.package')
    Object.defineProperty(fileInput.element, 'files', { configurable: true, value: [file] })
    await fileInput.trigger('change')
    await wrapper.get('[data-test="validate-restore"]').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain(safeMessage)
    expect(wrapper.text()).not.toContain('secret.package')
    expect(wrapper.find('[data-test="confirm-restore"]').exists()).toBe(false)
    expect(document.activeElement).toBe(fileInput.element)
    },
  )

  it('shows a stable retryable failure without exposing backend diagnostics', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/v1/backups/policy' && method === 'GET') return response(policy)
      if (url === '/api/v1/backups' && method === 'GET') return response([])
      if (url === '/api/v1/backups' && method === 'POST') {
        return response(
          job('failed-backup', 'backup.create', 'failed', {
            progress: 100,
            error_code: 'insufficient_space',
            error_message: 'D:\\private\\backup-staging',
          }),
          202,
        )
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)

    const wrapper = mountSettings()
    await flushPromises()
    await wrapper.get('[data-test="create-backup"]').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('可用磁盘空间不足')
    expect(wrapper.text()).not.toContain('backup-staging')
    expect(wrapper.find('[data-test="download-backup"]').exists()).toBe(false)
  })

  it('shows durable progress and lets the user request cancellation', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/v1/backups/policy' && method === 'GET') return response(policy)
      if (url === '/api/v1/backups' && method === 'GET') return response([])
      if (url === '/api/v1/backups' && method === 'POST') {
        return response(job('running-backup', 'backup.create', 'running', { progress: 42 }), 202)
      }
      if (url === '/api/v1/jobs/running-backup/cancel' && method === 'POST') {
        return response({
          id: 'running-backup',
          kind: 'backup.create',
          status: 'running',
          progress: 42,
          cancel_requested: true,
        })
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)

    const wrapper = mountSettings()
    await flushPromises()
    await wrapper.get('[data-test="create-backup"]').trigger('submit')
    await flushPromises()

    expect(wrapper.get('progress').attributes('value')).toBe('42')
    await wrapper.get('[data-test="cancel-backup"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('正在取消')
  })
})
