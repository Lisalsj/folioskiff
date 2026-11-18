<script setup lang="ts">
import { ref } from 'vue'

import type { components } from '@/api/schema'
import PdfReader from '@/components/PdfReader.vue'
import ContextActionBar from '@/features/reader/components/ContextActionBar.vue'
import type { MigrationPreviewMark, PageAnchor } from '@/features/reader/composables/useAnnotations'
import type { PdfTextSelection } from '@/lib/annotation-geometry'
import type { PdfReaderState } from '@/lib/pdf-session'

type LibraryDocument = components['schemas']['LibraryDocumentResponse']
type ReadingPosition = components['schemas']['ReadingPositionResponse']
type Annotation = components['schemas']['AnnotationResponse']

defineProps<{
  document: LibraryDocument
  documentUrl: string
  title: string
  position: ReadingPosition | null
  annotations: Annotation[]
  migrationPreview: MigrationPreviewMark | null
  selection: PdfTextSelection | null
  mobileActive: boolean
}>()

defineEmits<{
  'state-change': [state: PdfReaderState]
  'selection-change': [selection: PdfTextSelection | null]
  'quick-highlight': [selection: PdfTextSelection]
  'bookmark-request': [anchor: PageAnchor]
  'annotation-select': [annotation: Annotation]
  explain: [selection: PdfTextSelection]
  translate: [selection: PdfTextSelection]
  ask: [selection: PdfTextSelection]
  annotate: [selection: PdfTextSelection]
}>()

interface PdfReaderController {
  goToPage(value: number): Promise<void>
  requestBookmark(): void
  clearSelection(): void
}

const pdfReader = ref<PdfReaderController>()

async function goToPage(value: number): Promise<void> {
  await pdfReader.value?.goToPage(value)
}

function requestBookmark(): void {
  pdfReader.value?.requestBookmark()
}

function clearSelection(): void {
  pdfReader.value?.clearSelection()
}

defineExpose({ goToPage, requestBookmark, clearSelection })
</script>

<template>
  <section
    id="reader-pdf-panel"
    class="pdf-column"
    data-test="pdf-column"
    :data-mobile-active="mobileActive"
  >
    <div data-test="reader-content-url" :data-url="documentUrl" class="content-anchor">
      <PdfReader
        ref="pdfReader"
        :key="document.id"
        :document-url="documentUrl"
        :title="title"
        :initial-page="position?.page_number ?? 1"
        :initial-scale="position?.scale ?? 1.1"
        :fit-initial-width="position === null"
        :initial-rotation="position?.rotation ?? 0"
        :annotations="annotations"
        :migration-preview="migrationPreview"
        @state-change="$emit('state-change', $event)"
        @selection-change="$emit('selection-change', $event)"
        @quick-highlight="$emit('quick-highlight', $event)"
        @bookmark-request="$emit('bookmark-request', $event)"
        @annotation-select="$emit('annotation-select', $event)"
      />
    </div>
    <ContextActionBar
      :selection="selection"
      @explain="$emit('explain', $event)"
      @translate="$emit('translate', $event)"
      @ask="$emit('ask', $event)"
      @annotate="$emit('annotate', $event)"
    />
  </section>
</template>

<style scoped>
.pdf-column,
.content-anchor {
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.pdf-column {
  position: relative;
  overflow: hidden;
}

@media (max-width: 760px) {
  .pdf-column {
    min-height: 0;
    grid-area: 1 / 1;
  }

  .pdf-column[data-mobile-active='false'] {
    display: none;
  }
}
</style>
