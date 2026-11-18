<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import { computed } from 'vue'
import { PhArrowClockwise, PhStopCircle, PhWarningCircle } from '@phosphor-icons/vue'

const props = defineProps<{
  status: string
  completed: number
  total: number
  percent: number
  active: boolean
  cancelRequested: boolean
  diagnostics: string[]
  busy?: boolean
}>()

const emit = defineEmits<{
  cancel: []
  resume: []
}>()

const statusLabel = computed(() => ({
  draft: t('准备中'),
  queued: t('等待处理'),
  running: t('翻译中'),
  succeeded: t('已完成'),
  partial: t('部分完成'),
  failed: t('失败'),
  cancelled: t('已取消'),
}[props.status] ?? props.status))

const resumable = computed(() => ['partial', 'failed', 'cancelled'].includes(props.status))
</script>

<template>
  <section class="translation-progress" :aria-label="t('翻译进度')">
    <div class="progress-heading">
      <span class="status" :data-status="status">{{ statusLabel }}</span>
      <strong>{{ completed }} / {{ total || t('待计算') }}</strong>
    </div>
    <div
      class="progress-track"
      role="progressbar"
      :aria-valuenow="percent"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-label="t('翻译完成 {0}%', percent)"
    >
      <span :style="{ width: `${percent}%` }" />
    </div>
    <p v-if="cancelRequested" class="quiet-status" role="status">
      {{ t('正在安全停止，已完成段落会保留。') }}
    </p>

    <div v-if="diagnostics.length" class="diagnostics" role="alert">
      <PhWarningCircle :size="16" aria-hidden="true" />
      <div>
        <strong>{{ t('需要注意') }}</strong>
        <ul>
          <li v-for="diagnostic in diagnostics" :key="diagnostic">{{ diagnostic }}</li>
        </ul>
      </div>
    </div>

    <div v-if="active || resumable" class="progress-actions">
      <button
        v-if="active"
        type="button"
        class="quiet-button"
        data-test="translation-cancel"
        :disabled="cancelRequested || busy"
        @click="emit('cancel')"
      >
        <PhStopCircle :size="15" aria-hidden="true" />
        {{ cancelRequested ? t('正在取消') : t('取消翻译') }}
      </button>
      <button
        v-else-if="resumable"
        type="button"
        class="primary-button"
        data-test="translation-resume"
        :disabled="busy"
        @click="emit('resume')"
      >
        <PhArrowClockwise :size="15" aria-hidden="true" />
        {{ t('继续翻译') }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.translation-progress {
  display: grid;
  gap: 8px;
}

.progress-heading,
.progress-actions,
.primary-button,
.quiet-button {
  display: flex;
  align-items: center;
}

.progress-heading {
  justify-content: space-between;
  gap: 10px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.progress-heading strong {
  color: var(--color-ink);
  font-variant-numeric: tabular-nums;
}

.status {
  color: var(--color-forest);
  font-weight: 700;
}

.status[data-status='failed'],
.status[data-status='partial'],
.status[data-status='cancelled'] {
  color: var(--color-wine);
}

.progress-track {
  height: 5px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--color-line);
}

.progress-track > span {
  height: 100%;
  display: block;
  background: var(--color-forest);
  transition: width 160ms ease;
}

.quiet-status {
  margin: 0;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.diagnostics {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 7px;
  padding: 8px 9px;
  border-left: 2px solid var(--color-wine);
  background: var(--color-wine-soft);
  color: var(--color-wine);
  font-size: var(--type-meta);
  line-height: 1.5;
}

.diagnostics strong {
  display: block;
}

.diagnostics ul {
  margin: 3px 0 0;
  padding-left: 16px;
}

.progress-actions {
  justify-content: flex-end;
}

.primary-button,
.quiet-button {
  min-height: 32px;
  justify-content: center;
  gap: 6px;
  padding: 0 10px;
  border-radius: var(--radius-sm);
  font: inherit;
  font-size: var(--type-meta);
  cursor: pointer;
}

.primary-button {
  border: 1px solid var(--color-forest);
  background: var(--color-forest);
  color: var(--color-paper);
}

.quiet-button {
  border: 1px solid var(--color-line);
  background: transparent;
  color: var(--color-muted-strong);
}

.primary-button:focus-visible,
.quiet-button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.primary-button:disabled,
.quiet-button:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

@media (prefers-reduced-motion: reduce) {
  .progress-track > span {
    transition: none;
  }
}
</style>
