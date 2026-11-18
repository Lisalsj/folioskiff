<script setup lang="ts">
import { libraryText as t, messageRef } from '@/stores/locale'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  PhArrowRight,
  PhBookOpenText,
  PhFilePdf,
  PhHeart,
  PhMagnifyingGlass,
  PhPlus,
} from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import LibraryImportPanel from '@/components/LibraryImportPanel.vue'
import PaperFeedbackControls from '@/components/PaperFeedbackControls.vue'
import AsyncBoundary from '@/components/ui/AsyncBoundary.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import StatusNotice from '@/components/ui/StatusNotice.vue'
import { apiJson, ApiResponseError } from '@/lib/json-api'
import { LIBRARY_PAGE_SIZE, pageOffset, routeText } from '@/lib/library-route'
import { formatAuthors, formatShortDate } from '@/lib/formatters'

type LibraryPage = components['schemas']['LibraryPageResponse']
type LibraryWork = components['schemas']['LibraryWorkResponse']
type LibraryState = components['schemas']['LibraryStateResponse']

const page = ref<LibraryPage>({ items: [], total: 0, limit: 20, offset: 0 })
const route = useRoute()
const router = useRouter()
const query = ref('')
const appliedQuery = computed(() => routeText(route.query.query))
const selectedWork = computed(() => routeText(route.query.work) || routeText(route.query.related))
const loading = ref(true)
const errorMessage = messageRef(t)
const importPanelOpen = ref(false)
const importTarget = ref<{ id: string, title: string } | null>(null)
const importNotice = messageRef(t)
let loadGeneration = 0

const firstItem = computed(() => page.value.total === 0 ? 0 : page.value.offset + 1)
const lastItem = computed(() => Math.min(page.value.total, page.value.offset + page.value.items.length))
const hasPrevious = computed(() => page.value.offset > 0)
const hasNext = computed(() => page.value.offset + page.value.limit < page.value.total)

