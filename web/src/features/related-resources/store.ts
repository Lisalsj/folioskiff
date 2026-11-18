import { messageRef, messageTemplate, researchText as t } from '@/stores/locale'
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

import { useJobStore, type JobSummary } from '@/stores/jobs'
import {
  confirmRelatedIntent,
  draftRelatedIntent,
  getRelatedResourceSet,
  newRelatedIdempotencyKey,
  refineRelatedIntent,
  reviseRelatedIntent,
  runRelatedSearch,
  saveRelatedPaper,
  type AgentSelection,
  type ContextSelection,
  type RelatedResourceSet,
  type RelatedSearchIntent,
} from './api'

export type RelatedPhase = 'idle' | 'draft' | 'confirm' | 'running' | 'results'
export type RelatedResultTab = 'papers' | 'knowledge'

export interface RelatedIdentity {
  workId: string
  documentVersionId: string
  documentContentSha256: string
}

const EXTERNAL_WEB_KEY = 'paper-reader.related.external-web-enabled'
const TERMINAL = new Set(['partial', 'succeeded', 'failed', 'cancelled'])

function pointerKey(identity: RelatedIdentity): string {
  return `paper-reader.related.last.${identity.workId}.${identity.documentVersionId}.${identity.documentContentSha256}`
}

function readPointer(identity: RelatedIdentity): string {
  try {
    const value = window.localStorage.getItem(pointerKey(identity)) ?? ''
    return /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(value) ? value : ''
  } catch {
    return ''
  }
}

function writePointer(identity: RelatedIdentity, setId: string): void {
  try {
    window.localStorage.setItem(pointerKey(identity), setId)
  } catch {
    // Recovery metadata is optional; durable server state remains authoritative.
  }
}

function clearPointer(identity: RelatedIdentity): void {
  try {
    window.localStorage.removeItem(pointerKey(identity))
  } catch {
    // A restrictive storage policy must not block resetting the visible flow.
  }
}

function intentContent(value: RelatedSearchIntent): string {
  return JSON.stringify({
    summary: value.summary,
    required_groups: value.required_groups,
    optional_terms: value.optional_terms,
    excluded_terms: value.excluded_terms,
    recent_months: value.recent_months,
    source_ids: value.source_ids,
  })
}

function rememberedExternalWeb(): boolean {
  try {
    return window.localStorage.getItem(EXTERNAL_WEB_KEY) === 'true'
  } catch {
    return false
  }
}

function rememberExternalWeb(): void {
  try {
    window.localStorage.setItem(EXTERNAL_WEB_KEY, 'true')
  } catch {
    // Storage policy must not block an explicitly confirmed run.
  }
}

function runJobSummary(start: {
  job_id: string
  status: string
  progress: number
}): JobSummary {
  return {
    id: start.job_id,
    kind: 'related.search',
    status: start.status as JobSummary['status'],
    progress: start.progress,
    cancel_requested: false,
    pause_requested: false,
  }
}

