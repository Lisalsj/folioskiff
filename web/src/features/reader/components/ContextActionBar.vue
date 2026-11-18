<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import { computed, nextTick, onMounted, onScopeDispose, ref, watch } from 'vue'

import type { PdfTextSelection } from '@/lib/annotation-geometry'

const props = defineProps<{
  selection: PdfTextSelection | null
}>()

const emit = defineEmits<{
  explain: [selection: PdfTextSelection]
  translate: [selection: PdfTextSelection]
  ask: [selection: PdfTextSelection]
  annotate: [selection: PdfTextSelection]
}>()

const actionBar = ref<HTMLElement>()
const open = ref(false)
const position = ref({ left: 8, top: 8 })
let returnFocus: HTMLElement | null = null

const verifiedSelection = computed(() => (
  props.selection?.verified ? props.selection : null
))

function selectionRect(): DOMRect | null {
  const browserSelection = window.getSelection()
  if (browserSelection && browserSelection.rangeCount === 1) {
    const range = browserSelection.getRangeAt(0)
    if (typeof range.getBoundingClientRect === 'function') {
      const rect = range.getBoundingClientRect()
      if (rect.width > 0 || rect.height > 0) return rect
    }
  }
  const rect = verifiedSelection.value?.rects.at(-1)
  if (!rect) return null
  return {
    left: rect.x0,
    top: rect.y0,
    right: rect.x1,
    bottom: rect.y1,
    width: rect.x1 - rect.x0,
    height: rect.y1 - rect.y0,
    x: rect.x0,
    y: rect.y0,
    toJSON: () => ({}),
  }
}

async function updatePosition(): Promise<void> {
  await nextTick()
  if (!open.value || !verifiedSelection.value) return
  const anchor = selectionRect()
  if (!anchor) return
  const barRect = actionBar.value?.getBoundingClientRect()
  const width = barRect?.width || 344
  const height = barRect?.height || 44
  const margin = 8
  const gap = 10
  const maximumLeft = Math.max(margin, window.innerWidth - width - margin)
  const maximumTop = Math.max(margin, window.innerHeight - height - margin)
  const left = Math.min(
    maximumLeft,
    Math.max(margin, anchor.left + anchor.width / 2 - width / 2),
  )
  const preferredTop = anchor.top - height - gap
  const fallbackTop = anchor.bottom + gap
  const top = Math.min(
    maximumTop,
    Math.max(margin, preferredTop >= margin ? preferredTop : fallbackTop),
  )
  position.value = { left, top }
}

function run(
  action: 'explain' | 'translate' | 'ask' | 'annotate',
  event: MouseEvent,
): void {
  const selection = verifiedSelection.value
  if (!selection) return
  if (action === 'explain') emit('explain', selection)
  if (action === 'translate') emit('translate', selection)
  if (action === 'ask') emit('ask', selection)
  if (action === 'annotate') emit('annotate', selection)
  const button = event.currentTarget as HTMLButtonElement
  void nextTick(() => button.focus())
}

function closeAndRestoreFocus(): void {
  if (!open.value) return
  open.value = false
  void nextTick(() => returnFocus?.focus())
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  event.preventDefault()
  closeAndRestoreFocus()
}

watch(verifiedSelection, (selection) => {
  if (!selection) {
    open.value = false
    return
  }
  returnFocus = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null
  open.value = true
  void updatePosition()
}, { immediate: true })

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
  window.addEventListener('resize', updatePosition)
  void updatePosition()
})
onScopeDispose(() => {
  document.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('resize', updatePosition)
})
</script>

<template>
  <div
    v-if="open && verifiedSelection"
    ref="actionBar"
    class="context-action-bar"
    data-test="context-action-bar"
    role="toolbar"
    :aria-label="t('已选文字操作')"
    :style="{ left: `${position.left}px`, top: `${position.top}px` }"
  >
    <button data-test="context-explain" type="button" @click="run('explain', $event)">{{ t('解释') }}</button>
    <button data-test="context-translate" type="button" @click="run('translate', $event)">{{ t('翻译') }}</button>
    <button data-test="context-ask" type="button" @click="run('ask', $event)">{{ t('引用到提问') }}</button>
    <button data-test="context-annotate" type="button" @click="run('annotate', $event)">{{ t('保存标注') }}</button>
  </div>
</template>

<style scoped>
.context-action-bar {
  position: fixed;
  z-index: 20;
  display: flex;
  align-items: center;
  padding: 4px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  box-shadow: 0 10px 28px rgb(30 41 34 / 18%);
  background: var(--color-paper-raised);
}

button {
  min-height: 36px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  font-weight: 650;
  cursor: pointer;
}

button:hover {
  background: var(--color-forest-hover-soft);
  color: var(--color-forest);
}

button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

@media (max-width: 520px) {
  .context-action-bar {
    max-width: calc(100vw - 16px);
    overflow-x: auto;
  }

  button {
    min-width: max-content;
  }
}
</style>
