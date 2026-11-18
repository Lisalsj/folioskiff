import { expect, test, type BrowserContext, type Page, type Route } from '@playwright/test'

import { fulfillCompletedOnboarding } from './support'

const SAMPLE_COUNT = 20
const BRIEFING_INTERACTIVE_BUDGET_MS = 800
const PDF_FIRST_CANVAS_BUDGET_MS = 2_500
const LAYOUT_SHIFT_BUDGET = 0.1
const SAMPLE_TIMEOUT_MS = 15_000

type ReaderMarks = {
  startedAt: number
  canvasAt?: number
  textAt?: number
}

type ReaderSample = {
  canvas_ms: number
  text_ms: number
  complete_ms: number
}

declare global {
  interface Window {
    __paperReaderPerformance?: ReaderMarks
    __paperReaderLayoutShift?: number
  }
}

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

function fixturePdf(text: string): Buffer {
  const stream = `BT /F1 18 Tf 62 724 Td (${text}) Tj ET`
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ]
  let body = '%PDF-1.4\n% paper-reader performance fixture\n'
  const offsets = [0]
  objects.forEach((object, index) => {
    offsets.push(Buffer.byteLength(body))
    body += `${index + 1} 0 obj\n${object}\nendobj\n`
  })
  const xrefOffset = Buffer.byteLength(body)
  body += `xref\n0 ${objects.length + 1}\n`
  body += '0000000000 65535 f \n'
  for (const offset of offsets.slice(1)) {
    body += `${String(offset).padStart(10, '0')} 00000 n \n`
  }
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\n`
  body += `startxref\n${xrefOffset}\n%%EOF\n`
  return Buffer.from(body)
}

const workId = 'work-reader-performance'
const documentId = 'document-reader-performance'
const paperText = 'Measured evidence for local paper reading'
const pdf = fixturePdf(paperText)
const document = {
  id: documentId,
  work_id: workId,
  content_sha256: '8'.repeat(64),
  access_status: 'user_provided',
  media_type: 'application/pdf',
  completeness: 100,
  extraction_status: 'complete',
  available: true,
  byte_size: pdf.byteLength,
  original_filename: 'performance-fixture.pdf',
  acquisition_kind: 'manual_import',
  last_accessed_at: null,
  created_at: '2026-07-15T08:00:00Z',
  is_representative: true,
}
const work = {
  id: workId,
  title: 'Measured Local Paper Reading',
  authors: ['Paper Reader Performance Fixture'],
  abstract: 'A deterministic fixture for measuring PDF first-page presentation.',
  doi: null,
  published_at: '2026-07-01T00:00:00Z',
  added_via: 'pdf',
  added_at: '2026-07-15T08:00:00Z',
  favorite: false,
  feedback: 'none',
  pinned: false,
  reading_progress: 0,
  analysis_state: 'not_analyzed',
  document_count: 1,
  representative_document: document,
}
const briefing = {
  generated_at: '2026-07-15T08:00:00Z',
  empty_state: {
    active: false,
    reason: null,
    title: null,
    description: null,
    primary_action: null,
  },
  plans: [{
    id: 'plan-performance',
    name: '本地性能研究',
    research_intent: '评估本地论文阅读与推荐性能',
    enabled: true,
    schedule_frequency: 'weekly',
    sources: ['arxiv', 'openreview'],
    latest_run: {
      id: 'run-performance',
      status: 'succeeded',
      months: ['2026-07'],
      created_at: '2026-07-15T07:00:00Z',
      completed_at: '2026-07-15T07:01:00Z',
      recommendation_set_id: 'recommendations-performance',
      recommendation_set_version: 1,
      recommendation_count: SAMPLE_COUNT,
    },
  }],
  new_papers: Array.from({ length: SAMPLE_COUNT }, (_, index) => ({
    work_id: `briefing-work-${index + 1}`,
    scored_work_id: `briefing-work-${index + 1}`,
    title: `Local performance paper ${index + 1}`,
    authors: ['Paper Reader Fixture'],
    plan_id: 'plan-performance',
    run_id: 'run-performance',
    recommendation_set_id: 'recommendations-performance',
    position: index + 1,
    channel: index % 5 === 0 ? 'exploration' : 'exploitation',
    composite_score: 90 - index,
    relevance_score: 92 - index,
    quality_score: 88 - index,
    coverage: 90,
    confidence: 90,
    warnings: [],
  })),
  important_revisions: [],
  pending_catchups: [],
  active_jobs: [],
  failed_jobs: [],
}

async function installRoutes(context: BrowserContext): Promise<void> {
  await context.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (path === '/api/v1/jobs' && method === 'GET') return json(route, [])
    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === '/api/v1/briefing' && method === 'GET') return json(route, briefing)
    if (path === '/api/v1/library/works' && method === 'GET') {
      return json(route, { items: [work], total: 1, limit: 20, offset: 0 })
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
        progress: 100,
        created_at: '2026-07-15T08:00:00Z',
        updated_at: '2026-07-15T08:00:00Z',
      })
    }
    if (path === `/api/v1/documents/${documentId}/annotations` && method === 'GET') {
      return json(route, [])
    }
    if (path === `/api/v1/documents/${documentId}/content` && method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/pdf',
        headers: {
          'Accept-Ranges': 'bytes',
          'Content-Disposition': 'inline; filename="performance-fixture.pdf"',
          'X-Content-Type-Options': 'nosniff',
        },
        body: pdf,
      })
    }
    return json(route, { detail: `Unhandled performance fixture route: ${method} ${path}` }, 500)
  })
}

async function installLayoutShiftObserver(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.__paperReaderLayoutShift = 0
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const shift = entry as PerformanceEntry & { hadRecentInput: boolean; value: number }
        if (!shift.hadRecentInput) {
          window.__paperReaderLayoutShift = (window.__paperReaderLayoutShift ?? 0) + shift.value
        }
      }
    }).observe({ type: 'layout-shift', buffered: true })
  })
}

async function settleLayout(page: Page): Promise<void> {
  await page.evaluate(() => new Promise<void>((resolve) => {
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => resolve()))
  }))
  await page.waitForTimeout(50)
}

async function measureBriefingInteractive(page: Page): Promise<number> {
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.locator('[data-test="new-papers"] article')).toHaveCount(SAMPLE_COUNT)
  await expect(page.getByRole('link', { name: '新建关注计划' })).toBeEnabled()
  return page.evaluate(() => performance.now())
}

async function measureReaderOpen(page: Page): Promise<ReaderSample> {
  await page.goto('/library')
  const readLink = page.locator(`[data-test="read-${workId}"]`)
  await expect(readLink).toBeVisible()
  await readLink.evaluate((element) => {
    element.addEventListener('click', () => {
      if (window.__paperReaderLayoutShift !== undefined) window.__paperReaderLayoutShift = 0
      const marks: ReaderMarks = { startedAt: window.performance.now() }
      window.__paperReaderPerformance = marks
      const observer = new MutationObserver(() => inspect())
      let frame = 0

      function canvasHasPaint(canvas: HTMLCanvasElement): boolean {
        if (canvas.width === 300 && canvas.height === 150) return false
        const context = canvas.getContext('2d', { willReadFrequently: true })
        if (!context || canvas.width < 1 || canvas.height < 1) return false
        const points = [
          [0, 0],
          [Math.floor(canvas.width / 2), Math.floor(canvas.height / 2)],
          [Math.max(0, canvas.width - 1), Math.max(0, canvas.height - 1)],
          [Math.floor(canvas.width * 0.1), Math.floor(canvas.height * 0.1)],
        ]
        return points.some(([x, y]) => context.getImageData(x, y, 1, 1).data[3] > 0)
      }

      function inspect(): void {
        const current = window.__paperReaderPerformance
        if (!current) return
        const canvas = document.querySelector<HTMLCanvasElement>(
          '[data-test="pdf-page-shell"] canvas',
        )
        const textLayer = document.querySelector<HTMLElement>('[data-test="pdf-text-layer"]')
        if (current.canvasAt === undefined && canvas && canvasHasPaint(canvas)) {
          current.canvasAt = window.performance.now()
        }
        if (current.textAt === undefined && textLayer?.textContent?.trim()) {
          current.textAt = window.performance.now()
        }
        if (current.canvasAt !== undefined && current.textAt !== undefined) {
          observer.disconnect()
          window.cancelAnimationFrame(frame)
          return
        }
        frame = window.requestAnimationFrame(inspect)
      }

      observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true })
      frame = window.requestAnimationFrame(inspect)
    }, { once: true, capture: true })
  })

  await readLink.click()
  await expect.poll(
    () => page.evaluate(() => {
      const marks = window.__paperReaderPerformance
      return marks?.canvasAt !== undefined && marks.textAt !== undefined
    }),
    { timeout: SAMPLE_TIMEOUT_MS },
  ).toBe(true)
  const marks = await page.evaluate(() => window.__paperReaderPerformance)
  if (!marks || marks.canvasAt === undefined || marks.textAt === undefined) {
    throw new Error('Reader performance marks were not recorded')
  }
  const canvasMs = marks.canvasAt - marks.startedAt
  const textMs = marks.textAt - marks.startedAt
  return {
    canvas_ms: roundMilliseconds(canvasMs),
    text_ms: roundMilliseconds(textMs),
    complete_ms: roundMilliseconds(Math.max(canvasMs, textMs)),
  }
}

function roundMilliseconds(value: number): number {
  return Math.round(value * 1_000) / 1_000
}

function summary(values: number[]) {
  const sorted = [...values].sort((left, right) => left - right)
  const percentile95 = sorted[Math.ceil(sorted.length * 0.95) - 1]
  const middle = sorted.length / 2
  return {
    min: roundMilliseconds(sorted[0] ?? 0),
    median: roundMilliseconds((sorted[middle - 1] + sorted[middle]) / 2),
    p95: roundMilliseconds(percentile95 ?? 0),
    max: roundMilliseconds(sorted.at(-1) ?? 0),
  }
}

test('seeded reading briefing meets the local interactive p95 budget', async ({ browser }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium', 'Desktop product budget only')
  test.setTimeout(120_000)

  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const samples: number[] = []
  try {
    await installRoutes(context)
    const page = await context.newPage()
    for (let sample = 0; sample < SAMPLE_COUNT; sample += 1) {
      samples.push(roundMilliseconds(await measureBriefingInteractive(page)))
      await page.goto('about:blank')
    }
  } finally {
    await context.close()
  }

  const evidence = {
    schema_version: 'paper-reader-briefing-performance-v1',
    samples,
    interactive_ms: summary(samples),
    budget_ms: BRIEFING_INTERACTIVE_BUDGET_MS,
  }
  await testInfo.attach('briefing-performance.json', {
    body: Buffer.from(JSON.stringify(evidence, null, 2)),
    contentType: 'application/json',
  })
  process.stdout.write(`PAPER_READER_BRIEFING_PERFORMANCE ${JSON.stringify(evidence)}\n`)

  expect(evidence.interactive_ms.p95).toBeLessThanOrEqual(BRIEFING_INTERACTIVE_BUDGET_MS)
})

test('briefing does not request PDF runtime assets before a reader route', async ({ browser }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium', 'Route-loading contract only')

  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const pdfRuntimeRequests: string[] = []
  try {
    await installRoutes(context)
    const page = await context.newPage()
    page.on('request', (request) => {
      const pathname = new URL(request.url()).pathname
      if (/pdf(?:\.worker|js)/i.test(pathname)) pdfRuntimeRequests.push(pathname)
    })

    await measureBriefingInteractive(page)

    expect(pdfRuntimeRequests).toEqual([])
  } finally {
    await context.close()
  }
})

test('briefing and reader stay below the layout-shift budget', async ({ browser }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium', 'Desktop product budget only')

  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  try {
    await installRoutes(context)
    const page = await context.newPage()
    await installLayoutShiftObserver(page)

    await measureBriefingInteractive(page)
    await settleLayout(page)
    const briefingShift = await page.evaluate(() => window.__paperReaderLayoutShift ?? 0)

    await measureReaderOpen(page)
    await settleLayout(page)
    const readerShift = await page.evaluate(() => window.__paperReaderLayoutShift ?? 0)

    const evidence = {
      schema_version: 'paper-reader-layout-shift-v1',
      briefing: roundMilliseconds(briefingShift),
      reader: roundMilliseconds(readerShift),
      budget: LAYOUT_SHIFT_BUDGET,
    }
    await testInfo.attach('layout-shift.json', {
      body: Buffer.from(JSON.stringify(evidence, null, 2)),
      contentType: 'application/json',
    })
    process.stdout.write(`PAPER_READER_LAYOUT_SHIFT ${JSON.stringify(evidence)}\n`)

    expect(briefingShift).toBeLessThan(LAYOUT_SHIFT_BUDGET)
    expect(readerShift).toBeLessThan(LAYOUT_SHIFT_BUDGET)
  } finally {
    await context.close()
  }
})

test('PDF first canvas and selectable text meet the desktop product budget', async ({ browser }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium', 'Desktop product budget only')
  test.setTimeout(180_000)

  const coldSamples: ReaderSample[] = []
  for (let sample = 0; sample < SAMPLE_COUNT; sample += 1) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    try {
      await installRoutes(context)
      const page = await context.newPage()
      coldSamples.push(await measureReaderOpen(page))
    } finally {
      await context.close()
    }
  }

  const warmContext = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const warmSamples: ReaderSample[] = []
  try {
    await installRoutes(warmContext)
    const page = await warmContext.newPage()
    await measureReaderOpen(page)
    for (let sample = 0; sample < SAMPLE_COUNT; sample += 1) {
      warmSamples.push(await measureReaderOpen(page))
    }
  } finally {
    await warmContext.close()
  }

  const evidence = {
    schema_version: 'paper-reader-pdf-performance-v1',
    platform: process.platform,
    browser: browser.browserType().name(),
    browser_version: browser.version(),
    project: testInfo.project.name,
    samples_per_mode: SAMPLE_COUNT,
    measurement: 'click on the library read action to painted first-page canvas and selectable text',
    cold_context: {
      samples: coldSamples,
      canvas_ms: summary(coldSamples.map((sample) => sample.canvas_ms)),
      text_ms: summary(coldSamples.map((sample) => sample.text_ms)),
      complete_ms: summary(coldSamples.map((sample) => sample.complete_ms)),
    },
    warm_context: {
      samples: warmSamples,
      canvas_ms: summary(warmSamples.map((sample) => sample.canvas_ms)),
      text_ms: summary(warmSamples.map((sample) => sample.text_ms)),
      complete_ms: summary(warmSamples.map((sample) => sample.complete_ms)),
    },
    canvas_budget_ms: PDF_FIRST_CANVAS_BUDGET_MS,
  }
  await testInfo.attach('pdf-performance.json', {
    body: Buffer.from(JSON.stringify(evidence, null, 2)),
    contentType: 'application/json',
  })
  process.stdout.write(`PAPER_READER_PDF_PERFORMANCE ${JSON.stringify(evidence)}\n`)

  expect(evidence.cold_context.canvas_ms.p95).toBeLessThanOrEqual(PDF_FIRST_CANVAS_BUDGET_MS)
  expect(evidence.warm_context.canvas_ms.p95).toBeLessThanOrEqual(PDF_FIRST_CANVAS_BUDGET_MS)
})
