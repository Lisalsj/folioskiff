import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Route, type TestInfo } from '@playwright/test'

import { fulfillCompletedOnboarding } from './support'

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

const score = (value: number, coverage: number, confidence: number, id: string) => ({
  snapshot_id: id,
  rule_version: `${id}-v1`,
  score: value,
  coverage,
  confidence,
})

const recommendation = {
  run_id: 'run-e2e',
  keyword_version_id: 'keywords-e2e',
  recommendation_set_id: 'set-e2e',
  recommendation_set_version: 1,
  set_sha256: 'a'.repeat(64),
  view: 'composite',
  available_views: ['composite', 'relevance', 'quality', 'newest', 'preference'],
  fixed_historical_months: false,
  requested_limit: 20,
  candidate_cap: 50,
  candidate_count: 32,
  recommendation_count: 1,
  exploration_target: 4,
  exploration_count: 1,
  degradation_reasons: [],
  created_at: '2026-07-15T05:00:00Z',
  items: [{
    item_id: 'item-e2e',
    work_id: 'work-e2e',
    scored_work_id: 'scored-e2e',
    title: 'Evidence-Aware Agents for Scientific Reading',
    authors: ['Lin Chen', 'Avery Singh'],
    doi: '10.1000/recommendation.e2e',
    sources: ['arxiv', 'openreview'],
    source_updated_at: '2026-07-14T00:00:00Z',
    default_position: 1,
    selected: true,
    historical_match: false,
    eligible_by_default: true,
    channel: 'exploration',
    scores: {
      relevance: score(93, 100, 92, 'relevance-e2e'),
      quality: score(50, 0, 25, 'quality-e2e'),
      freshness: score(84, 100, 100, 'freshness-e2e'),
      preference: score(71, 72, 65, 'preference-e2e'),
      composite: score(85, 72, 69, 'composite-e2e'),
    },
    evidence_summary: {
      keyword_eligible: true,
      preference_suppressed: false,
      eligible_by_default: true,
      historical_match: false,
      ineligibility_reasons: [],
      exploration_reasons: ['new_author'],
      integrity_status: 'retracted',
      integrity_coverage: 100,
      integrity_warning_codes: ['retracted'],
    },
    warnings: [],
    library_state: {
      work_id: 'work-e2e',
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
  }],
  historical_matches: [],
}

const capability = {
  backend_id: 'codex_cli',
  display_name: 'Codex CLI',
  location: 'cloud',
  installed: true,
  authenticated: true,
  available: true,
  supports_schema: true,
  supports_streaming: true,
  supports_vision: false,
  supports_external_research: true,
  credential_required: false,
  credential_configured: false,
  supports_manual_model_id: true,
  supports_default_model: true,
  models: [{
    model_id: 'fixture-model',
    display_name: 'Fixture model',
    supports_vision: false,
    context_window: 128_000,
  }],
  degradation_reasons: [],
}

const preferences = {
  timezone_mode: 'system',
  timezone_name: null,
  system_timezone: 'Asia/Shanghai',
  system_timezone_fallback: false,
  resolved_timezone: 'Asia/Shanghai',
  theme: 'system',
  default_agent_backend: 'codex_cli',
  default_agent_model_id: null,
  default_reasoning_effort: 'medium',
  report_language: 'zh-CN',
  translation_source_language: 'en',
  translation_target_language: 'zh-CN',
  updated_at: '2026-07-15T05:00:00Z',
}

const sourceCapabilities = [
  {
    connector_id: 'arxiv',
    display_name: 'arXiv',
    credential_requirement: 'none',
    supports_exact_updated_date: false,
    enabled: true,
    disabled_reason: null,
    credential_configured: false,
    setup_url: 'https://info.arxiv.org/help/api/',
    protocol_version: '1',
  },
  {
    connector_id: 'openreview',
    display_name: 'OpenReview',
    credential_requirement: 'none',
    supports_exact_updated_date: true,
    enabled: true,
    disabled_reason: null,
    credential_configured: false,
    setup_url: 'https://docs.openreview.net/',
    protocol_version: '1',
  },
]

const suggestedDefinition = {
  required: [
    {
      key: 'research_agent',
      label: '研究智能体',
      terms: ['AI agent', 'research agent', '研究智能体'],
    },
    {
      key: 'paper_reading',
      label: '论文阅读',
      terms: ['paper reading', 'scientific literature', '论文阅读'],
    },
  ],
  boost: [
    {
      key: 'evidence',
      label: '证据约束',
      terms: ['evidence grounded', 'citation tracing', '证据溯源'],
    },
    {
      key: 'reliability',
      label: '可靠性',
      terms: ['reliability', 'verification', '可靠性'],
    },
    {
      key: 'diffusion',
      label: '扩散模型邻域',
      terms: ['diffusion model', 'generative model', '扩散模型'],
    },
  ],
  excluded: [
    {
      key: 'chatbot',
      label: '普通聊天机器人',
      terms: ['chatbot', 'generic assistant', '普通聊天机器人'],
    },
  ],
}

function keywordVersion(
  id: string,
  researchIntent: string,
  definition = suggestedDefinition,
  generatorInfo: Record<string, unknown> = { mode: 'agent' },
) {
  return {
    id,
    research_intent: researchIntent,
    parent_version_id: null,
    definition,
    change_summary: {},
    generator_info: generatorInfo,
    created_at: '2026-07-15T05:10:00Z',
    confirmed_at: null,
  }
}

function neutralLibraryState(workId: string) {
  return {
    work_id: workId,
    favorite: false,
    feedback: 'neutral',
    feedback_reasons: [],
    pinned: false,
    reading_progress: 0,
    analysis_state: 'not_analyzed',
    persisted: false,
    created_at: null,
    updated_at: null,
  }
}

function recommendationItem(
  workId: string,
  title: string,
  position: number,
  preferenceScore: number,
  qualityCoverage = 84,
) {
  return {
    ...recommendation.items[0],
    item_id: `item-${workId}`,
    work_id: workId,
    scored_work_id: `scored-${workId}`,
    title,
    doi: `10.1000/${workId}`,
    default_position: position,
    channel: position === 2 ? 'exploration' : 'priority',
    scores: {
      relevance: score(position === 1 ? 94 : 82, 100, 94, `relevance-${workId}`),
      quality: score(qualityCoverage > 0 ? (position === 1 ? 79 : 88) : 50, qualityCoverage, qualityCoverage > 0 ? 78 : 25, `quality-${workId}`),
      freshness: score(position === 1 ? 86 : 93, 100, 100, `freshness-${workId}`),
      preference: score(preferenceScore, 78, 72, `preference-${workId}`),
      composite: score(position === 1 ? 88 : 84, qualityCoverage > 0 ? 86 : 36, qualityCoverage > 0 ? 82 : 39, `composite-${workId}`),
    },
    evidence_summary: {
      ...recommendation.items[0].evidence_summary,
      exploration_reasons: position === 2 ? ['adjacent_topic'] : [],
      integrity_status: 'normal',
      integrity_warning_codes: [],
    },
    library_state: neutralLibraryState(workId),
  }
}

function recommendationResult(
  runId: string,
  view: string,
  items: ReturnType<typeof recommendationItem>[],
  degradationReasons: string[] = [],
) {
  return {
    ...recommendation,
    run_id: runId,
    keyword_version_id: `keywords-${runId}`,
    recommendation_set_id: `set-${runId}`,
    set_sha256: runId.padEnd(64, '0').slice(0, 64),
    view,
    candidate_count: 11,
    recommendation_count: items.length,
    exploration_count: items.filter((item) => item.channel === 'exploration').length,
    degradation_reasons: degradationReasons,
    items,
  }
}

function batch(
  id: string,
  connectorId: string,
  status: 'succeeded' | 'failed',
  options: { newWorkCount: number; updateCount: number; retryable?: boolean },
) {
  return {
    id,
    connector_id: connectorId,
    month: '2026-07',
    status,
    checkpoint: {},
    fetched_count: status === 'succeeded' ? 12 : 0,
    accepted_count: status === 'succeeded' ? 5 : 0,
    ingested_count: status === 'succeeded' ? 3 : 0,
    scanned_count: status === 'succeeded' ? 12 : 0,
    new_work_count: options.newWorkCount,
    update_count: options.updateCount,
    duplicate_count: status === 'succeeded' ? 2 : 0,
    attempt_count: 1,
    retryable: options.retryable ?? false,
    retry_after_seconds: options.retryable ? 30 : null,
    error_code: status === 'failed' ? 'rate_limited' : null,
    error_message: status === 'failed' ? 'OpenReview 暂时限流' : null,
    query_fingerprint: `${connectorId}-2026-07`,
    completed_at: '2026-07-15T05:31:00Z',
  }
}

async function auditAdaptiveModes(page: Page, testInfo: TestInfo): Promise<void> {
  const standard = await new AxeBuilder({ page }).analyze()
  expect(standard.violations).toEqual([])

  if (testInfo.project.name === 'desktop-chromium') {
    for (const theme of ['light', 'dark'] as const) {
      await page.evaluate((value) => { document.documentElement.dataset.theme = value }, theme)
      const themed = await new AxeBuilder({ page }).analyze()
      expect(themed.violations, `${theme} theme accessibility`).toEqual([])
    }
    await page.evaluate(() => { delete document.documentElement.dataset.theme })
    await page.emulateMedia({ colorScheme: 'dark' })
    const systemTheme = await new AxeBuilder({ page }).analyze()
    expect(systemTheme.violations, 'system theme accessibility').toEqual([])
    await page.emulateMedia({ colorScheme: 'light' })
  }

  if (testInfo.project.name === 'medium-chromium') {
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
    await expect.poll(() => page.evaluate(() => ({
      viewport: window.innerWidth,
      content: document.documentElement.scrollWidth,
    }))).toEqual(expect.objectContaining({ viewport: 900, content: 900 }))
    const enlarged = await new AxeBuilder({ page }).analyze()
    expect(enlarged.violations, '200% text accessibility').toEqual([])
    await page.evaluate(() => { document.documentElement.style.fontSize = '' })
  }

  if (testInfo.project.name === 'mobile-chromium') {
    await page.emulateMedia({ reducedMotion: 'reduce', forcedColors: 'active' })
    const adapted = await new AxeBuilder({ page }).analyze()
    expect(adapted.violations, 'reduced motion and forced colors accessibility').toEqual([])
    await page.emulateMedia({ reducedMotion: 'no-preference', forcedColors: 'none' })
  }
}

test('empty recommendations explain source and filter failures without starting tasks', async ({ page }, testInfo) => {
  const calls: string[] = []
  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    calls.push(`${method} ${path}`)
    if (path === '/api/v1/jobs') return json(route, [])
    if (path === '/api/v1/runs/run-e2e/recommendations') {
      return json(route, {
        ...recommendation, candidate_count: 0, recommendation_count: 0, exploration_count: 0,
        items: [], historical_matches: [], degradation_reasons: ['insufficient_eligible_candidates'],
      })
    }
    if (path === '/api/v1/runs/run-e2e') {
      return json(route, {
        id: 'run-e2e', status: 'partial_success', months: ['2026-09'], failed_batches: 1,
        batches: [
          {
            ...batch('arxiv-zero', 'arxiv', 'succeeded', { newWorkCount: 0, updateCount: 0 }),
            month: '2026-09', fetched_count: 32, accepted_count: 0,
            checkpoint: { stop_reason: 'source_exhausted' },
            filter_counts: { evaluated_count: 32, missing_updated_at_count: 0,
              outside_date_range_count: 31, in_date_range_count: 1, keyword_mismatch_count: 1, accepted_count: 0 },
          },
          {
            ...batch('openreview-failed', 'openreview', 'failed', { newWorkCount: 0, updateCount: 0 }),
            month: '2026-09', error_code: 'permanent_request', error_message: 'HTTP 400',
          },
        ],
      })
    }
    return json(route, { detail: `Unhandled fixture route: ${method} ${path}` }, 500)
  })
  await page.goto('/runs/run-e2e/results')
  const details = page.getByRole('region', { name: '为什么没有候选论文？' })
  await expect(details).toContainText('部分成功')
  await expect(details).toContainText('日期外 31')
  await expect(details).toContainText('关键词不符 1')
  await expect(details).toContainText('来源拒绝检索参数')
  await expect(page.getByRole('button', { name: '重新评分' })).toBeDisabled()
  await expect(details.getByRole('link', { name: '返回关注计划' })).toHaveAttribute('href', '/plans')
  await auditAdaptiveModes(page, testInfo)
  expect(calls.every((call) => call.startsWith('GET '))).toBe(true)
  expect(calls.some((call) => call.includes('/analysis/') || call.includes('/keywords/suggestions'))).toBe(false)
})

