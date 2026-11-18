import { expect, type Page, type Route } from '@playwright/test'

const DEFAULT_USER_PREFERENCES = {
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
  updated_at: '2026-07-15T08:00:00Z',
}

const COMPLETED_ONBOARDING = {
  state: {
    current_step: 'complete',
    completed: true,
    steps: {
      storage_privacy: 'completed',
      agents: 'skipped',
      sources: 'skipped',
      reading_preferences: 'skipped',
    },
    privacy_acknowledged_at: '2026-07-15T08:00:00Z',
    updated_at: '2026-07-15T08:00:00Z',
  },
  storage: {
    data_root: 'C:\\paper-reader-e2e',
    writable: true,
    state_writable: true,
    cache_writable: true,
    library_writable: true,
    state_root: 'C:\\paper-reader-e2e\\state',
    cache_root: 'C:\\paper-reader-e2e\\cache',
    library_root: 'C:\\paper-reader-e2e\\library',
    backups_root: 'C:\\paper-reader-e2e\\library\\backups',
  },
  agents: [],
  sources: [],
  retrieval: {
    lexical_enabled: true,
    semantic_enabled: false,
    model_state: 'not_installed',
    degradation_reasons: ['embedding_model_not_enabled'],
  },
  blocking_issues: [],
}

export async function fulfillCompletedOnboarding(route: Route): Promise<boolean> {
  const request = route.request()
  if (request.method() !== 'GET' || new URL(request.url()).pathname !== '/api/v1/onboarding') {
    return false
  }
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(COMPLETED_ONBOARDING),
  })
  return true
}

export async function fulfillDefaultUserPreferences(route: Route): Promise<boolean> {
  const request = route.request()
  if (
    request.method() !== 'GET'
    || new URL(request.url()).pathname !== '/api/v1/settings/preferences'
  ) {
    return false
  }
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(DEFAULT_USER_PREFERENCES),
  })
  return true
}

export interface RecordedTranslationCommand {
  body: Record<string, unknown>
  idempotencyKey: string
}

export interface TranslationFixtureController {
  readonly workId: string
  readonly documentVersionId: string
  readonly selectionArtifactId: string
  readonly fullArtifactId: string
  readonly translationCommands: RecordedTranslationCommand[]
  readonly externalRequests: string[]
  readonly resumeCount: number
  readonly resumeIdempotencyKeys: string[]
  readonly resumedSegmentIds: string[]
}

type TranslationFixtureState =
  | 'idle'
  | 'selection_succeeded'
  | 'full_running'
  | 'full_cancelled'
  | 'full_succeeded'

const TRANSLATION_WORK_ID = 'work-translation-e2e'
const TRANSLATION_DOCUMENT_ID = 'document-translation-e2e'
const TRANSLATION_DOCUMENT_HASH = '7'.repeat(64)
const SELECTION_ARTIFACT_ID = 'translation-selection-e2e'
const FULL_ARTIFACT_ID = 'translation-full-e2e'
const SELECTION_TASK_ID = 'translation-selection-task-e2e'
const FULL_TASK_ID = 'translation-full-task-e2e'
const SELECTION_JOB_ID = 'translation-selection-job-e2e'
const FULL_JOB_ID = 'translation-full-job-e2e'
const FIXTURE_TIME = '2026-08-01T08:00:00Z'

