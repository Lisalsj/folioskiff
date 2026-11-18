<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import { computed, ref, toRef } from 'vue'
import 'pdfjs-dist/web/pdf_viewer.css'

import type { components } from '@/api/schema'
import { displayRectFromCanonical } from '@/lib/annotation-geometry'
import type { PdfRotation, PdfSearchResult } from '@/lib/pdf-session'
import { usePdfSelection } from '@/features/pdf/composables/usePdfSelection'
import type { PdfRenderSurface } from '@/features/pdf/composables/usePdfDocument'

type Annotation = components['schemas']['AnnotationResponse']
type PdfRect = components['schemas']['PdfRectResponse']

interface PdfPreviewMark {
  id: string
  pageNumber: number
  pageWidth: number
  pageHeight: number
  rects: PdfRect[]
}

const props = defineProps<{
  title: string
  hasDocument: boolean
  loading: boolean
  errorMessage: string
  rendering: boolean
  page: number
  pageCount: number
  pageSize: { width: number, height: number }
  canonicalPageSize: { width: number, height: number }
  scale: number
  rotation: PdfRotation
  annotations: Annotation[]
  migrationPreview: PdfPreviewMark | null
  query: string
  searching: boolean
  matches: PdfSearchResult[]
}>()

const emit = defineEmits<{
  retry: []
  'selection-change': [selection: import('@/lib/annotation-geometry').PdfTextSelection | null]
  'annotation-select': [annotation: Annotation]
  'select-match': [match: PdfSearchResult]
}>()

const viewport = ref<HTMLElement>()
const pageShell = ref<HTMLElement>()
const canvas = ref<HTMLCanvasElement>()
const textLayer = ref<HTMLDivElement>()
const selection = usePdfSelection({
  page: toRef(props, 'page'),
  pageCount: toRef(props, 'pageCount'),
  scale: toRef(props, 'scale'),
  rotation: toRef(props, 'rotation'),
  canonicalPageSize: toRef(props, 'canonicalPageSize'),
  onChange: (value) => emit('selection-change', value),
})

const pageShellStyle = computed(() => ({
  width: props.pageSize.width ? `${props.pageSize.width}px` : undefined,
  height: props.pageSize.height ? `${props.pageSize.height}px` : undefined,
}))
const annotationOverlays = computed(() => props.annotations
  .filter((annotation) => annotation.page_number === props.page)
  .flatMap((annotation) => annotation.rects.map((rect, index) => {
    const displayed = displayRectFromCanonical(
      rect,
      { width: annotation.page_width, height: annotation.page_height },
      props.rotation,
    )
    return {
      annotation,
      key: `${annotation.id}-${index}`,
      style: {
        left: `${roundPixel(displayed.x0 * props.scale)}px`,
        top: `${roundPixel(displayed.y0 * props.scale)}px`,
        width: `${roundPixel((displayed.x1 - displayed.x0) * props.scale)}px`,
        height: `${roundPixel((displayed.y1 - displayed.y0) * props.scale)}px`,
      },
    }
  })))
const migrationPreviewOverlays = computed(() => {
  const preview = props.migrationPreview
  if (!preview || preview.pageNumber !== props.page) return []
  return preview.rects.map((rect, index) => {
    const displayed = displayRectFromCanonical(
      rect,
      { width: preview.pageWidth, height: preview.pageHeight },
      props.rotation,
    )
    return {
      id: preview.id,
      key: `${preview.id}-${index}`,
      style: {
        left: `${roundPixel(displayed.x0 * props.scale)}px`,
        top: `${roundPixel(displayed.y0 * props.scale)}px`,
        width: `${roundPixel((displayed.x1 - displayed.x0) * props.scale)}px`,
        height: `${roundPixel((displayed.y1 - displayed.y0) * props.scale)}px`,
      },
    }
  })
})

function captureSelection(): void {
  if (textLayer.value && pageShell.value) {
    selection.captureSelection(textLayer.value, pageShell.value)
  }
}

function getSurface(): PdfRenderSurface | null {
  if (!canvas.value || !textLayer.value || !viewport.value) return null
  return { canvas: canvas.value, textLayer: textLayer.value, viewport: viewport.value }
}

