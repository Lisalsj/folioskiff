import { beforeEach, describe, expect, it, vi } from 'vitest'

import { bootstrapLocalSession } from './session'

describe('bootstrapLocalSession', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/')
  })

  it('exchanges the fragment secret and immediately removes it from the address bar', async () => {
    window.history.replaceState({}, '', '/?view=briefing#bootstrap=one-time-secret')
    const fetcher = vi.fn().mockResolvedValue({ ok: true, status: 204 })

    const exchanged = await bootstrapLocalSession({ fetcher })

    expect(exchanged).toBe(true)
    expect(fetcher).toHaveBeenCalledWith('/api/v1/session/bootstrap', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: 'one-time-secret' }),
    })
    expect(window.location.pathname).toBe('/')
    expect(window.location.search).toBe('?view=briefing')
    expect(window.location.hash).toBe('')
  })

  it('does nothing when the launcher secret is absent', async () => {
    const fetcher = vi.fn()

    const exchanged = await bootstrapLocalSession({ fetcher })

    expect(exchanged).toBe(false)
    expect(fetcher).not.toHaveBeenCalled()
  })
})
