<script setup lang="ts">
import { messageRef, discoveryText as t } from '@/stores/locale'
import { onMounted, reactive, ref } from 'vue'
import { PhArrowsMerge, PhGitDiff } from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'

type MergeReview = components['schemas']['MergeReviewResponse']
type MergeDecision = components['schemas']['MergeReviewDecision']['decision']

const reviews = ref<MergeReview[]>([])
const notes = reactive<Record<string, string>>({})
const deciding = ref<string>()
const loading = ref(true)
const error = messageRef(t)

function formatEvidence(value: unknown): string {
  if (typeof value === 'number') {
    return value >= 0 && value <= 1 ? `${Math.round(value * 100)}%` : String(value)
  }
  if (Array.isArray(value)) return value.join('、') || t('无')
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return t('未知')
  return JSON.stringify(value)
}

function evidenceLabel(key: string): string {
  const labels: Record<string, string> = {
    title_similarity: t('标题相似度'),
    author_similarity: t('作者相似度'),
    shared_authors: t('共同作者'),
    doi: t('DOI 证据'),
  }
  return labels[key] ?? key.replaceAll('_', ' ')
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    reviews.value = await apiJson<MergeReview[]>(
      '/api/v1/works/merge-reviews?status=pending',
    )
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法读取重复论文审阅队列'
  } finally {
    loading.value = false
  }
}

async function decide(review: MergeReview, decision: MergeDecision): Promise<void> {
  deciding.value = review.id
  error.value = ''
  try {
    await apiJson<MergeReview>(
      `/api/v1/works/merge-reviews/${encodeURIComponent(review.id)}/decision`,
      {
        method: 'POST',
        body: JSON.stringify({ decision, note: notes[review.id]?.trim() || null }),
      },
    )
    reviews.value = reviews.value.filter((item) => item.id !== review.id)
    delete notes[review.id]
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法保存重复论文决策'
  } finally {
    deciding.value = undefined
  }
}

onMounted(() => void load())
</script>

<template>
  <section class="merge-queue" aria-labelledby="merge-review-title">
    <div class="queue-heading">
      <div>
        <p>{{ t('不会静默合并') }}</p>
        <h2 id="merge-review-title">{{ t('待确认的重复论文') }}</h2>
      </div>
      <span v-if="!loading">{{ reviews.length }} {{ t('项') }}</span>
    </div>

    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="loading" class="quiet-state">{{ t('正在检查相似论文…') }}</p>
    <p v-else-if="reviews.length === 0" class="quiet-state">{{ t('暂无待确认的重复论文') }}</p>

    <article v-for="review in reviews" v-else :key="review.id" class="review-row">
      <header>
        <span class="similarity">{{ Math.round(review.similarity * 100) }}{{ t('% 相似') }}</span>
        <p>{{ t('请依据标题、作者和 DOI 判断；不确定时应保持分开。') }}</p>
      </header>

      <div class="identity-comparison">
        <section>
          <small>{{ t('新候选论文') }}</small>
          <h3>{{ review.candidate_work.title }}</h3>
          <p>{{ review.candidate_work.authors.join(' · ') || t('作者未知') }}</p>
          <dl>
            <div><dt>DOI</dt><dd>{{ review.candidate_work.doi ?? t('无') }}</dd></div>
            <div><dt>{{ t('来源记录') }}</dt><dd>{{ review.candidate_work.source_count }}</dd></div>
          </dl>
        </section>

        <PhGitDiff class="compare-icon" :size="22" aria-hidden="true" />

        <section>
          <small>{{ t('已有规范论文') }}</small>
          <h3>{{ review.matched_work.title }}</h3>
          <p>{{ review.matched_work.authors.join(' · ') || t('作者未知') }}</p>
          <dl>
            <div><dt>DOI</dt><dd>{{ review.matched_work.doi ?? t('无') }}</dd></div>
            <div><dt>{{ t('来源记录') }}</dt><dd>{{ review.matched_work.source_count }}</dd></div>
          </dl>
        </section>
      </div>

      <dl class="evidence-list">
        <div v-for="(value, key) in review.evidence" :key="key">
          <dt>{{ evidenceLabel(String(key)) }}</dt>
          <dd>{{ formatEvidence(value) }}</dd>
        </div>
      </dl>

      <div class="decision-row">
        <label :for="`review-note-${review.id}`">{{ t('决策备注（可选）') }}</label>
        <textarea
          :id="`review-note-${review.id}`"
          v-model="notes[review.id]"
          :data-test="`note-${review.id}`"
          rows="2"
          :placeholder="t('记录判断依据')"
        />
        <div class="decision-actions">
          <button
            :data-test="`keep-${review.id}`"
            type="button"
            :disabled="deciding === review.id"
            @click="decide(review, 'keep_separate')"
          >
            {{ t('保持分开') }}
          </button>
          <button
            :data-test="`merge-${review.id}`"
            class="merge-button"
            type="button"
            :disabled="deciding === review.id"
            @click="decide(review, 'merge')"
          >
            <PhArrowsMerge :size="17" aria-hidden="true" />
            {{ t('确认合并') }}
          </button>
        </div>
      </div>
    </article>
  </section>
