import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import type { components } from '@/api/schema'
import { apiFetch } from '@/lib/api'

export type JobSummary = components['schemas']['JobSummary']

const EVENT_TYPES = [
  'job.started',
  'job.progress',
  'job.completed',
  'job.failed',
  'job.cancelled',
  'discovery.run.started',
  'discovery.run.succeeded',
  'discovery.run.partial_success',
  'discovery.run.failed',
  'discovery.run.cancelled',
  'report.delta',
  'analysis.report.started',
  'analysis.report.fragment',
  'analysis.report.succeeded',
  'analysis.report.failed',
  'analysis.report.cancelled',
  'conversation.reply.started',
  'conversation.reply.fragment',
  'conversation.reply.succeeded',
  'conversation.reply.failed',
  'conversation.reply.cancelled',
  'assistant.task.started',
  'assistant.fragment',
  'assistant.task.succeeded',
  'assistant.task.failed',
  'assistant.task.cancelled',
  'related.search.started',
  'related.source.page',
  'related.external.verified',
  'related.search.succeeded',
  'related.search.failed',
  'related.search.cancelled',
  'translation.started',
  'translation.batch.succeeded',
  'translation.succeeded',
  'translation.failed',
  'translation.cancelled',
  'comparison.generate.started',
  'comparison.generate.fragment',
  'comparison.paper.processed',
  'comparison.batch.succeeded',
  'comparison.generate.succeeded',
  'comparison.generate.failed',
  'comparison.generate.cancelled',
  'backup.started',
  'backup.progress',
  'backup.archived',
  'backup.succeeded',
  'backup.failed',
  'backup.cancelled',
  'backup.retention_warning',
  'backup.restore_validation.started',
  'backup.restore_validation.succeeded',
  'backup.restore_validation.failed',
  'backup.restore.pending_restart',
  'publishing.started',
  'publishing.succeeded',
  'publishing.failed',
  'publishing.cancelled',
  'models.install.started',
  'models.install.progress',
  'models.install.paused',
  'models.install.succeeded',
  'models.install.failed',
  'models.install.cancelled',
]

