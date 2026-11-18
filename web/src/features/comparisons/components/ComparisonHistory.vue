<script setup lang="ts">
import { currentLocale, researchText as t } from '@/stores/locale'
import type { components } from '@/api/schema'

type ComparisonReportSummary = components['schemas']['ComparisonReportSummaryResponse']

defineProps<{ history: readonly ComparisonReportSummary[], loading: boolean }>()
defineEmits<{ select: [reportId: string] }>()
</script>

<template>
  <aside class="history-pane">
    <div class="section-heading"><div><span>05</span><h2>{{ t('版本历史') }}</h2></div></div>
    <p v-if="loading" class="quiet-state">{{ t('正在读取历史…') }}</p>
    <p v-else-if="history.length === 0" class="quiet-state">{{ t('还没有比较版本。') }}</p>
    <ol v-else class="history-list">
      <li v-for="item in history" :key="item.id">
        <button type="button" @click="$emit('select', item.id)">
          <span>{{ t('版本') }} {{ item.version }} · {{ item.mode === 'direct' ? t('一次综合') : t('分阶段') }}</span>
          <strong>{{ item.title }}</strong>
          <small>{{ item.coverage }}{{ t('% 覆盖 ·') }} {{ new Date(item.created_at).toLocaleDateString(currentLocale()) }}</small>
        </button>
      </li>
    </ol>
  </aside>
</template>

<style scoped>
.history-pane { min-width: 0; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-bottom: 18px; }
.section-heading > div { display: flex; align-items: baseline; gap: 11px; }
.section-heading span { color: var(--color-wine); font-family: var(--font-editorial); font-size: 13px; }
.section-heading h2 { margin: 0; font-family: var(--font-editorial); font-size: 25px; font-weight: 500; }
.quiet-state { padding: 40px 24px; border-block: 1px solid var(--color-line); color: var(--color-muted); text-align: center; }
.history-list { padding: 0; margin: 0; border-block: 1px solid var(--color-line); list-style: none; }
.history-list li + li { border-top: 1px solid var(--color-line); }
.history-list button { width: 100%; padding: 14px 4px; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.history-list button:hover { background: var(--color-forest-hover-soft); }
.history-list button:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
.history-list span,
.history-list strong,
.history-list small { display: block; }
.history-list span,
.history-list small { color: var(--color-muted); font-size: var(--type-meta); }
.history-list strong { margin: 6px 0; font-family: var(--font-editorial); font-size: 15px; }
@media (max-width: 980px) { .history-pane { order: -1; } }
</style>
