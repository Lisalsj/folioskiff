<script setup lang="ts">
import { discoveryText as t } from '@/stores/locale'
import { ref, watch } from 'vue'
import { PhArrowRight, PhFlask, PhInfo, PhSparkle, PhWarning } from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import PaperFeedbackControls from '@/components/PaperFeedbackControls.vue'

type Paper = components['schemas']['RecommendationPaperResponse']
type LibraryState = components['schemas']['LibraryStateResponse']
type Score = components['schemas']['ScoreSummaryResponse']

const props = defineProps<{ paper: Paper }>()
const emit = defineEmits<{
  analyze: [paper: Paper]
  evidence: [snapshotId: string]
  stateUpdated: [state: LibraryState]
}>()

const currentPaper = ref<Paper>(copyPaper(props.paper))

watch(
  () => props.paper,
  (paper) => { currentPaper.value = copyPaper(paper) },
  { deep: true },
)

function copyPaper(paper: Paper): Paper {
  return { ...paper, library_state: { ...paper.library_state } }
}

function scoreValue(score: Score): string {
  return score.coverage <= 0 ? t('未知') : String(Math.round(score.score))
}

function authorsLabel(authors: string[]): string {
  return authors.length ? authors.slice(0, 4).join('、') : t('作者未知')
}

function warningLabel(code: string): string {
  return {
    retracted: t('已撤稿'),
    expression_of_concern: t('关注声明'),
    major_correction: t('重大更正'),
    corrected: t('已更正'),
  }[code] ?? code
}

function explorationLabel(code: string): string {
  return {
    adjacent_topic: t('相邻主题'),
    new_author: t('新作者'),
    different_source: t('不同来源'),
    underrepresented_source: t('补充来源'),
  }[code] ?? code
}

function updateState(state: LibraryState): void {
  currentPaper.value = {
    ...currentPaper.value,
    library_state: {
      work_id: state.work_id,
      favorite: state.favorite,
      feedback: state.feedback,
      feedback_reasons: state.feedback_reasons,
      pinned: state.pinned,
      reading_progress: state.reading_progress,
      analysis_state: state.analysis_state,
      persisted: state.persisted,
      created_at: state.created_at,
      updated_at: state.updated_at,
    },
  }
  emit('stateUpdated', state)
}
</script>

<template>
  <article class="recommendation-card" :class="{ historical: currentPaper.historical_match }">
    <div
      v-if="currentPaper.evidence_summary.integrity_warning_codes.length"
      data-test="integrity-warning"
      class="integrity-warning"
      role="alert"
    >
      <PhWarning :size="16" weight="fill" aria-hidden="true" />
      <strong>{{ currentPaper.evidence_summary.integrity_warning_codes.map(warningLabel).join(' · ') }}</strong>
      <span>{{ t('完整性警告不会被高分抵消') }}</span>
    </div>

    <div class="card-grid">
      <div class="paper-copy">
        <div class="paper-flags">
          <span v-if="currentPaper.default_position">NO. {{ currentPaper.default_position }}</span>
          <span v-if="currentPaper.channel === 'exploration'" class="exploration"><PhSparkle :size="12" aria-hidden="true" /> {{ t('探索') }}</span>
          <span v-if="currentPaper.historical_match">{{ t('历史匹配') }}</span>
        </div>
        <h2>{{ currentPaper.title }}</h2>
        <p class="authors">{{ authorsLabel(currentPaper.authors) }}</p>
        <p class="source-line">
          {{ currentPaper.sources.join(' · ') || t('来源未知') }}
          <span v-if="currentPaper.doi">DOI {{ currentPaper.doi }}</span>
        </p>
        <p
          v-if="currentPaper.evidence_summary.exploration_reasons.length"
          data-test="exploration-reason"
          class="exploration-reason"
        >
          <PhFlask :size="14" aria-hidden="true" />
          {{ t('探索理由：') }}{{ currentPaper.evidence_summary.exploration_reasons.map(explorationLabel).join('、') }}
        </p>
      </div>

      <dl class="score-ledger" :aria-label="t('论文评分摘要')">
        <div data-test="score-relevance"><dt>{{ t('相关性') }}</dt><dd>{{ scoreValue(currentPaper.scores.relevance) }}</dd></div>
        <div data-test="score-quality"><dt>{{ t('质量') }}</dt><dd>{{ scoreValue(currentPaper.scores.quality) }}</dd></div>
        <div data-test="score-confidence"><dt>{{ t('置信度') }}</dt><dd>{{ Math.round(currentPaper.scores.composite.confidence) }}</dd></div>
        <div data-test="score-coverage"><dt>{{ t('数据覆盖') }}</dt><dd>{{ Math.round(currentPaper.scores.composite.coverage) }}</dd></div>
      </dl>
    </div>

    <div class="card-footer">
      <PaperFeedbackControls
        :work-id="currentPaper.work_id"
        :state="currentPaper.library_state"
        compact
        @updated="updateState"
      />
      <div class="paper-actions">
        <button
          type="button"
          class="evidence-action"
          data-test="show-evidence"
          @click="emit('evidence', currentPaper.scores.composite.snapshot_id)"
        >
          <PhInfo :size="15" aria-hidden="true" />
          {{ t('评分证据') }}
        </button>
        <button
          type="button"
          class="analysis-action"
          data-test="analyze-paper"
          @click="emit('analyze', currentPaper)"
        >
          {{ t('分析') }}
          <PhArrowRight :size="15" aria-hidden="true" />
        </button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.recommendation-card { padding: 24px 0 22px; border-top: 1px solid var(--color-line); }
