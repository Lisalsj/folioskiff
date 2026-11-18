<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { PhArrowLeft, PhList } from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import AnalysisPanel from '@/components/AnalysisPanel.vue'
import AnnotationPanel from '@/components/AnnotationPanel.vue'
import ResearchAssistant from '@/features/assistant/components/ResearchAssistant.vue'
import type { AssistantReaderContext } from '@/features/assistant/store'
import ReaderDocumentPane from '@/features/reader/components/ReaderDocumentPane.vue'
import ReaderLibraryPane from '@/features/reader/components/ReaderLibraryPane.vue'
import ReaderMobileTabs from '@/features/reader/components/ReaderMobileTabs.vue'
import ReaderResearchSidebar from '@/features/reader/components/ReaderResearchSidebar.vue'
import {
  useAnnotations,
  type AnnotationCreateIntent,
  type PageAnchor,
} from '@/features/reader/composables/useAnnotations'
import { useReaderWorkspace } from '@/features/reader/composables/useReaderWorkspace'
import { useReadingPosition } from '@/features/reader/composables/useReadingPosition'
import { useResponsiveReader } from '@/features/reader/composables/useResponsiveReader'
import type { PdfTextSelection } from '@/lib/annotation-geometry'

type LibraryDocument = components['schemas']['LibraryDocumentResponse']
type MigrationCandidate = components['schemas']['MigrationCandidateResponse']
type AssistantMode = 'ask' | 'explain' | 'translate' | 'related'

interface PdfReaderController {
  goToPage(value: number): Promise<void>
  requestBookmark(): void
  clearSelection(): void
}

const route = useRoute()
const router = useRouter()
const pdfReader = ref<PdfReaderController>()

const workId = computed(() => String(route.params.workId ?? ''))
const documentVersionId = computed(() => String(route.params.documentVersionId ?? ''))
const requestedAssistantMode = ref<AssistantMode>(
  route.query.task === 'related' ? 'related' : 'ask',
)

const workspace = useReaderWorkspace(workId, documentVersionId)
const {
  work,
  documents,
  selectedDocument,
  contentUrl,
  loading: workspaceLoading,
  errorMessage,
  retry: loadWorkspace,
} = workspace

const contentSha256 = computed(() => selectedDocument.value?.content_sha256 ?? '')
const readingPosition = useReadingPosition(documentVersionId, { contentSha256 })
const {
  position,
  readerState,
  loadedDocumentId: positionDocumentId,
  statusMessage: positionStatus,
  updateState: updateReaderState,
} = readingPosition

const availableDocumentIds = computed(() => documents.value
  .filter((document) => document.available)
  .map((document) => document.id))
const annotationState = useAnnotations(documentVersionId, contentSha256, {
  pageCount: computed(() => readerState.value.pageCount),
  availableDocumentIds,
})
const {
  annotations,
  selection: annotationSelection,
  loading: annotationLoading,
  loadedDocumentId: annotationDocumentId,
  saving: annotationSaving,
  errorMessage: annotationError,
  statusMessage: annotationStatus,
  migrationPreview: annotationMigrationPreview,
  migrationPreviewMark,
  migrationLoading,
  migrationSaving,
  migrationError,
  migrationStatus,
  setSelection,
  clearSelection,
  beginCreate,
  quickHighlight: persistQuickHighlight,
  commitBookmark,
  update: updateAnnotation,
  remove: deleteAnnotation,
  retry: retryAnnotations,
  previewMigration: previewAnnotationMigration,
  previewCandidate,
  decideMigration: decideAnnotationMigration,
} = annotationState

const responsiveReader = useResponsiveReader()
const {
  libraryDrawerOpen,
  isCompactLayout,
  mobilePanelView,
  agentTab,
  reportVisited,
  assistantVisited,
  agentColumn,
  selectAgentTab,
  selectMobilePanel,
  showAgentTab,
  showPdf,
  resetForDocument,
} = responsiveReader