test('recommendations preserve score uncertainty, feedback facts and click-only analysis', async ({
  page,
}) => {
  const analysisCalls: string[] = []
  let savedFeedback = 'neutral'
  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()

    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/runs/run-e2e/recommendations' && method === 'GET') {
      return json(route, { ...recommendation, view: url.searchParams.get('view') ?? 'composite' })
    }
    if (path === '/api/v1/library/works/work-e2e' && method === 'PUT') {
      const body = request.postDataJSON() as Record<string, unknown>
      savedFeedback = String(body.feedback)
      return json(route, {
        work_id: 'work-e2e',
        ...body,
        persisted: true,
        created_at: '2026-07-15T05:01:00Z',
        updated_at: '2026-07-15T05:01:00Z',
      })
    }
    if (path.includes('/api/v1/analysis/')) analysisCalls.push(`${method} ${path}`)
    if (path === '/api/v1/analysis/works/work-e2e/reports' && method === 'GET') {
      return json(route, [])
    }
    if (path === '/api/v1/settings/agents' && method === 'GET') return json(route, [capability])
    if (path === '/api/v1/analysis/works/work-e2e/preflight' && method === 'POST') {
      return json(route, {
        work_id: 'work-e2e',
        document_version_id: null,
        document_content_sha256: null,
        evidence_level: 'abstract_only',
        evidence_sha256: 'b'.repeat(64),
        coverage: 35,
        backend_id: 'codex_cli',
        backend_display_name: 'Codex CLI',
        backend_location: 'cloud',
        cloud_backend: true,
        model_id: 'fixture-model',
        model_options: capability.models,
        manual_model_id_allowed: true,
        default_model_allowed: true,
        reasoning_effort: 'high',
        service_tier: 'flex',
        language: 'zh-CN',
        template_version_id: 'builtin-general-v1',
        template_name: '通用论文分析',
        external_research: false,
        scope: ['abstract', 'evidence:abstract'],
        warnings: ['全文尚不可用；报告只使用摘要证据。'],
        ready: true,
        prompt_sha256: 'c'.repeat(64),
        preflight_fingerprint: 'd'.repeat(64),
      })
    }
    return json(route, { detail: `Unhandled fixture route: ${method} ${path}` }, 500)
  })

  await page.goto('/runs/run-e2e/results')
  await expect(page.getByRole('heading', { level: 1, name: '推荐结果' })).toBeVisible()
  await expect(page.getByText('Evidence-Aware Agents for Scientific Reading')).toBeVisible()
  await expect(page.locator('[data-test="score-quality"] dd')).toHaveText('未知')
  await expect(page.locator('[data-test="integrity-warning"]')).toContainText('已撤稿')
  await expect(page.locator('[data-test="integrity-warning"]')).toContainText('不会被高分抵消')
  expect(analysisCalls).toEqual([])

  await page.locator('[data-test="feedback-like"]').click()
  await page.locator('[data-test="feedback-reason-strong_evidence"]').check()
  await page.locator('[data-test="save-feedback"]').click()
  await expect(page.locator('[data-test="feedback-status"]')).toContainText('评价已保存')
  expect(savedFeedback).toBe('like')
  expect(analysisCalls).toEqual([])

  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])

  await page.locator('[data-test="analyze-paper"]').click()
  await expect(page.getByRole('dialog', { name: '分析前预检' })).toBeVisible()
  await expect(page.getByText('仅摘要证据')).toBeVisible()
  expect(analysisCalls).toContain('GET /api/v1/analysis/works/work-e2e/reports')
  expect(analysisCalls).toContain('POST /api/v1/analysis/works/work-e2e/preflight')
  expect(analysisCalls.some((call) => call === 'POST /api/v1/analysis/works/work-e2e/reports')).toBe(false)
})

