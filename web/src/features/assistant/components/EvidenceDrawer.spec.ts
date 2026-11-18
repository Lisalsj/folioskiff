import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import EvidenceDrawer from './EvidenceDrawer.vue'

describe('EvidenceDrawer', () => {
  it('shows the bounded scope, immutable fingerprints and warnings on demand', async () => {
    const wrapper = mount(EvidenceDrawer, {
      props: {
        currentScopeLabel: '当前页，第 2 页',
        context: {
          id: 'snapshot-1',
          work_id: 'work-1',
          document_version_id: 'document-1',
          document_content_sha256: 'a'.repeat(64),
          scope: 'current_page',
          page_numbers: [2],
          selection: null,
          annotation_ids: [],
          section_fallback: false,
          extraction_version: 'fixture',
          warnings: ['ocr_gap'],
          evidence_manifest: {},
          evidence_sha256: 'b'.repeat(64),
          snapshot_sha256: 'c'.repeat(64),
          created_at: '2026-08-01T00:00:00Z',
        },
      },
    })

    await wrapper.get('[data-test="assistant-evidence-trigger"]').trigger('click')
    expect(wrapper.text()).toContain('当前页，第 2 页')
    expect(wrapper.text()).toContain('ocr_gap')
    expect(wrapper.text()).toContain('aaaaaaaaaaaa')
    expect(wrapper.text()).not.toContain('a'.repeat(64))
  })
})
