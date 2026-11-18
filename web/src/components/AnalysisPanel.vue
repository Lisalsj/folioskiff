<script setup lang="ts">
import { currentLocale, messageRef, researchText as t, serverMessage } from '@/stores/locale'
import { computed, onMounted, ref, watch } from 'vue'
import {
  PhArrowClockwise,
  PhArrowSquareOut,
  PhCheckCircle,
  PhCloud,
  PhDownloadSimple,
  PhFileText,
  PhPrinter,
  PhSparkle,
  PhStopCircle,
  PhWarningCircle,
} from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import { ApiResponseError, apiJson } from '@/lib/json-api'
import { useJobStore, type JobSummary } from '@/stores/jobs'

import AnalysisPreflightDialog from './AnalysisPreflightDialog.vue'

type Capability = components['schemas']['AgentCapabilityResponse']
type Preflight = components['schemas']['AnalysisPreflight']
type Selection = components['schemas']['AnalysisSelection']
type AnalysisJob = components['schemas']['AnalysisJobResponse']
type ReportSummary = components['schemas']['AnalysisReportSummaryResponse']
type Report = components['schemas']['AnalysisReportResponse']
type Failure = components['schemas']['AnalysisFailureResponse']
type Fragment = components['schemas']['AnalysisFragmentResponse']
type Statement = components['schemas']['ReportStatement']
type Reference = components['schemas']['EvidenceReference']

interface ReportSection {
  key: string
  title: string
  statements: Statement[]
  emphasis?: 'author' | 'agent'
}

const props = withDefaults(defineProps<{
  workId: string
  documentVersionId?: string | null
  documentContentSha256?: string | null
  autoOpenPreflight?: boolean
}>(), {
  documentVersionId: null,
  documentContentSha256: null,
  autoOpenPreflight: false,
})

const emit = defineEmits<{
  navigatePage: [pageNumber: number]
}>()

const jobStore = useJobStore()
const reports = ref<ReportSummary[]>([])
const selectedReport = ref<Report | null>(null)
const selectedReportId = ref('')
const reportsLoading = ref(true)
const reportLoading = ref(false)
const loadError = messageRef(t)
const statusMessage = messageRef(t)
const activeJobId = ref('')
const fragments = ref<Fragment[]>([])
const failure = ref<Failure | null>(null)
const preflightOpen = ref(false)
const preflightLoading = ref(false)
const preflightSubmitting = ref(false)
const preflightError = messageRef(t)
const preflight = ref<Preflight | null>(null)
const selection = ref<Selection>(initialSelection())
const capabilities = ref<Capability[]>([])

let loadGeneration = 0
let terminalJob = ''

const activeJob = computed(() => activeJobId.value
  ? jobStore.find(activeJobId.value)
  : undefined)
const active = computed(() => Boolean(
  activeJob.value && ['queued', 'running'].includes(activeJob.value.status),
))
const currentVersionReport = computed(() => selectedReport.value?.document_content_sha256
  === props.documentContentSha256)
const reportSections = computed<ReportSection[]>(() => {
  const content = selectedReport.value?.content
  if (!content) return []
  return [
    { key: 'overview', title: t('概览'), statements: [content.overview] },
    { key: 'research-question', title: t('研究问题'), statements: [content.research_question] },
    { key: 'author-claims', title: t('作者主张'), statements: content.author_claims, emphasis: 'author' },
    { key: 'contributions', title: t('核心贡献'), statements: content.contributions },
    { key: 'method', title: t('方法'), statements: content.method },
    { key: 'assumptions', title: t('关键假设'), statements: content.assumptions },
    { key: 'experiments', title: t('数据与实验'), statements: content.data_and_experiments },
    { key: 'findings', title: t('主要发现'), statements: content.findings },
    { key: 'limitations', title: t('局限与风险'), statements: content.limitations },
    { key: 'reproducibility', title: t('可复现性'), statements: content.reproducibility },
    {
      key: 'intent',
      title: t('与研究意图的关系'),
      statements: content.relation_to_user_intent,
    },
    {
      key: 'agent-judgments',
      title: t('Agent 判断'),
      statements: content.agent_judgments,
      emphasis: 'agent',
    },
  ]
})