test('an explicit preference closes the intent, discovery, recommendation and next-suggestion loop', async ({
  page,
}, testInfo) => {
  const intent = '研究可验证的论文阅读智能体，并关注扩散模型证据，排除普通聊天机器人'
  const nextIntent = '继续追踪可验证引用链与论文阅读智能体'
  const suggestionBodies: Array<Record<string, unknown>> = []
  const rankingViews: string[] = []
  const analysisCalls: string[] = []
  let seedLiked = false
  let mainLiked = false
  let createdPlan: Record<string, unknown> | undefined
  let manualVersionBody: Record<string, unknown> | undefined

  const nextDefinition = {
    ...suggestedDefinition,
    required: [
      {
        ...suggestedDefinition.required[0],
        terms: ['evidence trace agent', 'verifiable research agent', '可验证研究智能体'],
      },
      suggestedDefinition.required[1],
    ],
  }
  const seedPaper = recommendationItem(
    'work-seed-diffusion',
    'Diffusion Evidence for Reliable Scientific Agents',
    1,
    76,
  )

  function mainItems(view: string) {
    const agentPaper = recommendationItem(
      'work-loop-agent',
      'Evidence-Aware Agents for Scientific Reading',
      1,
      mainLiked ? 92 : 61,
    )
    const adjacentPaper = recommendationItem(
      'work-loop-diffusion',
      'Preference-Aligned Diffusion Readers',
      2,
      81,
    )
    if (view === 'quality' || view === 'newest') return [adjacentPaper, agentPaper]
    if (view === 'preference' && !mainLiked) return [adjacentPaper, agentPaper]
    return [agentPaper, adjacentPaper]
  }

  const partialRun = {
    id: 'run-loop-e2e',
    plan_id: 'plan-loop-e2e',
    keyword_version_id: 'keywords-manual-e2e',
    job_id: 'job-run-loop-e2e',
    status: 'partial',
    months: ['2026-07'],
    total_batches: 2,
    succeeded_batches: 1,
    failed_batches: 1,
    cancelled_batches: 0,
    connector_versions: { arxiv: '1', openreview: '1' },
    created_at: '2026-07-15T05:30:00Z',
    started_at: '2026-07-15T05:30:01Z',
    completed_at: '2026-07-15T05:31:00Z',
    batches: [
      batch('batch-arxiv-loop', 'arxiv', 'succeeded', { newWorkCount: 2, updateCount: 1 }),
      batch('batch-openreview-loop', 'openreview', 'failed', { newWorkCount: 0, updateCount: 0, retryable: true }),
    ],
  }

  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()

    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === '/api/v1/settings/preferences' && method === 'GET') return json(route, preferences)
    if (path === '/api/v1/settings/sources' && method === 'GET') return json(route, sourceCapabilities)
    if (path === '/api/v1/works/merge-reviews' && method === 'GET') return json(route, [])
    if (path === '/api/v1/plans/catchups' && method === 'GET') return json(route, [])

    if (path === '/api/v1/runs/seed-preference/recommendations' && method === 'GET') {
      const view = url.searchParams.get('view') ?? 'composite'
      return json(route, recommendationResult('seed-preference', view, [seedPaper]))
    }
    if (path === '/api/v1/library/works/work-seed-diffusion' && method === 'PUT') {
      const body = request.postDataJSON() as Record<string, unknown>
      seedLiked = body.feedback === 'like'
      return json(route, {
        work_id: 'work-seed-diffusion',
        ...body,
        persisted: true,
        created_at: '2026-07-15T05:03:00Z',
        updated_at: '2026-07-15T05:03:00Z',
      })
    }
    if (path === '/api/v1/library/works/work-loop-agent' && method === 'PUT') {
      const body = request.postDataJSON() as Record<string, unknown>
      mainLiked = body.feedback === 'like'
      return json(route, {
        work_id: 'work-loop-agent',
        ...body,
        persisted: true,
        created_at: '2026-07-15T05:42:00Z',
        updated_at: '2026-07-15T05:42:00Z',
      })
    }

    if (path === '/api/v1/keywords/preference-context' && method === 'GET') {
      const terms = mainLiked
        ? [
            { term: 'evidence-aware agents', weight: 100, sources: ['liked'] },
            { term: 'citation tracing', weight: 88, sources: ['liked'] },
          ]
        : seedLiked
          ? [{ term: 'diffusion evidence', weight: 100, sources: ['liked'] }]
          : []
      return json(route, {
        version: 'preference-context-v1',
        terms,
        source_categories: terms.length ? ['liked'] : [],
        contributing_work_count: terms.length ? 1 : 0,
        context_sha256: (mainLiked ? 'f' : seedLiked ? 'e' : '0').repeat(64),
        empty: terms.length === 0,
      })
    }
    if (path === '/api/v1/keywords/suggestions' && method === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>
      suggestionBodies.push(body)
      const isNext = body.research_intent === nextIntent
      return json(
        route,
        keywordVersion(
          isNext ? 'keywords-next-e2e' : 'keywords-agent-e2e',
          String(body.research_intent),
          isNext ? nextDefinition : suggestedDefinition,
          {
            mode: 'agent',
            preference_context: {
              enabled: body.use_preference_context,
              sha256: body.preference_context_sha256,
              term_count: isNext ? 2 : 1,
            },
          },
        ),
        201,
      )
    }
    if (path === '/api/v1/keywords/versions' && method === 'POST') {
      manualVersionBody = request.postDataJSON() as Record<string, unknown>
      return json(route, {
        id: 'keywords-manual-e2e',
        research_intent: intent,
        parent_version_id: 'keywords-agent-e2e',
        definition: manualVersionBody.definition,
        change_summary: {
          added_terms: { research_agent: ['evidence agent'] },
          removed_terms: { research_agent: ['AI agent'] },
        },
        generator_info: manualVersionBody.generator_info,
        created_at: '2026-07-15T05:12:00Z',
        confirmed_at: null,
      }, 201)
    }
    if (path === '/api/v1/keywords/versions/keywords-manual-e2e/confirm' && method === 'POST') {
      return json(route, {
        id: 'keywords-manual-e2e',
        research_intent: intent,
        parent_version_id: 'keywords-agent-e2e',
        definition: manualVersionBody?.definition ?? suggestedDefinition,
        change_summary: {
          added_terms: { research_agent: ['evidence agent'] },
          removed_terms: { research_agent: ['AI agent'] },
        },
        generator_info: { backend: 'manual_edit', intent_used_for_filtering: false },
        created_at: '2026-07-15T05:12:00Z',
        confirmed_at: '2026-07-15T05:13:00Z',
      })
    }

    if (path === '/api/v1/plans/preview' && method === 'POST') {
      return json(route, {
        id: 'job-preview-loop',
        kind: 'discovery.preview',
        status: 'queued',
        progress: 0,
        cancel_requested: false,
      }, 202)
    }
    if (path === '/api/v1/jobs/job-preview-loop' && method === 'GET') {
      return json(route, {
        id: 'job-preview-loop',
        kind: 'discovery.preview',
        status: 'succeeded',
        progress: 100,
        cancel_requested: false,
        payload: {},
        checkpoint: null,
        error_code: null,
        error_message: null,
        created_at: '2026-07-15T05:20:00Z',
        updated_at: '2026-07-15T05:20:01Z',
        result: {
          plan_id: 'draft:loop',
          keyword_version_id: 'keywords-manual-e2e',
          months: ['2026-07'],
          warnings: [],
          sources: [
            {
              connector_id: 'arxiv',
              display_name: 'arXiv',
              status: 'ready',
              estimate: 23,
              estimate_exact: false,
              samples: [{
                external_id: '2607.10001v2',
                title: 'Evidence-Aware Agents for Scientific Reading',
                authors: ['Lin Chen', 'Avery Singh'],
                updated_at: '2026-07-14T00:00:00Z',
              }],
              keyword_impact: {
                remote_candidates: 31,
                in_date_range: 27,
                matched_confirmed_keywords: 23,
              },
              warnings: ['estimate_before_local_updated_filter'],
              error_code: null,
              error_message: null,
              retryable: false,
              setup_url: null,
            },
            {
              connector_id: 'openreview',
              display_name: 'OpenReview',
              status: 'failed',
              estimate: null,
              estimate_exact: false,
              samples: [],
              keyword_impact: null,
              warnings: [],
              error_code: 'rate_limited',
              error_message: '来源预览暂时限流，仍可保存并在运行后重试。',
              retryable: true,
              setup_url: null,
            },
          ],
        },
      })
    }
    if (path === '/api/v1/plans' && method === 'POST') {
      createdPlan = request.postDataJSON() as Record<string, unknown>
      return json(route, {
        id: 'plan-loop-e2e',
        ...createdPlan,
        created_at: '2026-07-15T05:25:00Z',
        warnings: [],
      }, 201)
    }
    if (path === '/api/v1/plans/plan-loop-e2e/runs' && method === 'POST') {
      return json(route, {
        id: 'job-run-loop-e2e',
        kind: 'discovery.run',
        status: 'queued',
        progress: 0,
        cancel_requested: false,
      }, 202)
    }
    if (path === '/api/v1/plans' && method === 'GET') {
      return json(route, createdPlan ? [{
        id: 'plan-loop-e2e',
        ...createdPlan,
        created_at: '2026-07-15T05:25:00Z',
        warnings: [],
      }] : [])
    }
    if (path === '/api/v1/plans/plan-loop-e2e/runs' && method === 'GET') {
      return json(route, { items: [partialRun], limit: 3, offset: 0, total: 1 })
    }
    if (path === '/api/v1/runs/run-loop-e2e/recommendations' && method === 'GET') {
      const view = url.searchParams.get('view') ?? 'composite'
      rankingViews.push(view)
      return json(route, recommendationResult('run-loop-e2e', view, mainItems(view)))
    }

    if (path === '/api/v1/briefing' && method === 'GET') {
      return json(route, {
        generated_at: '2026-07-15T05:45:00Z',
        empty_state: {
          active: false,
          reason: null,
          title: null,
          description: null,
          primary_action: null,
        },
        plans: [{
          id: 'plan-loop-e2e',
          name: String(createdPlan?.name ?? '可验证论文智能体月报'),
          research_intent: intent,
          sources: ['arxiv', 'openreview'],
          schedule_frequency: 'manual',
          enabled: true,
          latest_run: {
            id: 'run-loop-e2e',
            status: 'partial',
            months: ['2026-07'],
            recommendation_set_id: 'set-run-loop-e2e',
            recommendation_set_version: 1,
            recommendation_count: 2,
            created_at: '2026-07-15T05:30:00Z',
            completed_at: '2026-07-15T05:31:00Z',
          },
        }],
        new_papers: [{
          plan_id: 'plan-loop-e2e',
          run_id: 'run-loop-e2e',
          recommendation_set_id: 'set-run-loop-e2e',
          work_id: 'work-loop-agent',
          scored_work_id: 'scored-work-loop-agent',
          title: 'Evidence-Aware Agents for Scientific Reading',
          authors: ['Lin Chen', 'Avery Singh'],
          position: 1,
          channel: 'priority',
          relevance_score: 94,
          quality_score: 79,
          composite_score: 88,
          coverage: 86,
          confidence: 82,
          warnings: [],
        }],
        important_revisions: [{
          id: 'update-loop-e2e',
          run_id: 'run-loop-e2e',
          work_id: 'work-existing-loop',
          title: 'A Previously Seen Paper with New Evidence',
          authors: ['Mina Rao'],
          source: 'arxiv',
          changed_fields: ['abstract', 'document'],
          source_updated_at: '2026-07-14T00:00:00Z',
          detected_at: '2026-07-15T05:31:00Z',
          state: 'unread',
          has_readable_document: true,
        }],
        pending_catchups: [],
        active_jobs: [],
        failed_jobs: [],
      })
    }

    if (path.includes('/api/v1/analysis/')) analysisCalls.push(`${method} ${path}`)
    if (path === '/api/v1/analysis/works/work-loop-agent/reports' && method === 'GET') {
      return json(route, [])
    }
    if (path === '/api/v1/settings/agents' && method === 'GET') return json(route, [capability])
    if (path === '/api/v1/analysis/works/work-loop-agent/preflight' && method === 'POST') {
      return json(route, {
        work_id: 'work-loop-agent',
        document_version_id: null,
        document_content_sha256: null,
        evidence_level: 'abstract_only',
        evidence_sha256: '1'.repeat(64),
        coverage: 35,
        backend_id: 'codex_cli',
        backend_display_name: 'Codex CLI',
        backend_location: 'cloud',
        cloud_backend: true,
        model_id: 'fixture-model',
        model_options: capability.models,
        manual_model_id_allowed: true,
        default_model_allowed: true,
        reasoning_effort: 'high',
        service_tier: 'flex',
        language: 'zh-CN',
        template_version_id: 'builtin-general-v1',
        template_name: '通用论文分析',
        external_research: false,
        scope: ['abstract', 'evidence:abstract'],
        warnings: ['全文尚不可用；报告只使用摘要证据。'],
        ready: true,
        prompt_sha256: '2'.repeat(64),
        preflight_fingerprint: '3'.repeat(64),
      })
    }

    return json(route, { detail: `Unhandled fixture route: ${method} ${path}` }, 500)
  })

  await page.goto('/runs/seed-preference/results')
  const seedCard = page.locator('.recommendation-card').filter({ hasText: seedPaper.title })
  await seedCard.locator('[data-test="feedback-like"]').click()
  await seedCard.locator('[data-test="feedback-reason-strong_evidence"]').check()
  await seedCard.locator('[data-test="save-feedback"]').click()
  await expect(seedCard.locator('[data-test="feedback-status"]')).toContainText('评价已保存')
  expect(seedLiked).toBe(true)
  expect(analysisCalls).toEqual([])

  await page.goto('/plans/new')
  await expect(page.locator('[data-test="preference-context"]')).toContainText('diffusion evidence')
  const preferenceToggle = page.locator('[data-test="use-preference-context"]')
  await preferenceToggle.focus()
  await page.keyboard.press('Space')
  await expect(preferenceToggle).not.toBeChecked()
  await expect(page.locator('[data-test="preference-context"]')).toContainText('本次不会发送')
  await page.keyboard.press('Space')
  await expect(preferenceToggle).toBeChecked()
  await expect(page.locator('[data-test="preference-context"]')).toContainText('只辅助建议')

  await page.getByLabel('计划名称').fill('可验证论文智能体月报')
  await page.getByLabel('你想研究什么？').fill(intent)
  await page.locator('[data-test="suggest-keywords"]').click()
  await expect(page.getByRole('heading', { level: 1, name: '关键词工作台' })).toBeVisible()
  const termInputs = page.locator('.term-input input')
  await expect(termInputs).toHaveCount(18)
  const suggestedTerms = await termInputs.evaluateAll((inputs) => inputs.map((input) => (input as HTMLInputElement).value))
  expect(suggestedTerms.some((term) => /[\u3400-\u9fff]/u.test(term))).toBe(true)
  expect(suggestedTerms.some((term) => /^[\x00-\x7f]+$/u.test(term))).toBe(true)
  await page.getByLabel('研究智能体关键词 1').fill('evidence agent')
  await page.locator('[data-test="confirm-keywords"]').click()
  await expect(page.getByRole('heading', { level: 1, name: '月份与来源' })).toBeVisible()
  expect(manualVersionBody).toMatchObject({
    parent_version_id: 'keywords-agent-e2e',
    generator_info: { backend: 'manual_edit', intent_used_for_filtering: false },
  })
  expect(JSON.stringify(manualVersionBody?.definition)).toContain('evidence agent')
  expect(suggestionBodies[0]).toMatchObject({
    target_count: 18,
    use_preference_context: true,
    preference_context_sha256: 'e'.repeat(64),
  })

  await page.locator('.source-row').filter({ hasText: 'OpenReview' }).locator('input').check()
  await page.locator('[data-test="preview-sources"]').click()
  await expect(page.getByRole('heading', { level: 1, name: '来源预览' })).toBeVisible()
  await expect(page.getByText('Evidence-Aware Agents for Scientific Reading')).toBeVisible()
  await expect(page.getByText('23 篇')).toBeVisible()
  await expect(page.getByText('来源预览暂时限流，仍可保存并在运行后重试。')).toBeVisible()
  expect(analysisCalls).toEqual([])

  await page.locator('[data-test="save-and-run"]').click()
  await expect(page).toHaveURL(/\/plans$/)
  await page.locator('[data-test="toggle-runs-plan-loop-e2e"]').click()
  await expect(page.locator('[data-test="recent-runs-plan-loop-e2e"]')).toContainText('部分成功')
  await expect(page.locator('[data-test="recent-runs-plan-loop-e2e"]')).toContainText('arxiv 成功')
  await expect(page.locator('[data-test="recent-runs-plan-loop-e2e"]')).toContainText('openreview 待重试')
  await page.locator('[data-test="view-results-run-loop-e2e"]').click()

  await expect(page.getByRole('heading', { level: 1, name: '推荐结果' })).toBeVisible()
  for (const view of ['relevance', 'quality', 'newest', 'preference'] as const) {
    const control = page.locator(`[data-test="ranking-view-${view}"]`)
    await control.click()
    await expect(control).toHaveAttribute('aria-pressed', 'true')
  }
  expect(new Set(rankingViews)).toEqual(new Set(['composite', 'relevance', 'quality', 'newest', 'preference']))
  await expect(page.locator('.recommendation-card h2').first()).toHaveText('Preference-Aligned Diffusion Readers')
  expect(analysisCalls).toEqual([])

  const agentCard = page.locator('.recommendation-card').filter({ hasText: 'Evidence-Aware Agents for Scientific Reading' })
  await agentCard.locator('[data-test="feedback-like"]').click()
  await agentCard.locator('[data-test="feedback-reason-high_relevance"]').check()
  await agentCard.locator('[data-test="save-feedback"]').click()
  await expect(agentCard.locator('[data-test="feedback-status"]')).toContainText('评价已保存')
  expect(mainLiked).toBe(true)
  await page.locator('[data-test="ranking-view-composite"]').click()
  await expect(page.locator('[data-test="ranking-view-composite"]')).toHaveAttribute('aria-pressed', 'true')
  await page.locator('[data-test="ranking-view-preference"]').click()
  await expect(page.locator('[data-test="ranking-view-preference"]')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.recommendation-card h2').first()).toHaveText('Evidence-Aware Agents for Scientific Reading')
  expect(analysisCalls).toEqual([])

  await page.goto('/plans/new')
  await expect(page.locator('[data-test="preference-context"]')).toContainText('evidence-aware agents')
  await expect(page.locator('[data-test="preference-context"]')).toContainText('citation tracing')
  await page.getByLabel('计划名称').fill('下一轮证据追踪')
  await page.getByLabel('你想研究什么？').fill(nextIntent)
  await page.locator('[data-test="suggest-keywords"]').click()
  await expect(page.getByLabel('研究智能体关键词 1')).toHaveValue('evidence trace agent')
  expect(suggestionBodies.at(-1)).toMatchObject({
    research_intent: nextIntent,
    use_preference_context: true,
    preference_context_sha256: 'f'.repeat(64),
  })
  expect(analysisCalls).toEqual([])

  await page.goto('/')
  await expect(page.locator('[data-test="new-papers"]')).toContainText('1 篇')
  await expect(page.locator('[data-test="new-papers"]')).not.toContainText('A Previously Seen Paper with New Evidence')
  await expect(page.locator('[data-test="important-revisions"]')).toContainText('1 项')
  await expect(page.locator('[data-test="important-revisions"]')).toContainText('A Previously Seen Paper with New Evidence')
  expect(analysisCalls).toEqual([])

  await page.goto('/runs/run-loop-e2e/results')
  await expect(page.getByRole('heading', { level: 1, name: '推荐结果' })).toBeVisible()
  await auditAdaptiveModes(page, testInfo)
  expect(analysisCalls).toEqual([])
  const finalAgentCard = page.locator('.recommendation-card').filter({ hasText: 'Evidence-Aware Agents for Scientific Reading' })
  await finalAgentCard.locator('[data-test="analyze-paper"]').click()
  await expect(page.getByRole('dialog', { name: '分析前预检' })).toBeVisible()
  expect(analysisCalls).toContain('GET /api/v1/analysis/works/work-loop-agent/reports')
  expect(analysisCalls).toContain('POST /api/v1/analysis/works/work-loop-agent/preflight')
  expect(analysisCalls).not.toContain('POST /api/v1/analysis/works/work-loop-agent/reports')
})