export function buildFixturePdf(pages: string[]): Buffer {
  const objects: string[] = []
  const pageObjectNumbers: number[] = []
  const fontObjectNumber = 3 + pages.length * 2
  pages.forEach((pageText, index) => {
    const pageObjectNumber = 3 + index * 2
    const streamObjectNumber = pageObjectNumber + 1
    pageObjectNumbers.push(pageObjectNumber)
    const stream = `BT /F1 18 Tf 62 724 Td (${pageText}) Tj ET`
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
  let body = '%PDF-1.4\n% paper-reader browser fixture\n'
  const offsets = [0]
  for (const [index, object] of orderedObjects.entries()) {
    offsets.push(Buffer.byteLength(body))
    body += `${index + 1} 0 obj\n${object}\nendobj\n`
  }
  const xrefOffset = Buffer.byteLength(body)
  body += `xref\n0 ${orderedObjects.length + 1}\n0000000000 65535 f \n`
  body += offsets.slice(1).map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')
  body += `trailer\n<< /Size ${orderedObjects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`
  return Buffer.from(body)
}

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

function translationArtifact(
  artifactId: string,
  taskId: string,
  jobId: string,
  scope: 'selection' | 'full_document',
  status: 'running' | 'succeeded' | 'cancelled',
) {
  return {
    id: artifactId,
    work_id: TRANSLATION_WORK_ID,
    version: 1,
    document_version_id: TRANSLATION_DOCUMENT_ID,
    document_content_sha256: TRANSLATION_DOCUMENT_HASH,
    context_snapshot_id: `${artifactId}-context`,
    task_id: taskId,
    job_id: jobId,
    source_language: 'en',
    target_language: 'zh-CN',
    scope,
    status,
    evidence_level: scope === 'selection' ? 'native_text' : 'mixed_text',
    prompt_version: 'translation-v1',
    created_at: FIXTURE_TIME,
    updated_at: FIXTURE_TIME,
    completed_at: status === 'succeeded' || status === 'cancelled' ? FIXTURE_TIME : null,
    terms: scope === 'full_document'
      ? [{
          id: 'term-diffusion-model',
          artifact_id: artifactId,
          source_term: 'Diffusion model',
          normalized_source_term: 'diffusion model',
          target_term: '扩散模型',
          origin: 'user',
          locked: true,
          created_at: FIXTURE_TIME,
          updated_at: FIXTURE_TIME,
        }]
      : [],
  }
}

function selectionSegments() {
  return [{
    segment_id: 'segment-000001',
    segment_index: 1,
    page_number: 1,
    block_numbers: [1],
    source_text: 'Diffusion model',
    source_sha256: '8'.repeat(64),
    translated_text: '扩散模型',
    source_kind: 'native_text',
    ocr_confidence: null,
    status: 'succeeded',
    diagnostic: null,
    created_at: FIXTURE_TIME,
    updated_at: FIXTURE_TIME,
  }]
}

function fullSegments(completed: boolean) {
  return [{
    segment_id: 'segment-000001',
    segment_index: 1,
    page_number: 1,
    block_numbers: [1],
    source_text: 'Diffusion model supports local evidence.',
    source_sha256: '9'.repeat(64),
    translated_text: '扩散模型用于局部证据。',
    source_kind: 'native_text',
    ocr_confidence: null,
    status: 'succeeded',
    diagnostic: null,
    created_at: FIXTURE_TIME,
    updated_at: FIXTURE_TIME,
  }, {
    segment_id: 'segment-000002',
    segment_index: 2,
    page_number: 2,
    block_numbers: [1],
    source_text: 'Related work comes from scanned sources.',
    source_sha256: 'a'.repeat(64),
    translated_text: completed ? '相关工作来自扫描页。' : null,
    source_kind: 'ocr_text',
    ocr_confidence: 0.62,
    status: completed ? 'succeeded' : 'pending',
    diagnostic: null,
    created_at: FIXTURE_TIME,
    updated_at: FIXTURE_TIME,
  }]
}

function translationJob(
  artifactId: string,
  taskId: string,
  jobId: string,
  status: 'running' | 'succeeded' | 'cancelled',
  quick: boolean,
) {
  return {
    artifact_id: artifactId,
    task_id: taskId,
    job_id: jobId,
    kind: 'translation.generate',
    status,
    progress: status === 'succeeded' ? 100 : 50,
    cancel_requested: false,
    quick,
    error_code: null,
    error_message: null,
    created_at: FIXTURE_TIME,
    updated_at: FIXTURE_TIME,
  }
}

function jobSummary(job: ReturnType<typeof translationJob>) {
  return {
    id: job.job_id,
    kind: job.kind,
    status: job.status,
    progress: job.progress,
    cancel_requested: job.cancel_requested,
    pause_requested: false,
  }
}

export async function installTranslationFixture(
  page: Page,
): Promise<TranslationFixtureController> {
  const pdf = buildFixturePdf([
    'Diffusion model supports local evidence.',
    'Related work comes from scanned sources.',
  ])
  const translationCommands: RecordedTranslationCommand[] = []
  const externalRequests: string[] = []
  const resumeIdempotencyKeys: string[] = []
  const resumedSegmentIds: string[] = []
  let state: TranslationFixtureState = 'idle'
  let resumeCount = 0

  const document = {
    id: TRANSLATION_DOCUMENT_ID,
    work_id: TRANSLATION_WORK_ID,
    content_sha256: TRANSLATION_DOCUMENT_HASH,
    access_status: 'user_provided',
    media_type: 'application/pdf',
    completeness: 100,
    extraction_status: 'complete',
    available: true,
    byte_size: pdf.byteLength,
    original_filename: 'translation-evidence.pdf',
    acquisition_kind: 'manual_import',
    last_accessed_at: null,
    created_at: FIXTURE_TIME,
    is_representative: true,
  }
  const work = {
    id: TRANSLATION_WORK_ID,
    title: 'Evidence-Bound Paper Translation',
    authors: ['Lin Chen', 'Avery Singh'],
    abstract: 'A local-first paper translation fixture.',
    doi: '10.1000/translation.e2e',
    published_at: '2026-07-01T00:00:00Z',
    added_via: 'pdf',
    added_at: FIXTURE_TIME,
    favorite: true,
    feedback: 'like',
    pinned: false,
    reading_progress: 0,
    analysis_state: 'not_analyzed',
    document_count: 1,
    representative_document: document,
  }
  const capabilities = [{
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
      model_id: 'fixture-translation-model',
      display_name: 'Fixture translation model',
      supports_vision: true,
      context_window: 128_000,
    }],
    degradation_reasons: [],
  }]

  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.protocol.startsWith('http') && url.origin !== 'http://127.0.0.1:4178') {
      externalRequests.push(request.url())
    }
  })

  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    if (await fulfillDefaultUserPreferences(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()

    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === '/api/v1/jobs' && method === 'GET') {
      if (state === 'selection_succeeded') {
        return json(route, [jobSummary(translationJob(
          SELECTION_ARTIFACT_ID,
          SELECTION_TASK_ID,
          SELECTION_JOB_ID,
          'succeeded',
          true,
        ))])
      }
      if (state === 'full_running' || state === 'full_cancelled' || state === 'full_succeeded') {
        const status = state === 'full_running'
          ? 'running'
          : state === 'full_cancelled'
            ? 'cancelled'
            : 'succeeded'
        return json(route, [jobSummary(translationJob(
          FULL_ARTIFACT_ID,
          FULL_TASK_ID,
          FULL_JOB_ID,
          status,
          false,
        ))])
      }
      return json(route, [])
    }
    if (path === `/api/v1/works/${TRANSLATION_WORK_ID}` && method === 'GET') {
      return json(route, work)
    }
    if (path === `/api/v1/works/${TRANSLATION_WORK_ID}/documents` && method === 'GET') {
      return json(route, [document])
    }
    if (path === `/api/v1/documents/${TRANSLATION_DOCUMENT_ID}/reading-position` && method === 'GET') {
      return json(route, null)
    }
    if (path === `/api/v1/documents/${TRANSLATION_DOCUMENT_ID}/reading-position` && method === 'PUT') {
      const payload = request.postDataJSON() as Record<string, unknown>
      return json(route, {
        document_version_id: TRANSLATION_DOCUMENT_ID,
        ...payload,
        progress: Number(payload.page_number) / Number(payload.page_count) * 100,
        created_at: FIXTURE_TIME,
        updated_at: FIXTURE_TIME,
      })
    }
    if (path === `/api/v1/documents/${TRANSLATION_DOCUMENT_ID}/annotations` && method === 'GET') {
      return json(route, [])
    }
    if (path === `/api/v1/documents/${TRANSLATION_DOCUMENT_ID}/content` && method === 'GET') {
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
    if (path === `/api/v1/analysis/works/${TRANSLATION_WORK_ID}/reports` && method === 'GET') {
      return json(route, [])
    }
    if (path === `/api/v1/assistant/works/${TRANSLATION_WORK_ID}` && method === 'GET') {
      return json(route, {
        work_id: TRANSLATION_WORK_ID,
        session_id: null,
        tasks: [],
        messages: [],
        latest_checkpoint: null,
      })
    }
    if (path === '/api/v1/translations' && method === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>
      translationCommands.push({
        body,
        idempotencyKey: request.headers()['idempotency-key'] ?? '',
      })
      const context = body.context as { scope?: string } | undefined
      if (context?.scope === 'selection') {
        state = 'selection_succeeded'
        return json(route, translationJob(
          SELECTION_ARTIFACT_ID,
          SELECTION_TASK_ID,
          SELECTION_JOB_ID,
          'succeeded',
          true,
        ), 202)
      }
      if (context?.scope === 'full_document') {
        state = 'full_running'
        return json(route, translationJob(
          FULL_ARTIFACT_ID,
          FULL_TASK_ID,
          FULL_JOB_ID,
          'running',
          false,
        ), 202)
      }
      return json(route, { detail: 'Unsupported translation fixture scope' }, 422)
    }
    if (path === `/api/v1/translations/${SELECTION_ARTIFACT_ID}` && method === 'GET') {
      return json(route, translationArtifact(
        SELECTION_ARTIFACT_ID,
        SELECTION_TASK_ID,
        SELECTION_JOB_ID,
        'selection',
        'succeeded',
      ))
    }
    if (path === `/api/v1/translations/${SELECTION_ARTIFACT_ID}/segments` && method === 'GET') {
      return json(route, selectionSegments())
    }
    if (path === `/api/v1/translations/${FULL_ARTIFACT_ID}` && method === 'GET') {
      const status = state === 'full_succeeded'
        ? 'succeeded'
        : state === 'full_cancelled'
          ? 'cancelled'
          : 'running'
      return json(route, translationArtifact(
        FULL_ARTIFACT_ID,
        FULL_TASK_ID,
        FULL_JOB_ID,
        'full_document',
        status,
      ))
    }
    if (path === `/api/v1/translations/${FULL_ARTIFACT_ID}/segments` && method === 'GET') {
      return json(route, fullSegments(state === 'full_succeeded'))
    }
    if (
      (path === `/api/v1/assistant/tasks/${SELECTION_TASK_ID}`
        || path === `/api/v1/assistant/tasks/${FULL_TASK_ID}`)
      && method === 'GET'
    ) {
      return json(route, {
        backend_id: 'codex_cli',
        requested_model_id: 'fixture-translation-model',
        runs: [{ resolved_model_id: 'fixture-translation-model' }],
      })
    }
    if (path === `/api/v1/jobs/${FULL_JOB_ID}/cancel` && method === 'POST') {
      state = 'full_cancelled'
      return json(route, translationJob(
        FULL_ARTIFACT_ID,
        FULL_TASK_ID,
        FULL_JOB_ID,
        'cancelled',
        false,
      ), 202)
    }
    if (path === `/api/v1/translations/${FULL_ARTIFACT_ID}/resume` && method === 'POST') {
      resumeCount += 1
      resumeIdempotencyKeys.push(request.headers()['idempotency-key'] ?? '')
      resumedSegmentIds.splice(0, resumedSegmentIds.length, 'segment-000002')
      state = 'full_succeeded'
      return json(route, translationJob(
        FULL_ARTIFACT_ID,
        FULL_TASK_ID,
        FULL_JOB_ID,
        'succeeded',
        false,
      ), 202)
    }
    return json(route, { detail: `Unhandled translation fixture route: ${method} ${path}` }, 500)
  })

  return {
    workId: TRANSLATION_WORK_ID,
    documentVersionId: TRANSLATION_DOCUMENT_ID,
    selectionArtifactId: SELECTION_ARTIFACT_ID,
    fullArtifactId: FULL_ARTIFACT_ID,
    translationCommands,
    externalRequests,
    get resumeCount() { return resumeCount },
    resumeIdempotencyKeys,
    resumedSegmentIds,
  }
}

