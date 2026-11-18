<script setup lang="ts">
import { discoveryText as t, messageRef } from '@/stores/locale'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { PhArrowLeft, PhArrowClockwise, PhInfo, PhX } from '@phosphor-icons/vue'
import { useRoute } from 'vue-router'

import type { components } from '@/api/schema'
import AnalysisPanel from '@/components/AnalysisPanel.vue'
import RecommendationCard from '@/components/RecommendationCard.vue'
import { apiJson } from '@/lib/json-api'
import {
  discoveryBatchErrorLabel,
  discoveryBatchStatusLabel,
  discoveryRunStatusLabel,
  discoveryStopReasonLabel,
  recommendationDegradationLabel,
} from '@/lib/discovery-status'
import { useJobStore } from '@/stores/jobs'

type RecommendationRun = components['schemas']['RecommendationRunResponse']
type DiscoveryRun = components['schemas']['DiscoveryRunResponse']
type RecommendationPaper = components['schemas']['RecommendationPaperResponse']
type RankingView = components['schemas']['RankingView']
type ScoreSnapshot = components['schemas']['ScoreSnapshotResponse']
type LibraryState = components['schemas']['LibraryStateResponse']

const route = useRoute()
const jobs = useJobStore()
const runId = computed(() => String(route.params.runId ?? ''))
const result = ref<RecommendationRun>()
const discovery = ref<DiscoveryRun>()
const discoveryError = messageRef(t)
const activeView = ref<RankingView>('composite')
const loading = ref(true)
const refreshing = ref(false)
const errorMessage = messageRef(t)
const evidence = ref<ScoreSnapshot>()
const evidenceLoading = ref(false)
const evidenceError = messageRef(t)
const analysisPaper = ref<RecommendationPaper>()
let generation = 0

const views: ReadonlyArray<{ key: RankingView, label: string }> = [
  { key: 'composite', get label() { return t('综合') } },
  { key: 'relevance', get label() { return t('最相关') } },
  { key: 'quality', get label() { return t('最高质量') } },
  { key: 'newest', get label() { return t('最新') } },
  { key: 'preference', get label() { return t('最符合偏好') } },
]

async function load(view: RankingView = activeView.value): Promise<void> {
  const currentGeneration = ++generation
  loading.value = true
  errorMessage.value = ''
  try {
    const loaded = await apiJson<RecommendationRun>(
      `/api/v1/runs/${encodeURIComponent(runId.value)}/recommendations?view=${encodeURIComponent(view)}`,
    )
    if (currentGeneration !== generation) return
    result.value = loaded
    activeView.value = loaded.view
    if (loaded.candidate_count === 0 && discovery.value?.id !== loaded.run_id) {
      try {
        const sourceRun = await apiJson<DiscoveryRun>(`/api/v1/runs/${encodeURIComponent(loaded.run_id)}`)
        if (currentGeneration !== generation) return
        discovery.value = sourceRun
        discoveryError.value = ''
      } catch (caught) {
        if (currentGeneration === generation) {
          discoveryError.value = caught instanceof Error ? caught.message : '无法读取来源检索明细'
        }
      }
    }
  } catch (caught) {
    if (currentGeneration === generation) {
      errorMessage.value = caught instanceof Error ? caught.message : '暂时无法读取推荐结果'
    }
  } finally {
    if (currentGeneration === generation) loading.value = false
  }
}

async function selectView(view: RankingView): Promise<void> {
  if (view === activeView.value || loading.value) return
  evidence.value = undefined
  evidenceError.value = ''
  await load(view)
}

async function showEvidence(snapshotId: string): Promise<void> {
  evidenceLoading.value = true
  evidenceError.value = ''
  try {
    evidence.value = await apiJson<ScoreSnapshot>(
      `/api/v1/scores/snapshots/${encodeURIComponent(snapshotId)}`,
    )
  } catch (caught) {
    evidenceError.value = caught instanceof Error ? caught.message : '评分证据暂时不可用'
  } finally {
    evidenceLoading.value = false
  }
}