test('the main discovery flow remains honest without API keys, preference history or a model', async ({
  page,
}) => {
  const intent = '筛选本月与癌症影像分割有关、证据清晰的论文'
  const analysisCalls: string[] = []
  const suggestionBodies: Array<Record<string, unknown>> = []
  let createdPlan: Record<string, unknown> | undefined

  const offlineSources = [
    sourceCapabilities[0],
    {
      ...sourceCapabilities[1],
      credential_requirement: 'api_key',
      enabled: false,
      disabled_reason: 'missing_api_key',
      setup_url: 'https://docs.openreview.net/getting-started/using-the-api',
    },
  ]
  const offlinePaper = recommendationItem(
    'work-offline-cancer',
    'Lexical Evidence for Cancer Image Segmentation',
    1,
    50,
    0,
  )
  const offlineRun = {
    id: 'run-offline-e2e',
    plan_id: 'plan-offline-e2e',
    keyword_version_id: 'keywords-offline-e2e',
    job_id: 'job-run-offline-e2e',
    status: 'succeeded',
    months: ['2026-07'],
    total_batches: 1,
    succeeded_batches: 1,
    failed_batches: 0,
    cancelled_batches: 0,
    connector_versions: { arxiv: '1' },
    created_at: '2026-07-15T06:00:00Z',
    started_at: '2026-07-15T06:00:01Z',
    completed_at: '2026-07-15T06:00:10Z',
    batches: [batch('batch-offline-arxiv', 'arxiv', 'succeeded', { newWorkCount: 1, updateCount: 0 })],
  }

  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()

    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === '/api/v1/settings/preferences' && method === 'GET') return json(route, preferences)
    if (path === '/api/v1/settings/sources' && method === 'GET') return json(route, offlineSources)
    if (path === '/api/v1/settings/agents' && method === 'GET') return json(route, [])
    if (path === '/api/v1/works/merge-reviews' && method === 'GET') return json(route, [])
    if (path === '/api/v1/plans/catchups' && method === 'GET') return json(route, [])
    if (path === '/api/v1/keywords/preference-context' && method === 'GET') {
      return json(route, {
        version: 'preference-context-v1',
        terms: [],
        source_categories: [],
        contributing_work_count: 0,
        context_sha256: '0'.repeat(64),
        empty: true,
      })
    }
    if (path === '/api/v1/keywords/suggestions' && method === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>
      suggestionBodies.push(body)
      return json(route, keywordVersion(
        'keywords-offline-e2e',
        intent,
        {
          ...suggestedDefinition,
          required: [
            { key: 'cancer', label: '癌症影像', terms: ['cancer imaging', 'oncology imaging', '癌症影像'] },
            { key: 'segmentation', label: '图像分割', terms: ['image segmentation', 'tumor delineation', '肿瘤分割'] },
          ],
        },
        {
          mode: 'local_fallback',
          backend: 'local_corpus_statistics',
          model_available: false,
          preference_context: { enabled: false, sha256: null, term_count: 0 },
        },
      ), 201)
    }
    if (path === '/api/v1/keywords/versions/keywords-offline-e2e/confirm' && method === 'POST') {
      const version = keywordVersion('keywords-offline-e2e', intent, {
        ...suggestedDefinition,
        required: [
          { key: 'cancer', label: '癌症影像', terms: ['cancer imaging', 'oncology imaging', '癌症影像'] },
          { key: 'segmentation', label: '图像分割', terms: ['image segmentation', 'tumor delineation', '肿瘤分割'] },
        ],
      }, { mode: 'local_fallback', model_available: false })
      return json(route, { ...version, confirmed_at: '2026-07-15T05:55:00Z' })
    }
    if (path === '/api/v1/plans/preview' && method === 'POST') {
      return json(route, {
        id: 'job-preview-offline',
        kind: 'discovery.preview',
        status: 'queued',
        progress: 0,
        cancel_requested: false,
      }, 202)
    }
    if (path === '/api/v1/jobs/job-preview-offline' && method === 'GET') {
      return json(route, {
        id: 'job-preview-offline',
        kind: 'discovery.preview',
        status: 'succeeded',
        progress: 100,
        cancel_requested: false,
        payload: {},
        checkpoint: null,
        error_code: null,
        error_message: null,
        created_at: '2026-07-15T05:56:00Z',
        updated_at: '2026-07-15T05:56:01Z',
        result: {
          plan_id: 'draft:offline',
          keyword_version_id: 'keywords-offline-e2e',
          months: ['2026-07'],
          warnings: ['semantic_model_unavailable'],
          sources: [{
            connector_id: 'arxiv',
            display_name: 'arXiv',
            status: 'ready',
            estimate: 9,
            estimate_exact: false,
            samples: [{
              external_id: '2607.20002v1',
              title: offlinePaper.title,
              authors: ['Yun Li'],
              updated_at: '2026-07-12T00:00:00Z',
            }],
            keyword_impact: {
              remote_candidates: 18,
              in_date_range: 13,
              matched_confirmed_keywords: 9,
            },
            warnings: ['estimate_before_local_updated_filter'],
            error_code: null,
            error_message: null,
            retryable: false,
            setup_url: null,
          }],
        },
      })
    }
    if (path === '/api/v1/plans' && method === 'POST') {
      createdPlan = request.postDataJSON() as Record<string, unknown>
      return json(route, {
        id: 'plan-offline-e2e',
        ...createdPlan,
        created_at: '2026-07-15T05:58:00Z',
        warnings: [],
      }, 201)
    }
    if (path === '/api/v1/plans/plan-offline-e2e/runs' && method === 'POST') {
      return json(route, {
        id: 'job-run-offline-e2e',
        kind: 'discovery.run',
        status: 'queued',
        progress: 0,
        cancel_requested: false,
      }, 202)
    }
    if (path === '/api/v1/plans' && method === 'GET') {
      return json(route, createdPlan ? [{
        id: 'plan-offline-e2e',
        ...createdPlan,
        created_at: '2026-07-15T05:58:00Z',
        warnings: [],
      }] : [])
    }
    if (path === '/api/v1/plans/plan-offline-e2e/runs' && method === 'GET') {
      return json(route, { items: [offlineRun], limit: 3, offset: 0, total: 1 })
    }
    if (path === '/api/v1/runs/run-offline-e2e/recommendations' && method === 'GET') {
      const view = url.searchParams.get('view') ?? 'composite'
      return json(route, recommendationResult(
        'run-offline-e2e',
        view,
        [offlinePaper],
        ['semantic_embedding_unavailable', 'quality_enrichment_unavailable'],
      ))
    }
    if (path.includes('/api/v1/analysis/')) {
      analysisCalls.push(`${method} ${path}`)
      return json(route, { detail: 'No model is configured' }, 503)
    }
    return json(route, { detail: `Unhandled fixture route: ${method} ${path}` }, 500)
  })

  await page.goto('/plans/new')
  await expect(page.locator('[data-test="preference-context"]')).toContainText('还没有可用的收藏')
  await page.getByLabel('计划名称').fill('癌症影像词法追踪')
  await page.getByLabel('你想研究什么？').fill(intent)
  await page.locator('[data-test="suggest-keywords"]').click()
  await expect(page.getByRole('heading', { level: 1, name: '关键词工作台' })).toBeVisible()
  await expect(page.locator('.term-input input')).toHaveCount(18)
  await expect(page.getByLabel('癌症影像关键词 1')).toHaveValue('cancer imaging')
  expect(suggestionBodies[0]).toMatchObject({
    target_count: 18,
    use_preference_context: false,
    preference_context_sha256: null,
  })
  await page.locator('[data-test="confirm-keywords"]').click()
  await expect(page.getByRole('heading', { level: 1, name: '月份与来源' })).toBeVisible()
  await expect(page.locator('.source-option').filter({ hasText: 'OpenReview' })).toContainText('需要 API key')
  await page.locator('[data-test="preview-sources"]').click()
  await expect(page.getByText(offlinePaper.title)).toBeVisible()
  await expect(page.getByText('9 篇')).toBeVisible()
  await page.locator('[data-test="save-and-run"]').click()
  await page.locator('[data-test="toggle-runs-plan-offline-e2e"]').click()
  await expect(page.locator('[data-test="recent-runs-plan-offline-e2e"]')).toContainText('运行成功')
  await page.locator('[data-test="view-results-run-offline-e2e"]').click()

  await expect(page.getByRole('heading', { level: 1, name: '推荐结果' })).toBeVisible()
  await expect(page.getByText(offlinePaper.title)).toBeVisible()
  await expect(page.locator('[data-test="score-quality"] dd')).toHaveText('未知')
  await expect(page.locator('[data-test="score-coverage"] dd')).toHaveText('36')
  await expect(page.locator('[data-test="score-confidence"] dd')).toHaveText('39')
  await expect(page.getByText(/semantic_embedding_unavailable/)).toBeVisible()
  await expect(page.getByText(/quality_enrichment_unavailable/)).toBeVisible()
  expect(analysisCalls).toEqual([])
  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
})
