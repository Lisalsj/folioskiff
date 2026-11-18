import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PluginSettings from './PluginSettings.vue'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const plugin = {
  plugin_id: 'fixture.catalog',
  display_name: 'Fixture Catalog',
  distribution_name: 'paper-reader-fixture-catalog',
  distribution_version: '2.1.0',
  license: 'Apache-2.0',
  protocol_minimum: '1.0',
  protocol_maximum: '1.0',
  compatible: true,
  compatibility_reason: null,
  enabled: false,
  status: 'disabled',
  health_reason: 'plugin_disabled',
  consecutive_failures: 0,
  circuit_open_reason: null,
  last_called_at: null,
  sandbox_available: true,
  sandbox_reason: null,
  capabilities: [
    {
      capability_id: 'fixture.catalog.source',
      kind: 'source_connector',
      methods: ['estimate', 'fetch_page', 'probe'],
      timeout_seconds: 30,
      ready: false,
      permissions: [
        {
          kind: 'network',
          resource: 'api.example.org',
          purpose: 'Read public fixture metadata',
          granted: false,
        },
        {
          kind: 'credential',
          resource: 'fixture_api_key',
          purpose: 'Authenticate through the host broker',
          granted: false,
        },
      ],
    },
  ],
} as const

function mountSettings() {
  return mount(PluginSettings, { attachTo: document.body })
}

