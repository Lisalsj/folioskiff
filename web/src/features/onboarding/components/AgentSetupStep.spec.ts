import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { components } from '@/api/schema'

import AgentSetupStep from './AgentSetupStep.vue'

type AgentProbeCode = components['schemas']['AgentProbeCode']

function codexCapability(probeCode: AgentProbeCode) {
  return {
    backend_id: 'codex_cli',
    display_name: 'Codex CLI',
    location: 'cloud',
    probe_code: probeCode,
    recommended_action: '请修正 Agent 配置文件中的无效设置，然后重新检测。',
    installed: true,
    authenticated: false,
    available: false,
    supports_manual_model_id: true,
    supports_default_model: true,
    models: [],
    degradation_reasons: ['configuration_invalid'],
  }
}

describe('AgentSetupStep', () => {
  it('shows invalid Codex configuration separately from login state', () => {
    const wrapper = mount(AgentSetupStep, {
      props: { agents: [codexCapability('configuration_invalid')] },
    })

    expect(wrapper.get('[data-agent="codex_cli"]').text()).toContain('Codex 配置无效')
    expect(wrapper.text()).not.toContain('尚未登录')
    expect(wrapper.text()).toContain('本地 CLI，模型可能在云端运行')
  })

  it('shows discovered models and emits only an explicit refresh action', async () => {
    const capability = {
      ...codexCapability('ready'),
      authenticated: true,
      available: true,
      version: '0.101.0',
      desktop_app_running: true,
      recommended_action: null,
      models: [{
        model_id: 'gpt-fixture',
        display_name: 'GPT Fixture',
        supports_vision: true,
        context_window: 128000,
      }],
    }
    const wrapper = mount(AgentSetupStep, { props: { agents: [capability], probing: false } })

    expect(wrapper.text()).toContain('GPT Fixture')
    expect(wrapper.text()).toContain('0.101.0')
    expect(wrapper.text()).toContain('ChatGPT 桌面应用正在运行')
    await wrapper.get('[data-test="probe-agents"]').trigger('click')
    expect(wrapper.emitted('refresh')).toHaveLength(1)
  })
})
