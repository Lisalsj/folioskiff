<script setup lang="ts">
import { currentLocale, discoveryText as t, messageRef, serverMessage } from '@/stores/locale'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  PhArrowRight,
  PhBinoculars,
  PhClockCounterClockwise,
  PhPlus,
  PhWarningCircle,
  PhX,
} from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import AsyncBoundary from '@/components/ui/AsyncBoundary.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { discoveryRunStatusLabel } from '@/lib/discovery-status'
import { apiJson } from '@/lib/json-api'

type Briefing = components['schemas']['ReadingBriefingResponse']
type LibraryWork = components['schemas']['LibraryWorkResponse']

const router = useRouter()
const briefing = ref<Briefing>()
const loading = ref(true)
const errorMessage = messageRef(t)
const explorationDismissed = ref(false)
const explorationOpening = ref(false)
const quickSearch = ref('')
let generation = 0

function isBriefing(value: unknown): value is Briefing {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const candidate = value as Record<string, unknown>
  const emptyState = candidate.empty_state
  return typeof candidate.generated_at === 'string'
    && Boolean(emptyState)
    && typeof emptyState === 'object'
    && !Array.isArray(emptyState)
    && typeof (emptyState as Record<string, unknown>).active === 'boolean'
    && Array.isArray(candidate.plans)
    && Array.isArray(candidate.new_papers)
    && Array.isArray(candidate.important_revisions)
    && Array.isArray(candidate.pending_catchups)
    && Array.isArray(candidate.active_jobs)
    && Array.isArray(candidate.failed_jobs)
}

async function load(): Promise<void> {
  const currentGeneration = ++generation
  loading.value = true
  errorMessage.value = ''
  try {
    const loaded = await apiJson<unknown>('/api/v1/briefing')
    if (!isBriefing(loaded)) throw new Error(t('简报数据格式不完整，请重新读取'))
    if (currentGeneration === generation) briefing.value = loaded
  } catch (caught) {
    if (currentGeneration === generation) {
      errorMessage.value = caught instanceof Error ? caught.message : '暂时无法读取阅读简报'
    }
  } finally {
    if (currentGeneration === generation) loading.value = false
  }
}

function formatAuthors(authors: string[]): string {
  return authors.length ? authors.slice(0, 3).join('、') : t('作者未知')
}

function submitQuickSearch(): void {
  const query = quickSearch.value.trim()
  void router.push(query ? { path: '/search', query: { query } } : '/search')
}

function changedFields(fields: string[]): string {
  const labels: Record<string, string> = {
    abstract: t('摘要'),
    authors: t('作者'),
    document: t('全文'),
    title: t('标题'),
    doi: 'DOI',
  }
  return fields.map((field) => labels[field] ?? field).join(' · ')
}

function jobLabel(kind: string): string {
  return {
    'discovery.run': t('论文发现'),
    'recommendation.run': t('推荐评分'),
    'metadata.enrich': t('元数据补全'),
    'analysis.report': t('论文分析'),
  }[kind] ?? kind
}

async function openExplorationHint(): Promise<void> {
  const hint = briefing.value?.exploration_hint
  if (!hint || explorationOpening.value) return
  explorationOpening.value = true
  const anchorWorkId = hint.related_work_ids[0] ?? hint.work_id
  try {
    const work = await apiJson<LibraryWork>(`/api/v1/works/${encodeURIComponent(anchorWorkId)}`)
    const document = work.representative_document
    if (document?.available) {
      await router.push(
        `/library/${encodeURIComponent(work.id)}/read/${encodeURIComponent(document.id)}?task=related`,
      )
      return
    }
  } catch {
    // Fall through to the library: the hint itself remains a local, non-executable fact.
  } finally {
    explorationOpening.value = false
  }
  await router.push({ path: '/library', query: { related: anchorWorkId } })
}

onMounted(() => void load())
onBeforeUnmount(() => { generation += 1 })
</script>

