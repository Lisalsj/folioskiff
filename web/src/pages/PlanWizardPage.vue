<script setup lang="ts">
import { discoveryText as t, messageRef, serverMessage } from '@/stores/locale'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  PhArrowLeft,
  PhArrowRight,
  PhCheck,
  PhMinus,
  PhPlus,
  PhSparkle,
} from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import { ApiResponseError, apiJson } from '@/lib/json-api'
import { usePreferencesStore } from '@/stores/preferences'

type KeywordVersion = components['schemas']['KeywordVersionResponse']
type KeywordDefinition = components['schemas']['KeywordSetDefinition']
type ConceptGroup = components['schemas']['ConceptGroup']
type SourceCapability = components['schemas']['SourceCapabilityResponse']
type PlanCreate = components['schemas']['DiscoveryPlanCreate']
type PlanResponse = components['schemas']['DiscoveryPlanResponse']
type AcceptedJob = components['schemas']['AcceptedJobResponse']
type JobDetail = components['schemas']['JobDetail']
type PreferenceContext = components['schemas']['PreferenceContextResponse']
type AgentCapability = components['schemas']['AgentCapabilityResponse']
type DefaultAgentBackend = components['schemas']['UserPreferencesUpdate']['default_agent_backend']
type DefaultReasoningEffort = components['schemas']['UserPreferencesUpdate']['default_reasoning_effort']
type SectionKey = 'required' | 'boost' | 'excluded'
type ScheduleFrequency = components['schemas']['ScheduleSpec']['frequency']

interface PreviewPaper {
  external_id: string
  title: string
  authors: string[]
  updated_at: string | null
}

interface SourcePreview {
  connector_id: string
  display_name: string
  status: string
  estimate: number | null
  estimate_exact: boolean
  samples: PreviewPaper[]
  keyword_impact: Record<string, number> | null
  warnings: string[]
  error_code: string | null
  error_message: string | null
  retryable: boolean
  setup_url: string | null
}

interface PreviewResult {
  plan_id: string
  keyword_version_id: string
  months: string[]
  warnings: string[]
  sources: SourcePreview[]
}

const sections: Array<{ key: SectionKey; label: string; description: string }> = [
  { key: 'required', get label() { return t('必需概念') }, get description() { return t('每一组都必须命中，组内同义词使用 OR。') } },
  { key: 'boost', get label() { return t('增强概念') }, get description() { return t('用于提高召回与相关性，不构成硬条件。') } },
  { key: 'excluded', get label() { return t('排除概念') }, get description() { return t('命中这些概念的候选会被本地严格排除。') } },
]
const stepLabels = computed(() => [t('研究意图'), t('关键词工作台'), t('月份与来源'), t('来源预览')])
const weekDays = computed(() => [t('周一'), t('周二'), t('周三'), t('周四'), t('周五'), t('周六'), t('周日')])
const router = useRouter()
const preferences = usePreferencesStore()
const step = ref(0)
const planName = ref('')
const researchIntent = ref('')
const keywordVersion = ref<KeywordVersion>()
const definition = ref<KeywordDefinition>()
const originalDefinition = ref('')
const sourceCapabilities = ref<SourceCapability[]>([])
const sourceSelection = ref<Record<string, boolean>>({})
const dateMode = ref<'rolling' | 'fixed'>('rolling')
const rollingMonths = ref(1)
const startMonth = ref(currentMonth())
const endMonth = ref(currentMonth())
const scheduleFrequency = ref<ScheduleFrequency>('manual')
const scheduleTime = ref('08:00')
const weeklyDay = ref(0)
const monthlyDay = ref(1)
const arxivCategories = ref('')
const openReviewVenue = ref('')
const openReviewInvitation = ref('')
const europePmcSource = ref('')
const ieeeContentType = ref('')
const ieeePublisher = ref('')
const springerContentType = ref('')
const openAccessOnly = ref<Record<string, boolean>>({})
const preview = ref<PreviewResult>()
const pending = ref(false)
const errorMessage = messageRef(t)
const preferenceContext = ref<PreferenceContext>()
const preferenceContextPending = ref(true)
const preferenceContextError = messageRef(t)
const usePreferenceContext = ref(true)
const manualKeywordMode = ref(false)
const agentCapabilities = ref<AgentCapability[]>([])
const agentSelectionPending = ref(true)
const agentSelectionError = messageRef(t)
const selectedAgentBackend = ref<DefaultAgentBackend>('codex_cli')
const selectedAgentModel = ref('')
const selectedAgentReasoning = ref<DefaultReasoningEffort>('medium')

const selectedSources = computed(() =>
  sourceCapabilities.value
    .filter((source) => source.enabled && sourceSelection.value[source.connector_id])
    .map((source) => source.connector_id),
)
const selectableAgents = computed(() => agentCapabilities.value)
const selectedAgent = computed(() =>
  selectableAgents.value.find((agent) => agent.backend_id === selectedAgentBackend.value),
)
const selectedAgentModels = computed(() => selectedAgent.value?.models ?? [])
const selectedAgentLabel = computed(() => selectedAgent.value?.backend_id === 'codex_cli'
  ? 'OpenAI / Codex CLI'
  : selectedAgent.value?.display_name ?? selectedAgentBackend.value)
const selectedModelLabel = computed(() => {
  if (selectedAgentModel.value) return selectedAgentModel.value
  if (selectedAgent.value?.backend_id === 'ollama_local' || selectedAgent.value?.backend_id === 'ollama_cloud') {
    return t('自动选择第一个已发现模型')
  }
  return t('后端默认模型')
})
const pendingLabel = computed(() => manualKeywordMode.value
  ? t('正在按本地规则整理研究概念')
  : t('正在调用 {0} · {1}', selectedAgentLabel.value, selectedModelLabel.value))
const selectedMonthCount = computed(() => {
  if (dateMode.value === 'rolling') return Math.max(1, rollingMonths.value || 1)
  const [startYear, startValue] = startMonth.value.split('-').map(Number)
  const [endYear, endValue] = endMonth.value.split('-').map(Number)
  if (!startYear || !startValue || !endYear || !endValue) return 0
  return (endYear * 12 + endValue) - (startYear * 12 + startValue) + 1
})

function currentMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function cloneDefinition(value: KeywordDefinition): KeywordDefinition {
  return JSON.parse(JSON.stringify(value)) as KeywordDefinition
}

function cleanDefinition(): KeywordDefinition {
  if (!definition.value) throw new Error(t('还没有可确认的关键词'))
  const cleaned = {} as KeywordDefinition
  for (const section of sections) {
    cleaned[section.key] = definition.value[section.key]
      .map((group) => ({
        key: group.key.trim(),
        label: group.label.trim(),
        terms: group.terms.map((term) => term.trim()).filter(Boolean),
      }))
      .filter((group) => group.key && group.label && group.terms.length)
  }
  if (!cleaned.required.length) throw new Error(t('至少保留一个必需概念组'))
  return cleaned
}

