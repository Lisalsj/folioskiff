import { messageRef, readerText as t } from '@/stores/locale'
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

import { ApiResponseError } from '@/lib/json-api'
import { useJobStore, type JobSummary } from '@/stores/jobs'
import {
  createTranslation,
  getTranslation,
  getTranslationAgentMetadata,
  listTranslationSegments,
  newTranslationIdempotencyKey,
  resumeTranslation,
  updateTranslationTerm,
  type TranslationAgentMetadata,
  type TranslationArtifact,
  type TranslationCommand,
  type TranslationJob,
  type TranslationSegment,
} from './api'

interface RememberedTranslation {
  artifactId: string
  workId: string
  documentVersionId: string
}

function rememberedKey(workId: string): string {
  return `paper-reader.translation-active.${workId}`
}

function remember(artifact: TranslationArtifact): void {
  try {
    const value: RememberedTranslation = {
      artifactId: artifact.id,
      workId: artifact.work_id,
      documentVersionId: artifact.document_version_id,
    }
    window.sessionStorage.setItem(rememberedKey(artifact.work_id), JSON.stringify(value))
  } catch {
    // Storage restrictions must not block the active in-memory translation.
  }
}

function recall(workId: string): RememberedTranslation | undefined {
  try {
    const raw = window.sessionStorage.getItem(rememberedKey(workId))
    if (!raw) return undefined
    const value = JSON.parse(raw) as Partial<RememberedTranslation>
    if (
      value.workId === workId
      && typeof value.artifactId === 'string'
      && typeof value.documentVersionId === 'string'
    ) return value as RememberedTranslation
  } catch {
    // Ignore malformed or unavailable session storage.
  }
  return undefined
}

function forget(workId: string): void {
  try {
    window.sessionStorage.removeItem(rememberedKey(workId))
  } catch {
    // The in-memory state can still be cleared.
  }
}

function summaryFromJob(job: TranslationJob): JobSummary {
  return {
    id: job.job_id,
    kind: job.kind,
    status: job.status,
    progress: job.progress,
    cancel_requested: job.cancel_requested,
    pause_requested: false,
  }
}

function readableError(error: unknown, fallback: string): string {
  if (error instanceof ApiResponseError && typeof error.detail === 'string') return error.detail
  return fallback
}

