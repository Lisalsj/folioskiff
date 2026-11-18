import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AppShell from './AppShell.vue'

describe('AppShell', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('offers a keyboard bypass and focuses main content after route changes', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(AppShell, {
      attachTo: document.body,
      slots: {
        default: '<h1>Page content</h1><button data-test="page-filter">Filter</button>',
      },
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    const bypass = wrapper.get('[data-test="skip-to-content"]')
    const main = wrapper.get('#main-content')
    expect(bypass.attributes('href')).toBe('#main-content')
    expect(main.attributes('tabindex')).toBe('-1')

    await bypass.trigger('click')
    expect(document.activeElement).toBe(main.element)

    await router.push('/plans')
    await flushPromises()
    expect(document.activeElement).toBe(main.element)

    const pageFilter = wrapper.get<HTMLButtonElement>('[data-test="page-filter"]')
    pageFilter.element.focus()
    await router.replace({ path: '/plans', query: { view: 'quality' } })
    await flushPromises()
    expect(document.activeElement).toBe(pageFilter.element)

    wrapper.unmount()
  })

  it('presents the reading briefing and the six primary destinations', async () => {
    const fetcher = vi.fn(async () =>
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(AppShell, {
      slots: { default: '<h1>阅读简报</h1>' },
      global: {
        plugins: [createPinia(), router],
      },
    })
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('阅读简报')
    expect(wrapper.findAll('[data-test="desktop-primary-nav"] a').map((link) => link.text())).toEqual([
      '阅读简报',
      '搜索论文',
      '关注计划',
      '我的论文库',
      '多论文比较',
      '精选发布',
      '设置',
    ])
    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/jobs',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
    wrapper.unmount()
  })

  it('exposes four labeled mobile controls and moves secondary destinations into More', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })),
    )
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(AppShell, {
      attachTo: document.body,
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    const controls = wrapper.findAll(
      '[data-test="mobile-primary-nav"] a, [data-test="mobile-more"]',
    )
    expect(controls.map((control) => control.text().trim())).toEqual([
      '简报',
      '计划',
      '论文库',
      '更多',
    ])
    expect(wrapper.find('[data-test="mobile-primary-nav"] a[href="/comparisons"]').exists())
      .toBe(false)
    expect(wrapper.find('[data-test="mobile-primary-nav"] a[href="/publishing"]').exists())
      .toBe(false)
    expect(wrapper.find('[data-test="mobile-primary-nav"] a[href="/settings"]').exists())
      .toBe(false)

    const more = wrapper.get<HTMLButtonElement>('[data-test="mobile-more"]')
    more.element.focus()
    await more.trigger('click')
    await flushPromises()
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')
    expect(dialog?.textContent).toContain('多论文比较')
    expect(dialog?.querySelector('a[href="/search"]')).toBeTruthy()
    expect(dialog?.textContent).toContain('诊断与支持')
    const close = document.querySelector<HTMLButtonElement>('[aria-label="关闭更多功能"]')
    close?.click()
    await flushPromises()
    expect(document.activeElement).toBe(more.element)
    wrapper.unmount()
  })

  it('marks reader routes as an immersive workspace without redundant shell navigation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })),
    )
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{
        path: '/library/work/read/document',
        component: { template: '<div />' },
        meta: { workspace: 'reader' },
      }],
    })
    await router.push('/library/work/read/document')
    await router.isReady()
    const wrapper = mount(AppShell, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    expect(wrapper.get('.app-shell').classes()).toContain('is-reader-workspace')
    expect(wrapper.get('#main-content').classes()).toContain('reader-main')
    wrapper.unmount()
  })

  it('exposes a persisted three-state theme control in the shell header', async () => {
    const snapshot = {
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
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/v1/jobs') return new Response(JSON.stringify([]), { status: 200 })
      if (url === '/api/v1/settings/preferences' && !init?.method) {
        return new Response(JSON.stringify(snapshot), { status: 200 })
      }
      if (url === '/api/v1/settings/preferences' && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { theme: string }
        return new Response(JSON.stringify({ ...snapshot, theme: body.theme }), { status: 200 })
      }
      throw new Error(`Unexpected request ${url}`)
    })
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(AppShell, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    const toggle = wrapper.get<HTMLButtonElement>('[data-test="theme-toggle"]')
    expect(toggle.attributes('aria-label')).toBe('切换到浅色主题')
    await toggle.trigger('click')
    await flushPromises()
    expect(toggle.attributes('aria-label')).toBe('切换到深色主题')
    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/settings/preferences',
      expect.objectContaining({ method: 'PUT' }),
    )
    wrapper.unmount()
  })
})
