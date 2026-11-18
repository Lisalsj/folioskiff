import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { components } from '@/api/schema'
import AgentSetupStep from '@/features/onboarding/components/AgentSetupStep.vue'
import ReadingPreferencesStep from '@/features/onboarding/components/ReadingPreferencesStep.vue'
import StoragePrivacyStep from '@/features/onboarding/components/StoragePrivacyStep.vue'
import { useOnboardingStore } from '@/features/onboarding/store'
import { usePreferencesStore } from '@/stores/preferences'

import OnboardingPage from './OnboardingPage.vue'

type OnboardingOverview = components['schemas']['OnboardingOverview']

function overview(currentStep: components['schemas']['OnboardingStep']): OnboardingOverview {
  return {
    state: {
      completed: false,
      current_step: currentStep,
      privacy_acknowledged_at: null,
      steps: {
        storage_privacy: currentStep === 'storage_privacy' ? 'pending' : 'completed',
        agents: 'pending',
        sources: 'pending',
        reading_preferences: 'pending',
      },
      updated_at: '2026-08-01T00:00:00Z',
    },
    storage: {
      writable: true,
      state_writable: true,
      cache_writable: true,
      library_writable: true,
      state_root: 'C:\\state',
      cache_root: 'C:\\cache',
      library_root: 'D:\\papers',
      backups_root: 'D:\\papers\\backups',
    },
    agents: [],
    sources: [],
    retrieval: {
      lexical_enabled: true,
      semantic_enabled: false,
      model_state: 'not_installed',
      degradation_reasons: [],
    },
    blocking_issues: [],
  }
}

async function mountPage(currentStep: components['schemas']['OnboardingStep']) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useOnboardingStore()
  const preferences = usePreferencesStore()
  const value = overview(currentStep)
  store.overview = value
  store.snapshot = value.state
  store.loaded = true
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<h1>阅读简报</h1>' } },
      { path: '/onboarding', component: OnboardingPage },
    ],
  })
  await router.push('/onboarding')
  await router.isReady()
  const wrapper = mount(OnboardingPage, { global: { plugins: [pinia, router] } })
  return { preferences, store, wrapper }
}

describe('OnboardingPage', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('renders a focused quiet-library shell without global navigation', async () => {
    const { wrapper } = await mountPage('storage_privacy')

    expect(wrapper.get('h1').text()).toBe('首次配置页舟')
    expect(wrapper.get('main').attributes('aria-labelledby')).toBe('onboarding-title')
    expect(wrapper.find('nav[aria-label="首次配置步骤"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('数据默认保存在你的设备上')
    expect(wrapper.find('.sidebar').exists()).toBe(false)
    expect(wrapper.find('[data-test="skip-step"]').exists()).toBe(false)
    expect(wrapper.findComponent(StoragePrivacyStep).exists()).toBe(true)
  })

  it('offers a later action only on optional steps', async () => {
    const { wrapper } = await mountPage('agents')

    expect(wrapper.get('[data-test="skip-step"]').text()).toContain('稍后配置')
    expect(wrapper.get('[aria-current="step"]').text()).toContain('AI 能力')
    expect(wrapper.findComponent(AgentSetupStep).exists()).toBe(true)
  })

  it('lets users review an earlier completed step and return through its continue action', async () => {
    const { store, wrapper } = await mountPage('agents')
    vi.spyOn(store, 'saveRuntimePaths').mockResolvedValue({
      state_root: 'C:\\state',
      cache_root: 'C:\\cache',
      library_root: 'D:\\papers',
      backups_root: 'D:\\papers\\backups',
      restart_required: false,
    })
    vi.spyOn(store, 'saveStep').mockResolvedValue(true)

    await wrapper.get('[data-test="back-step"]').trigger('click')

    expect(wrapper.findComponent(StoragePrivacyStep).exists()).toBe(true)
    expect(wrapper.find('[data-test="skip-step"]').exists()).toBe(false)

    wrapper.findComponent(StoragePrivacyStep).vm.$emit('complete', {
      cache_root: 'C:\\cache',
      library_root: 'D:\\papers',
      privacy_acknowledged: true,
    })
    await flushPromises()

    expect(wrapper.findComponent(AgentSetupStep).exists()).toBe(true)
  })

  it('shows a recoverable load error and retries explicitly', async () => {
    const { store, wrapper } = await mountPage('storage_privacy')
    store.error = '首次配置暂时不可用，请稍后重试。'
    const load = vi.spyOn(store, 'load').mockResolvedValue(true)
    await wrapper.vm.$nextTick()
    await wrapper.get('[data-test="retry-onboarding"]').trigger('click')

    expect(wrapper.get('[role="alert"]').text()).toContain('首次配置暂时不可用')
    expect(load).toHaveBeenCalledWith(true)
  })

  it('persists valid runtime paths and the required step before restarting', async () => {
    const { store, wrapper } = await mountPage('storage_privacy')
    const savePaths = vi.spyOn(store, 'saveRuntimePaths').mockResolvedValue({
      state_root: 'C:\\state',
      cache_root: 'D:\\cache',
      library_root: 'D:\\papers',
      backups_root: 'D:\\papers\\backups',
      restart_required: true,
    })
    const saveStep = vi.spyOn(store, 'saveStep').mockResolvedValue(true)
    const restart = vi.spyOn(store, 'requestRuntimeRestart').mockResolvedValue({
      request_id: 'f311776c-1815-4599-85f7-04c0ded0c837',
      restart_required: true,
    })

    wrapper.findComponent(StoragePrivacyStep).vm.$emit('complete', {
      cache_root: 'D:\\cache',
      library_root: 'D:\\papers',
      privacy_acknowledged: true,
    })
    await flushPromises()

    expect(savePaths).toHaveBeenCalledWith({
      cache_root: 'D:\\cache',
      library_root: 'D:\\papers',
    })
    expect(saveStep).toHaveBeenCalledWith('storage_privacy', 'completed')
    expect(restart).toHaveBeenCalledOnce()
    expect(savePaths.mock.invocationCallOrder[0]).toBeLessThan(saveStep.mock.invocationCallOrder[0]!)
    expect(saveStep.mock.invocationCallOrder[0]).toBeLessThan(restart.mock.invocationCallOrder[0]!)
  })

  it('persists the complete Agent, report, translation, and reading defaults', async () => {
    const { preferences, store, wrapper } = await mountPage('reading_preferences')
    const savePreferences = vi.spyOn(preferences, 'save').mockResolvedValue(true)
    const saveStep = vi.spyOn(store, 'saveStep').mockResolvedValue(true)

    wrapper.findComponent(ReadingPreferencesStep).vm.$emit('complete', {
      report_language: 'bilingual',
      translation_source_language: 'en',
      translation_target_language: 'zh-CN',
      default_agent_backend: 'ollama_local',
      reasoning_effort: 'high',
      theme: 'dark',
      timezone_mode: 'custom',
      timezone_name: 'Asia/Shanghai',
    })
    await flushPromises()

    expect(savePreferences).toHaveBeenCalledWith({
      theme: 'dark',
      timezone_mode: 'custom',
      timezone_name: 'Asia/Shanghai',
      default_agent_backend: 'ollama_local',
      default_agent_model_id: null,
      default_reasoning_effort: 'high',
      report_language: 'bilingual',
      translation_source_language: 'en',
      translation_target_language: 'zh-CN',
    })
    expect(saveStep).toHaveBeenCalledWith('reading_preferences', 'completed')
    expect(savePreferences.mock.invocationCallOrder[0]).toBeLessThan(
      saveStep.mock.invocationCallOrder[0]!,
    )
  })
})
