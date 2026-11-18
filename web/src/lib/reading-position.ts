import type { components } from '@/api/schema'
import type { PdfReaderState } from '@/lib/pdf-session'

export type ReadingPositionDraft = components['schemas']['ReadingPositionRequest']

export interface ReadingPositionPersister {
  schedule(value: ReadingPositionDraft): void
  flush(): Promise<void>
  dispose(): Promise<void>
}

export function positionDraftFromState(
  documentContentSha256: string,
  state: PdfReaderState,
): ReadingPositionDraft | null {
  if (state.pageCount < 1 || state.page < 1 || state.page > state.pageCount) return null
  return {
    document_content_sha256: documentContentSha256,
    page_number: state.page,
    page_count: state.pageCount,
    scale: state.scale,
    rotation: state.rotation,
  }
}

export function createReadingPositionPersister(
  save: (value: ReadingPositionDraft) => Promise<void>,
  delay = 700,
  onError: (error: unknown) => void = () => undefined,
  initialSaved?: ReadingPositionDraft,
): ReadingPositionPersister {
  let pending: ReadingPositionDraft | undefined
  let timeout: ReturnType<typeof setTimeout> | undefined
  let lastSavedKey = initialSaved ? stableKey(initialSaved) : ''
  let active: Promise<void> = Promise.resolve()
  let disposed = false

  function schedule(value: ReadingPositionDraft): void {
    if (disposed) return
    pending = value
    if (timeout !== undefined) clearTimeout(timeout)
    timeout = setTimeout(() => {
      timeout = undefined
      void flush().catch(onError)
    }, delay)
  }

  async function flush(): Promise<void> {
    if (timeout !== undefined) {
      clearTimeout(timeout)
      timeout = undefined
    }
    active = active.catch(() => undefined).then(async () => {
      while (pending !== undefined) {
        const value = pending
        pending = undefined
        const key = stableKey(value)
        if (key === lastSavedKey) continue
        await save(value)
        lastSavedKey = key
      }
    })
    await active
  }

  async function dispose(): Promise<void> {
    if (disposed) return
    await flush()
    disposed = true
  }

  return { schedule, flush, dispose }
}

function stableKey(value: ReadingPositionDraft): string {
  return [
    value.document_content_sha256,
    value.page_number,
    value.page_count,
    value.scale,
    value.rotation,
  ].join(':')
}
