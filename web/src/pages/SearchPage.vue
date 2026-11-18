<script setup lang="ts">
import { libraryText as t, messageRef } from '@/stores/locale'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import { PhArrowRight, PhMagnifyingGlass } from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import AsyncBoundary from '@/components/ui/AsyncBoundary.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import StatusNotice from '@/components/ui/StatusNotice.vue'
import { formatAuthors, formatShortDate } from '@/lib/formatters'
import { apiJson } from '@/lib/json-api'
import { LIBRARY_PAGE_SIZE, pageOffset, routeText } from '@/lib/library-route'

type LibraryPage = components['schemas']['LibraryPageResponse']

const page = ref<LibraryPage>({ items: [], total: 0, limit: 20, offset: 0 })
const route = useRoute()
const router = useRouter()
const query = ref('')
const source = ref('')
const publishedFrom = ref('')
const publishedTo = ref('')
const favorite = ref(false)
const unread = ref(false)
const loading = ref(true)
const errorMessage = messageRef(t)
const applied = computed(() => routeText(route.query.query))
let generation = 0

const hasFilters = computed(() => Boolean(query.value || source.value || publishedFrom.value
  || publishedTo.value || favorite.value || unread.value || Object.keys(route.query).length))

function restoreForm(): void {
  query.value = applied.value
  source.value = routeText(route.query.source)
  publishedFrom.value = routeText(route.query.from)
  publishedTo.value = routeText(route.query.to)
  favorite.value = route.query.favorite === 'true'
  unread.value = route.query.unread === 'true'
}

async function load(): Promise<void> {
  const current = ++generation
  loading.value = true
  errorMessage.value = ''
  const offset = pageOffset(route.query.page)
  const params = new URLSearchParams({ limit: String(LIBRARY_PAGE_SIZE), offset: String(offset) })
  if (applied.value) params.set('query', applied.value)
  const sourceFilter = routeText(route.query.source)
  const from = routeText(route.query.from)
  const to = routeText(route.query.to)
  if (sourceFilter) params.set('source', sourceFilter)
  if (from) params.set('published_from', `${from}T00:00:00Z`)
  if (to) params.set('published_to', `${to}T23:59:59.999999Z`)
  if (route.query.favorite === 'true') params.set('favorite', 'true')
  if (route.query.unread === 'true') params.set('unread', 'true')
  try {
    const result = await apiJson<LibraryPage>(`/api/v1/library/works?${params}`)
    if (current !== generation) return
    if (offset && result.items.length === 0 && offset >= result.total) {
      const lastPage = Math.max(1, Math.ceil(result.total / LIBRARY_PAGE_SIZE))
      await router.replace({ query: { ...route.query, page: lastPage > 1 ? String(lastPage) : undefined } })
      return
    }
    page.value = result
  } catch {
    if (current === generation) errorMessage.value = '搜索暂时不可用，请重试。'
  } finally {
    if (current === generation) loading.value = false
  }
}

function submitSearch(clear = false): void {
  const filters: LocationQueryRaw = {}
  if (!clear) {
    if (query.value.trim()) filters.query = query.value.trim()
    if (source.value) filters.source = source.value
    if (publishedFrom.value) filters.from = publishedFrom.value
    if (publishedTo.value) filters.to = publishedTo.value
    if (favorite.value) filters.favorite = 'true'
    if (unread.value) filters.unread = 'true'
  }
  const target = { path: '/search', query: filters }
  if (router.resolve(target).fullPath === route.fullPath) {
    restoreForm()
    void load()
  } else {
    void router.push(target)
  }
}

function goToPage(offset: number): void {
  const number = Math.floor(offset / LIBRARY_PAGE_SIZE) + 1
  void router.push({ query: { ...route.query, page: number > 1 ? String(number) : undefined } })
}

watch(() => route.fullPath, () => {
  if (route.path === '/search') {
    restoreForm()
    void load()
  }
}, { immediate: true })
onBeforeUnmount(() => { generation += 1 })
</script>

