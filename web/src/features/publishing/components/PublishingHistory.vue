<script setup lang="ts">
import { currentLocale, publishingText as t } from '@/stores/locale'
import { computed } from 'vue'
import { PhCheckCircle, PhGitBranch, PhX } from '@phosphor-icons/vue'

import type { components } from '@/api/schema'

type PublishingJob = components['schemas']['PublishingJobResponse']

const ACTIVE_STATUSES = new Set(['queued', 'running'])

const props = defineProps<{
  jobs: readonly PublishingJob[]
  publishing: boolean
}>()

defineEmits<{
  retry: [job: PublishingJob]
  cancel: [job: PublishingJob]
}>()

const unfinishedJobs = computed(() => props.jobs.filter((job) => job.status !== 'succeeded'))
const successfulJobs = computed(() => props.jobs.filter((job) => job.status === 'succeeded'))

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return t('日期未知')
  return new Intl.DateTimeFormat(currentLocale(), {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

function resultString(job: PublishingJob, key: 'snapshot_id' | 'commit_sha' | 'branch'): string | undefined {
  const value = job.result?.[key]
  return typeof value === 'string' ? value : undefined
}

function retryable(job: PublishingJob): boolean {
  return job.result?.retryable === true
}

function safeFailure(job: PublishingJob): string {
  const messages: Record<string, string> = {
    push_rejected: t('远端发布分支已经变化；冻结快照仍在本机，可以核对目标后重试。'),
    remote_unavailable: t('暂时无法连接发布远端；冻结快照仍在本机，可以稍后重试。'),
    git_timeout: t('Git 操作超时；冻结快照仍在本机，可以稍后重试。'),
    target_changed: t('Git 仓库或远端设置已经变化。请重新预览并核对发布目标。'),
    snapshot_unavailable: t('冻结快照已不可用，不能继续发布；请重新生成公开预览。'),
    snapshot_integrity: t('冻结快照未通过完整性校验，不能继续发布。'),
    internal_error: t('发布没有完成；主工作树和既有成功版本均未改变。'),
  }
  if (job.status === 'cancelled') return t('发布已取消；冻结快照仍保留，可在核对目标后重试。')
  return messages[job.error_code ?? ''] ?? t('发布没有完成；主工作树和既有成功版本均未改变。')
}

function phaseLabel(job: PublishingJob): string {
  if (job.cancel_requested && ACTIVE_STATUSES.has(job.status)) return t('正在取消')
  if (job.status === 'queued') return t('冻结公开快照，等待执行')
  if (job.status === 'running') {
    if (job.checkpoint?.phase === 'interrupted') return t('上次执行中断，等待恢复')
    return t('在临时工作树构建并推送')
  }
  if (job.status === 'succeeded') return t('发布完成')
  if (job.status === 'cancelled') return t('已取消')
  return t('发布失败')
}
</script>

<template>
  <section class="publishing-jobs" aria-labelledby="publishing-jobs-title">
    <div class="section-heading">
      <div><span aria-hidden="true">04</span><div><p>{{ t('持久任务') }}</p><h2 id="publishing-jobs-title">{{ t('发布状态与历史') }}</h2></div></div>
      <small>{{ t('失败不会覆盖旧版本') }}</small>
    </div>

    <div class="job-history-grid">
      <section aria-labelledby="current-publication-jobs">
        <h3 id="current-publication-jobs">{{ t('当前与失败任务') }}</h3>
        <p v-if="unfinishedJobs.length === 0" class="empty-note">{{ t('当前没有等待处理的发布任务。') }}</p>
        <ol v-else class="publication-job-list">
          <li v-for="job in unfinishedJobs" :key="job.id" :data-test="`publication-job-${job.id}`">
            <div class="job-topline">
              <span :class="['job-state', job.status]">{{ phaseLabel(job) }}</span>
              <time :datetime="job.updated_at">{{ formatDate(job.updated_at) }}</time>
            </div>
            <progress :value="job.progress" max="100">{{ job.progress }}%</progress>
            <p v-if="job.status === 'failed' || job.status === 'cancelled'" class="failure-copy">{{ safeFailure(job) }}</p>
            <dl v-if="resultString(job, 'snapshot_id')" class="job-result">
              <div><dt>{{ t('冻结快照') }}</dt><dd class="technical-value">{{ resultString(job, 'snapshot_id') }}</dd></div>
            </dl>
            <div class="job-actions">
              <button
                v-if="ACTIVE_STATUSES.has(job.status)"
                :data-test="`cancel-publication-${job.id}`"
                type="button"
                :disabled="job.cancel_requested"
                @click="$emit('cancel', job)"
              ><PhX :size="15" aria-hidden="true" />{{ job.cancel_requested ? t('正在取消') : t('取消发布') }}</button>
              <button
                v-if="(job.status === 'failed' || job.status === 'cancelled') && retryable(job)"
                :data-test="`retry-publication-${job.id}`"
                type="button"
                :disabled="publishing"
                @click="$emit('retry', job)"
              ><PhGitBranch :size="15" aria-hidden="true" />{{ t('核对目标并重试') }}</button>
            </div>
          </li>
        </ol>
      </section>

      <section aria-labelledby="successful-publications">
        <h3 id="successful-publications">{{ t('成功快照') }}</h3>
        <p v-if="successfulJobs.length === 0" class="empty-note">{{ t('还没有成功发布的公开版本。') }}</p>
        <ol v-else class="success-list">
          <li v-for="job in successfulJobs" :key="job.id">
            <PhCheckCircle :size="19" aria-hidden="true" />
            <div>
              <div class="job-topline"><strong>{{ t('已发布到') }} {{ resultString(job, 'branch') || 'published' }}</strong><time :datetime="job.updated_at">{{ formatDate(job.updated_at) }}</time></div>
              <dl class="job-result">
                <div><dt>{{ t('快照') }}</dt><dd class="technical-value">{{ resultString(job, 'snapshot_id') || t('记录缺失') }}</dd></div>
                <div><dt>{{ t('提交') }}</dt><dd class="technical-value">{{ resultString(job, 'commit_sha') || t('记录缺失') }}</dd></div>
                <div><dt>{{ t('分支') }}</dt><dd class="technical-value">{{ resultString(job, 'branch') || 'published' }}</dd></div>
              </dl>
            </div>
          </li>
        </ol>
      </section>
    </div>
  </section>
</template>

<style scoped>
.publishing-jobs { margin-top: clamp(38px, 6vw, 68px); padding-top: 30px; border-top: 1px solid var(--color-line); }
.section-heading,
.job-topline { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; }
.section-heading { align-items: center; margin-bottom: 20px; }
.section-heading > div { display: flex; align-items: flex-start; gap: 12px; }
.section-heading > div > span { padding-top: 3px; color: var(--color-wine); font-family: var(--font-editorial); font-size: 13px; }
.section-heading p { margin: 0 0 8px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: 0.14em; text-transform: uppercase; }
.section-heading h2,
.job-history-grid h3 { margin-top: 0; font-family: var(--font-editorial); font-weight: 500; }
.section-heading h2 { margin-bottom: 0; font-size: 26px; }
.section-heading small { color: var(--color-muted); font-size: var(--type-meta); }
.job-history-grid { display: grid; grid-template-columns: minmax(0, 0.9fr) minmax(360px, 1.1fr); gap: clamp(30px, 5vw, 58px); margin-top: 26px; }
.job-history-grid h3 { margin-bottom: 14px; font-size: 18px; }
.publication-job-list,
.success-list { padding: 0; margin: 0; border-block: 1px solid var(--color-line); list-style: none; }
.publication-job-list li + li,
.success-list li + li { border-top: 1px solid var(--color-line); }
.publication-job-list > li,
.success-list > li { padding: 16px 4px; }
.job-topline { align-items: baseline; gap: 14px; }
.job-topline time { color: var(--color-muted); font-size: var(--type-meta); }
.job-state { color: var(--color-muted-strong); font-size: 12px; font-weight: 650; }
.job-state.failed,
.job-state.cancelled { color: var(--color-danger); }
.publication-job-list progress { width: 100%; height: 4px; margin: 13px 0; accent-color: var(--color-forest); }
.failure-copy { margin-bottom: 12px; color: var(--color-danger); font-size: var(--type-meta); line-height: 1.65; }
.job-result { margin: 0; }
.job-result > div { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 12px; padding: 7px 0; border-bottom: 1px solid var(--color-line); }
.job-result dt { color: var(--color-muted); font-size: var(--type-meta); }
.job-result dd { min-width: 0; margin: 0; font-size: var(--type-meta); }
.job-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 12px; margin-top: 13px; }
.job-actions button { display: inline-flex; align-items: center; gap: 6px; padding: 5px 0; border: 0; background: transparent; color: var(--color-forest); font-size: var(--type-meta); font-weight: 650; cursor: pointer; }
.job-actions button:disabled { opacity: 0.3; cursor: default; }
.success-list > li { display: grid; grid-template-columns: 22px minmax(0, 1fr); gap: 10px; }
.success-list > li > svg { color: var(--color-forest); }
.success-list strong { font-size: 12px; }
.empty-note { color: var(--color-muted); font-size: 12px; line-height: 1.7; }
button:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
@media (max-width: 980px) { .job-history-grid { grid-template-columns: 1fr; } }
@media (max-width: 680px) { .job-topline { display: grid; gap: 4px; } }
</style>
