import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Route } from '@playwright/test'

import { fulfillCompletedOnboarding } from './support'

const PDF_COLD_START_TIMEOUT_MS = 20_000

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

function fixturePdf(pages: string[]): Buffer {
  const objects: string[] = []
  const pageObjectNumbers: number[] = []
  const fontObjectNumber = 3 + pages.length * 2
  pages.forEach((text, index) => {
    const pageObjectNumber = 3 + index * 2
    const streamObjectNumber = pageObjectNumber + 1
    pageObjectNumbers.push(pageObjectNumber)
    const stream = `BT /F1 18 Tf 62 724 Td (${text}) Tj ET`
    objects.push(
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 ${fontObjectNumber} 0 R >> >> /Contents ${streamObjectNumber} 0 R >>`,
      `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`,
    )
  })
  const orderedObjects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    `<< /Type /Pages /Kids [${pageObjectNumbers.map((value) => `${value} 0 R`).join(' ')}] /Count ${pages.length} >>`,
    ...objects,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ]
  let body = '%PDF-1.4\n% paper-reader fixture\n'
  const offsets = [0]
  orderedObjects.forEach((object, index) => {
    offsets.push(Buffer.byteLength(body))
    body += `${index + 1} 0 obj\n${object}\nendobj\n`
  })
  const xrefOffset = Buffer.byteLength(body)
  body += `xref\n0 ${orderedObjects.length + 1}\n`
  body += '0000000000 65535 f \n'
  for (const offset of offsets.slice(1)) {
    body += `${String(offset).padStart(10, '0')} 00000 n \n`
  }
  body += `trailer\n<< /Size ${orderedObjects.length + 1} /Root 1 0 R >>\n`
  body += `startxref\n${xrefOffset}\n%%EOF\n`
  return Buffer.from(body)
}

const sourcePdf = fixturePdf([
  'Agent evidence for reliable paper reading',
  'Agent comparison on the second page',
])
const revisedPdf = fixturePdf([
  'A revised introduction for reliable agents',
  'Additional experiments in the new version',
  'Agent evidence for reliable paper reading',
])

const sourceDocument = {
  id: 'document-reader-v1',
  work_id: 'work-reader-e2e',
  content_sha256: 'a'.repeat(64),
  access_status: 'user_provided',
  media_type: 'application/pdf',
  completeness: 100,
  extraction_status: 'complete',
  available: true,
  byte_size: sourcePdf.byteLength,
  original_filename: 'agent-reader-v1.pdf',
  acquisition_kind: 'manual_import',
  last_accessed_at: null,
  created_at: '2026-07-13T08:00:00Z',
  is_representative: true,
}

const revisedDocument = {
  ...sourceDocument,
  id: 'document-reader-v2',
  content_sha256: 'b'.repeat(64),
  byte_size: revisedPdf.byteLength,
  original_filename: 'agent-reader-v2.pdf',
  created_at: '2026-07-13T09:00:00Z',
  is_representative: true,
}

const baseWork = {
  id: 'work-reader-e2e',
  title: 'Reliable Agents for Paper Reading',
  authors: ['Alice Chen', 'Ravi Kumar'],
  abstract: 'An evidence-first local research reading workflow.',
  doi: '10.1000/reader.e2e',
  published_at: '2026-07-01T00:00:00Z',
  added_via: 'pdf',
  added_at: '2026-07-13T08:00:00Z',
  favorite: true,
  feedback: 'like',
  pinned: false,
  reading_progress: 0,
  analysis_state: 'not_analyzed',
}

const analysisCapability = {
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
  credential_configured: false,
  supports_manual_model_id: true,
  supports_default_model: true,
  models: [{
    model_id: 'fixture-model',
    display_name: 'Fixture model',
    supports_vision: true,
    context_window: 128_000,
  }],
  degradation_reasons: [],
}

const analysisPreflight = {
  work_id: baseWork.id,
  document_version_id: sourceDocument.id,
  document_content_sha256: sourceDocument.content_sha256,
  evidence_level: 'full_text',
  evidence_sha256: 'e'.repeat(64),
  coverage: 100,
  backend_id: 'codex_cli',
  backend_display_name: 'Codex CLI',
  backend_location: 'cloud',
  cloud_backend: true,
  model_id: 'fixture-model',
  model_options: analysisCapability.models,
  manual_model_id_allowed: true,
  default_model_allowed: true,
  reasoning_effort: 'high',
  service_tier: 'flex',
  language: 'zh-CN',
  template_version_id: 'builtin-general-v1',
  template_name: '通用论文分析',
  external_research: false,
  scope: ['pages:2', 'annotations:2', 'evidence:full_text'],
  warnings: [],
  ready: true,
  prompt_sha256: 'f'.repeat(64),
  preflight_fingerprint: '9'.repeat(64),
}

function migrationCandidate(accepted: boolean) {
  return {
    id: 'migration-candidate-e2e',
    batch_id: 'migration-preview-e2e',
    source_annotation_id: 'annotation-document-reader-v1-1',
    source_kind: 'highlight',
    source_page_number: 1,
    source_quote_text: 'Agent evidence for reliable paper reading',
    source_note: null,
    source_color: 'amber',
    match_state: 'matched',
    confidence: 'high',
    score: 0.98,
    target_page_number: 3,
    target_page_width: 612,
    target_page_height: 792,
    target_quote_text: 'Agent evidence for reliable paper reading',
    target_context_before: '',
    target_context_after: '',
    target_rects: [{ x0: 60, y0: 48, x1: 430, y1: 76 }],
    text_source: 'native',
    alternatives: [],
    decision: accepted ? 'accept' : null,
    target_annotation_id: accepted ? 'annotation-migrated-e2e' : null,
    created_at: '2026-07-13T09:10:00Z',
    decided_at: accepted ? '2026-07-13T09:12:00Z' : null,
  }
}

function migrationPreview(accepted: boolean) {
  return {
    id: 'migration-preview-e2e',
    algorithm_version: 'annotation-migration-v1',
    source_document_version_id: sourceDocument.id,
    source_document_content_sha256: sourceDocument.content_sha256,
    target_document_version_id: revisedDocument.id,
    target_document_content_sha256: revisedDocument.content_sha256,
    target_extraction_sha256: 'c'.repeat(64),
    candidates: [migrationCandidate(accepted)],
    coverage: {
      total: 1,
      matched: 1,
      ambiguous: 0,
      orphaned: 0,
      pending: accepted ? 0 : 1,
      accepted: accepted ? 1 : 0,
      rejected: 0,
    },
    created_at: '2026-07-13T09:10:00Z',
  }
}

test('lawful import becomes a persistent reading and version-migration workflow', async ({ page }, testInfo) => {
  let imported = false
  let revisionImported = false
  let initialImportAttempts = 0
  let migrationAccepted = false
  let analysisCreateCalls = 0
  let assistantCreateCalls = 0
  const positions = new Map<string, Record<string, unknown>>()
  const annotations = new Map<string, Array<Record<string, unknown>>>([
    [sourceDocument.id, []],
    [revisedDocument.id, []],
  ])

  function currentWork() {
    return {
      ...baseWork,
      document_count: revisionImported ? 2 : 1,
      representative_document: revisionImported ? revisedDocument : sourceDocument,
    }
  }

  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === `/api/v1/analysis/works/${baseWork.id}/reports` && method === 'GET') {
      return json(route, [])
    }
    if (path === '/api/v1/settings/agents' && method === 'GET') {
      return json(route, [analysisCapability])
    }
    if (path === `/api/v1/analysis/works/${baseWork.id}/preflight` && method === 'POST') {
      return json(route, analysisPreflight)
    }
    if (path === `/api/v1/analysis/works/${baseWork.id}/reports` && method === 'POST') {
      analysisCreateCalls += 1
      return json(route, { detail: 'The fixture must not create a model Job before confirmation.' }, 500)
    }
    if (path === `/api/v1/assistant/works/${baseWork.id}` && method === 'GET') {
      return json(route, {
        work_id: baseWork.id,
        session_id: null,
        tasks: [],
        messages: [],
        latest_checkpoint: null,
      })
    }
    if (path === `/api/v1/assistant/works/${baseWork.id}/tasks` && method === 'POST') {
      assistantCreateCalls += 1
      return json(route, { detail: 'The reader fixture must not call an Agent without a click.' }, 500)
    }
    if (path === '/api/v1/library/works' && method === 'GET') {
      const items = imported ? [currentWork()] : []
      return json(route, { items, total: items.length, limit: 20, offset: 0 })
    }
    if (path === '/api/v1/library/imports/pdf' && method === 'POST') {
      const attachesToExistingWork = (request.postData() ?? '').includes(baseWork.id)
      if (!attachesToExistingWork && initialImportAttempts === 0) {
        initialImportAttempts += 1
        return json(route, { detail: 'D:\\private\\oversized-reader.pdf' }, 413)
      }
      if (attachesToExistingWork) revisionImported = true
      else imported = true
      const document = attachesToExistingWork ? revisedDocument : sourceDocument
      return json(route, {
        work_id: baseWork.id,
        document_version_id: document.id,
        content_sha256: document.content_sha256,
        created_work: !attachesToExistingWork,
        created_document: true,
      }, 201)
    }
    if (path === `/api/v1/works/${baseWork.id}` && method === 'GET') {
      return json(route, currentWork())
    }
    if (path === `/api/v1/works/${baseWork.id}/documents` && method === 'GET') {
      return json(route, revisionImported
        ? [revisedDocument, { ...sourceDocument, is_representative: false }]
        : [sourceDocument])
    }
    const positionMatch = path.match(/^\/api\/v1\/documents\/([^/]+)\/reading-position$/)
    if (positionMatch && method === 'GET') {
      return json(route, positions.get(positionMatch[1] ?? '') ?? null)
    }
    if (positionMatch && method === 'PUT') {
      const documentId = positionMatch[1] ?? ''
      const payload = request.postDataJSON() as Record<string, unknown>
      const value = {
        document_version_id: documentId,
        ...payload,
        progress: Number(payload.page_number) / Number(payload.page_count) * 100,
        created_at: '2026-07-13T08:00:00Z',
        updated_at: '2026-07-13T08:05:00Z',
      }
      positions.set(documentId, value)
      return json(route, value)
    }
    const annotationMatch = path.match(/^\/api\/v1\/documents\/([^/]+)\/annotations$/)
    if (annotationMatch && method === 'GET') {
      return json(route, annotations.get(annotationMatch[1] ?? '') ?? [])
    }
    if (annotationMatch && method === 'POST') {
      const documentId = annotationMatch[1] ?? ''
      const values = annotations.get(documentId) ?? []
      const payload = request.postDataJSON() as Record<string, unknown>
      const annotation = {
        id: `annotation-${documentId}-${values.length + 1}`,
        document_version_id: documentId,
        ...payload,
        created_at: '2026-07-13T08:06:00Z',
        updated_at: '2026-07-13T08:06:00Z',
      }
      values.push(annotation)
      annotations.set(documentId, values)
      return json(route, annotation, 201)
    }
    if (path === `/api/v1/documents/${sourceDocument.id}/content` && method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/pdf',
        headers: {
          'Accept-Ranges': 'bytes',
          'Content-Disposition': 'inline; filename="agent-reader-v1.pdf"',
          'X-Content-Type-Options': 'nosniff',
        },
        body: sourcePdf,
      })
    }
    if (path === `/api/v1/documents/${revisedDocument.id}/content` && method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/pdf',
        headers: {
          'Accept-Ranges': 'bytes',
          'Content-Disposition': 'inline; filename="agent-reader-v2.pdf"',
          'X-Content-Type-Options': 'nosniff',
        },
        body: revisedPdf,
      })
    }
    if (
      path === `/api/v1/documents/${sourceDocument.id}/annotation-migrations/preview`
      && method === 'POST'
    ) return json(route, migrationPreview(false))
    if (path === '/api/v1/annotation-migrations/migration-preview-e2e' && method === 'GET') {
      return json(route, migrationPreview(migrationAccepted))
    }
    if (
      path === '/api/v1/annotation-migrations/candidates/migration-candidate-e2e/decision'
      && method === 'PUT'
    ) {
      migrationAccepted = true
      annotations.set(revisedDocument.id, [{
        id: 'annotation-migrated-e2e',
        document_version_id: revisedDocument.id,
        document_content_sha256: revisedDocument.content_sha256,
        kind: 'highlight',
        page_number: 3,
        page_width: 612,
        page_height: 792,
        quote_text: 'Agent evidence for reliable paper reading',
        context_before: '',
        context_after: '',
        rects: [{ x0: 60, y0: 48, x1: 430, y1: 76 }],
        color: 'amber',
        note: null,
        created_at: '2026-07-13T09:12:00Z',
        updated_at: '2026-07-13T09:12:00Z',
      }])
      return json(route, migrationCandidate(true))
    }
    return json(route, { detail: `Unhandled fixture route: ${method} ${path}` }, 500)
  })

  await page.goto('/library')
  await expect(page.getByRole('heading', { level: 1, name: '我的论文库' })).toBeVisible()
  await expect(page.locator('[data-test="library-empty"]')).toContainText('论文库还是空的')
  await page.locator('[data-test="open-library-import"]').click()

  const fileInput = page.locator('[data-test="pdf-import-file"]')
  await fileInput.setInputFiles({
    name: 'notes.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('not a PDF'),
  })
  await expect(page.getByRole('alert')).toContainText('请选择 PDF 文件')
  await fileInput.setInputFiles({
    name: 'agent-reader-v1.pdf',
    mimeType: 'application/pdf',
    buffer: sourcePdf,
  })
  await page.locator('[data-test="pdf-import-title"]').fill(baseWork.title)
  await page.locator('[data-test="pdf-import-authors"]').fill('Alice Chen；Ravi Kumar')
  await page.locator('[data-test="pdf-import-doi"]').fill(baseWork.doi)
  await page.locator('[data-test="pdf-import-rights"]').check()
  await page.locator('[data-test="submit-pdf-import"]').click()
  await expect(page.getByRole('alert')).toContainText('PDF 超过 100 MiB')
  await expect(page.getByRole('alert')).not.toContainText('private')
  await page.locator('[data-test="submit-pdf-import"]').click()

  await expect(page.locator(`[data-test="library-work-${baseWork.id}"]`)).toBeVisible()
  await expect(page.getByText('论文和 PDF 已加入本地库', { exact: false })).toBeVisible()
  await page.getByRole('link', { name: `阅读《${baseWork.title}》` }).click()

  await expect(page.locator('[data-test="reader-workspace"]')).toBeVisible()
  await expect(page.locator('[data-test="page-status"]')).toContainText('1 / 2', {
    timeout: PDF_COLD_START_TIMEOUT_MS,
  })
  if (testInfo.project.name === 'mobile-chromium') {
    const mobilePdfTab = page.locator('[data-test="mobile-pdf-tab"]')
    const mobileAnnotationsTab = page.locator('[data-test="mobile-annotations-tab"]')
    await mobilePdfTab.focus()
    await page.keyboard.press('ArrowRight')
    await expect(mobileAnnotationsTab).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(mobileAnnotationsTab).toHaveAttribute('aria-pressed', 'true')
    await mobilePdfTab.click()
  } else {
    const annotationsTab = page.locator('[data-test="agent-annotations-tab"]')
    const reportTab = page.locator('[data-test="agent-report-tab"]')
    await annotationsTab.focus()
    await page.keyboard.press('ArrowRight')
    await expect(reportTab).toBeFocused()
    await expect(reportTab).toHaveAttribute('aria-selected', 'true')
    await page.keyboard.press('ArrowLeft')
    await expect(annotationsTab).toHaveAttribute('aria-selected', 'true')
  }
  if (testInfo.project.name === 'medium-chromium') {
    const drawerToggle = page.locator('[data-test="library-drawer-toggle"]')
    const libraryColumn = page.locator('#reader-library-column')
    await expect(drawerToggle).toBeVisible()
    await expect(drawerToggle).toHaveAttribute('aria-expanded', 'false')
    await expect(libraryColumn).toHaveAttribute('aria-hidden', 'true')
    await expect(libraryColumn).toHaveAttribute('inert', '')
    await drawerToggle.click()
    await expect(drawerToggle).toHaveAttribute('aria-expanded', 'true')
    await expect(libraryColumn).toHaveAttribute('aria-hidden', 'false')
    await expect(libraryColumn).not.toHaveAttribute('inert', '')
    await libraryColumn.getByRole('button', { name: '关闭论文与版本列表' }).click()
    await expect(drawerToggle).toHaveAttribute('aria-expanded', 'false')
    await expect(libraryColumn).toHaveAttribute('inert', '')
  }
  const canvas = page.locator('canvas[aria-label*="第 1 页"]')
  await expect.poll(
    () => canvas.evaluate((element) => (element as HTMLCanvasElement).width),
    { timeout: PDF_COLD_START_TIMEOUT_MS },
  ).toBeGreaterThan(0)
  await expect.poll(
    () => canvas.evaluate((element) => (element as HTMLCanvasElement).height),
    { timeout: PDF_COLD_START_TIMEOUT_MS },
  ).toBeGreaterThan(0)
  await expect(canvas).toHaveCSS('background-color', 'rgb(255, 255, 255)')
  await page.emulateMedia({ colorScheme: 'dark' })
  await expect(canvas).toHaveCSS('background-color', 'rgb(255, 255, 255)')
  await page.emulateMedia({ colorScheme: 'light' })
  if (testInfo.project.name === 'mobile-chromium') {
    await expect(page.getByRole('navigation', { name: '移动主导航' })).toBeHidden()
  }

  const textLayer = page.locator('[data-test="pdf-text-layer"]')
  await expect(textLayer).toContainText('Agent evidence for reliable paper reading', {
    timeout: PDF_COLD_START_TIMEOUT_MS,
  })
  await textLayer.evaluate((layer) => {
    const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT)
    let textNode: Node | null = walker.nextNode()
    while (textNode && !textNode.textContent?.includes('Agent evidence')) {
      textNode = walker.nextNode()
    }
    if (!textNode?.textContent) throw new Error('PDF text fixture was not rendered')
    const range = document.createRange()
    range.selectNodeContents(textNode)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
    layer.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
  })
  await expect(page.locator('[data-test="selection-quote"]')).toContainText(
    'Agent evidence for reliable paper reading',
  )
  await page.getByRole('toolbar', { name: '已选文字操作' })
    .getByRole('button', { name: '保存标注' })
    .click()
  await expect(page.getByText('批注已保存', { exact: true })).toBeAttached()
  if (testInfo.project.name === 'mobile-chromium') {
    await page.locator('[data-test="mobile-pdf-tab"]').click()
  }
  await expect(page.locator('[data-test="annotation-overlay-annotation-document-reader-v1-1"]')).toBeVisible()

  await page.getByRole('button', { name: '下一页' }).click()
  await expect(page.locator('[data-test="page-status"]')).toContainText('2 / 2')
  await expect.poll(() => positions.get(sourceDocument.id)?.page_number).toBe(2)
  await page.getByLabel('在论文中搜索').fill('Agent')
  await page.getByLabel('在论文中搜索').press('Enter')
  await expect(page.locator('[data-test="search-result-1"]')).toContainText('第 1 页')
  await expect(page.locator('[data-test="search-result-2"]')).toContainText('第 2 页')
  await page.getByRole('button', { name: '顺时针旋转' }).click()
  await page.getByRole('button', { name: '适合宽度' }).click()
  if (testInfo.project.name === 'mobile-chromium') {
    await page.locator('[data-test="mobile-annotations-tab"]').click()
  }
  await page.locator('[data-test="create-bookmark"]').click()
  await expect(page.getByText('书签已保存', { exact: true })).toBeAttached()
  if (testInfo.project.name === 'mobile-chromium') {
    await page.locator('[data-test="mobile-pdf-tab"]').click()
    await expect(page.locator('[data-test="page-status"]')).toContainText('2 / 2')
  }
  await expect.poll(() => positions.get(sourceDocument.id)?.rotation).toBe(90)

  await page.emulateMedia({ reducedMotion: 'reduce' })
  if (testInfo.project.name === 'mobile-chromium') {
    await page.locator('[data-test="mobile-report-tab"]').click()
  } else {
    await page.getByRole('tab', { name: '分析报告' }).click()
  }
  await expect(page.locator('[data-test="analysis-empty"]')).toContainText('尚未分析这篇论文')
  expect(analysisCreateCalls).toBe(0)
  await page.locator('[data-test="open-analysis-preflight"]').click()
  const preflightDialog = page.locator('[data-test="analysis-preflight-dialog"]')
  await expect(preflightDialog).toBeVisible()
  await expect(preflightDialog).toContainText('云端后端')
  await expect(preflightDialog).toContainText('全文证据')
  expect(await preflightDialog.evaluate((element) => element.contains(document.activeElement))).toBe(true)
  expect(analysisCreateCalls).toBe(0)
  await page.keyboard.press('Escape')
  await expect(preflightDialog).toBeHidden()

  if (testInfo.project.name === 'mobile-chromium') {
    await page.locator('[data-test="mobile-assistant-tab"]').click()
  } else {
    await page.getByRole('tab', { name: '论文助理' }).click()
  }
  const assistant = page.locator('[data-test="research-assistant"]')
  await expect(assistant.locator('[data-test="assistant-empty"]')).toContainText(
    '从正在阅读的证据开始',
  )
  await expect(assistant.locator('[data-test="assistant-network-boundary"]')).toContainText(
    '仅使用当前论文与本地资料',
  )
  expect(assistantCreateCalls).toBe(0)
  if (testInfo.project.name === 'mobile-chromium') {
    await page.locator('[data-test="mobile-pdf-tab"]').click()
  }
  await expect(page.locator('[data-test="page-status"]')).toContainText('2 / 2')
  await page.waitForTimeout(800)
  await expect.poll(() => positions.get(sourceDocument.id)?.page_number).toBe(2)

  await page.reload()
  await expect(page.locator('[data-test="reader-workspace"]')).toBeVisible()
  await expect(page.locator('[data-test="page-status"]')).toContainText('2 / 2')
  await expect(page.locator('[data-test="annotation-overlay-annotation-document-reader-v1-2"]')).toHaveCount(0)
  await page.getByRole('link', { name: '返回我的论文库' }).click()

  await page.locator(`[data-test="add-version-${baseWork.id}"]`).click()
  await expect(page.getByText(`作为新版本加入《${baseWork.title}》`, { exact: false })).toBeVisible()
  await page.locator('[data-test="pdf-import-file"]').setInputFiles({
    name: 'agent-reader-v2.pdf',
    mimeType: 'application/pdf',
    buffer: revisedPdf,
  })
  await page.locator('[data-test="pdf-import-rights"]').check()
  await page.locator('[data-test="submit-pdf-import"]').click()
  await expect(page.locator(`[data-test="library-work-${baseWork.id}"]`)).toContainText(
    'agent-reader-v2.pdf',
  )
  await page.getByRole('link', { name: `阅读《${baseWork.title}》` }).click()

  await expect(page.locator('[data-test="page-status"]')).toContainText('1 / 3')
  if (testInfo.project.name === 'mobile-chromium') {
    await page.locator('[data-test="mobile-annotations-tab"]').click()
  }
  await expect(page.locator('[data-test="migration-source"]')).toHaveValue(sourceDocument.id)
  await page.locator('[data-test="start-migration-preview"]').click()
  await expect(page.locator('[data-test="migration-coverage"]')).toContainText('1 可迁移')
  await page.locator('[data-test="preview-migration-candidate"]').click()
  await expect(page.locator('[data-test="page-status"]')).toContainText('3 / 3')
  await expect(page.locator('[data-test="migration-preview-overlay-migration-candidate-e2e"]')).toBeVisible()
  if (testInfo.project.name === 'mobile-chromium') {
    await page.locator('[data-test="mobile-annotations-tab"]').click()
  }
  await page.locator('[data-test="accept-migration-candidate"]').click()
  await expect(page.getByText('已接受迁移', { exact: true })).toBeVisible()
  expect(annotations.get(sourceDocument.id)).toHaveLength(2)
  if (testInfo.project.name === 'mobile-chromium') {
    await page.locator('[data-test="mobile-pdf-tab"]').click()
  }
  await expect(page.locator('[data-test="migration-preview-overlay-migration-candidate-e2e"]')).toHaveCount(0)
  await expect(page.locator('[data-test="annotation-overlay-annotation-migrated-e2e"]')).toBeVisible()
  await expect(page.locator('canvas[aria-label*="第 3 页"]')).toHaveCSS(
    'background-color',
    'rgb(255, 255, 255)',
  )

  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
  await page.screenshot({
    path: testInfo.outputPath('quiet-library-reader-migration.png'),
    fullPage: true,
  })
})

test('a missing local PDF fails safely without exposing its filesystem path', async ({ page }) => {
  const missingDocument = {
    ...sourceDocument,
    id: 'document-missing',
    work_id: 'work-missing',
    original_filename: 'missing.pdf',
  }
  const missingWork = {
    ...baseWork,
    id: 'work-missing',
    title: 'Missing Local Evidence',
    document_count: 1,
    representative_document: missingDocument,
  }
  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === '/api/v1/works/work-missing') return json(route, missingWork)
    if (path === '/api/v1/works/work-missing/documents') return json(route, [missingDocument])
    if (path === '/api/v1/documents/document-missing/reading-position') return json(route, null)
    if (path === '/api/v1/documents/document-missing/annotations') return json(route, [])
    if (path === '/api/v1/documents/document-missing/content') {
      return json(route, { detail: 'D:\\private\\library\\objects\\missing.pdf' }, 410)
    }
    return json(route, { detail: `Unhandled fixture route: ${method} ${path}` }, 500)
  })

  await page.goto('/library/work-missing/read/document-missing')
  await expect(page.locator('[data-test="reader-workspace"]')).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('无法打开这份 PDF')
  await expect(page.locator('body')).not.toContainText('private')
  await expect(page.locator('[data-test="retry-pdf"]')).toBeVisible()
  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
})
