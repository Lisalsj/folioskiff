<script setup lang="ts">
import { messageRef, settingsText as t, serverMessage } from '@/stores/locale'
import { computed, onMounted, ref, watch } from 'vue'
import { PhArrowCounterClockwise, PhCheck, PhFloppyDisk } from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import ScoreEvidencePanel from '@/components/ScoreEvidencePanel.vue'
import { apiJson } from '@/lib/json-api'
import { useSettingsDirtyState } from '@/pages/settings/settings-dirty-state'

type QualityRule = components['schemas']['QualityRuleResponse']
type QualityDimension = components['schemas']['QualityRuleDimensionResponse']

interface DraftDimension extends QualityDimension {
  previous_weight: number
}

const rule = ref<QualityRule>()
const name = ref('')
const dimensions = ref<DraftDimension[]>([])
const loading = ref(true)
const saving = ref(false)
const error = messageRef(t)
const notice = messageRef(t)
const dirtyState = useSettingsDirtyState()

const totalWeight = computed(() =>
  dimensions.value.reduce(
    (total, dimension) => total + (dimension.enabled ? Number(dimension.weight_percent) || 0 : 0),
    0,
  ),
)
const valid = computed(
  () =>
    Boolean(name.value.trim()) &&
    Math.abs(totalWeight.value - 100) < 0.001 &&
    dimensions.value.some((dimension) => dimension.enabled) &&
    dimensions.value.every(
      (dimension) =>
        (!dimension.enabled && dimension.weight_percent === 0) ||
        (dimension.enabled && dimension.weight_percent > 0),
    ),
)
const isDirty = computed(() => {
  const current = rule.value
  if (!current) return false
  if (name.value.trim() !== current.name) return true
  return dimensions.value.some((draft) => {
    const stored = current.dimensions.find((dimension) => dimension.key === draft.key)
    return !stored
      || stored.enabled !== draft.enabled
      || stored.weight_percent !== Number(draft.weight_percent)
  })
})

function applyRule(value: QualityRule): void {
  rule.value = value
  name.value = value.name
  dimensions.value = value.dimensions.map((dimension) => ({
    ...dimension,
    previous_weight: dimension.weight_percent || 5,
  }))
}

function toggleDimension(dimension: DraftDimension, enabled: boolean): void {
  if (!enabled) {
    if (dimension.weight_percent > 0) dimension.previous_weight = dimension.weight_percent
    dimension.enabled = false
    dimension.weight_percent = 0
    return
  }
  dimension.enabled = true
  dimension.weight_percent = dimension.previous_weight || 5
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    applyRule(await apiJson<QualityRule>('/api/v1/scores/rules/quality'))
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法读取评分规则'
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  if (!valid.value) return
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const saved = await apiJson<QualityRule>('/api/v1/scores/rules/quality', {
      method: 'PUT',
      body: JSON.stringify({
        name: name.value.trim(),
        dimensions: dimensions.value.map((dimension) => ({
          key: dimension.key,
          enabled: dimension.enabled,
          weight_percent: dimension.enabled ? Number(dimension.weight_percent) : 0,
        })),
      }),
    })
    applyRule(saved)
    notice.value = '规则已保存为不可变修订；历史评分仍保留原公式。'
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法保存评分规则'
  } finally {
    saving.value = false
  }
}

async function reset(): Promise<void> {
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    applyRule(
      await apiJson<QualityRule>('/api/v1/scores/rules/quality', {
        method: 'DELETE',
      }),
    )
    notice.value = '已恢复内置默认规则；自定义历史修订仍保留用于解释旧评分。'
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法恢复默认规则'
  } finally {
    saving.value = false
  }
}

onMounted(() => void load())
watch(isDirty, (value) => dirtyState.setDirty(value))
</script>

