import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiFetch } from './api'

describe('apiFetch', () => {
  beforeEach(() => {
    document.cookie = 'paper_reader_csrf=; Max-Age=0; path=/'
  })

  it('adds the double-submit CSRF proof to local mutations', async () => {
    document.cookie = 'paper_reader_csrf=csrf-proof; path=/'
    const fetcher = vi.fn().mockResolvedValue({ ok: true })

    await apiFetch('/api/v1/jobs/job-1/cancel', { method: 'POST' }, fetcher)

    const [input, init] = fetcher.mock.calls[0]
    expect(input).toBe('/api/v1/jobs/job-1/cancel')
    expect(init.method).toBe('POST')
    expect(init.credentials).toBe('same-origin')
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf-proof')
  })
})
