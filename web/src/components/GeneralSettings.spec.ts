import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import GeneralSettings from './GeneralSettings.vue'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const initial = {
  timezone_mode: 'system',
  timezone_name: null,
  system_timezone: 'America/New_York',
  system_timezone_fallback: false,
  resolved_timezone: 'America/New_York',
  theme: 'system',
  default_agent_backend: 'codex_cli',
  default_agent_model_id: null,
  default_reasoning_effort: 'medium',
  report_language: 'zh-CN',
  translation_source_language: 'en',
  translation_target_language: 'zh-CN',
  updated_at: '2026-07-15T08:00:00Z',
}

describe('GeneralSettings', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    window.localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
  })

  it('shows resolved system facts and saves a closed custom choice', async () => {
    const bodies: unknown[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/v1/settings/preferences' && !init?.method) return json(initial)
      if (url === '/api/v1/settings/preferences' && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body))
        bodies.push(body)
        return json({
          ...initial,
          ...body,
          resolved_timezone: body.timezone_name,
          updated_at: '2026-07-15T08:01:00Z',
        })
      }
      throw new Error(`Unexpected request ${url}`)
    }))
    const wrapper = mount(GeneralSettings, { global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(wrapper.text()).toContain('America/New_York')
    await wrapper.get('[data-test="timezone-custom"]').setValue(true)
    await wrapper.get('[data-test="timezone-name"]').setValue('Europe/Berlin')
    await wrapper.get('[data-test="theme-dark"]').setValue(true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(bodies).toEqual([{
      timezone_mode: 'custom',
      timezone_name: 'Europe/Berlin',
      theme: 'dark',
      default_agent_backend: 'codex_cli',
      default_agent_model_id: null,
      default_reasoning_effort: 'medium',
      report_language: 'zh-CN',
      translation_source_language: 'en',
      translation_target_language: 'zh-CN',
    }])
    expect(wrapper.get('[data-test="general-settings-status"]').text()).toContain('已保存')
    expect(wrapper.text()).toContain('Europe/Berlin')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('explains the safe fallback when the operating-system timezone cannot be resolved', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({
      ...initial,
      system_timezone: 'Asia/Shanghai',
      resolved_timezone: 'Asia/Shanghai',
      system_timezone_fallback: true,
    })))
    const wrapper = mount(GeneralSettings, { global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(wrapper.get('[data-test="timezone-fallback"]').text()).toContain('Asia/Shanghai')
  })
})