function changeSummary(version: KeywordVersion): string[] {
  const summary = version.change_summary
  const result: string[] = []
  const added = summary.added_groups
  const removed = summary.removed_groups
  if (Array.isArray(added) && added.length) result.push(t('新增 {0} 组', added.length))
  if (Array.isArray(removed) && removed.length) result.push(t('移除 {0} 组', removed.length))
  if (
    summary.added_terms &&
    typeof summary.added_terms === 'object' &&
    Object.keys(summary.added_terms).length
  ) result.push(t('补充了关联词'))
  if (
    summary.removed_terms &&
    typeof summary.removed_terms === 'object' &&
    Object.keys(summary.removed_terms).length
  ) result.push(t('精简了关键词'))
  return result
}

function csvTerms(value: string): string[] {
  return [...new Set(value.split(/[,，;；\n]/).map((item) => item.trim()).filter(Boolean))]
}

function sourceFilters(): Record<string, Record<string, unknown>> {
  const filters: Record<string, Record<string, unknown>> = {}
  const selected = new Set(selectedSources.value)
  const put = (source: string, key: string, value: unknown): void => {
    if (!selected.has(source)) return
    if (value === '' || value === false || (Array.isArray(value) && !value.length)) return
    filters[source] ??= {}
    filters[source][key] = value
  }

  put('arxiv', 'categories', csvTerms(arxivCategories.value))
  put('openreview', 'venue', openReviewVenue.value.trim())
  put('openreview', 'invitation', openReviewInvitation.value.trim())
  put('europe_pmc', 'source', europePmcSource.value.trim())
  put('ieee', 'content_type', ieeeContentType.value.trim())
  put('ieee', 'publisher', ieeePublisher.value.trim())
  put('springer_nature', 'content_type', springerContentType.value.trim())
  for (const source of ['europe_pmc', 'ieee', 'springer_nature']) {
    put(source, 'open_access', openAccessOnly.value[source] === true)
  }
  return filters
}

async function loadSources(): Promise<void> {
  try {
    sourceCapabilities.value = await apiJson('/api/v1/settings/sources')
    const preferred = sourceCapabilities.value.find(
      (source) => source.connector_id === 'arxiv' && source.enabled,
    )
    const fallback = sourceCapabilities.value.find((source) => source.enabled)
    const initial = preferred ?? fallback
    if (initial) sourceSelection.value[initial.connector_id] = true
  } catch (caught) {
    errorMessage.value = caught instanceof Error ? caught.message : '无法读取论文来源'
  }
}

async function loadPreferenceContext(): Promise<void> {
  preferenceContextPending.value = true
  preferenceContextError.value = ''
  try {
    const context = await apiJson<PreferenceContext>(
      '/api/v1/keywords/preference-context',
    )
    preferenceContext.value = context
    usePreferenceContext.value = !context.empty
  } catch (caught) {
    preferenceContext.value = undefined
    usePreferenceContext.value = false
    preferenceContextError.value = caught instanceof Error ? caught.message : '无法读取偏好词'
  } finally {
    preferenceContextPending.value = false
  }
}

function syncAgentSelectionFromPreferences(): void {
  const snapshot = preferences.snapshot
  if (!snapshot) return
  selectedAgentBackend.value = snapshot.default_agent_backend
  selectedAgentModel.value = snapshot.default_agent_model_id ?? ''
  selectedAgentReasoning.value = snapshot.default_reasoning_effort
}

function normalizeAgentModelSelection(): void {
  if (!selectedAgentModel.value) return
  const known = selectedAgentModels.value.some((model) => model.model_id === selectedAgentModel.value)
  if (!known && selectedAgent.value?.supports_manual_model_id !== true) {
    selectedAgentModel.value = ''
  }
}

async function loadAgentSelection(): Promise<void> {
  agentSelectionPending.value = true
  agentSelectionError.value = ''
  const [agentResult, preferenceResult] = await Promise.allSettled([
    apiJson<AgentCapability[]>('/api/v1/settings/agents'),
    preferences.load(),
  ])
  if (agentResult.status === 'fulfilled') {
    agentCapabilities.value = agentResult.value
  } else {
    agentSelectionError.value = '暂时无法读取 Agent 和模型列表；可以稍后重试。'
  }
  if (preferenceResult.status === 'fulfilled' && preferenceResult.value) {
    syncAgentSelectionFromPreferences()
  }
  if (!selectedAgent.value && selectableAgents.value.length) {
    selectedAgentBackend.value = selectableAgents.value[0]!.backend_id as DefaultAgentBackend
    selectedAgentModel.value = ''
  }
  normalizeAgentModelSelection()
  agentSelectionPending.value = false
}

function onAgentBackendChange(): void {
  selectedAgentModel.value = ''
  normalizeAgentModelSelection()
}

function preferenceSourceLabel(source: string): string {
  const labels: Record<string, string> = {
    liked: t('喜欢'),
    favorite: t('收藏'),
    interested_author: t('关注作者'),
    aggregated_behavior: t('长期阅读'),
  }
  return labels[source] ?? source
}

async function suggestKeywords(parentVersionId?: string): Promise<void> {
  if (!researchIntent.value.trim()) {
    errorMessage.value = '请先描述研究问题、对象与希望排除的范围'
    return
  }
  if (!planName.value.trim()) planName.value = researchIntent.value.trim().slice(0, 32)
  pending.value = true
  errorMessage.value = ''
  try {
    const includePreferenceContext =
      usePreferenceContext.value &&
      preferenceContext.value !== undefined &&
      !preferenceContext.value.empty
    const version = await apiJson<KeywordVersion>('/api/v1/keywords/suggestions', {
      method: 'POST',
      body: JSON.stringify({
        research_intent: researchIntent.value.trim(),
        mode: manualKeywordMode.value ? 'manual' : 'model',
        agent_backend_id: manualKeywordMode.value ? null : selectedAgentBackend.value,
        agent_model_id: manualKeywordMode.value ? null : selectedAgentModel.value.trim() || null,
        agent_reasoning_effort: manualKeywordMode.value ? null : selectedAgentReasoning.value,
        parent_version_id: parentVersionId ?? null,
        target_count: 18,
        use_preference_context: includePreferenceContext,
        preference_context_sha256: includePreferenceContext
          ? (preferenceContext.value?.context_sha256 ?? null)
          : null,
      }),
    })
    keywordVersion.value = version
    definition.value = cloneDefinition(version.definition)
    originalDefinition.value = JSON.stringify(version.definition)
    preview.value = undefined
    step.value = 1
  } catch (caught) {
    const staleContext =
      caught instanceof ApiResponseError &&
      caught.status === 409 &&
      typeof caught.detail === 'object' &&
      caught.detail !== null &&
      'code' in caught.detail &&
      caught.detail.code === 'preference_context_stale'
    if (staleContext) {
      await loadPreferenceContext()
      errorMessage.value = '阅读偏好刚刚发生变化。请检查更新后的偏好词，再重新生成。'
    } else {
      errorMessage.value = caught instanceof Error ? caught.message : 'Agent 暂时无法建议关键词'
    }
  } finally {
    pending.value = false
  }
}