function updateLibraryState(state: LibraryState): void {
  if (!result.value) return
  const update = (paper: RecommendationPaper): RecommendationPaper => paper.work_id === state.work_id
    ? {
        ...paper,
        library_state: {
          work_id: state.work_id,
          favorite: state.favorite,
          feedback: state.feedback,
          feedback_reasons: state.feedback_reasons,
          pinned: state.pinned,
          reading_progress: state.reading_progress,
          analysis_state: state.analysis_state,
          persisted: state.persisted,
          created_at: state.created_at,
          updated_at: state.updated_at,
        },
      }
    : paper
  result.value = {
    ...result.value,
    items: result.value.items.map(update),
    historical_matches: result.value.historical_matches.map(update),
  }
}

async function refreshRecommendations(): Promise<void> {
  if (refreshing.value || !result.value?.candidate_count) return
  refreshing.value = true
  errorMessage.value = ''
  try {
    await apiJson(`/api/v1/runs/${encodeURIComponent(runId.value)}/recommendations/refresh`, {
      method: 'POST',
      headers: { 'Idempotency-Key': newRecommendationRefreshKey() },
      body: '{}',
    })
    await jobs.refresh()
  } catch (caught) {
    errorMessage.value = caught instanceof Error ? caught.message : '无法启动重新评分'
  } finally {
    refreshing.value = false
  }
}

function newRecommendationRefreshKey(): string {
  const random = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `recommendations-ui-${random}`
}

function missingPolicyLabel(snapshot: ScoreSnapshot): string {
  const strategy = snapshot.missing_policy.strategy
  if (strategy === 'neutral_prior') {
    return t('缺失证据采用中性参考值，同时降低覆盖度与置信度')
  }
  if (strategy === 'not_applicable_excluded') {
    return t('不适用的维度不参与评分，剩余权重按规则重新分配')
  }
  return t('未知与不适用数据已按当前规则分别处理')
}

watch(runId, () => {
  result.value = undefined
  discovery.value = undefined
  discoveryError.value = ''
  evidence.value = undefined
  evidenceError.value = ''
  analysisPaper.value = undefined
  void load()
}, { immediate: true })
onBeforeUnmount(() => { generation += 1 })
</script>

