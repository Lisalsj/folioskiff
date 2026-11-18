import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AgentSettingsPage from './AgentSettingsPage.vue'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const preferences = {
  timezone_mode: 'system',
  timezone_name: null,
  system_timezone: 'Asia/Shanghai',
  system_timezone_fallback: false,
  resolved_timezone: 'Asia/Shanghai',
  theme: 'system',
  default_agent_backend: 'codex_cli',
  default_agent_model_id: null,
  default_reasoning_effort: 'medium',
  report_language: 'zh-CN',
  translation_source_language: 'en',
  translation_target_language: 'zh-CN',
  updated_at: '2026-08-01T00:00:00Z',
}

const cloudAgent = {
  backend_id: 'ollama_cloud',
  display_name: 'Ollama Cloud',
  location: 'cloud',
  version: null,
  probe_code: 'not_authenticated',
  recommended_action: '请先配置凭据。',
  installed: true,
  authenticated: false,
  available: false,
  supports_schema: true,
  supports_streaming: true,
  supports_vision: false,
  supports_external_research: false,
  credential_required: true,
  credential_configured: false,
  supports_manual_model_id: true,
  supports_default_model: false,
  models: [],
  degradation_reasons: ['missing_credential'],
}

const codexAgent = {
  ...cloudAgent,
  backend_id: 'codex_cli',
  display_name: 'Codex CLI',
  version: '0.101.0',
  probe_code: 'configuration_invalid',
  recommended_action: '请修正配置。',
  installed: true,
  credential_required: false,
  supports_vision: true,
  supports_external_research: true,
  supports_default_model: true,
  degradation_reasons: ['configuration_invalid'],
}

describe('AgentSettingsPage', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('shows exact diagnostics without invoking generation endpoints', async () => {
    const requests: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      requests.push(url)
      if (url === '/api/v1/settings/agents') return response([codexAgent])
      if (url === '/api/v1/settings/preferences') return response(preferences)
      throw new Error(`Unexpected request: ${url}`)
    }))

    const wrapper = mount(AgentSettingsPage, { global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(wrapper.get('#codex_cli').text()).toContain('configuration_invalid')
    expect(wrapper.get('#codex_cli').text()).toContain('配置无效')
    expect(wrapper.get('#codex_cli').text()).toContain('云端')
    expect(requests).toEqual([
      '/api/v1/settings/agents',
      '/api/v1/settings/preferences',
    ])
  })

  it('contains a malformed capability response in the recoverable error state', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/v1/settings/agents') return response({ unexpected: true })
      if (url === '/api/v1/settings/preferences') return response(preferences)
      throw new Error(`Unexpected request: ${url}`)
    }))

    const wrapper = mount(AgentSettingsPage, { global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('暂时无法读取')
    expect(wrapper.text()).toContain('0 个')
  })

  it('stores and clears an Ollama Cloud credential without rendering it', async () => {
    const secret = 'private-key'
    const requests: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push(`${method} ${url}`)
      if (url === '/api/v1/settings/agents' && method === 'GET') {
        return response([cloudAgent])
      }
      if (url === '/api/v1/settings/preferences' && method === 'GET') {
        return response(preferences)
      }
      if (
        url === '/api/v1/settings/agents/ollama-cloud/credential'
        && method === 'PUT'
      ) {
        expect(JSON.parse(String(init?.body))).toEqual({ value: secret })
        return response({ backend_id: 'ollama_cloud', configured: true })
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }))

    const wrapper = mount(AgentSettingsPage, { global: { plugins: [createPinia()] } })
    await flushPromises()
    const input = wrapper.get<HTMLInputElement>('[data-test="ollama-cloud-key"]')
    await input.setValue(secret)
    await wrapper.get('[data-test="save-ollama-cloud"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain(secret)
    expect(input.element.value).toBe('')
    expect(requests).toContain('PUT /api/v1/settings/agents/ollama-cloud/credential')
  })

  it('saves closed Agent and language defaults while preserving reading environment', async () => {
    const bodies: unknown[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/v1/settings/agents' && method === 'GET') {
        return response([codexAgent, cloudAgent])
      }
      if (url === '/api/v1/settings/preferences' && method === 'GET') {
        return response(preferences)
      }
      if (url === '/api/v1/settings/preferences' && method === 'PUT') {
        const body = JSON.parse(String(init?.body))
        bodies.push(body)
        return response({ ...preferences, ...body })
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }))
    const wrapper = mount(AgentSettingsPage, { global: { plugins: [createPinia()] } })
    await flushPromises()

    await wrapper.get('[data-test="default-agent-backend"]').setValue('ollama_cloud')
    await wrapper.get('[data-test="default-agent-model"]').setValue('qwen3.5:cloud')
    await wrapper.get('[data-test="default-reasoning-effort"]').setValue('high')
    await wrapper.get('[data-test="default-report-language"]').setValue('bilingual')
    await wrapper.get('[data-test="agent-defaults-form"]').trigger('submit')
    await flushPromises()

    expect(bodies).toEqual([{
      timezone_mode: 'system',
      timezone_name: null,
      theme: 'system',
      default_agent_backend: 'ollama_cloud',
      default_agent_model_id: 'qwen3.5:cloud',
      default_reasoning_effort: 'high',
      report_language: 'bilingual',
      translation_source_language: 'en',
      translation_target_language: 'zh-CN',
    }])
    expect(wrapper.text()).toContain('默认值已保存')
  })
})
