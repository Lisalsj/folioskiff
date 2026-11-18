import { ref, shallowRef } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { usePdfSearch } from './usePdfSearch'
import type { PdfDocumentHandle, PdfSearchResult } from '@/lib/pdf-session'

describe('usePdfSearch', () => {
  it('invalidates older searches even when the latest query repeats the first query', async () => {
    const pending: Array<(value: PdfSearchResult[]) => void> = []
    const handle = shallowRef<PdfDocumentHandle>({
      pageCount: 3,
      renderPage: vi.fn(),
      getPageSize: vi.fn(),
      search: vi.fn(() => new Promise<PdfSearchResult[]>((resolve) => pending.push(resolve))),
    })
    const search = usePdfSearch({
      handle,
      goToPage: vi.fn(),
      onStateChange: vi.fn(),
      onSafetyLimit: vi.fn(),
    })

    search.query.value = 'agent'
    const first = search.searchDocument()
    search.query.value = 'paper'
    const second = search.searchDocument()
    search.query.value = 'agent'
    const newest = search.searchDocument()
    pending[2]?.([{ pageNumber: 3, occurrences: 1, snippet: 'newest' }])
    await newest
    pending[0]?.([{ pageNumber: 1, occurrences: 1, snippet: 'stale-agent' }])
    pending[1]?.([{ pageNumber: 2, occurrences: 1, snippet: 'stale-paper' }])
    await Promise.all([first, second])

    expect(search.matches.value).toEqual([
      { pageNumber: 3, occurrences: 1, snippet: 'newest' },
    ])
    expect(search.searching.value).toBe(false)
  })
})
