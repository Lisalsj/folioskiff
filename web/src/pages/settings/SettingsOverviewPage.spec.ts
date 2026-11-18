import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { components } from '@/api/schema'

import SettingsOverviewPage from './SettingsOverviewPage.vue'

type OnboardingOverview = components['schemas']['OnboardingOverview']

const overview: OnboardingOverview = {
  state: {
    completed: true,
    current_step: 'complete',
    steps: {
      storage_privacy: 'completed',
      agents: 'completed',
      sources: 'completed',
      reading_preferences: 'completed',
    },
    privacy_acknowledged_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  },
  storage: {
    writable: false,
    state_writable: true,
    cache_writable: false,
    library_writable: true,
    state_root: 'C:\\state',
    cache_root: 'C:\\cache',
    library_root: 'D:\\papers',
    backups_root: 'D:\\papers\\backups',
  },
  agents: [{
    backend_id: 'codex_cli',
    display_name: 'Codex CLI',
    location: 'cloud',
    version: '0.101.0',
    probe_code: 'configuration_invalid',
    recommended_action: '请修正配置后重新检测。',
    installed: true,
    authenticated: false,
    available: false,
    supports_manual_model_id: true,
    supports_default_model: true,
    models: [],
    degradation_reasons: ['configuration_invalid'],
  }],
  sources: [
    {
      connector_id: 'arxiv',
      display_name: 'arXiv',
      enabled: true,
      credential_requirement: 'none',
      credential_configured: false,
      disabled_reason: null,
      setup_url: null,
    },
    {
      connector_id: 'ieee',
      display_name: 'IEEE Xplore',
      enabled: false,
      credential_requirement: 'api_key',
      credential_configured: false,
      disabled_reason: 'missing_api_key',
      setup_url: 'https://developer.ieee.org/',
    },
  ],
  retrieval: {
    lexical_enabled: true,
    semantic_enabled: false,
    model_state: 'not_installed',
    degradation_reasons: ['embedding_model_not_enabled'],
  },
  blocking_issues: ['storage_unwritable'],
}

describe('SettingsOverviewPage', () => {
  it('links each capability issue to its exact section', () => {
    const wrapper = mount(SettingsOverviewPage, {
      props: { overview },
      global: {
        stubs: {
          RouterLink: {
            props: ['to'],
            template: '<a :href="to"><slot /></a>',
          },
        },
      },
    })

    expect(wrapper.get('[data-issue="configuration_invalid"]').attributes('href'))
      .toBe('/settings/agents#codex_cli')
    expect(wrapper.get('[data-issue="storage_unwritable"]').attributes('href'))
      .toBe('/settings/storage')
    expect(wrapper.text()).toContain('词法检索可用')
    expect(wrapper.text()).toContain('1 个来源可用')
    expect(wrapper.text()).toContain('1 个免凭据 · 1 个凭据来源')
  })
})
