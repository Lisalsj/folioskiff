<script setup lang="ts">
import { currentLocale, researchText as t } from '@/stores/locale'
import { computed } from 'vue'
import { PhPaperPlaneTilt, PhStopCircle } from '@phosphor-icons/vue'

import type { AssistantMode } from '../api'

type Mode = Extract<AssistantMode, 'ask' | 'explain'>

const props = defineProps<{
  modelValue: string
  mode: Mode
  canSend: boolean
  active: boolean
  submitting: boolean
  statusMessage: string
  cancelRequested?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  send: []
  cancel: []
}>()

const placeholder = computed(() => props.mode === 'explain'
  ? t('例如：解释这段证据如何支撑作者的结论。')
  : t('例如：作者的核心假设在哪些条件下可能失效？'))

function update(event: Event): void {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}

function shortcut(event: KeyboardEvent): void {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault()
    emit('send')
  }
}
</script>

<template>
  <section class="composer-shell">
    <p v-if="statusMessage" id="assistant-status" class="composer-status" role="status" aria-live="polite">
      {{ statusMessage }}
    </p>
    <form class="assistant-composer" @submit.prevent="emit('send')">
      <label for="assistant-input">{{ mode === 'explain' ? t('说明你希望解释什么') : t('询问当前论文') }}</label>
      <textarea
        id="assistant-input"
        :value="modelValue"
        :placeholder="placeholder"
        :disabled="active"
        aria-describedby="assistant-input-help assistant-status"
        maxlength="20000"
        rows="4"
        data-test="assistant-input"
        @input="update"
        @keydown="shortcut"
      />
      <div class="composer-actions">
        <span id="assistant-input-help">
          {{ modelValue.length.toLocaleString(currentLocale()) }} {{ t('/ 20,000，Ctrl/⌘ + Enter 发送') }}
        </span>
        <button
          v-if="active"
          type="button"
          class="cancel-button"
          data-test="assistant-cancel"
          :disabled="cancelRequested"
          @click="emit('cancel')"
        >
          <PhStopCircle :size="15" aria-hidden="true" />
          {{ cancelRequested ? t('正在取消') : t('取消') }}
        </button>
        <button
          v-else
          type="submit"
          class="send-button"
          data-test="assistant-send"
          :disabled="!canSend"
        >
          <PhPaperPlaneTilt :size="15" aria-hidden="true" />
          {{ submitting ? t('正在保存') : mode === 'explain' ? t('开始解释') : t('发送') }}
        </button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.composer-shell {
  display: grid;
  gap: 8px;
  padding: 11px 13px 13px;
  border-top: 1px solid var(--color-line);
  background: var(--color-paper-deep);
}

.composer-status {
  margin: 0;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  line-height: 1.5;
}

.assistant-composer {
  display: grid;
  gap: 7px;
}

.assistant-composer > label {
  color: var(--color-muted);
  font-size: var(--type-meta);
  font-weight: 700;
}

.assistant-composer textarea {
  width: 100%;
  min-height: 84px;
  resize: vertical;
  padding: 9px 10px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-raised);
  color: var(--color-ink);
  font: inherit;
  font-size: var(--type-meta);
  line-height: 1.55;
}

.assistant-composer textarea::placeholder {
  color: var(--color-muted);
}

.assistant-composer textarea:focus-visible,
.composer-actions button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.composer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.composer-actions > span {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.composer-actions button {
  min-height: 34px;
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  font-size: var(--type-meta);
  font-weight: 700;
  cursor: pointer;
}

.send-button {
  border: 1px solid var(--color-forest);
  background: var(--color-forest);
  color: var(--color-on-accent);
}

.cancel-button {
  border: 1px solid var(--color-line);
  background: var(--color-paper-raised);
  color: var(--color-wine);
}

.composer-actions button:active:not(:disabled) {
  transform: translateY(1px);
}

.composer-actions button:disabled,
.assistant-composer textarea:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}
</style>
