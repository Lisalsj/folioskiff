<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import { PhArrowSquareOut, PhBookOpenText, PhRobot } from '@phosphor-icons/vue'

import {
  evidenceLabel,
  languageLabel,
  scopeLabel,
  type TranslationLanguage,
  type TranslationScope,
} from '../api'
import TranslationProgress from './TranslationProgress.vue'

defineProps<{
  artifactId: string
  sourceLanguage: TranslationLanguage
  targetLanguage: TranslationLanguage
  scope: TranslationScope
  evidenceLevel: 'native_text' | 'ocr_text' | 'mixed_text'
  agentLabel: string
  status: string
  pageRange: string
  completed: number
  total: number
  percent: number
  active: boolean
  cancelRequested: boolean
  diagnostics: string[]
  busy?: boolean
}>()

const emit = defineEmits<{
  open: [artifactId: string]
  cancel: []
  resume: []
  useAsContext: []
}>()

</script>

<template>
  <article class="translation-task" data-test="translation-task-card">
    <header>
      <div>
        <span class="eyebrow">{{ t('双语译文') }}</span>
        <strong data-test="language-pair">
          {{ languageLabel(sourceLanguage) }} → {{ languageLabel(targetLanguage) }}
        </strong>
      </div>
      <PhBookOpenText :size="18" aria-hidden="true" />
    </header>

    <dl>
      <div>
        <dt>{{ t('范围') }}</dt>
        <dd>{{ scopeLabel(scope) }}，{{ pageRange }}</dd>
      </div>
      <div>
        <dt>{{ t('证据') }}</dt>
        <dd>{{ evidenceLabel(evidenceLevel) }}</dd>
      </div>
      <div>
        <dt><PhRobot :size="13" aria-hidden="true" /> Agent</dt>
        <dd>{{ agentLabel }}</dd>
      </div>
    </dl>

    <TranslationProgress
      :status="status"
      :completed="completed"
      :total="total"
      :percent="percent"
      :active="active"
      :cancel-requested="cancelRequested"
      :diagnostics="diagnostics"
      :busy="busy"
      @cancel="emit('cancel')"
      @resume="emit('resume')"
    />

    <footer>
      <button
        v-if="scope === 'selection' && status === 'succeeded'"
        type="button"
        class="context-button"
        data-test="translation-use-context"
        @click="emit('useAsContext')"
      >
        {{ t('用作后续提问上下文') }}
      </button>
      <button
        type="button"
        class="open-button"
        data-test="open-bilingual"
        @click="emit('open', artifactId)"
      >
        {{ t('打开双语译文') }}
        <PhArrowSquareOut :size="14" aria-hidden="true" />
      </button>
    </footer>
  </article>
</template>

<style scoped>
.translation-task {
  display: grid;
  gap: 11px;
  padding: 12px 13px;
  border-top: 1px solid var(--color-line);
  border-bottom: 1px solid var(--color-line);
  background: var(--color-paper-raised);
}

.translation-task header,
.translation-task footer,
.open-button,
.context-button,
dt {
  display: flex;
  align-items: center;
}

.translation-task header {
  justify-content: space-between;
  gap: 10px;
  color: var(--color-forest);
}

.translation-task header strong,
.eyebrow {
  display: block;
}

.eyebrow {
  margin-bottom: 2px;
  color: var(--color-muted);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.translation-task header strong {
  color: var(--color-ink);
  font-family: var(--font-editorial);
  font-size: 15px;
  font-weight: 500;
}

dl {
  display: grid;
  gap: 5px;
  margin: 0;
}

dl > div {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 8px;
  font-size: var(--type-meta);
  line-height: 1.45;
}

dt {
  gap: 3px;
  color: var(--color-muted);
}

dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  color: var(--color-muted-strong);
}

.translation-task footer {
  justify-content: flex-end;
  gap: 7px;
  padding-top: 2px;
}

.open-button,
.context-button {
  min-height: 34px;
  justify-content: center;
  gap: 6px;
  padding: 0 10px;
  border-radius: var(--radius-sm);
  font: inherit;
  font-size: var(--type-meta);
  cursor: pointer;
}

.open-button {
  border: 1px solid var(--color-forest);
  background: var(--color-forest);
  color: var(--color-paper);
}

.context-button {
  border: 1px solid var(--color-line);
  background: transparent;
  color: var(--color-muted-strong);
}

.open-button:focus-visible,
.context-button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}
</style>
