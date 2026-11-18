<script setup lang="ts">
import { messageRef, messageTemplate, settingsText as t } from '@/stores/locale'
import { ref } from 'vue'
import {
  PhArchiveBox,
  PhCheckCircle,
  PhEye,
  PhLockKey,
  PhTrash,
} from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'
import { formatBytes } from '@/lib/formatters'

type CleanupPreview = components['schemas']['CleanupPreviewResponse']
type CleanupResult = components['schemas']['CleanupResultResponse']
type CleanupCandidate = components['schemas']['CleanupCandidateResponse']

const cleanupPreview = ref<CleanupPreview>()
const previewingCleanup = ref(false)
const applyingCleanup = ref(false)
const notice = messageRef(t)
const error = messageRef(t)

function reasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    feedback_not_disliked: t('未标记不喜欢'),
    favorite: t('收藏'),
    pinned: t('置顶'),
    analysis_available: t('已有分析'),
    annotation: t('带标注'),
    within_retention: t('仍在保留期'),
    path_conflict: t('文件路径冲突'),
    unsafe_path: t('文件路径需人工检查'),
  }
  return labels[reason] ?? t('受保护')
}

function candidateLabel(candidate: CleanupCandidate): string {
  if (candidate.eligible) return t('可以删除')
  return candidate.protected_reasons.map(reasonLabel).join('、')
}

async function previewCleanup(): Promise<void> {
  previewingCleanup.value = true
  error.value = ''
  notice.value = ''
  cleanupPreview.value = undefined
  try {
    cleanupPreview.value = await apiJson<CleanupPreview>('/api/v1/library/cache-cleanup/preview', {
      method: 'POST',
      body: JSON.stringify({ retention_days: 90 }),
    })
  } catch {
    error.value = '无法生成安全的清理预览；没有文件被删除。'
  } finally {
    previewingCleanup.value = false
  }
}

async function applyCleanup(): Promise<void> {
  if (!cleanupPreview.value || cleanupPreview.value.eligible_count === 0) return
  applyingCleanup.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await apiJson<CleanupResult>('/api/v1/library/cache-cleanup/apply', {
      method: 'POST',
      body: JSON.stringify({ preview_token: cleanupPreview.value.preview_token }),
    })
    notice.value = result.cancelled
      ? '清理已安全取消，没有继续删除文件。'
      : messageTemplate('已删除 {0} 个缓存文件，释放 {1}。', result.deleted_objects, formatBytes(result.reclaimed_bytes))
    cleanupPreview.value = undefined
  } catch {
    error.value = '清理预览已经变化或无法安全执行，请重新生成预览。'
    cleanupPreview.value = undefined
  } finally {
    applyingCleanup.value = false
  }
}
</script>

<template>
  <section class="reader-storage" aria-labelledby="reader-storage-title">
    <div class="section-heading">
      <div>
        <p>{{ t('预览后执行') }}</p>
        <h2 id="reader-storage-title">{{ t('论文全文缓存') }}</h2>
      </div>
      <PhArchiveBox :size="24" aria-hidden="true" />
    </div>

    <p class="section-intro">
      {{ t('缓存清理与模型管理彼此独立。这里只处理合法取得的论文全文文件；论文元数据、反馈、报告与标注不会随缓存一起删除。') }}
    </p>
    <p v-if="error" class="message error" role="alert">{{ error }}</p>
    <p v-if="notice" class="message success" role="status">{{ notice }}</p>

    <article class="cleanup-card">
      <div class="card-title">
        <div class="card-icon"><PhTrash :size="20" aria-hidden="true" /></div>
        <div>
          <span class="eyebrow">{{ t('90 天保留规则') }}</span>
          <h3>{{ t('清理可移除的全文缓存') }}</h3>
        </div>
      </div>
      <p>
        {{ t('仅检查超过 90 天且标记为“不喜欢”的全文。收藏、置顶、已分析或带标注的论文始终受保护，元数据与反馈不会删除。') }}
      </p>
      <button
        data-test="preview-cleanup"
        type="button"
        :disabled="previewingCleanup || applyingCleanup"
        @click="previewCleanup"
      >
        <PhEye :size="17" aria-hidden="true" />
        {{ previewingCleanup ? t('正在生成预览…') : t('先查看清理预览') }}
      </button>

      <div v-if="cleanupPreview" class="cleanup-preview" aria-live="polite">
        <div class="preview-summary">
          <strong>{{ cleanupPreview.eligible_count }} {{ t('个可移除') }}</strong>
          <span>{{ cleanupPreview.protected_count }} {{ t('个受保护') }}</span>
          <span>{{ t('可回收') }} {{ formatBytes(cleanupPreview.reclaimable_bytes) }}</span>
        </div>
        <ul>
          <li
            v-for="candidate in cleanupPreview.candidates"
            :key="candidate.content_sha256"
            :data-test="candidate.eligible ? 'cleanup-removable' : 'cleanup-protected'"
            :class="{ protected: !candidate.eligible }"
          >
            <span class="candidate-state">
              <PhLockKey v-if="!candidate.eligible" :size="16" aria-hidden="true" />
              <PhTrash v-else :size="16" aria-hidden="true" />
              {{ candidateLabel(candidate) }}
            </span>
            <small>
              {{ candidate.content_sha256.slice(0, 10) }}… · {{ formatBytes(candidate.byte_size) }} · {{ candidate.age_days }} {{ t('天') }}
            </small>
          </li>
        </ul>
        <button
          v-if="cleanupPreview.eligible_count > 0"
          data-test="apply-cleanup"
          class="destructive-action"
          type="button"
          :disabled="applyingCleanup"
          @click="applyCleanup"
        >
          <PhTrash :size="17" aria-hidden="true" />
          {{ applyingCleanup ? t('正在清理…') : t('清理 {0} 个可移除文件', cleanupPreview.eligible_count) }}
        </button>
        <p v-else class="ready-note">
          <PhCheckCircle :size="17" aria-hidden="true" />
          {{ t('当前没有符合规则的可移除缓存') }}
        </p>
      </div>
    </article>
  </section>
