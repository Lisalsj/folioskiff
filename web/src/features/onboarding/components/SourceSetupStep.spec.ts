import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SourceSetupStep from './SourceSetupStep.vue'

const sources = [
  {
    connector_id: 'arxiv',
    display_name: 'arXiv',
    enabled: true,
    credential_requirement: 'none',
    credential_configured: false,
    disabled_reason: null,
    setup_url: 'https://info.arxiv.org/help/api/',
  },
  {
    connector_id: 'ieee',
    display_name: 'IEEE Xplore',
    enabled: false,
    credential_requirement: 'api_key',
    credential_configured: false,
    disabled_reason: 'missing_api_key',
    setup_url: 'https://developer.ieee.org/getting_started',
  },
]

describe('SourceSetupStep', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('marks free sources usable and keyed sources optional', () => {
    const wrapper = mount(SourceSetupStep, { props: { sources } })

    expect(wrapper.get('[data-source="arxiv"]').text()).toContain('可用')
    expect(wrapper.get('[data-source="ieee"]').text()).toContain('可稍后配置')
    expect(wrapper.findAll('input[type="password"]')).toHaveLength(1)
  })

  it('clears a source secret immediately after a successful save', async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(JSON.parse(String(init?.body))).toEqual({ value: 'private-key' })
      return new Response(JSON.stringify({
        ...sources[1],
        enabled: true,
        credential_configured: true,
        disabled_reason: null,
        supports_exact_updated_date: true,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(SourceSetupStep, { props: { sources } })
    const input = wrapper.get<HTMLInputElement>('[data-credential="ieee"]')
    await input.setValue('private-key')
    await wrapper.get('[data-save-credential="ieee"]').trigger('click')
    await flushPromises()

    expect(input.element.value).toBe('')
    expect(wrapper.text()).not.toContain('private-key')
    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/settings/sources/ieee/credential',
      expect.objectContaining({ method: 'PUT', credentials: 'same-origin' }),
    )
  })

  it('clears a source secret and keeps server details hidden after a failed save', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      detail: { code: 'credential_store_failed', diagnostic: 'do-not-render' },
    }), { status: 500, headers: { 'Content-Type': 'application/json' } })))
    const wrapper = mount(SourceSetupStep, { props: { sources } })
    const input = wrapper.get<HTMLInputElement>('[data-credential="ieee"]')
    await input.setValue('private-key')
    await wrapper.get('[data-save-credential="ieee"]').trigger('click')
    await flushPromises()

    expect(input.element.value).toBe('')
    expect(wrapper.get('[role="alert"]').text()).toContain('凭据保存失败')
    expect(wrapper.text()).not.toContain('private-key')
    expect(wrapper.text()).not.toContain('do-not-render')
  })
})
