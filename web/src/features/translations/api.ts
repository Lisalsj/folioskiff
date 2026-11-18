import { readerText as t } from '@/stores/locale'
import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'

export type TranslationArtifact = components['schemas']['TranslationArtifactResponse']
export type TranslationCommand = components['schemas']['TranslationCommand']
export type TranslationJob = components['schemas']['TranslationJobResponse']
export type TranslationLanguage = TranslationArtifact['source_language']
export type TranslationScope = components['schemas']['TranslationScope']
export type TranslationSegment = components['schemas']['TranslationSegmentResponse']
export type TranslationStatus = components['schemas']['TranslationStatus']
export type TranslationTerm = components['schemas']['TranslationTermResponse']
export type TranslationTermUpdate = components['schemas']['TranslationTermUpdateRequest']
export type TranslationEvidenceLevel = TranslationArtifact['evidence_level']

export type BilingualMode = 'parallel' | 'source' | 'target'

export interface TranslationAgentMetadata {
  backend_id: string
  requested_model_id: string | null
  latest_run?: { resolved_model_id?: string | null } | null
}

export function createTranslation(
  command: TranslationCommand,
  idempotencyKey: string,
): Promise<TranslationJob> {
  return apiJson<TranslationJob>('/api/v1/translations', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(command),
  })
}

export function getTranslation(artifactId: string): Promise<TranslationArtifact> {
  return apiJson<TranslationArtifact>(
    `/api/v1/translations/${encodeURIComponent(artifactId)}`,
  )
}

export function listTranslationSegments(artifactId: string): Promise<TranslationSegment[]> {
  return apiJson<TranslationSegment[]>(
    `/api/v1/translations/${encodeURIComponent(artifactId)}/segments`,
  )
}

export function getTranslationAgentMetadata(taskId: string): Promise<TranslationAgentMetadata> {
  return apiJson<TranslationAgentMetadata>(
    `/api/v1/assistant/tasks/${encodeURIComponent(taskId)}`,
  )
}

export function updateTranslationTerm(
  artifactId: string,
  termId: string,
  update: TranslationTermUpdate,
  idempotencyKey: string,
): Promise<TranslationTerm> {
  return apiJson<TranslationTerm>(
    `/api/v1/translations/${encodeURIComponent(artifactId)}/terms/${encodeURIComponent(termId)}`,
    {
      method: 'PUT',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(update),
    },
  )
}

export function resumeTranslation(
  artifactId: string,
  idempotencyKey: string,
): Promise<TranslationJob> {
  return apiJson<TranslationJob>(
    `/api/v1/translations/${encodeURIComponent(artifactId)}/resume`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  )
}

export function translationExportUrl(
  artifactId: string,
  format: 'markdown' | 'html' | 'print',
  draft = false,
): string {
  const extension = format === 'markdown' ? 'export.md' : format === 'html' ? 'export.html' : 'print'
  const query = draft ? '?draft=true' : ''
  return `/api/v1/translations/${encodeURIComponent(artifactId)}/${extension}${query}`
}

export function newTranslationIdempotencyKey(): string {
  const value = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `translation-ui-${value}`
}

const LANGUAGE_LABELS: Record<TranslationLanguage, string> = {
  get en() { return t('英文') },
  get 'zh-CN'() { return t('简体中文') },
  get 'zh-TW'() { return t('繁体中文') },
  get ja() { return t('日文') },
  get ko() { return t('韩文') },
  get de() { return t('德文') },
  get fr() { return t('法文') },
}

const SCOPE_LABELS: Record<TranslationScope, string> = {
  get selection() { return t('选中文本') },
  get page() { return t('当前页') },
  get section() { return t('当前章节') },
  get full_document() { return t('全文') },
}

const EVIDENCE_LABELS: Record<TranslationEvidenceLevel, string> = {
  get native_text() { return t('PDF 原生文本') },
  get ocr_text() { return t('OCR 文本') },
  get mixed_text() { return t('原生文本与 OCR 混合') },
}

export function languageLabel(language: TranslationLanguage): string {
  return LANGUAGE_LABELS[language]
}

export function scopeLabel(scope: TranslationScope): string {
  return SCOPE_LABELS[scope]
}

export function evidenceLabel(level: TranslationEvidenceLevel): string {
  return EVIDENCE_LABELS[level]
}