</template>

<style scoped>
.reader-storage {
  margin-top: clamp(42px, 6vw, 76px);
}

.section-heading,
.card-title,
.preview-summary,
.candidate-state,
.ready-note,
button {
  display: flex;
  align-items: center;
}

.section-heading {
  justify-content: space-between;
  gap: 18px;
  padding-bottom: 17px;
  border-bottom: 1px solid var(--color-line);
}

.section-heading p,
.eyebrow {
  margin: 0 0 7px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.section-heading h2,
.cleanup-card h3 {
  margin: 0;
  font-family: var(--font-editorial);
  font-weight: 500;
}

.section-heading h2 {
  font-size: clamp(28px, 4vw, 39px);
}

.section-intro {
  max-width: 760px;
  margin: 18px 0 0;
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.75;
}

.cleanup-card {
  max-width: 840px;
  margin-top: 24px;
  padding: clamp(20px, 3vw, 28px);
  border: 1px solid var(--color-line);
  border-top: 3px solid var(--color-wine);
  border-radius: var(--radius-sm);
  background: var(--color-paper);
}

.card-title {
  gap: 12px;
  margin-bottom: 16px;
}

.card-icon {
  display: grid;
  flex: 0 0 38px;
  width: 38px;
  height: 38px;
  place-items: center;
  border: 1px solid var(--color-line);
  color: var(--color-wine);
}

.cleanup-card h3 {
  font-size: 22px;
}

.cleanup-card > p {
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.65;
}

button {
  justify-content: center;
  gap: 7px;
  min-height: 38px;
  padding: 7px 13px;
  border: 1px solid var(--color-line-strong);
  border-radius: var(--radius-sm);
  color: var(--color-ink);
  background: transparent;
  font: inherit;
  font-size: var(--type-meta);
  font-weight: 650;
  cursor: pointer;
}

button:hover:not(:disabled) {
  border-color: var(--color-forest);
}

button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.destructive-action {
  margin-top: 15px;
  color: var(--color-wine);
}

.ready-note {
  gap: 7px;
  margin: 16px 0 0;
  color: var(--color-forest);
  font-weight: 650;
}

.cleanup-preview {
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--color-line);
}

.preview-summary {
  flex-wrap: wrap;
  gap: 7px;
}

.preview-summary strong,
.preview-summary span {
  padding: 5px 9px;
  border: 1px solid var(--color-line);
  font-size: var(--type-meta);
}

.preview-summary span {
  color: var(--color-muted);
}

ul {
  display: grid;
  gap: 8px;
  margin: 14px 0 0;
  padding: 0;
  list-style: none;
}

li {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 11px 12px;
  border-left: 3px solid var(--color-wine);
  background: var(--color-surface);
}

li.protected {
  border-left-color: var(--color-forest);
}

.candidate-state {
  gap: 6px;
  font-weight: 650;
}

li small {
  color: var(--color-muted);
  text-align: right;
}

.message {
  margin: 18px 0 0;
  padding: 11px 14px;
  border-left: 3px solid var(--color-forest);
  background: var(--color-forest-hover-soft);
  font-size: 12px;
}

.message.error {
  border-color: var(--color-danger);
  color: var(--color-wine);
}

.message.success {
  color: var(--color-forest);
}

@media (max-width: 600px) {
  .cleanup-card { padding: 18px; }
  li {
    align-items: flex-start;
    flex-direction: column;
  }
  li small { text-align: left; }
}

@media (forced-colors: active) {
  .cleanup-card,
  li,
  button {
    border-color: CanvasText;
  }
}
</style>
