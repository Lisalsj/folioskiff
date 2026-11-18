import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Route } from '@playwright/test'

import {
  buildFixturePdf,
  fulfillCompletedOnboarding,
  fulfillDefaultUserPreferences,
  selectFixturePdfText,
} from './support'

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

const workId = 'work-agent-e2e'
const documentId = 'document-agent-e2e'
const documentHash = 'a'.repeat(64)
const pdf = buildFixturePdf([
  'Evidence bound Agent analysis',
  'selected workspace evidence',
])

const document = {
  id: documentId,
  work_id: workId,
  content_sha256: documentHash,
  access_status: 'user_provided',
  media_type: 'application/pdf',
  completeness: 100,
  extraction_status: 'complete',
  available: true,
  byte_size: pdf.byteLength,
  original_filename: 'agent-evidence.pdf',
  acquisition_kind: 'manual_import',
  last_accessed_at: null,
  created_at: '2026-07-14T08:00:00Z',
  is_representative: true,
}

const work = {
  id: workId,
  title: 'Evidence-Bound Research Agents',
  authors: ['Lin Chen', 'Avery Singh'],
  abstract: 'A local-first workflow for evidence-bound paper analysis.',
  doi: '10.1000/agent.e2e',
  published_at: '2026-07-01T00:00:00Z',
  added_via: 'pdf',
  added_at: '2026-07-14T08:00:00Z',
  favorite: true,
  feedback: 'like',
  pinned: false,
  reading_progress: 0,
  analysis_state: 'not_analyzed',
  document_count: 1,
  representative_document: document,
}

const capabilities = [
  {
    backend_id: 'codex_cli',
    display_name: 'Codex CLI',
    location: 'cloud',
    installed: true,
    authenticated: true,
    available: true,
    supports_schema: true,
    supports_streaming: true,
    supports_vision: true,
    supports_external_research: true,
    credential_required: false,
    credential_configured: true,
    supports_manual_model_id: true,
    supports_default_model: true,
    models: [{
      model_id: 'fixture-vision-model',
      display_name: 'Fixture vision model',
      supports_vision: true,
      context_window: 128_000,
    }],
    degradation_reasons: [],
  },
  {
    backend_id: 'ollama_local',
    display_name: 'Ollama Local',
    location: 'local',
    installed: true,
    authenticated: true,
    available: true,
    supports_schema: true,
    supports_streaming: true,
    supports_vision: false,
    supports_external_research: false,
    credential_required: false,
    credential_configured: true,
    supports_manual_model_id: true,
    supports_default_model: false,
    models: [{
      model_id: 'fixture-text-model',
      display_name: 'Fixture text model',
      supports_vision: false,
      context_window: 32_000,
    }],
    degradation_reasons: [],
  },
]

const preflight = {
  work_id: workId,
  document_version_id: documentId,
  document_content_sha256: documentHash,
  evidence_level: 'full_text',
  evidence_sha256: 'b'.repeat(64),
  coverage: 100,
  backend_id: 'codex_cli',
  backend_display_name: 'Codex CLI',
  backend_location: 'cloud',
  cloud_backend: true,
  model_id: 'fixture-vision-model',
  model_options: capabilities[0]?.models ?? [],
  manual_model_id_allowed: true,
  default_model_allowed: true,
  reasoning_effort: 'high',
  service_tier: 'flex',
  language: 'zh-CN',
  template_version_id: 'builtin-general-v1',
  template_name: '通用论文分析',
  external_research: false,
  scope: ['pages:1', 'annotations:0', 'evidence:full_text', 'visual-pages:1'],
  warnings: [],
  ready: true,
  prompt_sha256: 'c'.repeat(64),
  preflight_fingerprint: 'd'.repeat(64),
}

const reference = {
  evidence_id: 'page:1:native',
  kind: 'page',
  page_number: 1,
  locator: 'Figure 1',
  url: null,
  external_title: null,
}

