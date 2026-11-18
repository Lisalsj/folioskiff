<script setup lang="ts">
import { settingsText as t } from '@/stores/locale'
import type { OnboardingStep, OnboardingStepStatus } from '../api'

type WizardStep = Exclude<OnboardingStep, 'complete'>

interface StepDefinition {
  id: WizardStep
  label: string
}

const props = defineProps<{
  current: OnboardingStep
  steps: Partial<Record<WizardStep, OnboardingStepStatus>>
}>()

const definitions: readonly StepDefinition[] = [
  { id: 'storage_privacy', get label() { return t('存储与隐私') } },
  { id: 'agents', get label() { return t('AI 能力') } },
  { id: 'sources', get label() { return t('论文来源') } },
  { id: 'reading_preferences', get label() { return t('阅读偏好') } },
]

function statusLabel(step: WizardStep): string {
  if (props.current === step) return t('当前步骤')
  const status = props.steps[step] ?? 'pending'
  if (status === 'completed') return t('已完成')
  if (status === 'skipped') return t('已跳过')
  return t('待配置')
}
</script>

<template>
  <ol class="onboarding-progress" :aria-label="t('首次配置进度')">
    <li
      v-for="(step, index) in definitions"
      :key="step.id"
      :data-step="step.id"
      :aria-current="current === step.id ? 'step' : undefined"
      :class="{
        'is-current': current === step.id,
        'is-complete': steps[step.id] === 'completed',
        'is-skipped': steps[step.id] === 'skipped',
      }"
    >
      <span class="step-index" aria-hidden="true">{{ index + 1 }}</span>
      <span class="step-copy">
        <strong>{{ step.label }}</strong>
        <span>{{ statusLabel(step.id) }}</span>
      </span>
    </li>
  </ol>
</template>

<style scoped>
.onboarding-progress {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin: 0;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
  background: var(--color-line);
  list-style: none;
}

.onboarding-progress li {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  background: var(--color-paper-raised);
  color: var(--color-muted);
}

.step-index {
  width: 24px;
  height: 24px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border: 1px solid var(--color-line-strong);
  border-radius: 50%;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.step-copy,
.step-copy strong,
.step-copy span {
  display: block;
}

.step-copy {
  min-width: 0;
}

.step-copy strong {
  overflow: hidden;
  color: var(--color-muted-strong);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.step-copy span {
  margin-top: 2px;
  font-size: var(--type-meta);
}

.onboarding-progress li.is-current {
  background: var(--color-forest-soft);
  color: var(--color-forest);
}

.onboarding-progress li.is-current .step-index {
  border-color: var(--color-forest);
  background: var(--color-forest);
  color: var(--color-on-accent);
}

.onboarding-progress li.is-current strong,
.onboarding-progress li.is-complete strong {
  color: var(--color-ink);
}

.onboarding-progress li.is-complete .step-index {
  border-color: var(--color-forest);
  color: var(--color-forest);
}

@media (max-width: 720px) {
  .onboarding-progress {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 440px) {
  .onboarding-progress {
    grid-template-columns: 1fr;
  }
}
</style>
