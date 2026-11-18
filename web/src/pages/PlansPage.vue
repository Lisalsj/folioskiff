<script setup lang="ts">
import { currentLocale, messageRef, discoveryText as t } from '@/stores/locale'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { PhArrowRight, PhClockCounterClockwise, PhPlus } from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import MergeReviewQueue from '@/components/MergeReviewQueue.vue'
import { apiJson } from '@/lib/json-api'
import {
  discoveryBatchErrorLabel,
  discoveryBatchStatusLabel,
  discoveryRunStatusLabel,
} from '@/lib/discovery-status'
import { useJobStore } from '@/stores/jobs'

type Plan = components['schemas']['DiscoveryPlanResponse']
type Catchup = components['schemas']['PlanCatchupResponse']
type Run = components['schemas']['DiscoveryRunResponse']
type RunPage = components['schemas']['DiscoveryRunPageResponse']

const plans = ref<Plan[]>([])
const catchups = ref<Catchup[]>([])
const openPlan = ref('')
const recentRuns = ref<Run[]>([])
const historyError = messageRef(t)
const readingHistory = ref(false)
let historyRequest: AbortController | undefined
const loading = ref(true)
const error = messageRef(t)
const runningPlan = ref<string>()
const runningCatchup = ref<string>()
const retryingRun = ref<string>()
const jobs = useJobStore()

function rangeLabel(plan: Plan): string {
  if (plan.date_range.mode === 'rolling') {
    return t('最近 {0} 个自然月', plan.date_range.rolling_months ?? 1)
  }
  return `${plan.date_range.start_month} — ${plan.date_range.end_month}`
}

function hasRetryableBatch(run: Run): boolean {
  return run.batches.some((batch) => batch.status === 'failed' && batch.retryable)
}

function runTimeLabel(run: Run): string {
  return new Date(run.completed_at ?? run.started_at ?? run.created_at).toLocaleString(currentLocale())
}

async function loadRunHistory(planId: string): Promise<void> {
  historyRequest?.abort()
  const request = new AbortController()
  historyRequest = request
  historyError.value = ''
  readingHistory.value = true
  try {
    const page = await apiJson<RunPage>(
      `/api/v1/plans/${encodeURIComponent(planId)}/runs?limit=3&offset=0`, { signal: request.signal },
    )
    if (!request.signal.aborted) recentRuns.value = page.items
  } catch {
    if (!request.signal.aborted) historyError.value = '最近运行暂时不可用。'
  } finally {
    if (!request.signal.aborted) readingHistory.value = false
  }
}

function toggleHistory(planId: string): void {
  historyRequest?.abort()
  recentRuns.value = []
  openPlan.value = openPlan.value === planId ? '' : planId
  if (openPlan.value) void loadRunHistory(planId)
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [loadedPlans, loadedCatchups] = await Promise.all([
      apiJson<Plan[]>('/api/v1/plans'),
      apiJson<Catchup[]>('/api/v1/plans/catchups'),
    ])
    plans.value = loadedPlans
    catchups.value = loadedCatchups
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法读取关注计划'
  } finally {
    loading.value = false
  }
}

async function refreshAfterAction(): Promise<void> {
  const previous = historyRequest
  await jobs.refresh()
  if (openPlan.value && previous === historyRequest) await loadRunHistory(openPlan.value)
}

async function retryRun(runId: string): Promise<void> {
  retryingRun.value = runId
  error.value = ''
  try {
    await apiJson(`/api/v1/runs/${runId}/retry`, { method: 'POST' })
    await refreshAfterAction()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法重试失败来源'
  } finally {
    retryingRun.value = undefined
  }
}

async function runPlan(planId: string): Promise<void> {
  runningPlan.value = planId
  error.value = ''
  try {
    await apiJson(`/api/v1/plans/${planId}/runs`, { method: 'POST' })
    await refreshAfterAction()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法启动计划'
  } finally {
    runningPlan.value = undefined
  }
}

