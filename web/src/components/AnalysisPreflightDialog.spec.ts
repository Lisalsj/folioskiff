import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import type { components } from '@/api/schema'

import AnalysisPreflightDialog from './AnalysisPreflightDialog.vue'

type Capability = components['schemas']['AgentCapabilityResponse']
type Preflight = components['schemas']['AnalysisPreflight']
type Selection = components['schemas']['AnalysisSelection']

const capabilities: Capability[] = [
  {
    backend_id: 'codex_cli',
    display_name: 'Codex CLI',
    location: 'cloud',
    probe_code: 'ready',
    recommended_action: null,
    installed: true,
    authenticated: true,
    available: true,
    supports_schema: true,
    supports_streaming: true,
    supports_vision: true,
    supports_external_research: true,
    credential_required: false,
    credential_configured: false,
    supports_manual_model_id: true,
    supports_default_model: true,
    models: [
      {
        model_id: 'fixture-model',
        display_name: 'Fixture model',
        supports_vision: true,
        context_window: 128_000,
      },
    ],
    degradation_reasons: [],
  },
  {
    backend_id: 'ollama_local',
    display_name: 'Ollama Local',
    location: 'local',
    probe_code: 'ready',
    recommended_action: null,
    installed: true,
    authenticated: true,
    available: true,
    supports_schema: true,
    supports_streaming: true,
    supports_vision: false,
    supports_external_research: false,
    credential_required: false,
    credential_configured: false,
    supports_manual_model_id: true,
    supports_default_model: false,
    models: [],
    degradation_reasons: ['model_catalog_empty'],
  },
]

const selection: Selection = {
  document_version_id: 'document-1',
  backend_id: 'codex_cli',
  model_id: 'fixture-model',
  reasoning_effort: 'high',
  service_tier: 'flex',
  language: 'zh-CN',
  template_version_id: 'builtin-general-v1',
  external_research: false,
  include_visual_evidence: true,
  temporary_instruction: null,
}

const preflight: Preflight = {
  work_id: 'work-1',
  document_version_id: 'document-1',
  document_content_sha256: 'a'.repeat(64),
  evidence_level: 'full_text',
  evidence_sha256: 'b'.repeat(64),
  coverage: 86,
  backend_id: 'codex_cli',
  backend_display_name: 'Codex CLI',
  backend_location: 'cloud',
  cloud_backend: true,
  model_id: 'fixture-model',
  model_options: [
    {
      model_id: 'fixture-model',
      display_name: 'Fixture model',
      supports_vision: true,
      context_window: 128_000,
    },
  ],
  manual_model_id_allowed: true,
  default_model_allowed: true,
  reasoning_effort: 'high',
  service_tier: 'flex',
  language: 'zh-CN',
  template_version_id: 'builtin-general-v1',
  template_name: '通用论文分析',
  external_research: false,
  scope: ['pages:12', 'annotations:3', 'evidence:full_text', 'visual-pages:2'],
  warnings: [],
  ready: true,
  prompt_sha256: 'c'.repeat(64),
  preflight_fingerprint: 'd'.repeat(64),
}

describe('AnalysisPreflightDialog', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('shows the compact evidence and execution contract without a private-paper gate', async () => {
    const wrapper = mount(AnalysisPreflightDialog, {
      attachTo: document.body,
      props: {
        open: true,
        preflight,
        selection,
        capabilities,
        loading: false,
        errorMessage: '',
        submitting: false,
      },
    })
    await flushPromises()

    const dialog = document.querySelector('[data-test="analysis-preflight-dialog"]')
    expect(dialog).not.toBeNull()
    expect(dialog?.textContent).toContain('分析前预检')
    expect(dialog?.textContent).toContain('云端后端')
    expect(dialog?.textContent).toContain('全文证据')
    expect(dialog?.textContent).toContain('86%')
    expect(dialog?.textContent).toContain('通用论文分析')
    expect(dialog?.textContent).toContain('12 页')
    expect(dialog?.textContent).toContain('3 条批注')
    expect(dialog?.textContent).not.toContain('私有论文确认')
    expect(document.querySelector('[data-test="analysis-backend"]')).not.toBeNull()
    expect(document.querySelector('[data-test="analysis-model"]')).not.toBeNull()
    expect(document.querySelector('[data-test="analysis-reasoning"]')).not.toBeNull()
    expect(document.querySelector('[data-test="analysis-language"]')).not.toBeNull()
    expect(document.querySelector('[data-test="analysis-network"]')).not.toBeNull()
    expect(document.querySelector('[data-test="analysis-visual-evidence"]')).not.toBeNull()
    expect(document.querySelector('[data-test="analysis-temporary-instruction"]')).not.toBeNull()
    expect(document.querySelector<HTMLButtonElement>('[data-test="start-analysis"]')?.disabled)
      .toBe(false)

    wrapper.unmount()
  })

  it('requires a refreshed preflight after changing backend and manual model', async () => {
    const wrapper = mount(AnalysisPreflightDialog, {
      attachTo: document.body,
      props: {
        open: true,
        preflight,
        selection,
        capabilities,
        loading: false,
        errorMessage: '',
        submitting: false,
      },
    })
    await flushPromises()

    const backend = document.querySelector<HTMLSelectElement>(
      '[data-test="analysis-backend"]',
    )
    expect(backend).not.toBeNull()
    backend!.value = 'ollama_local'
    backend!.dispatchEvent(new Event('change', { bubbles: true }))
    await flushPromises()

    const model = document.querySelector<HTMLInputElement>('[data-test="analysis-model"]')
    expect(model).not.toBeNull()
    model!.value = 'qwen-local'
    model!.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()

    expect(document.querySelector<HTMLButtonElement>('[data-test="start-analysis"]')?.disabled)
      .toBe(true)
    const refresh = document.querySelector<HTMLButtonElement>('[data-test="refresh-preflight"]')
    refresh?.click()
    await flushPromises()

    expect(wrapper.emitted('preview')).toHaveLength(1)
    expect(wrapper.emitted('preview')?.[0]?.[0]).toMatchObject({
      backend_id: 'ollama_local',
      model_id: 'qwen-local',
      external_research: false,
    })
    wrapper.unmount()
  })

  it('treats the visual-page policy as an explicit preflight choice', async () => {
    const wrapper = mount(AnalysisPreflightDialog, {
      attachTo: document.body,
      props: {
        open: true,
        preflight,
        selection,
        capabilities,
        loading: false,
        errorMessage: '',
        submitting: false,
      },
    })
    await flushPromises()

    const visual = document.querySelector<HTMLInputElement>(
      '[data-test="analysis-visual-evidence"]',
    )
    expect(visual?.checked).toBe(true)
    visual?.click()
    await flushPromises()
    expect(document.querySelector<HTMLButtonElement>('[data-test="start-analysis"]')?.disabled)
      .toBe(true)
    document.querySelector<HTMLButtonElement>('[data-test="refresh-preflight"]')?.click()
    await flushPromises()
    expect(wrapper.emitted('preview')?.[0]?.[0]).toMatchObject({
      include_visual_evidence: false,
    })
    wrapper.unmount()
  })
})
