import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Route } from '@playwright/test'

import { fulfillCompletedOnboarding } from './support'

const keywordVersion = {
  id: 'keywords-e2e',
  research_intent: '研究可靠的论文阅读智能体，并排除纯聊天机器人',
  parent_version_id: null,
  definition: {
    required: [
      { key: 'agent', label: '研究智能体', terms: ['AI agent', '智能体'] },
      { key: 'reading', label: '论文阅读', terms: ['paper reading', '论文阅读'] },
    ],
    boost: [
      { key: 'reliability', label: '可靠性', terms: ['reliability', '可靠性'] },
    ],
    excluded: [
      { key: 'chatbot', label: '普通聊天机器人', terms: ['chatbot', '聊天机器人'] },
    ],
  },
  change_summary: {},
  generator_info: { backend: 'fixture' },
  created_at: '2026-07-13T00:00:00Z',
  confirmed_at: null,
}

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

test('research intent becomes a confirmed, previewed and running discovery plan', async ({
  page,
}) => {
  let createdPlan: Record<string, unknown> | undefined
  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()

    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/sources' && method === 'GET') {
      return json(route, [
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
          connector_id: 'ieee',
          display_name: 'IEEE Xplore',
          credential_requirement: 'api_key',
          supports_exact_updated_date: true,
          enabled: false,
          disabled_reason: 'missing_api_key',
          credential_configured: false,
          setup_url: 'https://developer.ieee.org/',
          protocol_version: '1',
        },
      ])
    }
    if (path === '/api/v1/keywords/suggestions' && method === 'POST') {
      return json(route, keywordVersion, 201)
    }
    if (path === '/api/v1/keywords/versions/keywords-e2e/confirm' && method === 'POST') {
      return json(route, { ...keywordVersion, confirmed_at: '2026-07-13T00:01:00Z' })
    }
    if (path === '/api/v1/plans/preview' && method === 'POST') {
      return json(
        route,
        {
          id: 'preview-job-e2e',
          kind: 'discovery.preview',
          status: 'queued',
          progress: 0,
          cancel_requested: false,
        },
        202,
      )
    }
    if (path === '/api/v1/jobs/preview-job-e2e' && method === 'GET') {
      return json(route, {
        id: 'preview-job-e2e',
        kind: 'discovery.preview',
        status: 'succeeded',
        progress: 100,
        cancel_requested: false,
        payload: {},
        checkpoint: null,
        error_code: null,
        error_message: null,
        created_at: '2026-07-13T00:02:00Z',
        updated_at: '2026-07-13T00:02:01Z',
        result: {
          plan_id: 'draft:e2e',
          keyword_version_id: 'keywords-e2e',
          months: ['2026-07'],
          warnings: [],
          sources: [
            {
              connector_id: 'arxiv',
              display_name: 'arXiv',
              status: 'ready',
              estimate: 18,
              estimate_exact: false,
              samples: [
                {
                  external_id: '2607.12345v1',
                  title: 'Reliable Agents for Evidence-grounded Paper Reading',
                  authors: ['Alice Researcher', 'Chen Ming'],
                  updated_at: '2026-07-13T00:00:00Z',
                },
              ],
              keyword_impact: {
                remote_candidates: 25,
                in_date_range: 21,
                matched_confirmed_keywords: 18,
              },
              warnings: ['estimate_before_local_updated_filter'],
              error_code: null,
              error_message: null,
              retryable: false,
              setup_url: null,
            },
          ],
        },
      })
    }
    if (path === '/api/v1/plans' && method === 'POST') {
      createdPlan = request.postDataJSON() as Record<string, unknown>
      return json(
        route,
        {
          id: 'plan-e2e',
          ...createdPlan,
          created_at: '2026-07-13T00:03:00Z',
          warnings: [],
        },
        201,
      )
    }
    if (path === '/api/v1/plans/plan-e2e/runs' && method === 'POST') {
      return json(
        route,
        {
          id: 'run-job-e2e',
          kind: 'discovery.run',
          status: 'queued',
          progress: 0,
          cancel_requested: false,
        },
        202,
      )
    }
    if (path === '/api/v1/plans' && method === 'GET') {
      return json(
        route,
        createdPlan
          ? [{ id: 'plan-e2e', ...createdPlan, created_at: '2026-07-13T00:03:00Z', warnings: [] }]
          : [],
      )
    }
    if (path === '/api/v1/plans/catchups' && method === 'GET') return json(route, [])
    if (path === '/api/v1/works/merge-reviews' && method === 'GET') return json(route, [])

    return json(route, { detail: `Unhandled fixture route: ${method} ${path}` }, 500)
  })

  await page.goto('/plans/new')
  await page.getByLabel('计划名称').fill('可靠论文智能体月报')
  await page.getByLabel('你想研究什么？').fill('研究可靠的论文阅读智能体，并排除纯聊天机器人')
  await page.getByRole('button', { name: '生成研究关键词' }).click()

  await expect(page.getByRole('heading', { level: 1, name: '关键词工作台' })).toBeVisible()
  await expect(page.getByLabel('研究智能体关键词 1')).toHaveValue('AI agent')
  await page.getByRole('button', { name: '确认此关键词版本' }).click()

  await page.locator('[data-test="arxiv-categories"]').fill('cs.AI, cs.CL')
  await page.locator('[data-test="schedule-frequency"]').selectOption('weekly')
  await page.locator('[data-test="weekly-day"]').selectOption('2')
  await page.getByRole('button', { name: '预览来源' }).click()

  await expect(page.getByRole('heading', { level: 1, name: '来源预览' })).toBeVisible()
  await expect(page.getByText('Reliable Agents for Evidence-grounded Paper Reading')).toBeVisible()
  await expect(page.getByText('18 篇')).toBeVisible()

  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])

  await page.getByRole('button', { name: '保存并运行' }).click()
  await expect(page).toHaveURL(/\/plans$/)
  await expect(page.getByRole('heading', { level: 1, name: '关注计划' })).toBeVisible()
  await expect(page.getByText('可靠论文智能体月报')).toBeVisible()
  expect(createdPlan).toMatchObject({
    source_filters: { arxiv: { categories: ['cs.AI', 'cs.CL'] } },
    schedule: { frequency: 'weekly', weekly_day: 2 },
  })
})
