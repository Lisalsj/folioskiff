<script setup lang="ts">
import { researchText as t } from '@/stores/locale'
import { ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import {
  PhArrowClockwise,
  PhBinoculars,
  PhGlobeHemisphereWest,
  PhMagicWand,
  PhShieldCheck,
} from '@phosphor-icons/vue'

import KnowledgeResourceList from './KnowledgeResourceList.vue'
import RelatedIntentEditor from './RelatedIntentEditor.vue'
import RelatedPaperList from './RelatedPaperList.vue'
import type { AgentSelection, ContextSelection } from '../api'
import { useRelatedResourcesStore } from '../store'

const props = defineProps<{
  context: ContextSelection | null
  agent: AgentSelection | null
  backendReady: boolean
  supportsExternalResearch: boolean
}>()

const store = useRelatedResourcesStore()
const {
  activeJob,
  canConfirm,
  canRun,
  confirming,
  errorMessage,
  externalWebConsent,
  includeAcademicSources,
  includeExternalWeb,
  intent,
  loading,
  phase,
  refining,
  resourceSet,
  resultTab,
  resumeAvailable,
  savingId,
  starting,
  statusMessage,
} = storeToRefs(store)
const refinementInstruction = ref('')

watch(
  () => props.supportsExternalResearch,
  (supported) => {
    if (!supported && phase.value !== 'running') {
      includeExternalWeb.value = false
      externalWebConsent.value = false
    }
  },
  { immediate: true },
)

async function begin(): Promise<void> {
  if (props.context) await store.beginDraft(props.context)
}

async function refine(): Promise<void> {
  const instruction = refinementInstruction.value.trim()
  if (!instruction || !props.agent) return
  if (await store.refine(instruction, props.agent)) refinementInstruction.value = ''
}

async function run(): Promise<void> {
  await store.run(includeExternalWeb.value ? props.agent : null)
}
</script>

<template>
  <section class="related-task" data-test="related-task">
    <div v-if="phase === 'idle'" class="related-intro">
      <PhBinoculars :size="27" aria-hidden="true" />
      <p class="eyebrow">{{ t('从正在读的论文出发') }}</p>
      <h3>{{ t('找方法邻居，也补齐关键知识') }}</h3>
      <p>{{ t('先在本地整理双语检索词。不会因打开、翻页或切换到此模式而调用模型或联网。') }}</p>
      <div class="intro-actions">
        <button
          v-if="resumeAvailable"
          type="button"
          data-test="resume-related"
          :disabled="loading"
          @click="store.resume"
        >{{ loading ? t('正在恢复…') : t('继续上次关联研究') }}</button>
        <button
          type="button"
          data-test="start-related"
          :class="{ quiet: resumeAvailable }"
          :disabled="loading || !context"
          @click="begin"
        >{{ loading ? t('正在准备…') : resumeAvailable ? t('准备新的检索词') : t('开始准备检索词') }}</button>
      </div>
      <small v-if="!context">{{ t('当前阅读范围还不可用，请等待 PDF 页码载入。') }}</small>
    </div>

    <template v-else-if="intent">
      <RelatedIntentEditor
        v-if="phase === 'draft'"
        :model-value="intent"
        :disabled="refining || confirming"
        @update:model-value="store.editIntent"
      />

      <section v-else-if="phase === 'confirm'" class="confirmed-intent">
        <PhShieldCheck :size="19" aria-hidden="true" />
        <div>
          <p>{{ t('已确认检索意图 · 版本') }} {{ intent.revision }}</p>
          <h3>{{ intent.summary }}</h3>
          <span>{{ intent.required_groups.flat().join(' · ') }}</span>
        </div>
        <button type="button" @click="store.editIntent({ ...intent, confirmed: false, confirmed_at: null })">
          {{ t('返回编辑') }}
        </button>
      </section>

      <section v-if="phase === 'draft'" class="agent-refine" aria-labelledby="agent-refine-title">
        <div>
          <PhMagicWand :size="15" aria-hidden="true" />
          <span><strong id="agent-refine-title">{{ t('让 Agent 建议检索词') }}</strong><small>{{ t('独立操作；不会开始论文检索') }}</small></span>
        </div>
        <textarea
          v-model="refinementInstruction"
          rows="2"
          maxlength="10000"
          :disabled="refining || !backendReady"
          :placeholder="t('例如：更关注可复现评测，并排除纯综述')"
        />
        <button
          type="button"
          data-test="refine-related"
          :disabled="refining || !backendReady || !refinementInstruction.trim()"
          @click="refine"
        >{{ refining ? t('正在建议…') : t('生成建议并显示差异') }}</button>
        <p v-if="!backendReady">{{ t('当前 Agent 不可用；仍可手工编辑并使用学术来源。') }}</p>
      </section>

      <section
        v-if="phase === 'draft' || phase === 'confirm' || phase === 'running'"
        class="retrieval-boundaries"
        aria-labelledby="retrieval-boundaries-title"
      >
        <div class="boundary-title">
          <div>
            <p>{{ t('运行边界') }}</p>
            <h3 id="retrieval-boundaries-title">{{ t('两类检索彼此独立') }}</h3>
          </div>
          <span v-if="phase === 'running'">{{ t('进行中') }} {{ activeJob?.progress ?? 0 }}%</span>
        </div>
        <label>
          <input v-model="includeAcademicSources" type="checkbox" :disabled="phase === 'running'">
          <span><strong>{{ t('检索学术论文来源') }}</strong><small>{{ intent.source_ids.join(' · ') }}{{ t('，按已确认关键词查询') }}</small></span>
        </label>
        <label>
          <input
            v-model="includeExternalWeb"
            name="external-research"
            type="checkbox"
            :disabled="phase === 'running' || !supportsExternalResearch"
          >
          <span><strong>{{ t('同时查找外部知识资源') }}</strong><small>{{ t('官方文档、课程、教材与数据库；默认关闭') }}</small></span>
        </label>
        <p v-if="!supportsExternalResearch" class="boundary-note">{{ t('所选 Agent 不支持外部研究；学术论文检索仍可独立运行。') }}</p>
        <div v-if="includeExternalWeb" class="external-consent" data-test="external-consent">
          <PhGlobeHemisphereWest :size="16" aria-hidden="true" />
          <div>
            <strong>{{ t('Codex 将联网访问公共网站') }}</strong>
            <p>{{ t('只发送已确认的检索意图和最小论文上下文；结果链接还会经过安全核验。') }}</p>
            <label>
              <input v-model="externalWebConsent" type="checkbox" :disabled="phase === 'running'">
              {{ t('我同意本次外部研究') }}
            </label>
          </div>
        </div>
      </section>

      <div v-if="phase === 'draft' || phase === 'confirm'" class="related-actions">
        <button
          type="button"
          class="quiet"
          :disabled="confirming || starting"
          @click="store.reset"
        >{{ t('重新选择范围') }}</button>
        <button
          v-if="phase === 'draft'"
          type="button"
          data-test="confirm-related"
          :disabled="!canConfirm"
          @click="store.confirmIntent"
        >{{ confirming ? t('正在确认…') : t('确认检索意图') }}</button>
        <button
          v-else
          type="button"
          data-test="run-related"
          :disabled="!canRun || (includeExternalWeb && !agent)"
          @click="run"
        >{{ starting ? t('正在建立任务…') : t('运行推荐') }}</button>
      </div>
      <p v-if="phase === 'draft' && !canConfirm" class="validation-note">
        {{ t('请保留研究焦点、至少一个非空必需概念和一个学术来源，再确认检索意图。') }}
      </p>

      <section v-if="phase === 'running'" class="running-state" role="status">
        <span class="progress-track"><i :style="{ width: `${activeJob?.progress ?? 0}%` }" /></span>
        <div><strong>{{ t('正在查询已确认来源') }}</strong><small>{{ t('关闭页面不会丢失进度；失败来源可以恢复。') }}</small></div>
        <button type="button" :disabled="activeJob?.cancel_requested" @click="store.cancel">
          {{ activeJob?.cancel_requested ? t('已请求取消') : t('取消') }}
        </button>
      </section>

      <section v-if="phase === 'results' && resourceSet" class="related-results">
        <header>
          <div><p>{{ t('覆盖') }} {{ Math.round(resourceSet.coverage) }}%</p><h3>{{ t('关联研究结果') }}</h3></div>
          <button type="button" @click="store.refreshResults"><PhArrowClockwise :size="14" aria-hidden="true" /> {{ t('刷新') }}</button>
        </header>
        <div class="result-tabs" role="tablist" :aria-label="t('关联研究结果类型')">
          <button
            type="button"
            role="tab"
            data-tab="papers"
            :aria-selected="resultTab === 'papers'"
            @click="resultTab = 'papers'"
          >{{ t('相关论文') }} <span>{{ resourceSet.papers.length }}</span></button>
          <button
            type="button"
            role="tab"
            data-tab="knowledge"
            :aria-selected="resultTab === 'knowledge'"
            @click="resultTab = 'knowledge'"
          >{{ t('知识资源') }} <span>{{ resourceSet.knowledge_resources.length }}</span></button>
        </div>
        <RelatedPaperList
          v-if="resultTab === 'papers'"
          :papers="resourceSet.papers"
          :saving-id="savingId"
          @save="store.savePaper"
        />
        <KnowledgeResourceList v-else :resources="resourceSet.knowledge_resources" />
      </section>
    </template>

    <p v-if="errorMessage" class="task-message error" role="alert">{{ errorMessage }}</p>
    <p v-else-if="statusMessage" class="task-message" role="status">{{ statusMessage }}</p>
  </section>
</template>

<style scoped>
.related-task { flex: 1; display: flex; flex-direction: column; gap: 13px; padding: 14px 13px 18px; overflow: auto; }
.related-intro { display: grid; justify-items: start; padding: 24px 5px; }
.related-intro > svg { color: var(--color-forest); }
.related-intro .eyebrow { margin: 13px 0 0; color: var(--color-forest); font-size: var(--type-meta); font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
.related-intro h3 { margin: 5px 0 0; font-family: var(--font-editorial); font-size: 21px; font-weight: 500; }
.related-intro > p:not(.eyebrow) { max-width: 42ch; margin: 8px 0 0; color: var(--color-muted); font-size: var(--type-meta); line-height: 1.65; }
.intro-actions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 15px; }
.related-intro button, .related-actions button, .agent-refine > button { min-height: 34px; padding: 0 12px; border: 1px solid var(--color-forest); border-radius: var(--radius-sm); background: var(--color-forest); color: var(--color-paper-raised); font-size: var(--type-meta); font-weight: 700; cursor: pointer; }
.related-intro button.quiet { background: transparent; color: var(--color-forest); }
.related-intro small { margin-top: 7px; color: var(--color-muted); font-size: var(--type-meta); }
.confirmed-intent { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: start; gap: 9px; padding: 12px; border: 1px solid var(--color-forest); border-radius: var(--radius-md); background: var(--color-forest-soft); }
.confirmed-intent > svg { color: var(--color-forest); }
.confirmed-intent p, .confirmed-intent h3 { margin: 0; }
.confirmed-intent p { color: var(--color-forest); font-size: var(--type-meta); font-weight: 700; }
.confirmed-intent h3 { margin-top: 4px; font-size: 13px; line-height: 1.45; }
.confirmed-intent span { display: block; margin-top: 5px; color: var(--color-muted-strong); font-size: var(--type-meta); line-height: 1.5; }
.confirmed-intent button { border: 0; background: transparent; color: var(--color-forest); font-size: var(--type-meta); cursor: pointer; }
.agent-refine, .retrieval-boundaries { padding: 12px; border: 1px solid var(--color-line); border-radius: var(--radius-md); }
.agent-refine > div, .agent-refine > div span, .retrieval-boundaries > label, .retrieval-boundaries > label span, .external-consent, .running-state { display: flex; }
.agent-refine > div { align-items: flex-start; gap: 7px; color: var(--color-forest); }
.agent-refine > div span, .retrieval-boundaries > label span { align-items: flex-start; flex-direction: column; }
.agent-refine strong, .agent-refine small { display: block; }
.agent-refine strong { font-size: var(--type-meta); }
.agent-refine small, .retrieval-boundaries small { margin-top: 2px; color: var(--color-muted); font-size: var(--type-meta); }
.agent-refine textarea { width: 100%; box-sizing: border-box; margin-top: 9px; padding: 8px; resize: vertical; border: 1px solid var(--color-line); border-radius: var(--radius-sm); background: var(--color-paper-raised); color: var(--color-ink); font: inherit; font-size: var(--type-meta); }
.agent-refine > button { margin-top: 7px; background: transparent; color: var(--color-forest); }
.agent-refine > p, .boundary-note { margin: 6px 0 0; color: var(--color-muted); font-size: var(--type-meta); }
.boundary-title { display: flex; align-items: end; justify-content: space-between; margin-bottom: 8px; }
.boundary-title p, .boundary-title h3 { margin: 0; }
.boundary-title p { color: var(--color-forest); font-size: var(--type-meta); font-weight: 700; }
.boundary-title h3 { margin-top: 2px; font-size: 13px; }
.boundary-title > span { color: var(--color-muted); font-size: var(--type-meta); }
.retrieval-boundaries > label { align-items: flex-start; gap: 7px; padding: 8px 0; border-top: 1px solid var(--color-line); }
.retrieval-boundaries input { margin-top: 2px; accent-color: var(--color-forest); }
.retrieval-boundaries > label strong { font-size: var(--type-meta); }
.external-consent { align-items: flex-start; gap: 8px; margin-top: 5px; padding: 10px; border-left: 2px solid var(--color-wine); background: var(--color-wine-soft); color: var(--color-wine); }
.external-consent p { margin: 3px 0 7px; color: var(--color-muted-strong); font-size: var(--type-meta); line-height: 1.5; }
.external-consent label { color: var(--color-ink); font-size: var(--type-meta); font-weight: 700; }
.related-actions { display: flex; justify-content: flex-end; gap: 7px; }
.related-actions button { margin-top: 0; }
.related-actions .quiet { border-color: var(--color-line); background: transparent; color: var(--color-muted-strong); font-weight: 500; }
.running-state { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 8px; padding: 10px 0; }
.progress-track { grid-column: 1 / -1; height: 3px; overflow: hidden; border-radius: 3px; background: var(--color-line); }
.progress-track i { display: block; height: 100%; background: var(--color-forest); transition: width 180ms ease; }
.running-state div { display: flex; flex-direction: column; }
.running-state strong { font-size: var(--type-meta); }
.running-state small { margin-top: 2px; color: var(--color-muted); font-size: var(--type-meta); }
.running-state button, .related-results header button { display: inline-flex; align-items: center; gap: 4px; border: 0; background: transparent; color: var(--color-wine); font-size: var(--type-meta); cursor: pointer; }
.related-results header { display: flex; align-items: end; justify-content: space-between; }
.related-results header p, .related-results header h3 { margin: 0; }
.related-results header p { color: var(--color-forest); font-size: var(--type-meta); }
.related-results header h3 { margin-top: 3px; font-family: var(--font-editorial); font-size: 19px; font-weight: 500; }
.related-results header button { color: var(--color-forest); }
.result-tabs { display: grid; grid-template-columns: 1fr 1fr; margin: 12px 0 10px; border-bottom: 1px solid var(--color-line); }
.result-tabs button { min-height: 34px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--color-muted); font-size: var(--type-meta); cursor: pointer; }
.result-tabs button[aria-selected='true'] { border-bottom-color: var(--color-forest); color: var(--color-forest); font-weight: 700; }
.result-tabs span { margin-left: 4px; color: inherit; }
.task-message { margin: 0; padding: 8px 10px; background: var(--color-forest-soft); color: var(--color-forest); font-size: var(--type-meta); line-height: 1.45; }
.task-message.error { background: var(--color-wine-soft); color: var(--color-wine); }
.validation-note { margin: -6px 0 0; color: var(--color-wine); font-size: var(--type-meta); line-height: 1.45; text-align: right; }
button:focus-visible, textarea:focus-visible, input:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 1px; }
button:disabled, textarea:disabled, input:disabled { cursor: not-allowed; opacity: .55; }
@media (prefers-reduced-motion: reduce) { .progress-track i { transition: none; } }
</style>
