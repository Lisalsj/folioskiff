import { messageRef, researchText as t } from '@/stores/locale'
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

import type { components } from '@/api/schema'
import type { PdfTextSelection } from '@/lib/annotation-geometry'
import { ApiResponseError, apiJson } from '@/lib/json-api'
import { useJobStore, type JobSummary } from '@/stores/jobs'
import { usePreferencesStore } from '@/stores/preferences'
import {
  createAssistantTask,
  getAssistantFragments,
  getAssistantTask,
  getAssistantWork,
  newAssistantIdempotencyKey,
  retryAssistantTask,
  type AssistantCapability,
  type AssistantFragment,
  type AssistantMode,
  type AssistantTaskCommand,
  type AssistantTaskSummary,
  type AssistantWork,
  type ContextScope,
  type ReasoningEffort,
} from './api'

type AssistantModeChoice = Extract<AssistantMode, 'ask' | 'explain'>
type OutputLanguage = AssistantTaskCommand['output_language']
type ContextSelection = AssistantTaskCommand['context']

export interface AssistantReaderAnnotation {
  id: string
  pageNumber: number
  label: string
}

export interface AssistantReaderContext {
  currentPage: number
  pageCount: number
  selection: PdfTextSelection | null
  annotations: AssistantReaderAnnotation[]
}

export interface AssistantIdentity {
  workId: string
  documentVersionId: string
  documentContentSha256: string
}

function draftKey(workId: string): string {
  return `paper-reader.assistant-draft.${workId}`
}

function readDraft(workId: string): string {
  try {
    return window.sessionStorage.getItem(draftKey(workId)) ?? ''
  } catch {
    return ''
  }
}

function persistDraft(workId: string, value: string): void {
  if (!workId) return
  try {
    if (value) window.sessionStorage.setItem(draftKey(workId), value)
    else window.sessionStorage.removeItem(draftKey(workId))
  } catch {
    // A private browsing policy must not block an in-memory draft.
  }
}

function summaryFromJob(job: components['schemas']['AssistantJobResponse']): JobSummary {
  return {
    id: job.id,
    kind: job.kind,
    status: job.status as JobSummary['status'],
    progress: job.progress,
    cancel_requested: job.cancel_requested,
    pause_requested: false,
  }
}

function selectionContext(selection: PdfTextSelection): ContextSelection['selection'] {
  return {
    page_number: selection.pageNumber,
    page_width: selection.pageWidth,
    page_height: selection.pageHeight,
    quote_text: selection.quoteText,
    context_before: selection.contextBefore,
    context_after: selection.contextAfter,
    rects: selection.rects,
  }
}

