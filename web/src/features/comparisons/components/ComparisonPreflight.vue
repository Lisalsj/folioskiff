<script setup lang="ts">
import { researchText as t } from '@/stores/locale'
import { PhCheck, PhSparkle, PhWarning } from '@phosphor-icons/vue'

import type { components } from '@/api/schema'

type ComparisonPreflight = components['schemas']['ComparisonPreflight']

interface ProgressJob {
  status: string
  progress: number
  checkpoint: { stage?: string, processed?: number, available?: number } | null
}

defineProps<{
  preflight: ComparisonPreflight
  activeJob: ProgressJob | null
  generating: boolean
}>()

defineEmits<{ generate: [] }>()

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    direct: t('一次综合'), extracted: t('已提取'), reused: t('复用检查点'), failed: t('提取失败'),
    unavailable: t('证据不可用'), available: t('证据可用'),
  }
  return labels[status] ?? status
}

function jobStatusLabel(job: ProgressJob): string {
  const terminal: Record<string, string> = {
    cancelled: t('比较已取消'),
    failed: t('比较失败'),
    succeeded: t('比较已完成'),
    queued: t('等待开始'),
  }
  return terminal[job.status]
    ?? (job.checkpoint?.stage === 'extraction' ? t('逐篇提取') : t('综合'))
}
</script>

<template>
  <section class="preflight-panel" aria-labelledby="preflight-title">
    <header>
      <div>
        <p>{{ preflight.mode === 'direct' ? t('一次综合') : t('分阶段综合') }}</p>
        <h2 id="preflight-title">{{ t('证据预检') }}</h2>
      </div>
      <div class="coverage-total">
        <strong>{{ preflight.coverage }}%</strong>
        <span>{{ t('证据覆盖') }} {{ preflight.coverage }}%</span>
      </div>
    </header>
    <div class="cloud-note" :class="preflight.cloud_backend ? 'cloud' : 'local'">
      <PhWarning v-if="preflight.cloud_backend" :size="17" aria-hidden="true" />
      <PhCheck v-else :size="17" aria-hidden="true" />
      {{ preflight.backend_display_name }} · {{ preflight.cloud_backend ? t('云端后端') : t('本地后端') }} ·
      {{ preflight.model_id || t('默认模型') }}
    </div>
    <ol class="evidence-list">
      <li v-for="item in preflight.items" :key="item.work_id">
        <span>{{ String(item.position + 1).padStart(2, '0') }}</span>
        <div>
          <strong>{{ item.title }}</strong>
          <small>{{ item.evidence_level === 'full_text' ? t('全文证据') : item.evidence_level === 'abstract_only' ? t('摘要证据') : t('无可用证据') }}</small>
        </div>
        <div class="paper-coverage">
          <span>{{ statusLabel(item.status) }}</span>
          <progress :value="item.coverage" max="100">{{ item.coverage }}%</progress>
          <strong>{{ item.coverage }}%</strong>
        </div>
      </li>
    </ol>
    <div v-if="activeJob" class="active-progress" aria-live="polite">
      <span>{{ jobStatusLabel(activeJob) }}</span>
      <progress :value="activeJob.progress" max="100">{{ activeJob.progress }}%</progress>
      <strong>{{ activeJob.progress }}%</strong>
      <small v-if="activeJob.checkpoint?.available">{{ t('已处理') }} {{ activeJob.checkpoint.processed ?? 0 }} / {{ activeJob.checkpoint.available }} {{ t('篇') }}</small>
    </div>
    <button
      type="button"
      data-test="generate-comparison"
      class="primary-action"
      :disabled="!preflight.ready || generating"
      @click="$emit('generate')"
    >
      <PhSparkle :size="17" aria-hidden="true" />
      {{ generating ? t('正在生成比较…') : t('生成比较报告') }}
    </button>
  </section>
</template>

<style scoped>
.preflight-panel { margin-top: 52px; padding-top: 26px; border-top: 1px solid var(--color-line); }
.preflight-panel > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 22px; }
.preflight-panel header p { margin: 0 0 9px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: 0.14em; text-transform: uppercase; }
.preflight-panel h2 { margin: 0; font-family: var(--font-editorial); font-size: 28px; font-weight: 500; }
.coverage-total { text-align: right; }
.coverage-total strong,
.coverage-total span { display: block; }
.coverage-total strong { font-family: var(--font-editorial); font-size: 28px; font-weight: 500; }
.coverage-total span { color: var(--color-muted); font-size: var(--type-meta); }
.cloud-note { display: flex; align-items: center; gap: 8px; margin: 18px 0; padding: 10px 12px; border-left: 3px solid var(--color-forest); background: var(--color-forest-soft); font-size: var(--type-meta); }
.cloud-note.cloud { border-color: var(--color-wine); background: var(--color-wine-soft); }
.evidence-list { padding: 0; margin: 0; border-block: 1px solid var(--color-line); list-style: none; }
.evidence-list li { min-height: 64px; display: grid; grid-template-columns: 32px minmax(0, 1fr) minmax(170px, 0.35fr); align-items: center; gap: 14px; padding: 10px 4px; }
.evidence-list li + li { border-top: 1px solid var(--color-line); }
.evidence-list > li > span { color: var(--color-wine); font-family: var(--font-editorial); font-size: 12px; }
.evidence-list li > div > strong,
.evidence-list li > div > small { display: block; }
.evidence-list li > div > strong { font-family: var(--font-editorial); font-size: 15px; }
.evidence-list li > div > small { margin-top: 4px; color: var(--color-muted); }
.paper-coverage { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 5px 10px; }
.paper-coverage span { color: var(--color-muted); font-size: var(--type-meta); }
.paper-coverage progress { grid-column: 1 / -1; width: 100%; height: 4px; accent-color: var(--color-forest); }
.paper-coverage strong { font-size: var(--type-meta); }
.active-progress { display: grid; grid-template-columns: auto minmax(100px, 1fr) auto; align-items: center; gap: 10px; margin-top: 16px; color: var(--color-muted-strong); font-size: var(--type-meta); }
.active-progress progress { width: 100%; accent-color: var(--color-forest); }
.active-progress small { grid-column: 2 / -1; }
.primary-action { min-height: 40px; display: inline-flex; align-items: center; gap: 8px; margin-top: 18px; padding: 0 17px; border: 1px solid var(--color-forest); border-radius: var(--radius-sm); background: var(--color-forest); color: var(--color-on-accent); font-weight: 650; cursor: pointer; }
.primary-action:disabled { opacity: 0.45; cursor: default; }
.primary-action:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
@media (max-width: 680px) {
  .evidence-list li { grid-template-columns: 28px minmax(0, 1fr); }
  .paper-coverage { grid-column: 2; }
}
</style>
