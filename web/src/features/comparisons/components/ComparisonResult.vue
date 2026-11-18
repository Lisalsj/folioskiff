<script setup lang="ts">
import { researchText as t } from '@/stores/locale'
import { PhDownloadSimple, PhScales } from '@phosphor-icons/vue'

import type { components } from '@/api/schema'

type ComparisonContent = components['schemas']['StructuredComparisonReport']
type ComparisonStatement = components['schemas']['ComparisonStatement']
type ComparisonReportBase = components['schemas']['ComparisonReportResponse']
type ComparisonReport = Omit<ComparisonReportBase, 'provenance'> & {
  provenance: ComparisonReportBase['provenance'] & { batch_count?: number }
}

defineProps<{ report: ComparisonReport | null, loading: boolean }>()

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    direct: t('一次综合'), extracted: t('已提取'), reused: t('复用检查点'), failed: t('提取失败'),
    unavailable: t('证据不可用'), available: t('证据可用'),
  }
  return labels[status] ?? status
}

function reportSections(content: ComparisonContent): Array<{ title: string, statements: ComparisonStatement[] }> {
  return [
    { title: t('概览'), statements: [content.overview] },
    ...content.axes.map((axis) => ({ title: axis.name, statements: axis.findings })),
    { title: t('共同点'), statements: content.agreements },
    { title: t('差异'), statements: content.differences },
    { title: t('权衡'), statements: content.tradeoffs },
    { title: t('局限'), statements: content.limitations },
    { title: t('建议'), statements: content.recommendations },
  ]
}
</script>

<template>
  <article class="report-pane">
    <div class="section-heading"><div><span>04</span><h2>{{ t('比较报告') }}</h2></div></div>
    <p v-if="loading" class="quiet-state" role="status">{{ t('正在打开比较版本…') }}</p>
    <div v-else-if="!report" class="empty-report">
      <PhScales :size="30" weight="thin" aria-hidden="true" />
      <p>{{ t('报告只会在你点击生成后出现。') }}</p>
      <small>{{ t('预检不会调用模型，也不会创建单篇论文分析。') }}</small>
    </div>
    <div v-else class="report-content">
      <header>
        <div>
          <span>{{ t('版本') }} {{ report.version }}</span>
          <h2>{{ report.content.title }}</h2>
          <p>{{ report.mode === 'direct' ? t('一次综合') : t('{0} 个批次的分阶段综合', report.provenance.batch_count ?? 0) }} {{ t('· 覆盖') }} {{ report.coverage }}%</p>
        </div>
        <a data-test="export-comparison-markdown" :href="`/api/v1/comparisons/reports/${report.id}/markdown`">
          <PhDownloadSimple :size="16" aria-hidden="true" /> Markdown
        </a>
      </header>
      <section v-for="section in reportSections(report.content)" :key="section.title">
        <h3>{{ section.title }}</h3>
        <div v-for="statement in section.statements" :key="statement.text" class="statement">
          <p>{{ statement.text }}</p>
          <ul>
            <li v-for="reference in statement.references" :key="`${reference.work_id}-${reference.evidence_id}`">
              {{ reference.work_id }} · {{ reference.kind === 'abstract' ? t('摘要') : reference.evidence_id }}
            </li>
          </ul>
        </div>
      </section>
      <ol class="report-item-coverage" :aria-label="t('报告逐篇覆盖')">
        <li v-for="item in report.items" :key="item.work_id">
          <span>{{ item.position + 1 }}. {{ item.work_id }}</span>
          <span>{{ statusLabel(item.extraction_status) }}</span>
          <strong>{{ item.coverage }}%</strong>
        </li>
      </ol>
    </div>
  </article>
</template>

<style scoped>
.report-pane { min-width: 0; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-bottom: 18px; }
.section-heading > div { display: flex; align-items: baseline; gap: 11px; }
.section-heading span { color: var(--color-wine); font-family: var(--font-editorial); font-size: 13px; }
.section-heading h2 { margin: 0; font-family: var(--font-editorial); font-size: 25px; font-weight: 500; }
.quiet-state,
.empty-report { padding: 40px 24px; border-block: 1px solid var(--color-line); color: var(--color-muted); text-align: center; }
.empty-report p { margin: 12px 0 5px; color: var(--color-ink); font-family: var(--font-editorial); font-size: 18px; }
.report-content > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 22px; padding-bottom: 22px; border-bottom: 1px solid var(--color-line); }
.report-content header span,
.report-content header p { color: var(--color-muted); font-size: var(--type-meta); }
.report-content header h2 { margin: 6px 0 8px; font-family: var(--font-editorial); font-size: 28px; font-weight: 500; }
.report-content header p { margin-bottom: 0; }
.report-content header a { display: inline-flex; align-items: center; gap: 7px; color: var(--color-forest); font-size: 12px; font-weight: 650; text-decoration: none; }
.report-content > section { padding: 24px 0; border-bottom: 1px solid var(--color-line); }
.report-content h3 { margin: 0 0 14px; font-family: var(--font-editorial); font-size: 20px; font-weight: 500; }
.statement + .statement { margin-top: 14px; }
.statement p { margin: 0 0 8px; line-height: 1.75; }
.statement ul { display: flex; flex-wrap: wrap; gap: 6px; padding: 0; margin: 0; list-style: none; }
.statement li { padding: 3px 7px; border: 1px solid var(--color-line); border-radius: 4px; color: var(--color-muted); font-size: var(--type-meta); }
.report-item-coverage { padding: 0; margin: 22px 0 0; list-style: none; }
.report-item-coverage li { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--color-line); font-size: var(--type-meta); }
a:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
</style>