function addTerm(group: ConceptGroup): void {
  group.terms.push('')
}

function removeTerm(group: ConceptGroup, index: number): void {
  group.terms.splice(index, 1)
}

function addGroup(section: SectionKey): void {
  if (!definition.value) return
  const suffix = Date.now().toString(36)
  definition.value[section].push({
    key: `custom_${suffix}`,
    label: '自定义概念',
    terms: [''],
  })
}

function removeGroup(section: SectionKey, index: number): void {
  definition.value?.[section].splice(index, 1)
}

async function confirmKeywords(): Promise<void> {
  if (!keywordVersion.value) return
  pending.value = true
  errorMessage.value = ''
  try {
    const cleaned = cleanDefinition()
    let version = keywordVersion.value
    if (JSON.stringify(cleaned) !== originalDefinition.value) {
      version = await apiJson<KeywordVersion>('/api/v1/keywords/versions', {
        method: 'POST',
        body: JSON.stringify({
          research_intent: researchIntent.value.trim(),
          parent_version_id: keywordVersion.value.id,
          definition: cleaned,
          generator_info: { backend: 'manual_edit', intent_used_for_filtering: false },
        }),
      })
    }
    keywordVersion.value = await apiJson<KeywordVersion>(
      `/api/v1/keywords/versions/${version.id}/confirm`,
      { method: 'POST' },
    )
    definition.value = cloneDefinition(keywordVersion.value.definition)
    originalDefinition.value = JSON.stringify(keywordVersion.value.definition)
    step.value = 2
  } catch (caught) {
    errorMessage.value = caught instanceof Error ? caught.message : '无法确认关键词'
  } finally {
    pending.value = false
  }
}

function planPayload(): PlanCreate {
  if (!keywordVersion.value?.confirmed_at) throw new Error(t('关键词尚未确认'))
  if (!selectedSources.value.length) throw new Error(t('至少选择一个可用论文来源'))
  if (dateMode.value === 'fixed' && startMonth.value > endMonth.value) {
    throw new Error(t('起始月份不能晚于结束月份'))
  }
  return {
    name: planName.value.trim(),
    research_intent: researchIntent.value.trim(),
    keyword_version_id: keywordVersion.value.id,
    date_range:
      dateMode.value === 'rolling'
        ? {
            mode: 'rolling',
            rolling_months: rollingMonths.value,
            timezone: preferences.resolvedTimezone,
          }
        : {
            mode: 'fixed',
            start_month: startMonth.value,
            end_month: endMonth.value,
            timezone: preferences.resolvedTimezone,
          },
    sources: selectedSources.value,
    source_filters: sourceFilters(),
    schedule: {
      frequency: scheduleFrequency.value,
      local_time: scheduleTime.value,
      weekly_day: weeklyDay.value,
      monthly_day: monthlyDay.value,
    },
    quota: { priority_limit: 20, candidate_limit: 50 },
    enabled: true,
  }
}

async function pollJob(jobId: string): Promise<JobDetail> {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const job = await apiJson<JobDetail>(`/api/v1/jobs/${jobId}`)
    if (!['queued', 'running'].includes(job.status)) return job
    await new Promise((resolve) => window.setTimeout(resolve, 250))
  }
  throw new Error(t('预览等待超时，可在任务抽屉中继续查看'))
}

function parsePreview(value: Record<string, unknown> | null): PreviewResult {
  if (!value || !Array.isArray(value.sources) || !Array.isArray(value.months)) {
    throw new Error(t('来源预览返回了不兼容的数据'))
  }
  return value as unknown as PreviewResult
}

