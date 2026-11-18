<script setup lang="ts">
import { libraryText as t } from '@/stores/locale'
import { nextTick, onBeforeUnmount, onMounted, ref, shallowRef, useId } from 'vue'

import type { components } from '@/api/schema'
import type { LibraryWork } from '@/features/library/ordered-selection'
import { apiJson } from '@/lib/json-api'

defineProps<{
  selectedIds: readonly string[]
  maximum: number
  optionTestId: (work: LibraryWork, index: number) => string
}>()

const emit = defineEmits<{ toggle: [work: LibraryWork, checked: boolean] }>()
const searchId = useId()
const searchField = ref<HTMLInputElement>()
const options = ref<HTMLOListElement>()
const pageSize = 20
const page = shallowRef<components['schemas']['LibraryPageResponse']>()
const draft = ref('')
const query = ref('')
const offset = ref(0)
const loading = ref(true)
const failed = ref(false)
let active: AbortController | undefined

async function load(nextOffset: number, nextQuery = query.value): Promise<void> {
  active?.abort()
  const request = new AbortController()
  active = request
  query.value = nextQuery
  offset.value = nextOffset
  loading.value = true
  failed.value = false
  const params = new URLSearchParams({ limit: String(pageSize), offset: String(nextOffset) })
  if (nextQuery) params.set('query', nextQuery)
  try {
    const result = await apiJson<components['schemas']['LibraryPageResponse']>(
      `/api/v1/library/works?${params}`, { signal: request.signal },
    )
    if (request.signal.aborted) return
    if (nextOffset > 0 && nextOffset >= result.total) {
      await load(Math.max(0, Math.ceil(result.total / pageSize) - 1) * pageSize)
    } else {
      page.value = result
    }
  } catch {
    if (!request.signal.aborted) failed.value = true
  } finally {
    if (!request.signal.aborted) loading.value = false
  }
}

async function changePage(nextOffset: number): Promise<void> {
  const previousFocus = document.activeElement
  await load(nextOffset)
  await nextTick()
  if (previousFocus && !previousFocus.isConnected && document.activeElement === document.body) {
    const firstOption = options.value?.querySelector<HTMLInputElement>('input:not(:disabled)')
    const focusTarget = firstOption ?? searchField.value
    focusTarget?.focus()
  }
}

onMounted(() => { void load(0) })
onBeforeUnmount(() => active?.abort())
</script>

<template>
  <form class="picker-search" role="search" :aria-label="t('搜索可选论文')" @submit.prevent="load(0, draft.trim())">
    <label :for="searchId">{{ t('搜索论文库') }}</label>
    <input :id="searchId" ref="searchField" v-model="draft" type="search" maxlength="500" :placeholder="t('标题、作者或 DOI')">
    <button class="quiet-button" type="submit">{{ t('搜索') }}</button>
  </form>
  <p v-if="loading" class="picker-note" role="status">{{ t('正在读取本地论文库…') }}</p>
  <div v-else-if="failed" class="picker-note" role="alert">
    {{ t('暂时无法读取论文库。') }}
    <button type="button" class="quiet-button" @click="changePage(offset)">{{ t('重试') }}</button>
  </div>
  <template v-else-if="page">
    <p v-if="!page.items.length" class="picker-note" role="status">
      {{ query ? t('没有找到匹配的论文。') : t('论文库为空，请先收藏或导入论文。') }}
    </p>
    <ol v-else ref="options" class="picker-options">
      <li v-for="(work, index) in page.items" :key="work.id">
        <label>
          <input
            type="checkbox"
            :data-test="optionTestId(work, index)"
            :checked="selectedIds.includes(work.id)"
            :disabled="selectedIds.length >= maximum && !selectedIds.includes(work.id)"
            @change="emit('toggle', work, ($event.currentTarget as HTMLInputElement).checked)"
          >
          <span>
            <strong>{{ work.title }}</strong>
            <small>{{ work.authors.slice(0, 3).join('、') || t('作者未知') }}</small>
          </span>
        </label>
      </li>
    </ol>
    <nav v-if="page.total > page.limit" class="picker-pages" :aria-label="t('可选论文分页')">
      <button type="button" class="quiet-button" :disabled="page.offset === 0" @click="changePage(Math.max(0, page.offset - page.limit))">{{ t('上一页') }}</button>
      <span aria-live="polite">{{ page.offset + 1 }}–{{ page.offset + page.items.length }} / {{ page.total }}</span>
      <button type="button" class="quiet-button" :disabled="page.offset + page.limit >= page.total" @click="changePage(page.offset + page.limit)">{{ t('下一页') }}</button>
    </nav>
  </template>
</template>

<style scoped>
.picker-search { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 6px 8px; margin-bottom: 14px; }
.picker-search label { grid-column: 1 / -1; font-size: 13px; font-weight: 600; }
.picker-search input { min-width: 0; min-height: 44px; padding: 8px 10px; border: 1px solid var(--color-line); border-radius: var(--radius-sm); background: var(--color-paper-raised); color: var(--color-ink); }
.picker-search input:focus-visible, .picker-options input:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
.picker-options { max-height: 520px; margin: 0; overflow-y: auto; padding: 0; border-block: 1px solid var(--color-line); list-style: none; }
.picker-options li + li { border-top: 1px solid var(--color-line); }
.picker-options label { min-height: 64px; display: flex; align-items: center; gap: 12px; padding: 10px 6px; cursor: pointer; }
.picker-options label:hover { background: var(--color-forest-hover-soft); }
.picker-options input { flex: none; width: 18px; height: 18px; margin: 0; accent-color: var(--color-forest); }
.picker-options strong, .picker-options small { display: block; overflow-wrap: anywhere; }
.picker-options strong { font-family: var(--font-editorial); font-size: 16px; font-weight: 600; line-height: 1.4; }
.picker-options small { margin-top: 4px; color: var(--color-muted); font-size: 13px; }
.picker-note { margin: 0; padding: 16px 0; color: var(--color-muted); }
.picker-pages { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; margin-top: 10px; }
.picker-pages span { font-size: 13px; color: var(--color-muted-strong); }
.picker-search button, .picker-pages button, .picker-note button { min-height: 44px; }
</style>