async function loadLibrary(): Promise<void> {
  const offset = pageOffset(route.query.page)
  const generation = ++loadGeneration
  loading.value = true
  errorMessage.value = ''
  const parameters = new URLSearchParams({
    limit: String(LIBRARY_PAGE_SIZE),
    offset: String(offset),
  })
  if (appliedQuery.value) parameters.set('query', appliedQuery.value)
  try {
    if (selectedWork.value) {
      const work = await apiJson<LibraryWork>(`/api/v1/works/${encodeURIComponent(selectedWork.value)}`)
      if (generation === loadGeneration) page.value = { items: [work], total: 1, limit: LIBRARY_PAGE_SIZE, offset: 0 }
      return
    }
    const loaded = await apiJson<LibraryPage>(`/api/v1/library/works?${parameters}`)
    if (generation !== loadGeneration) return
    if (offset && loaded.items.length === 0 && offset >= loaded.total) {
      const lastPage = Math.max(1, Math.ceil(loaded.total / LIBRARY_PAGE_SIZE))
      await router.replace({ query: { ...route.query, page: lastPage > 1 ? String(lastPage) : undefined } })
      return
    }
    page.value = loaded
  } catch (cause) {
    if (generation === loadGeneration) {
      errorMessage.value = selectedWork.value && cause instanceof ApiResponseError && cause.status === 404
        ? '这篇论文记录已不可用，可以返回全部论文。'
        : '暂时无法读取论文库。你的本地数据没有被修改。'
    }
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

async function showList(search: string, offset = 0): Promise<void> {
  const number = Math.floor(offset / LIBRARY_PAGE_SIZE) + 1
  const target = { path: '/library', query: { query: search || undefined, page: number > 1 ? String(number) : undefined } }
  if (router.resolve(target).fullPath === route.fullPath) {
    await loadLibrary()
  } else {
    await router.push(target)
  }
}

function openLibraryImport(): void {
  importTarget.value = null
  importNotice.value = ''
  importPanelOpen.value = true
}

function addDocumentVersion(work: LibraryWork): void {
  importTarget.value = { id: work.id, title: work.title }
  importNotice.value = ''
  importPanelOpen.value = true
}

async function handleImported(result: {
  workId: string
  documentVersionId: string | null
  kind: 'pdf' | 'reference'
}): Promise<void> {
  importNotice.value = result.kind === 'pdf'
    ? '论文和 PDF 已加入本地库；现在可以打开阅读。'
    : '引用已加入本地库；正文尚未下载。'
  importTarget.value = null
  query.value = ''
  if (selectedWork.value === result.workId) await loadLibrary()
  else await showList('')
}

function documentLabel(work: LibraryWork): string {
  const document = work.representative_document
  if (!document) return t('暂无 PDF')
  if (document.original_filename) return document.original_filename
  return t('{0}% 完整版本', Math.round(document.completeness))
}

function handleFeedbackUpdated(workId: string, state: LibraryState): void {
  page.value = {
    ...page.value,
    items: page.value.items.map((work) => work.id === workId
      ? {
          ...work,
          favorite: state.favorite,
          feedback: state.feedback,
          pinned: state.pinned,
          reading_progress: state.reading_progress,
          analysis_state: state.analysis_state,
        }
      : work),
  }
}

watch(() => route.fullPath, () => {
  if (route.path === '/library') {
    query.value = appliedQuery.value
    void loadLibrary()
  }
}, { immediate: true })
onBeforeUnmount(() => { loadGeneration += 1 })
</script>

<template>
  <PageHeader
    :eyebrow="t('本地收藏')"
    :title="t('我的论文库')"
    :description="t('保存论文、阅读进度、版本关系与后续分析，所有内容留在这台设备上。')"
  >
    <template #actions>
      <button
        class="quiet-button quiet-button--primary"
        type="button"
        data-test="open-library-import"
        @click="openLibraryImport"
      >
        <PhPlus :size="17" aria-hidden="true" />
        {{ t('导入论文') }}
      </button>
    </template>
    <template #status>
      <div class="library-count" aria-live="polite">
        <strong>{{ page.total }}</strong>
        <span>{{ selectedWork || appliedQuery ? t('篇匹配论文') : t('篇已保存论文') }}</span>
      </div>
    </template>
  </PageHeader>

  <StatusNotice v-if="importNotice" class="import-notice" kind="success" :message="importNotice" />

  <section class="library-tools" :aria-label="t('论文库筛选')">
    <form data-test="library-search-form" role="search" @submit.prevent="showList(query.trim())">
      <PhMagnifyingGlass :size="17" aria-hidden="true" />
      <label class="sr-only" for="library-search">{{ t('搜索论文库') }}</label>
      <input
        id="library-search"
        v-model="query"
        data-test="library-search"
        type="search"
        :placeholder="t('按标题、作者或 DOI 搜索')"
      >
      <button type="submit">{{ t('筛选') }}</button>
    </form>
    <RouterLink v-if="selectedWork || appliedQuery" to="/library">{{ t('查看全部论文') }}</RouterLink>
    <p v-if="appliedQuery">{{ t('当前筛选：') }}{{ appliedQuery }}</p>
  </section>

  <AsyncBoundary
    :loading="loading"
    :error="errorMessage"
    :empty="page.items.length === 0"
    @retry="loadLibrary"
  >
    <template #loading>
      <StatusNotice
        kind="progress"
        :title="t('正在整理本地论文库')"
        :message="t('只读取已保存的规范论文记录与可访问版本。')"
      />
    </template>
    <template #error="{ error, retry }">
      <StatusNotice kind="error" :message="error ?? ''">
        <template #action>
          <button
            class="quiet-button quiet-button--primary"
            type="button"
            data-test="retry-library"
            @click="retry"
          >{{ t('重新读取') }}</button>
        </template>
      </StatusNotice>
    </template>
    <template #empty>
      <EmptyState
        data-test="library-empty"
        :title="appliedQuery ? t('没有匹配的已保存论文') : t('论文库还是空的')"
        :description="appliedQuery
          ? t('换一个标题、作者或 DOI 关键词再试一次。')
          : t('收藏论文或导入你合法取得的 PDF 后，它们会按版本关系出现在这里。')"
      >
        <template #icon><PhBookOpenText :size="28" aria-hidden="true" /></template>
      </EmptyState>
    </template>

    <template #default>
    <section class="library-list" :aria-label="t('已保存论文')">
      <article
        v-for="work in page.items"
        :key="work.id"
        :data-test="`library-work-${work.id}`"
        class="library-row"
      >
        <div class="work-marker" aria-hidden="true">
          <PhFilePdf :size="20" weight="regular" />
        </div>

        <div class="work-main">
          <div class="work-title-line">
            <h2>{{ work.title }}</h2>
            <PhHeart v-if="work.favorite" :size="16" weight="fill" :aria-label="t('已收藏')" />
          </div>
          <p class="authors">{{ formatAuthors(work.authors) }}</p>
          <p v-if="work.abstract" class="abstract">{{ work.abstract }}</p>
          <div class="work-meta">
            <span>{{ formatShortDate(work.published_at) }}</span>
            <span>{{ work.document_count }} {{ t('个版本') }}</span>
            <span>{{ documentLabel(work) }}</span>
            <span v-if="work.doi">DOI {{ work.doi }}</span>
          </div>
          <PaperFeedbackControls
            class="work-feedback"
            :work-id="work.id"
            :state="{
              work_id: work.id,
              favorite: work.favorite,
              feedback: work.feedback,
              pinned: work.pinned,
              reading_progress: work.reading_progress,
              analysis_state: work.analysis_state,
              persisted: true,
            }"
            compact
            @updated="handleFeedbackUpdated(work.id, $event)"
          />
        </div>

        <div class="work-progress">
          <span>{{ t('阅读进度') }}</span>
          <strong>{{ Math.round(work.reading_progress) }}%</strong>
          <progress :value="work.reading_progress" max="100">
            {{ Math.round(work.reading_progress) }}%
          </progress>
        </div>

        <div class="work-actions">
          <button
            type="button"
            :data-test="`add-version-${work.id}`"
            :aria-label="t('为《{0}》添加 PDF 版本', work.title)"
            @click="addDocumentVersion(work)"
          >
            <PhPlus :size="14" aria-hidden="true" />
            {{ t('添加版本') }}
          </button>
          <RouterLink
            v-if="work.representative_document?.available"
            class="read-link"
            :data-test="`read-${work.id}`"
            :to="`/library/${work.id}/read/${work.representative_document.id}`"
            :aria-label="t('阅读《{0}》', work.title)"
          >
            {{ t('阅读') }}
            <PhArrowRight :size="16" aria-hidden="true" />
          </RouterLink>
          <span v-else class="unavailable" :data-test="`unavailable-${work.id}`">
            {{ t('暂无可读 PDF') }}
          </span>
        </div>
      </article>
    </section>

    <nav v-if="page.total > page.limit" class="pagination" :aria-label="t('论文库分页')">
      <button type="button" :disabled="!hasPrevious" @click="showList(appliedQuery, Math.max(0, page.offset - page.limit))">
        {{ t('上一页') }}
      </button>
      <span>{{ firstItem }} {{ t('至') }} {{ lastItem }}{{ t('，共') }} {{ page.total }} {{ t('篇') }}</span>
      <button type="button" :disabled="!hasNext" @click="showList(appliedQuery, page.offset + page.limit)">
        {{ t('下一页') }}
      </button>
    </nav>
    </template>
  </AsyncBoundary>

  <LibraryImportPanel
    v-model:open="importPanelOpen"
    :target-work="importTarget"
    @imported="handleImported"
  />
</template>

<style scoped>
h2,
p {
  margin-top: 0;
}

[data-test='open-library-import'] {
  white-space: nowrap;
}

h2 {
  font-family: var(--font-editorial);
  font-weight: 500;
}

.library-count {
  min-width: 130px;
  padding-left: 20px;
  border-left: 2px solid var(--color-forest);
}

.library-count strong,
.library-count span {
  display: block;
}

.library-count strong {
  font-family: var(--font-editorial);
  font-size: 28px;
  font-weight: 500;
}

.library-count span {
  margin-top: 3px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.import-notice {
  margin: 18px 0 0;
}

.library-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 22px 0 16px;
}

.library-tools form {
  width: min(520px, 100%);
  display: flex;
  align-items: center;
  gap: 9px;
  padding-left: 12px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-deep);
  color: var(--color-muted);
}

