import { defineComponent, h, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { components } from '@/api/schema'
import { useReadingPosition } from './useReadingPosition'

type ReadingPosition = components['schemas']['ReadingPositionResponse']

function savedPosition(overrides: Partial<ReadingPosition> = {}): ReadingPosition {
  return {
    document_version_id: 'document-1',
    document_content_sha256: 'a'.repeat(64),
    page_number: 2,
    page_count: 8,
    scale: 1.25,
    rotation: 90,
    progress: 25,
    created_at: '2026-07-13T08:00:00Z',
    updated_at: '2026-07-13T08:00:00Z',
    ...overrides,
  }
}

describe('useReadingPosition', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('restores, debounces and flushes document-owned state when the page is hidden', async () => {
    vi.useFakeTimers()
    const documentId = ref('document-1')
    const contentSha256 = ref('a'.repeat(64))
    const writes: unknown[] = []
    const api = {
      read: vi.fn(async () => savedPosition()),
      write: vi.fn(async (_id: string, draft: unknown) => {
        writes.push(draft)
        return savedPosition({ page_number: 7, updated_at: '2026-07-13T08:05:00Z' })
      }),
    }
    let state!: ReturnType<typeof useReadingPosition>
    const wrapper = mount(defineComponent({
      setup() {
        state = useReadingPosition(documentId, { contentSha256, api, debounceMs: 700 })
        return () => h('div')
      },
    }))
    await flushPromises()

    expect(state.readerState.value).toMatchObject({ page: 2, scale: 1.25, rotation: 90 })
    state.updateState({ page: 7, pageCount: 8, scale: 1.25, rotation: 90, query: '' })
    await vi.advanceTimersByTimeAsync(699)
    expect(writes).toHaveLength(0)

    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    visibility.mockRestore()
    expect(writes).toEqual([{
      document_content_sha256: 'a'.repeat(64),
      page_number: 7,
      page_count: 8,
      scale: 1.25,
      rotation: 90,
    }])
    expect(state.statusMessage.value).toBe('阅读位置已保存')
    wrapper.unmount()
    await flushPromises()
  })

  it('keeps a read failure non-fatal and retries against the same document', async () => {
    let fail = true
    const api = {
      read: vi.fn(async () => {
        if (fail) throw new Error('private path')
        return savedPosition()
      }),
      write: vi.fn(async () => savedPosition()),
    }
    let state!: ReturnType<typeof useReadingPosition>
    const wrapper = mount(defineComponent({
      setup() {
        state = useReadingPosition(ref('document-1'), {
          contentSha256: ref('a'.repeat(64)), api,
        })
        return () => h('div')
      },
    }))
    await flushPromises()

    expect(state.errorMessage.value).toContain('上次阅读位置暂时无法恢复')
    expect(state.errorMessage.value).not.toContain('private')
    fail = false
    await state.retry()
    expect(state.position.value?.page_number).toBe(2)
    expect(state.errorMessage.value).toBe('')
    wrapper.unmount()
    await flushPromises()
  })

  it('turns an explicit flush failure into visible retryable state', async () => {
    const api = {
      read: vi.fn(async () => null),
      write: vi.fn(async () => { throw new Error('D:\\private\\reader.db') }),
    }
    let state!: ReturnType<typeof useReadingPosition>
    const wrapper = mount(defineComponent({
      setup() {
        state = useReadingPosition(ref('document-1'), {
          contentSha256: ref('a'.repeat(64)), api,
        })
        return () => h('div')
      },
    }))
    await flushPromises()
    state.updateState({ page: 3, pageCount: 8, scale: 1.1, rotation: 0, query: '' })

    await expect(state.flush()).resolves.toBeUndefined()
    expect(state.errorMessage.value).toContain('阅读位置尚未保存')
    expect(state.errorMessage.value).not.toContain('private')
    wrapper.unmount()
    await flushPromises()
  })
})