async function confirmCatchup(catchupId: string): Promise<void> {
  runningCatchup.value = catchupId
  error.value = ''
  try {
    await apiJson(`/api/v1/plans/catchups/${catchupId}/confirm`, { method: 'POST' })
    catchups.value = catchups.value.filter((item) => item.id !== catchupId)
    await refreshAfterAction()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法确认补跑任务'
  } finally {
    runningCatchup.value = undefined
  }
}

watch(() => jobs.jobs.filter((job) => job.kind === 'discovery.run').map((job) => `${job.id}:${job.status}`).join('|'), () => {
  if (openPlan.value) void loadRunHistory(openPlan.value)
})

onMounted(() => void load())
onBeforeUnmount(() => historyRequest?.abort())
</script>

<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">{{ t('研究发现') }}</p>
      <h1>{{ t('关注计划') }}</h1>
      <p class="lede">{{ t('把研究意图、确认关键词与月份来源保存为可复现的论文发现计划。') }}</p>
    </div>
    <RouterLink class="primary-action" to="/plans/new">
      <PhPlus :size="18" weight="bold" aria-hidden="true" />
      {{ t('新建关注计划') }}
    </RouterLink>
  </header>

  <p v-if="error" class="notice error" role="alert">{{ error }}</p>

  <section v-if="catchups.length" class="catchups" aria-labelledby="catchup-title">
    <div class="section-heading">
      <PhClockCounterClockwise :size="21" aria-hidden="true" />
      <div>
        <p>{{ t('应用关闭期间') }}</p>
        <h2 id="catchup-title">{{ t('待你确认的补跑') }}</h2>
      </div>
    </div>
    <div v-for="item in catchups" :key="item.id" class="catchup-row">
      <span>{{ new Date(item.scheduled_for).toLocaleString(currentLocale()) }}</span>
      <button
        type="button"
        :disabled="runningCatchup === item.id"
        @click="confirmCatchup(item.id)"
      >
        {{ runningCatchup === item.id ? t('正在启动') : t('确认并补跑') }}
      </button>
    </div>
  </section>

  <section class="plan-list" aria-labelledby="plan-list-title">
    <div class="section-title-row">
      <div>
        <p class="section-label">{{ t('已确认的研究范围') }}</p>
        <h2 id="plan-list-title">{{ t('你的计划') }}</h2>
      </div>
      <span v-if="!loading">{{ plans.length }} {{ t('个') }}</span>
    </div>

    <p v-if="loading" class="quiet-state">{{ t('正在读取本地计划…') }}</p>
    <div v-else-if="plans.length" class="rows">
      <article v-for="(plan, index) in plans" :key="plan.id" class="plan-entry">
        <div class="plan-row">
          <div class="plan-index" aria-hidden="true">{{ String(index + 1).padStart(2, '0') }}</div>
          <div class="plan-copy">
            <h3>{{ plan.name }}</h3>
            <p>{{ plan.research_intent }}</p>
            <dl>
              <div><dt>{{ t('月份') }}</dt><dd>{{ rangeLabel(plan) }}</dd></div>
              <div><dt>{{ t('来源') }}</dt><dd>{{ plan.sources.join(' · ') }}</dd></div>
              <div><dt>{{ t('频率') }}</dt><dd>{{ plan.schedule.frequency }}</dd></div>
            </dl>
          </div>
          <button
            type="button"
            class="run-action"
            :disabled="runningPlan === plan.id"
            @click="runPlan(plan.id)"
          >
            {{ runningPlan === plan.id ? t('正在启动') : t('立即运行') }}
            <PhArrowRight :size="17" aria-hidden="true" />
          </button>
        </div>

        <section
          class="recent-runs"
          :data-test="`recent-runs-${plan.id}`"
          :aria-labelledby="`recent-runs-title-${plan.id}`"
        >
          <h4 :id="`recent-runs-title-${plan.id}`">
            <button type="button" :data-test="`toggle-runs-${plan.id}`" :aria-expanded="openPlan === plan.id"
              :aria-controls="openPlan === plan.id ? `run-history-${plan.id}` : undefined" @click="toggleHistory(plan.id)">{{ t('最近运行') }}</button>
          </h4>
          <div v-if="openPlan === plan.id" :id="`run-history-${plan.id}`">
            <p v-if="historyError" class="history-error" role="status">
              {{ historyError }} <button type="button" @click="loadRunHistory(plan.id)">{{ t('重试') }}</button>
            </p>
            <p v-else-if="readingHistory && !recentRuns.length" class="no-runs" role="status">{{ t('正在读取运行记录…') }}</p>
            <ol v-else-if="recentRuns.length" class="run-history">
              <li v-for="run in recentRuns" :key="run.id" class="run-record">
                <div class="run-summary">
                  <div>
                    <strong>{{ discoveryRunStatusLabel(run.status) }}</strong>
                    <span>{{ runTimeLabel(run) }}</span>
                    <span>{{ run.succeeded_batches }}/{{ run.total_batches }} {{ t('个来源批次完成') }}</span>
                  </div>
                  <div class="run-actions">
                    <RouterLink
                      :to="`/runs/${run.id}/results`"
                      :data-test="`view-results-${run.id}`"
                    >
                      {{ t('查看推荐') }}
                    </RouterLink>
                    <button
                      v-if="hasRetryableBatch(run)"
                      type="button"
                      :data-test="`retry-${run.id}`"
                      :disabled="retryingRun === run.id"
                      @click="retryRun(run.id)"
                    >
                      {{ retryingRun === run.id ? t('正在提交') : t('重试失败来源') }}
                    </button>
                  </div>
                </div>
                <ul class="batch-list" :aria-label="t('来源批次状态')">
                  <li
                    v-for="batch in run.batches"
                    :key="batch.id"
                    :class="['batch-status', `is-${batch.status}`]"
                  >
                    <span>{{ batch.month }} · {{ batch.connector_id }} {{ discoveryBatchStatusLabel(batch) }}</span>
                    <span v-if="batch.status === 'succeeded'"> {{ t('· 获取') }} {{ batch.fetched_count }} {{ t('· 匹配') }} {{ batch.accepted_count }}</span>
                    <span v-if="batch.checkpoint.stop_reason === 'scan_limit'"> {{ t('· 已到扫描上限') }}</span>
                    <span v-if="batch.status === 'failed'"> · {{ discoveryBatchErrorLabel(batch) }}</span>
                  </li>
                </ul>
              </li>
            </ol>
            <p v-else class="no-runs">{{ t('尚未运行。关键词确认后，点击“立即运行”开始。') }}</p>
          </div>
        </section>
      </article>
    </div>
    <div v-else class="quiet-state empty">
      <strong>{{ t('还没有关注计划') }}</strong>
      <p>{{ t('从一句研究意图开始，Agent 会先建议关键词；只有你确认后才会检索。') }}</p>
      <RouterLink to="/plans/new">{{ t('创建第一个计划') }}</RouterLink>
    </div>
  </section>

  <MergeReviewQueue />