function initialSelection(): Selection {
  return {
    document_version_id: props.documentVersionId,
    include_visual_evidence: true,
  }
}

function storageKey(): string {
  return `paper-reader.analysis-job.${props.workId}.${props.documentVersionId ?? 'abstract'}`
}

function readStoredJob(): string {
  try {
    return window.localStorage.getItem(storageKey()) ?? ''
  } catch {
    return ''
  }
}

function storeJob(jobId: string): void {
  try {
    if (jobId) window.localStorage.setItem(storageKey(), jobId)
    else window.localStorage.removeItem(storageKey())
  } catch {
    // A private browser mode can deny storage; live Job updates still work.
  }
}

function jobSummary(job: AnalysisJob): JobSummary {
  return {
    id: job.id,
    kind: job.kind,
    status: job.status,
    progress: job.progress,
    cancel_requested: job.cancel_requested,
    pause_requested: false,
  }
}

function jobMatches(job: AnalysisJob): boolean {
  return job.kind === 'analysis.report'
    && job.payload.work_id === props.workId
    && job.payload.document_version_id === props.documentVersionId
}

async function initialize(): Promise<void> {
  const generation = ++loadGeneration
  reportsLoading.value = true
  loadError.value = ''
  await Promise.allSettled([
    loadReports(undefined, generation),
    recoverActiveJob(generation),
  ])
  if (generation === loadGeneration) reportsLoading.value = false
}

async function recoverActiveJob(generation: number): Promise<void> {
  const stored = readStoredJob()
  if (stored) {
    try {
      const job = await apiJson<AnalysisJob>(`/api/v1/jobs/${encodeURIComponent(stored)}`)
      if (generation !== loadGeneration) return
      if (jobMatches(job) && !['succeeded', 'failed', 'cancelled'].includes(job.status)) {
        jobStore.upsert(jobSummary(job))
        activeJobId.value = job.id
        return
      }
      if (jobMatches(job) && ['failed'].includes(job.status)) {
        jobStore.upsert(jobSummary(job))
        activeJobId.value = job.id
        return
      }
      if (jobMatches(job) && job.status === 'succeeded') {
        jobStore.upsert(jobSummary(job))
        activeJobId.value = job.id
        return
      }
      storeJob('')
    } catch {
      storeJob('')
    }
  }

  await jobStore.refresh()
  if (generation !== loadGeneration) return
  for (const summary of jobStore.jobs) {
    if (summary.kind !== 'analysis.report' || !['queued', 'running'].includes(summary.status)) {
      continue
    }
    try {
      const job = await apiJson<AnalysisJob>(
        `/api/v1/jobs/${encodeURIComponent(summary.id)}`,
      )
      if (generation !== loadGeneration) return
      if (jobMatches(job)) {
        activeJobId.value = job.id
        storeJob(job.id)
        return
      }
    } catch {
      // Another active analysis Job can belong to a different paper.
    }
  }
}

async function loadReports(
  preferredJobId?: string,
  generation = loadGeneration,
): Promise<void> {
  try {
    const loaded = await apiJson<ReportSummary[]>(
      `/api/v1/analysis/works/${encodeURIComponent(props.workId)}/reports`,
    )
    if (generation !== loadGeneration) return
    reports.value = loaded
    const preferred = preferredJobId
      ? loaded.find((item) => item.job_id === preferredJobId)
      : undefined
    const retained = loaded.find((item) => item.id === selectedReportId.value)
    const currentDocument = loaded.find(
      (item) => item.document_content_sha256 === props.documentContentSha256,
    )
    const next = preferred ?? retained ?? currentDocument ?? loaded[0]
    if (next) await loadReport(next.id, generation)
    else {
      selectedReport.value = null
      selectedReportId.value = ''
    }
  } catch {
    if (generation === loadGeneration) {
      loadError.value = '暂时无法读取分析报告。论文与 PDF 仍可继续阅读。'
    }
  }
}

