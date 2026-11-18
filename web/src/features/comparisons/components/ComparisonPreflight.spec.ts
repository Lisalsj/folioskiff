import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ComparisonPreflight from './ComparisonPreflight.vue'

describe('ComparisonPreflight', () => {
  it('keeps unavailable evidence and running progress explicit', async () => {
    const wrapper = mount(ComparisonPreflight, {
      props: {
        preflight: {
          comparison_key: 'a'.repeat(64), mode: 'staged', item_count: 2, available_count: 1,
          coverage: 50, backend_id: 'codex_cli', backend_display_name: 'Codex CLI',
          backend_location: 'cloud', cloud_backend: true, model_id: 'fixture', model_options: [],
          manual_model_id_allowed: true, default_model_allowed: true, reasoning_effort: 'high',
          service_tier: 'flex', language: 'zh-CN', ready: false, warnings: [],
          prompt_sha256: 'b'.repeat(64), preflight_fingerprint: 'c'.repeat(64),
          items: [{
            position: 0, requested_work_id: 'work-1', work_id: 'work-1', title: 'Unavailable paper',
            document_version_id: null, document_content_sha256: null, evidence_level: null,
            evidence_sha256: null, coverage: 0, status: 'unavailable', warnings: [],
          }],
        },
        activeJob: {
          status: 'running', progress: 40,
          checkpoint: { stage: 'extraction', processed: 1, available: 2 },
        },
        generating: true,
      },
    })
    expect(wrapper.text()).toContain('无可用证据')
    expect(wrapper.text()).toContain('逐篇提取')
    expect(wrapper.text()).toContain('已处理 1 / 2 篇')
    expect(wrapper.get('[data-test="generate-comparison"]').attributes('disabled')).toBeDefined()

    await wrapper.setProps({
      activeJob: { status: 'cancelled', progress: 40, checkpoint: null },
      generating: false,
    })
    expect(wrapper.text()).toContain('比较已取消')

    await wrapper.setProps({
      activeJob: { status: 'failed', progress: 100, checkpoint: null },
    })
    expect(wrapper.text()).toContain('比较失败')
  })
})
