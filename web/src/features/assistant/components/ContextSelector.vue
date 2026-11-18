<script setup lang="ts">
import { researchText as t } from '@/stores/locale'
import { computed } from 'vue'

import type { PdfTextSelection } from '@/lib/annotation-geometry'
import type { ContextScope } from '../api'
import type { AssistantReaderAnnotation } from '../store'

const props = withDefaults(defineProps<{
  modelValue: ContextScope
  currentPage: number
  pageCount: number
  selection: PdfTextSelection | null
  annotations: AssistantReaderAnnotation[]
  selectedAnnotationIds: string[]
  sectionFallback?: boolean
  translationMode?: boolean
  disabled?: boolean
}>(), {
  sectionFallback: false,
  translationMode: false,
  disabled: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: ContextScope]
  'update:selectedAnnotationIds': [value: string[]]
}>()

const selectionExcerpt = computed(() => {
  const value = props.selection?.quoteText.trim() ?? ''
  if (!value) return ''
  return value.length > 76 ? `${value.slice(0, 76)}…` : value
})

function updateScope(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  if (
    value === 'selection'
    || value === 'current_page'
    || value === 'current_section'
    || value === 'full_document'
    || value === 'annotations'
  ) emit('update:modelValue', value)
}

function updateAnnotation(id: string, event: Event): void {
  const checked = (event.target as HTMLInputElement).checked
  const next = new Set(props.selectedAnnotationIds)
  if (checked) next.add(id)
  else next.delete(id)
  emit('update:selectedAnnotationIds', [...next].sort())
}
</script>

<template>
  <section class="context-selector" data-test="assistant-context" aria-labelledby="context-label">
    <label id="context-label" for="assistant-context-choice">{{ t('证据范围') }}</label>
    <select
      id="assistant-context-choice"
      :value="modelValue"
      :disabled="disabled"
      data-test="assistant-context-choice"
      @change="updateScope"
    >
      <option value="selection" :disabled="!selection">{{ t('选中文本') }}</option>
      <option value="current_page">{{ t('当前页，第') }} {{ currentPage }} {{ t('页') }}</option>
      <option value="current_section">{{ t('当前章节') }}</option>
      <option value="full_document">{{ t('全文') }}</option>
      <option value="annotations" :disabled="translationMode || annotations.length === 0">{{ t('批注') }}</option>
    </select>

    <p v-if="modelValue === 'selection' && selection" class="scope-note">
      <strong>{{ t('第') }} {{ selection.pageNumber }} {{ t('页') }}</strong>
      <span>{{ selectionExcerpt }}</span>
      <small v-if="!selection.verified">{{ t('选区未通过页内文本校验，提交后会明确降级。') }}</small>
    </p>
    <p v-else-if="modelValue === 'current_page'" class="scope-note">
      {{ t('只读取第') }} {{ currentPage }} {{ t('页，不会自动扩大范围。') }}
    </p>
    <p v-else-if="modelValue === 'current_section'" class="scope-note">
      {{ t('由本地标题结构确定章节。章节识别不稳定时回退到当前页。') }}
    </p>
    <p v-else-if="modelValue === 'full_document'" class="scope-note">
      {{ t('读取当前 PDF 的可用全文，过长内容会显示截断警告。') }}
    </p>

    <fieldset v-if="modelValue === 'annotations'" class="annotation-choices">
      <legend>{{ t('选择要交给 Assistant 的批注') }}</legend>
      <label v-for="annotation in annotations" :key="annotation.id">
        <input
          type="checkbox"
          :checked="selectedAnnotationIds.includes(annotation.id)"
          :disabled="disabled"
          :data-test="`assistant-annotation-${annotation.id}`"
          @change="updateAnnotation(annotation.id, $event)"
        >
        <span>
          <strong>{{ t('第') }} {{ annotation.pageNumber }} {{ t('页') }}</strong>
          {{ annotation.label }}
        </span>
      </label>
      <small v-if="selectedAnnotationIds.length === 0">{{ t('至少选择一条批注后才能发送。') }}</small>
    </fieldset>

    <p
      v-if="sectionFallback && modelValue === 'current_section'"
      class="fallback-note"
      role="status"
    >
      {{ t('最近一次章节范围识别已降级为当前页证据。') }}
    </p>
    <span class="page-count">{{ t('当前文档共') }} {{ pageCount || 0 }} {{ t('页') }}</span>
  </section>
</template>

<style scoped>
.context-selector {
  display: grid;
  gap: 7px;
}

.context-selector > label,
.annotation-choices legend {
  color: var(--color-muted);
  font-size: var(--type-meta);
  font-weight: 700;
}

.context-selector select {
  width: 100%;
  min-height: 36px;
  padding: 0 30px 0 10px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-raised);
  color: var(--color-ink);
  font: inherit;
  font-size: var(--type-meta);
}

.context-selector select:focus-visible,
.annotation-choices input:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.scope-note,
.fallback-note {
  margin: 0;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  line-height: 1.55;
}

.scope-note strong,
.scope-note span,
.scope-note small {
  display: block;
}

.scope-note span {
  margin-top: 3px;
  color: var(--color-ink);
}

.scope-note small,
.fallback-note {
  margin-top: 3px;
  color: var(--color-wine);
}

.annotation-choices {
  max-height: 156px;
  display: grid;
  gap: 3px;
  margin: 0;
  padding: 8px;
  overflow: auto;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
}

.annotation-choices label {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: start;
  gap: 7px;
  padding: 6px;
  border-radius: calc(var(--radius-sm) - 2px);
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  line-height: 1.45;
}

.annotation-choices label:hover {
  background: var(--color-forest-hover-soft);
}

.annotation-choices input {
  margin: 2px 0 0;
  accent-color: var(--color-forest);
}

.annotation-choices strong {
  margin-right: 4px;
  color: var(--color-forest);
}

.annotation-choices small,
.page-count {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.page-count {
  justify-self: end;
}
</style>
