<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import {
  PhBookOpenText,
  PhBookmarkSimple,
  PhFileText,
  PhSparkle,
} from '@phosphor-icons/vue'
import { ToggleGroupItem, ToggleGroupRoot } from 'reka-ui'

import type { MobilePanelView } from '@/features/reader/composables/useResponsiveReader'

defineProps<{
  modelValue: MobilePanelView
}>()

const emit = defineEmits<{
  'update:modelValue': [value: MobilePanelView]
}>()

const order: MobilePanelView[] = ['pdf', 'annotations', 'report', 'assistant']

function activate(value: MobilePanelView): void {
  emit('update:modelValue', value)
}

function move(event: KeyboardEvent, current: MobilePanelView): void {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  event.stopPropagation()
  const currentIndex = order.indexOf(current)
  const nextIndex = event.key === 'Home'
    ? 0
    : event.key === 'End'
      ? order.length - 1
      : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + order.length) % order.length
  const nextValue = order[nextIndex]
  const root = (event.currentTarget as HTMLElement).parentElement
  root?.querySelector<HTMLElement>(`[value="${nextValue}"]`)?.focus()
  activate(nextValue)
}
</script>

<template>
  <ToggleGroupRoot
    class="mobile-panel-tabs"
    type="single"
    orientation="horizontal"
    :model-value="modelValue"
    :roving-focus="true"
    :loop="true"
    :aria-label="t('移动端阅读面板')"
  >
    <ToggleGroupItem value="pdf" data-test="mobile-pdf-tab" aria-controls="reader-pdf-panel" @click="activate('pdf')" @keydown="move($event, 'pdf')">
      <PhBookOpenText :size="17" aria-hidden="true" /> PDF
    </ToggleGroupItem>
    <ToggleGroupItem value="annotations" data-test="mobile-annotations-tab" aria-controls="reader-agent-panel" @click="activate('annotations')" @keydown="move($event, 'annotations')">
      <PhBookmarkSimple :size="17" aria-hidden="true" /> {{ t('批注') }}
    </ToggleGroupItem>
    <ToggleGroupItem value="report" data-test="mobile-report-tab" aria-controls="reader-agent-panel" @click="activate('report')" @keydown="move($event, 'report')">
      <PhFileText :size="17" aria-hidden="true" /> {{ t('报告') }}
    </ToggleGroupItem>
    <ToggleGroupItem value="assistant" data-test="mobile-assistant-tab" aria-controls="reader-agent-panel" @click="activate('assistant')" @keydown="move($event, 'assistant')">
      <PhSparkle :size="17" aria-hidden="true" /> {{ t('助理') }}
    </ToggleGroupItem>
  </ToggleGroupRoot>
</template>

<style scoped>
.mobile-panel-tabs {
  display: none;
  border-bottom: 1px solid var(--color-line);
}

button {
  position: relative;
  min-height: 46px;
  flex: 1;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 0 4px;
  border: 0;
  background: transparent;
  color: var(--color-muted);
  font-size: var(--type-meta);
  cursor: pointer;
}

button[aria-pressed='true'] {
  color: var(--color-forest);
  font-weight: 700;
}

button[aria-pressed='true']::after {
  position: absolute;
  right: 12px;
  bottom: -1px;
  left: 12px;
  height: 2px;
  background: var(--color-forest);
  content: '';
}

button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

@media (max-width: 760px) {
  .mobile-panel-tabs {
    display: flex;
    background: var(--color-paper-deep);
  }

  button {
    display: inline-flex;
  }
}
</style>