<template>
  <section class="score-rule-section" aria-labelledby="score-rule-title">
    <div class="section-heading">
      <div>
        <p>{{ t('可复现公式') }}</p>
        <h2 id="score-rule-title">{{ t('客观质量规则') }}</h2>
      </div>
      <span v-if="rule" class="revision-label">
        {{ rule.builtin ? t('内置默认规则') : t('自定义修订 {0}', rule.revision) }}
      </span>
    </div>

    <p class="section-intro">
      {{ t('启用或停用内置维度并分配权重。每次保存都会生成不可变修订；新增评分维度由后续插件能力提供。') }}
    </p>

    <p v-if="loading" class="message" role="status">{{ t('正在读取评分规则…') }}</p>
    <p v-else-if="error && !rule" class="message error" role="alert">{{ error }}</p>

    <div v-if="rule" class="editor-layout">
      <form data-test="score-rule-form" @submit.prevent="save">
        <label class="rule-name">
          <span>{{ t('修订名称') }}</span>
          <input
            v-model="name"
            data-test="score-rule-name"
            maxlength="80"
            autocomplete="off"
          />
        </label>

        <fieldset>
          <legend>{{ t('质量维度与权重') }}</legend>
          <div
            v-for="dimension in dimensions"
            :key="dimension.key"
            class="dimension-row"
            :class="{ disabled: !dimension.enabled }"
            :data-test="`score-rule-row-${dimension.key}`"
          >
            <label class="dimension-toggle">
              <input
                type="checkbox"
                :checked="dimension.enabled"
                :data-test="`score-rule-enabled-${dimension.key}`"
                @change="toggleDimension(dimension, ($event.currentTarget as HTMLInputElement).checked)"
              />
              <span>
                <strong>{{ serverMessage(dimension.label) }}</strong>
                <small>{{ serverMessage(dimension.description) }}</small>
              </span>
            </label>
            <label class="weight-input">
              <span class="visually-hidden">{{ serverMessage(dimension.label) }}{{ t('权重百分比') }}</span>
              <input
                v-model.number="dimension.weight_percent"
                type="number"
                min="0"
                max="100"
                step="0.5"
                :disabled="!dimension.enabled"
                :data-test="`score-rule-weight-${dimension.key}`"
              />
              <span aria-hidden="true">%</span>
            </label>
          </div>
        </fieldset>

        <div class="formula-footer">
          <p
            data-test="score-rule-total"
            :class="{ invalid: Math.abs(totalWeight - 100) >= 0.001 }"
            aria-live="polite"
          >
            {{ t('启用权重合计') }} <strong>{{ totalWeight }}%</strong>
            <span v-if="Math.abs(totalWeight - 100) >= 0.001">{{ t('（需要等于 100%）') }}</span>
          </p>
          <div class="form-actions">
            <button
              v-if="!rule.builtin"
              type="button"
              class="secondary-action"
              data-test="score-rule-reset"
              :disabled="saving"
              @click="reset"
            >
              <PhArrowCounterClockwise :size="16" aria-hidden="true" />
              {{ t('恢复默认') }}
            </button>
            <button type="submit" class="primary-action" :disabled="!valid || saving">
              <PhFloppyDisk :size="16" aria-hidden="true" />
              {{ saving ? t('正在保存…') : t('保存为新修订') }}
            </button>
          </div>
        </div>

        <p v-if="notice" class="message success" role="status">
          <PhCheck :size="15" weight="bold" aria-hidden="true" />
          {{ notice }}
        </p>
        <p v-if="error" class="message error" role="alert">{{ error }}</p>
      </form>

      <ScoreEvidencePanel :preview="rule.preview" :rule-version="rule.rule_version" />
    </div>
  </section>
</template>

<style scoped>
.score-rule-section {
  margin-top: 58px;
  padding-top: 42px;
  border-top: 1px solid var(--color-line);
}

.section-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
}

.section-heading p,
.section-heading h2,
.section-intro {
  margin-top: 0;
}

.section-heading p {
  margin-bottom: 6px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.12em;
}

.section-heading h2 {
  margin-bottom: 0;
  font-family: var(--font-editorial);
  font-size: 29px;
  font-weight: 500;
}

.revision-label {
  color: var(--color-muted);
  font-size: 12px;
}

.section-intro {
  max-width: 760px;
  margin-bottom: 24px;
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.7;
}

.editor-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(330px, 0.75fr);
  gap: 28px;
  align-items: start;
}

form {
  min-width: 0;
}

.rule-name {
  display: grid;
  gap: 7px;
  margin-bottom: 18px;
  color: var(--color-muted-strong);
  font-size: 12px;
}

.rule-name input,
.weight-input input {
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper);
  color: var(--color-ink);
}

.rule-name input {
  min-height: 42px;
  padding: 0 12px;
}

input:focus-visible,
button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

fieldset {
  margin: 0;
  padding: 0;
  border: 0;
  border-top: 1px solid var(--color-line);
}

legend {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.dimension-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 90px;
  gap: 18px;
  align-items: center;
  min-height: 70px;
  padding: 11px 0;
  border-bottom: 1px solid var(--color-line);
}

.dimension-row.disabled {
  padding-left: 10px;
  border-left: 2px solid var(--color-line);
}

.dimension-toggle {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  cursor: pointer;
}

.dimension-toggle input {
  width: 16px;
  height: 16px;
  margin: 3px 0 0;
  accent-color: var(--color-forest);
}

.dimension-toggle strong,
.dimension-toggle small {
  display: block;
}

.dimension-toggle strong {
  font-size: 13px;
  font-weight: 650;
}

.dimension-toggle small {
  margin-top: 4px;
  color: var(--color-muted);
  font-size: var(--type-meta);
  line-height: 1.5;
}

.weight-input {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-muted);
  font-size: 12px;
}

.weight-input input {
  width: 65px;
  height: 36px;
  padding: 0 7px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.formula-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-top: 18px;
}

.formula-footer p {
  margin: 0;
  color: var(--color-muted);
  font-size: 12px;
}

.formula-footer p.invalid {
  color: var(--color-danger);
}

.form-actions {
  display: flex;
  gap: 8px;
}

button {
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 0 14px;
  border-radius: var(--radius-sm);
  font: inherit;
  font-size: 12px;
  font-weight: 650;
  cursor: pointer;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.primary-action {
  border: 1px solid transparent;
  background: var(--color-forest);
  color: var(--color-on-accent);
}

.secondary-action {
  border: 1px solid var(--color-line);
  background: transparent;
  color: var(--color-muted-strong);
}

.message {
  margin: 15px 0 0;
  color: var(--color-muted);
  font-size: 12px;
}

.message.success {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-forest);
}

.message.error {
  color: var(--color-danger);
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 1120px) {
  .editor-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .section-heading,
  .formula-footer {
    align-items: stretch;
    flex-direction: column;
  }

  .dimension-row {
    grid-template-columns: minmax(0, 1fr) 82px;
  }

  .form-actions {
    flex-wrap: wrap;
  }
}
</style>