async function loadReport(reportId: string, generation = loadGeneration): Promise<void> {
  if (!reportId) return
  reportLoading.value = true
  loadError.value = ''
  try {
    const loaded = await apiJson<Report>(
      `/api/v1/analysis/reports/${encodeURIComponent(reportId)}`,
    )
    if (generation !== loadGeneration) return
    selectedReport.value = loaded
    selectedReportId.value = loaded.id
  } catch {
    if (generation === loadGeneration) {
      loadError.value = '所选报告暂时无法读取；其他历史版本没有改变。'
    }
  } finally {
    if (generation === loadGeneration) reportLoading.value = false
  }
}

async function openPreflight(): Promise<void> {
  preflightOpen.value = true
  preflightError.value = ''
  selection.value = initialSelection()
  preflight.value = null
  preflightLoading.value = true
  const requested = { ...selection.value }
  const [capabilityResult, preflightResult] = await Promise.allSettled([
    apiJson<Capability[]>('/api/v1/settings/agents'),
    requestPreflight(requested),
  ])
  if (capabilityResult.status === 'fulfilled') {
    capabilities.value = capabilityResult.value
  }
  if (preflightResult.status === 'rejected' && !preflightError.value) {
    preflightError.value = '预检暂时无法完成；没有创建分析任务。'
  }
  preflightLoading.value = false
}

async function requestPreflight(requested: Selection): Promise<void> {
  preflightLoading.value = true
  preflightError.value = ''
  try {
    const loaded = await apiJson<Preflight>(
      `/api/v1/analysis/works/${encodeURIComponent(props.workId)}/preflight`,
      { method: 'POST', body: JSON.stringify(requested) },
    )
    selection.value = {
      ...requested,
      document_version_id: loaded.document_version_id ?? props.documentVersionId,
      backend_id: loaded.backend_id,
      model_id: loaded.model_id,
      reasoning_effort: loaded.reasoning_effort,
      service_tier: loaded.service_tier,
      language: loaded.language,
      template_version_id: loaded.template_version_id,
      external_research: loaded.external_research,
    }
    preflight.value = loaded
    ensureSelectedCapability(loaded)
  } catch (error) {
    preflightError.value = error instanceof ApiResponseError && error.status === 409
      ? '论文证据或 Agent 设置已变化，请重新核对后再开始。'
      : '预检暂时无法完成；没有创建分析任务。'
    throw error
  } finally {
    preflightLoading.value = false
  }
}

function ensureSelectedCapability(value: Preflight): void {
  if (capabilities.value.some((item) => item.backend_id === value.backend_id)) return
  capabilities.value = [{
    backend_id: value.backend_id,
    display_name: value.backend_display_name,
    location: value.backend_location,
    probe_code: value.ready ? 'ready' : 'unavailable',
    recommended_action: value.ready ? null : t('请在设置中检查 Agent 状态后重试。'),
    installed: value.ready,
    authenticated: value.ready,
    available: value.ready,
    supports_schema: true,
    supports_streaming: true,
    supports_vision: false,
    supports_external_research: value.external_research,
    credential_required: false,
    credential_configured: false,
    supports_manual_model_id: value.manual_model_id_allowed,
    supports_default_model: value.default_model_allowed,
    models: value.model_options,
    degradation_reasons: value.warnings,
  }]
}

async function refreshPreflight(requested: Selection): Promise<void> {
  selection.value = requested
  await requestPreflight(requested).catch(() => undefined)
}

