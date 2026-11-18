<script setup lang="ts">
import { messageRef, messageTemplate, settingsText as t, serverMessage } from '@/stores/locale'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  PhArrowSquareOut,
  PhBrain,
  PhCheckCircle,
  PhDownloadSimple,
  PhHardDrives,
  PhPause,
  PhPlay,
  PhScan,
  PhShieldCheck,
  PhTrash,
  PhWarning,
  PhX,
} from '@phosphor-icons/vue'
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
import { apiJson } from '@/lib/json-api'
import { formatBytes } from '@/lib/formatters'

type ModelAsset = components['schemas']['ModelAssetResponse']
type AssetJob = components['schemas']['AssetJobResponse']
type ModelJob = components['schemas']['AssetJobDetailResponse']
type DeletionPreview = components['schemas']['DeletionPreviewResponse']

interface AssetGroup {
  bundleId: string
  primary: ModelAsset
  versions: ModelAsset[]
}

interface DeleteReview {
  asset: ModelAsset
  preview: DeletionPreview
}

const assets = ref<ModelAsset[]>([])
const jobsByAsset = ref<Record<string, ModelJob>>({})
const loading = ref(true)
const busyKey = ref('')
const error = messageRef(t)
const notice = messageRef(t)
const pendingInstall = ref<ModelAsset>()
const pendingDelete = ref<DeleteReview>()
let pollTimer: number | undefined
let disposed = false

const groups = computed<AssetGroup[]>(() => {
  const grouped = new Map<string, ModelAsset[]>()
  for (const asset of assets.value) {
    const versions = grouped.get(asset.bundle_id) ?? []
    versions.push(asset)
    grouped.set(asset.bundle_id, versions)
  }
  return [...grouped.entries()].map(([bundleId, versions]) => {
    const ordered = [...versions].sort((left, right) => {
      if (left.current_release !== right.current_release) return left.current_release ? -1 : 1
      if (left.active !== right.active) return left.active ? -1 : 1
      return right.version.localeCompare(left.version)
    })
    return { bundleId, primary: ordered[0], versions: ordered }
  })
})

function safeExternalUrl(value: string): string | undefined {
  try {
    const parsed = new URL(value)
    if (
      parsed.protocol !== 'https:' ||
      parsed.username ||
      parsed.password ||
      (parsed.port && parsed.port !== '443')
    ) return undefined
    parsed.search = ''
    parsed.hash = ''
    return parsed.toString()
  } catch {
    return undefined
  }
}