export const useTranslationStore = defineStore('translations', () => {
  const jobs = useJobStore()
  const artifact = ref<TranslationArtifact>()
  const segments = ref<TranslationSegment[]>([])
  const submittedJob = ref<TranslationJob>()
  const agentMetadata = ref<TranslationAgentMetadata>()
  const loading = ref(false)
  const submitting = ref(false)
  const savingTermId = ref('')
  const error = messageRef(t)
  const statusMessage = messageRef(t)
  let generation = 0

  const trackedJob = computed(() => {
    const jobId = submittedJob.value?.job_id ?? artifact.value?.job_id
    return jobId ? jobs.find(jobId) : undefined
  })
  const active = computed(() => {
    const status = trackedJob.value?.status ?? submittedJob.value?.status ?? artifact.value?.status
    return status === 'queued' || status === 'running'
  })
  const progress = computed(() => {
    const total = segments.value.length
    const completed = segments.value.filter((segment) => segment.status === 'succeeded').length
    const observed = trackedJob.value?.progress ?? submittedJob.value?.progress
    const percent = total > 0
      ? Math.round((completed / total) * 100)
      : Math.max(0, Math.min(100, observed ?? 0))
    return { completed, total, percent }
  })
  const pageRange = computed(() => {
    const pages = segments.value.map((segment) => segment.page_number)
    if (pages.length === 0) return t('尚无可用页')
    const first = Math.min(...pages)
    const last = Math.max(...pages)
    return first === last ? t('第 {0} 页', first) : t('第 {0} 至 {1} 页', first, last)
  })
  const diagnostics = computed(() => {
    const values = segments.value
      .filter((segment) => segment.diagnostic)
      .map((segment) => t('第 {0} 页：{1}', segment.page_number, segment.diagnostic))
    if (submittedJob.value?.error_message) values.push(submittedJob.value.error_message)
    return [...new Set(values)]
  })
  const agentLabel = computed(() => {
    if (!agentMetadata.value) return t('Agent 元数据读取中')
    const modelId = agentMetadata.value.latest_run?.resolved_model_id
      ?? agentMetadata.value.requested_model_id
    return modelId
      ? `${agentMetadata.value.backend_id} / ${modelId}`
      : t('{0} / 后端默认模型', agentMetadata.value.backend_id)
  })
  const hasStaleSegments = computed(() => segments.value.some(
    (segment) => segment.status === 'stale',
  ))

  async function load(artifactId: string, options: { quiet?: boolean } = {}): Promise<boolean> {
    const token = ++generation
    if (!options.quiet) loading.value = true
    error.value = ''
    try {
      const nextArtifact = await getTranslation(artifactId)
      const [nextSegments, metadata] = await Promise.all([
        listTranslationSegments(artifactId),
        getTranslationAgentMetadata(nextArtifact.task_id).catch(() => undefined),
        jobs.refresh().catch(() => false),
      ])
      if (token !== generation) return false
      artifact.value = nextArtifact
      segments.value = [...nextSegments].sort((left, right) => left.segment_index - right.segment_index)
      agentMetadata.value = metadata
      remember(nextArtifact)
      return true
    } catch (caught) {
      if (token !== generation) return false
      error.value = readableError(caught, '暂时无法读取这份双语译文。')
      return false
    } finally {
      if (token === generation) loading.value = false
    }
  }

  async function restoreForWork(workId: string, documentVersionId: string): Promise<boolean> {
    const remembered = recall(workId)
    if (!remembered || remembered.documentVersionId !== documentVersionId) return false
    const restored = await load(remembered.artifactId, { quiet: true })
    if (!restored && artifact.value?.work_id !== workId) forget(workId)
    return restored
  }

  async function create(command: TranslationCommand): Promise<TranslationJob | undefined> {
    if (submitting.value) return undefined
    submitting.value = true
    error.value = ''
    statusMessage.value = '正在建立翻译任务。'
    try {
      const nextJob = await createTranslation(command, newTranslationIdempotencyKey())
      submittedJob.value = nextJob
      jobs.upsert(summaryFromJob(nextJob))
      await load(nextJob.artifact_id, { quiet: true })
      statusMessage.value = nextJob.quick ? '选中文本已送入本地证据翻译。' : '翻译任务已开始。'
      return nextJob
    } catch (caught) {
      error.value = readableError(caught, '翻译任务未能开始，当前选区和设置仍保留。')
      statusMessage.value = ''
      return undefined
    } finally {
      submitting.value = false
    }
  }

  async function cancel(): Promise<void> {
    const jobId = submittedJob.value?.job_id ?? artifact.value?.job_id
    if (!jobId || !active.value) return
    error.value = ''
    try {
      await jobs.requestCancel(jobId)
      if (jobs.find(jobId)?.cancel_requested) {
        statusMessage.value = '已请求取消，已完成的段落会保留。'
      } else {
        error.value = '取消请求未被确认，任务状态没有改变。'
      }
    } catch {
      error.value = '暂时无法取消翻译，任务状态没有改变。'
    }
  }

  async function resume(): Promise<TranslationJob | undefined> {
    if (!artifact.value || submitting.value) return undefined
    submitting.value = true
    error.value = ''
    try {
      const nextJob = await resumeTranslation(
        artifact.value.id,
        newTranslationIdempotencyKey(),
      )
      submittedJob.value = nextJob
      jobs.upsert(summaryFromJob(nextJob))
      await load(artifact.value.id, { quiet: true })
      statusMessage.value = '继续翻译已开始，成功段落不会重复处理。'
      return nextJob
    } catch (caught) {
      error.value = readableError(caught, '暂时无法继续翻译，已有段落没有丢失。')
      return undefined
    } finally {
      submitting.value = false
    }
  }

  async function updateTerm(termId: string, targetTerm: string, locked: boolean): Promise<boolean> {
    if (!artifact.value || savingTermId.value) return false
    savingTermId.value = termId
    error.value = ''
    try {
      const saved = await updateTranslationTerm(
        artifact.value.id,
        termId,
        { target_term: targetTerm, locked },
        newTranslationIdempotencyKey(),
      )
      artifact.value = {
        ...artifact.value,
        terms: artifact.value.terms.map((term) => term.id === termId ? saved : term),
      }
      segments.value = (await listTranslationSegments(artifact.value.id))
        .sort((left, right) => left.segment_index - right.segment_index)
      statusMessage.value = '术语已保存。受影响段落需显式继续翻译。'
      return true
    } catch (caught) {
      error.value = readableError(caught, '术语未能保存，现有译文没有改变。')
      return false
    } finally {
      savingTermId.value = ''
    }
  }

  function clear(): void {
    generation += 1
    artifact.value = undefined
    segments.value = []
    submittedJob.value = undefined
    agentMetadata.value = undefined
    error.value = ''
    statusMessage.value = ''
  }

  watch(
    () => jobs.eventRevision,
    () => {
      if (artifact.value && (
        jobs.lastEventType.startsWith('translation.')
        || jobs.lastEventType.startsWith('job.')
      )) void load(artifact.value.id, { quiet: true })
    },
  )
  watch(
    () => `${trackedJob.value?.status ?? ''}:${trackedJob.value?.progress ?? ''}`,
    () => {
      if (artifact.value) void load(artifact.value.id, { quiet: true })
    },
  )

  return {
    active,
    agentLabel,
    artifact,
    cancel,
    clear,
    create,
    diagnostics,
    error,
    hasStaleSegments,
    load,
    loading,
    pageRange,
    progress,
    restoreForWork,
    resume,
    savingTermId,
    segments,
    statusMessage,
    submittedJob,
    submitting,
    trackedJob,
    updateTerm,
  }
})
