import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import RecommendationCard from './RecommendationCard.vue'

const score = (value: number, coverage: number, confidence: number, id: string) => ({
  snapshot_id: id,
  rule_version: `${id}-v1`,
  score: value,
  coverage,
  confidence,
})

const paper = {
  item_id: 'item-1',
  work_id: 'work-1',
  scored_work_id: 'scored-work-1',
  title: 'Reliable Diffusion Models for Cancer Imaging',
  authors: ['Alice Chen', 'Ravi Kumar'],
  doi: '10.1000/reliable',
  sources: ['arxiv', 'europe_pmc'],
  source_updated_at: '2026-07-14T00:00:00Z',
  default_position: 1,
  selected: true,
  historical_match: false,
  eligible_by_default: false,
  channel: 'exploration',
  scores: {
    relevance: score(88, 90, 84, 'relevance'),
    quality: score(50, 0, 0, 'quality'),
    freshness: score(72, 100, 100, 'freshness'),
    preference: score(65, 70, 60, 'preference'),
    composite: score(79, 55, 60, 'composite'),
  },
  evidence_summary: {
    keyword_eligible: true,
    preference_suppressed: false,
    eligible_by_default: false,
    historical_match: false,
    ineligibility_reasons: [],
    exploration_reasons: ['different_source'],
    integrity_status: 'retracted',
    integrity_coverage: 100,
    integrity_warning_codes: ['retracted', 'expression_of_concern'],
  },
  warnings: ['retracted', 'expression_of_concern'],
  library_state: {
    work_id: 'work-1',
    favorite: false,
    feedback: 'neutral',
    feedback_reasons: [],
    pinned: false,
    reading_progress: 0,
    analysis_state: 'not_analyzed',
    persisted: false,
    created_at: null,
    updated_at: null,
  },
}

describe('RecommendationCard', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('separates scores from evidence coverage and never renders unknown quality as zero', () => {
    vi.stubGlobal('fetch', vi.fn())
    const wrapper = mount(RecommendationCard, { props: { paper } })

    expect(wrapper.text()).toContain(paper.title)
    expect(wrapper.text()).toContain('Alice Chen、Ravi Kumar')
    expect(wrapper.get('[data-test="score-relevance"]').text()).toContain('88')
    expect(wrapper.get('[data-test="score-quality"]').text()).toContain('未知')
    expect(wrapper.get('[data-test="score-quality"]').text()).not.toContain('0')
    expect(wrapper.get('[data-test="score-confidence"]').text()).toContain('60')
    expect(wrapper.get('[data-test="score-coverage"]').text()).toContain('55')
    expect(wrapper.get('[data-test="integrity-warning"]').text()).toContain('已撤稿')
    expect(wrapper.get('[data-test="integrity-warning"]').text()).toContain('关注声明')
    expect(wrapper.get('[data-test="exploration-reason"]').text()).toContain('不同来源')
  })

  it('only emits an analysis intent when clicked and does not create an Agent job', async () => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(RecommendationCard, { props: { paper } })

    await wrapper.get('[data-test="analyze-paper"]').trigger('click')

    expect(wrapper.emitted('analyze')).toEqual([[paper]])
    expect(fetcher).not.toHaveBeenCalled()
  })
})
