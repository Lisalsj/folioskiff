<script setup lang="ts">
import { researchText as t } from '@/stores/locale'
import { PhBookmarkSimple, PhCheck, PhSealCheck, PhWarning } from '@phosphor-icons/vue'

import type { RelatedPaper } from '../api'

defineProps<{
  papers: RelatedPaper[]
  savingId: string
}>()

const emit = defineEmits<{
  save: [candidateId: string]
}>()

function identity(paper: RelatedPaper): string {
  return paper.normalized_doi ? `DOI ${paper.normalized_doi}` : `${paper.connector_id} ${paper.external_id}`
}

function verificationLabel(status: string): string {
  return status === 'verified' ? t('已核验') : status === 'pending_verification' ? t('待核验') : t('未通过核验')
}
</script>

<template>
  <section class="paper-results" aria-live="polite">
    <article v-for="paper in papers" :key="paper.id" class="paper-card">
      <header>
        <span class="source">{{ paper.connector_id }}</span>
        <span class="verification" :class="paper.verification_status">
          <PhSealCheck v-if="paper.verification_status === 'verified'" :size="13" aria-hidden="true" />
          <PhWarning v-else :size="13" aria-hidden="true" />
          {{ verificationLabel(paper.verification_status) }}
        </span>
      </header>
      <h4>{{ paper.title }}</h4>
      <p class="authors">{{ paper.authors.join('、') || t('作者未知') }}<span v-if="paper.publication_year"> · {{ paper.publication_year }}</span></p>
      <p class="reason">{{ paper.reason }}</p>
      <footer>
        <span>{{ identity(paper) }}</span>
        <span>{{ t('关联置信') }} {{ Math.round(paper.confidence) }}%</span>
        <button
          v-if="!paper.saved"
          type="button"
          :data-test="`save-related-${paper.id}`"
          :disabled="savingId === paper.id || paper.verification_status !== 'verified' || !paper.linked_work_id"
          @click="emit('save', paper.id)"
        >
          <PhBookmarkSimple :size="14" aria-hidden="true" />
          {{ savingId === paper.id ? t('正在保存…') : t('保存到论文库') }}
        </button>
        <span v-else class="saved"><PhCheck :size="13" aria-hidden="true" /> {{ t('已在论文库') }}</span>
      </footer>
      <p v-if="paper.merge_review_id" class="merge-note">{{ t('身份相似但未静默合并；请在论文库完成审阅。') }}</p>
    </article>
    <p v-if="!papers.length" class="empty">{{ t('所选学术来源暂时没有已核验的相关论文。') }}</p>
  </section>
</template>

<style scoped>
.paper-results { display: grid; gap: 10px; }
.paper-card { padding: 14px; border: 1px solid var(--color-line); border-radius: var(--radius-md); background: var(--color-paper-raised); }
.paper-card header, .paper-card footer, .verification, .saved { display: flex; align-items: center; }
.paper-card header { justify-content: space-between; gap: 10px; }
.source { color: var(--color-forest); font-size: var(--type-meta); font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.verification { gap: 4px; color: var(--color-muted); font-size: var(--type-meta); }
.verification.verified { color: var(--color-forest); }
.paper-card h4 { margin: 9px 0 0; font-family: var(--font-editorial); font-size: 17px; font-weight: 500; line-height: 1.35; }
.authors, .reason { margin: 5px 0 0; font-size: var(--type-meta); line-height: 1.5; }
.authors { color: var(--color-muted); }
.reason { color: var(--color-muted-strong); }
.paper-card footer { flex-wrap: wrap; gap: 6px 12px; margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--color-line); color: var(--color-muted); font-size: var(--type-meta); }
.paper-card footer button { min-height: 29px; display: inline-flex; align-items: center; gap: 4px; margin-left: auto; padding: 0 9px; border: 1px solid var(--color-forest); border-radius: var(--radius-sm); background: transparent; color: var(--color-forest); font-size: var(--type-meta); cursor: pointer; }
.saved { gap: 4px; margin-left: auto; color: var(--color-forest); font-weight: 700; }
.merge-note { margin: 8px 0 0; color: var(--color-wine); font-size: var(--type-meta); }
.empty { margin: 0; padding: 24px 10px; color: var(--color-muted); font-size: var(--type-meta); text-align: center; }
button:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 1px; }
button:disabled { cursor: not-allowed; opacity: .55; }
</style>
