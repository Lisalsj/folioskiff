import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import JobDrawer from './JobDrawer.vue'
import { useJobStore } from '@/stores/jobs'

describe('JobDrawer', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it('opens an honest empty task workspace', async () => {
    const wrapper = mount(JobDrawer, {
      attachTo: document.body,
      global: { plugins: [createPinia()] },
    })

    await wrapper.get('button[aria-label="查看任务进度"]').trigger('click')
    await nextTick()

    expect(document.body.textContent).toContain('任务进度')
    expect(document.body.textContent).toContain('当前没有任务')
  })

  it('presents a cancelled document acquisition as an isolated readable task', async () => {
    const pinia = createPinia()
    const wrapper = mount(JobDrawer, {
      attachTo: document.body,
      global: { plugins: [pinia] },
    })
    const store = useJobStore(pinia)
    store.jobs = [
      {
        id: 'acquisition-cancelled',
        kind: 'reader.acquire_document',
        status: 'cancelled',
        progress: 35,
        cancel_requested: true,
        pause_requested: false,
      },
    ]

    await wrapper.get('button[aria-label="查看任务进度"]').trigger('click')
    await nextTick()

    expect(document.body.textContent).toContain('获取论文全文')
    expect(document.body.textContent).toContain('已取消')
    expect(document.body.textContent).not.toContain('reader.acquire_document')
    expect(document.body.textContent).not.toContain('正在取消')
  })

  it('names a durable paper conversation reply in plain language', async () => {
    const pinia = createPinia()
    const wrapper = mount(JobDrawer, {
      attachTo: document.body,
      global: { plugins: [pinia] },
    })
    useJobStore(pinia).jobs = [{
      id: 'conversation-running',
      kind: 'conversation.reply',
      status: 'succeeded',
      progress: 100,
      cancel_requested: false,
      pause_requested: false,
    }]

    await wrapper.get('button[aria-label="查看任务进度"]').trigger('click')
    await nextTick()

    expect(document.body.textContent).toContain('回答论文问题')
    expect(document.body.textContent).toContain('已完成')
    expect(document.body.textContent).not.toContain('conversation.reply')
  })

  it('names paper translation without exposing its internal job kind', async () => {
    const pinia = createPinia()
    const wrapper = mount(JobDrawer, {
      attachTo: document.body,
      global: { plugins: [pinia] },
    })
    useJobStore(pinia).jobs = [{
      id: 'translation-running',
      kind: 'translation.generate',
      status: 'running',
      progress: 40,
      cancel_requested: false,
      pause_requested: false,
    }]

    await wrapper.get('button[aria-label="查看任务进度"]').trigger('click')
    await nextTick()

    expect(document.body.textContent).toContain('翻译论文')
    expect(document.body.textContent).not.toContain('translation.generate')
  })

  it('describes backup creation and restart-pending restore without internal names', async () => {
    const pinia = createPinia()
    const wrapper = mount(JobDrawer, {
      attachTo: document.body,
      global: { plugins: [pinia] },
    })
    useJobStore(pinia).jobs = [
      {
        id: 'backup-running',
        kind: 'backup.create',
        status: 'running',
        progress: 42,
        cancel_requested: false,
        pause_requested: false,
      },
      {
        id: 'restore-waiting',
        kind: 'backup.restore.confirm',
        status: 'pending_restart',
        progress: 100,
        cancel_requested: false,
        pause_requested: false,
      },
    ]

    await wrapper.get('button[aria-label="查看任务进度"]').trigger('click')
    await nextTick()

    expect(document.body.textContent).toContain('创建便携备份')
    expect(document.body.textContent).toContain('等待重启恢复')
    expect(document.body.textContent).toContain('等待重启')
    expect(document.body.textContent).not.toContain('backup.create')
    expect(document.body.textContent).not.toContain('backup.restore.confirm')
  })

  it('names curated publication as a user-visible task', async () => {
    const pinia = createPinia()
    const wrapper = mount(JobDrawer, {
      attachTo: document.body,
      global: { plugins: [pinia] },
    })
    useJobStore(pinia).jobs = [{
      id: 'publication-running',
      kind: 'publishing.publish',
      status: 'running',
      progress: 5,
      cancel_requested: false,
      pause_requested: false,
    }]

    await wrapper.get('button[aria-label="查看任务进度"]').trigger('click')
    await nextTick()

    expect(document.body.textContent).toContain('发布精选内容')
    expect(document.body.textContent).not.toContain('publishing.publish')
  })

  it('shows a paused model download without leaking its internal job kind', async () => {
    const pinia = createPinia()
    const wrapper = mount(JobDrawer, {
      attachTo: document.body,
      global: { plugins: [pinia] },
    })
    useJobStore(pinia).jobs = [{
      id: 'model-paused',
      kind: 'models.install',
      status: 'paused',
      progress: 48,
      cancel_requested: false,
      pause_requested: true,
    }]

    await wrapper.get('button[aria-label="查看任务进度"]').trigger('click')
    await nextTick()

    expect(document.body.textContent).toContain('下载模型资源')
    expect(document.body.textContent).toContain('已暂停')
    expect(document.body.textContent).not.toContain('models.install')
  })

  it('offers durable pause and resume controls only for model downloads', async () => {
    let status = 'running'
    const requests: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const url = String(input)
      requests.push(`${method} ${url}`)
      if (url.endsWith('/pause') && method === 'POST') {
        status = 'paused'
        return new Response(JSON.stringify({
          id: 'model-control', kind: 'models.install', status, progress: 51,
          cancel_requested: false, pause_requested: true,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/resume') && method === 'POST') {
        status = 'running'
        return new Response(JSON.stringify({
          id: 'model-control', kind: 'models.install', status, progress: 51,
          cancel_requested: false, pause_requested: false,
        }), { status: 202, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }))
    const pinia = createPinia()
    const wrapper = mount(JobDrawer, {
      attachTo: document.body,
      global: { plugins: [pinia] },
    })
    useJobStore(pinia).jobs = [{
      id: 'model-control',
      kind: 'models.install',
      status,
      progress: 51,
      cancel_requested: false,
      pause_requested: false,
    }]

    await wrapper.get('button[aria-label="查看任务进度"]').trigger('click')
    await nextTick()
    document.querySelector<HTMLButtonElement>('[data-test="pause-model-control"]')?.click()
    await flushPromises()
    expect(document.body.textContent).toContain('已暂停')

    document.querySelector<HTMLButtonElement>('[data-test="resume-model-control"]')?.click()
    await flushPromises()
    expect(document.body.textContent).toContain('进行中')
    expect(requests).toEqual([
      'POST /api/v1/settings/model-assets/jobs/model-control/pause',
      'POST /api/v1/settings/model-assets/jobs/model-control/resume',
    ])
  })
})
