import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, type RouteRecordRaw } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App.vue'
import { createAppRouter, routes } from './router'

describe('application routes', () => {
  beforeEach(() => vi.stubGlobal('scrollTo', vi.fn()))
  afterEach(() => vi.unstubAllGlobals())

  it('announces a descriptive document title after SPA navigation', async () => {
    const router = createAppRouter({ onboardingStatus: async () => true })

    await router.push('/plans')
    await router.isReady()

    expect(document.title).toBe('关注计划 | FolioSkiff')
  })

  it('keeps every routed page behind an asynchronous component boundary', async () => {
    function flatten(records: readonly RouteRecordRaw[]): RouteRecordRaw[] {
      return records.flatMap((record) => [record, ...flatten(record.children ?? [])])
    }

    const components = flatten(routes)
      .map((record) => record.component)
      .filter((component) => component !== undefined)

    expect(components.length).toBeGreaterThan(0)
    for (const component of components) {
      expect(typeof component).toBe('function')
      const loaded = (component as () => unknown)()
      expect(loaded).toBeInstanceOf(Promise)
      await loaded
    }
  })

  it('redirects protected pages while setup is incomplete', async () => {
    const router = createAppRouter({ onboardingStatus: async () => false })

    await router.push('/library')
    await router.isReady()

    expect(router.currentRoute.value.path).toBe('/onboarding')
  })

  it('leaves the onboarding route after setup is complete', async () => {
    const router = createAppRouter({ onboardingStatus: async () => true })

    await router.push('/onboarding')
    await router.isReady()

    expect(router.currentRoute.value.path).toBe('/')
  })

  it('routes probe failures to one recoverable setup page without looping', async () => {
    const status = vi.fn(async () => { throw new Error('offline') })
    const router = createAppRouter({ onboardingStatus: status })

    await router.push('/library')
    await router.isReady()

    expect(router.currentRoute.value.path).toBe('/onboarding')
    expect(router.currentRoute.value.query.status).toBe('unavailable')
    expect(status).toHaveBeenCalledTimes(1)
  })

  it('opens the plans workspace from its stable route', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes,
    })
    await router.push('/plans')
    await router.isReady()

    const wrapper = mount(App, {
      global: { plugins: [createPinia(), router] },
    })

    expect(wrapper.get('h1').text()).toBe('关注计划')
    const desktopNavigation = wrapper.get('[data-test="desktop-primary-nav"]')
    expect(desktopNavigation.get('a[aria-current="page"]').text()).toBe('关注计划')
    expect(desktopNavigation.findAll('a')).toHaveLength(7)
  })

  it('opens the research-intent wizard from the dedicated route', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes,
    })
    await router.push('/plans/new')
    await router.isReady()

    const wrapper = mount(App, {
      global: { plugins: [createPinia(), router] },
    })

    expect(wrapper.get('h1').text()).toBe('研究意图')
    expect(wrapper.get('[data-test="research-intent"]').attributes('placeholder')).toContain(
      '研究问题',
    )
  })
})
