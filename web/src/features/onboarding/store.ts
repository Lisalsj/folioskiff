import { messageRef, settingsText as t } from '@/stores/locale'
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiResponseError } from '@/lib/json-api'

import {
  onboardingApi,
  type DataResetResponse,
  type OnboardingOverview,
  type OnboardingSnapshot,
  type OnboardingStep,
  type OnboardingStepStatus,
  type RuntimePathSnapshot,
  type RuntimePathUpdate,
  type RuntimeRestartResponse,
} from './api'

const ERROR_MESSAGES: Readonly<Record<string, string>> = {
  onboarding_transition_invalid: '请先完成当前必需步骤，再继续配置。',
  data_reset_confirmation_invalid: '确认文字不匹配，未安排数据重建。',
  runtime_config_invalid: '目录设置无效。请选择绝对路径，且论文库不能位于应用状态目录内。',
}

let fallbackRequestSequence = 0

function idempotencyKey(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  fallbackRequestSequence += 1
  return `onboarding-${Date.now().toString(36)}-${fallbackRequestSequence.toString(36)}`
}

function responseCode(error: unknown): string | undefined {
  if (!(error instanceof ApiResponseError)) return undefined
  if (typeof error.detail !== 'object' || error.detail === null) return undefined
  const code = Reflect.get(error.detail, 'code')
  return typeof code === 'string' ? code : undefined
}

export function onboardingErrorMessage(error: unknown): string {
  const code = responseCode(error)
  if (code && ERROR_MESSAGES[code]) return ERROR_MESSAGES[code]
  if (error instanceof ApiResponseError && error.status === 401) {
    return '本地会话已失效，请重新打开 FolioSkiff。'
  }
  if (error instanceof ApiResponseError && error.status === 403) {
    return '安全校验未通过，请刷新页面后重试。'
  }
  return '首次配置暂时不可用，请稍后重试。'
}

export const useOnboardingStore = defineStore('onboarding', () => {
  const overview = ref<OnboardingOverview>()
  const snapshot = ref<OnboardingSnapshot>()
  const loaded = ref(false)
  const loading = ref(false)
  const probing = ref(false)
  const saving = ref(false)
  const resetting = ref(false)
  const runtimeRestartRequired = ref(false)
  const error = messageRef(t)

  const currentStep = computed<OnboardingStep | undefined>(
    () => snapshot.value?.current_step,
  )
  const completed = computed(() => snapshot.value?.completed ?? false)
  const blockingIssues = computed(() => overview.value?.blocking_issues ?? [])

  function acceptOverview(value: OnboardingOverview): void {
    overview.value = value
    snapshot.value = value.state
    loaded.value = true
  }

  function acceptSnapshot(value: OnboardingSnapshot): void {
    snapshot.value = value
    if (overview.value) overview.value = { ...overview.value, state: value }
  }

  async function load(force = false): Promise<boolean> {
    if (loading.value) return false
    if (loaded.value && !force) return true
    loading.value = true
    error.value = ''
    try {
      acceptOverview(await onboardingApi.overview())
      return true
    } catch (cause) {
      error.value = onboardingErrorMessage(cause)
      return false
    } finally {
      loading.value = false
    }
  }

  async function probe(): Promise<boolean> {
    if (probing.value) return false
    probing.value = true
    error.value = ''
    try {
      acceptOverview(await onboardingApi.probe(idempotencyKey()))
      return true
    } catch (cause) {
      error.value = onboardingErrorMessage(cause)
      return false
    } finally {
      probing.value = false
    }
  }

  async function saveStep(
    step: OnboardingStep,
    status: OnboardingStepStatus,
  ): Promise<boolean> {
    if (saving.value) return false
    saving.value = true
    error.value = ''
    try {
      acceptSnapshot(await onboardingApi.saveStep(step, { status }))
      return true
    } catch (cause) {
      error.value = onboardingErrorMessage(cause)
      return false
    } finally {
      saving.value = false
    }
  }

  async function requestDataReset(confirmation: string): Promise<DataResetResponse | undefined> {
    if (resetting.value) return undefined
    resetting.value = true
    error.value = ''
    try {
      return await onboardingApi.requestDataReset({ confirmation })
    } catch (cause) {
      error.value = onboardingErrorMessage(cause)
      return undefined
    } finally {
      resetting.value = false
    }
  }

  async function saveRuntimePaths(
    payload: RuntimePathUpdate,
  ): Promise<RuntimePathSnapshot | undefined> {
    if (saving.value) return undefined
    saving.value = true
    error.value = ''
    try {
      const saved = await onboardingApi.saveRuntimePaths(payload)
      runtimeRestartRequired.value = saved.restart_required
      return saved
    } catch (cause) {
      error.value = onboardingErrorMessage(cause)
      return undefined
    } finally {
      saving.value = false
    }
  }

  async function requestRuntimeRestart(): Promise<RuntimeRestartResponse | undefined> {
    if (!runtimeRestartRequired.value || resetting.value) return undefined
    resetting.value = true
    error.value = ''
    try {
      return await onboardingApi.requestRuntimeRestart()
    } catch (cause) {
      error.value = onboardingErrorMessage(cause)
      return undefined
    } finally {
      resetting.value = false
    }
  }

  return {
    blockingIssues,
    completed,
    currentStep,
    error,
    load,
    loaded,
    loading,
    overview,
    probe,
    probing,
    requestDataReset,
    requestRuntimeRestart,
    resetting,
    runtimeRestartRequired,
    saveRuntimePaths,
    saveStep,
    saving,
    snapshot,
  }
})
