import { messageRef, messageTemplate, readerText as t } from '@/stores/locale'
import {
  computed,
  onScopeDispose,
  ref,
  toValue,
  watch,
  type ComputedRef,
  type MaybeRefOrGetter,
  type Ref,
} from 'vue'

import type { components } from '@/api/schema'
import type { PdfTextSelection } from '@/lib/annotation-geometry'
import { apiJson, apiVoid } from '@/lib/json-api'

type Annotation = components['schemas']['AnnotationResponse']
type AnnotationRequest = components['schemas']['AnnotationRequest']
type AnnotationKind = components['schemas']['AnnotationKind']
type AnnotationColor = components['schemas']['AnnotationColor']
type MigrationPreview = components['schemas']['MigrationPreviewResponse']
type MigrationCandidate = components['schemas']['MigrationCandidateResponse']
export type MigrationDecision = 'accept' | 'reject'

export interface AnnotationCreateIntent {
  kind: AnnotationKind
  color: AnnotationColor
  note: string | null
}

export interface AnnotationUpdateIntent {
  color: AnnotationColor
  note: string | null
}

export interface PageAnchor {
  pageNumber: number
  pageCount: number
  pageWidth: number
  pageHeight: number
}

export interface AnnotationApi {
  list(documentVersionId: string): Promise<Annotation[]>
  create(documentVersionId: string, payload: AnnotationRequest): Promise<Annotation>
  update(
    documentVersionId: string,
    annotationId: string,
    payload: AnnotationRequest,
  ): Promise<Annotation>
  remove(documentVersionId: string, annotationId: string): Promise<void>
  previewMigration(sourceDocumentId: string, targetDocumentId: string): Promise<MigrationPreview>
  decideMigration(candidateId: string, decision: MigrationDecision): Promise<MigrationCandidate>
  getMigration(previewId: string): Promise<MigrationPreview>
}

export interface AnnotationOptions {
  pageCount: MaybeRefOrGetter<number>
  availableDocumentIds: MaybeRefOrGetter<readonly string[]>
  api?: AnnotationApi
}

export interface MigrationPreviewMark {
  id: string
  pageNumber: number
  pageWidth: number
  pageHeight: number
  rects: MigrationCandidate['target_rects']
}

export interface AnnotationState {
  annotations: Ref<Annotation[]>
  selection: Ref<PdfTextSelection | null>
  loading: Ref<boolean>
  loadedDocumentId: Ref<string>
  saving: Ref<boolean>
  errorMessage: Ref<string>
  statusMessage: Ref<string>
  migrationPreview: Ref<MigrationPreview | null>
  migrationCandidatePreview: Ref<MigrationCandidate | null>
  migrationPreviewMark: ComputedRef<MigrationPreviewMark | null>
  migrationLoading: Ref<boolean>
  migrationSaving: Ref<boolean>
  migrationError: Ref<string>
  migrationStatus: Ref<string>
  setSelection(value: PdfTextSelection | null): void
  clearSelection(): void
  beginCreate(intent: AnnotationCreateIntent): boolean
  quickHighlight(selection: PdfTextSelection): void
  commitBookmark(anchor: PageAnchor): Promise<void>
  update(annotation: Annotation, intent: AnnotationUpdateIntent): Promise<void>
  remove(annotation: Annotation): Promise<void>
  retry(): Promise<void>
  previewMigration(sourceDocumentId: string): Promise<void>
  previewCandidate(candidate: MigrationCandidate): number | null
  decideMigration(candidate: MigrationCandidate, decision: MigrationDecision): Promise<void>
}

