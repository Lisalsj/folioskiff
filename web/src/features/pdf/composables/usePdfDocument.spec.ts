import { defineComponent, h, nextTick, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import { usePdfDocument } from './usePdfDocument'
import type { PdfDocumentHandle, PdfSession } from '@/lib/pdf-session'

function fixture() {
  const handle: PdfDocumentHandle = {
    pageCount: 3,
    renderPage: vi.fn(async () => ({ width: 420, height: 595 })),
    getPageSize: vi.fn(async () => ({ width: 420, height: 595 })),
    search: vi.fn(async () => []),
  }
  const session: PdfSession = {
    open: vi.fn(async () => handle),
    close: vi.fn(async () => undefined),
  }
  return { handle, session }
}

describe('usePdfDocument', () => {
  it('owns replacement cleanup, page clamping and pending navigation', async () => {
    const pdf = fixture()
    const documentUrl = ref('/first.pdf')
    let controller: ReturnType<typeof usePdfDocument> | undefined
    const canvas = document.createElement('canvas')
    const textLayer = document.createElement('div')
    const viewport = document.createElement('div')
    const Harness = defineComponent({
      setup() {
        controller = usePdfDocument({
          documentUrl,
          initialPage: ref(1),
          initialScale: ref(1.1),
          initialRotation: ref(0),
          sessionFactory: () => pdf.session,
          getSurface: () => ({ canvas, textLayer, viewport }),
          clearSelection: vi.fn(),
          onStateChange: vi.fn(),
        })
        return () => h('div')
      },
    })
    const wrapper = mount(Harness)
    await flushPromises()
    expect(controller?.page.value).toBe(1)

    await controller?.goToPage(99)
    expect(controller?.page.value).toBe(3)
    documentUrl.value = '/second.pdf'
    await flushPromises()

    expect(pdf.session.close).toHaveBeenCalledTimes(1)
    expect(pdf.session.open).toHaveBeenNthCalledWith(2, '/second.pdf')
    wrapper.unmount()
    expect(pdf.session.close).toHaveBeenCalledTimes(2)
  })

  it('serializes rapid replacements behind the in-flight session close', async () => {
    const pdf = fixture()
    let releaseClose: (() => void) | undefined
    pdf.session.close = vi.fn()
      .mockImplementationOnce(() => new Promise<void>((resolve) => { releaseClose = resolve }))
      .mockResolvedValue(undefined)
    const documentUrl = ref('/first.pdf')
    const canvas = document.createElement('canvas')
    const textLayer = document.createElement('div')
    const viewport = document.createElement('div')
    const Harness = defineComponent({
      setup() {
        usePdfDocument({
          documentUrl,
          initialPage: ref(1),
          initialScale: ref(1.1),
          initialRotation: ref(0),
          sessionFactory: () => pdf.session,
          getSurface: () => ({ canvas, textLayer, viewport }),
          clearSelection: vi.fn(),
          onStateChange: vi.fn(),
        })
        return () => h('div')
      },
    })
    const wrapper = mount(Harness)
    await flushPromises()

    documentUrl.value = '/second.pdf'
    await nextTick()
    documentUrl.value = '/third.pdf'
    await nextTick()
    expect(pdf.session.open).toHaveBeenCalledTimes(1)

    releaseClose?.()
    await flushPromises()
    expect(pdf.session.open).toHaveBeenCalledTimes(2)
    expect(pdf.session.open).toHaveBeenLastCalledWith('/third.pdf')
    wrapper.unmount()
  })
})
