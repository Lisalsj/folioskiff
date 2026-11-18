<script setup lang="ts">
import { messageRef, settingsText as t, useLocale } from '@/stores/locale'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { PhSailboat } from '@phosphor-icons/vue'

import AgentSetupStep from '@/features/onboarding/components/AgentSetupStep.vue'
import OnboardingProgress from '@/features/onboarding/components/OnboardingProgress.vue'
import ReadingPreferencesStep from '@/features/onboarding/components/ReadingPreferencesStep.vue'
import SourceSetupStep from '@/features/onboarding/components/SourceSetupStep.vue'
import StoragePrivacyStep from '@/features/onboarding/components/StoragePrivacyStep.vue'
import '@/features/onboarding/components/steps.css'
import type {
  OnboardingStep,
  ReadingPreferenceDraft,
  RuntimePathUpdate,
} from '@/features/onboarding/api'
import { useOnboardingStore } from '@/features/onboarding/store'
import { usePreferencesStore } from '@/stores/preferences'

const store = useOnboardingStore()
const { switchTo, language, toggleLocale } = useLocale()
const preferences = usePreferencesStore()
const route = useRoute()
const router = useRouter()
const stepContainer = ref<HTMLElement>()
const stepError = messageRef(t)
const reviewStep = ref<OnboardingStep>()

const SETUP_STEPS: readonly OnboardingStep[] = [
  'storage_privacy',
  'agents',
  'sources',
  'reading_preferences',
]

const serverStep = computed<OnboardingStep>(
  () => store.currentStep ?? 'storage_privacy',
)
const currentStep = computed<OnboardingStep>(
  () => reviewStep.value ?? serverStep.value,
)
const canGoBack = computed(() => SETUP_STEPS.indexOf(currentStep.value) > 0)
const canSkip = computed(
  () => reviewStep.value === undefined
    && currentStep.value !== 'storage_privacy'
    && currentStep.value !== 'complete',
)
const visibleError = computed(() => {
  if (stepError.value) return stepError.value
  if (store.error) return store.error
  return route.query.status === 'unavailable'
    ? t('首次配置暂时不可用，请稍后重试。')
    : ''
})
const readingInitial = computed<Partial<ReadingPreferenceDraft>>(() => ({
  theme: preferences.theme,
  timezone_mode: preferences.timezoneMode,
  timezone_name: preferences.snapshot?.timezone_name ?? null,
  default_agent_backend: preferences.snapshot?.default_agent_backend ?? 'codex_cli',
  reasoning_effort: preferences.snapshot?.default_reasoning_effort ?? 'medium',
  report_language: preferences.snapshot?.report_language ?? 'zh-CN',
  translation_source_language: preferences.snapshot?.translation_source_language ?? 'en',
  translation_target_language: preferences.snapshot?.translation_target_language ?? 'zh-CN',
}))

async function retry(): Promise<void> {
  if (!await store.load(true)) return
  if (store.completed) {
    await router.replace('/')
    return
  }
  await router.replace('/onboarding')
}

async function saveCurrent(status: 'completed' | 'skipped'): Promise<void> {
  if (currentStep.value === 'complete') return
  if (!await store.saveStep(currentStep.value, status)) return
  reviewStep.value = undefined
  if (store.completed) await router.replace('/')
}

function goBack(): void {
  const index = SETUP_STEPS.indexOf(currentStep.value)
  if (index <= 0) return
  reviewStep.value = SETUP_STEPS[index - 1]
}

async function completeStorage(
  payload: RuntimePathUpdate & { privacy_acknowledged: true },
): Promise<void> {
  stepError.value = ''
  const runtime = await store.saveRuntimePaths(payload.data_root
    ? { data_root: payload.data_root }
    : {
        cache_root: payload.cache_root,
        library_root: payload.library_root,
      })
  if (!runtime) return
  if (!await store.saveStep('storage_privacy', 'completed')) return
  reviewStep.value = undefined
  if (runtime.restart_required) await store.requestRuntimeRestart()
}

async function completeReading(payload: ReadingPreferenceDraft): Promise<void> {
  stepError.value = ''
  const saved = await preferences.save({
    theme: payload.theme,
    timezone_mode: payload.timezone_mode,
    timezone_name: payload.timezone_name,
    default_agent_backend: payload.default_agent_backend,
    default_agent_model_id: null,
    default_reasoning_effort: payload.reasoning_effort,
    report_language: payload.report_language,
    translation_source_language: payload.translation_source_language,
    translation_target_language: payload.translation_target_language,
  })
  if (!saved) {
    stepError.value = preferences.error || '阅读偏好保存失败，请重试。'
    return
  }
  await saveCurrent('completed')
}

async function requestDataReset(confirmation: string): Promise<void> {
  await store.requestDataReset(confirmation)
}

watch(currentStep, async () => {
  await nextTick()
  stepContainer.value?.querySelector<HTMLElement>('h2')?.focus()
})

onMounted(() => {
  if (!store.loaded && route.query.status !== 'unavailable') void store.load()
})
</script>

