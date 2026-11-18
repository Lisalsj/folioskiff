import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AgentSettingsPage from './AgentSettingsPage.vue'
import SettingsLayoutPage from './SettingsLayoutPage.vue'
import SourcesSettingsPage from './SourcesSettingsPage.vue'
import { useSettingsDirtyState } from './settings-dirty-state'

const Root = defineComponent({ template: '<RouterView />' })

async function mountSettings(path: string) {
  const Placeholder = defineComponent({ template: '<h2 data-settings-heading tabindex="-1">占位</h2>' })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/settings',
        component: SettingsLayoutPage,
        children: [
          { path: 'overview', component: Placeholder },
          { path: 'agents', component: AgentSettingsPage },
          { path: 'sources', component: SourcesSettingsPage },
          { path: 'reading', component: Placeholder },
          { path: 'recommendation', component: Placeholder },
          { path: 'storage', component: Placeholder },
          { path: 'extensions', component: Placeholder },
          { path: 'about', component: Placeholder },
        ],
      },
    ],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(Root, {
    attachTo: document.body,
    global: { plugins: [createPinia(), router] },
  })
  await flushPromises()
  return { router, wrapper }
}

describe('SettingsLayoutPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it('renders one settings section and identifies it in the side navigation', async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify({
      state: {
        completed: true,
        current_step: 'complete',
        steps: {
          storage_privacy: 'completed',
          agents: 'completed',
          sources: 'completed',
          reading_preferences: 'completed',
        },
        privacy_acknowledged_at: '2026-08-01T00:00:00Z',
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
        degradation_reasons: [],
      },
      blocking_issues: [],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetcher)

    const { wrapper } = await mountSettings('/settings/agents')

    expect(wrapper.find('nav[aria-label="设置分类"]').exists()).toBe(true)
    expect(wrapper.get('[aria-current="page"]').text()).toBe('AI 与模型')
    expect(wrapper.findComponent(AgentSettingsPage).exists()).toBe(true)
    expect(wrapper.findComponent(SourcesSettingsPage).exists()).toBe(false)
    expect(fetcher.mock.calls.some(([input]) => String(input).includes('/settings/sources')))
      .toBe(false)
    wrapper.unmount()
  })

  it('blocks route changes only while a child section reports unsaved changes', async () => {
    const DirtyChild = defineComponent({
      setup() {
        const dirty = useSettingsDirtyState()
        return { markDirty: () => dirty.setDirty(true) }
      },
      template: '<button data-test="mark-dirty" @click="markDirty">修改</button>',
    })
    const Outside = defineComponent({ template: '<h1>外部页面</h1>' })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/settings',
          component: SettingsLayoutPage,
          children: [{ path: 'dirty', component: DirtyChild }],
        },
        { path: '/outside', component: Outside },
      ],
    })
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    await router.push('/settings/dirty')
    await router.isReady()
    const wrapper = mount(Root, { global: { plugins: [router] } })

    await wrapper.get('[data-test="mark-dirty"]').trigger('click')
    await router.push('/outside')
    await flushPromises()

    expect(confirm).toHaveBeenCalledOnce()
    expect(router.currentRoute.value.path).toBe('/settings/dirty')
    wrapper.unmount()
  })

  it('keeps the leave guard active until every child form is clean', async () => {
    const ContributorA = defineComponent({
      setup() {
        const dirty = useSettingsDirtyState()
        return {
          clear: dirty.clearDirty,
          mark: () => dirty.setDirty(true),
        }
      },
      template: '<button data-test="mark-a" @click="mark">修改 A</button><button data-test="clear-a" @click="clear">保存 A</button>',
    })
    const ContributorB = defineComponent({
      setup() {
        const dirty = useSettingsDirtyState()
        return { mark: () => dirty.setDirty(true) }
      },
      template: '<button data-test="mark-b" @click="mark">修改 B</button>',
    })
    const DirtyChild = defineComponent({
      components: { ContributorA, ContributorB },
      template: '<ContributorA /><ContributorB />',
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/settings',
          component: SettingsLayoutPage,
          children: [{ path: 'dirty', component: DirtyChild }],
        },
        { path: '/outside', component: defineComponent({ template: '<h1>外部页面</h1>' }) },
      ],
    })
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    await router.push('/settings/dirty')
    await router.isReady()
    const wrapper = mount(Root, { global: { plugins: [router] } })

    await wrapper.get('[data-test="mark-a"]').trigger('click')
    await wrapper.get('[data-test="mark-b"]').trigger('click')
    await wrapper.get('[data-test="clear-a"]').trigger('click')
    await router.push('/outside')
    await flushPromises()

    expect(confirm).toHaveBeenCalledOnce()
    expect(router.currentRoute.value.path).toBe('/settings/dirty')
    wrapper.unmount()
  })
})
