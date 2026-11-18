import { messageRef, settingsText as t } from '@/stores/locale'
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'

export type ThemeMode = components['schemas']['UserPreferencesSnapshot']['theme']
export type TimezoneMode = components['schemas']['UserPreferencesSnapshot']['timezone_mode']
export type PreferencesSnapshot = components['schemas']['UserPreferencesSnapshot']
export type PreferencesUpdate = components['schemas']['UserPreferencesUpdate']

export const THEME_CACHE_KEY = 'paper-reader.theme'
const FALLBACK_TIMEZONE = 'Asia/Shanghai'

function isTheme(value: unknown): value is ThemeMode {
  return value === 'system' || value === 'light' || value === 'dark'
}

export function readCachedTheme(storage: Storage = window.localStorage): ThemeMode {
  try {
    const value = storage.getItem(THEME_CACHE_KEY)
    return isTheme(value) ? value : 'system'
  } catch {
    return 'system'
  }
}

export function applyTheme(
  theme: ThemeMode,
  root: HTMLElement = document.documentElement,
): void {
  root.dataset.theme = theme
}

export function applyCachedTheme(): ThemeMode {
  const theme = readCachedTheme()
  applyTheme(theme)
  return theme
}

function cacheTheme(theme: ThemeMode): void {
  try {
    window.localStorage.setItem(THEME_CACHE_KEY, theme)
  } catch {
    // A disabled localStorage must not prevent the server preference from applying.
  }
}

function browserTimezone(): string {
  try {
    const value = Intl.DateTimeFormat().resolvedOptions().timeZone
    return value || FALLBACK_TIMEZONE
  } catch {
    return FALLBACK_TIMEZONE
  }
}

export const usePreferencesStore = defineStore('preferences', () => {
  const snapshot = ref<PreferencesSnapshot>()
  const cachedTheme = ref<ThemeMode>(readCachedTheme())
  const loading = ref(false)
  const saving = ref(false)
  const error = messageRef(t)

  const theme = computed<ThemeMode>(() => snapshot.value?.theme ?? cachedTheme.value)
  const timezoneMode = computed<TimezoneMode>(() => snapshot.value?.timezone_mode ?? 'system')
  const resolvedTimezone = computed(
    () => snapshot.value?.resolved_timezone ?? browserTimezone(),
  )

  function accept(value: PreferencesSnapshot): void {
    snapshot.value = value
    cachedTheme.value = value.theme
    cacheTheme(value.theme)
    applyTheme(value.theme)
  }

  async function load(): Promise<boolean> {
    if (loading.value) return false
    loading.value = true
    error.value = ''
    try {
      accept(await apiJson<PreferencesSnapshot>('/api/v1/settings/preferences'))
      return true
    } catch {
      error.value = '暂时无法读取时区与主题设置；已保留本机显示。'
      return false
    } finally {
      loading.value = false
    }
  }

  async function save(payload: PreferencesUpdate): Promise<boolean> {
    if (saving.value) return false
    saving.value = true
    error.value = ''
    try {
      const saved = await apiJson<PreferencesSnapshot>('/api/v1/settings/preferences', {
        method: 'PUT',
        body: JSON.stringify(payload),
      })
      accept(saved)
      return true
    } catch {
      error.value = '保存失败，原有时区与主题没有改变。'
      return false
    } finally {
      saving.value = false
    }
  }

  async function cycleTheme(): Promise<boolean> {
    if (!snapshot.value) {
      if (!await load() || !snapshot.value) return false
    }
    const current = theme.value
    const next: ThemeMode = current === 'system'
      ? 'light'
      : current === 'light'
        ? 'dark'
        : 'system'
    const currentSnapshot = snapshot.value
    if (!currentSnapshot) return false
    return save({
      timezone_mode: currentSnapshot.timezone_mode,
      timezone_name: currentSnapshot.timezone_name,
      theme: next,
      default_agent_backend: currentSnapshot.default_agent_backend,
      default_agent_model_id: currentSnapshot.default_agent_model_id,
      default_reasoning_effort: currentSnapshot.default_reasoning_effort,
      report_language: currentSnapshot.report_language,
      translation_source_language: currentSnapshot.translation_source_language,
      translation_target_language: currentSnapshot.translation_target_language,
    })
  }

  return {
    error,
    loading,
    resolvedTimezone,
    save,
    cycleTheme,
    saving,
    snapshot,
    theme,
    timezoneMode,
    load,
  }
})
