import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'

export type OnboardingOverview = components['schemas']['OnboardingOverview']
export type OnboardingSnapshot = components['schemas']['OnboardingSnapshot']
export type OnboardingStep = components['schemas']['OnboardingStep']
export type OnboardingStepRequest = components['schemas']['OnboardingStepRequest']
export type OnboardingStepStatus = components['schemas']['OnboardingStepStatus']
export type DataResetRequest = components['schemas']['DataResetRequest']
export type DataResetResponse = components['schemas']['DataResetResponse']
export type RuntimePathUpdate = components['schemas']['RuntimePathUpdate']
export type RuntimePathSnapshot = components['schemas']['RuntimePathSnapshot']
export type RuntimeRestartResponse = components['schemas']['RuntimeRestartResponse']

export interface ReadingPreferenceDraft {
  report_language: 'zh-CN' | 'en' | 'bilingual'
  translation_source_language: 'en' | 'zh-CN'
  translation_target_language: 'zh-CN' | 'en'
  default_agent_backend: 'codex_cli' | 'ollama_local' | 'ollama_cloud'
  reasoning_effort: 'minimal' | 'low' | 'medium' | 'high' | 'xhigh'
  theme: 'system' | 'light' | 'dark'
  timezone_mode: 'system' | 'custom'
  timezone_name: string | null
}

export const onboardingApi = {
  overview(): Promise<OnboardingOverview> {
    return apiJson<OnboardingOverview>('/api/v1/onboarding')
  },

  probe(idempotencyKey: string): Promise<OnboardingOverview> {
    return apiJson<OnboardingOverview>('/api/v1/onboarding/probe', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
    })
  },

  saveStep(
    step: OnboardingStep,
    payload: OnboardingStepRequest,
  ): Promise<OnboardingSnapshot> {
    return apiJson<OnboardingSnapshot>(
      `/api/v1/onboarding/steps/${encodeURIComponent(step)}`,
      {
        method: 'PUT',
        body: JSON.stringify(payload),
      },
    )
  },

  requestDataReset(payload: DataResetRequest): Promise<DataResetResponse> {
    return apiJson<DataResetResponse>('/api/v1/onboarding/data-reset', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  saveRuntimePaths(payload: RuntimePathUpdate): Promise<RuntimePathSnapshot> {
    return apiJson<RuntimePathSnapshot>('/api/v1/onboarding/runtime-config', {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  },

  requestRuntimeRestart(): Promise<RuntimeRestartResponse> {
    return apiJson<RuntimeRestartResponse>('/api/v1/onboarding/runtime-config/restart', {
      method: 'POST',
    })
  },
}
