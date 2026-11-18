import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import RecommendationSettingsPage from './RecommendationSettingsPage.vue'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const dimensions = [
  ['citation_performance', 25],
  ['venue_rating', 15],
  ['author_impact', 10],
  ['institution_impact', 10],
  ['code_data_impact', 20],
  ['research_transparency', 10],
  ['publication_maturity', 10],
] as const
const qualityRule = {
  id: 'quality-default-v1',
  rule_version: 'quality-v1',
  revision: 0,
  name: '默认客观质量规则',
  builtin: true,
  active: true,
  dimensions: dimensions.map(([key, weight]) => ({
    key,
    label: key,
    description: `${key} evidence`,
    enabled: true,
    weight_percent: weight,
  })),
  rule_sha256: 'a'.repeat(64),
  parent_version_id: null,
  created_at: null,
  preview: {
    score: 50,
    coverage: 0,
    confidence: 0,
    missing_policy: 'unknown_is_neutral_prior',
    components: dimensions.map(([key, weight]) => ({
      key,
      label: key,
      enabled: true,
      weight_percent: weight,
      effective_weight_percent: weight,
      status: 'unknown',
      observed_value: null,
      imputed_value: 50,
      reason: 'evidence_not_loaded',
    })),
  },
}
const ccfCatalog = {
  catalog_id: 'ccf-2022-sixth-2024-rename',
  edition: 6,
  rating_year: 2022,
  rename_revision_date: '2024-06-28',
  entry_count: 649,
  journal_count: 284,
  conference_count: 365,
  source_url: 'https://www.ccf.org.cn/Academic_Evaluation/By_category/2024-06-28/825349.shtml',
  source_pdf_sha256: 'b'.repeat(64),
}

describe('RecommendationSettingsPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('imports and deactivates a licensed rating catalog without loading source settings', async () => {
    const imported = {
      id: 'catalog-jcr-1',
      kind: 'jcr',
      catalog_name: 'JCR local export',
      catalog_version: '2025',
      source_note: 'Licensed institutional export',
      filename: 'jcr.csv',
      file_sha256: 'c'.repeat(64),
      row_count: 1,
      imported_at: '2026-08-01T00:00:00Z',
      active: true,
    }
    const requests: string[] = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push(`${method} ${url}`)
      if (url === '/api/v1/scores/rules/quality' && method === 'GET') return response(qualityRule)
      if (url === '/api/v1/scores/rating-catalogs' && method === 'GET') return response([])
      if (url === '/api/v1/scores/rating-catalogs/ccf' && method === 'GET') return response(ccfCatalog)
      if (url === '/api/v1/scores/rating-catalogs/import' && method === 'POST') {
        const body = init?.body as FormData
        expect(body.get('catalog_name')).toBe('JCR local export')
        expect(body.get('rights_confirmed')).toBe('true')
        return response(imported, 201)
      }
      if (url === '/api/v1/scores/rating-catalogs/jcr/active' && method === 'DELETE') {
        return new Response(null, { status: 204 })
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(RecommendationSettingsPage)
    await flushPromises()

    expect(wrapper.text()).toContain('CCF 2022 第六版')
    await wrapper.get('[data-test="rating-name"]').setValue('JCR local export')
    await wrapper.get('[data-test="rating-version"]').setValue('2025')
    await wrapper.get('[data-test="rating-source-note"]').setValue('Licensed institutional export')
    await wrapper.get('[data-test="rating-rights"]').setValue(true)
    const fileInput = wrapper.get('[data-test="rating-file"]')
    const file = new File(['venue,rating\nJournal One,Q1\n'], 'jcr.csv', { type: 'text/csv' })
    Object.defineProperty(fileInput.element, 'files', { configurable: true, value: [file] })
    await fileInput.trigger('change')
    await wrapper.get('[data-test="rating-import-form"]').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('JCR local export')
    expect(wrapper.text()).toContain('1 条')
    await wrapper.get('[data-test="rating-deactivate-jcr"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已停用')
    expect(requests.some((request) => request.includes('/settings/sources'))).toBe(false)
  })
})
