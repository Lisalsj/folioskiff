<script setup lang="ts">
import { messageRef, settingsText as t } from '@/stores/locale'
import { computed, onMounted, ref, watch } from 'vue'
import { PhDesktop, PhMoon, PhSun } from '@phosphor-icons/vue'

import type { ThemeMode, TimezoneMode } from '@/stores/preferences'
import { usePreferencesStore } from '@/stores/preferences'
import { useSettingsDirtyState } from '@/pages/settings/settings-dirty-state'

const preferences = usePreferencesStore()
const timezoneMode = ref<TimezoneMode>('system')
const timezoneName = ref('')
const theme = ref<ThemeMode>('system')
const statusMessage = messageRef(t)
const formError = messageRef(t)
const dirtyState = useSettingsDirtyState()

const timezoneOptions = computed(() => {
  const intl = Intl as typeof Intl & { supportedValuesOf?: (key: 'timeZone') => string[] }
  try {
    return intl.supportedValuesOf?.('timeZone') ?? [
      'Asia/Shanghai',
      'Asia/Tokyo',
      'Europe/Berlin',
      'Europe/London',
      'America/New_York',
      'America/Los_Angeles',
      'Pacific/Auckland',
    ]
  } catch {
    return []
  }
})
const isDirty = computed(() => {
  const snapshot = preferences.snapshot
  if (!snapshot) return false
  const selectedTimezone = timezoneMode.value === 'custom' ? timezoneName.value.trim() || null : null
  return timezoneMode.value !== snapshot.timezone_mode
    || selectedTimezone !== snapshot.timezone_name
    || theme.value !== snapshot.theme
})

function syncForm(): void {
  const value = preferences.snapshot
  if (!value) return
  timezoneMode.value = value.timezone_mode
  timezoneName.value = value.timezone_name ?? value.system_timezone
  theme.value = value.theme
}

async function load(): Promise<void> {
  await preferences.load()
  syncForm()
}

async function save(): Promise<void> {
  statusMessage.value = ''
  formError.value = ''
  const selectedTimezone = timezoneName.value.trim()
  if (timezoneMode.value === 'custom' && !selectedTimezone) {
    formError.value = '请填写一个 IANA 时区，例如 Asia/Shanghai。'
    return
  }
  const snapshot = preferences.snapshot
  if (!snapshot) {
    formError.value = '偏好尚未加载，请刷新后重试。'
    return
  }
  const saved = await preferences.save({
    timezone_mode: timezoneMode.value,
    timezone_name: timezoneMode.value === 'custom' ? selectedTimezone : null,
    theme: theme.value,
    default_agent_backend: snapshot.default_agent_backend,
    default_agent_model_id: snapshot.default_agent_model_id,
    default_reasoning_effort: snapshot.default_reasoning_effort,
    report_language: snapshot.report_language,
    translation_source_language: snapshot.translation_source_language,
    translation_target_language: snapshot.translation_target_language,
  })
  if (saved) {
    syncForm()
    statusMessage.value = '时区与主题已保存。新计划会使用当前解析时区，旧计划保持不变。'
  }
}

onMounted(() => void load())
watch(isDirty, (value) => dirtyState.setDirty(value))
</script>