function reportSummary(version: number) {
  return {
    id: `agent-report-${version}`,
    work_id: workId,
    version,
    job_id: `analysis-job-${version === 1 ? 'success-1' : 'success-2'}`,
    document_version_id: documentId,
    document_content_sha256: documentHash,
    evidence_level: 'full_text',
    evidence_sha256: 'b'.repeat(64),
    backend_id: 'codex_cli',
    requested_model_id: 'fixture-vision-model',
    resolved_model_id: 'fixture-vision-model',
    reasoning_effort: 'high',
    language: 'zh-CN',
    schema_version: 'analysis-report-v1',
    created_at: `2026-07-14T${String(8 + version).padStart(2, '0')}:00:00Z`,
  }
}

function report(version: number) {
  const title = version === 1 ? '证据约束分析 · 第一版' : '证据约束分析 · 第二版'
  const statement = {
    text: `${title}区分了作者主张与 Agent 判断。`,
    references: [reference],
  }
  return {
    ...reportSummary(version),
    evidence_manifest: {
      pages: [{ evidence_id: 'page:1:native', page_number: 1 }],
      visual_pages: [{ page_number: 1, document_content_sha256: documentHash }],
    },
    service_tier: 'flex',
    template_version_id: 'builtin-general-v1',
    complete_prompt: 'frozen fixture prompt',
    prompt_sha256: 'c'.repeat(64),
    network_policy: 'local_evidence_only',
    content: {
      title,
      overview: statement,
      research_question: statement,
      author_claims: [statement],
      contributions: [statement],
      method: [statement],
      assumptions: [statement],
      data_and_experiments: [statement],
      findings: [statement],
      limitations: [statement],
      reproducibility: [statement],
      relation_to_user_intent: [statement],
      agent_judgments: [statement],
    },
    rendered_markdown: `# ${title}`,
    external_citations: [],
    request_fingerprint: 'd'.repeat(64),
    report_sha256: String(version).repeat(64),
  }
}

function analysisJob(
  id: string,
  status: 'succeeded' | 'failed',
  reportVersion?: number,
) {
  return {
    id,
    kind: 'analysis.report',
    status,
    progress: 100,
    payload: { work_id: workId, document_version_id: documentId },
    result: reportVersion
      ? { report_id: `agent-report-${reportVersion}`, version: reportVersion }
      : null,
    checkpoint: null,
    error_code: status === 'failed' ? 'invalid_output' : null,
    error_message: status === 'failed' ? 'Agent output failed schema validation' : null,
    cancel_requested: false,
    created_at: '2026-07-14T09:00:00Z',
    updated_at: '2026-07-14T09:00:01Z',
  }
}

const assistantSessionId = 'assistant-session-e2e'
const assistantTaskId = 'assistant-task-e2e'
const assistantJobId = 'assistant-job-e2e'

const assistantContext = {
  id: 'assistant-context-e2e',
  work_id: workId,
  document_version_id: documentId,
  document_content_sha256: documentHash,
  scope: 'selection',
  page_numbers: [2],
  selection: {
    page_number: 2,
    page_width: 612,
    page_height: 792,
    quote_text: 'selected workspace evidence',
    context_before: null,
    context_after: null,
    rects: [{ x0: 62, y0: 50, x1: 310, y1: 74 }],
  },
  annotation_ids: [],
  section_fallback: false,
  extraction_version: 'pdfjs-fixture-v1',
  warnings: [],
  evidence_manifest: {
    document_content_sha256: documentHash,
    page_evidence_id: 'page:2:native',
  },
  evidence_sha256: 'f'.repeat(64),
  snapshot_sha256: '9'.repeat(64),
  created_at: '2026-07-14T11:00:00Z',
}

