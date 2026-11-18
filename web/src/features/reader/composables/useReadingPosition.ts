import { messageRef, readerText as t } from '@/stores/locale'
import {
  onMounted,
  onScopeDispose,
  ref,
  toValue,
  watch,
  type MaybeRefOrGetter,
  type Ref,
} from 'vue'

import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'
import type { PdfReaderState } from '@/lib/pdf-session'
import {
  createReadingPositionPersister,
  positionDraftFromState,
  type ReadingPositionDraft,
  type ReadingPositionPersister,
} from '@/lib/reading-position'

type ReadingPosition = components['schemas']['ReadingPositionResponse']

export interface ReadingPositionApi {
  read(documentVersionId: string): Promise<ReadingPosition | null>
  write(documentVersionId: string, draft: ReadingPositionDraft): Promise<ReadingPosition>
}

export interface ReadingPositionOptions {
  contentSha256: MaybeRefOrGetter<string>
  api?: ReadingPositionApi
  debounceMs?: number
}

export interface ReadingPositionState {
  position: Ref<ReadingPosition | null>
  readerState: Ref<PdfReaderState>
  loading: Ref<boolean>
  loadedDocumentId: Ref<string>
  errorMessage: Ref<string>
  statusMessage: Ref<string>
  updateState(value: PdfReaderState): void
  flush(): Promise<void>
  retry(): Promise<void>
}

const defaultReaderState = (): PdfReaderState => ({
  page: 1,
  pageCount: 0,
  scale: 1.1,
  rotation: 0,
  query: '',
})

const defaultApi: ReadingPositionApi = {
  read: (documentVersionId) => apiJson<ReadingPosition | null>(
    `/api/v1/documents/${encodeURIComponent(documentVersionId)}/reading-position`,
  ),
  write: (documentVersionId, draft) => apiJson<ReadingPosition>(
    `/api/v1/documents/${encodeURIComponent(documentVersionId)}/reading-position`,
    { method: 'PUT', body: JSON.stringify(draft) },
  ),
}

export function useReadingPosition(
  documentVersionId: MaybeRefOrGetter<string>,
  options: ReadingPositionOptions,
): ReadingPositionState {
  const api = options.api ?? defaultApi
  const position = ref<ReadingPosition | null>(null)
  const readerState = ref<PdfReaderState>(defaultReaderState())
  const loading = ref(false)
  const loadedDocumentId = ref('')
  const errorMessage = messageRef(t)
  const statusMessage = messageRef(t)
  let generation = 0
  let persister: ReadingPositionPersister | undefined

  function markPersistFailure(): void {
    errorMessage.value = '阅读位置尚未保存；应用会在下次翻页时重试。'
    statusMessage.value = errorMessage.value
  }

  async function disposePersister(): Promise<void> {
    const previous = persister
    persister = undefined
    if (previous) await previous.dispose().catch(() => undefined)
  }

  async function load(): Promise<void> {
    const currentGeneration = ++generation
    const requestedDocumentId = toValue(documentVersionId)
    const requestedHash = toValue(options.contentSha256)
    loading.value = Boolean(requestedDocumentId && requestedHash)
    loadedDocumentId.value = ''
    errorMessage.value = ''
    statusMessage.value = ''
    position.value = null
    readerState.value = defaultReaderState()
    await disposePersister()
    if (currentGeneration !== generation) return
    if (!requestedDocumentId || !requestedHash) {
      loading.value = false
      return
    }

    try {
      const loaded = await api.read(requestedDocumentId)
      if (currentGeneration !== generation) return
      if (loaded?.document_content_sha256 === requestedHash) position.value = loaded
    } catch {
      if (currentGeneration === generation) {
        errorMessage.value = '上次阅读位置暂时无法恢复；本次阅读仍可继续。'
        statusMessage.value = errorMessage.value
      }
    }
    if (currentGeneration !== generation) return

    readerState.value = {
      page: position.value?.page_number ?? 1,
      pageCount: 0,
      scale: position.value?.scale ?? 1.1,
      rotation: position.value?.rotation ?? 0,
      query: '',
    }
    const initial = position.value
      ? {
          document_content_sha256: position.value.document_content_sha256,
          page_number: position.value.page_number,
          page_count: position.value.page_count,
          scale: position.value.scale,
          rotation: position.value.rotation,
        }
      : undefined
    persister = createReadingPositionPersister(
      async (draft) => {
        const saved = await api.write(requestedDocumentId, draft)
        if (
          currentGeneration === generation
          && toValue(documentVersionId) === requestedDocumentId
        ) {
          position.value = saved
          statusMessage.value = '阅读位置已保存'
          errorMessage.value = ''
        }
      },
      options.debounceMs ?? 700,
      () => {
        if (currentGeneration === generation) markPersistFailure()
      },
      initial,
    )
    loadedDocumentId.value = requestedDocumentId
    loading.value = false
  }

  function updateState(value: PdfReaderState): void {
    readerState.value = value
    const hash = toValue(options.contentSha256)
    if (!persister || !hash) return
    const draft = positionDraftFromState(hash, value)
    if (!draft) return
    statusMessage.value = '正在记录阅读位置…'
    persister.schedule(draft)
  }

  async function flush(): Promise<void> {
    try {
      await persister?.flush()
    } catch {
      markPersistFailure()
    }
  }

  function flushWhenHidden(): void {
    if (document.visibilityState === 'hidden') void flush()
  }

  watch(
    () => [toValue(documentVersionId), toValue(options.contentSha256)] as const,
    () => { void load() },
    { immediate: true },
  )
  onMounted(() => document.addEventListener('visibilitychange', flushWhenHidden))
  onScopeDispose(() => {
    generation += 1
    document.removeEventListener('visibilitychange', flushWhenHidden)
    void disposePersister()
  })

  return {
    position,
    readerState,
    loading,
    loadedDocumentId,
    errorMessage,
    statusMessage,
    updateState,
    flush,
    retry: load,
  }
}