<template>
  <section class="general-settings" aria-labelledby="general-settings-title">
    <div class="section-heading">
      <div>
        <p>{{ t('阅读环境') }}</p>
        <h2 id="general-settings-title">{{ t('时区与主题') }}</h2>
      </div>
      <span>{{ preferences.resolvedTimezone }}</span>
    </div>

    <p v-if="preferences.loading" class="quiet-state" role="status">{{ t('正在读取本机偏好…') }}</p>
    <form v-else @submit.prevent="save">
      <fieldset>
        <legend>{{ t('自然月与定时任务时区') }}</legend>
        <label class="choice-row">
          <input
            v-model="timezoneMode"
            data-test="timezone-system"
            type="radio"
            value="system"
          />
          <span>
            <strong>{{ t('跟随系统时区') }}</strong>
            {{ t('当前解析为') }} {{ preferences.snapshot?.system_timezone ?? preferences.resolvedTimezone }}
          </span>
        </label>
        <label class="choice-row">
          <input
            v-model="timezoneMode"
            data-test="timezone-custom"
            type="radio"
            value="custom"
          />
          <span><strong>{{ t('自选时区') }}</strong>{{ t('使用 IANA 标准名称') }}</span>
        </label>
        <label v-if="timezoneMode === 'custom'" class="timezone-field">
          {{ t('IANA 时区') }}
          <input
            v-model="timezoneName"
            data-test="timezone-name"
            list="paper-reader-timezones"
            autocomplete="off"
            :placeholder="t('例如 Europe/Berlin')"
          />
          <datalist id="paper-reader-timezones">
            <option v-for="option in timezoneOptions" :key="option" :value="option" />
          </datalist>
        </label>
        <p
          v-if="preferences.snapshot?.system_timezone_fallback"
          data-test="timezone-fallback"
          class="fallback-note"
          role="status"
        >
          {{ t('操作系统时区无法可靠解析，当前安全回退到 Asia/Shanghai；你可以在上方自选时区。') }}
        </p>
      </fieldset>

      <fieldset>
        <legend>{{ t('界面主题') }}</legend>
        <div class="theme-grid">
          <label>
            <input v-model="theme" data-test="theme-system" type="radio" value="system" />
            <PhDesktop :size="19" aria-hidden="true" />
            <span><strong>{{ t('跟随系统') }}</strong>{{ t('自动匹配浅色或深色') }}</span>
          </label>
          <label>
            <input v-model="theme" data-test="theme-light" type="radio" value="light" />
            <PhSun :size="19" aria-hidden="true" />
            <span><strong>{{ t('浅色') }}</strong>{{ t('暖纸色安静图书馆') }}</span>
          </label>
          <label>
            <input v-model="theme" data-test="theme-dark" type="radio" value="dark" />
            <PhMoon :size="19" aria-hidden="true" />
            <span><strong>{{ t('深色') }}</strong>{{ t('低照度阅读环境') }}</span>
          </label>
        </div>
        <p>{{ t('PDF 页面始终保持原始白底，不会随深色主题反色。') }}</p>
      </fieldset>

      <div class="form-footer">
        <div>
          <p v-if="formError || preferences.error" role="alert">{{ formError || preferences.error }}</p>
          <p data-test="general-settings-status" role="status" aria-live="polite">{{ statusMessage }}</p>
        </div>
        <button
          type="submit"
          data-test="save-general-settings"
          :disabled="preferences.saving"
        >
          {{ preferences.saving ? t('正在保存') : t('保存阅读环境') }}
        </button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.general-settings { margin-top: 54px; }
.section-heading { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding-bottom: 16px; border-bottom: 2px solid var(--color-forest); }
.section-heading p { margin: 0 0 8px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: .12em; }
.section-heading h2 { margin: 0; font-family: var(--font-editorial); font-size: 28px; font-weight: 500; }
.section-heading > span { color: var(--color-muted); font-size: var(--type-meta); }
form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 34px 54px; padding-top: 25px; }
fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }
legend { margin-bottom: 13px; color: var(--color-muted); font-size: var(--type-meta); font-weight: 750; letter-spacing: .06em; }
.choice-row { display: grid; grid-template-columns: 18px minmax(0, 1fr); gap: 10px; padding: 13px 0; border-bottom: 1px solid var(--color-line); cursor: pointer; }
.choice-row input, .theme-grid input { margin: 3px 0 0; accent-color: var(--color-forest); }
.choice-row span, .theme-grid span { color: var(--color-muted); font-size: var(--type-meta); line-height: 1.5; }
.choice-row strong, .theme-grid strong { display: block; color: var(--color-ink); font-size: 12px; }
.timezone-field { display: grid; gap: 6px; margin-top: 15px; color: var(--color-muted-strong); font-size: var(--type-meta); font-weight: 650; }
.timezone-field input { min-height: 39px; padding: 0 11px; border: 1px solid var(--color-line); border-radius: 7px; background: var(--color-paper-raised); color: var(--color-ink); }
.theme-grid { display: grid; gap: 7px; }
.theme-grid label { display: grid; grid-template-columns: 18px 24px minmax(0, 1fr); gap: 8px; align-items: start; padding: 11px; border: 1px solid var(--color-line); border-radius: var(--radius-sm); cursor: pointer; }
.theme-grid label:has(input:checked) { border-color: var(--color-forest); background: var(--color-forest-soft); }
.theme-grid label:has(input:checked) span { color: var(--color-muted-strong); }
fieldset > p, .fallback-note { margin: 12px 0 0; color: var(--color-muted); font-size: var(--type-meta); line-height: 1.6; }
.fallback-note { padding: 9px 10px; border-left: 3px solid var(--color-wine); background: var(--color-wine-soft); color: var(--color-muted-strong); }
.form-footer { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between; gap: 20px; padding-top: 18px; border-top: 1px solid var(--color-line); }
.form-footer p { min-height: 1.2em; margin: 0; color: var(--color-danger); font-size: var(--type-meta); }
.form-footer p[role='status'] { color: var(--color-forest); }
.form-footer button { min-height: 40px; padding: 0 15px; border: 0; border-radius: 7px; background: var(--color-forest); color: var(--color-on-accent); font-size: var(--type-meta); font-weight: 700; cursor: pointer; }
input:focus-visible, button:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
button:disabled { opacity: .55; cursor: wait; }
.quiet-state { padding: 25px 0; color: var(--color-muted); }
@media (max-width: 760px) { form { grid-template-columns: 1fr; } .form-footer { grid-column: 1; align-items: stretch; flex-direction: column; } .form-footer button { width: 100%; } }
</style>
