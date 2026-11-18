<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import { computed } from 'vue'
import { PhScan, PhWarningCircle } from '@phosphor-icons/vue'

import type { BilingualMode, TranslationSegment } from '../api'

const props = withDefaults(defineProps<{
  segment: TranslationSegment
  mode: BilingualMode
  active?: boolean
  sourceLanguage?: string
  targetLanguage?: string
}>(), {
  active: false,
  sourceLanguage: 'en',
  targetLanguage: 'zh-CN',
})

const emit = defineEmits<{
  select: [segmentId: string]
}>()

const ocrLabel = computed(() => props.segment.ocr_confidence === null
  ? ''
  : `OCR ${Math.round(props.segment.ocr_confidence * 100)}%`)
const isStale = computed(() => props.segment.status === 'stale')
</script>

<template>
  <article
    :id="`translation-${segment.segment_id}`"
    class="bilingual-segment"
    :class="[`mode-${mode}`, { active, stale: isStale }]"
    :data-segment="segment.segment_id"
    :data-status="segment.status"
  >
    <header>
      <button
        type="button"
        class="segment-anchor"
        :aria-current="active ? 'location' : undefined"
        @click="emit('select', segment.segment_id)"
      >
        {{ t('段落') }} {{ segment.segment_index }}
      </button>
      <span v-if="ocrLabel" class="ocr-confidence">
        <PhScan :size="13" aria-hidden="true" />
        {{ ocrLabel }}
      </span>
      <span v-if="isStale" class="stale-label">
        <PhWarningCircle :size="13" aria-hidden="true" />
        {{ t('术语已变化，待重新翻译') }}
      </span>
    </header>
    <div class="segment-columns">
      <section v-if="mode !== 'target'" data-side="source" :lang="sourceLanguage">
        <span class="side-label">{{ t('原文') }}</span>
        <p>{{ segment.source_text }}</p>
      </section>
      <section v-if="mode !== 'source'" data-side="target" :lang="targetLanguage">
        <span class="side-label">{{ t('译文') }}</span>
        <p v-if="segment.translated_text">{{ segment.translated_text }}</p>
        <p v-else class="missing-translation">{{ t('此段尚无可用译文。') }}</p>
      </section>
    </div>
    <p v-if="segment.diagnostic" class="segment-diagnostic" role="alert">
      {{ segment.diagnostic }}
    </p>
  </article>
</template>

<style scoped>
.bilingual-segment {
  padding: 18px 0 20px;
  border-bottom: 1px solid var(--color-line);
  scroll-margin-top: 118px;
}

.bilingual-segment.active {
  outline: 2px solid var(--color-focus);
  outline-offset: 5px;
}

.bilingual-segment > header,
.ocr-confidence,
.stale-label {
  display: flex;
  align-items: center;
}

.segment-anchor {
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.segment-anchor:hover {
  color: var(--color-forest);
}

.segment-anchor:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

.bilingual-segment > header {
  min-height: 20px;
  gap: 9px;
  margin-bottom: 8px;
  color: var(--color-muted);
  font-size: var(--type-meta);
  font-variant-numeric: tabular-nums;
}

.ocr-confidence,
.stale-label {
  gap: 4px;
}

.ocr-confidence {
  color: var(--color-muted-strong);
}

.stale-label {
  color: var(--color-wine);
}

.segment-columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 28px;
}

.mode-source .segment-columns,
.mode-target .segment-columns {
  grid-template-columns: minmax(0, 720px);
  justify-content: center;
}

.segment-columns section {
  min-width: 0;
}

.side-label {
  display: block;
  margin-bottom: 6px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.08em;
}

.segment-columns p {
  margin: 0;
  color: var(--color-ink);
  font-family: var(--font-editorial);
  font-size: clamp(15px, 1.4vw, 18px);
  line-height: 1.8;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.segment-columns [data-side='source'] p {
  color: var(--color-muted-strong);
  font-size: clamp(14px, 1.25vw, 17px);
}

.segment-columns .missing-translation {
  color: var(--color-muted);
  font-family: var(--font-interface);
  font-size: 12px;
  font-style: italic;
}

.segment-diagnostic {
  margin: 10px 0 0;
  padding: 7px 9px;
  border-left: 2px solid var(--color-wine);
  background: var(--color-wine-soft);
  color: var(--color-wine);
  font-size: var(--type-meta);
  line-height: 1.5;
}

@media (max-width: 760px) {
  .segment-columns {
    grid-template-columns: 1fr;
    gap: 16px;
  }
}
</style>