async function createAnalysis(requested: Selection): Promise<void> {
  if (!preflight.value?.ready || preflightSubmitting.value) return
  preflightSubmitting.value = true
  preflightError.value = ''
  try {
    const job = await apiJson<AnalysisJob>(
      `/api/v1/analysis/works/${encodeURIComponent(props.workId)}/reports`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': newIdempotencyKey() },
        body: JSON.stringify({
          selection: requested,
          preflight_fingerprint: preflight.value.preflight_fingerprint,
        }),
      },
    )
    jobStore.upsert(jobSummary(job))
    activeJobId.value = job.id
    storeJob(job.id)
    terminalJob = ''
    fragments.value = []
    failure.value = null
    statusMessage.value = '分析任务已开始；关闭页面后仍可从任务状态恢复。'
    preflightOpen.value = false
  } catch (error) {
    preflightError.value = error instanceof ApiResponseError && error.status === 409
      ? '预检已经过期。请更新预检后再开始，模型尚未调用。'
      : '分析任务未能创建，模型尚未调用。'
  } finally {
    preflightSubmitting.value = false
  }
}

function newIdempotencyKey(): string {
  const random = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `analysis-ui-${random}`
}

async function refreshFragments(jobId: string): Promise<void> {
  try {
    fragments.value = await apiJson<Fragment[]>(
      `/api/v1/analysis/jobs/${encodeURIComponent(jobId)}/fragments`,
    )
  } catch {
    // Persisted fragments are supplemental; Job progress remains authoritative.
  }
}

async function loadFailure(jobId: string): Promise<void> {
  try {
    failure.value = await apiJson<Failure>(
      `/api/v1/analysis/jobs/${encodeURIComponent(jobId)}/failure`,
    )
  } catch {
    failure.value = {
      job_id: jobId,
      work_id: props.workId,
      error_category: 'unavailable',
      error_message: t('分析未能完成，诊断草稿暂时无法读取。'),
      raw_response: '',
      validation_errors: [],
      repair_count: 0,
      request_fingerprint: '0'.repeat(64),
      created_at: new Date().toISOString(),
    }
  }
}

async function cancelAnalysis(): Promise<void> {
  const job = activeJob.value
  if (!job || job.cancel_requested) return
  statusMessage.value = '正在请求取消；已保存的报告和片段不会被删除。'
  await jobStore.requestCancel(job.id)
}

function printReport(): void {
  if (!selectedReport.value) return
  window.open(
    `/api/v1/analysis/reports/${encodeURIComponent(selectedReport.value.id)}/print`,
    '_blank',
    'noopener,noreferrer',
  )
}

function failureTitle(value: string): string {
  const labels: Record<string, string> = {
    invalid_output: t('结构化报告未通过验证'),
    authentication: t('Agent 后端需要重新认证'),
    unavailable: t('Agent 后端当前不可用'),
    cancelled: t('分析已取消'),
    timeout: t('分析等待超时'),
    analysis_internal_error: t('分析已安全停止'),
  }
  return labels[value] ?? t('分析未能完成')
}