export interface RelatedApiRequest {
  method: string
  path: string
  body: Record<string, unknown> | null
}

export interface RelatedResourcesFixtureController {
  readonly workId: string
  readonly documentVersionId: string
  readonly relatedApiRequests: RelatedApiRequest[]
  readonly runRequests: Record<string, unknown>[]
  readonly academicSourceCalls: string[]
  readonly rateLimitedSources: string[]
  readonly rejectedKnowledgeUrls: string[]
  readonly externalBrowserRequests: string[]
  readonly draftCalls: number
  readonly agentRefineCalls: number
  readonly runCalls: number
  readonly externalResearchCalls: number
  readonly saveCalls: number
  readonly duplicateCandidatesCollapsed: number
}

const RELATED_WORK_ID = 'work-related-e2e'
const RELATED_DOCUMENT_ID = 'document-related-e2e'
const RELATED_DOCUMENT_HASH = '6'.repeat(64)
const RELATED_SET_ID = 'related-set-e2e'
const RELATED_JOB_ID = 'related-job-e2e'
const RELATED_FIXTURE_TIME = '2026-08-01T08:00:00Z'

function relatedDiff(addedTerms: string[] = []) {
  return {
    added_terms: addedTerms,
    removed_terms: [],
    moved_terms: [],
    added_source_ids: [],
    removed_source_ids: [],
    summary_changed: false,
    recent_months_changed: false,
  }
}