<template>
  <div class="onboarding-page">
    <header class="onboarding-header">
      <div class="onboarding-brand" aria-label="FolioSkiff">
        <span aria-hidden="true"><PhSailboat :size="24" /></span>
        <strong>FolioSkiff</strong>
      </div>
      <div>
        <h1 id="onboarding-title">{{ t('首次配置页舟') }}</h1>
        <p>{{ t('数据默认保存在你的设备上。只有你主动选择云端 Agent 时，论文内容才会发送到相应服务。') }}</p>
      </div>
      <button class="setup-language quiet-button" type="button" :aria-label="language" @click="toggleLocale">
        {{ switchTo }}
      </button>
    </header>

    <nav :aria-label="t('首次配置步骤')">
      <OnboardingProgress
        :current="currentStep"
        :steps="store.snapshot?.steps ?? {}"
      />
    </nav>

    <main
      ref="stepContainer"
      aria-labelledby="onboarding-title"
      :aria-busy="store.loading || store.saving || store.resetting || store.probing"
    >
      <div v-if="visibleError" class="onboarding-error" role="alert">
        <strong>{{ t('无法读取配置状态') }}</strong>
        <p>{{ visibleError }}</p>
        <button type="button" data-test="retry-onboarding" :disabled="store.loading" @click="retry">
          {{ store.loading ? t('正在重试') : t('重试') }}
        </button>
      </div>

      <div v-else-if="!store.overview" class="onboarding-loading" role="status">
        {{ t('正在读取本地配置') }}
      </div>
      <StoragePrivacyStep
        v-else-if="currentStep === 'storage_privacy'"
        :capability="store.overview.storage"
        @complete="completeStorage"
        @request-reset="requestDataReset"
      />
      <AgentSetupStep
        v-else-if="currentStep === 'agents'"
        :agents="store.overview.agents"
        :probing="store.probing"
        @refresh="store.probe"
        @complete="saveCurrent('completed')"
      />
      <SourceSetupStep
        v-else-if="currentStep === 'sources'"
        :sources="store.overview.sources"
        @complete="saveCurrent('completed')"
      />
      <ReadingPreferencesStep
        v-else-if="currentStep === 'reading_preferences'"
        :initial="readingInitial"
        @complete="completeReading"
      />
    </main>

    <footer v-if="!visibleError && (canGoBack || canSkip)" class="onboarding-actions">
      <button
        type="button"
        class="secondary"
        data-test="back-step"
        :disabled="!canGoBack"
        @click="goBack"
      >
        {{ t('返回') }}
      </button>
      <button
        v-if="canSkip"
        type="button"
        class="secondary"
        data-test="skip-step"
        :disabled="store.saving"
        @click="saveCurrent('skipped')"
      >
        {{ t('稍后配置') }}
      </button>
    </footer>
  </div>
</template>

<style scoped>
.onboarding-page {
  width: min(960px, calc(100% - 32px));
  min-height: 100dvh;
  margin: 0 auto;
  display: grid;
  grid-template-rows: auto auto 1fr auto;
  gap: 24px;
  padding: clamp(24px, 5vw, 56px) 0 28px;
  color: var(--color-ink);
}

.onboarding-header {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: clamp(24px, 5vw, 60px);
  align-items: end;
}

.setup-language { justify-self: start; align-self: start; }

.onboarding-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--color-forest);
}

.onboarding-brand span {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-md);
  background: var(--color-forest);
  color: var(--color-on-accent);
  font-family: var(--font-editorial);
}

.onboarding-header h1 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: clamp(30px, 4vw, 46px);
  line-height: 1.08;
  letter-spacing: -0.035em;
}

.onboarding-header p,
.onboarding-step p,
.onboarding-error p {
  max-width: 68ch;
  color: var(--color-muted-strong);
  line-height: 1.75;
}

.onboarding-header p {
  margin: 12px 0 0;
}

main {
  min-width: 0;
  padding: clamp(24px, 5vw, 52px);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-lg);
  background: var(--color-paper-raised);
  box-shadow: 0 18px 48px color-mix(in srgb, var(--color-forest) 7%, transparent);
}

.onboarding-error strong {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: clamp(23px, 3vw, 32px);
}

.onboarding-error {
  padding-left: 18px;
  border-left: 3px solid var(--color-wine);
}

.onboarding-error button,
.onboarding-actions button {
  min-height: 42px;
  padding: 0 16px;
  border: 1px solid var(--color-line-strong);
  border-radius: var(--radius-sm);
  font: inherit;
  font-weight: 650;
  white-space: nowrap;
  cursor: pointer;
}

.onboarding-error button,
.onboarding-actions .secondary {
  background: var(--color-paper-raised);
  color: var(--color-ink);
}

.onboarding-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

@media (max-width: 680px) {
  .onboarding-page {
    width: min(100% - 24px, 960px);
    gap: 18px;
    padding-top: 20px;
  }

  .onboarding-header {
    grid-template-columns: 1fr;
    gap: 22px;
  }

  main {
    padding: 24px 20px;
  }

  .onboarding-actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }

  .onboarding-actions .primary {
    grid-column: 2;
  }
}
</style>