const loading = computed(() => {
  if (workspaceLoading.value) return true
  const document = selectedDocument.value
  if (!document) return false
  return positionDocumentId.value !== document.id
    || annotationDocumentId.value !== document.id
})
const migrationVersionOptions = computed(() => documents.value.map((document) => ({
  id: document.id,
  label: versionLabel(document),
  available: document.available,
})))
const assistantReaderContext = computed<AssistantReaderContext>(() => ({
  currentPage: readerState.value.page,
  pageCount: readerState.value.pageCount,
  selection: annotationSelection.value,
  annotations: annotations.value.map((annotation) => ({
    id: annotation.id,
    pageNumber: annotation.page_number,
    label: annotation.note?.trim()
      || annotation.quote_text?.trim().slice(0, 80)
      || (annotation.kind === 'bookmark' ? t('页面书签') : t('未命名批注')),
  })),
}))

function updateSelection(value: PdfTextSelection | null): void {
  setSelection(value)
}

function clearAnnotationSelection(): void {
  clearSelection()
  pdfReader.value?.clearSelection()
}

function createAnnotation(intent: AnnotationCreateIntent): void {
  void showAgentTab('annotations')
  if (beginCreate(intent)) pdfReader.value?.requestBookmark()
}

function quickHighlight(selection: PdfTextSelection): void {
  void showAgentTab('annotations')
  persistQuickHighlight(selection)
}

function createBookmark(anchor: PageAnchor): void {
  void showAgentTab('annotations')
  void commitBookmark(anchor)
}

async function navigateAnnotation(pageNumber: number): Promise<void> {
  showPdf()
  await pdfReader.value?.goToPage(pageNumber)
}

async function navigateReportPage(pageNumber: number): Promise<void> {
  showPdf()
  await pdfReader.value?.goToPage(pageNumber)
}

async function previewMigrationCandidate(candidate: MigrationCandidate): Promise<void> {
  const pageNumber = previewCandidate(candidate)
  if (pageNumber === null) return
  showPdf()
  await pdfReader.value?.goToPage(pageNumber)
}

function selectAnnotation(): void {
  void showAgentTab('annotations')
}

function openSelectionAssistant(
  mode: Exclude<AssistantMode, 'related'>,
  selection: PdfTextSelection,
): void {
  setSelection(selection)
  requestedAssistantMode.value = mode
  void showAgentTab('assistant')
}

function saveSelectionAnnotation(selection: PdfTextSelection): void {
  quickHighlight(selection)
}

function selectDocument(documentId: string): void {
  libraryDrawerOpen.value = false
  void router.push(`/library/${encodeURIComponent(workId.value)}/read/${encodeURIComponent(documentId)}`)
}

function updateAgentColumn(element: HTMLElement | null): void {
  agentColumn.value = element ?? undefined
}

function openTranslation(artifactId: string): void {
  void router.push({
    name: 'bilingual-reader',
    params: { workId: workId.value, artifactId },
    query: { mode: 'parallel', page: String(readerState.value.page) },
  })
}

function versionLabel(document: LibraryDocument): string {
  if (document.original_filename) return document.original_filename
  return t('版本 ') + document.content_sha256.slice(0, 8)
}

watch(
  () => [route.params.workId, route.params.documentVersionId],
  () => resetForDocument(),
)
watch(
  () => route.query.task,
  (task) => {
    if (task === 'related') {
      requestedAssistantMode.value = 'related'
      void showAgentTab('assistant')
    }
  },
  { immediate: true },
)
</script>