function baseRelatedIntent() {
  return {
    set_id: RELATED_SET_ID,
    summary: '围绕扩散模型与规划智能体寻找可复现方法和评测工作',
    required_groups: [
      ['diffusion model', '扩散模型'],
      ['planning agent', '规划智能体'],
    ],
    optional_terms: ['evaluation', '评测'],
    excluded_terms: ['survey', '综述'],
    recent_months: 36,
    source_ids: ['arxiv', 'openreview', 'ieee'],
    revision: 1,
    confirmed: false,
    confirmed_at: null,
    diff: relatedDiff(),
  }
}

export async function installRelatedResourcesFixture(
  page: Page,
): Promise<RelatedResourcesFixtureController> {
  const pdf = buildFixturePdf([
    'Diffusion agents require reproducible evaluation.',
    'Related work compares verified methods across academic sources.',
  ])
  const relatedApiRequests: RelatedApiRequest[] = []
  const runRequests: Record<string, unknown>[] = []
  const academicSourceCalls: string[] = []
  const rateLimitedSources = ['ieee']
  const rejectedKnowledgeUrls = ['https://127.0.0.1/private']
  const externalBrowserRequests: string[] = []
  let intent = baseRelatedIntent()
  let draftCalls = 0
  let agentRefineCalls = 0
  let runCalls = 0
  let externalResearchCalls = 0
  let saveCalls = 0
  let hasRun = false
  let includeExternalWeb = false
  let externalWebConsent = false
  let verifiedPaperSaved = false

  const document = {
    id: RELATED_DOCUMENT_ID,
    work_id: RELATED_WORK_ID,
    content_sha256: RELATED_DOCUMENT_HASH,
    access_status: 'user_provided',
    media_type: 'application/pdf',
    completeness: 100,
    extraction_status: 'complete',
    available: true,
    byte_size: pdf.byteLength,
    original_filename: 'related-research-evidence.pdf',
    acquisition_kind: 'manual_import',
    last_accessed_at: null,
    created_at: RELATED_FIXTURE_TIME,
    is_representative: true,
  }
  const work = {
    id: RELATED_WORK_ID,
    title: 'Diffusion Agents for Reproducible Research',
    authors: ['Lin Chen', 'Avery Singh'],
    abstract: 'A fixture paper connecting diffusion models, planning and evaluation.',
    doi: '10.1000/related.e2e',
    published_at: '2026-07-01T00:00:00Z',
    added_via: 'pdf',
    added_at: RELATED_FIXTURE_TIME,
    favorite: true,
    feedback: 'like',
    pinned: false,
    reading_progress: 0,
    analysis_state: 'not_analyzed',
    document_count: 1,
    representative_document: document,
  }
  const capabilities = [{
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
      model_id: 'fixture-related-model',
      display_name: 'Fixture related-research model',
      supports_vision: true,
      context_window: 128_000,
    }],
    degradation_reasons: [],
  }]

  function sourceResults() {
    return [{
      connector_id: 'arxiv',
      status: 'succeeded',
      progress: 100,
      cursor: null,
      checkpoint: { page: 1 },
      candidate_count: 2,
      displayed_count: 1,
      error_code: null,
      error_message: null,
      completed_at: RELATED_FIXTURE_TIME,
    }, {
      connector_id: 'openreview',
      status: 'succeeded',
      progress: 100,
      cursor: null,
      checkpoint: { page: 1, duplicate_of: 'arxiv:2608.00001' },
      candidate_count: 1,
      displayed_count: 0,
      error_code: null,
      error_message: null,
      completed_at: RELATED_FIXTURE_TIME,
    }, {
      connector_id: 'ieee',
      status: 'failed',
      progress: 0,
      cursor: null,
      checkpoint: null,
      candidate_count: 0,
      displayed_count: 0,
      error_code: 'rate_limited',
      error_message: 'Fixture source rate limit',
      completed_at: RELATED_FIXTURE_TIME,
    }]
  }

  function papers() {
    return [{
      id: 'related-paper-verified',
      connector_id: 'arxiv',
      external_id: '2608.00001',
      normalized_doi: '10.1234/verified.related',
      title: 'Verified Diffusion Agent',
      authors: ['Mina Zhao', 'Robin Hart'],
      publication_year: 2026,
      reason: '同时匹配扩散模型、规划智能体与可复现评测。',
      match_evidence: { required_groups: 2, duplicate_sources: ['arxiv', 'openreview'] },
      confidence: 91,
      verification_status: 'verified',
      linked_work_id: 'work-related-verified',
      merge_review_id: null,
      field_provenance: { title: 'arxiv:title', authors: 'arxiv:authors' },
      saved: verifiedPaperSaved,
    }, {
      id: 'related-paper-review',
      connector_id: 'openreview',
      external_id: 'forum-review-candidate',
      normalized_doi: null,
      title: 'Planning Diffusion under Sparse Evidence',
      authors: ['Kai Wu'],
      publication_year: 2026,
      reason: '主题接近，但标题—作者身份只达到人工审阅阈值。',
      match_evidence: { title_author_similarity: 0.86 },
      confidence: 74,
      verification_status: 'pending_verification',
      linked_work_id: null,
      merge_review_id: 'merge-review-related-e2e',
      field_provenance: { title: 'openreview:title' },
      saved: false,
    }]
  }

  function knowledgeResources() {
    return includeExternalWeb ? [{
      id: 'knowledge-official-e2e',
      title: 'Official diffusion guide',
      canonical_url: 'https://docs.example.org/diffusion',
      final_url: 'https://docs.example.org/diffusion',
      domain: 'docs.example.org',
      resource_type: 'official_docs',
      reason: '解释当前论文引用的调度方法与公开接口。',
      publisher: 'Example Research Lab',
      verification_status: 'verified',
      http_status: 200,
      accessed_at: RELATED_FIXTURE_TIME,
      field_provenance: { title: 'verified_http', url: 'agent_proposal' },
    }] : []
  }

  function resourceSet() {
    return {
      id: RELATED_SET_ID,
      work_id: RELATED_WORK_ID,
      document_version_id: RELATED_DOCUMENT_ID,
      document_content_sha256: RELATED_DOCUMENT_HASH,
      context_snapshot_id: 'related-context-e2e',
      intent_revision: intent.revision,
      intent,
      status: hasRun ? 'partial' : intent.confirmed ? 'confirmed' : 'draft',
      include_academic_sources: true,
      include_external_web: includeExternalWeb,
      external_web_consent: externalWebConsent,
      coverage: hasRun ? 67 : 0,
      job_id: hasRun ? RELATED_JOB_ID : null,
      sources: hasRun ? sourceResults() : [],
      papers: hasRun ? papers() : [],
      knowledge_resources: hasRun ? knowledgeResources() : [],
      created_at: RELATED_FIXTURE_TIME,
      updated_at: RELATED_FIXTURE_TIME,
      confirmed_at: intent.confirmed_at,
      completed_at: hasRun ? RELATED_FIXTURE_TIME : null,
    }
  }

  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.protocol.startsWith('http') && url.origin !== 'http://127.0.0.1:4178') {
      externalBrowserRequests.push(request.url())
    }
  })

  await page.route('**/api/v1/**', async (route) => {
    if (await fulfillCompletedOnboarding(route)) return
    if (await fulfillDefaultUserPreferences(route)) return
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    const requestBody = request.postData()
      ? request.postDataJSON() as Record<string, unknown>
      : null

    if (path.startsWith('/api/v1/related-resources')) {
      relatedApiRequests.push({ method, path, body: requestBody })
    }
    if (path === '/api/v1/events' && method === 'GET') return route.abort()
    if (path === '/api/v1/jobs' && method === 'GET') {
      return json(route, hasRun ? [{
        id: RELATED_JOB_ID,
        kind: 'related.search',
        status: 'succeeded',
        progress: 100,
        cancel_requested: false,
        pause_requested: false,
      }] : [])
    }
    if (path === `/api/v1/works/${RELATED_WORK_ID}` && method === 'GET') {
      return json(route, work)
    }
    if (path === `/api/v1/works/${RELATED_WORK_ID}/documents` && method === 'GET') {
      return json(route, [document])
    }
    if (path === `/api/v1/documents/${RELATED_DOCUMENT_ID}/reading-position` && method === 'GET') {
      return json(route, null)
    }
    if (path === `/api/v1/documents/${RELATED_DOCUMENT_ID}/reading-position` && method === 'PUT') {
      const payload = requestBody ?? {}
      return json(route, {
        document_version_id: RELATED_DOCUMENT_ID,
        ...payload,
        progress: Number(payload.page_number) / Number(payload.page_count) * 100,
        created_at: RELATED_FIXTURE_TIME,
        updated_at: RELATED_FIXTURE_TIME,
      })
    }
    if (path === `/api/v1/documents/${RELATED_DOCUMENT_ID}/annotations` && method === 'GET') {
      return json(route, [])
    }
    if (path === `/api/v1/documents/${RELATED_DOCUMENT_ID}/content` && method === 'GET') {
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
    if (path === `/api/v1/analysis/works/${RELATED_WORK_ID}/reports` && method === 'GET') {
      return json(route, [])
    }
    if (path === `/api/v1/assistant/works/${RELATED_WORK_ID}` && method === 'GET') {
      return json(route, {
        work_id: RELATED_WORK_ID,
        session_id: null,
        tasks: [],
        messages: [],
        latest_checkpoint: null,
      })
    }
    if (
      path === `/api/v1/related-resources/works/${RELATED_WORK_ID}/draft`
      && method === 'POST'
    ) {
      draftCalls += 1
      expect(requestBody).toMatchObject({
        context: {
          document_version_id: RELATED_DOCUMENT_ID,
          scope: 'current_page',
          page_number: 1,
        },
      })
      return json(route, intent, 201)
    }
    if (path === `/api/v1/related-resources/${RELATED_SET_ID}/refine` && method === 'POST') {
      agentRefineCalls += 1
      expect(requestBody).toMatchObject({
        instruction: '加入可复现基准，并保留中英文同义词。',
        agent: { backend_id: 'codex_cli' },
      })
      intent = {
        ...intent,
        revision: intent.revision + 1,
        optional_terms: [...intent.optional_terms, 'reproducibility', '可复现性'],
        confirmed: false,
        confirmed_at: null,
        diff: relatedDiff(['reproducibility', '可复现性']),
      }
      return json(route, intent)
    }
    if (path === `/api/v1/related-resources/${RELATED_SET_ID}/intent` && method === 'PUT') {
      const submitted = requestBody?.intent as ReturnType<typeof baseRelatedIntent>
      expect(requestBody?.expected_revision).toBe(intent.revision)
      intent = {
        ...submitted,
        set_id: RELATED_SET_ID,
        revision: intent.revision + 1,
        confirmed: requestBody?.confirm === true,
        confirmed_at: requestBody?.confirm === true ? RELATED_FIXTURE_TIME : null,
      }
      return json(route, intent)
    }
    if (path === `/api/v1/related-resources/${RELATED_SET_ID}/run` && method === 'POST') {
      runCalls += 1
      expect(request.headers()['idempotency-key']).toMatch(/^related-ui-/)
      const options = requestBody ?? {}
      runRequests.push(options)
      includeExternalWeb = options.include_external_web === true
      externalWebConsent = options.external_web_consent === true
      if (options.include_academic_sources === true) {
        academicSourceCalls.splice(0, academicSourceCalls.length, 'arxiv', 'openreview', 'ieee')
      }
      if (includeExternalWeb) externalResearchCalls += 1
      hasRun = true
      return json(route, {
        set_id: RELATED_SET_ID,
        job_id: RELATED_JOB_ID,
        status: 'succeeded',
        progress: 100,
        replayed: false,
      }, 202)
    }
    if (path === `/api/v1/related-resources/${RELATED_SET_ID}` && method === 'GET') {
      return json(route, resourceSet())
    }
    if (
      path === `/api/v1/related-resources/${RELATED_SET_ID}/papers/related-paper-verified/save`
      && method === 'POST'
    ) {
      saveCalls += 1
      expect(request.headers()['idempotency-key']).toMatch(/^related-ui-/)
      verifiedPaperSaved = true
      return json(route, {
        set_id: RELATED_SET_ID,
        candidate_id: 'related-paper-verified',
        saved: true,
        linked_work_id: 'work-related-verified',
        merge_review_id: null,
      })
    }
    return json(route, { detail: `Unhandled related fixture route: ${method} ${path}` }, 500)
  })

  return {
    workId: RELATED_WORK_ID,
    documentVersionId: RELATED_DOCUMENT_ID,
    relatedApiRequests,
    runRequests,
    academicSourceCalls,
    rateLimitedSources,
    rejectedKnowledgeUrls,
    externalBrowserRequests,
    get draftCalls() { return draftCalls },
    get agentRefineCalls() { return agentRefineCalls },
    get runCalls() { return runCalls },
    get externalResearchCalls() { return externalResearchCalls },
    get saveCalls() { return saveCalls },
    duplicateCandidatesCollapsed: 1,
  }
}

export async function selectFixturePdfText(page: Page, text: string): Promise<void> {
  const textLayer = page.locator('[data-test="pdf-text-layer"]')
  await expect(textLayer).toContainText(text, { timeout: 20_000 })
  await textLayer.evaluate((layer, expectedText) => {
    const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT)
    let textNode: Node | null = walker.nextNode()
    while (textNode && !textNode.textContent?.includes(expectedText)) {
      textNode = walker.nextNode()
    }
    if (!textNode?.textContent) throw new Error('PDF selection fixture was not rendered')
    const start = textNode.textContent.indexOf(expectedText)
    const range = document.createRange()
    range.setStart(textNode, start)
    range.setEnd(textNode, start + expectedText.length)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
    layer.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
  }, text)
}

export async function showReaderAssistant(page: Page, projectName: string): Promise<void> {
  if (projectName === 'mobile-chromium') {
    await page.locator('[data-test="mobile-assistant-tab"]').click()
  } else {
    await page.getByRole('tab', { name: '论文助理' }).click()
  }
  await expect(page.locator('[data-test="research-assistant"]')).toBeVisible()
}
