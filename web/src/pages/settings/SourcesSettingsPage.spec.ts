import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SourcesSettingsPage from './SourcesSettingsPage.vue'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const freeSource = {
  connector_id: 'arxiv',
  display_name: 'arXiv',
  credential_requirement: 'none',
  supports_exact_updated_date: false,
  enabled: true,
  disabled_reason: null,
  credential_configured: false,
  setup_url: 'https://info.arxiv.org/help/api/',
  protocol_version: '1',
}
const keyedSource = {
  connector_id: 'ieee',
  display_name: 'IEEE Xplore',
  credential_requirement: 'api_key',
  supports_exact_updated_date: true,
  enabled: false,
  disabled_reason: 'missing_api_key',
  credential_configured: false,
  setup_url: 'https://developer.ieee.org/',
  protocol_version: '1',
}
const crossref = {
  enricher_id: 'crossref',
  display_name: 'Crossref',
  version: 'rest-v1',
  enabled: true,
  disabled_reason: null,
  setup_url: 'https://www.crossref.org/documentation/',
  default_requests_per_second: 5,
  max_concurrency: 1,
  credentials: [{
    credential_id: 'contact_email',
    label: 'Crossref contact email',
    required: false,
    secret: false,
    configured: false,
  }],
}

describe('SourcesSettingsPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('loads only source and evidence endpoints and never retains a saved secret', async () => {
    const secret = 'private-ieee-key'
    const requests: string[] = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push(`${method} ${url}`)
      if (url === '/api/v1/settings/sources' && method === 'GET') {
        return response([freeSource, keyedSource])
      }
      if (url === '/api/v1/settings/enrichers' && method === 'GET') return response([crossref])
      if (url === '/api/v1/settings/sources/ieee/credential' && method === 'PUT') {
        expect(JSON.parse(String(init?.body))).toEqual({ value: secret })
        return response({
          ...keyedSource,
          enabled: true,
          disabled_reason: null,
          credential_configured: true,
        })
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(SourcesSettingsPage)
    await flushPromises()

    expect(wrapper.text()).toContain('arXiv')
    expect(wrapper.text()).toContain('Crossref')
    expect(requests).toEqual([
      'GET /api/v1/settings/sources',
      'GET /api/v1/settings/enrichers',
    ])
    const input = wrapper.get<HTMLInputElement>('[data-test="credential-ieee"]')
    await input.setValue(secret)
    await wrapper.get('[data-test="credential-form-ieee"]').trigger('submit')
    await flushPromises()

    expect(input.element.value).toBe('')
    expect(wrapper.text()).toContain('已安全配置')
    expect(wrapper.text()).not.toContain(secret)
    expect(requests.some((request) => request.includes('rating-catalogs'))).toBe(false)
  })

  it('clears evidence inputs and hides backend diagnostics when saving fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/v1/settings/sources') return response([])
      if (url === '/api/v1/settings/enrichers' && method === 'GET') return response([crossref])
      if (url.includes('/credentials/contact_email') && method === 'PUT') {
        return response({ detail: { code: 'credential_store_failed', raw: 'do-not-render' } }, 500)
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }))
    const wrapper = mount(SourcesSettingsPage)
    await flushPromises()
    const input = wrapper.get<HTMLInputElement>('[data-test="enricher-crossref-contact_email"]')
    await input.setValue('reader@example.org')
    await wrapper.get('[data-test="enricher-form-crossref-contact_email"]').trigger('submit')
    await flushPromises()

    expect(input.element.value).toBe('')
    expect(wrapper.get('[role="alert"]').text()).toContain('无法保存')
    expect(wrapper.text()).not.toContain('reader@example.org')
    expect(wrapper.text()).not.toContain('do-not-render')
  })
})
