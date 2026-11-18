<script setup lang="ts">
import { researchText as t } from '@/stores/locale'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import {
  PhCaretDown,
  PhCloud,
  PhComputerTower,
  PhShieldCheck,
  PhSparkle,
} from '@phosphor-icons/vue'
import { CollapsibleContent, CollapsibleRoot, CollapsibleTrigger } from 'reka-ui'

import AssistantComposer from './AssistantComposer.vue'
import AssistantTimeline from './AssistantTimeline.vue'
import ContextSelector from './ContextSelector.vue'
import EvidenceDrawer from './EvidenceDrawer.vue'
import TaskModeSelector from './TaskModeSelector.vue'
import { useAssistantStore, type AssistantReaderContext } from '../store'
import RelatedTaskCard from '@/features/related-resources/components/RelatedTaskCard.vue'
import type { AgentSelection, ContextSelection } from '@/features/related-resources/api'
import { useRelatedResourcesStore } from '@/features/related-resources/store'
import QuickTranslationPanel from '@/features/translations/components/QuickTranslationPanel.vue'
import { useTranslationStore } from '@/features/translations/store'

type ReaderTaskMode = 'ask' | 'explain' | 'translate' | 'related'

const props = defineProps<{
  workId: string
  documentVersionId: string
  documentContentSha256: string
  readerContext: AssistantReaderContext
  initialMode?: ReaderTaskMode
}>()

const emit = defineEmits<{
  navigatePage: [pageNumber: number]
  openTranslation: [artifactId: string]
}>()

const store = useAssistantStore()
const translations = useTranslationStore()
const related = useRelatedResourcesStore()
const {
  active,
  activeJob,
  activeTaskId,
  backendId,
  backendReady,
  capabilities,
  draft,
  fragments,
  latestCheckpoint,
  latestContext,
  loadError,
  loading,
  messages,
  mode,
  modelId,
  reasoningEffort,
  scope,
  selectedAnnotationIds,
  selectedCapability,
  statusMessage,
  submitting,
  tasks,
} = storeToRefs(store)
const {
  active: translationActive,
} = storeToRefs(translations)
const {
  active: relatedActive,
  phase: relatedPhase,
} = storeToRefs(related)
const modelOpen = ref(false)
const taskMode = ref<ReaderTaskMode>(props.initialMode ?? 'ask')

const canSend = computed(() => store.canSend(props.readerContext))
const currentScopeLabel = computed(() => {
  if (scope.value === 'selection') {
    return props.readerContext.selection
      ? t('选中文本，第 {0} 页', props.readerContext.selection.pageNumber)
      : t('选中文本不可用')
  }
  if (scope.value === 'current_page') return t('当前页，第 {0} 页', props.readerContext.currentPage)
  if (scope.value === 'current_section') return t('当前章节，定位于第 {0} 页', props.readerContext.currentPage)
  if (scope.value === 'annotations') return t('已选 {0} 条批注', selectedAnnotationIds.value.length)
  return t('全文，共 {0} 页', props.readerContext.pageCount)
})
const locationLabel = computed(() => selectedCapability.value?.location === 'local'
  ? t('本机 Agent')
  : t('云端 Agent'))
const relatedContext = computed<ContextSelection | null>(() => {
  try {
    return store.buildContext(props.documentVersionId, props.readerContext)
  } catch {
    return null
  }
})
const relatedAgent = computed<AgentSelection | null>(() => backendReady.value ? {
  backend_id: backendId.value,
  model_id: modelId.value.trim() || null,
  reasoning_effort: reasoningEffort.value,
  service_tier: 'flex',
} : null)

function updateDraft(value: string): void {
  store.setDraft(value)
}

function updateBackend(event: Event): void {
  store.selectBackend((event.target as HTMLSelectElement).value)
}

function updateTaskMode(value: ReaderTaskMode): void {
  taskMode.value = value
  if (value === 'translate') {
    if (scope.value === 'annotations') {
      scope.value = props.readerContext.selection ? 'selection' : 'current_page'
    }
    return
  }
  if (value === 'related') return
  mode.value = value
}

function useTranslationAsContext(context: string): void {
  store.setDraft(t('请基于以下已确认的双语选区继续分析：\n\n{0}\n\n', context))
  taskMode.value = 'ask'
  mode.value = 'ask'
}

watch(
  () => [props.workId, props.documentVersionId, props.documentContentSha256] as const,
  ([workId, documentVersionId, documentContentSha256]) => {
    void store.initialize({ workId, documentVersionId, documentContentSha256 })
    related.initialize({ workId, documentVersionId, documentContentSha256 })
    translations.clear()
    updateTaskMode(props.initialMode ?? 'ask')
    void translations.restoreForWork(workId, documentVersionId)
  },
  { immediate: true },
)

watch(
  () => props.initialMode,
  (value) => {
    if (value) updateTaskMode(value)
  },
)

