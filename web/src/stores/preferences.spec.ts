import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  THEME_CACHE_KEY,
  applyCachedTheme,
  readCachedTheme,
  usePreferencesStore,
} from './preferences'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const systemSnapshot = {
  timezone_mode: 'system',
  timezone_name: null,
  system_timezone: 'America/New_York',
  system_timezone_fallback: false,
  resolved_timezone: 'America/New_York',
  theme: 'system',
  default_agent_backend: 'codex_cli',
  default_agent_model_id: null,
  default_reasoning_effort: 'medium',
  report_language: 'zh-CN',
  translation_source_language: 'en',
  translation_target_language: 'zh-CN',
  updated_at: '2026-07-15T08:00:00Z',
}

describe('preferences store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
  })

  afterEach(() => vi.unstubAllGlobals())

  it('applies only a closed cached theme before any API request', () => {
    window.localStorage.setItem(THEME_CACHE_KEY, 'dark')
    expect(applyCachedTheme()).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')

    window.localStorage.setItem(THEME_CACHE_KEY, 'sepia')
    expect(readCachedTheme()).toBe('system')
    expect(applyCachedTheme()).toBe('system')
    expect(document.documentElement.dataset.theme).toBe('system')
  })

  it('calibrates from the server and changes facts only after a successful save', async () => {
    let finishSave: ((response: Response) => void) | undefined
    const bodies: unknown[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/v1/settings/preferences' && !init?.method) {
        return json({ ...systemSnapshot, theme: 'light' })
      }
      if (url === '/api/v1/settings/preferences' && init?.method === 'PUT') {
        bodies.push(JSON.parse(String(init.body)))
        return new Promise<Response>((resolve) => { finishSave = resolve })
      }
      throw new Error(`Unexpected request ${url}`)
    }))
    const store = usePreferencesStore()

    await store.load()
    expect(store.theme).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    const saving = store.save({
      timezone_mode: 'custom',
      timezone_name: 'Europe/Berlin',
      theme: 'dark',
      default_agent_backend: 'codex_cli',
      default_agent_model_id: null,
      default_reasoning_effort: 'medium',
      report_language: 'zh-CN',
      translation_source_language: 'en',
      translation_target_language: 'zh-CN',
    })
    expect(store.theme).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    finishSave?.(json({
      ...systemSnapshot,
      timezone_mode: 'custom',
      timezone_name: 'Europe/Berlin',
      resolved_timezone: 'Europe/Berlin',
      theme: 'dark',
    }))
    await saving

    expect(bodies).toEqual([{
      timezone_mode: 'custom',
      timezone_name: 'Europe/Berlin',
      theme: 'dark',
      default_agent_backend: 'codex_cli',
      default_agent_model_id: null,
      default_reasoning_effort: 'medium',
      report_language: 'zh-CN',
      translation_source_language: 'en',
      translation_target_language: 'zh-CN',
    }])
    expect(store.resolvedTimezone).toBe('Europe/Berlin')
    expect(store.theme).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(window.localStorage.getItem(THEME_CACHE_KEY)).toBe('dark')
  })

  it('cycles system, light, and dark themes and rolls back a failed save', async () => {
    let savedTheme = 'system'
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/v1/settings/preferences' && !init?.method) {
        return json({ ...systemSnapshot, theme: savedTheme })
      }
      if (url === '/api/v1/settings/preferences' && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { theme: string }
        if (body.theme === 'system') throw new Error('offline')
        savedTheme = body.theme
        return json({ ...systemSnapshot, theme: savedTheme })
      }
      throw new Error(`Unexpected request ${url}`)
    }))
    const store = usePreferencesStore()

    await store.load()
    expect(store.theme).toBe('system')
    await store.cycleTheme()
    expect(store.theme).toBe('light')
    await store.cycleTheme()
    expect(store.theme).toBe('dark')
    expect(window.localStorage.getItem(THEME_CACHE_KEY)).toBe('dark')

    expect(await store.cycleTheme()).toBe(false)
    expect(store.theme).toBe('dark')
    expect(window.localStorage.getItem(THEME_CACHE_KEY)).toBe('dark')
  })
})
