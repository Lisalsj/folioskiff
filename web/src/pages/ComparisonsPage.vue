<script setup lang="ts">
import { messageRef, researchText as t } from '@/stores/locale'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import type { components } from '@/api/schema'
import ComparisonHistory from '@/features/comparisons/components/ComparisonHistory.vue'
import ComparisonPreflightPanel from '@/features/comparisons/components/ComparisonPreflight.vue'
import ComparisonResult from '@/features/comparisons/components/ComparisonResult.vue'
import ComparisonSelectionPanel from '@/features/comparisons/components/ComparisonSelection.vue'
import { useOrderedSelection, type LibraryWork } from '@/features/library/ordered-selection'
import { apiJson } from '@/lib/json-api'

type AgentCapability = components['schemas']['AgentCapabilityResponse']
type ComparisonSelection = components['schemas']['ComparisonSelection']
type ComparisonPreflight = components['schemas']['ComparisonPreflight']
type ComparisonReportSummary = components['schemas']['ComparisonReportSummaryResponse']
type ComparisonReportBase = components['schemas']['ComparisonReportResponse']
type ComparisonReport = Omit<ComparisonReportBase, 'provenance'> & {
  provenance: ComparisonReportBase['provenance'] & { batch_count?: number }
}
type ComparisonJobBase = components['schemas']['AnalysisJobResponse']
type ComparisonJob = Omit<ComparisonJobBase, 'result' | 'checkpoint'> & {
  result: { report_id?: string, version?: number } | null
  checkpoint: {
    stage?: string
    processed?: number
    available?: number
    completed_positions?: number[]
  } | null
}

const capabilities = ref<AgentCapability[]>([])
const history = ref<ComparisonReportSummary[]>([])
const maximum = 50
const { selectedIds, selectedWorks, toggle, move } = useOrderedSelection(maximum)
const historyLoading = ref(true)
const loadError = messageRef(t)
const actionError = messageRef(t)
const statusMessage = messageRef(t)
const preflight = ref<ComparisonPreflight | null>(null)
const preflightLoading = ref(false)
const generating = ref(false)
const activeJob = ref<ComparisonJob | null>(null)
const selectedReport = ref<ComparisonReport | null>(null)
const reportLoading = ref(false)
const backendId = ref('codex_cli')
const modelId = ref('')
const language = ref<'zh-CN' | 'en' | 'bilingual'>('zh-CN')
const reasoningEffort = ref<'minimal' | 'low' | 'medium' | 'high' | 'xhigh'>('high')
const serviceTier = ref<'flex' | 'fast'>('flex')
const instruction = ref('')
let generation = 0

const canPrepare = computed(() => selectedIds.value.length >= 2 && !preflightLoading.value)
const backendOptions = computed(() => capabilities.value.length > 0
  ? capabilities.value
  : [
      { backend_id: 'codex_cli', display_name: 'Codex CLI', available: true, models: [] },
      { backend_id: 'ollama_local', display_name: 'Ollama Local', available: true, models: [] },
      { backend_id: 'ollama_cloud', display_name: 'Ollama Cloud', available: true, models: [] },
    ])
const modelOptions = computed(() => capabilities.value
  .find((capability) => capability.backend_id === backendId.value)?.models ?? [])

function currentSelection(): ComparisonSelection {
  return {
    items: selectedIds.value.map((workId) => ({
      work_id: workId,
      document_version_id: null,
    })),
    backend_id: backendId.value,
    model_id: modelId.value.trim() || null,
    reasoning_effort: reasoningEffort.value,
    service_tier: serviceTier.value,
    language: language.value,
    user_instruction: instruction.value.trim() || null,
  }
}

function invalidatePreflight(): void {
  preflight.value = null
  selectedReport.value = null
  statusMessage.value = ''
  actionError.value = ''
}

function toggleWork(work: LibraryWork, checked: boolean): void {
  toggle(work, checked)
  invalidatePreflight()
}

function moveWork(workId: string, direction: -1 | 1): void {
  move(workId, direction)
  invalidatePreflight()
}

async function loadCapabilities(run: number): Promise<void> {
  try {
    const loaded = await apiJson<AgentCapability[]>('/api/v1/settings/agents')
    if (run === generation) capabilities.value = loaded
  } catch {
    // Manual backend/model entry remains usable when capability probing is unavailable.
  }
}

async function loadHistory(run = generation): Promise<void> {
  try {
    const reports = await apiJson<ComparisonReportSummary[]>('/api/v1/comparisons/reports')
    if (run === generation) history.value = reports
  } catch {
    if (run === generation && !loadError.value) loadError.value = '暂时无法读取比较历史。'
  } finally {
    if (run === generation) historyLoading.value = false
  }
}

async function prepareComparison(): Promise<void> {
  if (!canPrepare.value) return
  preflightLoading.value = true
  actionError.value = ''
  statusMessage.value = ''
  const selection = JSON.stringify(currentSelection())
  try {
    const prepared = await apiJson<ComparisonPreflight>('/api/v1/comparisons/preflight', {
      method: 'POST',
      body: selection,
    })
    if (selection === JSON.stringify(currentSelection())) preflight.value = prepared
  } catch {
    actionError.value = '无法准备这次比较。请检查论文证据和 Agent 设置。'
  } finally {
    preflightLoading.value = false
  }
}

function idempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return `comparison-${crypto.randomUUID()}`
  }
  return `comparison-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

async function generateComparison(): Promise<void> {
  if (!preflight.value?.ready || generating.value) return
  generating.value = true
  actionError.value = ''
  statusMessage.value = '比较任务已建立，正在冻结每篇论文的证据。'
  try {
    const job = await apiJson<ComparisonJob>('/api/v1/comparisons/reports', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey() },
      body: JSON.stringify({
        selection: currentSelection(),
        preflight_fingerprint: preflight.value.preflight_fingerprint,
      }),
    })
    activeJob.value = job
    storeJob(job.id)
    await followJob(job.id, generation)
  } catch {
    actionError.value = '比较任务没有完成；已有版本和逐篇证据检查点仍然保留。'
  } finally {
    generating.value = false
  }
}

async function followJob(jobId: string, run: number): Promise<void> {
  while (run === generation) {
    const job = await apiJson<ComparisonJob>(`/api/v1/jobs/${encodeURIComponent(jobId)}`)
    if (run !== generation) return
    activeJob.value = job
    if (job.status === 'succeeded' && job.result?.report_id) {
      storeJob('')
      await Promise.all([
        loadReport(job.result.report_id),
        loadHistory(),
      ])
      statusMessage.value = '比较报告已生成，并保留为新的不可变版本。'
      return
    }
    if (job.status === 'failed' || job.status === 'cancelled') {
      storeJob('')
      throw new Error(job.error_message ?? 'comparison failed')
    }
    await new Promise((resolve) => window.setTimeout(resolve, 500))
  }
}

async function loadReport(reportId: string): Promise<void> {
  reportLoading.value = true
  actionError.value = ''
  try {
    selectedReport.value = await apiJson<ComparisonReport>(
      `/api/v1/comparisons/reports/${encodeURIComponent(reportId)}`,
    )
  } catch {
    actionError.value = '比较版本暂时无法打开。'
  } finally {
    reportLoading.value = false
  }
}

function storageKey(): string {
  return 'paper-reader.comparison.active-job'
}

function storeJob(jobId: string): void {
  try {
    if (jobId) window.localStorage.setItem(storageKey(), jobId)
    else window.localStorage.removeItem(storageKey())
  } catch {
    // Live polling remains available when private browsing blocks storage.
  }
}

async function recoverJob(run: number): Promise<void> {
  let jobId = ''
  try {
    jobId = window.localStorage.getItem(storageKey()) ?? ''
  } catch {
    return
  }
  if (!jobId || run !== generation) return
  generating.value = true
  try {
    await followJob(jobId, run)
  } catch {
    storeJob('')
  } finally {
    if (run === generation) generating.value = false
  }
}

onMounted(() => {
  const run = ++generation
  void Promise.all([
    loadCapabilities(run),
    loadHistory(run),
    recoverJob(run),
  ])
})
onBeforeUnmount(() => { generation += 1 })
</script>

<template>
  <header class="comparison-header">
    <div>
      <p class="eyebrow">{{ t('证据综合') }}</p>
      <h1>{{ t('多论文比较') }}</h1>
      <p class="lede">{{ t('从本地论文库选择研究对象，Agent 只依据冻结证据比较方法、结论与权衡。') }}</p>
    </div>
    <div class="selection-count" aria-live="polite">
      <strong>{{ selectedIds.length }}</strong>
      <span>{{ selectedIds.length ? t('已选择 {0} 篇', selectedIds.length) : t('尚未选择论文') }}</span>
    </div>
  </header>

  <p v-if="loadError" class="notice error" role="alert">{{ loadError }}</p>
  <p v-if="actionError" class="notice error" role="alert">{{ actionError }}</p>
  <p v-if="statusMessage" class="notice" role="status">{{ statusMessage }}</p>

  <ComparisonSelectionPanel
    :selected-ids="selectedIds"
    :selected-works="selectedWorks"
    :maximum="maximum"
    @toggle="toggleWork"
    @move="moveWork"
  />

  <section class="comparison-controls" aria-labelledby="comparison-settings-title">
    <div class="section-heading">
      <div>
        <span>03</span>
        <h2 id="comparison-settings-title">{{ t('准备 Agent 比较') }}</h2>
      </div>
    </div>
    <div class="control-grid">
      <label>{{ t('后端') }}
        <select v-model="backendId" @change="invalidatePreflight">
          <option
            v-for="backend in backendOptions"
            :key="backend.backend_id"
            :value="backend.backend_id"
          >{{ backend.display_name }}{{ backend.available ? '' : t('（不可用）') }}</option>
        </select>
      </label>
      <label>{{ t('模型 ID（可选）') }}
        <input
          v-model="modelId"
          type="text"
          list="comparison-model-options"
          :placeholder="t('使用后端默认模型，或手填模型 ID')"
          @input="invalidatePreflight"
        >
        <datalist id="comparison-model-options" data-test="comparison-model-options">
          <option
            v-for="model in modelOptions"
            :key="model.model_id"
            :value="model.model_id"
          >{{ model.display_name }}</option>
        </datalist>
      </label>
      <label>{{ t('报告语言') }}
        <select v-model="language" @change="invalidatePreflight">
          <option value="zh-CN">{{ t('中文') }}</option>
          <option value="en">English</option>
          <option value="bilingual">{{ t('中英双语') }}</option>
        </select>
      </label>
      <label>{{ t('推理强度') }}
        <select v-model="reasoningEffort" @change="invalidatePreflight">
          <option value="low">{{ t('低') }}</option>
          <option value="medium">{{ t('中') }}</option>
          <option value="high">{{ t('高（默认）') }}</option>
          <option value="xhigh">{{ t('极高') }}</option>
        </select>
      </label>
      <label>{{ t('服务层级') }}
        <select v-model="serviceTier" @change="invalidatePreflight">
          <option value="flex">{{ t('灵活') }}</option>
          <option value="fast">{{ t('快速') }}</option>
        </select>
      </label>
      <label class="instruction">{{ t('比较关注点（可选）') }}
        <textarea
          v-model="instruction"
          rows="3"
          :placeholder="t('例如：重点比较数据泄漏控制、外部有效性与复现实验。')"
          @input="invalidatePreflight"
        />
      </label>
    </div>
    <button
      type="button"
      data-test="prepare-comparison"
      class="secondary-action"
      :disabled="!canPrepare"
      @click="prepareComparison"
    >
      {{ preflightLoading ? t('正在核对证据…') : t('核对证据与范围') }}
    </button>
  </section>

  <ComparisonPreflightPanel
    v-if="preflight"
    :preflight="preflight"
    :active-job="activeJob"
    :generating="generating"
    @generate="generateComparison"
  />

  <section class="results-layout" :aria-label="t('比较报告与版本历史')">
    <ComparisonResult :report="selectedReport" :loading="reportLoading" />
    <ComparisonHistory :history="history" :loading="historyLoading" @select="loadReport" />
  </section>
</template>

<style scoped>
.comparison-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 32px;
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
h2,
p {
  margin-top: 0;
}

h1,
h2 {
  font-family: var(--font-editorial);
  font-weight: 500;
}

h1 {
  margin-bottom: 10px;
  font-size: clamp(38px, 5vw, 58px);
  letter-spacing: -0.035em;
}

.lede {
  max-width: 720px;
  margin-bottom: 0;
  color: var(--color-muted);
  line-height: 1.8;
}

.selection-count {
  min-width: 150px;
  padding-left: 20px;
  border-left: 1px solid var(--color-line);
}

.selection-count strong,
.selection-count span {
  display: block;
}

.selection-count strong {
  color: var(--color-wine);
  font-family: var(--font-editorial);
  font-size: 36px;
  font-weight: 500;
}

.selection-count span {
  margin-top: 4px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.notice {
  margin: 20px 0 0;
  padding: 11px 14px;
  border-left: 3px solid var(--color-forest);
  background: var(--color-forest-soft);
  font-size: 12px;
  line-height: 1.6;
}

.notice.error {
  border-color: var(--color-danger);
  background: var(--color-wine-soft);
}

.comparison-controls {
  margin-top: 52px;
  padding-top: 26px;
  border-top: 1px solid var(--color-line);
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 18px;
}

.section-heading > div {
  display: flex;
  align-items: baseline;
  gap: 11px;
}

.section-heading span {
  color: var(--color-wine);
  font-family: var(--font-editorial);
  font-size: 13px;
}

.section-heading h2 {
  margin: 0;
  font-size: 25px;
}

.control-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.control-grid label {
  display: grid;
  gap: 7px;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  font-weight: 650;
}

.control-grid .instruction {
  grid-column: span 2;
}

.control-grid input,
.control-grid select,
.control-grid textarea {
  width: 100%;
  min-height: 39px;
  padding: 8px 10px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-raised);
  color: var(--color-ink);
}

.control-grid textarea {
  resize: vertical;
  line-height: 1.6;
}

.secondary-action {
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  margin-top: 18px;
  padding: 0 15px;
  border: 1px solid var(--color-forest);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-forest);
  font-weight: 650;
  cursor: pointer;
}

.secondary-action:disabled {
  opacity: 0.45;
  cursor: default;
}

.results-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 0.34fr);
  gap: clamp(34px, 5vw, 62px);
  margin-top: 56px;
}

button:focus-visible,
input:focus-visible,
select:focus-visible,
textarea:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

@media (max-width: 980px) {
  .results-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .comparison-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .selection-count {
    min-width: 0;
    padding: 0;
    border: 0;
  }

  .control-grid {
    grid-template-columns: 1fr;
  }

  .control-grid .instruction {
    grid-column: auto;
  }

  .secondary-action {
    width: 100%;
  }
}
</style>