export const useAssistantStore = defineStore('paper-assistant', () => {
  const jobs = useJobStore()
  const preferences = usePreferencesStore()
  const identity = ref<AssistantIdentity>()
  const work = ref<AssistantWork>()
  const capabilities = ref<AssistantCapability[]>([])
  const loading = ref(false)
  const submitting = ref(false)
  const loadError = messageRef(t)
  const statusMessage = messageRef(t)
  const draft = ref('')
  const mode = ref<AssistantModeChoice>('ask')
  const scope = ref<ContextScope>('current_page')
  const selectedAnnotationIds = ref<string[]>([])
  const backendId = ref('')
  const modelId = ref('')
  const reasoningEffort = ref<ReasoningEffort>('medium')
  const outputLanguage = ref<OutputLanguage>('zh-CN')
  const activeTaskId = ref('')
  const activeJobId = ref('')
  const fragments = ref<AssistantFragment[]>([])

  let loadGeneration = 0
  let fragmentFrame: number | undefined
  let fallbackTimer: number | undefined
  let fragmentRequest: { taskId: string, token: symbol } | undefined
  let terminalRefresh: { taskId: string, token: symbol } | undefined
  let pendingFragments: AssistantFragment[] = []

  const tasks = computed(() => work.value?.tasks ?? [])
  const messages = computed(() => work.value?.messages ?? [])
  const latestCheckpoint = computed(() => work.value?.latest_checkpoint ?? null)
  const selectedCapability = computed(() => capabilities.value.find(
    (capability) => capability.backend_id === backendId.value,
  ))
  const activeTask = computed(() => tasks.value.find((task) => task.id === activeTaskId.value))
  const activeJob = computed(() => activeJobId.value ? jobs.find(activeJobId.value) : undefined)
  const active = computed(() => {
    if (activeJob.value) return ['queued', 'running'].includes(activeJob.value.status)
    return Boolean(activeTask.value && ['queued', 'running'].includes(activeTask.value.status))
  })
  const latestTask = computed(() => tasks.value.at(-1))
  const latestContext = computed(() => latestTask.value?.context ?? null)
  const backendReady = computed(() => {
    const capability = selectedCapability.value
    return Boolean(
      capability?.available
      && (modelId.value.trim() || capability.supports_default_model),
    )
  })

  async function initialize(next: AssistantIdentity): Promise<void> {
    const generation = ++loadGeneration
    stopFallbackPolling()
    cancelFragmentFrame()
    fragmentRequest = undefined
    terminalRefresh = undefined
    identity.value = next
    work.value = undefined
    fragments.value = []
    activeTaskId.value = ''
    activeJobId.value = ''
    selectedAnnotationIds.value = []
    mode.value = 'ask'
    scope.value = 'current_page'
    draft.value = readDraft(next.workId)
    loading.value = true
    submitting.value = false
    loadError.value = ''
    statusMessage.value = ''

    const [workResult, capabilityResult] = await Promise.allSettled([
      getAssistantWork(next.workId),
      apiJson<AssistantCapability[]>('/api/v1/settings/agents'),
      preferences.snapshot ? Promise.resolve(true) : preferences.load(),
    ])
    if (generation !== loadGeneration) return

    if (workResult.status === 'fulfilled') {
      work.value = workResult.value
    } else {
      loadError.value = '暂时无法读取这篇论文的 Assistant 历史。'
    }
    if (capabilityResult.status === 'fulfilled') {
      capabilities.value = capabilityResult.value
    } else {
      capabilities.value = []
      loadError.value ||= t('暂时无法读取 Agent 后端；已有回答仍可查看。')
    }
    chooseAgentDefaults()
    recoverActiveTask()
    loading.value = false

    if (activeJobId.value) {
      await jobs.refresh()
      if (generation !== loadGeneration) return
      await syncFragments()
      manageFallbackPolling()
    }
  }

  function chooseAgentDefaults(): void {
    const snapshot = preferences.snapshot
    const preferred = capabilities.value.find((item) => (
      item.backend_id === snapshot?.default_agent_backend && item.available
    )) ?? capabilities.value.find((item) => item.backend_id === 'codex_cli' && item.available)
      ?? capabilities.value.find((item) => item.available)
    backendId.value = preferred?.backend_id ?? ''
    reasoningEffort.value = snapshot?.default_reasoning_effort ?? 'medium'
    outputLanguage.value = snapshot?.report_language ?? 'zh-CN'
    const preferredModel = snapshot && preferred?.backend_id === snapshot.default_agent_backend
      ? snapshot.default_agent_model_id
      : null
    modelId.value = preferredModel ?? preferred?.models[0]?.model_id ?? ''
  }

  function recoverActiveTask(): void {
    const pending = [...tasks.value].reverse().find((task) => (
      task.status === 'queued' || task.status === 'running'
    ))
    if (!pending) return
    activeTaskId.value = pending.id
    activeJobId.value = pending.job_id
    mode.value = pending.mode === 'explain' ? 'explain' : 'ask'
    scope.value = pending.context.scope
  }

  function setDraft(value: string): void {
    draft.value = value.slice(0, 20_000)
    persistDraft(identity.value?.workId ?? '', draft.value)
  }

  function selectBackend(value: string): void {
    backendId.value = value
    const capability = selectedCapability.value
    modelId.value = capability?.models[0]?.model_id ?? ''
  }

  function hasUsableContext(reader: AssistantReaderContext): boolean {
    if (scope.value === 'selection') return Boolean(reader.selection)
    if (scope.value === 'annotations') return selectedAnnotationIds.value.length > 0
    return reader.currentPage >= 1 && reader.pageCount >= reader.currentPage
  }

  function canSend(reader: AssistantReaderContext): boolean {
    return Boolean(
      draft.value.trim()
      && backendReady.value
      && hasUsableContext(reader)
      && !loading.value
      && !submitting.value
      && !active.value,
    )
  }

  async function send(reader: AssistantReaderContext): Promise<boolean> {
    const currentIdentity = identity.value
    if (!currentIdentity || !canSend(reader)) return false
    const generation = loadGeneration
    const message = draft.value.trim()
    const command: AssistantTaskCommand = {
      mode: mode.value,
      message,
      context: buildContext(currentIdentity.documentVersionId, reader),
      agent: {
        backend_id: backendId.value,
        model_id: modelId.value.trim() || null,
        reasoning_effort: reasoningEffort.value,
        service_tier: 'flex',
      },
      network_policy: 'local_only',
      output_language: outputLanguage.value,
    }
    submitting.value = true
    statusMessage.value = ''
    try {
      const job = await createAssistantTask(
        currentIdentity.workId,
        command,
        newAssistantIdempotencyKey(),
      )
      jobs.upsert(summaryFromJob(job))
      if (generation !== loadGeneration) {
        persistDraft(currentIdentity.workId, '')
        return true
      }
      activeTaskId.value = job.task_id
      activeJobId.value = job.id
      fragments.value = []
      setDraft('')
      statusMessage.value = '问题与证据范围已保存，Agent 正在处理。'
      await refreshHistory()
      if (generation === loadGeneration) manageFallbackPolling()
      return true
    } catch (error) {
      if (generation === loadGeneration) {
        statusMessage.value = error instanceof ApiResponseError && error.status === 409
          ? '当前论文已有冲突任务，或所选证据已变化。请刷新后重试。'
          : '任务未能保存，Agent 没有开始运行。'
      }
      return false
    } finally {
      if (generation === loadGeneration) submitting.value = false
    }
  }

  function buildContext(
    documentVersionId: string,
    reader: AssistantReaderContext,
  ): ContextSelection {
    if (scope.value === 'selection') {
      if (!reader.selection) throw new Error('Selection context is unavailable')
      return {
        document_version_id: documentVersionId,
        scope: 'selection',
        selection: selectionContext(reader.selection),
        annotation_ids: [],
      }
    }
    if (scope.value === 'current_page' || scope.value === 'current_section') {
      return {
        document_version_id: documentVersionId,
        scope: scope.value,
        page_number: reader.currentPage,
        annotation_ids: [],
      }
    }
    if (scope.value === 'annotations') {
      return {
        document_version_id: documentVersionId,
        scope: 'annotations',
        annotation_ids: [...selectedAnnotationIds.value].sort(),
      }
    }
    return {
      document_version_id: documentVersionId,
      scope: 'full_document',
      annotation_ids: [],
    }
  }

  async function retry(taskId: string): Promise<boolean> {
    if (submitting.value || active.value) return false
    const generation = loadGeneration
    submitting.value = true
    statusMessage.value = ''
    try {
      const job = await retryAssistantTask(taskId, newAssistantIdempotencyKey())
      jobs.upsert(summaryFromJob(job))
      if (generation !== loadGeneration) return true
      activeTaskId.value = job.task_id
      activeJobId.value = job.id
      fragments.value = []
      statusMessage.value = '已按原证据快照重新排队。'
      await refreshHistory()
      if (generation === loadGeneration) manageFallbackPolling()
      return true
    } catch {
      if (generation === loadGeneration) {
        statusMessage.value = '重试未能建立，原任务和历史没有改变。'
      }
      return false
    } finally {
      if (generation === loadGeneration) submitting.value = false
    }
  }

  async function cancel(): Promise<void> {
    if (!activeJobId.value || activeJob.value?.cancel_requested) return
    statusMessage.value = '正在请求取消，已保存的消息与片段不会删除。'
    await jobs.requestCancel(activeJobId.value)
  }

  async function refreshHistory(): Promise<void> {
    const current = identity.value
    if (!current) return
    const generation = loadGeneration
    try {
      const nextWork = await getAssistantWork(current.workId)
      if (generation === loadGeneration && identity.value?.workId === current.workId) {
        work.value = nextWork
      }
    } catch {
      if (generation === loadGeneration) {
        loadError.value = '任务仍会继续，但本地历史暂时无法刷新。'
      }
    }
  }

  async function syncFragments(): Promise<void> {
    const taskId = activeTaskId.value
    const generation = loadGeneration
    if (!taskId || fragmentRequest?.taskId === taskId) return
    const token = Symbol(taskId)
    fragmentRequest = { taskId, token }
    const lastSequence = Math.max(-1, ...fragments.value.map((item) => item.sequence))
    try {
      const received = await getAssistantFragments(taskId, lastSequence)
      if (generation === loadGeneration && activeTaskId.value === taskId) {
        enqueueFragments(received)
      }
    } catch {
      // Durable task history remains authoritative if a transient fragment read fails.
    } finally {
      if (fragmentRequest?.token === token) fragmentRequest = undefined
    }
  }

  function enqueueFragments(received: AssistantFragment[]): void {
    if (!received.length) return
    const known = new Set([
      ...fragments.value.map((item) => item.sequence),
      ...pendingFragments.map((item) => item.sequence),
    ])
    pendingFragments.push(...received.filter((item) => !known.has(item.sequence)))
    if (fragmentFrame !== undefined) return
    fragmentFrame = scheduleFrame(() => {
      fragmentFrame = undefined
      fragments.value = [...fragments.value, ...pendingFragments]
        .sort((left, right) => left.sequence - right.sequence)
      pendingFragments = []
    })
  }

  async function handleJobSignal(): Promise<void> {
    const taskId = activeTaskId.value
    const jobId = activeJobId.value
    const generation = loadGeneration
    if (!taskId || !jobId) return
    await syncFragments()
    if (
      generation !== loadGeneration
      || activeTaskId.value !== taskId
      || activeJobId.value !== jobId
    ) return
    const job = activeJob.value
    if (!job || job.status === 'queued' || job.status === 'running') {
      manageFallbackPolling()
      return
    }
    if (terminalRefresh?.taskId === taskId) return
    const token = Symbol(taskId)
    terminalRefresh = { taskId, token }
    try {
      await getAssistantTask(taskId).catch(() => undefined)
      await refreshHistory()
      if (
        generation !== loadGeneration
        || activeTaskId.value !== taskId
        || activeJobId.value !== jobId
      ) return
      statusMessage.value = job.status === 'succeeded'
        ? '回答已保存，引用可以直接返回 PDF。'
        : job.status === 'cancelled'
          ? '任务已取消，已有历史没有改变。'
          : '任务失败，诊断已保存，可按原证据快照重试。'
      activeTaskId.value = ''
      activeJobId.value = ''
      stopFallbackPolling()
    } finally {
      if (terminalRefresh?.token === token) terminalRefresh = undefined
    }
  }

  function manageFallbackPolling(): void {
    if (!activeJobId.value || jobs.connection === 'connected') {
      stopFallbackPolling()
      return
    }
    if (fallbackTimer !== undefined) return
    fallbackTimer = window.setInterval(() => {
      void jobs.refresh().then(() => handleJobSignal())
    }, 1_500)
  }

  function stopFallbackPolling(): void {
    if (fallbackTimer === undefined) return
    window.clearInterval(fallbackTimer)
    fallbackTimer = undefined
  }

  function cancelFragmentFrame(): void {
    if (fragmentFrame !== undefined) cancelFrame(fragmentFrame)
    fragmentFrame = undefined
    pendingFragments = []
  }

  function dispose(): void {
    loadGeneration += 1
    stopFallbackPolling()
    cancelFragmentFrame()
    fragmentRequest = undefined
    terminalRefresh = undefined
    submitting.value = false
    activeTaskId.value = ''
    activeJobId.value = ''
  }

  watch(
    () => jobs.eventRevision,
    () => {
      if (jobs.lastEventType.startsWith('assistant.')) void handleJobSignal()
    },
  )
  watch(
    () => `${activeJob.value?.id ?? ''}:${activeJob.value?.status ?? ''}:${activeJob.value?.progress ?? ''}`,
    () => void handleJobSignal(),
  )
  watch(
    () => jobs.connection,
    manageFallbackPolling,
  )

  return {
    active,
    activeJob,
    activeJobId,
    activeTaskId,
    backendId,
    backendReady,
    buildContext,
    capabilities,
    canSend,
    cancel,
    dispose,
    draft,
    fragments,
    initialize,
    latestCheckpoint,
    latestContext,
    latestTask,
    loadError,
    loading,
    messages,
    mode,
    modelId,
    outputLanguage,
    reasoningEffort,
    retry,
    scope,
    selectBackend,
    selectedAnnotationIds,
    selectedCapability,
    send,
    setDraft,
    statusMessage,
    submitting,
    tasks,
  }
})

function scheduleFrame(callback: FrameRequestCallback): number {
  if (typeof window.requestAnimationFrame === 'function') {
    return window.requestAnimationFrame(callback)
  }
  return window.setTimeout(() => callback(performance.now()), 16)
}

function cancelFrame(handle: number): void {
  if (typeof window.cancelAnimationFrame === 'function') {
    window.cancelAnimationFrame(handle)
  } else {
    window.clearTimeout(handle)
  }
}
