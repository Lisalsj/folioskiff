<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { TabsContent, TabsList, TabsRoot, TabsTrigger } from 'reka-ui'

import type { AgentTab } from '@/features/reader/composables/useResponsiveReader'

defineProps<{
  activeTab: AgentTab
  mobileActive: boolean
  reportVisited: boolean
  assistantVisited: boolean
}>()

const emit = defineEmits<{
  'update:activeTab': [value: AgentTab]
  ready: [element: HTMLElement | null]
}>()
const root = ref<HTMLElement>()
const order: AgentTab[] = ['annotations', 'report', 'assistant']

function activate(value: AgentTab): void {
  emit('update:activeTab', value)
}

function move(event: KeyboardEvent, current: AgentTab): void {
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
  root.value?.querySelector<HTMLElement>(`[data-tab="${nextValue}"]`)?.focus()
  activate(nextValue)
}

onMounted(() => emit('ready', root.value ?? null))
onBeforeUnmount(() => emit('ready', null))
</script>

<template>
  <aside
    id="reader-agent-panel"
    ref="root"
    class="agent-column"
    data-test="agent-column"
    :data-mobile-active="mobileActive"
  >
    <TabsRoot
      class="agent-tabs-root"
      orientation="horizontal"
      activation-mode="automatic"
      :model-value="activeTab"
      :unmount-on-hide="false"
    >
      <TabsList class="agent-tabs" :aria-label="t('论文 Agent')">
        <TabsTrigger value="annotations" data-tab="annotations" data-test="agent-annotations-tab" @click="activate('annotations')" @keydown="move($event, 'annotations')">{{ t('批注') }}</TabsTrigger>
        <TabsTrigger value="report" data-tab="report" data-test="agent-report-tab" @click="activate('report')" @keydown="move($event, 'report')">{{ t('分析报告') }}</TabsTrigger>
        <TabsTrigger value="assistant" data-tab="assistant" data-test="agent-assistant-tab" @click="activate('assistant')" @keydown="move($event, 'assistant')">{{ t('论文助理') }}</TabsTrigger>
      </TabsList>

      <TabsContent value="annotations" class="annotation-tab-panel">
        <slot name="annotations" />
      </TabsContent>
      <TabsContent v-if="reportVisited" value="report" force-mount class="analysis-tab-panel">
        <slot name="report" />
      </TabsContent>
      <TabsContent v-if="assistantVisited" value="assistant" force-mount class="assistant-tab-panel">
        <slot name="assistant" />
      </TabsContent>
    </TabsRoot>
  </aside>
</template>

<style scoped>
.agent-column {
  min-width: 0;
  min-height: 0;
  overflow: auto;
  border-left: 1px solid var(--color-line);
  background: var(--color-paper-deep);
}

.agent-tabs-root,
.annotation-tab-panel,
.analysis-tab-panel,
.assistant-tab-panel {
  min-height: 100%;
}

.agent-tabs {
  position: sticky;
  z-index: 2;
  top: 0;
  display: flex;
  border-bottom: 1px solid var(--color-line);
  background: var(--color-paper-deep);
}

.agent-tabs button {
  position: relative;
  min-height: 54px;
  flex: 1;
  padding: 0 10px;
  border: 0;
  background: transparent;
  color: var(--color-muted);
  font-size: var(--type-meta);
  cursor: pointer;
}

.agent-tabs button[aria-selected='true'] {
  color: var(--color-forest);
  font-weight: 700;
}

.agent-tabs button[aria-selected='true']::after {
  position: absolute;
  right: 12px;
  bottom: -1px;
  left: 12px;
  height: 2px;
  background: var(--color-forest);
  content: '';
}

.agent-tabs button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

@media (max-width: 760px) {
  .agent-column {
    min-height: 0;
    grid-area: 1 / 1;
    border-left: 0;
  }

  .agent-column[data-mobile-active='false'] {
    display: none;
  }

  .agent-tabs {
    display: none;
  }
}
</style>