.library-tools form:focus-within {
  border-color: var(--color-focus);
}

.library-tools input {
  min-width: 0;
  height: 40px;
  flex: 1;
  padding: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--color-ink);
  font-size: 13px;
}

.library-tools button,
.pagination button {
  min-height: 36px;
  padding: 0 14px;
  border: 0;
  border-radius: 7px;
  background: var(--color-forest);
  color: var(--color-on-accent);
  cursor: pointer;
}

.library-tools button {
  align-self: stretch;
  border-radius: 0 7px 7px 0;
}

.library-tools button:focus-visible,
.pagination button:focus-visible,
.read-link:focus-visible,
.library-tools input:focus-visible,
.work-actions > button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

.library-tools > p {
  margin: 0;
  color: var(--color-muted);
  font-size: 12px;
}

.library-list {
  border-top: 1px solid var(--color-line);
}

.library-row {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) 112px 104px;
  gap: 18px;
  align-items: center;
  padding: 22px 0;
  border-bottom: 1px solid var(--color-line);
}

.work-marker {
  align-self: start;
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  color: var(--color-danger);
}

.work-main {
  min-width: 0;
}

.work-title-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.work-title-line svg {
  flex: none;
  color: var(--color-danger);
}

.work-main h2 {
  margin-bottom: 7px;
  font-size: clamp(19px, 2vw, 24px);
  line-height: 1.25;
  letter-spacing: -0.015em;
}