function clearSelection(clearBrowserSelection = true): void {
  selection.clearSelection(clearBrowserSelection)
}

function roundPixel(value: number): number {
  return Math.round(value * 100) / 100
}

defineExpose({ clearSelection, getSurface })
</script>

<template>
  <div class="reader-body">
    <div
      ref="viewport"
      class="pdf-viewport"
      data-test="pdf-viewport"
      tabindex="0"
      :aria-label="t('PDF 页面滚动区域')"
    >
      <div v-if="!hasDocument" class="reader-message" data-test="pdf-empty">
        <strong>{{ t('尚未选择可阅读的 PDF') }}</strong>
        <span>{{ t('从论文库选择一个合法可访问的版本后，页面会在这里打开。') }}</span>
      </div>
      <div v-else-if="loading" class="reader-message" role="status" aria-live="polite">
        <strong>{{ t('正在打开 PDF') }}</strong>
        <span>{{ t('首次打开时会加载本地 PDF 阅读组件。') }}</span>
      </div>
      <div v-else-if="errorMessage" class="reader-message" role="alert">
        <strong>{{ errorMessage }}</strong>
        <button type="button" data-test="retry-pdf" @click="emit('retry')">{{ t('重新打开') }}</button>
      </div>
      <div
        v-show="hasDocument && !loading && !errorMessage"
        ref="pageShell"
        class="pdf-page-shell"
        data-test="pdf-page-shell"
        :style="pageShellStyle"
        :aria-busy="rendering"
      >
        <canvas ref="canvas" :aria-label="t('{0}，第 {1} 页', title, page)" />
        <div
          ref="textLayer"
          class="textLayer"
          data-test="pdf-text-layer"
          :aria-label="t('可选择的页面文字')"
          @mouseup="captureSelection"
          @keyup="captureSelection"
        />
        <div class="annotation-overlays">
          <span
            v-for="overlay in migrationPreviewOverlays"
            :key="`migration-${overlay.key}`"
            class="migration-preview-overlay"
            :data-test="`migration-preview-overlay-${overlay.id}`"
            :style="overlay.style"
            aria-hidden="true"
          />
          <button
            v-for="overlay in annotationOverlays"
            :key="overlay.key"
            type="button"
            :data-test="`annotation-overlay-${overlay.annotation.id}`"
            :data-color="overlay.annotation.color"
            :style="overlay.style"
            :aria-label="t('{0}：第 {1} 页{2}', overlay.annotation.kind === 'bookmark' ? t('书签') : t('批注'), overlay.annotation.page_number, overlay.annotation.note ? `，${overlay.annotation.note}` : '')"
            @click.stop="emit('annotation-select', overlay.annotation)"
          />
        </div>
        <span v-if="rendering" class="rendering-note" role="status">{{ t('正在呈现第') }} {{ page }} {{ t('页') }}</span>
      </div>
    </div>

    <aside v-if="query && !loading && !errorMessage" class="search-results" :aria-label="t('全文搜索结果')">
      <header>
        <strong>{{ t('搜索结果') }}</strong>
        <span v-if="searching">{{ t('正在检索') }}</span>
        <span v-else>{{ matches.length }} {{ t('个页面') }}</span>
      </header>
      <p v-if="!searching && matches.length === 0">{{ t('没有找到匹配内容。') }}</p>
      <button
        v-for="match in matches"
        :key="match.pageNumber"
        type="button"
        :data-test="`search-result-${match.pageNumber}`"
        @click="emit('select-match', match)"
      >
        <span>{{ t('第') }} {{ match.pageNumber }} {{ t('页 ·') }} {{ match.occurrences }} {{ t('处') }}</span>
        <small>{{ match.snippet }}</small>
      </button>
    </aside>
  </div>
</template>

<style scoped>
.reader-body {
  position: relative;
  min-height: 0;
  overflow: hidden;
}

.pdf-viewport {
  width: 100%;
  height: 100%;
  overflow: auto;
  padding: 24px;
  background: var(--color-reader-chrome, #e8e5dc);
  scrollbar-gutter: stable;
}

.pdf-viewport:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: -3px;
}