<template>
  <PageHeader
    :eyebrow="t('研究工作台')"
    :title="t('阅读简报')"
    :description="t('从已确认的研究范围出发，查看新论文、重要修订和需要你决定的任务。')"
  >
    <template #actions>
      <form class="quick-search" role="search" @submit.prevent="submitQuickSearch">
        <label for="briefing-quick-search">{{ t('快速搜索') }}</label>
        <input id="briefing-quick-search" v-model="quickSearch" type="search" :placeholder="t('标题、作者或 DOI')">
        <button type="submit">{{ t('搜索') }}</button>
      </form>
      <RouterLink class="primary-action" data-test="primary-action" to="/plans/new">
        <PhPlus :size="18" weight="bold" aria-hidden="true" />
        {{ t('新建关注计划') }}
      </RouterLink>
    </template>
  </PageHeader>

  <AsyncBoundary
    :loading="loading"
    :error="errorMessage"
    :empty="!briefing"
    :loading-message="t('正在整理本地阅读简报…')"
    :error-title="t('简报暂时没有读出来')"
    :empty-title="t('简报尚未准备好')"
    :retry-label="t('重新读取')"
    @retry="load"
  >
  <template v-if="briefing">
    <section
      v-if="briefing.empty_state.active"
      data-test="briefing-empty"
      class="empty-briefing"
      aria-labelledby="empty-briefing-title"
    >
      <div>
        <p class="section-label">{{ t('开始第一条研究线索') }}</p>
        <h2 id="empty-briefing-title">{{ serverMessage(briefing.empty_state.title) }}</h2>
        <p>{{ serverMessage(briefing.empty_state.description) }}</p>
        <RouterLink v-if="briefing.empty_state.primary_action === 'create_plan'" to="/plans/new">
          {{ t('描述研究意图') }} <PhArrowRight :size="16" aria-hidden="true" />
        </RouterLink>
      </div>
      <ol>
        <li><span>01</span> {{ t('输入研究意图') }}</li>
        <li><span>02</span> {{ t('审阅并确认双语关键词') }}</li>
        <li><span>03</span> {{ t('选择月份与论文来源') }}</li>
      </ol>
    </section>

    <template v-else>
      <section class="briefing-totals" :aria-label="t('简报数量')">
        <div><strong>{{ briefing.plans.length }}</strong><span>{{ t('关注计划') }}</span></div>
        <div><strong>{{ briefing.new_papers.length }}</strong><span>{{ t('本次新论文') }}</span></div>
        <div><strong>{{ briefing.important_revisions.length }}</strong><span>{{ t('重要修订') }}</span></div>
        <p>{{ t('更新于') }} {{ new Date(briefing.generated_at).toLocaleString(currentLocale()) }}</p>
      </section>

      <div class="briefing-grid">
        <div class="briefing-primary">
          <section data-test="new-papers" class="briefing-section" aria-labelledby="new-papers-title">
            <div class="section-heading">
              <div><h2 id="new-papers-title">{{ t('新论文') }}</h2></div>
              <span>{{ briefing.new_papers.length }} {{ t('篇') }}</span>
            </div>
            <article v-for="paper in briefing.new_papers" :key="`${paper.run_id}:${paper.work_id}`" class="paper-row">
              <div class="position">{{ paper.position ? String(paper.position).padStart(2, '0') : '—' }}</div>
              <div>
                <RouterLink :to="`/runs/${paper.run_id}/results`">{{ paper.title }}</RouterLink>
                <p>{{ formatAuthors(paper.authors) }}</p>
                <div class="paper-scores">
                  <span>{{ t('相关') }} {{ Math.round(paper.relevance_score) }}</span>
                  <span>{{ t('质量') }} {{ Math.round(paper.quality_score) }}</span>
                  <span>{{ t('覆盖') }} {{ Math.round(paper.coverage) }}</span>
                  <span>{{ t('置信') }} {{ Math.round(paper.confidence) }}</span>
                </div>
                <p v-if="paper.warnings.length" class="paper-warning">
                  <PhWarningCircle :size="14" aria-hidden="true" /> {{ paper.warnings.map(serverMessage).join(' · ') }}
                </p>
              </div>
              <RouterLink class="row-action" :to="`/runs/${paper.run_id}/results`" :aria-label="t('查看完整推荐结果')">
                <PhArrowRight :size="17" aria-hidden="true" />
              </RouterLink>
            </article>
            <p v-if="!briefing.new_papers.length" class="section-empty">{{ t('最近一次运行还没有新推荐。') }}</p>
          </section>

          <section data-test="important-revisions" class="briefing-section revisions" aria-labelledby="revisions-title">
            <div class="section-heading">
              <div><h2 id="revisions-title">{{ t('重要修订') }}</h2></div>
              <span>{{ briefing.important_revisions.length }} {{ t('项') }}</span>
            </div>
            <article v-for="revision in briefing.important_revisions" :key="revision.id" class="revision-row">
              <div>
                <h3>{{ revision.title }}</h3>
                <p>{{ revision.source }} · {{ formatAuthors(revision.authors) }}</p>
              </div>
              <span>{{ changedFields(revision.changed_fields) }}</span>
            </article>
            <p v-if="!briefing.important_revisions.length" class="section-empty">{{ t('没有未读的重要修订。') }}</p>
          </section>
        </div>

        <aside>
          <section
            v-if="briefing.exploration_hint && !explorationDismissed"
            class="exploration-hint"
            data-test="exploration-hint"
            aria-labelledby="exploration-hint-title"
          >
            <button
              type="button"
              class="dismiss-hint"
              :aria-label="t('暂时隐藏相关推荐')"
              @click="explorationDismissed = true"
            ><PhX :size="14" aria-hidden="true" /></button>
            <PhBinoculars :size="20" aria-hidden="true" />
            <p class="section-label">{{ t('本地论文库线索') }}</p>
            <h2 id="exploration-hint-title">{{ t('继续探索相邻问题') }}</h2>
            <p>{{ serverMessage(briefing.exploration_hint.reason) }}</p>
            <div>
              <span v-for="term in briefing.exploration_hint.suggested_terms" :key="term">{{ term }}</span>
            </div>
            <button
              type="button"
              class="open-hint"
              :disabled="explorationOpening"
              @click="openExplorationHint"
            >{{ explorationOpening ? t('正在打开…') : t('相关推荐') }} <PhArrowRight :size="14" aria-hidden="true" /></button>
          </section>

          <section class="focus-plans" aria-labelledby="focus-plans-title">
            <div class="section-heading compact">
              <div><h2 id="focus-plans-title">{{ t('研究计划') }}</h2></div>
              <RouterLink to="/plans">{{ t('全部') }}</RouterLink>
            </div>
            <article v-for="plan in briefing.plans" :key="plan.id">
              <h3>{{ plan.name }}</h3>
              <p>{{ plan.research_intent }}</p>
              <div>
                <span>{{ plan.sources.join(' · ') }}</span>
                <RouterLink v-if="plan.latest_run?.recommendation_set_id" :to="`/runs/${plan.latest_run.id}/results`">
                  {{ plan.latest_run.recommendation_count }} {{ t('篇结果') }}
                </RouterLink>
                <span v-else>{{ plan.latest_run ? discoveryRunStatusLabel(plan.latest_run.status) : t('尚未运行') }}</span>
              </div>
            </article>
          </section>

          <section data-test="briefing-attention" class="attention" aria-labelledby="attention-title">
            <div class="section-heading compact">
              <div><h2 id="attention-title">{{ t('任务与补跑') }}</h2></div>
            </div>
            <p v-if="briefing.pending_catchups.length" class="attention-line">
              <PhClockCounterClockwise :size="15" aria-hidden="true" />
              {{ briefing.pending_catchups.length }} {{ t('项待补跑') }}
            </p>
            <div v-for="job in briefing.active_jobs" :key="job.id" class="job-line">
              <span>{{ jobLabel(job.kind) }}</span><strong>{{ job.progress }}%</strong>
            </div>
            <div v-for="job in briefing.failed_jobs" :key="job.id" class="job-line failed">
              <span>{{ jobLabel(job.kind) }}</span><details v-if="job.error_code"><summary>{{ job.error_code === 'source_rate_limited' ? t('来源限流') : t('运行失败') }}</summary><code>{{ job.error_code }}</code></details><strong v-else>{{ t('失败') }}</strong>
            </div>
            <p v-if="!briefing.pending_catchups.length && !briefing.active_jobs.length && !briefing.failed_jobs.length" class="section-empty">
              {{ t('暂无需要处理的任务。') }}
            </p>
          </section>
        </aside>
      </div>
    </template>
  </template>
  </AsyncBoundary>
