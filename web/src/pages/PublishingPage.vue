<script setup lang="ts">
import { messageRef, publishingText as t } from '@/stores/locale'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { PhBroadcast, PhShieldCheck } from '@phosphor-icons/vue'
import {
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogOverlay,
  AlertDialogPortal,
  AlertDialogRoot,
  AlertDialogTitle,
} from 'reka-ui'

import type { components } from '@/api/schema'
import { useOrderedSelection, type LibraryWork } from '@/features/library/ordered-selection'
import PublishingHistory from '@/features/publishing/components/PublishingHistory.vue'
import PublishingPreview from '@/features/publishing/components/PublishingPreview.vue'
import PublishingSelection from '@/features/publishing/components/PublishingSelection.vue'
import PublishingSettings from '@/features/publishing/components/PublishingSettings.vue'
import { apiJson } from '@/lib/json-api'
import { useLocale } from '@/stores/locale'
import { useJobStore, type JobSummary } from '@/stores/jobs'

type FormalReport = components['schemas']['AnalysisReportSummaryResponse']
type GitDiagnostic = components['schemas']['GitPublishingDiagnostic']
type PublicationSelection = components['schemas']['PublicationSelection']
type PreviewEnvelope = components['schemas']['PublishingPreviewEnvelope']
type PublishingJob = components['schemas']['PublishingJobResponse']

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled'])
const ACTIVE_STATUSES = new Set(['queued', 'running'])

const jobStore = useJobStore()
const { locale } = useLocale()
const maximum = 100
const { selectedWorks, selectedIds, toggle, move } = useOrderedSelection(maximum)
const diagnostic = ref<GitDiagnostic>()
const reportIds = ref<Record<string, string>>({})
const reportsByWork = ref<Record<string, FormalReport[]>>({})
const reportLoading = ref<Record<string, boolean>>({})
const reportErrors = ref<Record<string, boolean>>({})
const slug = ref('paper-reader-picks')
const publicTitle = ref(t('我的论文精选'))
const publicIntro = ref('')
const previewEnvelope = ref<PreviewEnvelope>()
const loading = ref(true)
const previewing = ref(false)
const publishing = ref(false)
const actionError = messageRef(t)
const statusMessage = messageRef(t)
const confirmationOpen = ref(false)
const composer = ref<InstanceType<typeof PublishingSettings>>()
const preview = ref<InstanceType<typeof PublishingPreview>>()
const jobDetails = ref<Record<string, PublishingJob>>({})
const jobKeys = new Map<string, string>()
const pollTimers = new Map<string, number>()
let disposed = false

const selectionProblem = computed(() => {
  if (!slug.value.trim()) return t('请填写公开地址。')
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug.value.trim())) {
    return t('公开地址只能使用小写字母、数字和单个连字符。')
  }
  if (!publicTitle.value.trim()) return t('请填写公开标题。')
  if (publicTitle.value.trim().length > 200) return t('公开标题不能超过 200 个字符。')
  if (publicIntro.value.length > 4_000) return t('公开导语不能超过 4,000 个字符。')
  if (selectedIds.value.length === 0) return t('请至少选择一篇论文。')
  return ''
})

const canPreview = computed(
  () => !loading.value && !previewing.value && !selectionProblem.value,
)

const publicationJobs = computed(() => Object.values(jobDetails.value).sort((left, right) =>
  Date.parse(right.created_at) - Date.parse(left.created_at)))