<template>
  <header class="results-header">
    <div>
      <RouterLink to="/plans" class="back-link"><PhArrowLeft :size="15" aria-hidden="true" /> {{ t('关注计划') }}</RouterLink>
      <p class="eyebrow">{{ t('可解释推荐') }}</p>
      <h1>{{ t('推荐结果') }}</h1>
      <p class="lede">{{ t('评分来自冻结证据与规则；Agent 不参与打分或排序。') }}</p>
    </div>
    <button
      type="button"
      class="refresh-action"
      data-test="refresh-recommendations"
      :disabled="refreshing || !result?.candidate_count"
      :title="t('重新评分仅处理已有候选，不会重新检索论文')"
      @click="refreshRecommendations"
    >
      <PhArrowClockwise :size="16" aria-hidden="true" />
      {{ refreshing ? t('已加入任务') : t('重新评分') }}
    </button>
  </header>

  <p v-if="errorMessage" class="notice error" role="alert">{{ errorMessage }}</p>
  <p v-if="loading && !result" class="quiet-state" role="status">{{ t('正在读取不可变推荐结果…') }}</p>

  <template v-if="result">
    <section class="result-summary" :aria-label="t('推荐批次摘要')">
      <div><span>{{ t('候选') }}</span><strong>{{ result.candidate_count }}</strong></div>
      <div><span>{{ t('新推荐') }}</span><strong>{{ result.recommendation_count }}</strong></div>
      <div><span>{{ t('探索') }}</span><strong>{{ result.exploration_count }}</strong></div>
      <div><span>{{ t('结果版本') }}</span><strong>v{{ result.recommendation_set_version }}</strong></div>
      <p v-if="result.degradation_reasons.length">
        <PhInfo :size="14" aria-hidden="true" />
        {{ t('结果说明：') }}{{ result.degradation_reasons.map(recommendationDegradationLabel).join(' · ') }}
      </p>
    </section>

    <section v-if="result.candidate_count === 0" class="discovery-details" data-test="discovery-details" aria-labelledby="discovery-details-title">
      <h2 id="discovery-details-title">{{ t('为什么没有候选论文？') }}</h2>
      <template v-if="discovery">
        <p>{{ discoveryRunStatusLabel(discovery.status) }} · {{ discovery.months.join('、') }}<span v-if="discovery.failed_batches"> · {{ discovery.failed_batches }} {{ t('个来源批次失败，结果可能不完整。') }}</span></p>
        <p>{{ t('获取记录后，仍需通过更新时间与已确认关键词校验。下面的数量按月份和来源分别统计。') }}</p>
        <div class="source-table-scroll" role="region" :aria-label="t('来源筛选统计，可左右滚动')" tabindex="0">
          <table>
            <thead><tr><th scope="col">{{ t('月份 / 来源') }}</th><th scope="col">{{ t('状态') }}</th><th scope="col">{{ t('获取记录') }}</th><th scope="col">{{ t('日期内') }}</th><th scope="col">{{ t('关键词匹配') }}</th></tr></thead>
            <tbody>
              <template v-for="batch in discovery.batches" :key="batch.id">
                <tr>
                  <th scope="row">{{ batch.month }} / {{ batch.connector_id }}</th>
                  <td>{{ discoveryBatchStatusLabel(batch) }}</td>
                  <td>{{ batch.fetched_count }}</td>
                  <td>{{ batch.filter_counts?.in_date_range_count ?? '—' }}</td>
                  <td>{{ batch.accepted_count }}</td>
                </tr>
                <tr><td colspan="5" class="source-note">
                  <p v-if="batch.filter_counts">{{ t('日期外') }} {{ batch.filter_counts.outside_date_range_count }} {{ t('· 缺少更新时间') }} {{ batch.filter_counts.missing_updated_at_count }} {{ t('· 关键词不符') }} {{ batch.filter_counts.keyword_mismatch_count }}<span v-if="batch.filter_counts.evaluated_count !== batch.fetched_count"> {{ t('· 筛选明细仅覆盖') }} {{ batch.filter_counts.evaluated_count }}/{{ batch.fetched_count }} {{ t('条记录') }}</span></p>
                  <p v-else-if="batch.fetched_count">{{ t('这次历史运行未保存详细过滤数量；重新运行计划后可查看。') }}</p>
                  <p v-if="batch.status === 'failed'" class="source-error">{{ discoveryBatchErrorLabel(batch) }}</p>
                  <p v-if="discoveryStopReasonLabel(batch)">{{ discoveryStopReasonLabel(batch) }}</p>
                </td></tr>
              </template>
            </tbody>
          </table>
        </div>
      </template>
      <p v-else-if="discoveryError" role="status">{{ t('来源明细暂时不可用：') }}{{ discoveryError }}</p>
      <p v-else role="status">{{ t('正在读取来源检索与过滤明细…') }}</p>
      <p>{{ t('必需概念组需要同时命中，同组关键词命中任意一个即可。可在新计划中扩大月份范围或调整关键词，确认后再检索。') }}</p>
      <p>{{ t('重新评分只处理已有候选。重新检索请回到关注计划，点击“立即运行”。') }}</p>
      <div class="discovery-actions"><RouterLink to="/plans">{{ t('返回关注计划') }}</RouterLink><RouterLink to="/plans/new">{{ t('新建计划调整条件') }}</RouterLink></div>
    </section>

    <nav class="ranking-views" :aria-label="t('推荐排序方式')">
      <button
        v-for="view in views"
        :key="view.key"
        type="button"
        :data-test="`ranking-view-${view.key}`"
        :aria-pressed="activeView === view.key"
        :disabled="loading"
        @click="selectView(view.key)"
      >
        {{ view.label }}
      </button>
    </nav>

    <section class="recommendation-list" aria-labelledby="new-recommendations-title">
      <div class="section-heading">
        <div><p>{{ t('本次新论文') }}</p><h2 id="new-recommendations-title">{{ t('优先阅读') }}</h2></div>
        <span>{{ result.items.length }} {{ t('篇') }}</span>
      </div>
      <RecommendationCard
        v-for="paper in result.items"
        :key="paper.item_id"
        :paper="paper"
        @state-updated="updateLibraryState"
        @evidence="showEvidence"
        @analyze="analysisPaper = $event"
      />
      <p v-if="!result.items.length" class="quiet-state">{{ t('当前视图没有可推荐的新论文。') }}</p>
    </section>

    <details
      v-if="result.historical_matches.length"
      data-test="historical-matches"
      class="historical-matches"
    >
      <summary>{{ t('历史匹配 ·') }} {{ result.historical_matches.length }} {{ t('篇') }}</summary>
      <p>{{ t('这些论文已读、已收藏或已分析，不占本次新论文配额。') }}</p>
      <RecommendationCard
        v-for="paper in result.historical_matches"
        :key="paper.item_id"
        :paper="paper"
        @state-updated="updateLibraryState"
        @evidence="showEvidence"
        @analyze="analysisPaper = $event"
      />
    </details>
  </template>

  <aside v-if="evidenceLoading || evidence || evidenceError" data-test="score-evidence" class="evidence-drawer" aria-live="polite">
    <button type="button" :aria-label="t('关闭评分证据')" @click="evidence = undefined; evidenceError = ''"><PhX :size="16" aria-hidden="true" /></button>
    <p class="eyebrow">{{ t('评分证据') }}</p>
    <p v-if="evidenceLoading">{{ t('正在读取完整快照…') }}</p>
    <p v-else-if="evidenceError" role="alert">{{ evidenceError }}</p>
    <template v-else-if="evidence">
      <h2>{{ evidence.score_type }} · {{ Math.round(evidence.score) }}</h2>
      <dl>
        <div><dt>{{ t('规则') }}</dt><dd>{{ evidence.rule_version }}</dd></div>
        <div><dt>{{ t('覆盖度') }}</dt><dd>{{ Math.round(evidence.coverage) }}</dd></div>
        <div><dt>{{ t('置信度') }}</dt><dd>{{ Math.round(evidence.confidence) }}</dd></div>
        <div><dt>{{ t('缺失数据策略') }}</dt><dd>{{ missingPolicyLabel(evidence) }}</dd></div>
      </dl>
    </template>
  </aside>

  <aside v-if="analysisPaper" data-test="analysis-drawer" class="analysis-drawer" :aria-label="t('论文分析')">
    <header>
      <div><p class="eyebrow">{{ t('按需分析') }}</p><h2>{{ analysisPaper.title }}</h2></div>
      <button type="button" :aria-label="t('关闭论文分析')" @click="analysisPaper = undefined"><PhX :size="18" aria-hidden="true" /></button>
    </header>
    <AnalysisPanel
      :work-id="analysisPaper.work_id"
      :document-version-id="null"
      :document-content-sha256="null"
      auto-open-preflight
    />
  </aside>