</template>

<style scoped>
.merge-queue { margin-top: clamp(52px, 8vw, 96px); }

.queue-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding-bottom: 18px;
  border-bottom: 2px solid var(--color-forest);
}

.queue-heading p {
  margin: 0 0 9px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.13em;
}

.queue-heading h2,
.review-row h3 { margin: 0; font-family: var(--font-editorial); font-weight: 500; }
.queue-heading h2 { font-size: 28px; }
.queue-heading > span { color: var(--color-muted); font-size: 12px; }

.review-row { padding: 26px 0 32px; border-bottom: 1px solid var(--color-line); }
.review-row > header { display: flex; align-items: center; gap: 12px; }
.review-row > header p { margin: 0; color: var(--color-muted); font-size: var(--type-meta); }

.similarity {
  flex: none;
  color: var(--color-wine, #7d3438);
  font-size: 12px;
  font-weight: 750;
}

.identity-comparison {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  gap: 22px;
  align-items: center;
  margin-top: 22px;
}

.identity-comparison section { min-width: 0; }
.identity-comparison small { color: var(--color-muted); font-size: var(--type-meta); letter-spacing: 0.08em; }
.identity-comparison h3 { margin-top: 8px; font-size: 20px; line-height: 1.25; }
.identity-comparison section > p { margin: 8px 0 12px; color: var(--color-muted-strong); font-size: var(--type-meta); }
.compare-icon { color: var(--color-muted); }

dl { margin: 0; }
.identity-comparison dl { display: flex; flex-wrap: wrap; gap: 8px 22px; }
.identity-comparison dl div { display: flex; gap: 6px; font-size: var(--type-meta); }
dt { color: var(--color-muted); }
dd { margin: 0; overflow-wrap: anywhere; }

.evidence-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 28px;
  margin-top: 22px;
  padding: 13px 0;
  border-block: 1px solid var(--color-line);
}

.evidence-list div { display: flex; gap: 8px; font-size: var(--type-meta); }
.evidence-list dd { color: var(--color-ink); font-weight: 650; }

.decision-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 7px 16px; margin-top: 18px; }
.decision-row label { grid-column: 1 / -1; color: var(--color-muted-strong); font-size: var(--type-meta); font-weight: 650; }
.decision-row textarea {
  min-height: 42px;
  resize: vertical;
  padding: 9px 11px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-deep);
  color: var(--color-ink);
  font-size: 12px;
}

.decision-actions { display: flex; align-items: stretch; gap: 8px; }
.decision-actions button {
  min-height: 42px;
  padding: 0 14px;
  border: 1px solid var(--color-forest);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-forest);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.decision-actions .merge-button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  background: var(--color-forest);
  color: var(--color-on-accent);
}

.decision-actions button:disabled { opacity: 0.5; cursor: wait; }
.decision-row textarea:focus-visible,
.decision-actions button:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
.quiet-state { margin: 0; padding: 28px 0; color: var(--color-muted); }
.error { color: var(--color-danger); font-size: 12px; }

@media (max-width: 760px) {
  .identity-comparison { grid-template-columns: 1fr; gap: 16px; }
  .compare-icon { transform: rotate(90deg); }
  .decision-row { grid-template-columns: 1fr; }
  .decision-actions { flex-wrap: wrap; }
}
</style>
