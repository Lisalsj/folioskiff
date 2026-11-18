import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import LibraryImportPanel from './LibraryImportPanel.vue'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function element<T extends Element>(selector: string): T {
  const value = document.querySelector<T>(selector)
  if (!value) throw new Error(`Missing element ${selector}`)
  return value
}

function input(selector: string, value: string): void {
  const field = element<HTMLInputElement | HTMLTextAreaElement>(selector)
  field.value = value
  field.dispatchEvent(new Event('input', { bubbles: true }))
}

function chooseFile(file: File): void {
  const field = element<HTMLInputElement>('[data-test="pdf-import-file"]')
  Object.defineProperty(field, 'files', {
    configurable: true,
    value: [file],
  })
  field.dispatchEvent(new Event('change', { bubbles: true }))
}

describe('LibraryImportPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it('requires a lawful PDF and submits bounded metadata as multipart form data', async () => {
    let submitted: RequestInit | undefined
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      submitted = init
      return json({
        work_id: 'work-imported',
        document_version_id: 'document-imported',
        content_sha256: 'a'.repeat(64),
        created_work: true,
        created_document: true,
      }, 201)
    }))
    const wrapper = mount(LibraryImportPanel, {
      attachTo: document.body,
      props: { open: true, targetWork: null },
    })
    await flushPromises()

    const submit = element<HTMLButtonElement>('[data-test="submit-pdf-import"]')
    expect(submit.disabled).toBe(true)
    chooseFile(new File(['plain text'], 'notes.txt', { type: 'text/plain' }))
    await flushPromises()
    expect(document.body.textContent).toContain('请选择 PDF 文件')
    expect(fetch).not.toHaveBeenCalled()

    chooseFile(new File(['%PDF-1.4\nfixture'], 'reader.pdf', { type: 'application/pdf' }))
    input('[data-test="pdf-import-title"]', 'Reliable Reader Agents')
    input('[data-test="pdf-import-authors"]', 'Alice Chen；Bob Kumar')
    input('[data-test="pdf-import-doi"]', '10.1000/reader')
    element<HTMLInputElement>('[data-test="pdf-import-rights"]').click()
    await flushPromises()
    expect(submit.disabled).toBe(false)
    submit.click()
    await flushPromises()

    expect(submitted?.method).toBe('POST')
    expect(submitted?.body).toBeInstanceOf(FormData)
    const form = submitted?.body as FormData
    expect(form.get('title')).toBe('Reliable Reader Agents')
    expect(JSON.parse(String(form.get('authors')))).toEqual(['Alice Chen', 'Bob Kumar'])
    expect(form.get('doi')).toBe('10.1000/reader')
    expect(form.get('rights_confirmed')).toBe('true')
    expect((form.get('file') as File).name).toBe('reader.pdf')
    expect(wrapper.emitted('imported')?.[0]).toEqual([{
      workId: 'work-imported',
      documentVersionId: 'document-imported',
      kind: 'pdf',
    }])
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])
  })

  it('attaches a new version and reports oversized or rejected files without leaking details', async () => {
    let responseStatus = 413
    vi.stubGlobal('fetch', vi.fn(async () => json({
      detail: 'D:\\private\\oversized.pdf implementation detail',
    }, responseStatus)))
    mount(LibraryImportPanel, {
      attachTo: document.body,
      props: {
        open: true,
        targetWork: { id: 'work-existing', title: 'Existing paper' },
      },
    })
    await flushPromises()

    expect(document.body.textContent).toContain('作为新版本加入《Existing paper》')
    chooseFile(new File(['%PDF-1.4\nfixture'], 'revision.pdf', { type: 'application/pdf' }))
    element<HTMLInputElement>('[data-test="pdf-import-rights"]').click()
    await flushPromises()
    element<HTMLButtonElement>('[data-test="submit-pdf-import"]').click()
    await flushPromises()
    expect(document.body.textContent).toContain('PDF 超过 100 MiB')
    expect(document.body.textContent).not.toContain('private')

    responseStatus = 422
    element<HTMLButtonElement>('[data-test="submit-pdf-import"]').click()
    await flushPromises()
    expect(document.body.textContent).toContain('无法导入这份 PDF')
    expect(document.body.textContent).not.toContain('implementation detail')
  })

  it('imports DOI, BibTeX or URL metadata without pretending to download full text', async () => {
    let submitted: RequestInit | undefined
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      submitted = init
      return json({
        work_id: 'work-reference',
        created_work: true,
        kind: 'bibtex',
        normalized_value: 'reader2026',
      }, 201)
    }))
    const wrapper = mount(LibraryImportPanel, {
      attachTo: document.body,
      props: { open: true, targetWork: null },
    })
    await flushPromises()

    element<HTMLButtonElement>('[data-test="import-mode-reference"]').click()
    await flushPromises()
    element<HTMLButtonElement>('[data-test="reference-kind-bibtex"]').click()
    await flushPromises()
    input(
      '[data-test="reference-import-value"]',
      '@article{reader2026, title={Reliable Reader}, author={Alice Chen}}',
    )
    await flushPromises()
    expect(document.body.textContent).toContain('这里只保存引用与来源信息，不会下载正文')
    element<HTMLButtonElement>('[data-test="submit-reference-import"]').click()
    await flushPromises()

    expect(submitted?.method).toBe('POST')
    expect(JSON.parse(String(submitted?.body))).toEqual({
      kind: 'bibtex',
      value: '@article{reader2026, title={Reliable Reader}, author={Alice Chen}}',
      title: null,
      authors: [],
      rights_confirmed: false,
    })
    expect(wrapper.emitted('imported')?.[0]).toEqual([{
      workId: 'work-reference',
      documentVersionId: null,
      kind: 'reference',
    }])
  })
})
