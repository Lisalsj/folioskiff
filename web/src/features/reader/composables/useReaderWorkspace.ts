import { messageRef, readerText as t } from '@/stores/locale'
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
import { apiJson } from '@/lib/json-api'

type LibraryWork = components['schemas']['LibraryWorkResponse']
type LibraryDocument = components['schemas']['LibraryDocumentResponse']

export interface ReaderWorkspaceApi {
  getWork(workId: string): Promise<LibraryWork>
  listDocuments(workId: string): Promise<LibraryDocument[]>
}

export interface ReaderWorkspaceState {
  work: Ref<LibraryWork | undefined>
  documents: Ref<LibraryDocument[]>
  selectedDocument: ComputedRef<LibraryDocument | undefined>
  contentUrl: ComputedRef<string>
  loading: Ref<boolean>
  errorMessage: Ref<string>
  retry(): Promise<void>
}

const defaultApi: ReaderWorkspaceApi = {
  getWork: (workId) => apiJson<LibraryWork>(
    `/api/v1/works/${encodeURIComponent(workId)}`,
  ),
  listDocuments: (workId) => apiJson<LibraryDocument[]>(
    `/api/v1/works/${encodeURIComponent(workId)}/documents`,
  ),
}

export function useReaderWorkspace(
  workId: MaybeRefOrGetter<string>,
  documentVersionId: MaybeRefOrGetter<string>,
  api: ReaderWorkspaceApi = defaultApi,
): ReaderWorkspaceState {
  const work = ref<LibraryWork>()
  const documents = ref<LibraryDocument[]>([])
  const loading = ref(true)
  const errorMessage = messageRef(t)
  let generation = 0

  const selectedDocument = computed(() => documents.value.find(
    (document) => document.id === toValue(documentVersionId),
  ))
  const contentUrl = computed(() => selectedDocument.value?.available
    ? `/api/v1/documents/${selectedDocument.value.id}/content`
    : '')

  async function load(): Promise<void> {
    const currentGeneration = ++generation
    const requestedWorkId = toValue(workId)
    const requestedDocumentId = toValue(documentVersionId)
    loading.value = true
    errorMessage.value = ''
    work.value = undefined
    documents.value = []
    try {
      if (!requestedWorkId || !requestedDocumentId) throw new Error('Missing reader identity')
      const [loadedWork, loadedDocuments] = await Promise.all([
        api.getWork(requestedWorkId),
        api.listDocuments(requestedWorkId),
      ])
      if (currentGeneration !== generation) return
      if (!loadedDocuments.some((document) => document.id === requestedDocumentId)) {
        throw new Error('Selected document is not part of this work')
      }
      work.value = loadedWork
      documents.value = loadedDocuments
    } catch {
      if (currentGeneration === generation) {
        errorMessage.value = '暂时无法打开阅读工作台。论文可能已被移除，或所选版本不再可用。'
      }
    } finally {
      if (currentGeneration === generation) loading.value = false
    }
  }

  watch(
    () => [toValue(workId), toValue(documentVersionId)] as const,
    () => { void load() },
    { immediate: true },
  )
  onScopeDispose(() => { generation += 1 })

  return {
    work,
    documents,
    selectedDocument,
    contentUrl,
    loading,
    errorMessage,
    retry: load,
  }
}
