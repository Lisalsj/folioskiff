import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'

export type AgentSelection = components['schemas']['AgentSelection']
export type ContextSelection = components['schemas']['ContextSelection']
export type KnowledgeResource = components['schemas']['KnowledgeResourceView']
export type RelatedDraftRequest = components['schemas']['RelatedDraftRequest']
export type RelatedPaper = components['schemas']['RelatedPaperView']
export type RelatedPaperSave = components['schemas']['RelatedPaperSave']
export type RelatedResourceSet = components['schemas']['RelatedResourceSetView']
export type RelatedRunOptions = components['schemas']['RelatedRunOptions']
export type RelatedRunStart = components['schemas']['RelatedRunStart']
export type RelatedSearchIntent = components['schemas']['RelatedSearchIntent']

export function draftRelatedIntent(
  workId: string,
  context: ContextSelection,
): Promise<RelatedSearchIntent> {
  const body: RelatedDraftRequest = { context }
  return apiJson<RelatedSearchIntent>(
    `/api/v1/related-resources/works/${encodeURIComponent(workId)}/draft`,
    { method: 'POST', body: JSON.stringify(body) },
  )
}

export function refineRelatedIntent(
  setId: string,
  instruction: string,
  agent: AgentSelection,
): Promise<RelatedSearchIntent> {
  return apiJson<RelatedSearchIntent>(
    `/api/v1/related-resources/${encodeURIComponent(setId)}/refine`,
    { method: 'POST', body: JSON.stringify({ instruction, agent }) },
  )
}

export function confirmRelatedIntent(
  setId: string,
  expectedRevision: number,
  intent: RelatedSearchIntent,
): Promise<RelatedSearchIntent> {
  return apiJson<RelatedSearchIntent>(
    `/api/v1/related-resources/${encodeURIComponent(setId)}/intent`,
    {
      method: 'PUT',
      body: JSON.stringify({
        expected_revision: expectedRevision,
        intent,
        confirm: true,
      }),
    },
  )
}

export function reviseRelatedIntent(
  setId: string,
  expectedRevision: number,
  intent: RelatedSearchIntent,
): Promise<RelatedSearchIntent> {
  return apiJson<RelatedSearchIntent>(
    `/api/v1/related-resources/${encodeURIComponent(setId)}/intent`,
    {
      method: 'PUT',
      body: JSON.stringify({
        expected_revision: expectedRevision,
        intent,
        confirm: false,
      }),
    },
  )
}

export function runRelatedSearch(
  setId: string,
  options: RelatedRunOptions,
  idempotencyKey: string,
): Promise<RelatedRunStart> {
  return apiJson<RelatedRunStart>(
    `/api/v1/related-resources/${encodeURIComponent(setId)}/run`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(options),
    },
  )
}

export function getRelatedResourceSet(setId: string): Promise<RelatedResourceSet> {
  return apiJson<RelatedResourceSet>(
    `/api/v1/related-resources/${encodeURIComponent(setId)}`,
  )
}

export function saveRelatedPaper(
  setId: string,
  candidateId: string,
  idempotencyKey: string,
): Promise<RelatedPaperSave> {
  return apiJson<RelatedPaperSave>(
    `/api/v1/related-resources/${encodeURIComponent(setId)}/papers/${encodeURIComponent(candidateId)}/save`,
    { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey } },
  )
}

export function newRelatedIdempotencyKey(): string {
  const value = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `related-ui-${value}`
}