</template>

<style scoped>
.results-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 30px; padding-bottom: 28px; border-bottom: 1px solid var(--color-line); }
.back-link { display: inline-flex; align-items: center; gap: 5px; margin-bottom: 28px; color: var(--color-muted); font-size: var(--type-meta); text-decoration: none; }
.eyebrow, .section-heading p { margin: 0 0 8px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: .12em; }
h1, h2, p { margin-top: 0; }
h1, h2 { font-family: var(--font-editorial); font-weight: 500; }
h1 { margin-bottom: 10px; font-size: clamp(36px, 4vw, 52px); letter-spacing: -.035em; }
.lede { max-width: 650px; margin-bottom: 0; color: var(--color-muted); line-height: 1.7; }
.refresh-action { display: inline-flex; min-height: 39px; align-items: center; gap: 7px; padding: 0 14px; border: 1px solid var(--color-line); border-radius: var(--radius-sm); background: transparent; color: var(--color-forest); font-size: var(--type-meta); font-weight: 700; cursor: pointer; }
.refresh-action:disabled { opacity: .55; cursor: not-allowed; }
.discovery-details { margin-top: 28px; padding: 22px; border: 1px solid var(--color-line); border-radius: var(--radius-sm); background: var(--color-paper-raised); }
.discovery-details h2 { margin-bottom: 14px; font-size: 24px; }
.discovery-details p { color: var(--color-muted); font-size: 12px; line-height: 1.7; }
.source-table-scroll { overflow-x: auto; margin-bottom: 18px; }
.source-table-scroll:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
.discovery-details table { width: 100%; border-collapse: collapse; text-align: left; font-size: 12px; }
.discovery-details th, .discovery-details td { padding: 9px 12px 9px 0; border-bottom: 1px solid var(--color-line); white-space: nowrap; }
.discovery-details thead th { color: var(--color-muted); font-weight: 500; }
.discovery-details .source-note { white-space: normal; }
.source-note p { margin: 0; }
.source-note .source-error { color: var(--color-danger); }
.discovery-actions { display: flex; flex-wrap: wrap; gap: 20px; }
.discovery-actions a { padding: 5px 0; color: var(--color-forest); font-size: 12px; }
.result-summary { display: flex; flex-wrap: wrap; align-items: stretch; gap: 0; margin: 28px 0 0; border-top: 1px solid var(--color-line); border-bottom: 1px solid var(--color-line); }
.result-summary > div { min-width: 108px; padding: 13px 24px 13px 0; }
.result-summary span, .result-summary strong { display: block; }
.result-summary span { color: var(--color-muted); font-size: var(--type-meta); }
.result-summary strong { margin-top: 3px; font-family: var(--font-editorial); font-size: 20px; font-weight: 500; }
.result-summary p { display: flex; align-items: center; gap: 6px; margin: 0 0 0 auto; padding: 13px 0; color: var(--color-wine); font-size: var(--type-meta); }
.ranking-views { display: flex; gap: 4px; margin: 38px 0 0; overflow-x: auto; }
.ranking-views button { min-height: 35px; flex: none; padding: 0 13px; border: 0; border-bottom: 1px solid var(--color-line); background: transparent; color: var(--color-muted); font-size: var(--type-meta); cursor: pointer; }
.ranking-views button[aria-pressed='true'] { border-bottom: 2px solid var(--color-forest); color: var(--color-forest); font-weight: 750; }
.ranking-views button:focus-visible, .refresh-action:focus-visible, aside button:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
.recommendation-list { margin-top: 42px; }
.section-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; padding-bottom: 14px; }
.section-heading h2 { margin: 0; font-size: 29px; }
.section-heading > span { color: var(--color-muted); font-size: var(--type-meta); }
.quiet-state { padding: 34px 0; color: var(--color-muted); }
.notice.error { margin-top: 20px; color: var(--color-danger); }
.historical-matches { margin-top: 54px; border-top: 1px solid var(--color-line); }
.historical-matches summary { padding: 17px 0; color: var(--color-forest); font-family: var(--font-editorial); font-size: 19px; cursor: pointer; }
.historical-matches > p { color: var(--color-muted); font-size: var(--type-meta); }
.evidence-drawer, .analysis-drawer { position: fixed; z-index: 30; top: 18px; right: 18px; bottom: 18px; width: min(500px, calc(100vw - 36px)); overflow: auto; padding: 24px; border: 1px solid var(--color-line); border-radius: var(--radius-md); background: var(--color-paper-raised); box-shadow: 0 20px 60px rgb(25 35 28 / 18%); }
.analysis-drawer { width: min(720px, calc(100vw - 36px)); }
.evidence-drawer > button, .analysis-drawer header > button { float: right; display: grid; width: 34px; height: 34px; place-items: center; border: 1px solid var(--color-line); border-radius: 50%; background: transparent; color: var(--color-muted); cursor: pointer; }
.evidence-drawer h2 { font-size: 24px; }
.evidence-drawer dl { display: grid; gap: 0; margin: 24px 0 0; border-top: 1px solid var(--color-line); }
.evidence-drawer dl div { display: grid; grid-template-columns: 110px 1fr; gap: 16px; padding: 11px 0; border-bottom: 1px solid var(--color-line); font-size: var(--type-meta); }
.evidence-drawer dt { color: var(--color-muted); }
.evidence-drawer dd { margin: 0; overflow-wrap: anywhere; }
.analysis-drawer header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid var(--color-line); }
.analysis-drawer header h2 { margin: 0; font-size: 22px; }
@media (max-width: 680px) { .results-header { align-items: stretch; flex-direction: column; } .refresh-action { align-self: flex-start; } .result-summary p { width: 100%; margin-left: 0; } .evidence-drawer, .analysis-drawer { inset: 0; width: 100%; border: 0; border-radius: 0; } }
</style>