</template>

<style scoped>
.page-header,
.section-title-row,
.plan-row,
.catchup-row {
  display: flex;
  justify-content: space-between;
  gap: 28px;
}

.page-header {
  align-items: flex-end;
  padding-bottom: 30px;
  border-bottom: 1px solid var(--color-line);
}

.eyebrow,
.section-label,
.section-heading p {
  margin: 0 0 9px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.13em;
}

h1,
h2,
h3,
p { margin-top: 0; }

h1,
h2,
h3 { font-family: var(--font-editorial); font-weight: 500; }
h1 { margin-bottom: 10px; font-size: clamp(36px, 4vw, 52px); letter-spacing: -0.035em; }
h2 { margin-bottom: 0; font-size: 28px; }
.lede { max-width: 670px; margin-bottom: 0; color: var(--color-muted); line-height: 1.7; }

.primary-action,
.run-action,
.catchup-row button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 42px;
  padding: 0 16px;
  border: 1px solid var(--color-forest);
  border-radius: var(--radius-sm);
  background: var(--color-forest);
  color: var(--color-on-accent);
  font-size: 13px;
  font-weight: 650;
  text-decoration: none;
  cursor: pointer;
}

.primary-action:focus-visible,
.run-action:focus-visible,
.catchup-row button:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }

