import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ScoreRuleEditor from './ScoreRuleEditor.vue'

const dimensions = [
  ['citation_performance', '归一化引用表现', 25],
  ['venue_rating', '发表场所与评级', 15],
  ['author_impact', '作者公开学术影响', 10],
  ['institution_impact', '机构与实验室影响', 10],
  ['code_data_impact', '代码与数据影响', 20],
  ['research_transparency', '研究透明度', 10],
  ['publication_maturity', '发表成熟度', 10],
] as const

function rule(custom = false) {
  const configured = dimensions.map(([key, label, weight]) => ({
    key,
    label,
    description: `${label}的证据说明`,
    enabled: custom ? key !== 'institution_impact' : true,
    weight_percent: custom
      ? key === 'citation_performance'
        ? 35
        : key === 'institution_impact'
          ? 0
          : weight
      : weight,
  }))
  return {
    id: custom ? 'rule-custom' : 'quality-default-v1',
    rule_version: custom ? 'quality-v1:custom' : 'quality-v1',
    revision: custom ? 1 : 0,
    name: custom ? '偏重归一化引用' : '默认客观质量规则',
    builtin: !custom,
    active: true,
    dimensions: configured,
    rule_sha256: 'a'.repeat(64),
    parent_version_id: null,
    created_at: custom ? '2026-07-13T08:00:00Z' : null,
    preview: {
      score: 50,
      coverage: 0,
      confidence: 0,
      missing_policy: 'unknown_is_neutral_prior',
      components: configured.map((item) => ({
        key: item.key,
        label: item.label,
        enabled: item.enabled,
        weight_percent: item.weight_percent,
        effective_weight_percent: item.weight_percent,
        status: 'unknown',
        observed_value: null,
        imputed_value: 50,
        reason: 'evidence_not_loaded',
      })),
    },
  }
}

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('ScoreRuleEditor', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('saves a server-versioned seven-dimension rule and can restore defaults', async () => {
    const requests: Array<{ method: string; body?: unknown }> = []
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      requests.push({
        method,
        body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
      })
      if (method === 'GET') return response(rule())
      if (method === 'PUT') return response(rule(true))
      if (method === 'DELETE') return response(rule())
      throw new Error(`Unexpected ${method}`)
    })
    vi.stubGlobal('fetch', fetcher)

    const wrapper = mount(ScoreRuleEditor)
    await flushPromises()

    expect(wrapper.findAll('[data-test^="score-rule-row-"]')).toHaveLength(7)
    expect(wrapper.text()).toContain('内置默认规则')
    expect(wrapper.text()).toContain('未知证据使用中性 50，不等于低质量')

    await wrapper.get('[data-test="score-rule-name"]').setValue('偏重归一化引用')
    await wrapper.get('[data-test="score-rule-enabled-institution_impact"]').setValue(false)
    await wrapper.get('[data-test="score-rule-weight-citation_performance"]').setValue(35)
    expect(wrapper.get('[data-test="score-rule-total"]').text()).toContain('100%')
    await wrapper.get('[data-test="score-rule-form"]').trigger('submit')
    await flushPromises()

    const put = requests.find((request) => request.method === 'PUT')
    expect(put?.body).toMatchObject({
      name: '偏重归一化引用',
      dimensions: expect.arrayContaining([
        { key: 'citation_performance', enabled: true, weight_percent: 35 },
        { key: 'institution_impact', enabled: false, weight_percent: 0 },
      ]),
    })
    expect(wrapper.text()).toContain('自定义修订 1')
    expect(wrapper.text()).toContain('规则已保存为不可变修订')

    await wrapper.get('[data-test="score-rule-reset"]').trigger('click')
    await flushPromises()
    expect(requests.some((request) => request.method === 'DELETE')).toBe(true)
    expect(wrapper.text()).toContain('已恢复内置默认规则')
  })
})
