import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { routes } from '@/router'

describe('legacy settings entry route', () => {
  it('redirects the stable settings URL to the routed overview', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/settings')
    await router.isReady()

    expect(router.currentRoute.value.path).toBe('/settings/overview')
  })
})