const defaultApi: AnnotationApi = {
  list: (documentVersionId) => apiJson<Annotation[]>(
    `/api/v1/documents/${encodeURIComponent(documentVersionId)}/annotations`,
  ),
  create: (documentVersionId, payload) => apiJson<Annotation>(
    `/api/v1/documents/${encodeURIComponent(documentVersionId)}/annotations`,
    { method: 'POST', body: JSON.stringify(payload) },
  ),
  update: (documentVersionId, annotationId, payload) => apiJson<Annotation>(
    `/api/v1/documents/${encodeURIComponent(documentVersionId)}/annotations/${encodeURIComponent(annotationId)}`,
    { method: 'PUT', body: JSON.stringify(payload) },
  ),
  remove: (documentVersionId, annotationId) => apiVoid(
    `/api/v1/documents/${encodeURIComponent(documentVersionId)}/annotations/${encodeURIComponent(annotationId)}`,
    { method: 'DELETE' },
  ),
  previewMigration: (sourceDocumentId, targetDocumentId) => apiJson<MigrationPreview>(
    `/api/v1/documents/${encodeURIComponent(sourceDocumentId)}/annotation-migrations/preview`,
    { method: 'POST', body: JSON.stringify({ target_document_version_id: targetDocumentId }) },
  ),
  decideMigration: (candidateId, decision) => apiJson<MigrationCandidate>(
    `/api/v1/annotation-migrations/candidates/${encodeURIComponent(candidateId)}/decision`,
    { method: 'PUT', body: JSON.stringify({ decision }) },
  ),
  getMigration: (previewId) => apiJson<MigrationPreview>(
    `/api/v1/annotation-migrations/${encodeURIComponent(previewId)}`,
  ),
}