function operationKey(prefix: string): string {
  const suffix = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${suffix}`
}

function summarize(job: PublishingJob): JobSummary {
  return {
    id: job.id,
    kind: job.kind,
    status: job.status,
    progress: job.progress,
    cancel_requested: job.cancel_requested,
    pause_requested: false,
  }
}

function summaryKey(job: JobSummary): string {
  return `${job.status}:${job.progress}:${job.cancel_requested}:${job.pause_requested}`
}

function retryable(job: PublishingJob): boolean {
  return job.result?.retryable === true
}

function currentSelection(): PublicationSelection {
  return {
    slug: slug.value.trim(),
    title: publicTitle.value.trim(),
    public_intro: publicIntro.value.trim(),
    display_language: locale.value,
    works: selectedIds.value.map((workId) => ({
      work_id: workId,
      report_id: reportIds.value[workId] || null,
    })),
  }
}

function invalidateMessages(): void {
  actionError.value = ''
  statusMessage.value = ''
}

async function loadReports(workId: string): Promise<void> {
  if (reportsByWork.value[workId] || reportLoading.value[workId]) return
  reportLoading.value = { ...reportLoading.value, [workId]: true }
  reportErrors.value = { ...reportErrors.value, [workId]: false }
  try {
    const reports = await apiJson<FormalReport[]>(
      `/api/v1/analysis/works/${encodeURIComponent(workId)}/reports`,
    )
    if (!disposed) reportsByWork.value = { ...reportsByWork.value, [workId]: reports }
  } catch {
    if (!disposed) reportErrors.value = { ...reportErrors.value, [workId]: true }
  } finally {
    if (!disposed) reportLoading.value = { ...reportLoading.value, [workId]: false }
  }
}

function toggleWork(work: LibraryWork, checked: boolean): void {
  invalidateMessages()
  toggle(work, checked)
  if (checked && selectedIds.value.includes(work.id)) {
    void loadReports(work.id)
  } else {
    const next = { ...reportIds.value }
    delete next[work.id]
    reportIds.value = next
  }
}

function selectReport(workId: string, reportId: string): void {
  reportIds.value = { ...reportIds.value, [workId]: reportId }
  invalidateMessages()
}

function moveWork(workId: string, direction: -1 | 1): void {
  move(workId, direction)
  invalidateMessages()
}

async function createPreview(): Promise<void> {
  if (!canPreview.value) {
    actionError.value = selectionProblem.value
    return
  }
  previewing.value = true
  invalidateMessages()
  const selection = JSON.stringify(currentSelection())
  try {
    const envelope = await apiJson<PreviewEnvelope>('/api/v1/publishing/previews', {
      method: 'POST',
      body: selection,
    })
    if (disposed || selection !== JSON.stringify(currentSelection())) return
    previewEnvelope.value = envelope
    await nextTick()
    preview.value?.focusHeading()
  } catch {
    actionError.value = '无法生成安全预览。请检查论文和正式报告是否仍然可用。'
  } finally {
    previewing.value = false
  }
}

async function discardPreview(): Promise<void> {
  previewEnvelope.value = undefined
  confirmationOpen.value = false
  invalidateMessages()
  await nextTick()
  composer.value?.focusHeading()
}

function acceptJob(job: PublishingJob): void {
  jobDetails.value = { ...jobDetails.value, [job.id]: job }
  const summary = summarize(job)
  jobKeys.set(job.id, summaryKey(summary))
  jobStore.upsert(summary)
  if (TERMINAL_STATUSES.has(job.status)) {
    const timer = pollTimers.get(job.id)
    if (timer !== undefined) window.clearTimeout(timer)
    pollTimers.delete(job.id)
  } else {
    schedulePoll(job.id)
  }
}

async function loadJobDetail(jobId: string): Promise<void> {
  try {
    const detail = await apiJson<PublishingJob>(`/api/v1/jobs/${encodeURIComponent(jobId)}`)
    if (!disposed) acceptJob(detail)
  } catch {
    // The durable task remains visible in the global drawer and will be retried on the next event.
  }
}

function schedulePoll(jobId: string): void {
  if (disposed || pollTimers.has(jobId)) return
  const timer = window.setTimeout(async () => {
    pollTimers.delete(jobId)
    await loadJobDetail(jobId)
    const current = jobDetails.value[jobId]
    if (current && ACTIVE_STATUSES.has(current.status)) schedulePoll(jobId)
  }, 1_000)
  pollTimers.set(jobId, timer)
}

async function syncJobs(): Promise<void> {
  const publications = jobStore.jobs.filter((job) => job.kind === 'publishing.publish')
  await Promise.all(publications.map(async (summary) => {
    const key = summaryKey(summary)
    if (jobKeys.get(summary.id) === key) return
    jobKeys.set(summary.id, key)
    await loadJobDetail(summary.id)
  }))
}

async function confirmTarget(targetFingerprint: string): Promise<void> {
  await apiJson('/api/v1/publishing/settings/confirm', {
    method: 'POST',
    body: JSON.stringify({ target_fingerprint: targetFingerprint }),
  })
}

async function publishPreview(): Promise<void> {
  const envelope = previewEnvelope.value
  const targetFingerprint = envelope?.git.target_fingerprint
  if (!envelope || !envelope.git.enabled || !targetFingerprint || publishing.value) return
  publishing.value = true
  confirmationOpen.value = false
  invalidateMessages()
  try {
    await confirmTarget(targetFingerprint)
    const created = await apiJson<PublishingJob>('/api/v1/publishing/jobs', {
      method: 'POST',
      headers: { 'Idempotency-Key': operationKey('publication') },
      body: JSON.stringify({
        preview: envelope.preview,
        target_fingerprint: targetFingerprint,
      }),
    })
    acceptJob(created)
    statusMessage.value = TERMINAL_STATUSES.has(created.status)
      ? '发布任务已经结束；结果保留在下方记录中。'
      : '公开快照已冻结，发布任务已经建立。'
  } catch {
    actionError.value = '无法启动发布。Git 目标可能已经变化，请返回并重新预览。'
  } finally {
    publishing.value = false
  }
}

async function retryPublication(job: PublishingJob): Promise<void> {
  const target = diagnostic.value?.target_fingerprint ?? previewEnvelope.value?.git.target_fingerprint
  if (!target || publishing.value || !retryable(job)) return
  publishing.value = true
  invalidateMessages()
  try {
    await confirmTarget(target)
    const retried = await apiJson<PublishingJob>(
      `/api/v1/publishing/jobs/${encodeURIComponent(job.id)}/retry`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': operationKey('publication-retry') },
      },
    )
    acceptJob(retried)
    statusMessage.value = retried.status === 'succeeded'
      ? '冻结快照已成功发布；原失败记录仍然保留。'
      : '已使用同一份冻结快照建立重试任务。'
  } catch {
    actionError.value = '无法重试这份冻结快照。请核对 Git 目标，必要时重新生成预览。'
  } finally {
    publishing.value = false
  }
}

async function cancelPublication(job: PublishingJob): Promise<void> {
  if (job.cancel_requested || !ACTIVE_STATUSES.has(job.status)) return
  invalidateMessages()
  try {
    const summary = await apiJson<JobSummary>(
      `/api/v1/jobs/${encodeURIComponent(job.id)}/cancel`,
      { method: 'POST' },
    )
    jobStore.upsert(summary)
    jobDetails.value = {
      ...jobDetails.value,
      [job.id]: {
        ...job,
        status: summary.status,
        progress: summary.progress,
        cancel_requested: summary.cancel_requested,
      },
    }
    jobKeys.set(job.id, summaryKey(summary))
    schedulePoll(job.id)
  } catch {
    actionError.value = '暂时无法发送取消请求；任务状态仍会继续更新。'
  }
}

async function loadPage(): Promise<void> {
  loading.value = true
  actionError.value = ''
  try {
    const result = await apiJson<GitDiagnostic>('/api/v1/publishing/git')
    if (!disposed) diagnostic.value = result
  } catch {
    if (!disposed) actionError.value = '暂时无法检查 Git 发布目标。'
  }
  if (disposed) return
  await jobStore.refresh()
  if (!disposed) await syncJobs()
  if (!disposed) loading.value = false
}

watch(locale, () => {
  previewEnvelope.value = undefined
  confirmationOpen.value = false
})

watch(
  () => jobStore.jobs
    .filter((job) => job.kind === 'publishing.publish')
    .map((job) => `${job.id}:${summaryKey(job)}`)
    .join('|'),
  () => { if (!disposed) void syncJobs() },
)

onMounted(() => void loadPage())
onBeforeUnmount(() => {
  disposed = true
  for (const timer of pollTimers.values()) window.clearTimeout(timer)
  pollTimers.clear()
})
</script>

<template>
  <div class="publishing-page">
    <header class="page-header">
      <div>
        <p class="eyebrow">{{ t('明确选择后发布') }}</p>
        <h1>{{ t('精选发布') }}</h1>
        <p class="lede">
          {{ t('从本地论文库制作一份只读公开书单。预览不会冻结快照，也不会运行任何 Git 命令； 只有最后一次确认才会发布。') }}
        </p>
      </div>
      <div class="privacy-mark">
        <PhShieldCheck :size="25" weight="thin" aria-hidden="true" />
        <span>{{ t('隐私白名单') }}</span>
        <strong>{{ t('默认拒绝') }}</strong>
      </div>
    </header>

    <p v-if="actionError" class="message error" role="alert">{{ actionError }}</p>
    <p v-if="statusMessage" class="message" role="status">{{ statusMessage }}</p>
    <p v-if="loading" class="quiet-state" role="status">{{ t('正在核对 Git 目标和发布记录…') }}</p>

    <section v-show="!previewEnvelope" class="composer">
      <PublishingSettings
        ref="composer"
        :slug="slug"
        :title="publicTitle"
        :intro="publicIntro"
        @update:slug="slug = $event"
        @update:title="publicTitle = $event"
        @update:intro="publicIntro = $event"
        @change="invalidateMessages"
      />

      <PublishingSelection
        :maximum="maximum"
        :selected-ids="selectedIds"
        :selected-works="selectedWorks"
        :report-ids="reportIds"
        :reports-by-work="reportsByWork"
        :report-loading="reportLoading"
        :report-errors="reportErrors"
        @toggle="toggleWork"
        @move="moveWork"
        @select-report="selectReport"
      />

      <div class="preview-action">
        <p :class="{ invalid: selectionProblem }">
          {{ selectionProblem || t('下一步只生成本地预览，不创建快照，也不执行 Git。') }}
        </p>
        <button
          data-test="create-publishing-preview"
          class="primary-action"
          type="button"
          :disabled="!canPreview"
          @click="createPreview"
        >
          <PhShieldCheck :size="18" aria-hidden="true" />
          {{ previewing ? t('正在核对白名单…') : t('生成安全预览') }}
        </button>
      </div>
    </section>

    <PublishingPreview
      v-if="previewEnvelope"
      ref="preview"
      :envelope="previewEnvelope"
      :publishing="publishing"
      @discard="discardPreview"
      @open-confirmation="confirmationOpen = true"
    />
    <PublishingHistory
      v-if="publicationJobs.length"
      :jobs="publicationJobs"
      :publishing="publishing"
      @retry="retryPublication"
      @cancel="cancelPublication"
    />

    <AlertDialogRoot
      :open="confirmationOpen"
      @update:open="(value) => { if (!publishing) confirmationOpen = value }"
    >
      <AlertDialogPortal>
        <AlertDialogOverlay class="confirmation-overlay" />
        <AlertDialogContent data-test="publishing-confirmation" class="confirmation-dialog">
          <div class="dialog-mark" aria-hidden="true">
            <PhBroadcast :size="21" />
          </div>
          <AlertDialogTitle>{{ t('冻结并发布这份公开快照？') }}</AlertDialogTitle>
          <AlertDialogDescription>
            {{ t('这会为当前预览创建不可变快照，在隔离工作树中生成四个白名单文件， 并向 published 分支快进推送。主工作树不会改变。') }}
          </AlertDialogDescription>
          <dl v-if="previewEnvelope" class="confirmation-target">
            <div>
              <dt>{{ t('远端') }}</dt>
              <dd class="technical-value">{{ previewEnvelope.git.remote_url }}</dd>
            </div>
            <div>
              <dt>{{ t('分支') }}</dt>
              <dd class="technical-value">{{ previewEnvelope.git.branch }}</dd>
            </div>
            <div>
              <dt>{{ t('文件') }}</dt>
              <dd>{{ previewEnvelope.files.length }} {{ t('个白名单文件') }}</dd>
            </div>
          </dl>
          <div class="dialog-actions">
            <AlertDialogCancel as-child>
              <button type="button" :disabled="publishing">{{ t('继续核对') }}</button>
            </AlertDialogCancel>
            <button
              data-test="confirm-publishing"
              class="confirm"
              type="button"
              :disabled="publishing"
              @click="publishPreview"
            >
              {{ publishing ? t('正在建立发布任务…') : t('确认冻结并发布') }}
            </button>
          </div>
        </AlertDialogContent>
      </AlertDialogPortal>
    </AlertDialogRoot>
  </div>
</template>

<style scoped>
.publishing-page {
  max-width: 1260px;
  margin: 0 auto;
}

.page-header,
.preview-action {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
}

.page-header {
  align-items: flex-end;
  padding-bottom: 28px;
  border-bottom: 1px solid var(--color-line);
}

.eyebrow {
  margin: 0 0 8px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 750;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

h1,
p {
  margin-top: 0;
}

h1 {
  margin-bottom: 10px;
  font-family: var(--font-editorial);
  font-size: clamp(38px, 5vw, 58px);
  font-weight: 500;
  letter-spacing: -0.035em;
}

.lede {
  max-width: 720px;
  margin-bottom: 0;
  color: var(--color-muted);
  line-height: 1.8;
}

.privacy-mark {
  min-width: 150px;
  padding-left: 20px;
  border-left: 1px solid var(--color-line);
  color: var(--color-forest);
}

.privacy-mark span,
.privacy-mark strong {
  display: block;
}

.privacy-mark span {
  margin-top: 9px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.privacy-mark strong {
  margin-top: 2px;
  font-family: var(--font-editorial);
  font-size: 17px;
}

.message {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 20px 0 0;
  padding: 12px 14px;
  border-left: 3px solid var(--color-forest);
  background: var(--color-forest-soft);
  font-size: 13px;
  line-height: 1.6;
}

.message.error {
  border-color: var(--color-danger);
  background: var(--color-wine-soft);
}

.quiet-state {
  margin-top: 42px;
  padding: 36px 0;
  border-block: 1px solid var(--color-line);
  color: var(--color-muted);
  text-align: center;
}

.composer {
  margin-top: clamp(38px, 6vw, 68px);
}

.preview-action {
  align-items: center;
  justify-content: flex-end;
  margin-top: 30px;
  padding-top: 22px;
  border-top: 1px solid var(--color-line);
}

.preview-action p {
  margin: 0 auto 0 0;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.preview-action p.invalid {
  color: var(--color-danger);
}

.primary-action {
  min-height: 42px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 17px;
  border: 1px solid var(--color-forest);
  border-radius: var(--radius-sm);
  background: var(--color-forest);
  color: var(--color-on-accent);
  font-weight: 650;
  cursor: pointer;
}

.primary-action:disabled {
  opacity: 0.45;
  cursor: default;
}

.confirmation-overlay {
  position: fixed;
  z-index: 30;
  inset: 0;
  background: rgb(10 22 15 / 42%);
}

.confirmation-dialog {
  position: fixed;
  z-index: 31;
  top: 50%;
  left: 50%;
  width: min(560px, calc(100vw - 32px));
  max-height: calc(100dvh - 32px);
  padding: 28px;
  overflow-y: auto;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
  background: var(--color-paper-raised);
  color: var(--color-ink);
  box-shadow: 0 24px 70px rgb(10 22 15 / 22%);
  transform: translate(-50%, -50%);
}

.dialog-mark {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  margin-bottom: 18px;
  border-radius: var(--radius-sm);
  background: var(--color-wine-soft);
  color: var(--color-wine);
}

.confirmation-dialog h2 {
  margin: 0 0 10px;
  font-family: var(--font-editorial);
  font-size: 27px;
  font-weight: 500;
}

.confirmation-dialog > p {
  color: var(--color-muted);
  font-size: 12px;
  line-height: 1.75;
}

.confirmation-target {
  margin: 18px 0 0;
}

.confirmation-target > div {
  display: grid;
  grid-template-columns: 70px minmax(0, 1fr);
  gap: 12px;
  padding: 7px 0;
  border-bottom: 1px solid var(--color-line);
}

.confirmation-target dt {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.confirmation-target dd {
  min-width: 0;
  margin: 0;
  font-size: var(--type-meta);
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 24px;
}

.dialog-actions button {
  min-height: 40px;
  padding: 0 15px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-ink);
  cursor: pointer;
}

.dialog-actions .confirm {
  border-color: var(--color-wine);
  background: var(--color-wine);
  color: #fffdf7;
  font-weight: 650;
}

button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

@media (max-width: 680px) {
  .page-header,
  .preview-action {
    align-items: flex-start;
    flex-direction: column;
  }

  .privacy-mark {
    min-width: 0;
    padding: 0;
    border: 0;
  }

  .preview-action p {
    margin: 0;
  }

  .primary-action {
    width: 100%;
  }

  .dialog-actions {
    flex-direction: column-reverse;
  }

  .dialog-actions button {
    width: 100%;
  }
}
</style>
