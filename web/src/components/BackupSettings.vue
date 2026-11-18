<script setup lang="ts">
import { currentLocale, messageRef, settingsText as t } from '@/stores/locale'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import {
  PhArchive,
  PhArrowCounterClockwise,
  PhCheckCircle,
  PhDownloadSimple,
  PhLockKey,
  PhWarning,
} from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'
import { formatBytes } from '@/lib/formatters'
import { useJobStore, type JobSummary } from '@/stores/jobs'

type BackupArchive = components['schemas']['BackupArchiveResponse']
type BackupJob = components['schemas']['BackupJobResponse']
type BackupPolicy = components['schemas']['BackupPolicyResponse']
type RestorePreview = components['schemas']['RestorePreviewResponse']

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled', 'pending_restart'])

const jobStore = useJobStore()
const policy = ref<BackupPolicy>()
const archives = ref<BackupArchive[]>([])
const loading = ref(true)
const loadError = messageRef(t)

const encryptBackup = ref(false)
const backupPassword = ref('')
const backupPasswordConfirmation = ref('')
const backupError = messageRef(t)
const backupNotice = messageRef(t)
const backupDownloadId = ref('')
const backupJobId = ref('')
const backupJobFallback = ref<JobSummary>()
const backupCancelRequested = ref(false)

const restoreFile = ref<File>()
const restoreFileInput = ref<HTMLInputElement>()
const restorePassword = ref('')
const restoreError = messageRef(t)
const restoreNotice = messageRef(t)
const restoreJobId = ref('')
const restoreJobFallback = ref<JobSummary>()
const restorePreview = ref<RestorePreview>()
const restorePreviewHeading = ref<HTMLElement>()
const restoreAcknowledged = ref(false)
const restorePendingRestart = ref(false)

const finalizingJobs = new Set<string>()
const finalizedJobs = new Set<string>()

const backupJob = computed(
  () => (backupJobId.value ? jobStore.find(backupJobId.value) : undefined) ?? backupJobFallback.value,
)
const restoreJob = computed(
  () => (restoreJobId.value ? jobStore.find(restoreJobId.value) : undefined) ?? restoreJobFallback.value,
)
const backupBusy = computed(() => ['queued', 'running'].includes(backupJob.value?.status ?? ''))
const restoreBusy = computed(() => ['queued', 'running'].includes(restoreJob.value?.status ?? ''))
const latestArchive = computed(() => archives.value[0])