export const useRelatedResourcesStore = defineStore('related-resources', () => {
  const jobs = useJobStore()
  const identity = ref<RelatedIdentity>()
  const phase = ref<RelatedPhase>('idle')
  const resultTab = ref<RelatedResultTab>('papers')
  const intent = ref<RelatedSearchIntent>()
  const persistedIntent = ref<RelatedSearchIntent>()
  const resourceSet = ref<RelatedResourceSet>()
  const loading = ref(false)
  const refining = ref(false)
  const confirming = ref(false)
  const starting = ref(false)
  const savingId = ref('')
  const errorMessage = messageRef(t)
  const statusMessage = messageRef(t)
  const includeAcademicSources = ref(true)
  const includeExternalWeb = ref(false)
  const externalWebConsent = ref(false)
  const activeJobId = ref('')
  const lastSetId = ref('')

  let generation = 0
  let fallbackTimer: number | undefined

  const activeJob = computed(() => activeJobId.value ? jobs.find(activeJobId.value) : undefined)
  const active = computed(() => (
    phase.value === 'running'
    && (!activeJob.value || ['queued', 'running'].includes(activeJob.value.status))
  ))
  const canRun = computed(() => Boolean(
    phase.value === 'confirm'
    && intent.value?.confirmed
    && !starting.value
    && (includeAcademicSources.value || includeExternalWeb.value)
    && (!includeExternalWeb.value || externalWebConsent.value),
  ))
  const canConfirm = computed(() => {
    const value = intent.value
    return Boolean(
      phase.value === 'draft'
      && value
      && value.summary.trim()
      && value.required_groups.length
      && value.required_groups.every((group) => (
        group.length > 0 && group.every((term) => term.trim())
      ))
      && value.optional_terms.every((term) => term.trim())
      && value.excluded_terms.every((term) => term.trim())
      && value.source_ids.length
      && Number.isInteger(value.recent_months)
      && value.recent_months >= 1
      && value.recent_months <= 120
      && !refining.value
      && !confirming.value
    )
  })
  const dirty = computed(() => Boolean(
    intent.value
    && persistedIntent.value
    && intentContent(intent.value) !== intentContent(persistedIntent.value),
  ))
  const resumeAvailable = computed(() => phase.value === 'idle' && Boolean(lastSetId.value))

  function initialize(next: RelatedIdentity): void {
    generation += 1
    stopPolling()
    identity.value = next
    phase.value = 'idle'
    resultTab.value = 'papers'
    intent.value = undefined
    persistedIntent.value = undefined
    resourceSet.value = undefined
    loading.value = false
    refining.value = false
    confirming.value = false
    starting.value = false
    savingId.value = ''
    errorMessage.value = ''
    statusMessage.value = ''
    includeAcademicSources.value = true
    includeExternalWeb.value = rememberedExternalWeb()
    externalWebConsent.value = false
    activeJobId.value = ''
    lastSetId.value = readPointer(next)
  }

  async function beginDraft(context: ContextSelection): Promise<boolean> {
    const current = identity.value
    if (!current || loading.value || phase.value !== 'idle') return false
    const currentGeneration = generation
    loading.value = true
    errorMessage.value = ''
    statusMessage.value = '正在从当前阅读范围整理本地检索词…'
    try {
      const created = await draftRelatedIntent(current.workId, context)
      if (currentGeneration !== generation) return true
      intent.value = created
      persistedIntent.value = created
      if (created.set_id) {
        lastSetId.value = created.set_id
        writePointer(current, created.set_id)
      }
      phase.value = 'draft'
      statusMessage.value = '本地草稿已准备；网络检索尚未开始。'
      return true
    } catch {
      if (currentGeneration === generation) {
        errorMessage.value = '无法冻结当前阅读范围。请确认正文已提取，再重试。'
        statusMessage.value = ''
      }
      return false
    } finally {
      if (currentGeneration === generation) loading.value = false
    }
  }

  function editIntent(next: RelatedSearchIntent): void {
    if (!intent.value || !['draft', 'confirm'].includes(phase.value)) return
    intent.value = {
      ...next,
      set_id: intent.value.set_id,
      revision: intent.value.revision,
      confirmed: false,
      confirmed_at: null,
    }
    phase.value = 'draft'
    statusMessage.value = '检索词已修改，需要重新确认后才能运行。'
  }

  async function refine(instruction: string, agent: AgentSelection): Promise<boolean> {
    let current = intent.value
    if (!current?.set_id || refining.value || phase.value === 'running') return false
    const setId = current.set_id
    const currentGeneration = generation
    refining.value = true
    errorMessage.value = ''
    try {
      if (dirty.value) {
        current = await reviseRelatedIntent(setId, current.revision, current)
        if (currentGeneration !== generation) return true
        intent.value = current
        persistedIntent.value = current
      }
      const refined = await refineRelatedIntent(setId, instruction, agent)
      if (currentGeneration !== generation) return true
      intent.value = refined
      persistedIntent.value = refined
      phase.value = 'draft'
      statusMessage.value = 'Agent 建议已生成并显示差异；请人工确认。'
      return true
    } catch {
      if (currentGeneration === generation) {
        errorMessage.value = 'Agent 没有生成可验证的检索词；本地草稿保持不变。'
      }
      return false
    } finally {
      if (currentGeneration === generation) refining.value = false
    }
  }

  async function confirmIntent(): Promise<boolean> {
    const current = intent.value
    if (!current?.set_id || !canConfirm.value) return false
    const currentGeneration = generation
    confirming.value = true
    errorMessage.value = ''
    try {
      const confirmed = await confirmRelatedIntent(current.set_id, current.revision, current)
      if (currentGeneration !== generation) return true
      intent.value = confirmed
      persistedIntent.value = confirmed
      phase.value = confirmed.confirmed ? 'confirm' : 'draft'
      statusMessage.value = confirmed.confirmed
        ? '检索意图已确认；点击运行后才会访问所选来源。'
        : '修改已保存。请再次核对这一新版本并确认。'
      return confirmed.confirmed
    } catch {
      if (currentGeneration === generation) {
        errorMessage.value = '确认失败，可能已有更新版本；本次没有开始检索。'
      }
      return false
    } finally {
      if (currentGeneration === generation) confirming.value = false
    }
  }

  async function run(agent: AgentSelection | null): Promise<boolean> {
    const current = intent.value
    if (!current?.set_id || !canRun.value) return false
    if (includeExternalWeb.value && !agent) return false
    const currentGeneration = generation
    starting.value = true
    errorMessage.value = ''
    try {
      const started = await runRelatedSearch(
        current.set_id,
        {
          intent_revision: current.revision,
          include_academic_sources: includeAcademicSources.value,
          include_external_web: includeExternalWeb.value,
          external_web_consent: includeExternalWeb.value && externalWebConsent.value,
          agent: includeExternalWeb.value ? agent : null,
        },
        newRelatedIdempotencyKey(),
      )
      jobs.upsert(runJobSummary(started))
      if (currentGeneration !== generation) return true
      if (includeExternalWeb.value && externalWebConsent.value) rememberExternalWeb()
      activeJobId.value = started.job_id
      phase.value = 'running'
      statusMessage.value = '检索已开始；学术来源与外部知识资源会分开显示。'
      managePolling()
      return true
    } catch {
      if (currentGeneration === generation) {
        errorMessage.value = '检索任务未能建立；已确认的检索词没有改变。'
      }
      return false
    } finally {
      if (currentGeneration === generation) starting.value = false
    }
  }

  async function refreshResults(): Promise<boolean> {
    const setId = intent.value?.set_id
    if (!setId) return false
    const currentGeneration = generation
    try {
      const next = await getRelatedResourceSet(setId)
      if (currentGeneration !== generation) return true
      resourceSet.value = next
      intent.value = next.intent
      persistedIntent.value = next.intent
      if (TERMINAL.has(next.status)) {
        phase.value = 'results'
        activeJobId.value = ''
        stopPolling()
        statusMessage.value = next.status === 'partial'
          ? messageTemplate('部分来源完成，当前数据覆盖 {0}%。', Math.round(next.coverage))
          : next.status === 'succeeded'
            ? '关联研究已完成。'
            : next.status === 'cancelled'
              ? '检索已取消，已核验结果仍然保留。'
              : '检索失败；已完成来源的结果仍然保留。'
      } else if (['queued', 'running'].includes(next.status)) {
        phase.value = 'running'
      }
      return true
    } catch {
      if (currentGeneration === generation) {
        errorMessage.value = '任务仍可能继续，但结果暂时无法刷新。'
      }
      return false
    }
  }

  async function resume(): Promise<boolean> {
    const current = identity.value
    const setId = lastSetId.value
    if (!current || !setId || phase.value !== 'idle' || loading.value) return false
    const currentGeneration = generation
    loading.value = true
    errorMessage.value = ''
    try {
      const next = await getRelatedResourceSet(setId)
      if (currentGeneration !== generation) return true
      if (
        next.work_id !== current.workId
        || next.document_version_id !== current.documentVersionId
        || next.document_content_sha256 !== current.documentContentSha256
      ) {
        clearPointer(current)
        lastSetId.value = ''
        errorMessage.value = '上次关联研究属于另一论文版本，未自动载入。'
        return false
      }
      resourceSet.value = next
      intent.value = next.intent
      persistedIntent.value = next.intent
      includeAcademicSources.value = next.include_academic_sources
      includeExternalWeb.value = next.include_external_web
      externalWebConsent.value = ['queued', 'running'].includes(next.status)
        && next.external_web_consent
      if (TERMINAL.has(next.status)) {
        phase.value = 'results'
        statusMessage.value = '已恢复上次关联研究结果。'
      } else if (next.status === 'confirmed') {
        phase.value = 'confirm'
        statusMessage.value = '已恢复上次确认的检索意图；尚未开始检索。'
      } else if (next.status === 'queued' || next.status === 'running') {
        phase.value = 'running'
        activeJobId.value = next.job_id ?? ''
        await jobs.refresh()
        managePolling()
        statusMessage.value = '已重新连接上次关联研究任务。'
      } else {
        phase.value = 'draft'
        statusMessage.value = '已恢复上次未确认的检索词。'
      }
      return true
    } catch {
      if (currentGeneration === generation) {
        clearPointer(current)
        lastSetId.value = ''
        errorMessage.value = '上次关联研究无法恢复；可以重新准备检索词。'
      }
      return false
    } finally {
      if (currentGeneration === generation) loading.value = false
    }
  }

  async function savePaper(candidateId: string): Promise<boolean> {
    const setId = intent.value?.set_id
    if (!setId || savingId.value) return false
    savingId.value = candidateId
    errorMessage.value = ''
    try {
      const saved = await saveRelatedPaper(setId, candidateId, newRelatedIdempotencyKey())
      if (saved.saved && resourceSet.value) {
        resourceSet.value = {
          ...resourceSet.value,
          papers: resourceSet.value.papers.map((paper) => paper.id === candidateId
            ? { ...paper, saved: true }
            : paper),
        }
      }
      statusMessage.value = saved.saved
        ? '已保存到我的论文库。'
        : '该候选需要先完成人工合并审阅。'
      return saved.saved
    } catch {
      errorMessage.value = '暂时无法保存这篇论文；结果本身没有改变。'
      return false
    } finally {
      savingId.value = ''
    }
  }

  async function cancel(): Promise<void> {
    if (!activeJobId.value || activeJob.value?.cancel_requested) return
    statusMessage.value = '正在请求取消；已核验结果不会删除。'
    await jobs.requestCancel(activeJobId.value)
  }

  function reset(): void {
    const current = identity.value
    if (current) {
      clearPointer(current)
      initialize(current)
    }
  }

  async function handleJobSignal(): Promise<void> {
    if (phase.value !== 'running') return
    const job = activeJob.value
    if (!job || !['queued', 'running'].includes(job.status)) await refreshResults()
  }

  function managePolling(): void {
    if (phase.value !== 'running' || jobs.connection === 'connected') {
      stopPolling()
      return
    }
    if (fallbackTimer !== undefined) return
    fallbackTimer = window.setInterval(() => {
      void jobs.refresh().then(handleJobSignal)
    }, 2_000)
  }

  function stopPolling(): void {
    if (fallbackTimer === undefined) return
    window.clearInterval(fallbackTimer)
    fallbackTimer = undefined
  }

  function dispose(): void {
    generation += 1
    stopPolling()
  }

  watch(
    () => jobs.eventRevision,
    () => {
      if (
        jobs.lastEventType.startsWith('job.')
        || jobs.lastEventType.startsWith('related.')
      ) void handleJobSignal()
    },
  )
  watch(
    () => `${activeJob.value?.id ?? ''}:${activeJob.value?.status ?? ''}:${activeJob.value?.progress ?? ''}`,
    () => void handleJobSignal(),
  )
  watch(() => jobs.connection, managePolling)

  return {
    active,
    activeJob,
    activeJobId,
    beginDraft,
    canConfirm,
    canRun,
    cancel,
    confirmIntent,
    confirming,
    dispose,
    dirty,
    editIntent,
    errorMessage,
    externalWebConsent,
    identity,
    includeAcademicSources,
    includeExternalWeb,
    initialize,
    intent,
    loading,
    phase,
    refine,
    refining,
    refreshResults,
    reset,
    resourceSet,
    resultTab,
    resume,
    resumeAvailable,
    run,
    savePaper,
    savingId,
    starting,
    statusMessage,
  }
})
