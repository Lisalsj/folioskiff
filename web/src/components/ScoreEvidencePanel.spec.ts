import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ScoreEvidencePanel from './ScoreEvidencePanel.vue'

describe('ScoreEvidencePanel', () => {
  it('renders server-provided score coverage and neutral imputations without recalculating', () => {
    const wrapper = mount(ScoreEvidencePanel, {
      props: {
        ruleVersion: 'quality-v1:fixture',
        preview: {
          score: 62.5,
          coverage: 25,
          confidence: 20,
          missing_policy: 'unknown_is_neutral_prior',
          components: [
            {
              key: 'citation_performance',
              label: '归一化引用表现',
              enabled: true,
              weight_percent: 25,
              effective_weight_percent: 25,
              status: 'observed',
              observed_value: 100,
              imputed_value: null,
              reason: null,
            },
            {
              key: 'venue_rating',
              label: '发表场所与评级',
              enabled: true,
              weight_percent: 15,
              effective_weight_percent: 15,
              status: 'unknown',
              observed_value: null,
              imputed_value: 50,
              reason: 'evidence_not_loaded',
            },
            {
              key: 'institution_impact',
              label: '机构与实验室影响',
              enabled: false,
              weight_percent: 0,
              effective_weight_percent: 0,
              status: 'unknown',
              observed_value: null,
              imputed_value: 50,
              reason: 'evidence_not_loaded',
            },
          ],
        },
      },
    })

    expect(wrapper.get('[data-test="evidence-score"]').text()).toBe('62.5')
    expect(wrapper.get('[data-test="evidence-coverage"]').text()).toBe('25%')
    expect(wrapper.get('[data-test="evidence-confidence"]').text()).toBe('20%')
    expect(wrapper.text()).toContain('未知证据使用中性 50，不等于低质量')
    expect(wrapper.text()).toContain('已观测 · 100')
    expect(wrapper.text()).toContain('未知 · 中性 50')
    expect(wrapper.text()).toContain('已停用')
    expect(wrapper.text()).toContain('quality-v1:fixture')
  })
})
