<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import { reactive, watch } from 'vue'
import { PhFloppyDisk, PhLockKey, PhWarningCircle } from '@phosphor-icons/vue'

import type { TranslationTerm } from '../api'

const props = defineProps<{
  terms: TranslationTerm[]
  savingTermId: string
  immutable: boolean
}>()

const emit = defineEmits<{
  updateTerm: [termId: string, targetTerm: string, locked: boolean]
}>()

interface TermDraft {
  targetTerm: string
  locked: boolean
}

const drafts = reactive<Record<string, TermDraft>>({})

watch(
  () => props.terms,
  (terms) => {
    for (const term of terms) {
      drafts[term.id] = {
        targetTerm: term.target_term ?? '',
        locked: term.locked,
      }
    }
  },
  { immediate: true, deep: true },
)

function save(term: TranslationTerm): void {
  const draft = drafts[term.id]
  if (!draft?.targetTerm.trim() || props.immutable) return
  emit('updateTerm', term.id, draft.targetTerm.trim(), draft.locked)
}
</script>

<template>
  <section class="glossary-editor" aria-labelledby="glossary-title">
    <header>
      <div>
        <span class="eyebrow">Terminology</span>
        <h2 id="glossary-title">{{ t('术语表') }}</h2>
      </div>
      <span>{{ terms.length }} {{ t('个术语') }}</span>
    </header>

    <p v-if="terms.length === 0" class="empty-copy">{{ t('当前译文还没有提取到术语。') }}</p>
    <p v-else-if="immutable" class="immutable-note">
      <PhLockKey :size="15" aria-hidden="true" />
      {{ t('已完成译文保持不可变。如需调整术语，请创建新版本。') }}
    </p>
    <p v-else class="change-note">
      <PhWarningCircle :size="15" aria-hidden="true" />
      {{ t('保存后受影响段落会标记为待重新翻译，不会静默改写现有译文。') }}
    </p>

    <form
      v-for="term in terms"
      :key="term.id"
      class="term-row"
      @submit.prevent="save(term)"
    >
      <label>
        <span>{{ t('原文') }}</span>
        <strong>{{ term.source_term }}</strong>
      </label>
      <label>
        <span>{{ t('译法') }}</span>
        <input
          v-model="drafts[term.id]!.targetTerm"
          :data-test="`term-target-${term.id}`"
          :disabled="immutable || savingTermId === term.id"
          maxlength="1000"
          required
        >
      </label>
      <label class="lock-choice">
        <input
          v-model="drafts[term.id]!.locked"
          type="checkbox"
          :data-test="`term-lock-${term.id}`"
          :disabled="immutable || savingTermId === term.id"
        >
        <PhLockKey :size="13" aria-hidden="true" />
        {{ t('锁定译法') }}
      </label>
      <button
        type="button"
        :data-test="`term-save-${term.id}`"
        :disabled="immutable || savingTermId === term.id || !drafts[term.id]?.targetTerm.trim()"
        @click="save(term)"
      >
        <PhFloppyDisk :size="14" aria-hidden="true" />
        {{ savingTermId === term.id ? t('保存中') : t('保存') }}
      </button>
    </form>
  </section>
</template>

<style scoped>
.glossary-editor {
  display: grid;
  gap: 11px;
}

.glossary-editor > header,
.change-note,
.immutable-note,
.lock-choice,
.term-row button {
  display: flex;
  align-items: center;
}

.glossary-editor > header {
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--color-line);
}

.glossary-editor > header h2 {
  margin: 1px 0 0;
  font-family: var(--font-editorial);
  font-size: 18px;
  font-weight: 500;
}

.glossary-editor > header > span,
.eyebrow {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.eyebrow {
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.change-note,
.immutable-note,
.empty-copy {
  gap: 6px;
  margin: 0;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  line-height: 1.5;
}

.change-note {
  color: var(--color-wine);
}

.term-row {
  display: grid;
  grid-template-columns: minmax(110px, 0.8fr) minmax(150px, 1fr);
  gap: 8px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--color-line);
}

.term-row label:not(.lock-choice) {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.term-row label > span {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.term-row strong {
  overflow-wrap: anywhere;
  color: var(--color-ink);
  font-size: var(--type-meta);
}

.term-row input[type='text'],
.term-row input:not([type]) {
  min-width: 0;
  min-height: 32px;
  padding: 0 8px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-raised);
  color: var(--color-ink);
  font: inherit;
  font-size: var(--type-meta);
}

.lock-choice {
  align-self: center;
  gap: 5px;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
}

.lock-choice input {
  accent-color: var(--color-forest);
}

.term-row button {
  min-height: 32px;
  align-self: center;
  justify-content: center;
  gap: 5px;
  padding: 0 9px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-forest);
  font: inherit;
  font-size: var(--type-meta);
  cursor: pointer;
}

.term-row :is(input, button):focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.term-row :is(input, button):disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

@media (max-width: 600px) {
  .term-row {
    grid-template-columns: 1fr;
  }
}
</style>
