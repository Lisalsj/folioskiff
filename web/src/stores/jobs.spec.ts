import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useJobStore } from './jobs'

class FakeEventSource {
  static instance: FakeEventSource | undefined
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  readonly listeners = new Map<string, () => void>()
  close = vi.fn()

  constructor(readonly url: string) {
    FakeEventSource.instance = this
  }

  addEventListener(type: string, listener: () => void): void {
    this.listeners.set(type, listener)
  }

  emit(type: string): void {
    this.listeners.get(type)?.()
  }
}

describe('job store Agent events', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    FakeEventSource.instance = undefined
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('coalesces streaming fragments and terminal events into a single list refresh', async () => {
    const fetcher = vi.fn(async () => Response.json([]))
    vi.stubGlobal('fetch', fetcher)
    vi.stubGlobal('EventSource', FakeEventSource)
    const store = useJobStore()
    await store.start()
    const stream = FakeEventSource.instance!
    expect(stream.url).toBe('/api/v1/events')
    for (let count = 0; count < 50; count++) stream.emit('analysis.report.fragment')
    stream.emit('conversation.reply.fragment')
    stream.emit('backup.progress')
    stream.emit('backup.restore.pending_restart')
    stream.emit('publishing.succeeded')
    expect(store.eventRevision).toBe(54)
    expect(store.lastEventType).toBe('publishing.succeeded')
    await vi.advanceTimersByTimeAsync(200)
    expect(fetcher).toHaveBeenCalledTimes(2)
    stream.onerror?.()
    expect(store.connection).toBe('polling')
    await vi.advanceTimersByTimeAsync(3_000)
    expect(fetcher).toHaveBeenCalledTimes(3)
    stream.onopen?.()
    await vi.advanceTimersByTimeAsync(6_000)
    expect(store.connection).toBe('connected')
    expect(fetcher).toHaveBeenCalledTimes(3)
    store.stop()
  })

  it('recovers from the first read failing and closes the retry loop on stop', async () => {
    const fetcher = vi.fn(async () => Response.json([]))
    fetcher.mockRejectedValueOnce(new Error('offline'))
    vi.stubGlobal('fetch', fetcher)
    vi.stubGlobal('EventSource', FakeEventSource)
    const store = useJobStore()
    await store.start()
    expect(store.connection).toBe('polling')
    await vi.advanceTimersByTimeAsync(3_000)
    expect(FakeEventSource.instance?.url).toBe('/api/v1/events')
    store.stop()
    await vi.advanceTimersByTimeAsync(9_000)
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(FakeEventSource.instance?.close).toHaveBeenCalledOnce()
  })

  it('shares overlapping reads and ignores an obsolete start after stop and restart', async () => {
    let finish!: (response: Response) => void
    const fetcher = vi.fn(async () => Response.json([]))
    fetcher.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    vi.stubGlobal('fetch', fetcher)
    vi.stubGlobal('EventSource', FakeEventSource)
    const store = useJobStore()
    const starting = store.start()
    const reading = store.refresh()
    expect(fetcher).toHaveBeenCalledTimes(1)
    store.stop()
    await store.start()
    const stream = FakeEventSource.instance
    finish(Response.json([{ id: 'obsolete' }]))
    await Promise.all([starting, reading])
    expect(store.jobs).toEqual([])
    expect(FakeEventSource.instance).toBe(stream)
    store.stop()
  })
})