</template>

<style scoped>
.section-label { margin: 0 0 9px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: .13em; }
.quick-search { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 6px; }
.quick-search label { grid-column: 1 / -1; color: var(--color-muted-strong); font-size: var(--type-meta); font-weight: 600; }
.quick-search input { width: min(230px, 34vw); min-height: 42px; }
.quick-search button { min-height: 42px; padding: 0 12px; border: 1px solid var(--color-line); border-radius: var(--radius-sm); background: var(--color-paper-raised); color: var(--color-forest); cursor: pointer; font-size: 12px; font-weight: 700; }
h2, h3, p { margin-top: 0; }
h2, h3 { font-family: var(--font-editorial); font-weight: 500; }
.primary-action, .empty-briefing a { display: inline-flex; min-height: 42px; align-items: center; justify-content: center; gap: 8px; padding: 0 17px; border-radius: var(--radius-sm); background: var(--color-forest); color: var(--color-on-accent); font-size: 12px; font-weight: 700; text-decoration: none; }
.primary-action { align-self: flex-end; }
.primary-action:focus-visible, a:focus-visible, button:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
.empty-briefing { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(280px, .7fr); gap: clamp(48px, 9vw, 120px); padding-top: clamp(52px, 9vw, 108px); }
.empty-briefing h2 { max-width: 600px; margin-bottom: 18px; font-size: clamp(29px, 3.4vw, 44px); line-height: 1.12; }
.empty-briefing > div > p:not(.section-label) { max-width: 610px; color: var(--color-muted-strong); line-height: 1.8; }
.empty-briefing a { margin-top: 18px; }
.empty-briefing ol { margin: 0; padding: 0; border-top: 2px solid var(--color-forest); list-style: none; }
.empty-briefing li { display: flex; gap: 15px; padding: 17px 0; border-bottom: 1px solid var(--color-line); font-size: 12px; }
.empty-briefing li span { color: var(--color-muted); font-family: var(--font-editorial); }
.briefing-totals { display: flex; flex-wrap: wrap; align-items: stretch; margin-top: 28px; border-top: 1px solid var(--color-line); border-bottom: 1px solid var(--color-line); }
.briefing-totals div { min-width: 118px; padding: 14px 28px 13px 0; }
.briefing-totals strong, .briefing-totals span { display: block; }
.briefing-totals strong { font-family: var(--font-editorial); font-size: 23px; font-weight: 500; }
.briefing-totals span { margin-top: 2px; color: var(--color-muted); font-size: var(--type-meta); }
.briefing-totals p { margin: 0 0 0 auto; padding: 18px 0; color: var(--color-muted); font-size: var(--type-meta); }
.briefing-grid { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(240px, .75fr); gap: clamp(28px, 4vw, 64px); margin-top: 36px; }
.briefing-section + .briefing-section { margin-top: 58px; }
.section-heading { display: flex; align-items: end; justify-content: space-between; gap: 18px; padding-bottom: 15px; }
.section-heading h2 { margin: 0; font-size: 29px; }
.section-heading > span, .section-heading a { color: var(--color-muted); font-size: var(--type-meta); }
.section-heading.compact h2 { font-size: 22px; }
.paper-row { display: grid; grid-template-columns: 34px minmax(0, 1fr) 38px; gap: 16px; align-items: start; padding: 19px 0; border-top: 1px solid var(--color-line); }
.paper-row:first-of-type { border-top: 2px solid var(--color-forest); }
.paper-row:focus-within, .paper-row:hover { background: var(--color-forest-hover-soft); }
.job-line details { text-align: end; }
.job-line summary { cursor: pointer; color: var(--color-danger); }
.job-line code { overflow-wrap: anywhere; }
.position { padding-top: 3px; color: var(--color-muted); font-family: var(--font-editorial); font-size: var(--type-meta); }
.paper-row > div > a { color: var(--color-ink); font-family: var(--font-editorial); font-size: 19px; line-height: 1.3; text-decoration: none; }
.paper-row > div > p { margin: 7px 0 0; color: var(--color-muted); font-size: var(--type-meta); }
.paper-scores { display: flex; flex-wrap: wrap; gap: 6px 15px; margin-top: 11px; color: var(--color-muted-strong); font-size: var(--type-meta); }
.paper-row .paper-warning { display: flex; align-items: center; gap: 5px; color: var(--color-danger); }
.row-action { display: grid; width: 38px; height: 38px; place-items: center; border: 1px solid var(--color-line); border-radius: 50%; color: var(--color-forest); }
.revision-row { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 15px 0; border-top: 1px solid var(--color-line); }
.revision-row h3 { margin: 0; font-size: 16px; }
.revision-row p, .revision-row > span { margin: 5px 0 0; color: var(--color-muted); font-size: var(--type-meta); }
.section-empty { padding: 18px 0; color: var(--color-muted); font-size: var(--type-meta); }
aside > section { padding: 18px 20px; border: 1px solid var(--color-line); border-top: 3px double var(--color-forest); background: var(--color-paper-raised); }
aside > section + section { margin-top: 24px; }
.exploration-hint { position: relative; }
.exploration-hint > svg { color: var(--color-forest); }
.exploration-hint h2 { margin: 6px 24px 7px 0; font-size: 21px; }
.exploration-hint > p:not(.section-label) { margin: 0; color: var(--color-muted-strong); font-size: var(--type-meta); line-height: 1.6; }
.exploration-hint > div { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 11px; }
.exploration-hint > div span { padding: 4px 6px; border: 1px solid var(--color-line); border-radius: 999px; color: var(--color-muted-strong); font-size: var(--type-meta); }
.dismiss-hint { position: absolute; top: 0; right: 0; display: grid; width: 28px; height: 28px; place-items: center; border: 0; background: transparent; color: var(--color-muted); cursor: pointer; }
.open-hint { min-height: 32px; display: inline-flex; align-items: center; gap: 5px; margin-top: 13px; padding: 0; border: 0; background: transparent; color: var(--color-forest); font-size: var(--type-meta); font-weight: 700; cursor: pointer; }
.open-hint:disabled { cursor: not-allowed; opacity: .55; }
.focus-plans article { padding: 14px 0; border-top: 1px solid var(--color-line); }
.focus-plans h3 { margin: 0; font-size: 17px; }
.focus-plans article > p { margin: 6px 0 10px; color: var(--color-muted); font-size: var(--type-meta); line-height: 1.55; }
.focus-plans article > div { display: flex; justify-content: space-between; gap: 12px; color: var(--color-muted); font-size: var(--type-meta); }
.focus-plans article a { color: var(--color-forest); }
.attention-line, .job-line { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 0; padding: 12px 0; border-top: 1px solid var(--color-line); color: var(--color-muted-strong); font-size: var(--type-meta); }
.attention-line { justify-content: flex-start; color: var(--color-wine); }
.job-line strong { color: var(--color-forest); font-size: var(--type-meta); }
.job-line.failed strong { color: var(--color-danger); }
@media (max-width: 900px) { .briefing-grid, .empty-briefing { grid-template-columns: 1fr; } aside { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 28px; } aside > section + section { margin-top: 0; } }
@media (max-width: 640px) {
  .quick-search, .quick-search input { width: 100%; }
  .quick-search input, .quick-search button, .primary-action, .open-hint { min-height: 44px; }
  .primary-action { align-self: flex-start; }
  .briefing-totals p { width: 100%; margin-left: 0; border-top: 1px solid var(--color-line); }
  .paper-row { grid-template-columns: 28px minmax(0, 1fr) 44px; }
  .row-action, .dismiss-hint { width: 44px; height: 44px; }
  .section-heading.compact, .focus-plans article > div { align-items: center; }
  .section-heading.compact a, .focus-plans article a { display: inline-flex; align-items: center; min-height: 44px; }
  .job-line summary { min-height: 44px; align-content: center; }
  aside { grid-template-columns: 1fr; }
  aside > section + section { margin-top: 28px; }
  .revision-row { align-items: flex-start; flex-direction: column; gap: 3px; }
}
</style>