export function useAnnotations(
  documentVersionId: MaybeRefOrGetter<string>,
  contentSha256: MaybeRefOrGetter<string>,
  options: AnnotationOptions,
): AnnotationState {
  const api = options.api ?? defaultApi
  const annotations = ref<Annotation[]>([])
  const selection = ref<PdfTextSelection | null>(null)
  const loading = ref(false)
  const loadedDocumentId = ref('')
  const saving = ref(false)
  const errorMessage = messageRef(t)
  const statusMessage = messageRef(t)
  const migrationPreview = ref<MigrationPreview | null>(null)
  const migrationCandidatePreview = ref<MigrationCandidate | null>(null)
  const migrationLoading = ref(false)
  const migrationSaving = ref(false)
  const migrationError = messageRef(t)
  const migrationStatus = messageRef(t)
  let generation = 0
  let pendingBookmarkIntent: AnnotationCreateIntent | undefined

  const migrationPreviewMark = computed<MigrationPreviewMark | null>(() => {
    const candidate = migrationCandidatePreview.value
    if (
      !candidate
      || candidate.target_page_number === null
      || candidate.target_page_width === null
      || candidate.target_page_height === null
    ) return null
    return {
      id: candidate.id,
      pageNumber: candidate.target_page_number,
      pageWidth: candidate.target_page_width,
      pageHeight: candidate.target_page_height,
      rects: candidate.target_rects,
    }
  })

  function reset(): void {
    annotations.value = []
    selection.value = null
    saving.value = false
    errorMessage.value = ''
    statusMessage.value = ''
    migrationPreview.value = null
    migrationCandidatePreview.value = null
    migrationLoading.value = false
    migrationSaving.value = false
    migrationError.value = ''
    migrationStatus.value = ''
    pendingBookmarkIntent = undefined
  }

  async function load(): Promise<void> {
    const currentGeneration = ++generation
    const requestedDocumentId = toValue(documentVersionId)
    const requestedHash = toValue(contentSha256)
    reset()
    loadedDocumentId.value = ''
    loading.value = Boolean(requestedDocumentId && requestedHash)
    if (!requestedDocumentId || !requestedHash) {
      loading.value = false
      return
    }
    try {
      const loaded = await api.list(requestedDocumentId)
      if (currentGeneration === generation) annotations.value = sortAnnotations(loaded)
    } catch {
      if (currentGeneration === generation) {
        errorMessage.value = '暂时无法读取批注。已有 PDF 仍可正常阅读。'
      }
    } finally {
      if (currentGeneration === generation) {
        loadedDocumentId.value = requestedDocumentId
        loading.value = false
      }
    }
  }

  function setSelection(value: PdfTextSelection | null): void {
    selection.value = value
  }

  function clearSelection(): void {
    selection.value = null
  }

  function beginCreate(intent: AnnotationCreateIntent): boolean {
    if (intent.kind === 'bookmark') {
      pendingBookmarkIntent = intent
      return true
    }
    if (!selection.value) {
      errorMessage.value = '请先在 PDF 中选择一段文字。'
      return false
    }
    void persist(intent, selection.value)
    return false
  }

  function quickHighlight(value: PdfTextSelection): void {
    selection.value = value
    void persist({ kind: 'highlight', color: 'amber', note: null }, value)
  }

  async function commitBookmark(anchor: PageAnchor): Promise<void> {
    const intent = pendingBookmarkIntent ?? { kind: 'bookmark', color: 'amber', note: null }
    pendingBookmarkIntent = undefined
    await persist(intent, anchor)
  }

  async function persist(
    intent: AnnotationCreateIntent,
    evidence: PdfTextSelection | PageAnchor,
  ): Promise<void> {
    const documentId = toValue(documentVersionId)
    const hash = toValue(contentSha256)
    if (!documentId || !hash || saving.value) return
    saving.value = true
    errorMessage.value = ''
    statusMessage.value = '正在保存批注…'
    const isBookmark = intent.kind === 'bookmark'
    const payload: AnnotationRequest = {
      document_content_sha256: hash,
      kind: intent.kind,
      page_number: evidence.pageNumber,
      page_count: evidence.pageCount,
      page_width: evidence.pageWidth,
      page_height: evidence.pageHeight,
      quote_text: isBookmark ? null : (evidence as PdfTextSelection).quoteText,
      context_before: isBookmark ? null : (evidence as PdfTextSelection).contextBefore,
      context_after: isBookmark ? null : (evidence as PdfTextSelection).contextAfter,
      rects: isBookmark ? [] : (evidence as PdfTextSelection).rects,
      color: intent.color,
      note: intent.note,
    }
    try {
      const saved = await api.create(documentId, payload)
      if (toValue(documentVersionId) !== documentId) return
      annotations.value = sortAnnotations([...annotations.value, saved])
      statusMessage.value = isBookmark ? '书签已保存' : '批注已保存'
      if (!isBookmark) selection.value = null
    } catch {
      if (toValue(documentVersionId) === documentId) {
        errorMessage.value = '批注未能保存。选区仍保留，可稍后重试。'
        statusMessage.value = ''
      }
    } finally {
      if (toValue(documentVersionId) === documentId) saving.value = false
    }
  }

  async function update(
    annotation: Annotation,
    intent: AnnotationUpdateIntent,
  ): Promise<void> {
    const documentId = toValue(documentVersionId)
    const hash = toValue(contentSha256)
    if (!documentId || !hash || saving.value) return
    saving.value = true
    errorMessage.value = ''
    statusMessage.value = '正在更新批注…'
    const payload: AnnotationRequest = {
      document_content_sha256: hash,
      kind: annotation.kind,
      page_number: annotation.page_number,
      page_count: Math.max(toValue(options.pageCount), annotation.page_number),
      page_width: annotation.page_width,
      page_height: annotation.page_height,
      quote_text: annotation.quote_text,
      context_before: annotation.context_before,
      context_after: annotation.context_after,
      rects: annotation.rects,
      color: intent.color,
      note: intent.note,
    }
    try {
      const saved = await api.update(documentId, annotation.id, payload)
      if (toValue(documentVersionId) !== documentId) return
      annotations.value = sortAnnotations(
        annotations.value.map((item) => item.id === saved.id ? saved : item),
      )
      statusMessage.value = '批注已更新'
    } catch {
      if (toValue(documentVersionId) === documentId) {
        errorMessage.value = '批注未能更新；原内容没有改变。'
        statusMessage.value = ''
      }
    } finally {
      if (toValue(documentVersionId) === documentId) saving.value = false
    }
  }

  async function remove(annotation: Annotation): Promise<void> {
    const documentId = toValue(documentVersionId)
    if (!documentId || saving.value) return
    saving.value = true
    errorMessage.value = ''
    statusMessage.value = '正在删除批注…'
    try {
      await api.remove(documentId, annotation.id)
      if (toValue(documentVersionId) !== documentId) return
      annotations.value = annotations.value.filter((item) => item.id !== annotation.id)
      statusMessage.value = '批注已删除'
    } catch {
      if (toValue(documentVersionId) === documentId) {
        errorMessage.value = '批注未能删除；原内容仍然保留。'
        statusMessage.value = ''
      }
    } finally {
      if (toValue(documentVersionId) === documentId) saving.value = false
    }
  }

  async function previewAnnotationMigration(sourceDocumentId: string): Promise<void> {
    const targetDocumentId = toValue(documentVersionId)
    const available = toValue(options.availableDocumentIds)
    if (
      !targetDocumentId
      || migrationLoading.value
      || sourceDocumentId === targetDocumentId
      || !available.includes(sourceDocumentId)
    ) return
    migrationLoading.value = true
    migrationError.value = ''
    migrationStatus.value = '正在逐条比对旧版批注与当前版本…'
    migrationCandidatePreview.value = null
    try {
      const preview = await api.previewMigration(sourceDocumentId, targetDocumentId)
      if (toValue(documentVersionId) !== targetDocumentId) return
      migrationPreview.value = preview
      migrationStatus.value = preview.coverage.total === 0
        ? '旧版本没有需要迁移的批注。'
        : messageTemplate('迁移预览已生成，共 {0} 条；请逐条确认。', preview.coverage.total)
    } catch {
      if (toValue(documentVersionId) === targetDocumentId) {
        migrationError.value = '迁移预览未能生成。请确认两个版本都已完成文本提取后重试。'
        migrationStatus.value = ''
      }
    } finally {
      if (toValue(documentVersionId) === targetDocumentId) migrationLoading.value = false
    }
  }

  function previewCandidate(candidate: MigrationCandidate): number | null {
    if (candidate.target_page_number === null) return null
    migrationCandidatePreview.value = candidate
    return candidate.target_page_number
  }

  async function decideAnnotationMigration(
    candidate: MigrationCandidate,
    decision: MigrationDecision,
  ): Promise<void> {
    const documentId = toValue(documentVersionId)
    const preview = migrationPreview.value
    if (
      !documentId
      || !preview
      || migrationSaving.value
      || candidate.batch_id !== preview.id
      || candidate.decision !== null
    ) return
    migrationSaving.value = true
    migrationError.value = ''
    migrationStatus.value = decision === 'accept'
      ? '正在建立新版批注…'
      : '正在记录保留决定…'
    try {
      await api.decideMigration(candidate.id, decision)
      const refreshed = await api.getMigration(preview.id)
      const refreshedAnnotations = decision === 'accept'
        ? await api.list(documentId)
        : null
      if (toValue(documentVersionId) !== documentId) return
      migrationPreview.value = refreshed
      if (refreshedAnnotations) annotations.value = sortAnnotations(refreshedAnnotations)
      migrationCandidatePreview.value = null
      migrationStatus.value = decision === 'accept'
        ? '新版批注已建立；旧版批注仍完整保留。'
        : '已拒绝本次迁移；旧版批注仍完整保留。'
    } catch {
      if (toValue(documentVersionId) === documentId) {
        migrationError.value = '迁移决定未能保存，任何原批注都没有改变。请刷新预览后重试。'
        migrationStatus.value = ''
      }
    } finally {
      if (toValue(documentVersionId) === documentId) migrationSaving.value = false
    }
  }

  watch(
    () => [toValue(documentVersionId), toValue(contentSha256)] as const,
    () => { void load() },
    { immediate: true },
  )
  onScopeDispose(() => { generation += 1 })

  return {
    annotations,
    selection,
    loading,
    loadedDocumentId,
    saving,
    errorMessage,
    statusMessage,
    migrationPreview,
    migrationCandidatePreview,
    migrationPreviewMark,
    migrationLoading,
    migrationSaving,
    migrationError,
    migrationStatus,
    setSelection,
    clearSelection,
    beginCreate,
    quickHighlight,
    commitBookmark,
    update,
    remove,
    retry: load,
    previewMigration: previewAnnotationMigration,
    previewCandidate,
    decideMigration: decideAnnotationMigration,
  }
}

function sortAnnotations(values: Annotation[]): Annotation[] {
  return [...values].sort((left, right) => (
    left.page_number - right.page_number
    || left.created_at.localeCompare(right.created_at)
    || left.id.localeCompare(right.id)
  ))
}