watch(
  () => props.readerContext.selection,
  (selection) => {
    if (selection) {
      if (scope.value === 'current_page' || scope.value === 'selection') scope.value = 'selection'
    } else if (scope.value === 'selection') {
      scope.value = 'current_page'
    }
  },
  { immediate: true },
)

watch(
  () => props.readerContext.annotations.map((annotation) => annotation.id).join(':'),
  () => {
    const available = new Set(props.readerContext.annotations.map((annotation) => annotation.id))
    selectedAnnotationIds.value = selectedAnnotationIds.value.filter((id) => available.has(id))
    if (scope.value === 'annotations' && selectedAnnotationIds.value.length === 0) {
      scope.value = props.readerContext.selection ? 'selection' : 'current_page'
    }
  },
)

onBeforeUnmount(() => {
  store.dispose()
  related.dispose()
})
</script>

<template>
  <section class="research-assistant" data-test="research-assistant">
    <header class="assistant-header">
      <div class="assistant-title">
        <PhSparkle :size="18" aria-hidden="true" />
        <div>
          <h2>{{ t('论文 Assistant') }}</h2>
          <span>{{ t('显式范围，可恢复历史') }}</span>
        </div>
      </div>
      <div
        v-if="selectedCapability"
        class="agent-location"
        :class="selectedCapability.location"
        data-test="assistant-location"
      >
        <PhComputerTower v-if="selectedCapability.location === 'local'" :size="13" aria-hidden="true" />
        <PhCloud v-else :size="13" aria-hidden="true" />
        {{ locationLabel }}
      </div>
    </header>

    <section class="assistant-controls" :aria-label="t('Assistant 请求设置')">
      <TaskModeSelector
        :model-value="taskMode"
        :disabled="active || translationActive || relatedActive"
        @update:model-value="updateTaskMode"
      />
      <ContextSelector
        v-model="scope"
        v-model:selected-annotation-ids="selectedAnnotationIds"
        :current-page="readerContext.currentPage"
        :page-count="readerContext.pageCount"
        :selection="readerContext.selection"
        :annotations="readerContext.annotations"
        :section-fallback="latestContext?.section_fallback ?? false"
        :translation-mode="taskMode === 'translate'"
        :disabled="active || translationActive || relatedActive || (taskMode === 'related' && relatedPhase !== 'idle')"
      />
      <div
        class="network-boundary"
        :data-test="taskMode === 'translate'
          ? 'translation-evidence-boundary'
          : taskMode === 'related'
            ? 'related-evidence-boundary'
            : 'assistant-network-boundary'"
      >
        <PhShieldCheck :size="15" aria-hidden="true" />
        <span>
          <strong>{{ taskMode === 'related' ? t('先本地拟词，再显式选择检索边界') : t('仅使用当前论文与本地资料') }}</strong>
          <small v-if="taskMode === 'translate'">{{ t('翻译不启用外部网络研究') }}</small>
          <small v-else-if="taskMode === 'related'">{{ t('切换模式不会联网；运行前必须确认检索意图') }}</small>
          <small v-else>{{ t('提问和解释不启用外部网络研究') }}</small>
        </span>
      </div>

      <CollapsibleRoot v-model:open="modelOpen" class="model-settings">
        <CollapsibleTrigger data-test="assistant-model-trigger">
          <span>{{ t('模型设置') }}</span>
          <span>{{ selectedCapability?.display_name || t('未找到可用后端') }}</span>
          <PhCaretDown :size="13" aria-hidden="true" />
        </CollapsibleTrigger>
        <CollapsibleContent class="model-settings-content">
          <label>
            <span>{{ t('后端') }}</span>
            <select :value="backendId" :disabled="active || translationActive || relatedActive" @change="updateBackend">
              <option v-if="capabilities.length === 0" value="">{{ t('暂无可用后端') }}</option>
              <option
                v-for="capability in capabilities"
                :key="capability.backend_id"
                :value="capability.backend_id"
                :disabled="!capability.available"
              >
                {{ capability.display_name }}{{ capability.available ? '' : t('（不可用）') }}
              </option>
            </select>
          </label>
          <label>
            <span>{{ t('模型') }}</span>
            <input
              v-model="modelId"
              :list="`assistant-models-${workId}`"
              :placeholder="selectedCapability?.supports_default_model ? t('使用后端默认模型') : t('填写模型 ID')"
              :disabled="active || translationActive || relatedActive || !backendId"
              autocomplete="off"
            >
            <datalist :id="`assistant-models-${workId}`">
              <option
                v-for="model in selectedCapability?.models ?? []"
                :key="model.model_id"
                :value="model.model_id"
              >
                {{ model.display_name }}
              </option>
            </datalist>
          </label>
          <label>
            <span>{{ t('推理强度') }}</span>
            <select v-model="reasoningEffort" :disabled="active || translationActive || relatedActive">
              <option value="minimal">{{ t('最少') }}</option>
              <option value="low">{{ t('低') }}</option>
              <option value="medium">{{ t('中') }}</option>
              <option value="high">{{ t('高') }}</option>
              <option value="xhigh">{{ t('很高') }}</option>
            </select>
          </label>
        </CollapsibleContent>
      </CollapsibleRoot>
      <p v-if="!backendReady && !loading && taskMode !== 'related'" class="backend-warning" role="alert">
        {{ t('当前后端不可用或缺少模型。请展开模型设置，或前往设置页完成配置。') }}
      </p>
    </section>

    <p v-if="loadError" class="assistant-error" role="alert">{{ loadError }}</p>

    <AssistantTimeline
      v-if="taskMode === 'ask' || taskMode === 'explain'"
      :loading="loading"
      :messages="messages"
      :tasks="tasks"
      :fragments="fragments"
      :active-task-id="activeTaskId"
      :current-document-content-sha256="documentContentSha256"
      :latest-checkpoint="latestCheckpoint"
      @navigate-page="emit('navigatePage', $event)"
      @retry="store.retry"
    />

    <EvidenceDrawer
      v-if="taskMode === 'ask' || taskMode === 'explain'"
      :current-scope-label="currentScopeLabel"
      :context="latestContext"
    />

    <AssistantComposer
      v-if="taskMode === 'ask' || taskMode === 'explain'"
      :model-value="draft"
      :mode="mode"
      :can-send="canSend"
      :active="active"
      :submitting="submitting"
      :status-message="statusMessage"
      :cancel-requested="activeJob?.cancel_requested"
      @update:model-value="updateDraft"
      @send="store.send(readerContext)"
      @cancel="store.cancel"
    />

    <QuickTranslationPanel
      v-else-if="taskMode === 'translate'"
      :document-version-id="documentVersionId"
      :reader-context="readerContext"
      :scope="scope"
      :backend-id="backendId"
      :model-id="modelId"
      :reasoning-effort="reasoningEffort"
      :backend-ready="backendReady"
      @open="emit('openTranslation', $event)"
      @use-as-context="useTranslationAsContext"
    />

    <RelatedTaskCard
      v-else
      :context="relatedContext"
      :agent="relatedAgent"
      :backend-ready="backendReady"
      :supports-external-research="selectedCapability?.supports_external_research ?? false"
    />
  </section>