function assistantRun(status: 'running' | 'succeeded') {
  return {
    id: 'assistant-run-e2e',
    task_id: assistantTaskId,
    attempt: 1,
    backend_id: 'codex_cli',
    requested_model_id: 'fixture-vision-model',
    resolved_model_id: status === 'succeeded' ? 'fixture-vision-model' : null,
    reasoning_effort: 'medium',
    service_tier: 'flex',
    status,
    repair_count: status === 'succeeded' ? 1 : 0,
    prompt_sha256: status === 'succeeded' ? '7'.repeat(64) : null,
    request_sha256: status === 'succeeded' ? '8'.repeat(64) : null,
    raw_output_sha256: status === 'succeeded' ? '6'.repeat(64) : null,
    usage: status === 'succeeded' ? { input_tokens: 96, output_tokens: 32 } : {},
    error_code: null,
    diagnostic: null,
    started_at: '2026-07-14T11:00:01Z',
    completed_at: status === 'succeeded' ? '2026-07-14T11:00:03Z' : null,
    created_at: '2026-07-14T11:00:00Z',
  }
}

function assistantTask(status: 'running' | 'succeeded') {
  return {
    id: assistantTaskId,
    session_id: assistantSessionId,
    retry_of_task_id: null,
    mode: 'explain',
    status,
    network_policy: 'local_only',
    backend_id: 'codex_cli',
    requested_model_id: 'fixture-vision-model',
    reasoning_effort: 'medium',
    service_tier: 'flex',
    output_language: 'zh-CN',
    job_id: assistantJobId,
    error_code: null,
    diagnostic: null,
    retryable: false,
    context: assistantContext,
    latest_run: assistantRun(status),
    created_at: '2026-07-14T11:00:00Z',
    started_at: '2026-07-14T11:00:01Z',
    completed_at: status === 'succeeded' ? '2026-07-14T11:00:03Z' : null,
    updated_at: status === 'succeeded'
      ? '2026-07-14T11:00:03Z'
      : '2026-07-14T11:00:01Z',
  }
}

function assistantMessages(complete: boolean) {
  const user = {
    id: 'assistant-message-user',
    session_id: assistantSessionId,
    task_id: assistantTaskId,
    sequence: 1,
    role: 'user',
    status: 'committed',
    content: '解释这段证据如何支持作者的结论。',
    citations: [],
    evidence_manifest: { document_content_sha256: documentHash },
    job_id: assistantJobId,
    reply_to_message_id: null,
    created_at: '2026-07-14T11:00:00Z',
  }
  if (!complete) return [user]
  return [
    user,
    {
      id: 'assistant-message-answer',
      session_id: assistantSessionId,
      task_id: assistantTaskId,
      sequence: 2,
      role: 'assistant',
      status: 'complete',
      content: '基于第 2 页，选中证据直接限定了当前解释的事实范围。',
      citations: [{
        evidence_id: 'page:2:native',
        kind: 'page',
        page_number: 2,
        locator: 'selected workspace evidence',
      }],
      evidence_manifest: { document_content_sha256: documentHash },
      job_id: assistantJobId,
      reply_to_message_id: user.id,
      created_at: '2026-07-14T11:00:03Z',
    },
  ]
}

function assistantWork(state: 'empty' | 'running' | 'succeeded') {
  if (state === 'empty') {
    return {
      work_id: workId,
      session_id: null,
      tasks: [],
      messages: [],
      latest_checkpoint: null,
    }
  }
  return {
    work_id: workId,
    session_id: assistantSessionId,
    tasks: [assistantTask(state)],
    messages: assistantMessages(state === 'succeeded'),
    latest_checkpoint: null,
  }
}

function assistantJob(status: 'running' | 'succeeded') {
  return {
    id: assistantJobId,
    task_id: assistantTaskId,
    kind: 'assistant.reply',
    status,
    progress: status === 'succeeded' ? 100 : 42,
    cancel_requested: false,
    error_code: null,
    error_message: null,
    created_at: '2026-07-14T11:00:00Z',
    updated_at: status === 'succeeded'
      ? '2026-07-14T11:00:03Z'
      : '2026-07-14T11:00:01Z',
  }
}