export const useJobStore = defineStore('jobs', () => {
  const jobs = ref<JobSummary[]>([])
  const connection = ref<'idle' | 'connected' | 'polling' | 'error'>('idle')
  const eventRevision = ref(0)
  const lastEventType = ref('')
  const activeCount = computed(
    () => jobs.value.filter((job) => ['queued', 'running'].includes(job.status)).length,
  )
  let started = false
  let eventSource: EventSource | undefined
  let pollingTimer: number | undefined

  let session = 0
  let inFlight: Promise<boolean> | undefined
  let controller: AbortController | undefined
  let pendingUpdates: Map<string, JobSummary> | undefined
  let refreshTimer: number | undefined
  let dirty = false
  let flushing = false

  async function readJobs(signal: AbortSignal, updates: Map<string, JobSummary>): Promise<boolean> {
    try {
      const response = await apiFetch('/api/v1/jobs', { signal })
      if (!response.ok) throw new Error('jobs unavailable')
      const result: JobSummary[] = await response.json()
      if (signal.aborted) return false
      // An action may have returned a newer job while the list was in flight.
      const ids = new Set(result.map((job) => job.id))
      jobs.value = [
        ...Array.from(updates.values()).filter((job) => !ids.has(job.id)),
        ...result.map((job) => updates.get(job.id) ?? job),
      ]
      if (eventSource?.readyState === 1) connection.value = 'connected'
      else if (pollingTimer !== undefined) connection.value = 'polling'
      return true
    } catch {
      if (!signal.aborted) connection.value = 'error'
      return false
    }
  }

  function refresh(): Promise<boolean> {
    if (inFlight) return inFlight
    const request = new AbortController()
    controller = request
    pendingUpdates = new Map()
    inFlight = readJobs(request.signal, pendingUpdates).finally(() => {
      if (controller === request) {
        controller = undefined
        inFlight = undefined
        pendingUpdates = undefined
      }
    })
    return inFlight
  }

  async function refreshAfterPending(run: number): Promise<void> {
    if (inFlight) await inFlight
    if (session === run) await refresh()
  }

  function queueRefresh(): void {
    dirty = true
    if (flushing || refreshTimer !== undefined) return
    const run = session
    refreshTimer = window.setTimeout(async () => {
      refreshTimer = undefined
      dirty = false
      flushing = true
      await refreshAfterPending(run)
      if (session !== run) return
      flushing = false
      if (dirty) queueRefresh()
    }, 200)
  }

  function upsert(job: JobSummary): void {
    pendingUpdates?.set(job.id, job)
    const index = jobs.value.findIndex((item) => item.id === job.id)
    if (index === -1) {
      jobs.value = [job, ...jobs.value]
      return
    }
    jobs.value = jobs.value.map((item, itemIndex) => itemIndex === index ? job : item)
  }

  function find(jobId: string): JobSummary | undefined {
    return jobs.value.find((job) => job.id === jobId)
  }

  function beginPolling(): void {
    if (pollingTimer !== undefined || !started) return
    connection.value = 'polling'
    const run = session
    const timer = window.setTimeout(async () => {
      const ok = await refresh()
      if (run !== session || pollingTimer !== timer) return
      pollingTimer = undefined
      if (ok && !eventSource) connectEvents()
      if (connection.value !== 'connected') beginPolling()
    }, 3_000)
    pollingTimer = timer
  }

  function connectEvents(): void {
    if (!('EventSource' in window)) {
      beginPolling()
      return
    }

    let source: EventSource
    try {
      source = new EventSource('/api/v1/events')
      eventSource = source
    } catch {
      beginPolling()
      return
    }
    source.onopen = () => {
      if (eventSource !== source) return
      window.clearTimeout(pollingTimer)
      pollingTimer = undefined
      connection.value = 'connected'
    }
    for (const eventType of EVENT_TYPES) {
      source.addEventListener(eventType, () => {
        if (eventSource !== source) return
        lastEventType.value = eventType
        eventRevision.value += 1
        queueRefresh()
      })
    }
    source.onerror = () => {
      if (eventSource !== source) return
      // CONNECTING streams keep the browser's Last-Event-ID on reconnection.
      if (source.readyState === 2) {
        source.close()
        eventSource = undefined
      }
      beginPolling()
    }
  }

  async function start(): Promise<void> {
    if (started) return
    started = true
    const run = ++session
    const ok = await refresh()
    if (session !== run) return
    if (ok) connectEvents()
    else beginPolling()
  }

  async function requestCancel(jobId: string): Promise<void> {
    const run = session
    const response = await apiFetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' })
    if (response.ok) {
      await refreshAfterPending(run)
    }
  }

  async function requestModelCommand(jobId: string, command: 'pause' | 'resume'): Promise<void> {
    const response = await apiFetch(
      `/api/v1/settings/model-assets/jobs/${encodeURIComponent(jobId)}/${command}`,
      { method: 'POST' },
    )
    if (response.ok) {
      upsert((await response.json()) as JobSummary)
    }
  }

  async function requestModelPause(jobId: string): Promise<void> {
    await requestModelCommand(jobId, 'pause')
  }

  async function requestModelResume(jobId: string): Promise<void> {
    await requestModelCommand(jobId, 'resume')
  }

  function stop(): void {
    eventSource?.close()
    eventSource = undefined
    session += 1
    controller?.abort()
    controller = undefined
    inFlight = undefined
    pendingUpdates = undefined
    window.clearTimeout(pollingTimer)
    window.clearTimeout(refreshTimer)
    pollingTimer = undefined
    refreshTimer = undefined
    dirty = false
    flushing = false
    started = false
    connection.value = 'idle'
  }

  return {
    jobs,
    connection,
    eventRevision,
    lastEventType,
    activeCount,
    find,
    refresh,
    requestCancel,
    requestModelPause,
    requestModelResume,
    start,
    stop,
    upsert,
  }
})