.recommendation-card:first-child { border-top: 2px solid var(--color-forest); }
.recommendation-card.historical { opacity: .86; }
.integrity-warning { display: flex; align-items: center; gap: 8px; margin: 0 0 16px; padding: 9px 11px; border-left: 3px solid var(--color-danger); background: var(--color-wine-soft); color: var(--color-danger); font-size: var(--type-meta); }
.integrity-warning span { margin-left: auto; color: var(--color-muted-strong); }
.card-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, .55fr); gap: clamp(30px, 6vw, 84px); }
.paper-flags { display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 9px; }
.paper-flags span { display: inline-flex; align-items: center; gap: 4px; color: var(--color-muted); font-size: var(--type-meta); font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
.paper-flags .exploration { color: var(--color-wine); }
h2 { max-width: 820px; margin: 0; font-family: var(--font-editorial); font-size: clamp(20px, 2vw, 27px); font-weight: 500; line-height: 1.22; letter-spacing: -.015em; }
.authors, .source-line { margin: 9px 0 0; color: var(--color-muted-strong); font-size: var(--type-meta); line-height: 1.55; }
.source-line { color: var(--color-muted); }
.source-line span { margin-left: 12px; }
.exploration-reason { display: flex; align-items: center; gap: 6px; margin: 13px 0 0; color: var(--color-wine); font-size: var(--type-meta); }
.score-ledger { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; border-top: 1px solid var(--color-line); border-left: 1px solid var(--color-line); }
.score-ledger div { min-width: 0; padding: 12px 13px; border-right: 1px solid var(--color-line); border-bottom: 1px solid var(--color-line); }
.score-ledger dt { color: var(--color-muted); font-size: var(--type-meta); letter-spacing: .06em; }
.score-ledger dd { margin: 4px 0 0; font-family: var(--font-editorial); font-size: 23px; }
.card-footer { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-top: 18px; }
.paper-actions { display: flex; flex: none; gap: 7px; }
.paper-actions button { display: inline-flex; min-height: 34px; align-items: center; justify-content: center; gap: 6px; padding: 0 12px; border: 1px solid var(--color-line); border-radius: 7px; background: transparent; color: var(--color-forest); font-size: var(--type-meta); font-weight: 700; cursor: pointer; }
.paper-actions .analysis-action { border-color: var(--color-forest); background: var(--color-forest); color: var(--color-on-accent); }
.paper-actions button:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
@media (max-width: 800px) { .card-grid { grid-template-columns: 1fr; gap: 20px; } .card-footer { align-items: stretch; flex-direction: column; } .paper-actions { justify-content: flex-end; } }
@media (max-width: 520px) { .integrity-warning { align-items: flex-start; flex-wrap: wrap; } .integrity-warning span { width: 100%; margin-left: 24px; } .paper-actions { justify-content: stretch; } .paper-actions button { flex: 1; } }
</style>
