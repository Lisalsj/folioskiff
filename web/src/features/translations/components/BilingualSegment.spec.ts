import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import BilingualSegment from './BilingualSegment.vue'

const segment = {
  segment_id: 'segment-000001',
  segment_index: 0,
  page_number: 3,
  block_numbers: [1],
  source_text: 'Diffusion model',
  source_sha256: 'a'.repeat(64),
  translated_text: '扩散模型',
  source_kind: 'ocr_text',
  ocr_confidence: 0.82,
  status: 'succeeded',
  diagnostic: null,
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
}

describe('BilingualSegment', () => {
  it('aligns source and target by immutable segment id and exposes OCR confidence', () => {
    const wrapper = mount(BilingualSegment, { props: { segment, mode: 'parallel' } })
    const row = wrapper.get('[data-segment="segment-000001"]')
    expect(row.get('[data-side="source"]').text()).toContain('Diffusion model')
    expect(row.get('[data-side="target"]').text()).toContain('扩散模型')
    expect(row.text()).toContain('OCR 82%')
  })
})