function operationKey(prefix: string): string {
  const suffix = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${suffix}`
}

function assetJob(asset: ModelAsset): ModelJob | undefined {
  return jobsByAsset.value[asset.asset_id]
}

function jobStatus(job: ModelJob): string {
  const labels: Record<string, string> = {
    queued: t('等待下载'),
    running: t('进行中'),
    paused: t('已暂停'),
    failed: t('下载失败'),
    succeeded: t('下载完成'),
    cancelled: t('已取消'),
  }
  if (job.cancel_requested && !['cancelled', 'failed', 'succeeded'].includes(job.status)) {
    return t('正在取消')
  }
  if (job.pause_requested && ['queued', 'running'].includes(job.status)) return t('正在暂停')
  return labels[job.status] ?? t('状态待确认')
}

function degradationText(asset: ModelAsset, reason: string): string {
  const common: Record<string, string> = {
    runtime_unavailable: t('当前运行包未包含所需运行时；模型文件不会被误判为可用。'),
    model_download_incomplete: t('下载尚未完成；已验证部分可以在稍后继续使用。'),
    model_integrity_failed: t('文件完整性校验失败，系统不会启用这个版本。'),
    model_not_enabled: t('文件已通过校验，启用后才会参与本地处理。'),
  }
  if (reason === 'model_missing') {
    return asset.capability === 'semantic'
      ? t('尚未安装；当前继续使用词法检索，推荐置信度会相应降低。')
      : t('尚未安装；普通 PDF 阅读不受影响，扫描页暂不能识别。')
  }
  return common[reason] ?? t('这项可选能力当前处于安全降级状态。')
}

function jobFailureText(code: string | null): string {
  const labels: Record<string, string> = {
    network: t('网络连接中断；已验证的部分仍会保留，可稍后继续。'),
    disk_full: t('可用空间不足；清理空间后可以从已验证位置继续。'),
    hash_mismatch: t('下载内容未通过 SHA-256 校验，文件没有启用；请重新安装。'),
    size_mismatch: t('来源返回的文件大小与内置清单不一致，文件没有启用。'),
    invalid_range: t('来源不支持当前安全续传检查点；重试时会重新验证下载范围。'),
    etag_changed: t('来源文件在暂停期间发生变化；重试时会从安全边界重新开始。'),
    encoded_response: t('来源返回了不受支持的压缩内容，文件没有启用。'),
    runtime_unavailable: t('当前运行包缺少所需运行时；更新应用后再启用。'),
    storage: t('本地模型存储暂时不可用；请检查可用空间后重试。'),
    unsafe_path: t('模型存储边界未通过安全检查，未写入任何可用版本。'),
    model_files_changed: t('已安装文件发生变化，系统已停用这个版本。'),
  }
  return labels[code ?? ''] ?? t('模型任务未完成；系统没有启用不完整文件。')
}

function canRetry(job: ModelJob): boolean {
  return job.status === 'failed' && job.retryable === true
}

function canStartFresh(asset: ModelAsset): boolean {
  const job = assetJob(asset)
  return job === undefined || ['cancelled', 'succeeded'].includes(job.status)
}

function schedulePoll(): void {
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
  pollTimer = undefined
  if (disposed) return
  const active = Object.values(jobsByAsset.value).some((job) =>
    ['queued', 'running'].includes(job.status),
  )
  if (active) {
    pollTimer = window.setTimeout(() => void load(false), 1_500)
  }
}

async function load(showLoading = true): Promise<void> {
  if (showLoading) loading.value = true
  try {
    const [assetItems, summaries] = await Promise.all([
      apiJson<ModelAsset[]>('/api/v1/settings/model-assets'),
      apiJson<ModelJob[]>('/api/v1/settings/model-assets/jobs'),
    ])
    const nextJobs: Record<string, ModelJob> = {}
    for (const detail of summaries) {
      if (!nextJobs[detail.asset_id]) nextJobs[detail.asset_id] = detail
    }
    assets.value = assetItems
    jobsByAsset.value = nextJobs
    error.value = ''
  } catch {
    if (showLoading || assets.value.length === 0) {
      error.value = '暂时无法读取本机模型状态；不会自动下载或启用任何内容。'
    }
  } finally {
    if (showLoading) loading.value = false
    schedulePoll()
  }
}

function mergeJob(asset: ModelAsset, job: AssetJob): void {
  const previous = jobsByAsset.value[asset.asset_id]
  jobsByAsset.value = {
    ...jobsByAsset.value,
    [asset.asset_id]: {
      id: job.id,
      kind: job.kind,
      status: job.status,
      progress: job.progress,
      cancel_requested: job.cancel_requested,
      pause_requested: job.pause_requested,
      asset_id: asset.asset_id,
      retryable: previous?.retryable ?? null,
      error_code: previous?.error_code ?? null,
      created_at: previous?.created_at ?? new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
  }
}

async function confirmInstall(): Promise<void> {
  const asset = pendingInstall.value
  if (!asset) return
  busyKey.value = `install:${asset.asset_id}`
  error.value = ''
  notice.value = ''
  try {
    const job = await apiJson<AssetJob>(
      `/api/v1/settings/model-assets/${encodeURIComponent(asset.bundle_id)}/install`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': operationKey('model-install') },
      },
    )
    mergeJob(asset, job)
    pendingInstall.value = undefined
    notice.value = messageTemplate('{0} 已加入本地下载任务。', asset.display_name)
    await load(false)
  } catch {
    error.value = '无法创建模型下载任务；没有文件被启用。'
  } finally {
    busyKey.value = ''
    schedulePoll()
  }
}

async function command(asset: ModelAsset, job: ModelJob, action: 'pause' | 'resume' | 'cancel') {
  busyKey.value = `${action}:${job.id}`
  error.value = ''
  notice.value = ''
  try {
    const updated = await apiJson<AssetJob>(
      `/api/v1/settings/model-assets/jobs/${encodeURIComponent(job.id)}/${action}`,
      { method: 'POST' },
    )
    mergeJob(asset, updated)
    await load(false)
  } catch {
    const labels = {
      pause: t('暂时无法暂停；任务状态没有被本页改写。'),
      resume: t('暂时无法继续；已验证的部分仍会保留。'),
      cancel: t('暂时无法确认取消；请刷新后查看持久任务状态。'),
    }
    error.value = labels[action]
  } finally {
    busyKey.value = ''
    schedulePoll()
  }
}

async function activate(asset: ModelAsset): Promise<void> {
  busyKey.value = `activate:${asset.asset_id}`
  error.value = ''
  notice.value = ''
  try {
    await apiJson<ModelAsset>(
      `/api/v1/settings/model-assets/${encodeURIComponent(asset.bundle_id)}/revisions/${encodeURIComponent(asset.revision)}/activate`,
      { method: 'POST' },
    )
    notice.value = messageTemplate('{0} {1} 已启用。', asset.display_name, asset.version)
    await load(false)
  } catch {
    error.value = asset.runtime_available
      ? '模型运行时检查未通过，原有活动版本保持不变。'
      : '当前运行包缺少所需运行时，模型文件保持已校验但未启用。'
  } finally {
    busyKey.value = ''
  }
}

async function previewDelete(asset: ModelAsset): Promise<void> {
  busyKey.value = `preview:${asset.asset_id}`
  error.value = ''
  notice.value = ''
  try {
    const preview = await apiJson<DeletionPreview>(
      `/api/v1/settings/model-assets/${encodeURIComponent(asset.bundle_id)}/revisions/${encodeURIComponent(asset.revision)}/deletion-preview`,
    )
    pendingDelete.value = { asset, preview }
  } catch {
    error.value = '无法生成模型清理预览；没有文件被删除。'
  } finally {
    busyKey.value = ''
  }
}

function blockingText(preview: DeletionPreview): string {
  if (preview.can_delete) return t('没有活动任务或索引引用这个版本。')
  if (preview.blocking_references.some((item) => item === 'active')) {
    return t('这是当前活动版本；请先启用另一个已验证版本。')
  }
  return t('仍有 {0} 项本地任务或索引正在使用，当前不能删除。', preview.blocking_references.length)
}

async function confirmDelete(): Promise<void> {
  const review = pendingDelete.value
  if (!review?.preview.can_delete) return
  busyKey.value = `delete:${review.asset.asset_id}`
  error.value = ''
  notice.value = ''
  try {
    await apiJson<DeletionPreview>(
      `/api/v1/settings/model-assets/${encodeURIComponent(review.asset.bundle_id)}/revisions/${encodeURIComponent(review.asset.revision)}`,
      { method: 'DELETE' },
    )
    pendingDelete.value = undefined
    notice.value = messageTemplate('{0} {1} 旧版本已删除。', review.asset.display_name, review.asset.version)
    await load(false)
  } catch {
    error.value = '模型引用已经变化，未执行删除；请重新预览。'
    pendingDelete.value = undefined
  } finally {
    busyKey.value = ''
  }
}

onMounted(() => {
  disposed = false
  void load()
})
onUnmounted(() => {
  disposed = true
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
})
</script>

<template>
  <section class="model-settings" aria-labelledby="model-settings-title">
    <div class="section-heading">
      <div>
        <p>{{ t('按需、本地、可撤销') }}</p>
        <h2 id="model-settings-title">{{ t('本地 AI 模型') }}</h2>
      </div>
      <PhHardDrives :size="25" aria-hidden="true" />
    </div>
    <p class="section-intro">
      {{ t('初始安装不包含模型权重。这里先展示精确大小、许可和降级影响；只有确认后才下载，只有完整校验并显式启用后才参与论文处理。') }}
    </p>

    <p v-if="error" class="model-message error" role="alert">{{ error }}</p>
    <p v-if="notice" class="model-message success" role="status">{{ notice }}</p>
    <p v-if="loading" class="quiet-state">{{ t('正在读取本机模型清单…') }}</p>
    <p v-else-if="groups.length === 0 && !error" class="quiet-state">
      {{ t('当前运行包没有声明可安装的模型资源。') }}
    </p>

    <div v-else class="model-grid">
      <article v-for="group in groups" :key="group.bundleId" class="model-card">
        <header>
          <div class="capability-icon" aria-hidden="true">
            <PhBrain v-if="group.primary.capability === 'semantic'" :size="22" />
            <PhScan v-else :size="22" />
          </div>
          <div>
            <span class="eyebrow">
              {{ group.primary.capability === 'semantic' ? t('语义推荐') : t('扫描页识别') }}
            </span>
            <h3>{{ serverMessage(group.primary.display_name) }}</h3>
          </div>
          <span v-if="group.versions.some((item) => item.available)" class="ready-badge">
            <PhCheckCircle :size="15" aria-hidden="true" />
            {{ t('已启用并可使用') }}
          </span>
        </header>

        <p class="purpose">{{ serverMessage(group.primary.purpose) }}</p>
        <dl class="model-facts">
          <div>
            <dt>{{ t('当前发布') }}</dt>
            <dd>{{ group.primary.version }}</dd>
          </div>
          <div>
            <dt>{{ t('精确下载') }}</dt>
            <dd>{{ formatBytes(group.primary.download_size_bytes) }}</dd>
          </div>
          <div>
            <dt>{{ t('运行时') }}</dt>
            <dd>{{ group.primary.runtime_available ? t('当前运行包可用') : t('当前运行包未包含') }}</dd>
          </div>
        </dl>
        <div class="external-facts">
          <a
            v-if="safeExternalUrl(group.primary.license_url)"
            :data-test="`license-${group.primary.asset_id}`"
            :href="safeExternalUrl(group.primary.license_url)"
            target="_blank"
            rel="noreferrer"
          >
            {{ group.primary.license_name }} {{ t('许可') }}
            <PhArrowSquareOut :size="13" aria-hidden="true" />
          </a>
          <a
            v-if="safeExternalUrl(group.primary.source_url)"
            :data-test="`source-${group.primary.asset_id}`"
            :href="safeExternalUrl(group.primary.source_url)"
            target="_blank"
            rel="noreferrer"
          >
            {{ t('模型来源') }}
            <PhArrowSquareOut :size="13" aria-hidden="true" />
          </a>
        </div>
        <p class="copyright">{{ group.primary.copyright_notice }}</p>

        <ul v-if="group.primary.degradation_reasons.length" class="degradation-list">
          <li v-for="reason in group.primary.degradation_reasons" :key="reason">
            <PhWarning :size="15" aria-hidden="true" />
            {{ degradationText(group.primary, reason) }}
          </li>
        </ul>

        <div class="version-list">
          <article
            v-for="asset in group.versions"
            :key="asset.asset_id"
            class="version-row"
            :data-test="`asset-${asset.asset_id}`"
          >
            <div class="version-heading">
              <div>
                <strong>{{ asset.version }}</strong>
                <span>{{ asset.current_release ? t('随应用提供的版本') : t('保留的旧版本') }}</span>
              </div>
              <span v-if="asset.active" class="active-label">{{ t('当前启用') }}</span>
              <span v-else-if="asset.integrity === 'verified'" class="verified-label">{{ t('已校验') }}</span>
              <span v-else class="missing-label">{{ t('未就绪') }}</span>
            </div>

            <div v-if="assetJob(asset)" class="job-state" :data-test="`job-${asset.asset_id}`">
              <div>
                <span>{{ jobStatus(assetJob(asset)!) }} · {{ assetJob(asset)!.progress }}%</span>
                <progress :value="assetJob(asset)!.progress" max="100">
                  {{ assetJob(asset)!.progress }}%
                </progress>
              </div>
              <p v-if="assetJob(asset)!.status === 'failed'" class="failure-guidance">
                {{ jobFailureText(assetJob(asset)!.error_code) }}
              </p>
            </div>

            <div class="version-actions">
              <button
                v-if="asset.current_release && asset.integrity !== 'verified' && canStartFresh(asset)"
                :data-test="`install-${asset.asset_id}`"
                type="button"
                class="primary-action"
                @click="pendingInstall = asset"
              >
                <PhDownloadSimple :size="16" aria-hidden="true" />
                {{ t('下载前确认') }}
              </button>
              <button
                v-if="assetJob(asset) && ['queued', 'running'].includes(assetJob(asset)!.status) && !assetJob(asset)!.pause_requested && !assetJob(asset)!.cancel_requested"
                :data-test="`pause-${assetJob(asset)!.id}`"
                type="button"
                @click="command(asset, assetJob(asset)!, 'pause')"
              >
                <PhPause :size="15" aria-hidden="true" />
                {{ t('暂停') }}
              </button>
              <button
                v-if="assetJob(asset)?.status === 'paused' && !assetJob(asset)!.cancel_requested"
                :data-test="`resume-${assetJob(asset)!.id}`"
                type="button"
                @click="command(asset, assetJob(asset)!, 'resume')"
              >
                <PhPlay :size="15" aria-hidden="true" />
                {{ t('继续') }}
              </button>
              <button
                v-if="assetJob(asset) && ['queued', 'running', 'paused'].includes(assetJob(asset)!.status) && !assetJob(asset)!.cancel_requested"
                :data-test="`cancel-${assetJob(asset)!.id}`"
                type="button"
                class="quiet-danger"
                @click="command(asset, assetJob(asset)!, 'cancel')"
              >
                <PhX :size="15" aria-hidden="true" />
                {{ t('取消') }}
              </button>
              <button
                v-if="assetJob(asset) && canRetry(assetJob(asset)!)"
                :data-test="`retry-${assetJob(asset)!.id}`"
                type="button"
                @click="command(asset, assetJob(asset)!, 'resume')"
              >
                <PhPlay :size="15" aria-hidden="true" />
                {{ t('继续下载') }}
              </button>
              <button
                v-if="asset.current_release && assetJob(asset)?.status === 'failed' && !canRetry(assetJob(asset)!)"
                :data-test="`reinstall-${assetJob(asset)!.id}`"
                type="button"
                @click="pendingInstall = asset"
              >
                <PhDownloadSimple :size="15" aria-hidden="true" />
                {{ t('重新安装') }}
              </button>
              <button
                v-if="asset.integrity === 'verified' && !asset.active && asset.runtime_available"
                :data-test="`activate-${asset.asset_id}`"
                type="button"
                class="primary-action"
                :disabled="Boolean(busyKey)"
                @click="activate(asset)"
              >
                <PhShieldCheck :size="15" aria-hidden="true" />
                {{ t('启用此版本') }}
              </button>
              <button
                v-if="asset.integrity === 'verified' && !asset.active"
                :data-test="`preview-delete-${asset.asset_id}`"
                type="button"
                class="quiet-danger"
                :disabled="Boolean(busyKey)"
                @click="previewDelete(asset)"
              >
                <PhTrash :size="15" aria-hidden="true" />
                {{ t('预览清理') }}
              </button>
            </div>
          </article>
        </div>
      </article>
    </div>

    <AlertDialogRoot
      :open="Boolean(pendingInstall)"
      @update:open="(value) => { if (!value && !busyKey) pendingInstall = undefined }"
    >
      <AlertDialogPortal>
        <AlertDialogOverlay class="dialog-overlay" />
        <AlertDialogContent class="model-dialog" data-test="model-install-dialog">
          <div class="dialog-mark" aria-hidden="true"><PhDownloadSimple :size="20" /></div>
          <AlertDialogTitle>{{ t('下载并校验这个模型？') }}</AlertDialogTitle>
          <AlertDialogDescription>
            {{ t('只下载下面这个固定版本。下载完成后仍需通过完整性与运行时检查，系统不会自动启用失败或不完整文件。') }}
          </AlertDialogDescription>
          <dl v-if="pendingInstall" class="dialog-review">
            <div><dt>{{ t('模型') }}</dt><dd>{{ serverMessage(pendingInstall.display_name) }} {{ pendingInstall.version }}</dd></div>
            <div><dt>{{ t('下载') }}</dt><dd>{{ formatBytes(pendingInstall.download_size_bytes) }}</dd></div>
            <div><dt>{{ t('许可') }}</dt><dd>{{ pendingInstall.license_name }}</dd></div>
            <div><dt>{{ t('用途') }}</dt><dd>{{ serverMessage(pendingInstall.purpose) }}</dd></div>
          </dl>
          <div class="dialog-actions">
            <AlertDialogCancel as-child>
              <button type="button" :disabled="Boolean(busyKey)">{{ t('暂不下载') }}</button>
            </AlertDialogCancel>
            <button
              data-test="confirm-model-install"
              type="button"
              class="confirm"
              :disabled="Boolean(busyKey)"
              @click="confirmInstall"
            >
              {{ busyKey ? t('正在创建任务…') : t('确认下载') }}
            </button>
          </div>
        </AlertDialogContent>
      </AlertDialogPortal>
    </AlertDialogRoot>

    <AlertDialogRoot
      :open="Boolean(pendingDelete)"
      @update:open="(value) => { if (!value && !busyKey) pendingDelete = undefined }"
    >
      <AlertDialogPortal>
        <AlertDialogOverlay class="dialog-overlay" />
        <AlertDialogContent class="model-dialog" data-test="model-delete-dialog">
          <div class="dialog-mark danger" aria-hidden="true"><PhTrash :size="20" /></div>
          <AlertDialogTitle>{{ t('清理这个模型版本？') }}</AlertDialogTitle>
          <AlertDialogDescription>
            {{ t('只删除这一固定 revision 的模型文件与记录，不删除论文、报告、标注或偏好。') }}
          </AlertDialogDescription>
          <dl v-if="pendingDelete" class="dialog-review">
            <div><dt>{{ t('版本') }}</dt><dd>{{ serverMessage(pendingDelete.asset.display_name) }} {{ pendingDelete.asset.version }}</dd></div>
            <div><dt>{{ t('可释放') }}</dt><dd>{{ formatBytes(pendingDelete.preview.reclaimable_bytes) }}</dd></div>
            <div><dt>{{ t('引用检查') }}</dt><dd>{{ blockingText(pendingDelete.preview) }}</dd></div>
          </dl>
          <div class="dialog-actions">
            <AlertDialogCancel as-child>
              <button type="button" :disabled="Boolean(busyKey)">{{ t('保留版本') }}</button>
            </AlertDialogCancel>
            <button
              v-if="pendingDelete?.preview.can_delete"
              data-test="confirm-model-delete"
              type="button"
              class="confirm danger"
              :disabled="Boolean(busyKey)"
              @click="confirmDelete"
            >
              {{ busyKey ? t('正在删除…') : t('确认删除旧版本') }}
            </button>
          </div>
        </AlertDialogContent>
      </AlertDialogPortal>
    </AlertDialogRoot>
  </section>
</template>

<style scoped>
.model-settings {
  margin-top: clamp(42px, 6vw, 76px);
}

.section-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding-bottom: 17px;
  border-bottom: 1px solid var(--color-line);
}

.section-heading p,
.eyebrow {
  margin: 0 0 7px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 720;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.section-heading h2 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: clamp(28px, 4vw, 39px);
  font-weight: 500;
}

.section-intro {
  max-width: 760px;
  margin: 18px 0 0;
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.75;
}

.model-message,
.quiet-state {
  margin: 18px 0 0;
  padding: 12px 14px;
  border-left: 3px solid var(--color-forest);
  background: var(--color-forest-hover-soft);
  color: var(--color-muted-strong);
  font-size: 12px;
  line-height: 1.6;
}

.model-message.error {
  border-color: var(--color-danger);
}

.model-message.success {
  color: var(--color-forest);
}

.model-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  margin-top: 24px;
}

.model-card {
  min-width: 0;
  padding: clamp(20px, 3vw, 28px);
  border: 1px solid var(--color-line);
  border-top: 3px solid var(--color-forest);
  border-radius: var(--radius-sm);
  background: var(--color-paper);
}

.model-card > header {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: start;
  gap: 12px;
}

.capability-icon {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border: 1px solid var(--color-line);
  color: var(--color-forest);
}

.model-card h3 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: 22px;
  font-weight: 520;
  line-height: 1.2;
}

.ready-badge,
.active-label,
.verified-label,
.missing-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  white-space: nowrap;
}

.missing-label {
  color: var(--color-muted);
}

.purpose {
  min-height: 44px;
  margin: 20px 0;
  color: var(--color-muted-strong);
  font-size: 13px;
  line-height: 1.65;
}

.model-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  margin: 0;
  background: var(--color-line);
}

.model-facts div {
  min-width: 0;
  padding: 11px;
  background: var(--color-paper);
}

.model-facts dt,
.dialog-review dt {
  color: var(--color-muted);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.08em;
}

.model-facts dd,
.dialog-review dd {
  margin: 5px 0 0;
  overflow-wrap: anywhere;
  color: var(--color-ink);
  font-size: var(--type-meta);
  line-height: 1.45;
}

.external-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  margin-top: 16px;
}

.external-facts a {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 650;
  text-underline-offset: 3px;
}

.copyright {
  margin: 10px 0 0;
  color: var(--color-muted);
  font-size: var(--type-meta);
  line-height: 1.5;
}

.degradation-list {
  display: grid;
  gap: 7px;
  margin: 18px 0 0;
  padding: 0;
  list-style: none;
}

.degradation-list li {
  display: grid;
  grid-template-columns: 17px 1fr;
  gap: 7px;
  color: var(--color-wine);
  font-size: var(--type-meta);
  line-height: 1.55;
}

.version-list {
  margin-top: 22px;
  border-top: 1px solid var(--color-line);
}

.version-row {
  padding: 16px 0 2px;
  border-bottom: 1px solid var(--color-line);
}

.version-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.version-heading > div {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 10px;
}

.version-heading strong {
  font-size: 12px;
}

.version-heading div span {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.job-state {
  margin-top: 12px;
}

.job-state > div {
  display: grid;
  grid-template-columns: auto minmax(90px, 1fr);
  align-items: center;
  gap: 12px;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
}

.job-state progress {
  width: 100%;
  height: 4px;
  border: 0;
  background: var(--color-line);
}

.job-state progress::-webkit-progress-bar { background: var(--color-line); }
.job-state progress::-webkit-progress-value,
.job-state progress::-moz-progress-bar { background: var(--color-forest); }

.failure-guidance {
  margin: 9px 0 0;
  color: var(--color-wine);
  font-size: var(--type-meta);
  line-height: 1.55;
}

.version-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 13px 0;
}

.version-actions button,
.dialog-actions button {
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 7px 11px;
  border: 1px solid var(--color-line-strong);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  font-weight: 650;
  cursor: pointer;
}

.version-actions .primary-action,
.dialog-actions .confirm {
  border-color: var(--color-forest);
  background: var(--color-forest);
  color: var(--color-on-accent);
}

.version-actions .quiet-danger,
.dialog-actions .danger {
  color: var(--color-danger);
}

.version-actions button:disabled,
.dialog-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.version-actions button:focus-visible,
.dialog-actions button:focus-visible,
.external-facts a:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

.dialog-overlay {
  position: fixed;
  z-index: 30;
  inset: 0;
  background: rgb(10 22 15 / 38%);
}

.model-dialog {
  position: fixed;
  z-index: 31;
  top: 50%;
  left: 50%;
  width: min(520px, calc(100vw - 32px));
  max-height: calc(100vh - 32px);
  padding: clamp(22px, 4vw, 32px);
  overflow-y: auto;
  transform: translate(-50%, -50%);
  border: 1px solid var(--color-line-strong);
  border-top: 4px solid var(--color-forest);
  background: var(--color-paper);
  color: var(--color-ink);
  box-shadow: 0 24px 80px rgb(10 22 15 / 22%);
}

.dialog-mark {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  margin-bottom: 18px;
  background: var(--color-forest-hover-soft);
  color: var(--color-forest);
}

.dialog-mark.danger {
  color: var(--color-danger);
}

.model-dialog h2 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: 27px;
  font-weight: 520;
}

.model-dialog > p {
  color: var(--color-muted);
  font-size: 12px;
  line-height: 1.65;
}

.dialog-review {
  display: grid;
  gap: 11px;
  margin: 20px 0 0;
  padding: 16px;
  border: 1px solid var(--color-line);
}

.dialog-review div {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 12px;
}

.dialog-review dd { margin-top: 0; }

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 24px;
}

@media (max-width: 860px) {
  .model-grid { grid-template-columns: 1fr; }
}

@media (max-width: 540px) {
  .model-card > header { grid-template-columns: auto 1fr; }
  .ready-badge { grid-column: 1 / -1; justify-self: start; }
  .model-facts { grid-template-columns: 1fr; }
  .job-state > div { grid-template-columns: 1fr; }
  .dialog-actions { flex-direction: column-reverse; }
  .dialog-actions button { width: 100%; }
}

@media (forced-colors: active) {
  .model-card,
  .model-dialog,
  .dialog-review,
  .version-actions button,
  .dialog-actions button {
    border-color: CanvasText;
  }
}
</style>