async function previewSources(): Promise<void> {
  pending.value = true
  errorMessage.value = ''
  try {
    const payload = planPayload()
    const accepted = await apiJson<AcceptedJob>('/api/v1/plans/preview', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    const job = await pollJob(accepted.id)
    if (job.status !== 'succeeded') {
      throw new Error(job.error_message ?? t('来源预览失败'))
    }
    preview.value = parsePreview(job.result)
    step.value = 3
  } catch (caught) {
    errorMessage.value = caught instanceof Error ? caught.message : '无法预览论文来源'
  } finally {
    pending.value = false
  }
}

async function savePlan(runAfterSave: boolean): Promise<void> {
  pending.value = true
  errorMessage.value = ''
  try {
    const created = await apiJson<PlanResponse>('/api/v1/plans', {
      method: 'POST',
      body: JSON.stringify(planPayload()),
    })
    if (runAfterSave) {
      await apiJson(`/api/v1/plans/${created.id}/runs`, { method: 'POST' })
    }
    await router.push('/plans')
  } catch (caught) {
    errorMessage.value = caught instanceof Error ? caught.message : '无法保存关注计划'
  } finally {
    pending.value = false
  }
}

function warningLabel(code: string): string {
  const labels: Record<string, string> = {
    estimate_before_local_updated_filter: t('估量会在本地按最近更新时间再次裁剪'),
    source_date_filter_is_approximate: t('该来源日期为近似语义'),
    online_date_is_not_revision_date: t('在线发表日期不等于最近修订日期'),
  }
  return labels[code] ?? code
}

onMounted(() => {
  void loadAgentSelection()
  void loadSources()
  void loadPreferenceContext()
})
</script>

<template>
  <div class="wizard-page">
    <RouterLink class="back-link" to="/plans">
      <PhArrowLeft :size="16" aria-hidden="true" /> {{ t('返回关注计划') }}
    </RouterLink>

    <ol class="step-rail" :aria-label="t('新建计划步骤')">
      <li v-for="(label, index) in stepLabels" :key="label" :class="{ current: index === step, done: index < step }">
        <span>{{ index < step ? '✓' : String(index + 1).padStart(2, '0') }}</span>{{ label }}
      </li>
    </ol>

    <p v-if="errorMessage" class="notice error" role="alert">{{ errorMessage }}</p>
    <p v-if="pending" class="sr-status" role="status" aria-live="polite">{{ t('正在处理，请稍候') }}</p>

    <section v-if="step === 0" class="wizard-section intent-step">
      <p class="eyebrow">{{ t('第一步 · 只用于生成建议') }}</p>
      <h1>{{ t('研究意图') }}</h1>
      <p class="lede">{{ t('先说明研究问题、对象、方法偏好与不想看到的内容。原始意图不会直接过滤论文。') }}</p>
      <label>
        <span>{{ t('计划名称') }}</span>
        <input data-test="plan-name" v-model="planName" maxlength="160" :placeholder="t('例如：可靠智能体月度追踪')" />
      </label>
      <label>
        <span>{{ t('你想研究什么？') }}</span>
        <textarea
          data-test="research-intent"
          v-model="researchIntent"
          rows="8"
          maxlength="10000"
          :placeholder="t('描述研究问题、研究对象、关注的方法，以及希望排除的范围……')"
        />
      </label>
      <div class="intent-note"><strong>{{ t('边界说明') }}</strong><span>{{ t('Agent 会建议多组双语关键词；只有你确认的版本才参与筛选。') }}</span></div>
      <section data-test="preference-context" class="preference-context" aria-labelledby="preference-context-title">
        <div class="preference-context-heading">
          <div>
            <p class="eyebrow">{{ t('可选参考') }}</p>
            <h2 id="preference-context-title">{{ t('参考你的阅读偏好') }}</h2>
          </div>
          <label v-if="preferenceContext && !preferenceContext.empty" class="context-toggle">
            <input
              data-test="use-preference-context"
              v-model="usePreferenceContext"
              type="checkbox"
            />
            <span>{{ usePreferenceContext ? t('本次启用') : t('本次关闭') }}</span>
          </label>
        </div>
        <p v-if="preferenceContextPending" class="context-state" role="status">{{ t('正在整理本地偏好词……') }}</p>
        <div v-else-if="preferenceContextError" class="context-state">
          <p>{{ t('暂时无法读取偏好词，仍可只依据研究意图继续。') }}</p>
          <button type="button" class="text-action" @click="loadPreferenceContext">{{ t('重新读取') }}</button>
        </div>
        <p v-else-if="preferenceContext?.empty" class="context-state">
          {{ t('还没有可用的收藏、喜欢或关注作者记录。本次只依据你的研究意图生成建议。') }}
        </p>
        <template v-else-if="preferenceContext">
          <p v-if="usePreferenceContext" class="context-explanation">
            {{ t('Agent 将看到这些聚合研究词。它们只辅助建议，不会直接筛选论文。') }}
          </p>
          <p v-else class="context-explanation disabled-copy">
            {{ t('本次不会发送这些偏好词；研究意图仍可独立生成建议。') }}
          </p>
          <ol class="preference-term-list" :class="{ muted: !usePreferenceContext }">
            <li v-for="term in preferenceContext.terms" :key="term.term">
              <span>{{ term.term }}</span>
              <small>{{ term.sources.map(preferenceSourceLabel).join(' · ') }}</small>
            </li>
          </ol>
          <p class="context-privacy">{{ t('不包含笔记、反馈原因、报告、聊天或论文正文。') }}</p>
        </template>
      </section>
      <section class="agent-selection" data-test="keyword-agent-selection" aria-labelledby="keyword-agent-title">
        <div class="agent-selection-heading">
          <div>
            <p class="eyebrow">{{ t('模型选择') }}</p>
            <h2 id="keyword-agent-title">{{ t('本次关键词生成使用哪个 Agent？') }}</h2>
          </div>
          <span v-if="!manualKeywordMode" class="agent-selection-summary">
            {{ selectedAgentLabel }} · {{ selectedModelLabel }}
          </span>
        </div>
        <p class="agent-selection-copy">
          {{ t('关键词生成会真实调用你选择的模型；这里的选择只作用于本次请求，不会改动全局默认值。') }}
        </p>
        <p v-if="agentSelectionPending" class="context-state" role="status">{{ t('正在读取可用 Agent 和模型……') }}</p>
        <p v-else-if="agentSelectionError" class="context-state error-copy" role="alert">{{ agentSelectionError }}</p>
        <div v-else class="agent-selection-grid">
          <label>
            <span>{{ t('Agent 后端') }}</span>
            <select
              v-model="selectedAgentBackend"
              data-test="keyword-agent-backend"
              :disabled="pending || manualKeywordMode"
              @change="onAgentBackendChange"
            >
              <option v-for="agent in selectableAgents" :key="agent.backend_id" :value="agent.backend_id">
                {{ agent.backend_id === 'codex_cli' ? 'OpenAI / ' : '' }}{{ agent.display_name }}{{ agent.location === 'cloud' ? t('（云端）') : t('（本机）') }}{{ agent.available ? '' : t(' · 不可用') }}
              </option>
            </select>
          </label>
          <label>
            <span>{{ t('模型') }}</span>
            <select
              v-model="selectedAgentModel"
              data-test="keyword-agent-model"
              :disabled="pending || manualKeywordMode || !selectedAgent"
            >
              <option value="">{{ selectedAgent?.backend_id === 'ollama_local' || selectedAgent?.backend_id === 'ollama_cloud' ? t('自动选择第一个已发现模型') : t('使用后端默认模型') }}</option>
              <option v-for="model in selectedAgentModels" :key="model.model_id" :value="model.model_id">
                {{ model.display_name }}{{ model.context_window ? t(' · 上下文 {0}', model.context_window.toLocaleString()) : '' }}
              </option>
            </select>
            <input
              v-if="selectedAgent?.supports_manual_model_id"
              v-model="selectedAgentModel"
              data-test="keyword-agent-model-custom"
              class="agent-model-custom"
              autocomplete="off"
              spellcheck="false"
              :placeholder="t('或输入自定义模型 ID')"
              :disabled="pending || manualKeywordMode"
            />
          </label>
          <label>
            <span>{{ t('推理强度') }}</span>
            <select
              v-model="selectedAgentReasoning"
              data-test="keyword-agent-reasoning"
              :disabled="pending || manualKeywordMode"
            >
              <option value="minimal">{{ t('最小') }}</option>
              <option value="low">{{ t('低') }}</option>
              <option value="medium">{{ t('中') }}</option>
              <option value="high">{{ t('高') }}</option>
              <option value="xhigh">{{ t('极高') }}</option>
            </select>
          </label>
        </div>
        <p v-if="selectedAgent?.backend_id?.startsWith('ollama_')" class="agent-selection-help">
          {{ t('Ollama 推理模型的思考和答案共用输出额度；选择“最小”可关闭支持此选项的模型的思考，更快生成关键词。') }}
        </p>
        <p v-if="selectedAgent && !selectedAgent.available" class="agent-selection-warning" role="status">
          {{ t('当前后端状态：') }}{{ selectedAgent.probe_code }}{{ t('。提交时会返回明确的后端错误，不会静默改用本地规则。') }}
        </p>
      </section>
      <label class="manual-mode-toggle">
        <input data-test="manual-keyword-mode" v-model="manualKeywordMode" type="checkbox" />
        <span>{{ t('使用本地规则手工模式') }}</span>
        <small>{{ t('只在明确勾选时启用，不会伪装成模型结果。') }}</small>
      </label>
      <button data-test="suggest-keywords" class="primary-action" type="button" :disabled="pending" @click="suggestKeywords()">
        <PhSparkle :size="18" weight="fill" aria-hidden="true" />
        {{ pending ? pendingLabel : manualKeywordMode ? t('按本地规则生成关键词') : t('生成研究关键词') }}
      </button>
      <p v-if="pending && !manualKeywordMode" class="generation-status" role="status">
        {{ t('模型正在加载并推理，完成后才会返回关键词；期间请保持此页面打开。') }}
      </p>
    </section>

    <section v-else-if="step === 1 && definition && keywordVersion" class="wizard-section keyword-step">
      <p class="eyebrow">{{ t('第二步 · 人工确认边界') }}</p>
      <h1>{{ t('关键词工作台') }}</h1>
      <p class="lede">{{ t('编辑、增加或删除概念。必需组之间是 AND，同组词之间是 OR。') }}</p>

      <p
        v-if="keywordVersion.generator_info.mode === 'agent'"
        class="generator-note"
        data-test="generator-info"
      >
        {{ t('模型生成 ·') }} {{ keywordVersion.generator_info.backend_id }}
        <span v-if="keywordVersion.generator_info.resolved_model_id">
          · {{ keywordVersion.generator_info.resolved_model_id }}
        </span>
      </p>
      <p v-else-if="keywordVersion.generator_info.mode === 'local_fallback'" class="generator-note warning">
        {{ t('本地规则生成 · 仅用于显式离线模式') }}
      </p>

      <div v-if="changeSummary(keywordVersion).length" class="diff-note">
        <strong>{{ t('相较上一版') }}</strong>
        <span v-for="item in changeSummary(keywordVersion)" :key="item">{{ item }}</span>
      </div>

      <fieldset v-for="section in sections" :key="section.key" class="concept-section">
        <legend>{{ section.label }}</legend>
        <p>{{ section.description }}</p>
        <div v-for="(group, groupIndex) in definition[section.key]" :key="group.key" class="concept-group">
          <div class="group-heading">
            <input v-model="group.label" :aria-label="t('{0}组名称', section.label)" />
            <button type="button" :aria-label="t('删除{0}', group.label)" @click="removeGroup(section.key, groupIndex)">
              <PhMinus :size="16" aria-hidden="true" />
            </button>
          </div>
          <div class="term-grid">
            <div v-for="(_, termIndex) in group.terms" :key="termIndex" class="term-input">
              <input v-model="group.terms[termIndex]" :aria-label="t('{0}关键词 {1}', group.label, termIndex + 1)" />
              <button type="button" :aria-label="t('删除关键词')" @click="removeTerm(group, termIndex)">×</button>
            </div>
            <button type="button" class="text-action" @click="addTerm(group)"><PhPlus :size="14" /> {{ t('添加词') }}</button>
          </div>
        </div>
        <button type="button" class="add-group" @click="addGroup(section.key)"><PhPlus :size="15" /> {{ t('添加概念组') }}</button>
      </fieldset>

      <div class="actions split-actions">
        <button type="button" class="secondary-action" :disabled="pending" @click="suggestKeywords(keywordVersion.id)">
          {{ t('让 Agent 继续扩展') }}
        </button>
        <button data-test="confirm-keywords" type="button" class="primary-action" :disabled="pending" @click="confirmKeywords">
          <PhCheck :size="17" weight="bold" aria-hidden="true" /> {{ t('确认此关键词版本') }}
        </button>
      </div>
    </section>

    <section v-else-if="step === 2" class="wizard-section source-step">
      <p class="eyebrow">{{ t('第三步 · 自然月范围') }}</p>
      <h1>{{ t('月份与来源') }}</h1>
      <p class="lede">{{ t('日期按论文最近一次更新判断；来源无法精确表达时会在本地再次严格裁剪。') }}</p>

      <fieldset class="date-choice">
        <legend>{{ t('月份模式') }}</legend>
        <label class="radio-row"><input v-model="dateMode" value="rolling" type="radio" /> <span><strong>{{ t('滚动月份') }}</strong>{{ t('每次都包含当前自然月') }}</span></label>
        <div v-if="dateMode === 'rolling'" class="inline-field"><label>{{ t('最近') }} <input v-model.number="rollingMonths" type="number" min="1" /> {{ t('个自然月') }}</label></div>
        <label class="radio-row"><input v-model="dateMode" value="fixed" type="radio" /> <span><strong>{{ t('固定范围') }}</strong>{{ t('用于历史主题回顾') }}</span></label>
        <div v-if="dateMode === 'fixed'" class="month-range">
          <label>{{ t('起始月') }}<input v-model="startMonth" type="month" /></label>
          <span>{{ t('至') }}</span>
          <label>{{ t('结束月') }}<input v-model="endMonth" type="month" /></label>
        </div>
        <p v-if="selectedMonthCount > 24" class="range-warning" role="status">
          {{ t('当前包含') }} {{ selectedMonthCount }} {{ t('个月，运行会拆成逐月批次，耗时和来源配额可能明显增加。') }}
        </p>
        <p data-test="plan-timezone" class="timezone-note">
          {{ t('当前时区：') }}<strong>{{ preferences.resolvedTimezone }}</strong>
          · {{ preferences.timezoneMode === 'system' ? t('跟随系统') : t('自选时区') }}{{ t('。 保存后会冻结在此计划中，不随以后设置变化。') }}
        </p>
      </fieldset>

      <fieldset class="source-choice">
        <legend>{{ t('论文来源') }}</legend>
        <div v-for="source in sourceCapabilities" :key="source.connector_id" class="source-option">
          <label class="source-row" :class="{ disabled: !source.enabled }">
            <input v-model="sourceSelection[source.connector_id]" type="checkbox" :disabled="!source.enabled" />
            <span class="source-copy"><strong>{{ source.display_name }}</strong><small>{{ source.supports_exact_updated_date ? t('精确更新时间') : t('日期需本地校验') }}</small></span>
            <span v-if="!source.enabled" class="source-state">{{ source.disabled_reason === 'missing_api_key' ? t('需要 API key') : t('不可用') }}</span>
            <span v-else class="source-state ready">{{ t('可用') }}</span>
          </label>

          <div v-if="source.enabled && sourceSelection[source.connector_id]" class="source-filters">
            <label v-if="source.connector_id === 'arxiv'">
              {{ t('arXiv 分类（可选，以逗号分隔）') }}
              <input data-test="arxiv-categories" v-model="arxivCategories" placeholder="cs.AI, cs.CL" />
            </label>
            <template v-else-if="source.connector_id === 'openreview'">
              <label>{{ t('Venue（可选）') }}<input v-model="openReviewVenue" placeholder="ICLR.cc/2026/Conference" /></label>
              <label>{{ t('Invitation（可选）') }}<input v-model="openReviewInvitation" :placeholder="t('完整 invitation ID')" /></label>
            </template>
            <label v-else-if="source.connector_id === 'europe_pmc'">
              {{ t('文献来源代码（可选）') }}
              <input v-model="europePmcSource" :placeholder="t('例如 MED')" />
            </label>
            <template v-else-if="source.connector_id === 'ieee'">
              <label>{{ t('内容类型（可选）') }}<input v-model="ieeeContentType" :placeholder="t('Journals 或 Conferences')" /></label>
              <label>{{ t('出版方（可选）') }}<input v-model="ieeePublisher" placeholder="IEEE" /></label>
            </template>
            <label v-else-if="source.connector_id === 'springer_nature'">
              {{ t('内容类型（可选）') }}
              <input v-model="springerContentType" :placeholder="t('Journal 或 Chapter')" />
            </label>
            <label v-if="['europe_pmc', 'ieee', 'springer_nature'].includes(source.connector_id)" class="inline-check">
              <input v-model="openAccessOnly[source.connector_id]" type="checkbox" />
              {{ t('仅开放获取') }}
            </label>
          </div>
        </div>
      </fieldset>

      <fieldset class="schedule-choice">
        <legend>{{ t('运行频率') }}</legend>
        <div class="schedule-grid">
          <label>
            {{ t('频率') }}
            <select data-test="schedule-frequency" v-model="scheduleFrequency">
              <option value="manual">{{ t('仅手动') }}</option>
              <option value="daily">{{ t('每天') }}</option>
              <option value="weekly">{{ t('每周') }}</option>
              <option value="monthly">{{ t('每月') }}</option>
            </select>
          </label>
          <label v-if="scheduleFrequency !== 'manual'">
            {{ t('本地时间') }}
            <input v-model="scheduleTime" type="time" />
          </label>
          <label v-if="scheduleFrequency === 'weekly'">
            {{ t('星期') }}
            <select data-test="weekly-day" v-model.number="weeklyDay">
              <option v-for="(day, index) in weekDays" :key="day" :value="index">{{ day }}</option>
            </select>
          </label>
          <label v-if="scheduleFrequency === 'monthly'">
            {{ t('每月日期') }}
            <input v-model.number="monthlyDay" type="number" min="1" max="28" />
          </label>
        </div>
        <p>{{ t('定时计划只在应用打开时执行；关闭期间错过的运行会等待你确认，不会静默补跑。') }}</p>
      </fieldset>

      <div class="actions">
        <button type="button" class="secondary-action" @click="step = 1">{{ t('返回关键词') }}</button>
        <button data-test="preview-sources" type="button" class="primary-action" :disabled="pending" @click="previewSources">
          {{ pending ? t('正在获取来源样例') : t('预览来源') }} <PhArrowRight :size="17" aria-hidden="true" />
        </button>
      </div>
    </section>

    <section v-else-if="step === 3 && preview" class="wizard-section preview-step">
      <p class="eyebrow">{{ t('第四步 · 尚未保存') }}</p>
      <h1>{{ t('来源预览') }}</h1>
      <p class="lede">{{ t('以下结果不会进入论文库。检查数量、样例和日期警告后，再决定保存或运行。') }}</p>
      <div class="preview-meta"><span>{{ preview.months.join(' — ') }}</span><span>{{ selectedSources.length }} {{ t('个来源') }}</span></div>

      <article v-for="source in preview.sources" :key="source.connector_id" class="source-preview">
        <header>
          <div><p>{{ source.connector_id }}</p><h2>{{ source.display_name }}</h2></div>
          <strong v-if="source.status === 'ready'">{{ source.estimate ?? t('未知') }} {{ t('篇') }}<small>{{ source.estimate_exact ? t('精确估量') : t('近似估量') }}</small></strong>
          <strong v-else class="failed">{{ source.status === 'disabled' ? t('未启用') : t('预览失败') }}</strong>
        </header>
        <dl v-if="source.keyword_impact">
          <div><dt>{{ t('远端候选') }}</dt><dd>{{ source.keyword_impact.remote_candidates ?? 0 }}</dd></div>
          <div><dt>{{ t('月份内') }}</dt><dd>{{ source.keyword_impact.in_date_range ?? 0 }}</dd></div>
          <div><dt>{{ t('关键词命中') }}</dt><dd>{{ source.keyword_impact.matched_confirmed_keywords ?? 0 }}</dd></div>
        </dl>
        <ul v-if="source.samples.length" class="sample-list">
          <li v-for="paper in source.samples" :key="paper.external_id">
            <span>{{ paper.authors.slice(0, 2).join('、') || t('作者未知') }}</span>
            <h3>{{ paper.title }}</h3>
          </li>
        </ul>
        <p v-else-if="source.error_message" class="source-error">{{ serverMessage(source.error_message) }}</p>
        <div v-if="source.warnings.length" class="warnings">
          <span v-for="warning in source.warnings" :key="warning">{{ warningLabel(warning) }}</span>
        </div>
      </article>

      <div class="actions final-actions">
        <button type="button" class="secondary-action" @click="step = 2">{{ t('调整范围') }}</button>
        <button type="button" class="secondary-action" :disabled="pending" @click="savePlan(false)">{{ t('仅保存计划') }}</button>
        <button data-test="save-and-run" type="button" class="primary-action" :disabled="pending" @click="savePlan(true)">
          {{ t('保存并运行') }} <PhArrowRight :size="17" aria-hidden="true" />
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.wizard-page { max-width: 1040px; margin: 0 auto; }
.back-link { display: inline-flex; align-items: center; gap: 7px; color: var(--color-muted-strong); font-size: 12px; text-decoration: none; }
.back-link:hover { color: var(--color-forest); }
.step-rail { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0; margin: 30px 0 clamp(48px, 7vw, 82px); padding: 0; border-top: 1px solid var(--color-line); list-style: none; }
.step-rail li { display: flex; gap: 9px; padding-top: 12px; color: var(--color-muted); font-size: var(--type-meta); }
.step-rail li span { font-family: var(--font-editorial); }
.step-rail li.current { border-top: 2px solid var(--color-forest); margin-top: -1px; color: var(--color-forest); font-weight: 700; }
.step-rail li.done { color: var(--color-muted-strong); }
.generator-note { margin: 18px 0 0; color: var(--color-forest); font-size: 13px; }
.generator-note.warning { color: var(--color-wine); }
.wizard-section { max-width: 900px; }
.intent-step { max-width: 760px; }
.eyebrow { margin: 0 0 10px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 700; letter-spacing: .13em; }
h1, h2, h3, p { margin-top: 0; }
h1, h2, h3 { font-family: var(--font-editorial); font-weight: 500; }
h1 { margin-bottom: 14px; font-size: clamp(38px, 5vw, 58px); line-height: 1; letter-spacing: -.04em; }
.lede { max-width: 720px; margin-bottom: 38px; color: var(--color-muted); font-size: 15px; line-height: 1.75; }
label > span:first-child { display: block; margin-bottom: 8px; color: var(--color-muted-strong); font-size: 12px; font-weight: 650; }
label { display: block; margin-top: 22px; }
input, textarea, select { width: 100%; border: 1px solid var(--color-line); border-radius: var(--radius-sm); background: color-mix(in srgb, var(--color-paper) 85%, transparent); color: var(--color-ink); }
input, select { min-height: 42px; padding: 0 12px; }
textarea { padding: 14px; resize: vertical; font-size: 15px; line-height: 1.7; }
input:focus, textarea:focus, select:focus { border-color: var(--color-forest); outline: 3px solid var(--color-focus); outline-offset: 1px; }
.intent-note { display: flex; gap: 14px; margin: 18px 0 28px; padding: 15px 0; border-top: 1px solid var(--color-line); border-bottom: 1px solid var(--color-line); font-size: 12px; line-height: 1.6; }
.intent-note strong { flex: none; color: var(--color-forest); }
.intent-note span { color: var(--color-muted); }
.preference-context { margin: 28px 0 30px; padding: 22px 0 20px; border-top: 1px solid var(--color-line); border-bottom: 1px solid var(--color-line); }
.preference-context-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; }
.preference-context-heading .eyebrow { margin-bottom: 5px; color: var(--color-muted-strong); }
.preference-context h2 { margin-bottom: 0; font-size: 24px; letter-spacing: -.02em; }
.context-toggle { display: inline-flex; align-items: center; gap: 9px; margin: 2px 0 0; color: var(--color-muted-strong); font-size: 12px; font-weight: 650; cursor: pointer; }
.context-toggle input { width: 17px; min-height: 17px; margin: 0; accent-color: var(--color-forest); }
.context-toggle span { margin: 0 !important; }
.agent-selection { margin: 26px 0 0; padding: 20px 0 18px; border-top: 1px solid var(--color-line); border-bottom: 1px solid var(--color-line); }
.agent-selection-heading { display: flex; align-items: end; justify-content: space-between; gap: 18px; }
.agent-selection-heading .eyebrow { margin-bottom: 5px; color: var(--color-muted-strong); }
.agent-selection h2 { margin-bottom: 0; font-size: 24px; letter-spacing: -.02em; }
.agent-selection-summary { color: var(--color-forest); font-size: var(--type-meta); text-align: right; }
.agent-selection-copy { margin: 12px 0 0; color: var(--color-muted); font-size: 12px; line-height: 1.65; }
.agent-selection-grid { display: grid; grid-template-columns: 1.1fr 1.4fr .8fr; gap: 14px; margin-top: 14px; }
.agent-selection-grid label { margin: 0; color: var(--color-muted-strong); font-size: var(--type-meta); font-weight: 650; }
.agent-selection-grid label > span:first-child { margin-bottom: 6px; font-size: var(--type-meta); }
.agent-selection-grid select { margin-top: 0; min-height: 40px; font-size: 12px; }
.agent-model-custom { margin-top: 7px; min-height: 36px; font-size: var(--type-meta); }
.agent-selection-warning, .error-copy { color: var(--color-wine); }
.agent-selection-warning { margin: 12px 0 0; font-size: var(--type-meta); line-height: 1.55; }
.generation-status { margin: 11px 0 0; color: var(--color-muted); font-size: var(--type-meta); line-height: 1.55; }
.agent-selection-help { margin: 12px 0 0; color: var(--color-muted); font-size: var(--type-meta); line-height: 1.55; }
.manual-mode-toggle { display: flex; align-items: baseline; gap: 8px; margin: 18px 0 10px; color: var(--color-muted-strong); font-size: 12px; cursor: pointer; }
.manual-mode-toggle input { width: 17px; min-height: 17px; margin: 0; accent-color: var(--color-forest); }
.manual-mode-toggle small { color: var(--color-muted); }
.context-state, .context-explanation { max-width: 680px; margin: 16px 0 0; color: var(--color-muted); font-size: 13px; line-height: 1.65; }
.context-state .text-action { margin-top: 8px; }
.disabled-copy { color: var(--color-wine); }
.preference-term-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 24px; margin: 18px 0 0; padding: 0; border-top: 1px solid color-mix(in srgb, var(--color-line) 70%, transparent); list-style: none; transition: opacity .16s ease; }
.preference-term-list.muted { opacity: .48; }
.preference-term-list li { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; min-width: 0; padding: 9px 0; border-bottom: 1px solid color-mix(in srgb, var(--color-line) 70%, transparent); }
.preference-term-list li > span { overflow: hidden; color: var(--color-ink); font-family: var(--font-editorial); font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.preference-term-list small { flex: none; color: var(--color-muted); font-size: var(--type-meta); }
.context-privacy { margin: 14px 0 0; color: var(--color-muted); font-size: var(--type-meta); }
.primary-action, .secondary-action, .add-group, .text-action { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 42px; padding: 0 16px; border-radius: var(--radius-sm); font-size: 13px; font-weight: 650; cursor: pointer; }
.primary-action { border: 1px solid var(--color-forest); background: var(--color-forest); color: var(--color-on-accent); }
.secondary-action, .add-group, .text-action { border: 1px solid var(--color-line); background: transparent; color: var(--color-forest); }
button:focus-visible, .back-link:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
button:disabled { opacity: .55; cursor: wait; }
.notice.error { margin: -26px 0 30px; padding: 12px 0; border-bottom: 1px solid var(--color-danger); color: var(--color-danger); font-size: 13px; }
.sr-status { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
.diff-note { display: flex; flex-wrap: wrap; gap: 9px 18px; margin-bottom: 28px; padding: 14px 0; border-block: 1px solid var(--color-line); color: var(--color-muted); font-size: 12px; }
.diff-note strong { color: var(--color-forest); }
.concept-section, .date-choice, .source-choice, .schedule-choice { margin: 0 0 34px; padding: 0; border: 0; }
.concept-section > legend, .date-choice > legend, .source-choice > legend, .schedule-choice > legend { width: 100%; padding: 18px 0 10px; border-top: 2px solid var(--color-forest); font-family: var(--font-editorial); font-size: 22px; }
.concept-section > p { color: var(--color-muted); font-size: 12px; }
.concept-group { padding: 18px 0; border-bottom: 1px solid var(--color-line); }
.group-heading { display: grid; grid-template-columns: minmax(0, 280px) 36px; gap: 8px; }
.group-heading button { border: 0; background: transparent; color: var(--color-danger); cursor: pointer; }
.term-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }
.term-input { position: relative; }
.term-input input { padding-right: 34px; font-size: 12px; }
.term-input button { position: absolute; right: 4px; top: 4px; width: 30px; height: 34px; border: 0; background: transparent; color: var(--color-muted); cursor: pointer; }
.text-action { min-height: 42px; border-style: dashed; }
.add-group { margin-top: 12px; min-height: 36px; }
.actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 38px; padding-top: 22px; border-top: 1px solid var(--color-line); }
.split-actions { justify-content: space-between; }
.radio-row { display: grid; grid-template-columns: 18px 1fr; gap: 10px; margin: 0; padding: 16px 0; border-bottom: 1px solid var(--color-line); }
.radio-row input, .source-row input { width: 16px; min-height: 16px; margin: 2px 0 0; accent-color: var(--color-forest); }
.radio-row span { font-size: 13px; }
.radio-row strong { display: block; margin-bottom: 3px; }
.timezone-note { margin: 15px 0 0; padding: 10px 12px; border-left: 3px solid var(--color-forest); background: var(--color-forest-soft); color: var(--color-muted-strong); font-size: var(--type-meta); line-height: 1.6; }
.timezone-note strong { color: var(--color-forest); }
.inline-field, .month-range { padding: 14px 28px; background: var(--color-paper-deep); }
.inline-field label, .month-range label { margin: 0; font-size: 12px; }
.inline-field input { display: inline-block; width: 70px; margin: 0 6px; }
.month-range { display: grid; grid-template-columns: 1fr auto 1fr; align-items: end; gap: 14px; }
.month-range span { padding-bottom: 12px; color: var(--color-muted); }
.range-warning { margin: 14px 0 0; padding: 12px 14px; border-left: 3px solid var(--color-danger); color: var(--color-muted-strong); font-size: var(--type-meta); line-height: 1.6; }
.source-option { border-bottom: 1px solid var(--color-line); }
.source-row { display: grid; grid-template-columns: 20px minmax(0, 1fr) auto; align-items: start; gap: 12px; margin: 0; padding: 16px 0; }
.source-row.disabled { opacity: .62; }
.source-copy strong, .source-copy small { display: block; }
.source-copy strong { font-family: var(--font-editorial); font-size: 17px; font-weight: 500; }
.source-copy small { margin-top: 4px; color: var(--color-muted); }
.source-state { color: var(--color-danger); font-size: var(--type-meta); }
.source-state.ready { color: var(--color-forest); }
.source-filters { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin: 0 0 16px 32px; padding: 14px; background: var(--color-paper-deep); }
.source-filters label, .schedule-grid label { margin: 0; color: var(--color-muted-strong); font-size: var(--type-meta); font-weight: 650; line-height: 1.6; }
.source-filters input, .schedule-grid input, .schedule-grid select { margin-top: 5px; min-height: 38px; font-size: 12px; }
.source-filters .inline-check { display: flex; align-items: center; gap: 8px; align-self: end; min-height: 38px; }
.source-filters .inline-check input { width: 16px; min-height: 16px; margin: 0; accent-color: var(--color-forest); }
.schedule-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.schedule-choice > p { margin: 13px 0 0; color: var(--color-muted); font-size: var(--type-meta); line-height: 1.6; }
.preview-meta { display: flex; gap: 24px; margin-bottom: 28px; color: var(--color-muted); font-size: 12px; }
.source-preview { padding: 24px 0 30px; border-top: 2px solid var(--color-forest); }
.source-preview + .source-preview { margin-top: 24px; }
.source-preview header { display: flex; align-items: start; justify-content: space-between; gap: 20px; }
.source-preview header p { margin-bottom: 4px; color: var(--color-muted); font-size: var(--type-meta); letter-spacing: .1em; text-transform: uppercase; }
.source-preview h2 { margin-bottom: 0; font-size: 26px; }
.source-preview header > strong { text-align: right; font-family: var(--font-editorial); font-size: 24px; font-weight: 500; }
.source-preview header > strong small { display: block; margin-top: 4px; color: var(--color-muted); font-family: var(--font-interface); font-size: var(--type-meta); font-weight: 400; }
.source-preview header > strong.failed { color: var(--color-danger); font-family: var(--font-interface); font-size: 13px; }
.source-preview dl { display: grid; grid-template-columns: repeat(3, 1fr); margin: 20px 0; padding: 14px 0; border-block: 1px solid var(--color-line); }
.source-preview dl div { text-align: center; }
.source-preview dt { color: var(--color-muted); font-size: var(--type-meta); }
.source-preview dd { margin: 4px 0 0; font-family: var(--font-editorial); font-size: 20px; }
.sample-list { margin: 0; padding: 0; list-style: none; }
.sample-list li { padding: 14px 0; border-bottom: 1px solid var(--color-line); }
.sample-list span { color: var(--color-muted); font-size: var(--type-meta); }
.sample-list h3 { margin: 5px 0 0; font-size: 17px; line-height: 1.4; }
.warnings { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 16px; color: var(--color-muted); font-size: var(--type-meta); }
.source-error { color: var(--color-danger); }
.final-actions { flex-wrap: wrap; }

@media (max-width: 720px) {
  .step-rail li { font-size: 0; }
  .step-rail li span { font-size: var(--type-meta); }
  .preference-context-heading { align-items: stretch; flex-direction: column; gap: 14px; }
  .agent-selection-heading { align-items: stretch; flex-direction: column; gap: 8px; }
  .agent-selection-summary { text-align: left; }
  .agent-selection-grid { grid-template-columns: 1fr; }
  .context-toggle { align-self: flex-start; }
  .preference-term-list { grid-template-columns: 1fr; }
  .term-grid { grid-template-columns: 1fr; }
  .month-range { grid-template-columns: 1fr; align-items: stretch; }
  .month-range span { display: none; }
  .source-filters, .schedule-grid { grid-template-columns: 1fr; }
  .source-filters { margin-left: 0; }
  .actions, .split-actions { align-items: stretch; flex-direction: column-reverse; }
  .actions button { width: 100%; }
}
</style>