<template>
  <PageHeader
    :eyebrow="t('发现工作台')"
    :title="t('搜索论文')"
    :description="t('按标题、作者或 DOI 查找本地论文，再按来源、发表日期和阅读状态筛选。')"
  />

  <section class="search-panel" :aria-label="t('论文搜索条件')">
    <form role="search" @submit.prevent="submitSearch()">
      <div class="search-form">
        <PhMagnifyingGlass :size="18" aria-hidden="true" />
        <label class="sr-only" for="search-query">{{ t('搜索意图') }}</label>
        <input id="search-query" v-model="query" type="search" maxlength="500" :placeholder="t('标题、作者或 DOI')">
        <button class="quiet-button quiet-button--primary" type="submit">{{ t('搜索') }}</button>
      </div>
      <div class="search-filters">
        <label>{{ t('来源') }}
          <select v-model="source">
            <option value="">{{ t('全部来源') }}</option>
            <option value="arxiv">arXiv</option>
            <option value="openreview">OpenReview</option>
            <option value="europe_pmc">Europe PMC</option>
            <option value="ieee">IEEE Xplore</option>
            <option value="springer_nature">Springer Nature</option>
          </select>
        </label>
        <label>{{ t('发表起始') }}<input v-model="publishedFrom" type="date" :max="publishedTo || undefined"></label>
        <label>{{ t('发表结束') }}<input v-model="publishedTo" type="date" :min="publishedFrom || undefined"></label>
        <label class="check-label"><input v-model="favorite" type="checkbox"> {{ t('仅收藏') }}</label>
        <label class="check-label"><input v-model="unread" type="checkbox"> {{ t('仅未读完') }}</label>
        <button v-if="hasFilters" class="quiet-button" type="button" @click="submitSearch(true)">{{ t('清除条件') }}</button>
      </div>
    </form>
  </section>

  <AsyncBoundary :loading="loading" :error="errorMessage" :empty="page.items.length === 0" @retry="load">
    <template #loading><StatusNotice kind="progress" :title="t('正在搜索本地论文')" :message="t('正在读取规范论文记录和可用版本。')" /></template>
    <template #error="{ error, retry }"><StatusNotice kind="error" :message="error ?? ''"><template #action><button class="quiet-button quiet-button--primary" type="button" @click="retry">{{ t('重试') }}</button></template></StatusNotice></template>
    <template #empty><EmptyState :title="t('没有找到匹配论文')" :description="t('换一个更短的研究意图，或清除部分筛选条件后再试。')" /></template>
    <template #default>
      <section class="results" :aria-label="t('搜索结果')" aria-live="polite">
        <div class="results-summary">{{ t('找到') }} <strong>{{ page.total }}</strong> {{ t('篇本地论文') }}<span v-if="applied">{{ t('，查询：') }}{{ applied }}</span></div>
        <article v-for="work in page.items" :key="work.id" class="result-row">
          <div>
            <h2>{{ work.title }}</h2>
            <p>{{ formatAuthors(work.authors) }} · {{ formatShortDate(work.published_at) }} · {{ work.document_count ? t('{0} 个文档版本', work.document_count) : t('暂无 PDF') }}</p>
            <p v-if="work.abstract" class="abstract">{{ work.abstract }}</p>
          </div>
          <RouterLink class="result-link" :to="work.representative_document?.available
            ? `/library/${encodeURIComponent(work.id)}/read/${encodeURIComponent(work.representative_document.id)}`
            : { path: '/library', query: { work: work.id } }">
            {{ work.representative_document?.available ? t('阅读论文') : t('查看论文记录') }} <PhArrowRight :size="16" aria-hidden="true" /></RouterLink>
        </article>
        <nav v-if="page.total > page.limit" class="pagination" :aria-label="t('搜索结果分页')">
          <button class="quiet-button" type="button" :disabled="page.offset === 0" @click="goToPage(page.offset - page.limit)">{{ t('上一页') }}</button>
          <span>{{ page.offset + 1 }}–{{ page.offset + page.items.length }} / {{ page.total }}</span>
          <button class="quiet-button" type="button" :disabled="page.offset + page.limit >= page.total" @click="goToPage(page.offset + page.limit)">{{ t('下一页') }}</button>
        </nav>
      </section>
    </template>
  </AsyncBoundary>
</template>

<style scoped>
.search-panel { margin: 18px 0 28px; padding: 18px; border: 1px solid var(--color-line); border-radius: var(--radius-md); background: var(--color-paper-raised); }
.search-form { display: flex; align-items: center; gap: 10px; }
.search-form input { flex: 1; min-width: 0; }
.search-filters { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; margin-top: 14px; }
.search-filters label { display: grid; gap: 5px; color: var(--color-muted-strong); font-size: 12px; }
.search-filters select, .search-filters input { min-height: 38px; }
.check-label { display: flex !important; align-items: center; min-height: 38px; }
.check-label input { min-height: auto; }
.pagination { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 1rem; }
.results-summary { margin-bottom: 10px; color: var(--color-muted-strong); font-size: 13px; }
.result-row { display: flex; justify-content: space-between; gap: 24px; padding: 20px 0; border-top: 1px solid var(--color-line); }
.result-row h2 { margin: 0; font-family: var(--font-editorial); font-size: 21px; font-weight: 600; }
.result-row p { margin: 7px 0 0; color: var(--color-muted-strong); font-size: 13px; line-height: 1.55; }
.result-row .abstract { max-width: 70ch; color: var(--color-muted); }
.result-link { display: inline-flex; align-items: center; gap: 6px; flex: 0 0 auto; color: var(--color-forest); font-size: 13px; text-decoration: none; }
@media (max-width: 640px) { .search-form { flex-wrap: wrap; } .search-form input { flex-basis: calc(100% - 30px); } .search-form button { width: 100%; } .result-row { display: grid; gap: 10px; } }
</style>
