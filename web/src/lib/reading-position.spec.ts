import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createReadingPositionPersister,
  positionDraftFromState,
} from './reading-position'

describe('reading position persistence', () => {
  afterEach(() => vi.useRealTimers())

  it('coalesces rapid changes, skips incomplete and duplicate states, and flushes pending work', async () => {
    vi.useFakeTimers()
    const save = vi.fn(async () => undefined)
    const persister = createReadingPositionPersister(save, 700)

    expect(positionDraftFromState('a'.repeat(64), {
      page: 1,
      pageCount: 0,
      scale: 1.1,
      rotation: 0,
      query: '',
    })).toBeNull()

    const first = positionDraftFromState('a'.repeat(64), {
      page: 1,
      pageCount: 5,
      scale: 1.1,
      rotation: 0,
      query: '',
    })!
    const latest = { ...first, page_number: 3, scale: 1.25, rotation: 90 as const }
    persister.schedule(first)
    persister.schedule({ ...first, page_number: 2 })
    persister.schedule(latest)
    await vi.advanceTimersByTimeAsync(699)
    expect(save).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(save).toHaveBeenCalledTimes(1)
    expect(save).toHaveBeenCalledWith(latest)

    persister.schedule({ ...latest })
    await vi.advanceTimersByTimeAsync(700)
    expect(save).toHaveBeenCalledTimes(1)

    persister.schedule({ ...latest, page_number: 4 })
    await persister.flush()
    expect(save).toHaveBeenCalledTimes(2)
    expect(save).toHaveBeenLastCalledWith({ ...latest, page_number: 4 })
    await persister.dispose()
  })

  it('recovers its queue after one failed save and retries a later page', async () => {
    vi.useFakeTimers()
    const onError = vi.fn()
    const save = vi.fn()
      .mockRejectedValueOnce(new Error('temporary local API failure'))
      .mockResolvedValue(undefined)
    const persister = createReadingPositionPersister(save, 100, onError)
    const draft = positionDraftFromState('b'.repeat(64), {
      page: 1,
      pageCount: 3,
      scale: 1.1,
      rotation: 0,
      query: '',
    })!

    persister.schedule(draft)
    await vi.advanceTimersByTimeAsync(100)
    expect(onError).toHaveBeenCalledTimes(1)
    persister.schedule({ ...draft, page_number: 2 })
    await vi.advanceTimersByTimeAsync(100)

    expect(save).toHaveBeenCalledTimes(2)
    expect(save).toHaveBeenLastCalledWith({ ...draft, page_number: 2 })
    await persister.dispose()
  })

  it('cancels a pending page change when navigation returns to the saved position', async () => {
    vi.useFakeTimers()
    const save = vi.fn(async () => undefined)
    const saved = positionDraftFromState('c'.repeat(64), {
      page: 2,
      pageCount: 3,
      scale: 1.1,
      rotation: 0,
      query: '',
    })!
    const persister = createReadingPositionPersister(
      save,
      700,
      () => undefined,
      saved,
    )

    persister.schedule({ ...saved, page_number: 1 })
    persister.schedule({ ...saved })
    await vi.advanceTimersByTimeAsync(700)

    expect(save).not.toHaveBeenCalled()
    await persister.dispose()
  })
})
