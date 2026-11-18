import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Route } from '@playwright/test'

type SetupStep = 'storage_privacy' | 'agents' | 'sources' | 'reading_preferences'
type StepStatus = 'pending' | 'completed' | 'skipped'

const stepOrder: readonly SetupStep[] = [
  'storage_privacy',
  'agents',
  'sources',
  'reading_preferences',
]

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function expectNoAxeViolations(page: Page): Promise<void> {
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
}

async function installFirstRunFixture(page: Page) {
  const requests: Array<{ method: string; path: string; body: unknown }> = []
  const snapshot: {
    completed: boolean
    current_step: SetupStep | 'complete'
    privacy_acknowledged_at: string | null
    steps: Record<SetupStep, StepStatus>
    updated_at: string
  } = {
    completed: false,
    current_step: 'storage_privacy',
    privacy_acknowledged_at: null,
    steps: {
      storage_privacy: 'pending',
      agents: 'pending',
      sources: 'pending',
      reading_preferences: 'pending',
    },
    updated_at: '2026-08-01T00:00:00Z',
  }
  const storage = {
    writable: true,
    state_writable: true,
    cache_writable: true,
    library_writable: true,
    state_root: 'C:\\paper-reader-test\\state',
    cache_root: 'C:\\paper-reader-test\\cache',
    library_root: 'C:\\paper-reader-test\\library',
    backups_root: 'C:\\paper-reader-test\\library\\backups',
  }
  const agents = [{
    backend_id: 'codex_cli',
    display_name: 'Codex CLI',
    location: 'cloud',
    version: '0.101.0',
    probe_code: 'configuration_invalid',
    recommended_action: '请修正 Codex 配置后重新检测。',
    installed: true,
    authenticated: false,
    available: false,
    supports_manual_model_id: true,
    supports_default_model: true,
    models: [],
    degradation_reasons: ['configuration_invalid'],
  }]
  const sources = [
    ['arxiv', 'arXiv', 'none', true],
    ['openreview', 'OpenReview', 'none', true],
    ['europe_pmc', 'Europe PMC', 'none', true],
    ['ieee', 'IEEE Xplore', 'api_key', false],
    ['springer_nature', 'Springer Nature', 'api_key', false],
  ].map(([connectorId, displayName, requirement, enabled]) => ({
    connector_id: connectorId,
    display_name: displayName,
    enabled,
    credential_requirement: requirement,
    credential_configured: false,
    disabled_reason: enabled ? null : 'missing_api_key',
    setup_url: enabled ? null : 'https://example.invalid/official-setup',
  }))
  let preferences = {
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
    updated_at: '2026-08-01T00:00:00Z',
  }

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    const body = request.postData() ? request.postDataJSON() : null
    requests.push({ method, path, body })

    if (path === '/api/v1/events') return route.abort()
    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/onboarding' && method === 'GET') {
      return json(route, {
        state: snapshot,
        storage,
        agents,
        sources,
        retrieval: {
          lexical_enabled: true,
          semantic_enabled: false,
          model_state: 'not_installed',
          degradation_reasons: ['embedding_model_not_enabled'],
        },
        blocking_issues: [],
      })
    }
    if (path === '/api/v1/onboarding/runtime-config' && method === 'PUT') {
      return json(route, { ...storage, restart_required: false })
    }
    if (path.startsWith('/api/v1/onboarding/steps/') && method === 'PUT') {
      const step = path.split('/').at(-1) as SetupStep
      const status = (body as { status: StepStatus }).status
      snapshot.steps[step] = status
      if (step === 'storage_privacy') snapshot.privacy_acknowledged_at = '2026-08-01T00:01:00Z'
      const next = stepOrder.find((item) => snapshot.steps[item] === 'pending')
      snapshot.completed = next === undefined
      snapshot.current_step = next ?? 'complete'
      snapshot.updated_at = '2026-08-01T00:01:00Z'
      return json(route, snapshot)
    }
    if (path === '/api/v1/settings/preferences' && method === 'GET') {
      return json(route, preferences)
    }
    if (path === '/api/v1/settings/preferences' && method === 'PUT') {
      preferences = {
        ...preferences,
        ...(body as typeof preferences),
        resolved_timezone: 'Asia/Shanghai',
        updated_at: '2026-08-01T00:02:00Z',
      }
      return json(route, preferences)
    }
    if (path === '/api/v1/briefing' && method === 'GET') {
      return json(route, {
        generated_at: '2026-08-01T00:03:00Z',
        empty_state: {
          active: true,
          title: '从一条研究意图开始',
          description: '确认关键词后，paper-reader 才会检索论文。',
          primary_action: 'create_plan',
        },
        plans: [],
        new_papers: [],
        important_revisions: [],
        pending_catchups: [],
        active_jobs: [],
        failed_jobs: [],
      })
    }
    return json(route, { detail: { code: 'unexpected_e2e_request' } }, 404)
  })
  return { requests, snapshot }
}

test('first run finishes without a model or paid source key', async ({ page }) => {
  const fixture = await installFirstRunFixture(page)

  await page.goto('/')
  await expect(page).toHaveURL(/\/onboarding$/)
  await expect(page.getByRole('heading', { level: 2, name: '确认存储与隐私边界' })).toBeVisible()
  await expectNoAxeViolations(page)

  await page.getByLabel(/我已了解本地与云端数据边界/).check()
  await page.getByRole('button', { name: '保存并继续' }).click()
  await expect(page.getByRole('heading', { level: 2, name: '检查 AI 能力' })).toBeFocused()
  await expect(page.getByText('configuration_invalid', { exact: true })).toBeVisible()
  await expectNoAxeViolations(page)

  await page.getByRole('button', { name: '稍后配置' }).click()
  await expect(page.getByRole('heading', { level: 2, name: '检查论文来源' })).toBeFocused()
  await expect(page.getByText('arXiv', { exact: true })).toBeVisible()
  await expect(page.getByText('Europe PMC', { exact: true })).toBeVisible()
  await expectNoAxeViolations(page)

  await page.getByRole('button', { name: '稍后配置' }).click()
  await expect(page.getByRole('heading', { level: 2, name: '设置阅读偏好' })).toBeFocused()
  await expect(page.getByLabel('翻译源语言')).toHaveValue('en')
  await expect(page.getByLabel('翻译目标语言')).toHaveValue('zh-CN')
  await expectNoAxeViolations(page)

  await page.getByRole('button', { name: '完成配置' }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('heading', { level: 1, name: '阅读简报' })).toBeVisible()

  expect(fixture.snapshot.steps).toEqual({
    storage_privacy: 'completed',
    agents: 'skipped',
    sources: 'skipped',
    reading_preferences: 'completed',
  })
  expect(fixture.requests.filter((request) => request.path.includes('/credential'))).toEqual([])
  expect(fixture.requests.filter((request) => /analysis|conversation|comparison/.test(request.path)))
    .toEqual([])
})
