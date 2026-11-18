import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Route } from '@playwright/test'

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function installSettingsFixture(page: Page) {
  const requests: string[] = []
  const onboardingOverview = {
    state: {
      completed: true,
      current_step: 'complete',
      privacy_acknowledged_at: '2026-08-01T00:00:00Z',
      steps: {
        storage_privacy: 'completed',
        agents: 'completed',
        sources: 'completed',
        reading_preferences: 'completed',
      },
      updated_at: '2026-08-01T00:00:00Z',
    },
    storage: {
      writable: true,
      state_writable: true,
      cache_writable: true,
      library_writable: true,
      state_root: 'C:\\paper-reader-test\\state',
      cache_root: 'C:\\paper-reader-test\\cache',
      library_root: 'C:\\paper-reader-test\\library',
      backups_root: 'C:\\paper-reader-test\\library\\backups',
    },
    agents: [{
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
    }],
    sources: [
      {
        connector_id: 'arxiv',
        display_name: 'arXiv',
        enabled: true,
        credential_requirement: 'none',
        credential_configured: false,
        disabled_reason: null,
        setup_url: null,
      },
      {
        connector_id: 'ieee',
        display_name: 'IEEE Xplore',
        enabled: false,
        credential_requirement: 'api_key',
        credential_configured: false,
        disabled_reason: 'missing_api_key',
        setup_url: 'https://developer.ieee.org/',
      },
    ],
    retrieval: {
      lexical_enabled: true,
      semantic_enabled: false,
      model_state: 'not_installed',
      degradation_reasons: ['embedding_model_not_enabled'],
    },
    blocking_issues: [],
  }
  const agents = [
    {
      backend_id: 'codex_cli',
      display_name: 'Codex CLI',
      location: 'cloud',
      version: '0.101.0',
      probe_code: 'configuration_invalid',
      recommended_action: '请修正 Codex 配置后重新检测。',
      installed: true,
      authenticated: false,
      available: false,
      supports_schema: true,
      supports_streaming: true,
      supports_vision: true,
      supports_external_research: true,
      credential_required: false,
      credential_configured: false,
      supports_manual_model_id: true,
      supports_default_model: true,
      models: [],
      degradation_reasons: ['configuration_invalid'],
    },
    {
      backend_id: 'ollama_local',
      display_name: 'Ollama Local',
      location: 'local',
      version: null,
      probe_code: 'service_not_running',
      recommended_action: '请先启动本机 Ollama 服务。',
      installed: false,
      authenticated: false,
      available: false,
      supports_schema: false,
      supports_streaming: true,
      supports_vision: false,
      supports_external_research: false,
      credential_required: false,
      credential_configured: false,
      supports_manual_model_id: true,
      supports_default_model: false,
      models: [],
      degradation_reasons: ['service_not_running'],
    },
    {
      backend_id: 'ollama_cloud',
      display_name: 'Ollama Cloud',
      location: 'cloud',
      version: null,
      probe_code: 'not_authenticated',
      recommended_action: '请先配置凭据。',
      installed: true,
      authenticated: false,
      available: false,
      supports_schema: true,
      supports_streaming: true,
      supports_vision: false,
      supports_external_research: false,
      credential_required: true,
      credential_configured: false,
      supports_manual_model_id: true,
      supports_default_model: false,
      models: [],
      degradation_reasons: ['missing_credential'],
    },
  ]
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
    updated_at: '2026-08-01T00:00:00Z',
  }
  const update = {
    current_version: '1.0.0',
    automatic_checks_enabled: false,
    status: 'disabled',
    checked: false,
    last_checked_at: null,
    next_automatic_check_at: null,
    error_code: null,
    release: null,
  }
  const responseFixtures = [onboardingOverview, agents, preferences, update]

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    requests.push(`${method} ${path}`)

    if (path === '/api/v1/events') return route.abort()
    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/onboarding' && method === 'GET') {
      return json(route, onboardingOverview)
    }
    if (path === '/api/v1/backups' && method === 'GET') return json(route, [])
    if (path === '/api/v1/settings/updates' && method === 'GET') return json(route, update)
    if (path === '/api/v1/settings/agents' && method === 'GET') return json(route, agents)
    if (path === '/api/v1/settings/preferences' && method === 'GET') {
      return json(route, preferences)
    }
    return json(route, { detail: { code: 'unexpected_e2e_request' } }, 404)
  })
  return { requests, responseFixtures }
}

test('settings navigation is responsive, focused, and secret-free', async ({ page }) => {
  const fixture = await installSettingsFixture(page)
  const mobile = (page.viewportSize()?.width ?? 1440) <= 640

  await page.goto('/settings/overview')
  await expect(page.getByRole('heading', { level: 2, name: '配置总览' })).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])

  if (mobile) {
    const mobileNavigation = page.getByRole('navigation', { name: '移动主导航' })
    await expect(mobileNavigation).toBeVisible()
    await expect(mobileNavigation.getByRole('link')).toHaveCount(3)
    await expect(mobileNavigation.getByRole('button', { name: '更多' })).toBeVisible()

    const more = mobileNavigation.getByRole('button', { name: '更多' })
    await more.click()
    const dialog = page.getByRole('dialog', { name: '更多功能' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByRole('link')).toHaveCount(5)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await dialog.getByRole('button', { name: '关闭更多功能' }).click()
    await expect(more).toBeFocused()

    await page.getByRole('combobox', { name: '设置分类' }).selectOption('/settings/agents')
  } else {
    const settingsNavigation = page.getByRole('navigation', { name: '设置分类' })
    await settingsNavigation.getByRole('link', { name: 'AI 与模型' }).click()
  }

  await expect(page).toHaveURL(/\/settings\/agents$/)
  await expect(page.getByRole('heading', { level: 2, name: 'AI 与模型' })).toBeFocused()
  await expect(page.locator('#codex_cli code')).toHaveText('configuration_invalid')
  await expect(page.locator('#codex_cli .location-badge')).toHaveText('云端')
  await expect(page.getByLabel('Ollama Cloud API key')).toHaveValue('')
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow).toBeLessThanOrEqual(1)
  expect(JSON.stringify(fixture.responseFixtures)).not.toMatch(
    /"(?:secret|token|api_key|value)"\s*:/i,
  )
  expect(fixture.requests.some((request) => /analysis|conversation|comparison/.test(request)))
    .toBe(false)
  expect(fixture.requests.some((request) => request.includes('/settings/sources'))).toBe(false)
})
