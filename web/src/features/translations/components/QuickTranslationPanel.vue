<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import { computed } from 'vue'
import { storeToRefs } from 'pinia'

import type { ContextScope, ReasoningEffort } from '@/features/assistant/api'
import type { AssistantReaderContext } from '@/features/assistant/store'
import { usePreferencesStore } from '@/stores/preferences'
import {
  languageLabel,
  type TranslationCommand,
  type TranslationLanguage,
} from '../api'
import { useTranslationStore } from '../store'
import TranslationTaskCard from './TranslationTaskCard.vue'

const props = defineProps<{
  documentVersionId: string
  readerContext: AssistantReaderContext
  scope: ContextScope
  backendId: string
  modelId: string
  reasoningEffort: ReasoningEffort
  backendReady: boolean
}>()

const emit = defineEmits<{
  open: [artifactId: string]
  useAsContext: [context: string]
}>()

const store = useTranslationStore()
const preferences = usePreferencesStore()
const {
  active,
  agentLabel,
  artifact,
  diagnostics,
  error,
  pageRange,
  progress,
  segments,
  statusMessage,
  submittedJob,
  submitting,
  trackedJob,
} = storeToRefs(store)

const sourceLanguage = computed<TranslationLanguage>(
  () => preferences.snapshot?.translation_source_language ?? 'en',
)
const targetLanguage = computed<TranslationLanguage>(
  () => preferences.snapshot?.translation_target_language ?? 'zh-CN',
)
const status = computed(() => (
  trackedJob.value?.status
  ?? submittedJob.value?.status
  ?? artifact.value?.status
  ?? 'draft'
))
const cancelRequested = computed(() => (
  trackedJob.value?.cancel_requested
  ?? submittedJob.value?.cancel_requested
  ?? false
))
const contextSupported = computed(() => (
  props.scope !== 'annotations'
  && (props.scope !== 'selection' || props.readerContext.selection?.verified === true)
))
const canStart = computed(() => (
  props.backendReady
  && contextSupported.value
  && sourceLanguage.value !== targetLanguage.value
  && !active.value
  && !submitting.value
))

function selection(): TranslationCommand['context']['selection'] {
  const selected = props.readerContext.selection
  if (props.scope !== 'selection' || !selected) return null
  return {
    page_number: selected.pageNumber,
    page_width: selected.pageWidth,
    page_height: selected.pageHeight,
    quote_text: selected.quoteText,
    context_before: selected.contextBefore,
    context_after: selected.contextAfter,
    rects: selected.rects,
  }
}

function command(): TranslationCommand {
  const selectedPage = props.readerContext.selection?.pageNumber
  return {
    context: {
      document_version_id: props.documentVersionId,
      scope: props.scope,
      page_number: props.scope === 'full_document'
        ? null
        : props.scope === 'selection'
          ? (selectedPage ?? props.readerContext.currentPage)
          : props.readerContext.currentPage,
      selection: selection(),
      annotation_ids: [],
    },
    source_language: sourceLanguage.value,
    target_language: targetLanguage.value,
    agent: {
      backend_id: props.backendId,
      model_id: props.modelId.trim() || null,
      reasoning_effort: props.reasoningEffort,
      service_tier: 'flex',
    },
    user_terminology: [],
    network_policy: 'local_only',
  }
}

async function start(): Promise<void> {
  if (!canStart.value) return
  await store.create(command())
}

function useAsContext(): void {
  const context = segments.value
    .filter((segment) => segment.status === 'succeeded' && segment.translated_text)
    .map((segment) => t('原文：{0}\n译文：{1}', segment.source_text, segment.translated_text))
    .join('\n\n')
  if (context) emit('useAsContext', context)
}
</script>

<template>
  <section class="translation-panel" :aria-label="t('论文翻译')">
    <div class="translation-preflight">
      <span class="preflight-eyebrow">Translation</span>
      <h2 data-test="language-pair">
        {{ languageLabel(sourceLanguage) }} → {{ languageLabel(targetLanguage) }}
      </h2>
      <p>{{ t('按当前证据范围创建独立、可恢复的译文。只有点击开始后才会调用所选 Agent。') }}</p>
      <p v-if="scope === 'selection' && !readerContext.selection?.verified" class="translation-warning">
        {{ t('选区尚未通过 PDF 文本校验，请重新选择文字后再翻译。') }}
      </p>
      <p v-if="sourceLanguage === targetLanguage" class="translation-warning">
        {{ t('源语言与目标语言不能相同，请先在“阅读与语言”设置中调整。') }}
      </p>
      <button
        type="button"
        class="translation-start"
        data-test="translation-start"
        :disabled="!canStart"
        @click="start"
      >
        {{ submitting ? t('正在建立任务') : artifact ? t('创建新译文版本') : t('开始翻译') }}
      </button>
    </div>

    <p v-if="error" class="translation-error" role="alert">{{ error }}</p>
    <p v-if="statusMessage" class="translation-status" role="status">{{ statusMessage }}</p>

    <TranslationTaskCard
      v-if="artifact"
      :artifact-id="artifact.id"
      :source-language="artifact.source_language"
      :target-language="artifact.target_language"
      :scope="artifact.scope"
      :evidence-level="artifact.evidence_level"
      :agent-label="agentLabel"
      :status="status"
      :page-range="pageRange"
      :completed="progress.completed"
      :total="progress.total"
      :percent="progress.percent"
      :active="active"
      :cancel-requested="cancelRequested"
      :diagnostics="diagnostics"
      :busy="submitting"
      @open="emit('open', $event)"
      @cancel="store.cancel"
      @resume="store.resume"
      @use-as-context="useAsContext"
    />
  </section>
</template>

<style scoped>
.translation-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--color-paper);
}

.translation-preflight {
  display: grid;
  gap: 7px;
  padding: 16px 13px;
}

.preflight-eyebrow {
  color: var(--color-muted);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.translation-preflight h2 {
  margin: 0;
  color: var(--color-ink);
  font-family: var(--font-editorial);
  font-size: 18px;
  font-weight: 500;
}

.translation-preflight p,
.translation-status,
.translation-error {
  margin: 0;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  line-height: 1.55;
}

.translation-preflight .translation-warning,
.translation-error {
  color: var(--color-wine);
}

.translation-start {
  min-height: 36px;
  margin-top: 3px;
  border: 1px solid var(--color-forest);
  border-radius: var(--radius-sm);
  background: var(--color-forest);
  color: var(--color-paper);
  font: inherit;
  font-size: var(--type-meta);
  font-weight: 700;
  cursor: pointer;
}

.translation-start:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.translation-start:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

.translation-status,
.translation-error {
  padding: 7px 13px;
  border-top: 1px solid var(--color-line);
}

.translation-status {
  color: var(--color-forest);
}

.translation-error {
  background: var(--color-wine-soft);
}
</style>