.pdf-page-shell {
  position: relative;
  min-width: 1px;
  min-height: 1px;
  margin: 0 auto;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 8px 24px rgb(30 41 34 / 14%);
}

.pdf-page-shell canvas {
  display: block;
  background: #fff;
  color-scheme: light;
  filter: none !important;
}

.pdf-page-shell .textLayer {
  position: absolute;
  inset: 0;
  overflow: hidden;
  line-height: 1;
  opacity: 1;
}

.annotation-overlays {
  position: absolute;
  z-index: 3;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}

.annotation-overlays button {
  position: absolute;
  box-sizing: border-box;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 2px;
  background: rgb(214 168 75 / 30%);
  mix-blend-mode: multiply;
  pointer-events: auto;
  cursor: pointer;
}

.migration-preview-overlay {
  position: absolute;
  box-sizing: border-box;
  border: 2px dashed #284f3a;
  border-radius: 3px;
  background: rgb(79 118 96 / 16%);
  box-shadow: 0 0 0 2px rgb(255 255 255 / 72%);
  mix-blend-mode: multiply;
}

.annotation-overlays button[data-color='green'] { background: rgb(79 118 96 / 27%); }
.annotation-overlays button[data-color='rose'] { background: rgb(141 74 80 / 25%); }
.annotation-overlays button[data-color='blue'] { background: rgb(79 104 125 / 25%); }

.annotation-overlays button:hover,
.annotation-overlays button:focus-visible {
  border-color: #18251d;
  outline: 2px solid #fff;
  outline-offset: 1px;
}

.rendering-note {
  position: absolute;
  top: 10px;
  right: 10px;
  padding: 5px 8px;
  border-radius: 6px;
  background: rgb(30 41 34 / 82%);
  color: #fff;
  font-size: var(--type-meta);
}

.reader-message {
  width: min(440px, calc(100% - 32px));
  min-height: 180px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 10px;
  margin: max(30px, 12vh) auto 0;
  padding: 28px;
  border-top: 2px solid var(--color-forest);
  background: var(--color-paper);
  color: var(--color-muted);
}

.reader-message strong {
  color: var(--color-ink);
  font-family: var(--font-editorial);
  font-size: 20px;
  font-weight: 500;
  line-height: 1.35;
}

.reader-message span {
  font-size: 13px;
  line-height: 1.65;
}

.reader-message button {
  min-height: 36px;
  margin-top: 4px;
  padding: 0 14px;
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--color-forest);
  color: var(--color-on-accent);
  cursor: pointer;
}

.reader-message button:focus-visible,
.search-results button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.search-results {
  position: absolute;
  z-index: 3;
  top: 8px;
  right: 16px;
  width: min(360px, calc(100% - 32px));
  max-height: min(440px, calc(100% - 24px));
  overflow: auto;
  border: 1px solid var(--color-line);
  background: var(--color-paper);
  box-shadow: 0 14px 34px rgb(30 41 34 / 18%);
}

.search-results header {
  position: sticky;
  top: 0;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--color-line);
  background: var(--color-paper-deep);
  font-size: 12px;
}

.search-results header span,
.search-results > p {
  color: var(--color-muted);
}

.search-results > p {
  margin: 0;
  padding: 18px 14px;
  font-size: 12px;
}

.search-results button {
  width: 100%;
  display: grid;
  gap: 5px;
  padding: 12px 14px;
  border: 0;
  border-bottom: 1px solid var(--color-line);
  background: transparent;
  color: var(--color-ink);
  text-align: left;
  cursor: pointer;
}

.search-results button:hover {
  background: var(--color-forest-hover-soft);
}

.search-results button span {
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
}

.search-results button small {
  color: var(--color-muted-strong);
  font-size: 12px;
  line-height: 1.5;
}

@media (max-width: 760px) {
  .pdf-viewport {
    padding: 16px;
  }
}

@media (forced-colors: active) {
  .annotation-overlays button,
  .migration-preview-overlay {
    border: 2px solid Highlight;
    background: transparent;
    forced-color-adjust: none;
  }
}
</style>