<template>
  <section v-if="loading" class="workspace-state" role="status" aria-live="polite">
    <span>{{ t('阅读工作台') }}</span>
    <h1>{{ t('正在准备阅读工作台') }}</h1>
    <p>{{ t('正在核对论文身份、可访问版本和本地阅读状态。') }}</p>
  </section>

  <section v-else-if="errorMessage" class="workspace-state" role="alert">
    <span>{{ t('无法打开') }}</span>
    <h1>{{ errorMessage }}</h1>
    <div>
      <button type="button" data-test="retry-workspace" @click="loadWorkspace">{{ t('重新读取') }}</button>
      <RouterLink to="/library">{{ t('返回论文库') }}</RouterLink>
    </div>
  </section>

  <section
    v-else-if="work && selectedDocument"
    class="reader-workspace"
    data-test="reader-workspace"
    data-layout="three-column"
  >
    <header class="workspace-bar">
      <button
        type="button"
        class="drawer-toggle"
        data-test="library-drawer-toggle"
        aria-controls="reader-library-column"
        :aria-expanded="libraryDrawerOpen"
        :aria-label="t('打开论文与版本列表')"
        @click="libraryDrawerOpen = !libraryDrawerOpen"
      >
        <PhList :size="18" aria-hidden="true" />
      </button>
      <RouterLink class="back-link" to="/library" :aria-label="t('返回我的论文库')">
        <PhArrowLeft :size="17" aria-hidden="true" />
        <span>{{ t('论文库') }}</span>
      </RouterLink>
      <div class="workspace-title">
        <h1>{{ work.title }}</h1>
        <span>{{ work.authors.slice(0, 3).join('、') || t('作者信息未知') }}</span>
      </div>
      <span class="reader-position" data-test="reader-position" aria-live="polite">
        {{ readerState.page }} / {{ readerState.pageCount || '–' }} {{ t('页') }}
      </span>
    </header>

    <ReaderMobileTabs
      :model-value="mobilePanelView"
      @update:model-value="selectMobilePanel"
    />

    <div class="workspace-columns">
      <ReaderLibraryPane
        :work="work"
        :documents="documents"
        :selected-document-id="selectedDocument.id"
        :open="libraryDrawerOpen"
        :compact="isCompactLayout"
        @close="libraryDrawerOpen = false"
        @select-document="selectDocument"
      />

      <ReaderDocumentPane
        ref="pdfReader"
        :document="selectedDocument"
        :document-url="contentUrl"
        :title="work.title"
        :position="position"
        :annotations="annotations"
        :migration-preview="migrationPreviewMark"
        :selection="annotationSelection"
        :mobile-active="mobilePanelView === 'pdf'"
        @state-change="updateReaderState"
        @selection-change="updateSelection"
        @quick-highlight="quickHighlight"
        @bookmark-request="createBookmark"
        @annotation-select="selectAnnotation"
        @explain="openSelectionAssistant('explain', $event)"
        @translate="openSelectionAssistant('translate', $event)"
        @ask="openSelectionAssistant('ask', $event)"
        @annotate="saveSelectionAnnotation"
      />

      <ReaderResearchSidebar
        :active-tab="agentTab"
        :mobile-active="mobilePanelView !== 'pdf'"
        :report-visited="reportVisited"
        :assistant-visited="assistantVisited"
        @update:active-tab="selectAgentTab"
        @ready="updateAgentColumn"
      >
        <template #annotations>
          <AnnotationPanel
            :annotations="annotations"
            :selection="annotationSelection"
            :current-page="readerState.page"
            :loading="annotationLoading"
            :saving="annotationSaving"
            :error-message="annotationError"
            :status-message="annotationStatus || positionStatus"
            :version-options="migrationVersionOptions"
            :current-document-id="selectedDocument.id"
            :migration-preview="annotationMigrationPreview"
            :migration-loading="migrationLoading"
            :migration-saving="migrationSaving"
            :migration-error="migrationError"
            :migration-status="migrationStatus"
            @create="createAnnotation"
            @update="updateAnnotation"
            @delete="deleteAnnotation"
            @navigate="navigateAnnotation"
            @retry="retryAnnotations"
            @clear-selection="clearAnnotationSelection"
            @preview-migration="previewAnnotationMigration"
            @preview-candidate="previewMigrationCandidate"
            @decide-migration="decideAnnotationMigration"
          />
        </template>

        <template #report>
          <AnalysisPanel
            :work-id="work.id"
            :document-version-id="selectedDocument.id"
            :document-content-sha256="selectedDocument.content_sha256"
            @navigate-page="navigateReportPage"
          />
        </template>

        <template #assistant>
          <ResearchAssistant
            :work-id="work.id"
            :document-version-id="selectedDocument.id"
            :document-content-sha256="selectedDocument.content_sha256"
            :reader-context="assistantReaderContext"
            :initial-mode="requestedAssistantMode"
            @navigate-page="navigateReportPage"
            @open-translation="openTranslation"
          />
        </template>
      </ReaderResearchSidebar>
    </div>
  </section>