.catchups { margin-top: 34px; padding: 22px 24px; border: 1px solid var(--color-line); border-left: 3px solid var(--color-wine, #7d3438); }
.section-heading { display: flex; align-items: flex-start; gap: 12px; }
.section-heading h2 { font-size: 21px; }
.catchup-row { align-items: center; padding-top: 16px; margin-top: 16px; border-top: 1px solid var(--color-line); font-size: 13px; }
.catchup-row button { min-height: 34px; background: transparent; color: var(--color-forest); }

.plan-list { margin-top: clamp(52px, 8vw, 96px); }
.section-title-row { align-items: end; padding-bottom: 18px; }
.section-title-row > span { color: var(--color-muted); font-size: 12px; }
.rows { border-top: 2px solid var(--color-forest); }
.plan-entry { border-bottom: 1px solid var(--color-line); }
.plan-row { align-items: center; padding: 26px 0 20px; }
.plan-index { align-self: start; width: 34px; color: var(--color-muted); font-family: var(--font-editorial); font-size: 12px; }
.plan-copy { flex: 1; min-width: 0; }
.plan-copy h3 { margin-bottom: 8px; font-size: 22px; }
.plan-copy > p { max-width: 760px; margin-bottom: 16px; color: var(--color-muted-strong); font-size: 13px; line-height: 1.6; }
dl { display: flex; flex-wrap: wrap; gap: 10px 28px; margin: 0; }
dl div { display: flex; gap: 8px; font-size: var(--type-meta); }
dt { color: var(--color-muted); }
dd { margin: 0; }
.run-action { flex: none; background: transparent; color: var(--color-forest); }
.run-action:disabled { opacity: 0.55; cursor: wait; }
.recent-runs { padding: 0 0 24px 62px; }
.recent-runs h4 { margin: 0 0 10px; color: var(--color-muted); font-size: var(--type-meta); letter-spacing: 0.08em; text-transform: uppercase; }
.recent-runs h4 button { min-height: 44px; padding: 0 12px; border: 1px solid var(--color-line); border-radius: var(--radius-sm); background: var(--color-paper-raised); color: var(--color-forest); cursor: pointer; }
.run-history { display: grid; gap: 10px; padding: 0; margin: 0; list-style: none; }
.run-record { padding: 13px 15px; border: 1px solid var(--color-line); border-radius: var(--radius-sm); background: color-mix(in srgb, var(--color-paper) 76%, transparent); }
.run-summary, .run-summary > div, .run-actions, .batch-list { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 14px; }
.run-summary { justify-content: space-between; }
.run-summary strong { color: var(--color-ink); font-size: 13px; }
.run-summary span, .no-runs, .history-error { color: var(--color-muted); font-size: var(--type-meta); }
.run-actions a, .run-actions button { border: 0; background: transparent; color: var(--color-forest); font: inherit; font-size: 12px; font-weight: 650; text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
.run-actions button:disabled { opacity: 0.55; cursor: wait; }
.batch-list { padding: 9px 0 0; margin: 0; list-style: none; }
.batch-status { padding: 3px 8px; border: 1px solid var(--color-line); border-radius: 999px; color: var(--color-muted-strong); font-size: var(--type-meta); }
.batch-status.is-failed { border-color: color-mix(in srgb, var(--color-danger) 45%, var(--color-line)); color: var(--color-danger); }
.history-error, .no-runs { margin: 0; }
.quiet-state { padding: 34px 0; border-top: 1px solid var(--color-line); color: var(--color-muted); }
.quiet-state strong { display: block; margin-bottom: 10px; color: var(--color-ink); font-family: var(--font-editorial); font-size: 22px; font-weight: 500; }
.quiet-state p { max-width: 560px; line-height: 1.7; }
.quiet-state a { color: var(--color-forest); font-weight: 650; }
.notice.error { margin: 24px 0 0; color: var(--color-danger); }

@media (max-width: 720px) {
  .page-header, .plan-row { align-items: stretch; flex-direction: column; }
  .primary-action, .run-action { align-self: flex-start; }
  .plan-index { display: none; }
  .recent-runs { padding-left: 0; }
}
</style>