</template>

<style scoped>
.research-assistant {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  color: var(--color-ink);
}

.assistant-header {
  min-height: 50px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 13px;
  border-bottom: 1px solid var(--color-line);
}

.assistant-title,
.assistant-title > div,
.agent-location,
.network-boundary,
.network-boundary > span {
  display: flex;
  align-items: center;
}

.assistant-title {
  min-width: 0;
  gap: 7px;
  color: var(--color-forest);
}

.assistant-title > div,
.network-boundary > span {
  min-width: 0;
  align-items: flex-start;
  flex-direction: column;
}

.assistant-title h2,
.assistant-title span,
.network-boundary strong,
.network-boundary small {
  display: block;
}

.assistant-title h2 {
  margin: 0;
  color: var(--color-ink);
  font-family: var(--font-editorial);
  font-size: 14px;
  font-weight: 500;
}

.assistant-title span,
.network-boundary small {
  margin-top: 1px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.agent-location {
  flex: none;
  gap: 4px;
  padding: 4px 6px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
}

.agent-location.cloud {
  border-color: var(--color-wine);
  color: var(--color-wine);
}

.assistant-controls {
  display: grid;
  gap: 10px;
  padding: 11px 13px;
  border-bottom: 1px solid var(--color-line);
  background: var(--color-paper-deep);
}

.network-boundary {
  align-items: flex-start;
  gap: 7px;
  color: var(--color-forest);
}

.network-boundary strong {
  font-size: var(--type-meta);
}

.model-settings {
  border-top: 1px solid var(--color-line);
}

.model-settings > button {
  width: 100%;
  min-height: 34px;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 7px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-muted);
  font-size: var(--type-meta);
  text-align: left;
  cursor: pointer;
}

.model-settings > button span:nth-child(2) {
  overflow: hidden;
  color: var(--color-muted-strong);
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-settings > button svg {
  transition: transform 140ms ease;
}

.model-settings > button[aria-expanded='true'] svg {
  transform: rotate(180deg);
}

.model-settings > button:focus-visible,
.model-settings-content :is(select, input):focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.model-settings-content {
  display: grid;
  gap: 8px;
  padding: 2px 0 4px;
}

.model-settings-content label {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.model-settings-content select,
.model-settings-content input {
  min-width: 0;
  min-height: 32px;
  padding: 0 8px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-raised);
  color: var(--color-ink);
  font: inherit;
  font-size: var(--type-meta);
}

.backend-warning,
.assistant-error {
  margin: 0;
  color: var(--color-wine);
  font-size: var(--type-meta);
  line-height: 1.5;
}

.assistant-error {
  padding: 8px 13px;
  background: var(--color-wine-soft);
}

:deep(.assistant-timeline) {
  flex: 1;
}

@media (prefers-reduced-motion: reduce) {
  .model-settings > button svg {
    transition: none;
  }
}
</style>
