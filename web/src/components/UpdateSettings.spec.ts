import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import UpdateSettings from './UpdateSettings.vue'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const available = {
  current_version: '0.6.0',
  automatic_checks_enabled: false,
  status: 'update_available',
  checked: true,
  last_checked_at: '2026-07-14T09:30:00Z',
  next_automatic_check_at: null,
  error_code: null,
  release: {
    version: '0.8.0',
    tag_name: 'v0.8.0',
    published_at: '2026-07-14T08:00:00Z',
    html_url: 'https://github.com/Lisalsj/folioskiff/releases/tag/v0.8.0',
    changelog: '新增只读更新检查。\n修复离线状态提示。',
    migration_notes: '升级前建议创建便携备份；数据库将在启动时自动迁移。',
    capability_matrix: {
      windows_x64: 'supported',
      installer: 'supported',
    },
    assets: [
      { name: 'release-manifest.json', size_bytes: 100, sha256: 'a'.repeat(64) },
    ],
    unsigned: true,
  },
}

const initial = {
  ...available,
  status: 'never_checked',
  checked: false,
  last_checked_at: null,
  release: null,
}

describe('UpdateSettings', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it('loads cached public metadata without starting a remote check', async () => {
    const requests: Array<{ method: string; url: string }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ method: init?.method ?? 'GET', url: String(input) })
      return response(available)
    }))

    const wrapper = mount(UpdateSettings, { attachTo: document.body })
    await flushPromises()

    expect(wrapper.text()).toContain('应用更新')
    expect(wrapper.text()).toContain('当前版本')
    expect(wrapper.text()).toContain('0.6.0')
    expect(wrapper.text()).toContain('可用版本')
    expect(wrapper.text()).toContain('0.8.0')
    expect(wrapper.text()).toContain('此版本尚未进行代码签名')
    expect(wrapper.text()).toContain('只有点击“立即检查”时才会向 GitHub 查询新版本')
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
    expect(requests).toEqual([{ method: 'GET', url: '/api/v1/settings/updates' }])
  })

  it('checks only after a click and opens the validated page only after confirmation', async () => {
    let checked = false
    const requests: Array<{ method: string; url: string }> = []
    const open = vi.fn()
    vi.stubGlobal('open', open)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const url = String(input)
      requests.push({ method, url })
      if (url === '/api/v1/settings/updates' && method === 'GET') return response(initial)
      if (url === '/api/v1/settings/updates/check' && method === 'POST') {
        checked = true
        return response(available)
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }))

    const wrapper = mount(UpdateSettings, { attachTo: document.body })
    await flushPromises()
    expect(requests.every((item) => item.method === 'GET')).toBe(true)

    await wrapper.get('[data-test="check-updates"]').trigger('click')
    await flushPromises()
    expect(checked).toBe(true)
    expect(wrapper.text()).toContain('新增只读更新检查')

    await wrapper.get('[data-test="review-update"]').trigger('click')
    await flushPromises()
    expect(open).not.toHaveBeenCalled()
    expect(document.querySelector('[data-test="update-confirm-dialog"]')?.textContent)
      .toContain('将离开 FolioSkiff')

    document.querySelector<HTMLButtonElement>('[data-test="confirm-open-release"]')?.click()
    await flushPromises()
    expect(open).toHaveBeenCalledWith(
      available.release.html_url,
      '_blank',
      'noopener,noreferrer',
    )
  })

  it('refuses a cross-host release URL even if a compromised server returns it', async () => {
    const open = vi.fn()
    vi.stubGlobal('open', open)
    vi.stubGlobal('fetch', vi.fn(async () => response({
      ...available,
      release: { ...available.release, html_url: 'https://evil.example/payload.exe' },
    })))

    const wrapper = mount(UpdateSettings, { attachTo: document.body })
    await flushPromises()
    expect(wrapper.text()).toContain('发布页地址未通过本地安全校验')
    expect(wrapper.find('[data-test="review-update"]').exists()).toBe(false)
    expect(open).not.toHaveBeenCalled()
  })

  it('clears the saved release on request and returns to the unchecked state', async () => {
    const requests: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      requests.push(`${method} ${input}`)
      if (method === 'GET') return response(available)
      if (method === 'DELETE') return response(initial)
      throw new Error(`Unexpected method: ${method}`)
    }))

    const wrapper = mount(UpdateSettings, { attachTo: document.body })
    await flushPromises()
    await wrapper.get('[data-test="clear-update-cache"]').trigger('click')
    await flushPromises()
    expect(requests).toEqual(['GET /api/v1/settings/updates', 'DELETE /api/v1/settings/updates/cache'])
    expect(wrapper.text()).toContain('尚未检查更新')
    expect(wrapper.get('[data-test="clear-update-cache"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="review-update"]').exists()).toBe(false)
  })

  it('presents offline state as a recoverable status without technical details', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({
      ...initial,
      status: 'offline',
      checked: true,
      last_checked_at: '2026-07-14T09:30:00Z',
      error_code: 'offline',
    })))
    const wrapper = mount(UpdateSettings, { attachTo: document.body })
    await flushPromises()
    expect(wrapper.text()).toContain('当前无法连接 GitHub')
    expect(wrapper.text()).toContain('稍后可以手动重试')
    expect(wrapper.text()).not.toContain('Traceback')
  })
})
