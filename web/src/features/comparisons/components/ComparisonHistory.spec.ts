import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ComparisonHistory from './ComparisonHistory.vue'

describe('ComparisonHistory', () => {
  it('renders immutable versions and emits a report identifier', async () => {
    const wrapper = mount(ComparisonHistory, {
      props: {
        loading: false,
        history: [{
          id: 'report-1', comparison_key: 'a'.repeat(64), version: 2, job_id: 'job-1',
          title: 'Versioned comparison', mode: 'direct', backend_id: 'codex_cli',
          requested_model_id: null, resolved_model_id: 'fixture', reasoning_effort: 'high',
          language: 'zh-CN', coverage: 88, schema_version: 'comparison-report-v1',
          created_at: '2026-08-01T00:00:00Z', items: [],
        }],
      },
    })
    expect(wrapper.text()).toContain('版本 2')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual(['report-1'])
  })
})
