<script setup lang="ts">
import { messageRef, readerText as t } from '@/stores/locale'
import { computed, nextTick, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'
import {
  PhArrowLeft,
  PhCaretLeft,
  PhCaretRight,
  PhColumns,
  PhDownloadSimple,
  PhPrinter,
  PhTextAlignLeft,
  PhTranslate,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { ToggleGroupItem, ToggleGroupRoot } from 'reka-ui'

import BilingualSegment from '@/features/translations/components/BilingualSegment.vue'
import GlossaryEditor from '@/features/translations/components/GlossaryEditor.vue'
import TranslationProgress from '@/features/translations/components/TranslationProgress.vue'
import {
  evidenceLabel,
  languageLabel,
  scopeLabel,
  translationExportUrl,
  type BilingualMode,
} from '@/features/translations/api'
import { useTranslationStore } from '@/features/translations/store'

const route = useRoute()
const router = useRouter()
const store = useTranslationStore()
const {
  active,
  agentLabel,
  artifact,
  diagnostics,
  error,
  hasStaleSegments,
  loading,
  pageRange,
  progress,
  savingTermId,
  segments,
  statusMessage,
  submittedJob,
  submitting,
  trackedJob,
} = storeToRefs(store)

const mode = ref<BilingualMode>('parallel')
const currentPage = ref(1)
const selectedSegmentId = ref('')
const identityError = messageRef(t)

const workId = computed(() => String(route.params.workId ?? ''))
const artifactId = computed(() => String(route.params.artifactId ?? ''))
const pages = computed(() => [...new Set(segments.value.map((segment) => segment.page_number))]
  .sort((left, right) => left - right))
const pageIndex = computed(() => pages.value.indexOf(currentPage.value))
const visibleSegments = computed(() => segments.value.filter(
  (segment) => segment.page_number === currentPage.value,
))
const status = computed(() => (
  trackedJob.value?.status
  ?? submittedJob.value?.status
  ?? artifact.value?.status
  ?? 'draft'
))
const cancelRequested = computed(() => (
  trackedJob.value?.cancel_requested
  ?? submittedJob.value?.cancel_requested
  ?? false
))
const draftExport = computed(() => artifact.value?.status !== 'succeeded')
const markdownUrl = computed(() => artifact.value
  ? translationExportUrl(artifact.value.id, 'markdown', draftExport.value)
  : '#')
const htmlUrl = computed(() => artifact.value
  ? translationExportUrl(artifact.value.id, 'html', draftExport.value)
  : '#')
const printUrl = computed(() => artifact.value
  ? translationExportUrl(artifact.value.id, 'print', draftExport.value)
  : '#')

function readMode(value: unknown): BilingualMode {
  return value === 'source' || value === 'target' || value === 'parallel' ? value : 'parallel'
}

function readPage(value: unknown): number | undefined {
  const parsed = Number(Array.isArray(value) ? value[0] : value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined
}

async function replaceQuery(next: Record<string, string | undefined>): Promise<void> {
  const query = { ...route.query }
  for (const [key, value] of Object.entries(next)) {
    if (value === undefined) delete query[key]
    else query[key] = value
  }
  await router.replace({ query })
}

function updateMode(value: unknown): void {
  const next = readMode(value)
  mode.value = next
  void replaceQuery({ mode: next })
}

function goToPage(page: number): void {
  if (!pages.value.includes(page)) return
  currentPage.value = page
  selectedSegmentId.value = ''
  void replaceQuery({ page: String(page), segment: undefined })
}

function selectSegment(segmentId: string): void {
  selectedSegmentId.value = segmentId
  void replaceQuery({ page: String(currentPage.value), segment: segmentId })
}

async function syncPositionFromQuery(): Promise<void> {
  const requestedSegment = typeof route.query.segment === 'string' ? route.query.segment : ''
  const segment = segments.value.find((item) => item.segment_id === requestedSegment)
  const requestedPage = segment?.page_number ?? readPage(route.query.page)
  if (requestedPage && pages.value.includes(requestedPage)) currentPage.value = requestedPage
  selectedSegmentId.value = segment?.segment_id ?? ''
  await nextTick()
  if (selectedSegmentId.value) {
    document.getElementById(`translation-${selectedSegmentId.value}`)
      ?.scrollIntoView?.({ block: 'center' })
  }
}

async function loadArtifact(id: string): Promise<void> {
  identityError.value = ''
  if (!id) {
    identityError.value = '译文标识无效。'
    return
  }
  if (!await store.load(id)) return
  if (artifact.value?.work_id !== workId.value) {
    identityError.value = '译文与当前论文不匹配。'
    return
  }
  currentPage.value = pages.value[0] ?? 1
  selectedSegmentId.value = ''
  await syncPositionFromQuery()
  mode.value = readMode(route.query.mode)
  await replaceQuery({
    mode: mode.value,
    page: String(currentPage.value),
    segment: selectedSegmentId.value || undefined,
  })
  await syncPositionFromQuery()
}

watch(artifactId, (id) => void loadArtifact(id), { immediate: true })
watch(
  () => route.query.mode,
  (value) => { mode.value = readMode(value) },
)
watch(
  () => [
    route.query.page,
    route.query.segment,
    segments.value.map((segment) => segment.segment_id).join(':'),
  ],
  () => void syncPositionFromQuery(),
)
</script>

<template>
  <section v-if="loading" class="page-state" role="status" aria-live="polite">
    <span>{{ t('双语阅读') }}</span>
    <h1>{{ t('正在整理译文段落') }}</h1>
    <p>{{ t('正在核对论文版本、段落锚点与翻译任务状态。') }}</p>
  </section>

  <section v-else-if="error || identityError || !artifact" class="page-state error-state" role="alert">
    <span>{{ t('双语阅读') }}</span>
    <h1>{{ t('暂时无法打开这份译文') }}</h1>
    <p>{{ identityError || error || t('译文不存在或已不可用。') }}</p>
    <button type="button" @click="loadArtifact(artifactId)">{{ t('重试') }}</button>
  </section>

  <section v-else class="bilingual-reader" data-test="bilingual-reader">
    <header class="reader-header">
      <RouterLink
        :to="{
          name: 'reader-workspace',
          params: { workId, documentVersionId: artifact.document_version_id },
        }"
        class="back-link"
        :aria-label="t('返回 PDF 阅读工作台')"
      >
        <PhArrowLeft :size="16" aria-hidden="true" />
      </RouterLink>
      <div class="reader-title">
        <span>{{ t('双语译文 · v') }}{{ artifact.version }}</span>
        <h1>{{ languageLabel(artifact.source_language) }} → {{ languageLabel(artifact.target_language) }}</h1>
      </div>
      <div class="header-meta">
        <span>{{ scopeLabel(artifact.scope) }}</span>
        <span>{{ pageRange }}</span>
      </div>
    </header>

    <nav class="reader-toolbar" :aria-label="t('双语阅读工具')">
      <ToggleGroupRoot
        class="view-modes"
        type="single"
        :model-value="mode"
        :aria-label="t('译文显示方式')"
        @update:model-value="updateMode"
      >
        <ToggleGroupItem value="parallel" data-test="translation-mode-parallel">
          <PhColumns :size="14" aria-hidden="true" />
          {{ t('对照') }}
        </ToggleGroupItem>
        <ToggleGroupItem value="source" data-test="translation-mode-source">
          <PhTextAlignLeft :size="14" aria-hidden="true" />
          {{ t('原文') }}
        </ToggleGroupItem>
        <ToggleGroupItem value="target" data-test="translation-mode-target">
          <PhTranslate :size="14" aria-hidden="true" />
          {{ t('译文') }}
        </ToggleGroupItem>
      </ToggleGroupRoot>

      <div class="page-navigation" :aria-label="t('译文分页')">
        <button
          type="button"
          :aria-label="t('上一页')"
          :disabled="pageIndex <= 0"
          @click="goToPage(pages[pageIndex - 1]!)"
        >
          <PhCaretLeft :size="14" aria-hidden="true" />
        </button>
        <label>
          <span class="sr-only">{{ t('当前页') }}</span>
          <select :value="currentPage" @change="goToPage(Number(($event.target as HTMLSelectElement).value))">
            <option v-for="page in pages" :key="page" :value="page">{{ t('第') }} {{ page }} {{ t('页') }}</option>
          </select>
        </label>
        <button
          type="button"
          :aria-label="t('下一页')"
          :disabled="pageIndex < 0 || pageIndex >= pages.length - 1"
          @click="goToPage(pages[pageIndex + 1]!)"
        >
          <PhCaretRight :size="14" aria-hidden="true" />
        </button>
      </div>

      <div class="export-actions" :aria-label="t('导出译文')">
        <a :href="markdownUrl" download>
          <PhDownloadSimple :size="14" aria-hidden="true" />
          Markdown
        </a>
        <a :href="htmlUrl" download>
          <PhDownloadSimple :size="14" aria-hidden="true" />
          HTML
        </a>
        <a :href="printUrl" target="_blank" rel="noopener noreferrer">
          <PhPrinter :size="14" aria-hidden="true" />
          {{ t('打印') }}
        </a>
      </div>
    </nav>

    <div class="reader-layout">
      <section class="translation-document" :aria-label="t('双语译文内容')" tabindex="-1">
        <div class="page-marker">
          <span>Page</span>
          <strong>{{ currentPage }}</strong>
        </div>
        <div v-if="visibleSegments.length" class="segment-list">
          <BilingualSegment
            v-for="segment in visibleSegments"
            :key="segment.segment_id"
            :segment="segment"
            :mode="mode"
            :active="selectedSegmentId === segment.segment_id"
            :source-language="artifact.source_language"
            :target-language="artifact.target_language"
            @select="selectSegment"
          />
        </div>
        <div v-else class="empty-page">
          <h2>{{ t('这一页还没有可读段落') }}</h2>
          <p>{{ t('任务可能仍在处理，或本页文本提取失败。诊断信息会保留在右侧。') }}</p>
        </div>
      </section>

      <aside class="translation-sidebar" :aria-label="t('译文详情与术语')">
        <section class="artifact-summary">
          <span class="eyebrow">Artifact</span>
          <h2>{{ t('译文状态') }}</h2>
          <dl>
            <div><dt>Agent</dt><dd>{{ agentLabel }}</dd></div>
            <div><dt>{{ t('证据') }}</dt><dd>{{ evidenceLabel(artifact.evidence_level) }}</dd></div>
            <div><dt>{{ t('提示词') }}</dt><dd>{{ artifact.prompt_version }}</dd></div>
          </dl>
          <TranslationProgress
            :status="status"
            :completed="progress.completed"
            :total="progress.total"
            :percent="progress.percent"
            :active="active"
            :cancel-requested="cancelRequested"
            :diagnostics="diagnostics"
            :busy="submitting"
            @cancel="store.cancel"
            @resume="store.resume"
          />
          <p v-if="statusMessage" class="status-message" role="status">{{ statusMessage }}</p>
          <p v-if="hasStaleSegments" class="stale-warning" role="alert">
            <PhWarningCircle :size="15" aria-hidden="true" />
            {{ t('有段落因术语变化而过期，请点击“继续翻译”显式生成新译文。') }}
          </p>
        </section>

        <GlossaryEditor
          :terms="artifact.terms"
          :saving-term-id="savingTermId"
          :immutable="!['draft', 'running'].includes(artifact.status)"
          @update-term="store.updateTerm"
        />
      </aside>
    </div>
  </section>
</template>

<style scoped>
.page-state {
  min-height: min(70vh, 620px);
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 8px;
  padding: 28px;
  text-align: center;
}

.page-state span,
.eyebrow {
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.page-state h1 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: 28px;
  font-weight: 500;
}

.page-state p {
  max-width: 520px;
  margin: 0;
  color: var(--color-muted-strong);
  line-height: 1.6;
}

.page-state button {
  min-height: 36px;
  margin-top: 8px;
  padding: 0 14px;
  border: 1px solid var(--color-forest);
  border-radius: var(--radius-sm);
  background: var(--color-forest);
  color: var(--color-paper);
  cursor: pointer;
}

.bilingual-reader {
  min-height: 100dvh;
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  background: var(--color-paper);
  color: var(--color-ink);
}

.reader-header {
  min-height: 66px;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 8px 78px 8px 18px;
  border-bottom: 1px solid var(--color-line);
  background: var(--color-paper-deep);
}

.back-link,
.reader-toolbar button,
.reader-toolbar a,
.page-navigation,
.view-modes {
  display: flex;
  align-items: center;
}

.back-link {
  width: 34px;
  height: 34px;
  justify-content: center;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  color: var(--color-forest);
}

.reader-title {
  min-width: 0;
}

.reader-title span,
.header-meta {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.reader-title h1 {
  margin: 2px 0 0;
  font-family: var(--font-editorial);
  font-size: 18px;
  font-weight: 500;
}

.header-meta {
  display: grid;
  justify-items: end;
  gap: 2px;
  white-space: nowrap;
}

.reader-toolbar {
  position: sticky;
  z-index: 4;
  top: 0;
  min-height: 48px;
  display: grid;
  grid-template-columns: auto minmax(150px, 1fr) auto;
  align-items: center;
  gap: 14px;
  padding: 6px 18px;
  border-bottom: 1px solid var(--color-line);
  background: color-mix(in srgb, var(--color-paper-deep) 94%, transparent);
  backdrop-filter: blur(8px);
}

.view-modes {
  padding: 2px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
}

.reader-toolbar button,
.reader-toolbar a {
  min-height: 30px;
  justify-content: center;
  gap: 5px;
  padding: 0 8px;
  border: 0;
  border-radius: calc(var(--radius-sm) - 2px);
  background: transparent;
  color: var(--color-muted-strong);
  font: inherit;
  font-size: var(--type-meta);
  text-decoration: none;
  cursor: pointer;
}

.view-modes button[data-state='on'] {
  background: var(--color-forest-soft);
  color: var(--color-forest);
  font-weight: 700;
}

.page-navigation {
  justify-self: center;
  gap: 3px;
}

.page-navigation select {
  min-height: 30px;
  padding: 0 22px 0 8px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-raised);
  color: var(--color-ink);
  font: inherit;
  font-size: var(--type-meta);
}

.export-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.reader-toolbar :is(button, a, select):focus-visible,
.back-link:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.reader-toolbar button:disabled {
  cursor: not-allowed;
  opacity: 0.42;
}

.reader-layout {
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(270px, 330px);
}

.translation-document {
  min-width: 0;
  padding: 32px clamp(22px, 5vw, 74px) 70px;
  overflow: auto;
}

.page-marker {
  display: flex;
  align-items: baseline;
  gap: 7px;
  max-width: 1120px;
  margin: 0 auto 2px;
  color: var(--color-muted);
  font-size: var(--type-meta);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.page-marker strong {
  color: var(--color-forest);
  font-family: var(--font-editorial);
  font-size: 24px;
  font-weight: 500;
}

.segment-list,
.empty-page {
  max-width: 1120px;
  margin: 0 auto;
}

.empty-page {
  padding: 60px 0;
  text-align: center;
}

.empty-page h2 {
  margin: 0;
  font-family: var(--font-editorial);
  font-weight: 500;
}

.empty-page p {
  max-width: 480px;
  margin: 8px auto 0;
  color: var(--color-muted);
  font-size: var(--type-meta);
  line-height: 1.6;
}

.translation-sidebar {
  min-height: 0;
  padding: 18px 16px 36px;
  overflow: auto;
  border-left: 1px solid var(--color-line);
  background: var(--color-paper-deep);
}

.artifact-summary {
  display: grid;
  gap: 11px;
  margin-bottom: 24px;
}

.artifact-summary h2 {
  margin: -7px 0 0;
  font-family: var(--font-editorial);
  font-size: 18px;
  font-weight: 500;
}

.artifact-summary dl {
  display: grid;
  gap: 5px;
  margin: 0;
}

.artifact-summary dl > div {
  display: grid;
  grid-template-columns: 50px minmax(0, 1fr);
  gap: 7px;
  font-size: var(--type-meta);
  line-height: 1.5;
}

.artifact-summary dt {
  color: var(--color-muted);
}

.artifact-summary dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  color: var(--color-muted-strong);
}

.status-message,
.stale-warning {
  margin: 0;
  color: var(--color-forest);
  font-size: var(--type-meta);
  line-height: 1.5;
}

.stale-warning {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 6px;
  color: var(--color-wine);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 920px) {
  .reader-toolbar {
    grid-template-columns: auto 1fr;
  }

  .export-actions {
    grid-column: 1 / -1;
    justify-content: flex-end;
  }

  .reader-layout {
    grid-template-columns: 1fr;
  }

  .translation-sidebar {
    border-top: 1px solid var(--color-line);
    border-left: 0;
  }
}

@media (max-width: 620px) {
  .reader-header {
    grid-template-columns: auto minmax(0, 1fr);
    padding-inline: 10px;
  }

  .header-meta {
    display: none;
  }

  .reader-toolbar {
    grid-template-columns: 1fr;
    gap: 6px;
    padding-inline: 10px;
  }

  .view-modes,
  .page-navigation {
    width: 100%;
  }

  .view-modes button,
  .page-navigation label {
    flex: 1;
  }

  .page-navigation select {
    width: 100%;
  }

  .export-actions {
    grid-column: auto;
    justify-content: center;
  }

  .translation-document {
    padding: 24px 16px 50px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .reader-toolbar {
    backdrop-filter: none;
  }
}
</style>