.authors,
.abstract {
  color: var(--color-muted-strong);
  font-size: 12px;
  line-height: 1.6;
}

.authors {
  margin-bottom: 6px;
}

.abstract {
  display: -webkit-box;
  max-width: 760px;
  margin-bottom: 10px;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.work-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 14px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.work-feedback {
  margin-top: 13px;
}

.work-progress {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 5px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.work-progress strong {
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
}

.work-progress progress {
  width: 100%;
  height: 4px;
  grid-column: 1 / -1;
  border: 0;
  accent-color: var(--color-forest);
}

.read-link {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  color: var(--color-forest);
  font-size: 12px;
  font-weight: 700;
  text-decoration: none;
}

.work-actions {
  display: grid;
  justify-items: end;
  gap: 9px;
}

.work-actions > button {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-muted);
  font-size: var(--type-meta);
  cursor: pointer;
}

.work-actions > button:hover {
  color: var(--color-forest);
}

.unavailable {
  color: var(--color-muted);
  font-size: var(--type-meta);
  text-align: right;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 14px;
  padding-top: 20px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.pagination button {
  background: var(--color-paper-deep);
  color: var(--color-forest);
  border: 1px solid var(--color-line);
}

.pagination button:disabled {
  opacity: 0.45;
  cursor: default;
}

@media (max-width: 980px) {
  .library-row {
    grid-template-columns: 32px minmax(0, 1fr) 82px;
  }

  .work-progress {
    display: none;
  }
}

@media (max-width: 640px) {
  .library-count {
    padding: 10px 0 0;
    border-top: 2px solid var(--color-forest);
    border-left: 0;
  }

  .library-tools {
    align-items: stretch;
    flex-direction: column;
  }

  .library-tools > p {
    order: -1;
  }

  .library-row {
    grid-template-columns: 28px minmax(0, 1fr);
    gap: 12px;
  }

  .read-link,
  .unavailable,
  .work-actions {
    grid-column: 2;
    justify-self: start;
    text-align: left;
  }

  .work-actions { justify-items: start; }
}
</style>