function operationKey(prefix: string): string {
  const suffix = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${suffix}`
}

function summarize(job: BackupJob): JobSummary {
  return {
    id: job.id,
    kind: job.kind,
    status: job.status,
    progress: job.progress,
    cancel_requested: job.cancel_requested,
    pause_requested: false,
  }
}

function formatDate(value: string | null | undefined): string {
  if (!value) return t('尚未生成')
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return t('日期未知')
  return new Intl.DateTimeFormat(currentLocale(), {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  }).format(date)
}

function resultString(job: BackupJob, key: string): string | undefined {
  const value = job.result?.[key]
  return typeof value === 'string' ? value : undefined
}

function safeRestoreError(code: string | null | undefined): string {
  const errors: Record<string, string> = {
    invalid_password: t('密码不正确，未读取或更改现有论文库。'),
    password_required: t('这个加密备份需要密码；未读取或更改现有论文库。'),
    invalid_package: t('恢复包已损坏或不是有效的 paper-reader 备份，不能继续恢复。'),
    invalid_encrypted_backup: t('加密恢复包无法通过完整性校验，不能继续恢复。'),
    unsupported_version: t('恢复包需要更新版本的 paper-reader，请先升级当前应用。'),
    corrupt_package: t('恢复包已损坏或不完整，不能继续恢复。'),
    future_format_version: t('恢复包来自更新的 paper-reader，请先升级当前应用。'),
    future_schema_revision: t('恢复包的数据版本高于当前应用，请先升级后再试。'),
    expired_preview: t('这份验证结果已经过期，请重新选择恢复包。'),
    fingerprint_mismatch: t('恢复包已发生变化，请重新验证。'),
    cancelled: t('恢复包验证已取消。'),
  }
  return errors[code ?? ''] ?? t('无法安全验证恢复包；现有论文库没有发生变化。')
}

function safeBackupError(code: string | null | undefined): string {
  if (code === 'cancelled') return t('便携备份已取消，没有生成下载文件。')
  if (code === 'insufficient_space') return t('可用磁盘空间不足，未能生成便携备份。')
  return t('暂时无法生成便携备份，请稍后重试。')
}

async function loadSettings(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const [backupPolicy, backupArchives] = await Promise.all([
      apiJson<BackupPolicy>('/api/v1/backups/policy'),
      apiJson<BackupArchive[]>('/api/v1/backups'),
    ])
    policy.value = backupPolicy
    archives.value = backupArchives
  } catch {
    loadError.value = '暂时无法读取备份状态；论文库本身不受影响。'
  } finally {
    loading.value = false
  }
}

function acceptBackupJob(job: BackupJob): void {
  const summary = summarize(job)
  backupJobId.value = job.id
  backupJobFallback.value = summary
  backupCancelRequested.value = job.cancel_requested
  jobStore.upsert(summary)
  if (TERMINAL_STATUSES.has(job.status)) finishBackupJob(job)
}

function finishBackupJob(job: BackupJob): void {
  finalizedJobs.add(job.id)
  if (job.status === 'succeeded') {
    const backupId = resultString(job, 'backup_id')
    if (!backupId) {
      backupError.value = '备份已结束，但下载信息不完整；请在任务抽屉中重试。'
      return
    }
    backupDownloadId.value = backupId
    backupNotice.value = '便携备份已完成。下载链接只指向本机生成的文件。'
    void loadArchiveList()
    return
  }
  backupDownloadId.value = ''
  backupError.value = safeBackupError(job.error_code ?? job.status)
}

async function loadArchiveList(): Promise<void> {
  try {
    archives.value = await apiJson<BackupArchive[]>('/api/v1/backups')
  } catch {
    // The completed download remains available even if refreshing the history fails.
  }
}

async function createBackup(): Promise<void> {
  if (backupBusy.value) return
  backupError.value = ''
  backupNotice.value = ''
  backupDownloadId.value = ''
  backupCancelRequested.value = false

  const password = backupPassword.value
  const confirmation = backupPasswordConfirmation.value
  if (encryptBackup.value && (!password || password !== confirmation)) {
    backupError.value = password ? '两次输入的备份密码不一致。' : '加密备份需要输入并确认密码。'
    return
  }

  const body = {
    kind: 'manual' as const,
    encrypted: encryptBackup.value,
    password: encryptBackup.value ? password : null,
  }
  const request = apiJson<BackupJob>('/api/v1/backups', {
    method: 'POST',
    headers: { 'Idempotency-Key': operationKey('manual-backup') },
    body: JSON.stringify(body),
  })
  backupPassword.value = ''
  backupPasswordConfirmation.value = ''

  try {
    acceptBackupJob(await request)
  } catch {
    backupError.value = '暂时无法启动便携备份，请稍后重试。'
  }
}

async function cancelBackup(): Promise<void> {
  if (!backupJobId.value || backupCancelRequested.value) return
  try {
    const updated = await apiJson<JobSummary>(`/api/v1/jobs/${backupJobId.value}/cancel`, {
      method: 'POST',
    })
    backupCancelRequested.value = true
    backupJobFallback.value = updated
    jobStore.upsert(updated)
  } catch {
    backupError.value = '暂时无法发送取消请求；任务状态仍会继续更新。'
  }
}

function selectRestoreFile(event: Event): void {
  const input = event.currentTarget as HTMLInputElement
  restoreFile.value = input.files?.[0]
  restoreError.value = ''
  restoreNotice.value = ''
  restorePreview.value = undefined
  restoreAcknowledged.value = false
  restorePendingRestart.value = false
}

async function focusRestoreFile(): Promise<void> {
  await nextTick()
  restoreFileInput.value?.focus()
}

async function showRestorePreview(previewId: string): Promise<void> {
  try {
    restorePreview.value = await apiJson<RestorePreview>(
      `/api/v1/backups/restore-previews/${previewId}`,
    )
    restoreAcknowledged.value = false
    restoreError.value = ''
    await nextTick()
    restorePreviewHeading.value?.focus()
  } catch {
    restorePreview.value = undefined
    restoreError.value = '验证结果无法读取或已经过期，请重新选择恢复包。'
    await focusRestoreFile()
  }
}

async function finishRestoreValidation(job: BackupJob): Promise<void> {
  finalizedJobs.add(job.id)
  if (job.status === 'succeeded') {
    const previewId = resultString(job, 'preview_id')
    if (previewId) {
      await showRestorePreview(previewId)
      return
    }
  }
  restorePreview.value = undefined
  restoreError.value = safeRestoreError(job.error_code ?? job.status)
  await focusRestoreFile()
}

function acceptRestoreJob(job: BackupJob): void {
  const summary = summarize(job)
  restoreJobId.value = job.id
  restoreJobFallback.value = summary
  jobStore.upsert(summary)
  if (TERMINAL_STATUSES.has(job.status)) void finishRestoreValidation(job)
}

async function validateRestore(): Promise<void> {
  const file = restoreFile.value
  if (!file || restoreBusy.value) {
    if (!file) {
      restoreError.value = '请先选择一个 paper-reader 备份包。'
      await focusRestoreFile()
    }
    return
  }

  restoreError.value = ''
  restoreNotice.value = ''
  restorePreview.value = undefined
  restoreAcknowledged.value = false
  restorePendingRestart.value = false

  const form = new FormData()
  form.append('package', file)
  if (restorePassword.value) form.append('password', restorePassword.value)
  const request = apiJson<BackupJob>('/api/v1/backups/restore-packages', {
    method: 'POST',
    headers: { 'Idempotency-Key': operationKey('restore-preview') },
    body: form,
  })

  restorePassword.value = ''
  restoreFile.value = undefined
  if (restoreFileInput.value) restoreFileInput.value.value = ''

  try {
    acceptRestoreJob(await request)
  } catch {
    restoreError.value = '恢复包未能安全上传和验证；现有论文库没有发生变化。'
    await focusRestoreFile()
  }
}

async function confirmRestore(): Promise<void> {
  const preview = restorePreview.value
  if (!preview || !restoreAcknowledged.value || restorePendingRestart.value) return
  restoreError.value = ''
  try {
    const job = await apiJson<BackupJob>('/api/v1/backups/restores', {
      method: 'POST',
      headers: { 'Idempotency-Key': operationKey('restore-confirm') },
      body: JSON.stringify({
        preview_id: preview.id,
        fingerprint: preview.fingerprint,
      }),
    })
    jobStore.upsert(summarize(job))
    if (job.status !== 'pending_restart') {
      restoreError.value = safeRestoreError(job.error_code ?? job.status)
      restorePreview.value = undefined
      await focusRestoreFile()
      return
    }
    restorePendingRestart.value = true
    restorePreview.value = undefined
    restoreNotice.value = '恢复已安全排队。请关闭并重新打开 paper-reader，应用会在启动前完成恢复。'
  } catch {
    restoreError.value = '无法确认恢复；现有论文库没有发生变化。请重新验证备份包。'
    restorePreview.value = undefined
    await focusRestoreFile()
  }
}

async function finalizeFromStore(jobId: string, target: 'backup' | 'restore'): Promise<void> {
  if (finalizedJobs.has(jobId) || finalizingJobs.has(jobId)) return
  finalizingJobs.add(jobId)
  try {
    const detail = await apiJson<BackupJob>(`/api/v1/jobs/${jobId}`)
    if (target === 'backup') finishBackupJob(detail)
    else await finishRestoreValidation(detail)
  } catch {
    if (target === 'backup') backupError.value = '任务已经结束，但暂时无法读取备份结果。'
    else {
      restoreError.value = '任务已经结束，但验证结果无法读取；请重新选择恢复包。'
      await focusRestoreFile()
    }
  } finally {
    finalizingJobs.delete(jobId)
  }
}

watch(
  () => backupJob.value?.status,
  (status) => {
    if (status && TERMINAL_STATUSES.has(status) && backupJobId.value) {
      void finalizeFromStore(backupJobId.value, 'backup')
    }
  },
)

watch(
  () => restoreJob.value?.status,
  (status) => {
    if (status && TERMINAL_STATUSES.has(status) && restoreJobId.value) {
      void finalizeFromStore(restoreJobId.value, 'restore')
    }
  },
)

onMounted(() => void loadSettings())
</script>

<template>
  <section class="backup-settings" aria-labelledby="backup-settings-title">
    <header class="section-heading">
      <div>
        <p class="kicker">{{ t('本地资料保全') }}</p>
        <h2 id="backup-settings-title">{{ t('备份与恢复') }}</h2>
      </div>
      <PhArchive :size="25" aria-hidden="true" />
    </header>

    <p class="section-intro">
      {{ t('数据库与合法取得的 PDF 对象可以随身迁移；凭据、模型、日志和缓存始终留在设备之外。') }}
    </p>
    <p v-if="loadError" class="message error" role="alert">{{ loadError }}</p>
    <p v-if="loading" class="quiet" role="status">{{ t('正在读取备份状态…') }}</p>

    <div v-else class="policy-strip" :aria-label="t('自动备份策略')">
      <div>
        <span>{{ t('节律') }}</span>
        <strong>{{ t('每') }} {{ policy?.automatic_interval_days }} {{ t('天') }}</strong>
      </div>
      <div>
        <span>{{ t('保留') }}</span>
        <strong>{{ t('最近') }} {{ policy?.automatic_retention }} {{ t('份') }}</strong>
      </div>
      <div>
        <span>{{ t('上次自动备份') }}</span>
        <strong>{{ formatDate(policy?.latest_automatic_at) }}</strong>
      </div>
      <div>
        <span>{{ t('下次检查') }}</span>
        <strong>{{ formatDate(policy?.next_automatic_at) }}</strong>
      </div>
    </div>

    <div class="backup-columns">
      <article class="backup-column" aria-labelledby="portable-backup-title">
        <div class="column-title">
          <span aria-hidden="true">01</span>
          <div>
            <p>{{ t('导出') }}</p>
            <h3 id="portable-backup-title">{{ t('制作便携备份') }}</h3>
          </div>
        </div>
        <p>
          {{ t('自动备份固定保留最近') }} {{ policy?.automatic_retention ?? 4 }} {{ t('份；手动导出不会挤占这四份轮换记录。') }}
        </p>

        <dl data-test="backup-privacy-boundary" class="backup-privacy-boundary">
          <div>
            <dt>{{ t('进入便携包') }}</dt>
            <dd>{{ t('论文元数据、研究计划、评分证据、提取文本和 OCR 全文、阅读进度、批注、分析报告、完整提示词、论文聊天和偏好、脱敏失败诊断，以及数据库引用的 PDF。') }}</dd>
          </div>
          <div>
            <dt>{{ t('始终排除') }}</dt>
            <dd>{{ t('系统凭据、日志、模型、缓存、临时文件和旧备份。') }}</dd>
          </div>
        </dl>

        <form data-test="create-backup" class="settings-form" @submit.prevent="createBackup">
          <label class="check-row">
            <input v-model="encryptBackup" data-test="encrypt-backup" type="checkbox" />
            <span>
              <strong>{{ t('用密码加密便携包') }}</strong>
              <small>{{ t('密码不会保存，遗失后无法替你找回。') }}</small>
            </span>
          </label>

          <p
            data-test="backup-encryption-notice"
            class="encryption-notice"
            :class="{ warning: !encryptBackup }"
            aria-live="polite"
          >
            {{
              encryptBackup
                ? t('这次导出将使用你输入的密码加密；密码不会保存。')
                : t('当前未启用加密。任何拿到文件的人都能读取其中的私密研究资料。')
            }}
          </p>

          <div v-if="encryptBackup" class="password-pair">
            <label>
              <span>{{ t('备份密码') }}</span>
              <input
                v-model="backupPassword"
                data-test="backup-password"
                type="password"
                autocomplete="new-password"
                required
              />
            </label>
            <label>
              <span>{{ t('再次输入') }}</span>
              <input
                v-model="backupPasswordConfirmation"
                data-test="backup-password-confirm"
                type="password"
                autocomplete="new-password"
                required
              />
            </label>
          </div>

          <button class="primary-action" type="submit" :disabled="backupBusy">
            <PhDownloadSimple :size="17" aria-hidden="true" />
            {{ backupBusy ? t('正在整理论文库…') : t('生成便携备份') }}
          </button>
        </form>

        <div v-if="backupJob" class="job-progress" aria-live="polite">
          <div>
            <span>{{ t('任务进度') }}</span>
            <strong>{{ Math.round(backupJob.progress) }}%</strong>
          </div>
          <progress :value="backupJob.progress" max="100">
            {{ Math.round(backupJob.progress) }}%
          </progress>
          <button
            v-if="backupBusy"
            data-test="cancel-backup"
            class="text-action"
            type="button"
            :disabled="backupCancelRequested"
            @click="cancelBackup"
          >
            {{ backupCancelRequested ? t('正在取消…') : t('取消这次备份') }}
          </button>
        </div>

        <p v-if="backupError" class="message error" role="alert">{{ backupError }}</p>
        <div v-if="backupNotice" class="completion" role="status">
          <PhCheckCircle :size="19" aria-hidden="true" />
          <div>
            <p>{{ backupNotice }}</p>
            <a
              v-if="backupDownloadId"
              data-test="download-backup"
              :href="`/api/v1/backups/${backupDownloadId}/download`"
            >
              {{ t('下载备份文件') }}
            </a>
          </div>
        </div>
        <p v-else-if="latestArchive" class="archive-note">
          {{ t('最近可用：') }}{{ formatDate(latestArchive.created_at) }} · {{ formatBytes(latestArchive.byte_size) }}
          <span>{{ latestArchive.encrypted ? t('已加密') : t('未加密') }}</span>
        </p>
      </article>

      <article class="backup-column restore-column" aria-labelledby="restore-title">
        <div class="column-title">
          <span aria-hidden="true">02</span>
          <div>
            <p>{{ t('恢复') }}</p>
            <h3 id="restore-title">{{ t('先验证，再替换') }}</h3>
          </div>
        </div>
        <p>
          {{ t('第一步只读取并验证包的完整性。确认范围后还需再次勾选同意，真正恢复只会在下次启动前执行。') }}
        </p>

        <form data-test="validate-restore" class="settings-form" @submit.prevent="validateRestore">
          <label>
            <span>{{ t('选择备份包') }}</span>
            <input
              ref="restoreFileInput"
              data-test="restore-file"
              type="file"
              accept=".paper-reader-backup,.enc,application/octet-stream"
              @change="selectRestoreFile"
            />
          </label>
          <label>
            <span>{{ t('解密密码') }} <small>{{ t('仅加密包需要') }}</small></span>
            <input
              v-model="restorePassword"
              data-test="restore-password"
              type="password"
              autocomplete="current-password"
            />
          </label>
          <button class="secondary-action" type="submit" :disabled="!restoreFile || restoreBusy">
            <PhLockKey :size="17" aria-hidden="true" />
            {{ restoreBusy ? t('正在完整验证…') : t('验证恢复包') }}
          </button>
        </form>

        <div v-if="restoreBusy && restoreJob" class="job-progress" aria-live="polite">
          <div>
            <span>{{ t('完整性检查') }}</span>
            <strong>{{ Math.round(restoreJob.progress) }}%</strong>
          </div>
          <progress :value="restoreJob.progress" max="100">
            {{ Math.round(restoreJob.progress) }}%
          </progress>
        </div>

        <p v-if="restoreError" class="message error" role="alert">
          <PhWarning :size="17" aria-hidden="true" />
          {{ restoreError }}
        </p>
        <p v-if="restoreNotice" class="message success" role="status">
          <PhCheckCircle :size="17" aria-hidden="true" />
          {{ restoreNotice }}
        </p>

        <section v-if="restorePreview" class="restore-preview" aria-labelledby="restore-preview-title">
          <div
            id="restore-preview-title"
            ref="restorePreviewHeading"
            data-test="restore-preview-heading"
            class="preview-heading"
            tabindex="-1"
          >
            <PhArrowCounterClockwise :size="20" aria-hidden="true" />
            <div>
              <span>{{ t('验证通过') }}</span>
              <h4>{{ t('这次恢复会带回什么') }}</h4>
            </div>
          </div>
          <dl>
            <div>
              <dt>{{ t('包含') }}</dt>
              <dd>{{ t('数据库与') }} {{ restorePreview.object_count }} {{ t('个 PDF 对象') }}</dd>
            </div>
            <div>
              <dt>{{ t('私密内容') }}</dt>
              <dd>{{ t('提取文本和 OCR 全文、阅读进度、批注、分析报告、完整提示词、论文聊天和偏好、脱敏失败诊断') }}</dd>
            </div>
            <div>
              <dt>{{ t('来源') }}</dt>
              <dd>
                {{ t('来源版本') }} {{ restorePreview.source_app_version }} {{ t('· 数据结构') }}
                {{ restorePreview.source_schema_revision }}
              </dd>
            </div>
            <div>
              <dt>{{ t('不包含') }}</dt>
              <dd>{{ t('凭据、日志、模型、缓存、临时文件和旧备份') }}</dd>
            </div>
            <div>
              <dt>{{ t('任务') }}</dt>
              <dd>{{ t('运行中的任务会标记为已中断，恢复后可以重新运行。') }}</dd>
            </div>
          </dl>
          <p class="replacement-warning">
            {{ t('当前数据库与对象库会先生成回滚快照；恢复失败时，应用会自动退回原状态。') }}
          </p>
          <label class="check-row acknowledgement">
            <input
              v-model="restoreAcknowledged"
              data-test="restore-acknowledgement"
              type="checkbox"
            />
            <span><strong>{{ t('我已核对上述范围，并同意在下次启动时替换当前论文库') }}</strong></span>
          </label>
          <button
            data-test="confirm-restore"
            class="danger-action"
            type="button"
            :disabled="!restoreAcknowledged"
            @click="confirmRestore"
          >
            {{ t('确认并等待重新启动') }}
          </button>
        </section>
      </article>
    </div>
  </section>
</template>

<style scoped>
.backup-settings {
  margin-top: 32px;
  padding: 30px 0 0;
  border-top: 1px solid var(--color-ink);
}

.backup-privacy-boundary {
  margin: 18px 0;
  padding: 4px 0 4px 14px;
  border-left: 3px solid var(--color-wine);
}

.backup-privacy-boundary div + div {
  margin-top: 10px;
}

.backup-privacy-boundary dt {
  color: var(--color-ink);
  font-size: 12px;
  font-weight: 750;
}

.backup-privacy-boundary dd {
  margin: 3px 0 0;
  color: var(--color-muted-strong);
  font-size: 12px;
  line-height: 1.65;
}

.encryption-notice {
  margin: 0;
  padding: 10px 12px;
  border-left: 3px solid var(--color-forest);
  background: var(--color-forest-soft);
  color: var(--color-ink);
  font-size: 12px;
  line-height: 1.55;
}

.encryption-notice.warning {
  border-left-color: var(--color-wine);
  background: var(--color-wine-soft);
}

.section-heading,
.column-title,
.check-row,
.completion,
.message,
.preview-heading,
button,
a {
  display: flex;
  align-items: center;
}

.section-heading {
  justify-content: space-between;
  gap: 24px;
}

.kicker,
.column-title p,
.preview-heading span {
  margin: 0 0 6px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 750;
  letter-spacing: 0.13em;
  text-transform: uppercase;
}

.section-heading h2,
.column-title h3,
.preview-heading h4 {
  margin: 0;
  font-family: var(--font-editorial);
  font-weight: 500;
}

.section-heading h2 {
  font-size: clamp(27px, 3vw, 36px);
}

.section-intro {
  max-width: 720px;
  margin: 12px 0 24px;
  color: var(--color-muted);
  line-height: 1.7;
}

.policy-strip {
  display: grid;
  grid-template-columns: 0.7fr 0.7fr 1.3fr 1.3fr;
  margin-bottom: 30px;
  border-block: 1px solid var(--color-line);
}

.policy-strip div {
  min-width: 0;
  padding: 14px 16px 14px 0;
}

.policy-strip div + div {
  padding-left: 16px;
  border-left: 1px solid var(--color-line);
}

.policy-strip span,
.policy-strip strong {
  display: block;
}

.policy-strip span {
  margin-bottom: 5px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.policy-strip strong {
  font-size: 14px;
  font-weight: 680;
}

.backup-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  border-block: 1px solid var(--color-line);
}

.backup-column {
  min-width: 0;
  padding: 26px 30px 30px 0;
}

.backup-column + .backup-column {
  padding-inline: 30px 0;
  border-left: 1px solid var(--color-line);
}

.column-title {
  gap: 13px;
  margin-bottom: 15px;
}

.column-title > span {
  color: var(--color-wine);
  font-family: var(--font-editorial);
  font-size: 24px;
}

.column-title h3 {
  font-size: 23px;
}

.backup-column > p {
  color: var(--color-muted);
  line-height: 1.65;
}

.settings-form {
  display: grid;
  gap: 15px;
  margin-top: 22px;
}

.settings-form > label:not(.check-row),
.password-pair label {
  display: grid;
  gap: 7px;
  color: var(--color-muted);
  font-size: 12px;
  font-weight: 650;
}

.settings-form label small {
  font-weight: 450;
}

input[type='password'],
input[type='file'] {
  width: 100%;
  min-height: 43px;
  box-sizing: border-box;
  border: 1px solid var(--color-line);
  border-radius: 6px;
  color: var(--color-ink);
  background: var(--color-surface);
  font: inherit;
}

input[type='password'] {
  padding: 0 11px;
}

input[type='file'] {
  padding: 7px;
}

.password-pair {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.check-row {
  align-items: flex-start;
  gap: 10px;
  color: var(--color-ink);
  line-height: 1.4;
}

.check-row input {
  flex: 0 0 auto;
  width: 17px;
  height: 17px;
  margin: 2px 0 0;
  accent-color: var(--color-forest);
}

.check-row strong,
.check-row small {
  display: block;
}

.check-row small {
  margin-top: 4px;
  color: var(--color-muted);
}

button,
a {
  width: fit-content;
  justify-content: center;
  gap: 7px;
  min-height: 41px;
  padding: 0 15px;
  border: 1px solid var(--color-line);
  border-radius: 999px;
  font: inherit;
  font-weight: 680;
}

button {
  color: var(--color-ink);
  background: transparent;
  cursor: pointer;
}

a {
  color: var(--color-forest);
  text-decoration: none;
}

button:hover:not(:disabled),
a:hover {
  border-color: var(--color-forest);
}

button:disabled {
  cursor: wait;
  opacity: 0.52;
}

.primary-action {
  color: var(--color-on-accent);
  border-color: var(--color-forest);
  background: var(--color-forest);
}

.secondary-action {
  border-color: var(--color-forest);
  color: var(--color-forest);
}

.danger-action {
  color: var(--color-on-accent);
  border-color: var(--color-wine);
  background: var(--color-wine);
}

.text-action {
  min-height: auto;
  padding: 0;
  border: 0;
  color: var(--color-wine);
  font-size: 12px;
}

.job-progress {
  display: grid;
  gap: 8px;
  margin-top: 18px;
  padding-top: 15px;
  border-top: 1px solid var(--color-line);
}

.job-progress div {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
}

.job-progress progress {
  width: 100%;
  height: 7px;
  accent-color: var(--color-forest);
}

.message,
.completion {
  gap: 8px;
  margin: 17px 0 0;
  padding: 11px 12px;
  border-left: 3px solid currentColor;
  line-height: 1.5;
}

.message.error {
  color: var(--color-wine);
  background: var(--color-wine-soft);
}

.message.success,
.completion {
  color: var(--color-forest);
  background: var(--color-forest-soft);
}

.completion {
  align-items: flex-start;
}

.completion p {
  margin: 0 0 9px;
}

.completion a {
  min-height: 34px;
  background: var(--color-surface);
}

.archive-note {
  margin-top: 19px;
  padding-top: 13px;
  border-top: 1px solid var(--color-line);
  font-size: 12px;
}

.archive-note span {
  color: var(--color-forest);
}

.restore-preview {
  margin-top: 23px;
  padding: 19px;
  border: 1px solid var(--color-forest);
  border-radius: 7px;
  background: var(--color-forest-soft);
}

.preview-heading {
  gap: 10px;
  outline-offset: 5px;
}

.preview-heading h4 {
  font-size: 20px;
}

.restore-preview dl {
  margin: 16px 0;
  border-block: 1px solid color-mix(in srgb, var(--color-forest) 18%, transparent);
}

.restore-preview dl div {
  display: grid;
  grid-template-columns: 67px minmax(0, 1fr);
  gap: 12px;
  padding: 9px 0;
}

.restore-preview dl div + div {
  border-top: 1px solid color-mix(in srgb, var(--color-forest) 12%, transparent);
}

.restore-preview dt {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.restore-preview dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  line-height: 1.5;
}

.replacement-warning {
  color: var(--color-wine);
  font-size: 12px;
  line-height: 1.55;
}

.acknowledgement {
  margin: 16px 0;
}

.quiet {
  color: var(--color-muted);
}

@media (max-width: 900px) {
  .policy-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .policy-strip div:nth-child(3) {
    padding-left: 0;
    border-left: 0;
    border-top: 1px solid var(--color-line);
  }

  .policy-strip div:nth-child(4) {
    border-top: 1px solid var(--color-line);
  }

  .backup-columns {
    grid-template-columns: 1fr;
  }

  .backup-column,
  .backup-column + .backup-column {
    padding: 25px 0;
    border-left: 0;
  }

  .backup-column + .backup-column {
    border-top: 1px solid var(--color-line);
  }
}

@media (max-width: 560px) {
  .policy-strip,
  .password-pair {
    grid-template-columns: 1fr;
  }

  .policy-strip div,
  .policy-strip div + div,
  .policy-strip div:nth-child(3) {
    padding: 11px 0;
    border-left: 0;
    border-top: 1px solid var(--color-line);
  }

  .policy-strip div:first-child {
    border-top: 0;
  }

  .restore-preview {
    padding: 15px;
  }

  .restore-preview dl div {
    grid-template-columns: 1fr;
    gap: 3px;
  }

  button,
  a {
    max-width: 100%;
    white-space: normal;
  }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}

@media (forced-colors: active) {
  .backup-settings,
  .policy-strip,
  .backup-columns,
  .backup-column,
  .restore-preview,
  input,
  button,
  a {
    border-color: CanvasText;
  }
}
</style>
