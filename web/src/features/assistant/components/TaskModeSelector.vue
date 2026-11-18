<script setup lang="ts">
import { researchText as t } from '@/stores/locale'
import { nextTick } from 'vue'
import { PhBinoculars, PhChatCircleText, PhLightbulb, PhTranslate } from '@phosphor-icons/vue'
import { ToggleGroupItem, ToggleGroupRoot } from 'reka-ui'

import type { AssistantMode } from '../api'

type Mode = Extract<AssistantMode, 'ask' | 'explain' | 'related'> | 'translate'

defineProps<{
  modelValue: Mode
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Mode]
}>()

function update(value: unknown): void {
  if (value === 'ask' || value === 'explain' || value === 'translate' || value === 'related') {
    emit('update:modelValue', value)
  }
}

function keyboardSelect(value: Mode, event: KeyboardEvent): void {
  event.preventDefault()
  event.stopPropagation()
  emit('update:modelValue', value)
  const root = (event.currentTarget as HTMLElement).parentElement
  void nextTick(() => {
    root?.querySelector<HTMLElement>(`button[value="${value}"]`)?.focus()
  })
}
</script>

<template>
  <ToggleGroupRoot
    class="mode-selector"
    type="single"
    orientation="horizontal"
    :model-value="modelValue"
    :disabled="disabled"
    :roving-focus="true"
    :loop="true"
    data-test="assistant-mode"
    :aria-label="t('Assistant 任务类型')"
    @update:model-value="update"
  >
    <ToggleGroupItem
      value="ask"
      data-test="assistant-mode-ask"
      @keydown.left="keyboardSelect('related', $event)"
      @keydown.right="keyboardSelect('explain', $event)"
    >
      <PhChatCircleText :size="15" aria-hidden="true" />
      {{ t('提问') }}
    </ToggleGroupItem>
    <ToggleGroupItem
      value="explain"
      data-test="assistant-mode-explain"
      @keydown.left="keyboardSelect('ask', $event)"
      @keydown.right="keyboardSelect('translate', $event)"
    >
      <PhLightbulb :size="15" aria-hidden="true" />
      {{ t('解释') }}
    </ToggleGroupItem>
    <ToggleGroupItem
      value="translate"
      data-mode="translate"
      data-test="assistant-mode-translate"
      @keydown.left="keyboardSelect('explain', $event)"
      @keydown.right="keyboardSelect('related', $event)"
    >
      <PhTranslate :size="15" aria-hidden="true" />
      {{ t('翻译') }}
    </ToggleGroupItem>
    <ToggleGroupItem
      value="related"
      data-mode="related"
      data-test="assistant-mode-related"
      @keydown.left="keyboardSelect('translate', $event)"
      @keydown.right="keyboardSelect('ask', $event)"
    >
      <PhBinoculars :size="15" aria-hidden="true" />
      {{ t('相关推荐') }}
    </ToggleGroupItem>
  </ToggleGroupRoot>
</template>

<style scoped>
.mode-selector {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  padding: 3px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper);
}

.mode-selector button {
  min-height: 34px;
  display: inline-flex;
  min-width: 0;
  align-items: center;
  justify-content: center;
  gap: 4px;
  border: 0;
  border-radius: calc(var(--radius-sm) - 2px);
  background: transparent;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  white-space: nowrap;
  cursor: pointer;
}

.mode-selector button svg {
  flex: 0 0 auto;
}

.mode-selector button[data-state='on'] {
  background: var(--color-forest-soft);
  color: var(--color-forest);
  font-weight: 700;
}

.mode-selector button:hover:not(:disabled) {
  color: var(--color-forest);
}

.mode-selector button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.mode-selector button:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}
</style>
