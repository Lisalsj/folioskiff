import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'

export type AssistantCapability = components['schemas']['AgentCapabilityResponse']
export type AssistantContext = components['schemas']['AssistantContextResponse']
export type AssistantFragment = components['schemas']['AssistantFragmentResponse']
export type AssistantJob = components['schemas']['AssistantJobResponse']
export type AssistantMessage = components['schemas']['AssistantMessageResponse']
export type AssistantMode = components['schemas']['AssistantMode']
export type AssistantTask = components['schemas']['AssistantTaskResponse']
export type AssistantTaskCommand = components['schemas']['AssistantTaskCommand']
export type AssistantTaskSummary = components['schemas']['AssistantTaskSummaryResponse']
export type AssistantWork = components['schemas']['AssistantWorkResponse']
export type ContextScope = components['schemas']['ContextScope']
export type ReasoningEffort = components['schemas']['ReasoningEffort']

export function getAssistantWork(workId: string): Promise<AssistantWork> {
  return apiJson<AssistantWork>(`/api/v1/assistant/works/${encodeURIComponent(workId)}`)
}

export function createAssistantTask(
  workId: string,
  command: AssistantTaskCommand,
  idempotencyKey: string,
): Promise<AssistantJob> {
  return apiJson<AssistantJob>(
    `/api/v1/assistant/works/${encodeURIComponent(workId)}/tasks`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(command),
    },
  )
}

export function getAssistantTask(taskId: string): Promise<AssistantTask> {
  return apiJson<AssistantTask>(`/api/v1/assistant/tasks/${encodeURIComponent(taskId)}`)
}

export function getAssistantFragments(
  taskId: string,
  afterSequence: number,
): Promise<AssistantFragment[]> {
  const query = new URLSearchParams({ after_sequence: String(afterSequence) })
  return apiJson<AssistantFragment[]>(
    `/api/v1/assistant/tasks/${encodeURIComponent(taskId)}/fragments?${query}`,
  )
}

export function retryAssistantTask(
  taskId: string,
  idempotencyKey: string,
): Promise<AssistantJob> {
  return apiJson<AssistantJob>(
    `/api/v1/assistant/tasks/${encodeURIComponent(taskId)}/retry`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  )
}

export function newAssistantIdempotencyKey(): string {
  const value = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `assistant-ui-${value}`
}