describe('PluginSettings', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it('keeps an honest empty state and never presents the example as installed', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response([])))

    const wrapper = mountSettings()
    await flushPromises()

    expect(wrapper.text()).toContain('扩展能力')
    expect(wrapper.text()).toContain('尚未发现已安装的扩展')
    expect(wrapper.text()).toContain('示例插件不会自动安装或冒充真实论文来源')
    expect(wrapper.text()).not.toContain('Fixture Catalog')
    expect(wrapper.get('[data-test="reload-plugins"]').attributes('type')).toBe('button')
  })

  it('discovers installed metadata only after an explicit isolated refresh', async () => {
    const requests: string[] = []
    let listed = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push(`${method} ${url}`)
      if (url === '/api/v1/plugins/refresh' && method === 'POST') {
        listed = true
        return response({ discovered: 1, registered: 1, issues: [] })
      }
      if (url === '/api/v1/plugins' && method === 'GET') {
        return response(listed ? [plugin] : [])
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }))

    const wrapper = mountSettings()
    await flushPromises()
    expect(wrapper.text()).toContain('尚未发现已安装的扩展')

    await wrapper.get('[data-test="reload-plugins"]').trigger('click')
    await flushPromises()

    expect(requests).toEqual([
      'GET /api/v1/plugins',
      'POST /api/v1/plugins/refresh',
      'GET /api/v1/plugins',
    ])
    expect(wrapper.text()).toContain('Fixture Catalog')
    expect(wrapper.text()).toContain('检查完成：发现 1 个，已登记 1 个')
  })

  it('shows distribution capabilities permissions and safe health without secret values', async () => {
    const privateCanary = 'secret-value-canary'
    vi.stubGlobal('fetch', vi.fn(async () => response([plugin])))

    const wrapper = mountSettings()
    await flushPromises()

    expect(wrapper.text()).toContain('Fixture Catalog')
    expect(wrapper.text()).toContain('paper-reader-fixture-catalog · 2.1.0')
    expect(wrapper.text()).toContain('论文来源')
    expect(wrapper.text()).toContain('api.example.org')
    expect(wrapper.text()).toContain('Read public fixture metadata')
    expect(wrapper.text()).toContain('等待启用')
    expect(wrapper.text()).toContain('Windows 隔离可用')
    expect(wrapper.text()).not.toContain(privateCanary)
    expect(wrapper.text().toLowerCase()).not.toContain('stderr')
    expect(wrapper.find('[data-test="enable-fixture.catalog"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="grant-fixture.catalog-network-api.example.org"]').exists())
      .toBe(true)
  })

  it('distinguishes an uninstalled or unverified plugin from protocol incompatibility', async () => {
    const stale = {
      ...plugin,
      compatible: false,
      compatibility_reason: 'plugin_not_installed',
      status: 'incompatible',
      health_reason: 'plugin_not_installed',
    }
    vi.stubGlobal('fetch', vi.fn(async () => response([stale])))

    const wrapper = mountSettings()
    await flushPromises()

    expect(wrapper.text()).toContain('未找到安装')
    expect(wrapper.text()).toContain('本次检查未在当前运行时找到这个扩展')
    expect(wrapper.text()).not.toContain('协议与本机版本不兼容')
  })

  it('confirms one exact permission before granting and updates only that plugin', async () => {
    const requests: Array<{ url: string; method: string; body?: unknown }> = []
    const granted = {
      ...plugin,
      capabilities: [
        {
          ...plugin.capabilities[0],
          permissions: [
            { ...plugin.capabilities[0].permissions[0], granted: true },
            plugin.capabilities[0].permissions[1],
          ],
        },
      ],
    }
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
      requests.push({ url, method, body })
      if (url === '/api/v1/plugins' && method === 'GET') return response([plugin])
      if (
        url === '/api/v1/plugins/fixture.catalog/permissions' &&
        method === 'POST'
      ) return response(granted)
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mountSettings()
    await flushPromises()

    await wrapper
      .get('[data-test="grant-fixture.catalog-network-api.example.org"]')
      .trigger('click')
    await flushPromises()

    const dialog = document.querySelector('[data-test="plugin-permission-dialog"]')
    expect(dialog?.textContent).toContain('api.example.org')
    expect(dialog?.textContent).toContain('Read public fixture metadata')
    expect(dialog?.textContent).toContain('只批准这一项')
    document.querySelector<HTMLButtonElement>('[data-test="confirm-plugin-permission"]')?.click()
    await flushPromises()

    expect(requests.at(-1)).toEqual({
      url: '/api/v1/plugins/fixture.catalog/permissions',
      method: 'POST',
      body: { kind: 'network', resource: 'api.example.org', decision: 'grant' },
    })
    expect(wrapper.text()).toContain('已批准 api.example.org')
    expect(document.querySelector('[data-test="plugin-permission-dialog"]')).toBeNull()
  })

  it('enables disables and explicitly resets an open circuit', async () => {
    const circuit = {
      ...plugin,
      enabled: true,
      status: 'circuit_open',
      health_reason: 'protocol_error',
      consecutive_failures: 3,
      circuit_open_reason: 'protocol_error',
    }
    const reset = {
      ...circuit,
      status: 'permissions_required',
      health_reason: 'permissions_required',
      consecutive_failures: 0,
      circuit_open_reason: null,
    }
    const disabled = { ...reset, enabled: false, status: 'disabled', health_reason: 'plugin_disabled' }
    const requests: string[] = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push(`${method} ${url}`)
      if (url === '/api/v1/plugins' && method === 'GET') return response([circuit])
      if (url.endsWith('/reset') && method === 'POST') return response(reset)
      if (url.endsWith('/enabled') && method === 'PUT') {
        expect(JSON.parse(String(init?.body))).toEqual({ enabled: false })
        return response(disabled)
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mountSettings()
    await flushPromises()

    expect(wrapper.text()).toContain('已暂停保护')
    expect(wrapper.text()).toContain('连续失败 3 次')
    await wrapper.get('[data-test="reset-fixture.catalog"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已恢复调用')
    await wrapper.get('[data-test="disable-fixture.catalog"]').trigger('click')
    await flushPromises()

    expect(requests).toContain('POST /api/v1/plugins/fixture.catalog/reset')
    expect(requests).toContain('PUT /api/v1/plugins/fixture.catalog/enabled')
    expect(wrapper.text()).toContain('已停用 Fixture Catalog')
  })

  it('uses a stable local message instead of rendering arbitrary backend diagnostics', async () => {
    const privateCanary = 'C:\\Users\\reader\\plugin-stderr-private.txt'
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      if (method === 'GET') return response([plugin])
      return response({ detail: privateCanary }, 500)
    })
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mountSettings()
    await flushPromises()

    await wrapper.get('[data-test="enable-fixture.catalog"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('无法更新扩展状态，请稍后重试')
    expect(wrapper.text()).not.toContain(privateCanary)
  })
})
