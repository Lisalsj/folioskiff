import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PublishingPreview from './PublishingPreview.vue'

describe('PublishingPreview', () => {
  it('keeps the public allowlist and private exclusions visible before confirmation', async () => {
    const wrapper = mount(PublishingPreview, {
      props: {
        publishing: false,
        envelope: {
          preview: {
            selection: {
              display_language: 'zh-CN', slug: 'picks', title: 'Picks', public_intro: '', works: [],
            },
            fingerprint: 'a'.repeat(64),
            payload: { schema_version: 'paper-reader-publication-v1', slug: 'picks', title: 'Picks', public_intro: '', works: [] },
          },
          git: {
            enabled: true, reason: null, git_available: true, repository_detected: true,
            repository_root: 'D:\\repo', remote_name: 'origin',
            remote_url: 'https://github.com/Lisalsj/folioskiff.git', branch: 'published',
            site_root: 'site', workflow_path: '.github/workflows/pages.yml',
            target_fingerprint: 'b'.repeat(64), stale_worktree_count: 0,
          },
          files: ['site/index.html'],
          included_fields: ['curated title and public introduction'],
          excluded_fields: ['annotations, private notes and reading progress'],
        },
      },
    })
    expect(wrapper.text()).toContain('curated title and public introduction')
    expect(wrapper.text()).toContain('annotations, private notes and reading progress')
    await wrapper.get('[data-test="open-publish-confirmation"]').trigger('click')
    expect(wrapper.emitted('open-confirmation')).toHaveLength(1)
  })
})
