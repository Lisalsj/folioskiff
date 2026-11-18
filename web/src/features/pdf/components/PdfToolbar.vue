<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import { ref } from 'vue'
import {
  PhArrowClockwise,
  PhArrowLeft,
  PhArrowRight,
  PhArrowsOutLineHorizontal,
  PhBookmarkSimple,
  PhMagnifyingGlass,
  PhMinus,
  PhPlus,
} from '@phosphor-icons/vue'

defineProps<{
  page: number
  pageCount: number
  pageInput: string
  zoomPercent: string
  query: string
  ready: boolean
  bookmarkReady: boolean
}>()

const emit = defineEmits<{
  'update:pageInput': [value: string]
  'update:query': [value: string]
  'submit-page': []
  previous: []
  next: []
  'zoom-out': []
  'zoom-in': []
  'fit-width': []
  rotate: []
  bookmark: []
  search: []
}>()

const searchInput = ref<HTMLInputElement>()

function inputValue(event: Event): string {
  return (event.target as HTMLInputElement).value
}

function focusSearch(): void {
  searchInput.value?.focus()
}

defineExpose({ focusSearch })
</script>

<template>
  <header class="pdf-toolbar" :aria-label="t('PDF 工具栏')">
    <div class="page-controls" :aria-label="t('翻页')">
      <button
        type="button"
        :aria-label="t('上一页')"
        aria-keyshortcuts="ArrowLeft PageUp"
        :disabled="page <= 1 || !ready"
        @click="emit('previous')"
      >
        <PhArrowLeft :size="17" aria-hidden="true" />
      </button>
      <form data-test="page-form" class="page-form" @submit.prevent="emit('submit-page')">
        <label class="sr-only" for="pdf-page-input">{{ t('跳转到页码') }}</label>
        <input
          id="pdf-page-input"
          :value="pageInput"
          data-test="page-input"
          type="number"
          inputmode="numeric"
          min="1"
          :max="pageCount || 1"
          :disabled="!ready"
          @input="emit('update:pageInput', inputValue($event))"
        >
        <span data-test="page-status" aria-live="polite">{{ page }} / {{ pageCount || '–' }}</span>
      </form>
      <button
        type="button"
        :aria-label="t('下一页')"
        aria-keyshortcuts="ArrowRight PageDown"
        :disabled="page >= pageCount || !ready"
        @click="emit('next')"
      >
        <PhArrowRight :size="17" aria-hidden="true" />
      </button>
    </div>

    <div class="view-controls" :aria-label="t('页面显示')">
      <button type="button" :aria-label="t('缩小')" aria-keyshortcuts="-" :disabled="!ready" @click="emit('zoom-out')">
        <PhMinus :size="16" aria-hidden="true" />
      </button>
      <span class="zoom-value" aria-live="polite">{{ zoomPercent }}</span>
      <button type="button" :aria-label="t('放大')" aria-keyshortcuts="+" :disabled="!ready" @click="emit('zoom-in')">
        <PhPlus :size="16" aria-hidden="true" />
      </button>
      <button type="button" :aria-label="t('适合宽度')" aria-keyshortcuts="0" :disabled="!ready" @click="emit('fit-width')">
        <PhArrowsOutLineHorizontal :size="17" aria-hidden="true" />
      </button>
      <button type="button" :aria-label="t('顺时针旋转')" aria-keyshortcuts="R" :disabled="!ready" @click="emit('rotate')">
        <PhArrowClockwise :size="17" aria-hidden="true" />
      </button>
      <button
        type="button"
        data-test="bookmark-page"
        :aria-label="t('为当前页添加书签')"
        :disabled="!bookmarkReady"
        @click="emit('bookmark')"
      >
        <PhBookmarkSimple :size="17" aria-hidden="true" />
      </button>
    </div>

    <form data-test="search-form" class="search-form" role="search" @submit.prevent="emit('search')">
      <PhMagnifyingGlass :size="16" aria-hidden="true" />
      <label class="sr-only" for="pdf-search-input">{{ t('在论文中搜索') }}</label>
      <input
        id="pdf-search-input"
        ref="searchInput"
        :value="query"
        data-test="search-input"
        :aria-label="t('在论文中搜索')"
        :placeholder="t('搜索全文')"
        type="search"
        :disabled="!ready"
        @input="emit('update:query', inputValue($event))"
      >
    </form>
  </header>
</template>

<style scoped>
.pdf-toolbar {
  position: relative;
  z-index: 2;
  min-height: 52px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 7px 12px;
  border-bottom: 1px solid var(--color-line);
  background: var(--color-paper-deep);
}

.page-controls,
.view-controls,
.page-form,
.search-form {
  display: flex;
  align-items: center;
}

.page-controls,
.view-controls {
  gap: 3px;
}

.pdf-toolbar button {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted-strong);
  cursor: pointer;
}

.pdf-toolbar button:hover:not(:disabled) {
  border-color: var(--color-line);
  background: var(--color-paper);
  color: var(--color-forest);
}

.pdf-toolbar button:focus-visible,
.pdf-toolbar input:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.pdf-toolbar button:disabled {
  color: var(--color-muted);
  cursor: default;
  opacity: 0.42;
}

.page-form {
  gap: 5px;
  color: var(--color-muted);
  font-size: 12px;
}

.page-form input {
  width: 42px;
  height: 30px;
  padding: 0 4px;
  border: 1px solid var(--color-line);
  border-radius: 6px;
  background: var(--color-paper);
  color: var(--color-ink);
  text-align: center;
}

.zoom-value {
  min-width: 44px;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  text-align: center;
}

.search-form {
  min-width: 150px;
  max-width: 260px;
  flex: 1 1 210px;
  gap: 7px;
  margin-left: auto;
  padding: 0 10px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper);
  color: var(--color-muted);
}

.search-form:focus-within {
  border-color: var(--color-focus);
}

.search-form input {
  width: 100%;
  height: 32px;
  padding: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--color-ink);
  font-size: 12px;
}

@media (max-width: 760px) {
  .pdf-toolbar {
    flex-wrap: wrap;
  }

  .search-form {
    order: 3;
    max-width: none;
    flex-basis: 100%;
  }
}
</style>
