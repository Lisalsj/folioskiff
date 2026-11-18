import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { components } from '@/api/schema'

import { useOnboardingStore } from './store'

type OnboardingOverview = components['schemas']['OnboardingOverview']

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function onboardingOverview(
  currentStep: components['schemas']['OnboardingStep'] = 'storage_privacy',
): OnboardingOverview {
  return {
    state: {
      completed: currentStep === 'complete',
      current_step: currentStep,
      privacy_acknowledged_at: null,
      steps: {
        storage_privacy: currentStep === 'storage_privacy' ? 'pending' : 'completed',
        agents: 'pending',
        sources: 'pending',
        reading_preferences: 'pending',
      },
      updated_at: '2026-08-01T00:00:00Z',
    },
    storage: {
      writable: true,
      state_writable: true,
      cache_writable: true,
      library_writable: true,
      state_root: 'C:\\state',
      cache_root: 'C:\\cache',
      library_root: 'D:\\papers',
      backups_root: 'D:\\papers\\backups',
    },
    agents: [],
    sources: [],
    retrieval: {
      lexical_enabled: true,
      semantic_enabled: false,
      model_state: 'not_installed',
      degradation_reasons: ['semantic_model_not_installed'],
    },
    blocking_issues: [],
  }
}

describe('onboarding store', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => vi.unstubAllGlobals())

  it('loads setup state without invoking an active probe', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init })
      return json(onboardingOverview())
    }))

    const store = useOnboardingStore()

    expect(await store.load()).toBe(true)
    expect(store.currentStep).toBe('storage_privacy')
    expect(store.blockingIssues).toEqual([])
    expect(calls).toHaveLength(1)
    expect(calls[0]?.url).toBe('/api/v1/onboarding')
    expect(calls[0]?.init?.method).toBeUndefined()
  })

  it('uses a fresh idempotency key for each explicit probe', async () => {
    const requests: RequestInit[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe('/api/v1/onboarding/probe')
      requests.push(init ?? {})
      return json(onboardingOverview('agents'))
    }))

    const store = useOnboardingStore()
    await store.probe()
    await store.probe()

    const keys = requests.map((init) => new Headers(init.headers).get('Idempotency-Key'))
    expect(requests.every((init) => init.method === 'POST')).toBe(true)
    expect(keys[0]).toBeTruthy()
    expect(keys[1]).toBeTruthy()
    expect(keys[0]).not.toBe(keys[1])
  })

  it('merges a saved step snapshot without discarding capabilities', async () => {
    const overview = onboardingOverview()
    const saved = {
      ...overview.state,
      current_step: 'agents' as const,
      steps: { ...overview.state.steps, storage_privacy: 'completed' as const },
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/v1/onboarding') return json(overview)
      expect(String(input)).toBe('/api/v1/onboarding/steps/storage_privacy')
      expect(JSON.parse(String(init?.body))).toEqual({ status: 'completed' })
      return json(saved)
    }))
    const store = useOnboardingStore()
    await store.load()

    expect(await store.saveStep('storage_privacy', 'completed')).toBe(true)
    expect(store.currentStep).toBe('agents')
    expect(store.overview?.storage.writable).toBe(true)
  })

  it('maps stable API codes and never retains reset confirmation text', async () => {
    const bodies: unknown[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)))
      if (String(input).endsWith('/data-reset')) {
        return json({ request_id: '1f1c528b-b2a7-44bb-8106-4861136138af', restart_required: true }, 202)
      }
      return json({ detail: { code: 'onboarding_transition_invalid' } }, 409)
    }))
    const store = useOnboardingStore()

    expect(await store.saveStep('agents', 'completed')).toBe(false)
    expect(store.error).toBe('请先完成当前必需步骤，再继续配置。')
    expect(await store.requestDataReset('REBUILD PAPER-READER DATA')).toEqual({
      request_id: '1f1c528b-b2a7-44bb-8106-4861136138af',
      restart_required: true,
    })
    expect(bodies).toEqual([
      { status: 'completed' },
      { confirmation: 'REBUILD PAPER-READER DATA' },
    ])
    expect(JSON.stringify(store.$state)).not.toContain('REBUILD PAPER-READER DATA')
  })

  it('stages runtime paths before requesting a controlled restart', async () => {
    const requests: Array<{ url: string; body: unknown }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      requests.push({
        url,
        body: init?.body ? JSON.parse(String(init.body)) : null,
      })
      if (url.endsWith('/restart')) {
        return json({
          request_id: 'a9d442c9-026e-4a15-a1f9-48f1fd7f0bee',
          restart_required: true,
        }, 202)
      }
      return json({
        state_root: 'C:\\state',
        cache_root: 'D:\\cache',
        library_root: 'D:\\papers',
        backups_root: 'D:\\papers\\backups',
        restart_required: true,
      })
    }))
    const store = useOnboardingStore()

    const staged = await store.saveRuntimePaths({
      cache_root: 'D:\\cache',
      library_root: 'D:\\papers',
    })
    const restart = await store.requestRuntimeRestart()

    expect(staged?.restart_required).toBe(true)
    expect(store.runtimeRestartRequired).toBe(true)
    expect(restart?.restart_required).toBe(true)
    expect(requests).toEqual([
      {
        url: '/api/v1/onboarding/runtime-config',
        body: { cache_root: 'D:\\cache', library_root: 'D:\\papers' },
      },
      { url: '/api/v1/onboarding/runtime-config/restart', body: null },
    ])
  })
})
