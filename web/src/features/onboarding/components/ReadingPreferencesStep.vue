<script setup lang="ts">
import { settingsText as t } from '@/stores/locale'
import { reactive, watch } from 'vue'

import type { ReadingPreferenceDraft } from '../api'

const props = defineProps<{ initial?: Partial<ReadingPreferenceDraft> }>()
const emit = defineEmits<{ complete: [payload: ReadingPreferenceDraft] }>()
const form = reactive<ReadingPreferenceDraft>({
  report_language: props.initial?.report_language ?? 'zh-CN',
  translation_source_language: props.initial?.translation_source_language ?? 'en',
  translation_target_language: props.initial?.translation_target_language ?? 'zh-CN',
  default_agent_backend: props.initial?.default_agent_backend ?? 'codex_cli',
  reasoning_effort: props.initial?.reasoning_effort ?? 'medium',
  theme: props.initial?.theme ?? 'system',
  timezone_mode: props.initial?.timezone_mode ?? 'system',
  timezone_name: props.initial?.timezone_name ?? null,
})

watch(
  () => form.translation_source_language,
  (source) => {
    if (source === form.translation_target_language) {
      form.translation_target_language = source === 'en' ? 'zh-CN' : 'en'
    }
  },
)
watch(
  () => form.translation_target_language,
  (target) => {
    if (target === form.translation_source_language) {
      form.translation_source_language = target === 'en' ? 'zh-CN' : 'en'
    }
  },
)

function submit(): void {
  emit('complete', {
    ...form,
    timezone_name: form.timezone_mode === 'custom' ? form.timezone_name?.trim() || null : null,
  })
}
</script>

<template>
  <form class="setup-step" @submit.prevent="submit">
    <header>
      <h2 tabindex="-1">{{ t('设置阅读偏好') }}</h2>
      <p>{{ t('默认以中文阅读英文论文。报告、翻译、Agent、主题和时区都可以在设置中随时修改。') }}</p>
    </header>

    <div class="setup-grid">
      <label class="setup-field">
        <span>{{ t('报告语言') }}</span>
        <select v-model="form.report_language" data-test="report-language">
          <option value="zh-CN">{{ t('中文') }}</option>
          <option value="en">{{ t('英文') }}</option>
          <option value="bilingual">{{ t('中英双语') }}</option>
        </select>
      </label>
      <label class="setup-field">
        <span>{{ t('默认 Agent') }}</span>
        <select v-model="form.default_agent_backend" data-test="default-agent">
          <option value="codex_cli">Codex CLI</option>
          <option value="ollama_local">Ollama Local</option>
          <option value="ollama_cloud">Ollama Cloud</option>
        </select>
      </label>
      <label class="setup-field">
        <span>{{ t('翻译源语言') }}</span>
        <select v-model="form.translation_source_language" data-test="translation-source">
          <option value="en">{{ t('英文') }}</option>
          <option value="zh-CN">{{ t('中文') }}</option>
        </select>
      </label>
      <label class="setup-field">
        <span>{{ t('翻译目标语言') }}</span>
        <select v-model="form.translation_target_language" data-test="translation-target">
          <option value="zh-CN">{{ t('中文') }}</option>
          <option value="en">{{ t('英文') }}</option>
        </select>
      </label>
      <label class="setup-field">
        <span>{{ t('推理强度') }}</span>
        <select v-model="form.reasoning_effort" data-test="reasoning-effort">
          <option value="minimal">{{ t('最简') }}</option>
          <option value="low">{{ t('低') }}</option>
          <option value="medium">{{ t('中') }}</option>
          <option value="high">{{ t('高') }}</option>
          <option value="xhigh">{{ t('极高') }}</option>
        </select>
      </label>
      <label class="setup-field">
        <span>{{ t('主题') }}</span>
        <select v-model="form.theme" data-test="theme">
          <option value="system">{{ t('跟随系统') }}</option>
          <option value="light">{{ t('浅色') }}</option>
          <option value="dark">{{ t('深色') }}</option>
        </select>
      </label>
      <label class="setup-field">
        <span>{{ t('时区') }}</span>
        <select v-model="form.timezone_mode" data-test="timezone-mode">
          <option value="system">{{ t('跟随系统') }}</option>
          <option value="custom">{{ t('自定义') }}</option>
        </select>
      </label>
      <label v-if="form.timezone_mode === 'custom'" class="setup-field">
        <span>{{ t('IANA 时区') }}</span>
        <input v-model="form.timezone_name" data-test="timezone-name" placeholder="Asia/Shanghai" />
      </label>
    </div>

    <div class="setup-button-row">
      <button type="submit" class="setup-button primary">{{ t('完成配置') }}</button>
    </div>
  </form>
</template>