function evidenceLabel(value: string): string {
  return value === 'full_text' ? t('全文证据') : t('仅摘要证据')
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(currentLocale(), {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function referenceLabel(reference: Reference): string {
  if (reference.kind === 'abstract') return t('摘要')
  if (reference.kind === 'annotation') return reference.locator || t('批注')
  if (reference.kind === 'external') return reference.external_title || t('外部来源')
  const page = reference.page_number ? t('页 {0}', reference.page_number) : t('论文证据')
  return reference.locator ? `${page} · ${reference.locator}` : page
}

function navigateReference(reference: Reference): void {
  if (reference.page_number) emit('navigatePage', reference.page_number)
}

watch(
  () => [props.workId, props.documentVersionId, props.documentContentSha256],
  () => {
    activeJobId.value = ''
    terminalJob = ''
    fragments.value = []
    failure.value = null
    reports.value = []
    selectedReport.value = null
    selectedReportId.value = ''
    selection.value = initialSelection()
    preflightOpen.value = false
    void initialize()
  },
)

watch(
  () => {
    const job = activeJob.value
    return job ? `${job.id}:${job.status}:${job.progress}:${job.cancel_requested}` : ''
  },
  async () => {
    const job = activeJob.value
    if (!job) return
    if (['queued', 'running'].includes(job.status)) {
      await refreshFragments(job.id)
      return
    }
    if (terminalJob === job.id) return
    terminalJob = job.id
    await refreshFragments(job.id)
    if (job.status === 'succeeded') {
      statusMessage.value = '正式报告已生成并绑定当前证据版本。'
      failure.value = null
      await loadReports(job.id)
    } else if (job.status === 'failed') {
      await loadFailure(job.id)
      statusMessage.value = '本次分析失败；已有正式报告没有改变。'
    } else if (job.status === 'cancelled') {
      statusMessage.value = '分析已取消；没有生成新的正式报告。'
    }
    storeJob('')
    activeJobId.value = ''
  },
  { flush: 'post' },
)

onMounted(() => {
  void initialize()
  if (props.autoOpenPreflight) void openPreflight()
})
</script>

<template>
  <div class="analysis-panel" data-test="analysis-panel">
    <div v-if="reportsLoading" class="analysis-state" role="status" aria-live="polite">
      <PhArrowClockwise :size="20" class="spinning" aria-hidden="true" />
      <span>{{ t('正在读取本地报告版本…') }}</span>
    </div>

    <template v-else>
      <header v-if="reports.length || selectedReport" class="analysis-toolbar">
        <label>
          <span>{{ t('报告版本') }}</span>
          <select
            v-model="selectedReportId"
            data-test="report-version"
            @change="loadReport(selectedReportId)"
          >
            <option v-for="item in reports" :key="item.id" :value="item.id">
              v{{ item.version }} · {{ evidenceLabel(item.evidence_level) }} · {{ formatDate(item.created_at) }}
            </option>
          </select>
        </label>
        <button type="button" @click="openPreflight">
          <PhSparkle :size="16" aria-hidden="true" />
          {{ t('重新分析') }}
        </button>
      </header>

      <section v-if="active" class="analysis-progress" aria-live="polite">
        <div>
          <PhSparkle :size="18" aria-hidden="true" />
          <div>
            <strong>{{ activeJob?.status === 'queued' ? t('等待 Agent') : t('正在生成报告') }}</strong>
            <span>{{ activeJob?.progress ?? 0 }}%</span>
          </div>
        </div>
        <progress :value="activeJob?.progress ?? 0" max="100">
          {{ activeJob?.progress ?? 0 }}%
        </progress>
        <p>{{ statusMessage }}</p>
        <details v-if="fragments.length" class="analysis-fragments" open>
          <summary>{{ t('已保存的生成片段') }}</summary>
          <ol>
            <li v-for="fragment in fragments.slice(-6)" :key="fragment.sequence">
              {{ fragment.text }}
            </li>
          </ol>
        </details>
        <button
          type="button"
          data-test="cancel-analysis"
          :disabled="activeJob?.cancel_requested"
          @click="cancelAnalysis"
        >
          <PhStopCircle :size="16" aria-hidden="true" />
          {{ activeJob?.cancel_requested ? t('正在取消') : t('取消分析') }}
        </button>
      </section>

      <section v-if="failure" class="analysis-failure" role="alert">
        <PhWarningCircle :size="20" aria-hidden="true" />
        <div>
          <span>{{ t('失败草稿') }}</span>
          <h2>{{ failureTitle(failure.error_category) }}</h2>
          <p>{{ serverMessage(failure.error_message) }}</p>
          <p v-if="failure.repair_count">
            {{ t('已尝试自动修复') }} {{ failure.repair_count }} {{ t('次，旧正式报告仍然保留。') }}
          </p>
          <details v-if="failure.validation_errors.length">
            <summary>{{ t('查看验证摘要') }}</summary>
            <ul>
              <li v-for="item in failure.validation_errors" :key="item">{{ item }}</li>
            </ul>
          </details>
          <button type="button" data-test="retry-analysis" @click="openPreflight">
            {{ t('重新预检') }}
          </button>
        </div>
      </section>

      <p v-if="statusMessage && !active" class="analysis-status" role="status" aria-live="polite">
        {{ statusMessage }}
      </p>

      <p
        v-if="loadError && selectedReport"
        class="analysis-status report-load-warning"
        data-test="report-load-warning"
        role="alert"
      >
        {{ loadError }} {{ t('当前显示的上一份正式报告没有改变。') }}
      </p>

      <section v-if="loadError && !selectedReport" class="analysis-error" role="alert">
        <h2>{{ t('报告暂时不可读') }}</h2>
        <p>{{ loadError }}</p>
        <button type="button" @click="initialize">{{ t('重新读取') }}</button>
      </section>

      <section
        v-else-if="!selectedReport && !active && !failure"
        class="analysis-empty"
        data-test="analysis-empty"
      >
        <PhFileText :size="25" weight="thin" aria-hidden="true" />
        <span>{{ t('按需生成') }}</span>
        <h2>{{ t('尚未分析这篇论文') }}</h2>
        <p>{{ t('只有你确认预检并点击开始后，应用才会调用 Agent。查看论文、批注和评分不会触发模型。') }}</p>
        <button
          type="button"
          data-test="open-analysis-preflight"
          @click="openPreflight"
        >
          <PhSparkle :size="17" aria-hidden="true" />
          {{ t('分析这篇论文') }}
        </button>
      </section>

      <article v-if="selectedReport" class="analysis-report" :aria-busy="reportLoading">
        <header class="report-heading">
          <div class="report-kicker">
            <span>{{ evidenceLabel(selectedReport.evidence_level) }}</span>
            <span>{{ selectedReport.backend_id }}</span>
            <span v-if="!currentVersionReport" class="other-version">{{ t('其他文档版本') }}</span>
          </div>
          <h2>{{ selectedReport.content.title }}</h2>
          <p>
            v{{ selectedReport.version }} · {{ selectedReport.resolved_model_id || t('后端默认模型') }}
            · {{ selectedReport.reasoning_effort }} · {{ formatDate(selectedReport.created_at) }}
          </p>
          <div class="report-actions" :aria-label="t('导出分析报告')">
            <a
              :href="`/api/v1/analysis/reports/${encodeURIComponent(selectedReport.id)}/markdown`"
              data-test="report-markdown-export"
            >
              <PhDownloadSimple :size="15" aria-hidden="true" />
              Markdown
            </a>
            <a
              :href="`/api/v1/analysis/reports/${encodeURIComponent(selectedReport.id)}/html`"
              data-test="report-html-export"
            >
              <PhArrowSquareOut :size="15" aria-hidden="true" />
              HTML
            </a>
            <button type="button" data-test="print-report" @click="printReport">
              <PhPrinter :size="15" aria-hidden="true" />
              {{ t('打印 / PDF') }}
            </button>
          </div>
        </header>

        <section
          v-for="section in reportSections"
          :key="section.key"
          class="report-section"
          :class="section.emphasis"
        >
          <div>
            <span>{{ String(reportSections.indexOf(section) + 1).padStart(2, '0') }}</span>
            <h3>{{ section.title }}</h3>
          </div>
          <ol>
            <li v-for="(statement, index) in section.statements" :key="`${section.key}-${index}`">
              <p>{{ statement.text }}</p>
              <div class="report-references" :aria-label="t('证据引用')">
                <template v-for="reference in statement.references" :key="reference.evidence_id">
                  <a
                    v-if="reference.kind === 'external' && reference.url"
                    :href="reference.url"
                    target="_blank"
                    rel="noreferrer noopener"
                  >{{ referenceLabel(reference) }}</a>
                  <button
                    v-else-if="reference.page_number"
                    type="button"
                    @click="navigateReference(reference)"
                  >{{ referenceLabel(reference) }}</button>
                  <span v-else>{{ referenceLabel(reference) }}</span>
                </template>
              </div>
            </li>
          </ol>
        </section>

        <footer class="report-provenance">
          <PhCheckCircle :size="17" aria-hidden="true" />
          <div>
            <strong>{{ t('可复现记录') }}</strong>
            <span>
              {{ t('文档') }} {{ selectedReport.document_content_sha256?.slice(0, 10) ?? t('摘要') }}
              {{ t('· 证据') }} {{ selectedReport.evidence_sha256.slice(0, 10) }}
              {{ t('· 提示词') }} {{ selectedReport.prompt_sha256.slice(0, 10) }}
            </span>
          </div>
        </footer>
      </article>
    </template>

    <AnalysisPreflightDialog
      :open="preflightOpen"
      :preflight="preflight"
      :selection="selection"
      :capabilities="capabilities"
      :loading="preflightLoading"
      :error-message="preflightError"
      :submitting="preflightSubmitting"
      @close="preflightOpen = false"
      @preview="refreshPreflight"
      @confirm="createAnalysis"
    />
  </div>
</template>

<style scoped>
.analysis-panel {
  min-height: 100%;
  color: var(--color-ink);
}

.analysis-state,
.analysis-error,
.analysis-empty {
  margin: 30px 22px;
  padding-top: 18px;
  border-top: 2px solid var(--color-forest);
}

.analysis-state {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
}

.analysis-empty > svg {
  color: var(--color-forest);
}

.analysis-empty > span,
.analysis-failure > div > span {
  display: block;
  margin-top: 13px;
  color: var(--color-wine);
  font-size: var(--type-meta);
  font-weight: 750;
  letter-spacing: 0.13em;
}

.analysis-empty h2,
.analysis-error h2,
.analysis-failure h2 {
  margin: 16px 0 9px;
  font-family: var(--font-editorial);
  font-size: 22px;
  font-weight: 500;
  line-height: 1.25;
}

.analysis-empty p,
.analysis-error p,
.analysis-failure p {
  margin: 0;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  line-height: 1.75;
}

.analysis-empty button,
.analysis-error button,
.analysis-failure button,
.analysis-toolbar button,
.analysis-progress > button,
.report-actions a,
.report-actions button {
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  font-size: var(--type-meta);
  font-weight: 700;
  text-decoration: none;
  cursor: pointer;
}

.analysis-empty button {
  margin-top: 19px;
  border: 1px solid var(--color-forest);
  background: var(--color-forest);
  color: var(--color-on-accent);
}

.analysis-toolbar {
  min-height: 56px;
  display: flex;
  align-items: end;
  gap: 8px;
  padding: 9px 14px;
  border-bottom: 1px solid var(--color-line);
  background: var(--color-paper-deep);
}

.analysis-toolbar label {
  min-width: 0;
  display: grid;
  flex: 1;
  gap: 3px;
}

.analysis-toolbar label span {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.analysis-toolbar select {
  width: 100%;
  min-height: 30px;
  border: 0;
  background: transparent;
  color: var(--color-ink);
  font-size: var(--type-meta);
}

.analysis-toolbar button,
.report-actions a,
.report-actions button {
  border: 1px solid var(--color-line);
  background: var(--color-paper);
  color: var(--color-forest);
}

.analysis-progress,
.analysis-failure {
  margin: 16px 14px;
  padding: 13px;
  border-left: 3px solid var(--color-forest);
  background: var(--color-forest-soft);
}

.analysis-progress > div:first-child {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--color-forest);
}

.analysis-progress > div:first-child > div {
  min-width: 0;
  display: flex;
  flex: 1;
  justify-content: space-between;
  gap: 8px;
  font-size: var(--type-meta);
}

.analysis-progress progress {
  width: 100%;
  height: 5px;
  margin-top: 10px;
  overflow: hidden;
  border: 0;
  border-radius: 999px;
  accent-color: var(--color-forest);
}

.analysis-progress p,
.analysis-status {
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  line-height: 1.55;
}

.analysis-progress > button {
  margin-top: 9px;
  border: 1px solid var(--color-line);
  background: transparent;
  color: var(--color-wine);
}

.analysis-progress > button:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.analysis-fragments {
  margin-top: 10px;
  font-size: var(--type-meta);
}

.analysis-fragments summary {
  color: var(--color-forest);
  cursor: pointer;
}

.analysis-fragments ol {
  margin: 7px 0 0;
  padding-left: 18px;
  color: var(--color-muted-strong);
}

.analysis-failure {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  border-left-color: var(--color-wine);
  background: var(--color-wine-soft);
  color: var(--color-wine);
}

.analysis-failure > div > span {
  margin-top: 0;
}

.analysis-failure h2 {
  margin-top: 7px;
  color: var(--color-ink);
  font-size: 18px;
}

.analysis-failure details {
  margin-top: 8px;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
}

.analysis-failure button {
  margin-top: 11px;
  border: 1px solid var(--color-wine);
  background: transparent;
  color: var(--color-wine);
}

.analysis-status {
  margin: 10px 14px;
}

.report-load-warning {
  padding: 9px 10px;
  border-left: 3px solid var(--color-wine);
  background: var(--color-wine-soft);
}

.analysis-report {
  padding: 22px 18px 34px;
}

.report-heading {
  padding-bottom: 20px;
  border-bottom: 2px solid var(--color-forest);
}

.report-kicker {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.report-kicker span {
  padding: 3px 7px;
  border: 1px solid var(--color-line);
  border-radius: 999px;
  color: var(--color-forest);
  font-size: var(--type-meta);
}

.report-kicker .other-version {
  border-color: var(--color-wine);
  color: var(--color-wine);
}

.report-heading h2 {
  margin: 18px 0 7px;
  font-family: var(--font-editorial);
  font-size: clamp(24px, 2.4vw, 31px);
  font-weight: 500;
  line-height: 1.2;
}

.report-heading > p {
  margin: 0;
  color: var(--color-muted);
  font-size: var(--type-meta);
  line-height: 1.5;
}

.report-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 16px;
}

.report-section {
  padding: 21px 0;
  border-bottom: 1px solid var(--color-line);
}

.report-section > div:first-child {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.report-section > div:first-child span {
  color: var(--color-wine);
  font-size: var(--type-meta);
  font-variant-numeric: tabular-nums;
}

.report-section h3 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: 17px;
  font-weight: 500;
}

.report-section > ol {
  margin: 13px 0 0;
  padding: 0;
  list-style: none;
}

.report-section > ol > li + li {
  margin-top: 14px;
}

.report-section p {
  margin: 0;
  color: var(--color-muted-strong);
  font-size: 12px;
  line-height: 1.8;
}

.report-section.author {
  border-left: 3px solid var(--color-forest);
  padding-left: 12px;
}

.report-section.agent {
  border-left: 3px solid var(--color-wine);
  padding-left: 12px;
}

.report-references {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 8px;
}

.report-references button,
.report-references a,
.report-references span {
  min-height: 24px;
  display: inline-flex;
  align-items: center;
  padding: 0 7px;
  border: 1px solid var(--color-line);
  border-radius: 999px;
  background: transparent;
  color: var(--color-forest);
  font-size: var(--type-meta);
  text-decoration: none;
}

.report-references button,
.report-references a {
  cursor: pointer;
}

.report-provenance {
  display: flex;
  gap: 9px;
  margin-top: 24px;
  padding: 12px;
  background: var(--color-paper-deep);
  color: var(--color-forest);
}

.report-provenance strong,
.report-provenance span {
  display: block;
}

.report-provenance strong {
  font-size: var(--type-meta);
}

.report-provenance span {
  margin-top: 4px;
  overflow-wrap: anywhere;
  color: var(--color-muted);
  font-family: ui-monospace, 'Cascadia Code', monospace;
  font-size: var(--type-meta);
  line-height: 1.5;
}

.analysis-panel button:focus-visible,
.analysis-panel a:focus-visible,
.analysis-panel select:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

.spinning {
  animation: analysis-spin 900ms linear infinite;
}

@keyframes analysis-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .spinning { animation: none; }
}
</style>
