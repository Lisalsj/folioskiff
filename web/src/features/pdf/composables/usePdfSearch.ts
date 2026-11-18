import { getCurrentScope, onScopeDispose, ref, watch, type ShallowRef } from 'vue'

import {
  PdfSafetyLimitError,
  type PdfDocumentHandle,
  type PdfSearchResult,
} from '@/lib/pdf-session'

interface UsePdfSearchOptions {
  handle: Readonly<ShallowRef<PdfDocumentHandle | undefined>>
  goToPage: (pageNumber: number) => Promise<void>
  onStateChange: () => void
  onSafetyLimit: () => void
}

export function usePdfSearch(options: UsePdfSearchOptions) {
  const query = ref('')
  const matches = ref<PdfSearchResult[]>([])
  const searching = ref(false)
  let searchGeneration = 0

  watch(query, () => {
    searchGeneration += 1
    searching.value = false
    if (!query.value.trim()) matches.value = []
  }, { flush: 'sync' })

  watch(options.handle, () => resetResults(), { flush: 'sync' })

  async function searchDocument(): Promise<void> {
    const currentHandle = options.handle.value
    if (!currentHandle) return
    const normalized = query.value.trim()
    query.value = normalized
    options.onStateChange()
    const generation = ++searchGeneration
    if (!normalized) {
      matches.value = []
      searching.value = false
      return
    }
    searching.value = true
    try {
      const results = await currentHandle.search(normalized)
      if (
        generation === searchGeneration
        && options.handle.value === currentHandle
        && query.value === normalized
      ) {
        matches.value = results
      }
    } catch (error) {
      if (generation === searchGeneration && options.handle.value === currentHandle) {
        matches.value = []
        if (error instanceof PdfSafetyLimitError) options.onSafetyLimit()
      }
    } finally {
      if (generation === searchGeneration) searching.value = false
    }
  }

  async function selectMatch(match: PdfSearchResult): Promise<void> {
    await options.goToPage(match.pageNumber)
  }

  function resetResults(): void {
    searchGeneration += 1
    matches.value = []
    searching.value = false
  }

  function dispose(): void {
    resetResults()
  }

  if (getCurrentScope()) onScopeDispose(dispose)

  return {
    matches,
    query,
    resetResults,
    searchDocument,
    searching,
    selectMatch,
  }
}
