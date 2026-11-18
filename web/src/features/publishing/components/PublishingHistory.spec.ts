import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PublishingHistory from './PublishingHistory.vue'

const failed = {
  id: 'publish-failed', kind: 'publishing.publish', status: 'failed', progress: 100,
  payload: {}, result: { snapshot_id: 'snapshot-1', retryable: true, worktree_path: 'D:\\private' },
  checkpoint: { phase: 'failed' }, error_code: 'push_rejected', error_message: 'D:\\private',
  cancel_requested: false, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
}

describe('PublishingHistory', () => {
  it('renders a safe failure and emits retry without exposing private diagnostics', async () => {
    const wrapper = mount(PublishingHistory, {
      props: { jobs: [failed], publishing: false },
    })
    expect(wrapper.text()).toContain('远端发布分支已经变化')
    expect(wrapper.text()).not.toContain('D:\\private')
    await wrapper.get('[data-test="retry-publication-publish-failed"]').trigger('click')
    expect(wrapper.emitted('retry')?.[0]).toEqual([failed])
  })
})