</template>

<style scoped>
.reader-workspace {
  height: 100dvh;
  min-height: 560px;
  display: grid;
  grid-template-rows: 58px minmax(0, 1fr);
  overflow: hidden;
  background: var(--color-paper);
}

.workspace-bar {
  position: relative;
  z-index: 7;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 13px;
  padding: 0 128px 0 16px;
  border-bottom: 1px solid var(--color-line);
  background: var(--color-paper-deep);
}

.back-link,
.drawer-toggle {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  background: transparent;
  color: var(--color-muted-strong);
}

.back-link {
  gap: 5px;
  min-height: 34px;
  padding: 0 8px;
  border-radius: var(--radius-sm);
  font-size: var(--type-meta);
  text-decoration: none;
}

.drawer-toggle {
  width: 34px;
  height: 34px;
  display: none;
  padding: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.back-link:hover,
.drawer-toggle:hover {
  background: var(--color-forest-hover-soft);
  color: var(--color-forest);
}

.back-link:focus-visible,
.drawer-toggle:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.workspace-title {
  min-width: 0;
  flex: 1;
}

.workspace-title h1,
.workspace-title span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-title h1 {
  margin: 0;
  color: var(--color-ink);
  font-family: var(--font-editorial);
  font-size: 15px;
  font-weight: 500;
}

.workspace-title span,
.reader-position {
  margin-top: 2px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.reader-position {
  flex: none;
  margin: 0;
  font-variant-numeric: tabular-nums;
}

.workspace-columns {
  position: relative;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(192px, .56fr) minmax(470px, 2.5fr) minmax(260px, .85fr);
  overflow: hidden;
}

.workspace-state {
  max-width: 680px;
  margin: clamp(56px, 10vh, 104px) clamp(24px, 7vw, 88px);
  padding-top: 22px;
  border-top: 2px solid var(--color-forest);
}

.workspace-state > span {
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.12em;
}

.workspace-state h1 {
  margin: 26px 0 12px;
  font-family: var(--font-editorial);
  font-size: clamp(28px, 4vw, 42px);
  font-weight: 500;
  line-height: 1.15;
}

.workspace-state p {
  color: var(--color-muted);
  font-size: 14px;
  line-height: 1.7;
}

.workspace-state div {
  display: flex;
  gap: 10px;
  margin-top: 22px;
}

.workspace-state button,
.workspace-state a {
  min-height: 38px;
  display: inline-flex;
  align-items: center;
  padding: 0 14px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-deep);
  color: var(--color-forest);
  font-size: 12px;
  text-decoration: none;
  cursor: pointer;
}

@media (max-width: 1280px) {
  .workspace-columns {
    grid-template-columns: minmax(0, 1fr) minmax(280px, 340px);
  }

  .drawer-toggle {
    display: grid;
  }
}

@media (max-width: 760px) {
  .reader-workspace {
    height: 100dvh;
    min-height: 480px;
    grid-template-rows: 54px 46px minmax(0, 1fr);
  }

  .workspace-bar {
    padding: 0 112px 0 8px;
  }

  .back-link span,
  .workspace-title span {
    display: none;
  }

  .reader-position {
    font-size: var(--type-meta);
  }

  .workspace-columns {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr);
  }
}
</style>
