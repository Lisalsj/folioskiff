<script setup lang="ts">
import { researchText as t, serverMessage } from '@/stores/locale'
import type { components } from '@/api/schema'

type QualityRulePreview = components['schemas']['QualityRulePreview']
type QualityRulePreviewComponent = components['schemas']['QualityRulePreviewComponent']

defineProps<{
  preview: QualityRulePreview
  ruleVersion: string
}>()

function number(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

function evidenceLabel(component: QualityRulePreviewComponent): string {
  if (!component.enabled) return t('已停用')
  if (component.status === 'observed' && component.observed_value != null) {
    return t('已观测 · {0}', number(component.observed_value))
  }
  if (component.status === 'not_applicable') return t('不适用')
  if (component.imputed_value != null) {
    return t('未知 · 中性 {0}', number(component.imputed_value))
  }
  return t('未知')
}
</script>

<template>
  <section class="evidence-panel" aria-labelledby="score-evidence-title">
    <header>
      <div>
        <p>{{ t('服务端解释预览') }}</p>
        <h3 id="score-evidence-title">{{ t('分数证据') }}</h3>
      </div>
      <span class="rule-version">{{ ruleVersion }}</span>
    </header>

    <dl class="score-summary">
      <div>
        <dt>{{ t('质量分') }}</dt>
        <dd data-test="evidence-score">{{ number(preview.score) }}</dd>
      </div>
      <div>
        <dt>{{ t('证据覆盖') }}</dt>
        <dd data-test="evidence-coverage">{{ number(preview.coverage) }}%</dd>
      </div>
      <div>
        <dt>{{ t('置信度') }}</dt>
        <dd data-test="evidence-confidence">{{ number(preview.confidence) }}%</dd>
      </div>
    </dl>

    <p class="missing-note">
      {{ t('未知证据使用中性 50，不等于低质量；覆盖度和置信度会如实下降。') }}
    </p>

    <div class="evidence-table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">{{ t('评分维度') }}</th>
            <th scope="col">{{ t('权重') }}</th>
            <th scope="col">{{ t('当前证据') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="component in preview.components" :key="component.key">
            <th scope="row">{{ serverMessage(component.label) }}</th>
            <td>{{ number(component.weight_percent) }}%</td>
            <td :class="{ muted: component.status !== 'observed' || !component.enabled }">
              {{ evidenceLabel(component) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.evidence-panel {
  min-width: 0;
  padding: 24px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--color-paper) 88%, white 12%);
}

header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--color-line);
}

header p,
h3 {
  margin: 0;
}

header p {
  margin-bottom: 5px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.12em;
}

h3 {
  font-family: var(--font-editorial);
  font-size: 22px;
  font-weight: 500;
}

.rule-version {
  color: var(--color-muted);
  font-family: ui-monospace, 'Cascadia Mono', monospace;
  font-size: var(--type-meta);
  overflow-wrap: anywhere;
  text-align: right;
}

.score-summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  margin: 0;
  padding: 20px 0;
}

.score-summary div {
  padding: 0 16px;
  border-right: 1px solid var(--color-line);
}

.score-summary div:first-child {
  padding-left: 0;
}

.score-summary div:last-child {
  padding-right: 0;
  border-right: 0;
}

dt {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

dd {
  margin: 5px 0 0;
  font-family: var(--font-editorial);
  font-size: 25px;
  font-variant-numeric: tabular-nums;
}

.missing-note {
  margin: 0 0 18px;
  padding-left: 13px;
  border-left: 2px solid var(--color-forest);
  color: var(--color-muted-strong);
  font-size: 12px;
  line-height: 1.65;
}

.evidence-table-wrap {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

th,
td {
  padding: 10px 8px;
  border-top: 1px solid var(--color-line);
  text-align: left;
  white-space: nowrap;
}

thead th {
  color: var(--color-muted);
  font-size: var(--type-meta);
  font-weight: 650;
  letter-spacing: 0.05em;
}

tbody th {
  font-weight: 600;
}

td:nth-child(2) {
  font-variant-numeric: tabular-nums;
}

.muted {
  color: var(--color-muted);
}

@media (max-width: 520px) {
  .evidence-panel {
    padding: 19px;
  }

  header {
    display: block;
  }

  .rule-version {
    display: block;
    margin-top: 8px;
    text-align: left;
  }

  .score-summary div {
    padding-inline: 10px;
  }
}
</style>