test('Agent analysis and the grounded Assistant are explicit, failure-safe and recoverable', async ({
  page,
}, testInfo) => {
  let analysisCreates = 0
  let assistantCreates = 0
  let assistantExternalResearch: boolean | null = null
  let assistantState: 'empty' | 'running' | 'succeeded' = 'empty'

  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    if (await fulfillDefaultUserPreferences(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()

    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === '/api/v1/jobs' && method === 'GET') {
      return json(route, assistantState === 'empty'
        ? []
        : [{
            ...assistantJob(assistantState),
            pause_requested: false,
          }])
    }
    if (path === `/api/v1/works/${workId}` && method === 'GET') return json(route, work)
    if (path === `/api/v1/works/${workId}/documents` && method === 'GET') {
      return json(route, [document])
    }
    if (path === `/api/v1/documents/${documentId}/reading-position` && method === 'GET') {
      return json(route, null)
    }
    if (path === `/api/v1/documents/${documentId}/reading-position` && method === 'PUT') {
      const payload = request.postDataJSON() as Record<string, unknown>
      return json(route, {
        document_version_id: documentId,
        ...payload,
        progress: Number(payload.page_number) / Number(payload.page_count) * 100,
        created_at: '2026-07-14T08:00:00Z',
        updated_at: '2026-07-14T11:00:00Z',
      })
    }
    if (path === `/api/v1/documents/${documentId}/annotations` && method === 'GET') {
      return json(route, [])
    }
    if (path === `/api/v1/documents/${documentId}/content` && method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/pdf',
        headers: { 'X-Content-Type-Options': 'nosniff' },
        body: pdf,
      })
    }
    if (path === '/api/v1/settings/agents' && method === 'GET') {
      return json(route, capabilities)
    }
    if (path === `/api/v1/analysis/works/${workId}/preflight` && method === 'POST') {
      return json(route, preflight)
    }
    if (path === `/api/v1/analysis/works/${workId}/reports` && method === 'GET') {
      if (analysisCreates >= 3) return json(route, [reportSummary(2), reportSummary(1)])
      if (analysisCreates >= 1) return json(route, [reportSummary(1)])
      return json(route, [])
    }
    if (path === `/api/v1/analysis/works/${workId}/reports` && method === 'POST') {
      analysisCreates += 1
      const command = request.postDataJSON() as {
        selection: { include_visual_evidence?: boolean }
      }
      expect(command.selection.include_visual_evidence).toBe(true)
      if (analysisCreates === 1) {
        return json(route, analysisJob('analysis-job-success-1', 'succeeded', 1), 202)
      }
      if (analysisCreates === 2) {
        return json(route, analysisJob('analysis-job-failure', 'failed'), 202)
      }
      return json(route, analysisJob('analysis-job-success-2', 'succeeded', 2), 202)
    }
    const reportMatch = path.match(/^\/api\/v1\/analysis\/reports\/agent-report-(\d)$/)
    if (reportMatch && method === 'GET') return json(route, report(Number(reportMatch[1])))
    const analysisFragmentMatch = path.match(/^\/api\/v1\/analysis\/jobs\/([^/]+)\/fragments$/)
    if (analysisFragmentMatch && method === 'GET') return json(route, [])
    if (path === '/api/v1/analysis/jobs/analysis-job-failure/failure' && method === 'GET') {
      return json(route, {
        job_id: 'analysis-job-failure',
        work_id: workId,
        error_category: 'invalid_output',
        error_message: 'Agent output failed schema validation',
        raw_response: '',
        validation_errors: ['findings: field required'],
        repair_count: 1,
        request_fingerprint: 'd'.repeat(64),
        created_at: '2026-07-14T10:00:00Z',
      })
    }
    if (path === `/api/v1/assistant/works/${workId}` && method === 'GET') {
      return json(route, assistantWork(assistantState))
    }
    if (path === `/api/v1/assistant/works/${workId}/tasks` && method === 'POST') {
      assistantCreates += 1
      const command = request.postDataJSON() as {
        mode: string
        message: string
        network_policy: string
        context: {
          scope: string
          selection?: { page_number: number, quote_text: string }
        }
      }
      expect(request.headers()['idempotency-key']).toMatch(/^assistant-ui-/)
      expect(command.mode).toBe('explain')
      expect(command.message).toBe('解释这段证据如何支持作者的结论。')
      expect(command.context.scope).toBe('selection')
      expect(command.context.selection?.page_number).toBe(2)
      expect(command.context.selection?.quote_text).toBe('selected workspace evidence')
      assistantExternalResearch = command.network_policy !== 'local_only'
      assistantState = 'running'
      return json(route, assistantJob('running'), 202)
    }
    if (path === `/api/v1/assistant/tasks/${assistantTaskId}` && method === 'GET') {
      const status = assistantState === 'succeeded' ? 'succeeded' : 'running'
      return json(route, {
        ...assistantTask(status),
        runs: [assistantRun(status)],
        messages: assistantMessages(status === 'succeeded'),
      })
    }
    if (path === `/api/v1/assistant/tasks/${assistantTaskId}/fragments` && method === 'GET') {
      return json(route, [
        {
          task_id: assistantTaskId,
          sequence: 0,
          run_id: 'assistant-run-e2e',
          kind: 'text',
          text: '正在核对选区证据，',
          created_at: '2026-07-14T11:00:01Z',
        },
        {
          task_id: assistantTaskId,
          sequence: 1,
          run_id: 'assistant-run-e2e',
          kind: 'text',
          text: '并验证第 2 页引用。',
          created_at: '2026-07-14T11:00:02Z',
        },
      ])
    }
    return json(route, { detail: `Unhandled fixture route: ${method} ${path}` }, 500)
  })

  await page.goto(`/library/${workId}/read/${documentId}`)
  await expect(page.locator('[data-test="reader-workspace"]')).toBeVisible()
  expect(analysisCreates).toBe(0)
  expect(assistantCreates).toBe(0)

  if (testInfo.project.name === 'mobile-chromium') {
    await page.locator('[data-test="mobile-report-tab"]').click()
    await expect(page.locator('[data-test="agent-column"]')).toHaveAttribute(
      'data-mobile-active',
      'true',
    )
    await expect(page.locator('[data-test="pdf-column"]')).toHaveAttribute(
      'data-mobile-active',
      'false',
    )
  } else {
    await page.getByRole('tab', { name: '分析报告' }).click()
  }

  await expect(page.locator('[data-test="analysis-empty"]')).toContainText('尚未分析这篇论文')
  expect(analysisCreates).toBe(0)
  await page.locator('[data-test="open-analysis-preflight"]').click()
  const dialog = page.locator('[data-test="analysis-preflight-dialog"]')
  await expect(dialog).toContainText('云端后端')
  await expect(dialog).toContainText('论文证据将发送给所选云端服务')
  await expect(dialog).toContainText('1 页视觉证据')
  await expect(dialog.locator('[data-test="analysis-backend"] option')).toHaveCount(2)
  await expect(dialog.locator('[data-test="analysis-visual-evidence"]')).toBeChecked()
  expect(analysisCreates).toBe(0)
  await page.screenshot({
    path: testInfo.outputPath('agent-cloud-preflight.png'),
    fullPage: true,
  })

  await dialog.locator('[data-test="start-analysis"]').click()
  await expect(page.getByRole('heading', { name: '证据约束分析 · 第一版' })).toBeVisible()
  await expect(page.locator('[data-test="report-markdown-export"]')).toHaveAttribute(
    'href',
    '/api/v1/analysis/reports/agent-report-1/markdown',
  )
  await expect(page.locator('[data-test="report-html-export"]')).toHaveAttribute(
    'href',
    '/api/v1/analysis/reports/agent-report-1/html',
  )
  expect(analysisCreates).toBe(1)

  await page.getByRole('button', { name: '重新分析' }).click()
  await page.locator('[data-test="start-analysis"]').click()
  await expect(page.getByRole('heading', { name: '结构化报告未通过验证' })).toBeVisible()
  await expect(page.getByText('已尝试自动修复 1 次', { exact: false })).toBeVisible()
  await expect(page.getByRole('heading', { name: '证据约束分析 · 第一版' })).toBeVisible()
  expect(analysisCreates).toBe(2)
  await page.screenshot({
    path: testInfo.outputPath('agent-failure-preserves-report.png'),
    fullPage: true,
  })

  await page.locator('[data-test="retry-analysis"]').click()
  await page.locator('[data-test="start-analysis"]').click()
  await expect(page.getByRole('heading', { name: '证据约束分析 · 第二版' })).toBeVisible()
  await expect(page.locator('[data-test="report-version"] option')).toHaveCount(2)
  await page.locator('[data-test="report-version"]').selectOption('agent-report-1')
  await expect(page.getByRole('heading', { name: '证据约束分析 · 第一版' })).toBeVisible()
  await page.locator('[data-test="report-version"]').selectOption('agent-report-2')
  await expect(page.getByRole('heading', { name: '证据约束分析 · 第二版' })).toBeVisible()
  expect(analysisCreates).toBe(3)

  if (testInfo.project.name === 'mobile-chromium') {
    await page.locator('[data-test="mobile-pdf-tab"]').click()
  }
  await page.getByRole('button', { name: '下一页' }).click()
  await expect(page.locator('[data-test="page-status"]')).toContainText('2 / 2')
  await selectFixturePdfText(page, 'selected workspace evidence')

  if (testInfo.project.name === 'mobile-chromium') {
    await page.locator('[data-test="mobile-assistant-tab"]').click()
  } else {
    await page.getByRole('tab', { name: '论文助理' }).click()
  }
  const assistant = page.locator('[data-test="research-assistant"]')
  await expect(assistant.locator('[data-test="assistant-empty"]')).toContainText(
    '从正在阅读的证据开始',
  )
  await expect(assistant.locator('[data-test="assistant-context"]')).toContainText(
    'selected workspace evidence',
  )
  await expect(assistant.locator('[data-test="assistant-network-boundary"]')).toContainText(
    '仅使用当前论文与本地资料',
  )
  await expect(assistant.locator('[data-test="assistant-location"]')).toContainText('云端 Agent')
  expect(assistantCreates).toBe(0)

  await assistant.locator('[data-test="assistant-mode-explain"]').click()
  await assistant.locator('[data-test="assistant-input"]').fill('解释这段证据如何支持作者的结论。')
  await assistant.locator('[data-test="assistant-send"]').click()
  await expect(assistant.getByText('正在核对选区证据，并验证第 2 页引用。', {
    exact: true,
  })).toBeVisible()
  expect(assistantCreates).toBe(1)
  expect(assistantExternalResearch).toBe(false)

  assistantState = 'succeeded'
  await page.reload()
  if (testInfo.project.name === 'mobile-chromium') {
    await page.locator('[data-test="mobile-assistant-tab"]').click()
  } else {
    await page.getByRole('tab', { name: '论文助理' }).click()
  }
  const recoveredAnswer = page.getByText('基于第 2 页，选中证据直接限定了当前解释的事实范围。', {
    exact: true,
  })
  await expect(recoveredAnswer).toBeVisible()
  await expect(recoveredAnswer.locator('..').getByText('当前版本', { exact: true })).toBeVisible()
  expect(assistantCreates).toBe(1)

  await page.locator('[data-test="assistant-citation"]').click()
  await expect(page.locator('[data-test="page-input"]')).toHaveValue('2')
  await expect(page.locator('[data-test="page-status"]')).toContainText('2 / 2')

  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
  await page.screenshot({
    path: testInfo.outputPath('agent-recovered-assistant.png'),
    fullPage: true,
  })
})
