<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import { computed, nextTick, ref, toRef } from 'vue'

import type { components } from '@/api/schema'
import PdfPageSurface from '@/features/pdf/components/PdfPageSurface.vue'
import PdfToolbar from '@/features/pdf/components/PdfToolbar.vue'
import { usePdfDocument } from '@/features/pdf/composables/usePdfDocument'
import { usePdfSearch } from '@/features/pdf/composables/usePdfSearch'
import type { PdfTextSelection } from '@/lib/annotation-geometry'
import type {
  PdfReaderState,
  PdfRotation,
  PdfSession,
} from '@/lib/pdf-session'

type Annotation = components['schemas']['AnnotationResponse']
type PdfRect = components['schemas']['PdfRectResponse']

export interface PdfPreviewMark {
  id: string
  pageNumber: number
  pageWidth: number
  pageHeight: number
  rects: PdfRect[]
}

const props = withDefaults(defineProps<{
  documentUrl: string
  title?: string
  initialPage?: number
  initialScale?: number
  fitInitialWidth?: boolean
  initialRotation?: PdfRotation
  annotations?: Annotation[]
  migrationPreview?: PdfPreviewMark | null
  sessionFactory?: () => PdfSession
}>(), {
  initialPage: 1,
  initialScale: 1.1,
  fitInitialWidth: false,
  initialRotation: 0,
  annotations: () => [],
  migrationPreview: null,
  sessionFactory: undefined,
})

const emit = defineEmits<{
  'state-change': [state: PdfReaderState]
  'selection-change': [selection: PdfTextSelection | null]
  'quick-highlight': [selection: PdfTextSelection]
  'bookmark-request': [anchor: {
    pageNumber: number
    pageCount: number
    pageWidth: number
    pageHeight: number
  }]
  'annotation-select': [annotation: Annotation]
}>()

const pageSurface = ref<InstanceType<typeof PdfPageSurface>>()
const toolbar = ref<InstanceType<typeof PdfToolbar>>()
const activeSelection = ref<PdfTextSelection | null>(null)

const documentController = usePdfDocument({
  documentUrl: toRef(props, 'documentUrl'),
  initialPage: toRef(props, 'initialPage'),
  initialScale: toRef(props, 'initialScale'),
  fitInitialWidth: toRef(props, 'fitInitialWidth'),
  initialRotation: toRef(props, 'initialRotation'),
  sessionFactory: props.sessionFactory,
  getSurface: () => pageSurface.value?.getSurface() ?? null,
  clearSelection,
  onStateChange: emitState,
})
const {
  canonicalPageSize,
  errorMessage,
  fitWidth,
  goToPage,
  handle,
  hasDocument,
  loading,
  page,
  pageCount,
  pageInput,
  pageSize,
  ready,
  rendering,
  retry,
  rotateClockwise,
  rotation,
  scale,
  setSafetyLimitError,
  submitPage,
  zoomBy,
  zoomPercent,
} = documentController

const searchController = usePdfSearch({
  handle,
  goToPage,
  onStateChange: emitState,
  onSafetyLimit: setSafetyLimitError,
})
const {
  matches,
  query,
  resetResults,
  searchDocument,
  searching,
  selectMatch,
} = searchController

const state = computed<PdfReaderState>(() => ({
  page: page.value,
  pageCount: pageCount.value,
  scale: scale.value,
  rotation: rotation.value,
  query: query.value,
}))
const bookmarkReady = computed(() => (
  ready.value && canonicalPageSize.value.width > 0 && canonicalPageSize.value.height > 0
))

function emitState(): void {
  emit('state-change', state.value)
}

function updateSelection(selection: PdfTextSelection | null): void {
  activeSelection.value = selection
  emit('selection-change', selection)
}

function clearSelection(clearBrowserSelection = true): void {
  if (pageSurface.value) {
    pageSurface.value.clearSelection(clearBrowserSelection)
    return
  }
  if (activeSelection.value !== null) updateSelection(null)
}

function requestBookmark(): void {
  if (!bookmarkReady.value) return
  emit('bookmark-request', {
    pageNumber: page.value,
    pageCount: pageCount.value,
    pageWidth: canonicalPageSize.value.width,
    pageHeight: canonicalPageSize.value.height,
  })
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

async function onKeydown(event: KeyboardEvent): Promise<void> {
  if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'f') {
    event.preventDefault()
    await nextTick()
    toolbar.value?.focusSearch()
    return
  }
  if (event.shiftKey && event.key.toLocaleLowerCase() === 'h' && activeSelection.value) {
    event.preventDefault()
    emit('quick-highlight', activeSelection.value)
    return
  }
  if (isEditableTarget(event.target)) return
  if (event.key === 'ArrowLeft' || event.key === 'PageUp') {
    event.preventDefault()
    await goToPage(page.value - 1)
  } else if (event.key === 'ArrowRight' || event.key === 'PageDown') {
    event.preventDefault()
    await goToPage(page.value + 1)
  } else if (event.key === '+' || event.key === '=') {
    event.preventDefault()
    await zoomBy(0.15)
  } else if (event.key === '-') {
    event.preventDefault()
    await zoomBy(-0.15)
  } else if (event.key === '0') {
    event.preventDefault()
    await fitWidth()
  } else if (event.key.toLocaleLowerCase() === 'r') {
    event.preventDefault()
    await rotateClockwise()
  } else if (event.key === 'Escape') {
    resetResults()
  }
}

defineExpose({ state, goToPage, fitWidth, clearSelection, requestBookmark })
</script>

<template>
  <section
    class="pdf-reader"
    data-test="pdf-reader"
    tabindex="0"
    :aria-label="t('PDF 阅读器')"
    @keydown="onKeydown"
  >
    <PdfToolbar
      ref="toolbar"
      :page="page"
      :page-count="pageCount"
      :page-input="pageInput"
      :zoom-percent="zoomPercent"
      :query="query"
      :ready="ready"
      :bookmark-ready="bookmarkReady"
      @update:page-input="pageInput = $event"
      @update:query="query = $event"
      @submit-page="submitPage"
      @previous="goToPage(page - 1)"
      @next="goToPage(page + 1)"
      @zoom-out="zoomBy(-0.15)"
      @zoom-in="zoomBy(0.15)"
      @fit-width="fitWidth"
      @rotate="rotateClockwise"
      @bookmark="requestBookmark"
      @search="searchDocument"
    />

    <PdfPageSurface
      ref="pageSurface"
      :title="title ?? t('论文 PDF')"
      :has-document="hasDocument"
      :loading="loading"
      :error-message="errorMessage"
      :rendering="rendering"
      :page="page"
      :page-count="pageCount"
      :page-size="pageSize"
      :canonical-page-size="canonicalPageSize"
      :scale="scale"
      :rotation="rotation"
      :annotations="annotations"
      :migration-preview="migrationPreview"
      :query="query"
      :searching="searching"
      :matches="matches"
      @retry="retry"
      @selection-change="updateSelection"
      @annotation-select="emit('annotation-select', $event)"
      @select-match="selectMatch"
    />
  </section>
</template>

<style scoped>
.pdf-reader {
  height: 100%;
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  background: var(--color-reader-chrome, #e8e5dc);
  outline: none;
}

.pdf-reader:focus-visible {
  box-shadow: inset 0 0 0 3px var(--color-focus);
}
</style>
