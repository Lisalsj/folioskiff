<script setup lang="ts">
import { researchText as t } from '@/stores/locale'
import { computed, ref, watch } from 'vue'
import { PhArrowClockwise, PhCloud, PhDesktop, PhShieldCheck, PhX } from '@phosphor-icons/vue'
import {
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from 'reka-ui'

import type { components } from '@/api/schema'

type Capability = components['schemas']['AgentCapabilityResponse']
type Preflight = components['schemas']['AnalysisPreflight']
type Selection = components['schemas']['AnalysisSelection']

const props = defineProps<{
  open: boolean
  preflight: Preflight | null
  selection: Selection
  capabilities: Capability[]
  loading: boolean
  errorMessage: string
  submitting: boolean
}>()

const emit = defineEmits<{
  close: []
  preview: [selection: Selection]
  confirm: [selection: Selection]
}>()

const draft = ref<Selection>(copySelection(props.selection))
const selectedCapability = computed(() => props.capabilities.find(
  (item) => item.backend_id === draft.value.backend_id,
))
const dirty = computed(() => selectionKey(draft.value) !== selectionKey(props.selection))
const canStart = computed(() => Boolean(
  props.preflight?.ready
  && !props.loading
  && !props.submitting
  && !props.errorMessage
  && !dirty.value,
))

watch(
  () => [props.open, props.selection] as const,
  ([open]) => {
    if (open) draft.value = copySelection(props.selection)
  },
  { deep: true },
)

function copySelection(value: Selection): Selection {
  return JSON.parse(JSON.stringify(value)) as Selection
}

function selectionKey(value: Selection): string {
  return JSON.stringify(value)
}

function updateBackend(event: Event): void {
  const backendId = (event.target as HTMLSelectElement).value
  const capability = props.capabilities.find((item) => item.backend_id === backendId)
  draft.value.backend_id = backendId
  draft.value.model_id = null
  if (!capability?.supports_external_research) draft.value.external_research = false
}

function updateModel(event: Event): void {
  const value = (event.target as HTMLInputElement | HTMLSelectElement).value.trim()
  draft.value.model_id = value || null
}

function updateTemporaryInstruction(event: Event): void {
  const value = (event.target as HTMLTextAreaElement).value
  draft.value.temporary_instruction = value.trim() ? value : null
}

function scopeLabel(value: string): string {
  const [kind, count] = value.split(':')
  if (kind === 'pages') return t('{0} 页', count ?? 0)
  if (kind === 'annotations') return t('{0} 条批注', count ?? 0)
  if (kind === 'evidence') return count === 'full_text' ? t('全文范围') : t('摘要范围')
  if (kind === 'visual-pages') return t('{0} 页视觉证据', count ?? 0)
  return value
}

function warningLabel(value: string): string {
  const labels: Record<string, string> = {
    abstract_only: t('本次只能使用摘要，报告不会声称阅读过全文。'),
    model_required: t('请选择或填写一个模型 ID。'),
    model_unavailable: t('所选模型当前不可用。'),
    manual_model_id: t('该模型 ID 将由后端在启动时再次验证。'),
    model_discovery_failed: t('模型目录探测失败，仍可手工填写模型 ID。'),
    backend_not_installed: t('所选后端尚未安装。'),
    backend_not_authenticated: t('所选后端尚未完成认证。'),
    backend_unavailable: t('所选后端当前不可用。'),
    external_research_unsupported: t('所选后端不支持外部网络研究。'),
    evidence_truncated: t('证据超过单次预算，预检已明确截断。'),
  }
  return labels[value] ?? value.replaceAll('_', ' ')
}

function languageLabel(value: string): string {
  if (value === 'en') return t('英文')
  if (value === 'bilingual') return t('中英双语')
  return t('中文')
}

function submitPreview(): void {
  emit('preview', copySelection(draft.value))
}

function submitAnalysis(): void {
  if (canStart.value) emit('confirm', copySelection(draft.value))
}
</script>

<template>
  <DialogRoot :open="open" @update:open="(value) => { if (!value) emit('close') }">
    <DialogPortal>
      <DialogOverlay class="preflight-overlay" />
      <DialogContent
        class="preflight-dialog"
        data-test="analysis-preflight-dialog"
        @escape-key-down="emit('close')"
      >
        <header class="preflight-heading">
          <div>
            <span>{{ t('按需生成') }}</span>
            <DialogTitle>{{ t('分析前预检') }}</DialogTitle>
            <DialogDescription>
              {{ t('确认这一次会交给 Agent 的论文证据、模型与范围。关闭不会创建任务。') }}
            </DialogDescription>
          </div>
          <DialogClose as-child>
            <button type="button" :aria-label="t('关闭分析预检')">
              <PhX :size="18" aria-hidden="true" />
            </button>
          </DialogClose>
        </header>

        <div v-if="loading && !preflight" class="preflight-state" role="status">
          <PhArrowClockwise :size="19" class="spinning" aria-hidden="true" />
          {{ t('正在核对后端、模型与当前论文证据…') }}
        </div>

        <form v-else class="preflight-form" @submit.prevent="submitAnalysis">
          <section class="execution-fields" aria-labelledby="execution-title">
            <div class="section-heading">
              <span>01</span>
              <h3 id="execution-title">{{ t('执行方式') }}</h3>
            </div>

            <label>
              <span>{{ t('Agent 后端') }}</span>
              <select
                :value="draft.backend_id ?? ''"
                data-test="analysis-backend"
                @change="updateBackend"
              >
                <option
                  v-for="capability in capabilities"
                  :key="capability.backend_id"
                  :value="capability.backend_id"
                >
                  {{ capability.display_name }} · {{ capability.location === 'cloud' ? t('云端') : t('本地') }}
                </option>
              </select>
            </label>

            <div
              v-if="selectedCapability"
              class="backend-location"
              :class="selectedCapability.location"
            >
              <PhCloud
                v-if="selectedCapability.location === 'cloud'"
                :size="16"
                aria-hidden="true"
              />
              <PhDesktop v-else :size="16" aria-hidden="true" />
              <span>
                {{ selectedCapability.location === 'cloud' ? t('云端后端') : t('本地后端') }}
              </span>
              <small>
                {{ selectedCapability.location === 'cloud'
                  ? t('论文证据将发送给所选云端服务。')
                  : t('论文证据留在本机后端。') }}
              </small>
            </div>

            <label>
              <span>{{ t('模型 ID') }}</span>
              <input
                v-if="selectedCapability?.supports_manual_model_id !== false"
                :value="draft.model_id ?? ''"
                data-test="analysis-model"
                :list="selectedCapability?.models.length ? 'analysis-model-options' : undefined"
                :placeholder="t('使用后端默认模型，或填写模型 ID')"
                autocomplete="off"
                @input="updateModel"
              >
              <select
                v-else
                :value="draft.model_id ?? ''"
                data-test="analysis-model"
                @change="updateModel"
              >
                <option value="">{{ t('选择模型') }}</option>
                <option
                  v-for="model in selectedCapability?.models ?? []"
                  :key="model.model_id"
                  :value="model.model_id"
                >
                  {{ model.display_name }}
                </option>
              </select>
              <datalist id="analysis-model-options">
                <option
                  v-for="model in selectedCapability?.models ?? []"
                  :key="model.model_id"
                  :value="model.model_id"
                >{{ model.display_name }}</option>
              </datalist>
            </label>

            <div class="paired-fields">
              <label>
                <span>{{ t('推理强度') }}</span>
                <select v-model="draft.reasoning_effort" data-test="analysis-reasoning">
                  <option value="minimal">{{ t('最简') }}</option>
                  <option value="low">{{ t('低') }}</option>
                  <option value="medium">{{ t('中') }}</option>
                  <option value="high">{{ t('高') }}</option>
                  <option value="xhigh">{{ t('最高') }}</option>
                </select>
              </label>
              <label>
                <span>{{ t('报告语言') }}</span>
                <select v-model="draft.language" data-test="analysis-language">
                  <option value="zh-CN">{{ t('中文') }}</option>
                  <option value="en">{{ t('英文') }}</option>
                  <option value="bilingual">{{ t('中英双语') }}</option>
                </select>
              </label>
            </div>
          </section>

          <section class="evidence-contract" aria-labelledby="evidence-title">
            <div class="section-heading">
              <span>02</span>
              <h3 id="evidence-title">{{ t('证据与范围') }}</h3>
            </div>

            <div v-if="preflight" class="evidence-summary">
              <div>
                <PhShieldCheck :size="18" aria-hidden="true" />
                <span>
                  {{ preflight.evidence_level === 'full_text' ? t('全文证据') : t('仅摘要证据') }}
                </span>
                <strong>{{ preflight.coverage }}%</strong>
              </div>
              <p>{{ t('文档版本') }} {{ preflight.document_content_sha256?.slice(0, 10) ?? t('仅元数据') }}</p>
              <ul :aria-label="t('分析证据范围')">
                <li v-for="scope in preflight.scope" :key="scope">{{ scopeLabel(scope) }}</li>
              </ul>
            </div>

            <dl v-if="preflight" class="preflight-facts">
              <div>
                <dt>{{ t('提示词模板') }}</dt>
                <dd>{{ preflight.template_name }} · {{ preflight.template_version_id }}</dd>
              </div>
              <div>
                <dt>{{ t('语言') }}</dt>
                <dd>{{ languageLabel(preflight.language) }}</dd>
              </div>
              <div>
                <dt>{{ t('联网范围') }}</dt>
                <dd>{{ draft.external_research ? t('论文证据 + 外部研究') : t('仅论文与本地证据') }}</dd>
              </div>
            </dl>

            <label class="network-field">
              <input
                v-model="draft.include_visual_evidence"
                type="checkbox"
                data-test="analysis-visual-evidence"
              >
              <span>
                {{ t('按需附加关键图表页') }}
                <small>{{ t('仅在模型明确支持视觉且文本不足时发送；最多 6 页，可随时关闭。') }}</small>
              </span>
            </label>

            <label class="network-field">
              <input
                v-model="draft.external_research"
                type="checkbox"
                data-test="analysis-network"
                :disabled="!selectedCapability?.supports_external_research"
              >
              <span>
                {{ t('允许外部网络研究') }}
                <small>{{ t('默认关闭；外部主张必须单独引用。') }}</small>
              </span>
            </label>

            <label>
              <span>{{ t('本次临时指令（可选）') }}</span>
              <textarea
                :value="draft.temporary_instruction ?? ''"
                data-test="analysis-temporary-instruction"
                rows="3"
                maxlength="20000"
                :placeholder="t('例如：重点检查实验公平性与消融设置。')"
                @input="updateTemporaryInstruction"
              />
            </label>
          </section>

          <div v-if="preflight?.warnings.length" class="preflight-warnings" role="status">
            <strong>{{ t('预检提醒') }}</strong>
            <ul>
              <li v-for="warning in preflight.warnings" :key="warning">
                {{ warningLabel(warning) }}
              </li>
            </ul>
          </div>
          <p v-if="errorMessage" class="preflight-error" role="alert">{{ errorMessage }}</p>
          <p v-else-if="dirty" class="preflight-dirty" role="status">
            {{ t('选项已改变，请先更新预检；这一步仍不会调用模型。') }}
          </p>

          <footer>
            <button
              type="button"
              class="secondary-action"
              data-test="refresh-preflight"
              :disabled="loading || submitting"
              @click="submitPreview"
            >
              <PhArrowClockwise :size="16" :class="{ spinning: loading }" aria-hidden="true" />
              {{ loading ? t('正在预检') : t('更新预检') }}
            </button>
            <button
              type="submit"
              class="primary-action"
              data-test="start-analysis"
              :disabled="!canStart"
            >
              {{ submitting ? t('正在创建任务…') : t('开始分析') }}
            </button>
          </footer>
        </form>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style scoped>
.preflight-overlay {
  position: fixed;
  z-index: 30;
  inset: 0;
  background: rgb(20 28 23 / 54%);
}

.preflight-dialog {
  position: fixed;
  z-index: 31;
  top: 50%;
  left: 50%;
  width: min(760px, calc(100vw - 28px));
  max-height: min(88dvh, 840px);
  overflow: auto;
  transform: translate(-50%, -50%);
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-paper);
  box-shadow: 0 28px 80px rgb(17 27 20 / 26%);
  color: var(--color-ink);
}

.preflight-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding: 24px 26px 20px;
  border-bottom: 1px solid var(--color-line);
}

.preflight-heading > div > span {
  color: var(--color-wine);
  font-size: var(--type-meta);
  font-weight: 750;
  letter-spacing: 0.13em;
}

.preflight-heading h2 {
  margin: 8px 0 5px;
  font-family: var(--font-editorial);
  font-size: 27px;
  font-weight: 500;
}

.preflight-heading p {
  max-width: 560px;
  margin: 0;
  color: var(--color-muted-strong);
  font-size: 12px;
  line-height: 1.65;
}

.preflight-heading button {
  width: 36px;
  height: 36px;
  display: grid;
  flex: none;
  place-items: center;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted-strong);
  cursor: pointer;
}

.preflight-form {
  padding: 0 26px 24px;
}

.execution-fields,
.evidence-contract {
  padding: 22px 0;
  border-bottom: 1px solid var(--color-line);
}

.section-heading {
  display: flex;
  align-items: baseline;
  gap: 9px;
  margin-bottom: 17px;
}

.section-heading span {
  color: var(--color-wine);
  font-size: var(--type-meta);
  font-variant-numeric: tabular-nums;
}

.section-heading h3 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: 18px;
  font-weight: 500;
}

.preflight-form label {
  display: grid;
  gap: 7px;
  margin-top: 13px;
}

.preflight-form label > span,
.preflight-facts dt {
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  font-weight: 650;
}

.preflight-form input:not([type='checkbox']),
.preflight-form select,
.preflight-form textarea {
  width: 100%;
  min-height: 40px;
  padding: 8px 10px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-deep);
  color: var(--color-ink);
  font-size: 12px;
}

.preflight-form textarea {
  min-height: 76px;
  resize: vertical;
  line-height: 1.55;
}

.paired-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.backend-location {
  min-height: 42px;
  display: grid;
  grid-template-columns: auto auto minmax(0, 1fr);
  align-items: center;
  gap: 7px;
  margin-top: 10px;
  padding: 9px 11px;
  border-left: 3px solid var(--color-forest);
  background: var(--color-forest-soft);
  color: var(--color-forest);
  font-size: var(--type-meta);
}

.backend-location.cloud {
  border-left-color: var(--color-wine);
  background: var(--color-wine-soft);
  color: var(--color-wine);
}

.backend-location small {
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
}

.evidence-summary {
  padding: 13px;
  border: 1px solid var(--color-line);
  border-left: 3px solid var(--color-forest);
  background: var(--color-paper-deep);
}

.evidence-summary > div {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--color-forest);
}

.evidence-summary strong {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}

.evidence-summary p {
  margin: 7px 0 0;
  color: var(--color-muted);
  font-family: ui-monospace, 'Cascadia Code', monospace;
  font-size: var(--type-meta);
}

.evidence-summary ul {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 11px 0 0;
  padding: 0;
  list-style: none;
}

.evidence-summary li {
  padding: 3px 7px;
  border: 1px solid var(--color-line);
  border-radius: 999px;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
}

.preflight-facts {
  display: grid;
  grid-template-columns: 2fr 1fr 1.5fr;
  gap: 12px;
  margin: 15px 0 0;
}

.preflight-facts div {
  min-width: 0;
}

.preflight-facts dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
  color: var(--color-ink);
  font-size: var(--type-meta);
}

.network-field {
  grid-template-columns: auto minmax(0, 1fr);
  align-items: start;
}

.network-field input {
  width: 17px;
  height: 17px;
  margin: 1px 0 0;
  accent-color: var(--color-forest);
}

.network-field span,
.network-field small {
  display: block;
}

.network-field small {
  margin-top: 3px;
  color: var(--color-muted);
  font-size: var(--type-meta);
  font-weight: 400;
}

.preflight-warnings,
.preflight-error,
.preflight-dirty,
.preflight-state {
  margin: 16px 0 0;
  padding: 11px 12px;
  border-left: 3px solid var(--color-wine);
  background: var(--color-wine-soft);
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  line-height: 1.55;
}

.preflight-warnings ul {
  margin: 6px 0 0;
  padding-left: 18px;
}

.preflight-dirty,
.preflight-state {
  border-left-color: var(--color-forest);
  background: var(--color-forest-soft);
}

.preflight-state {
  display: flex;
  align-items: center;
  gap: 9px;
  margin: 24px 26px;
}

.preflight-form footer {
  display: flex;
  justify-content: flex-end;
  gap: 9px;
  padding-top: 20px;
}

.preflight-form footer button {
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 0 15px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.secondary-action {
  border: 1px solid var(--color-line);
  background: var(--color-paper-deep);
  color: var(--color-forest);
}

.primary-action {
  border: 1px solid var(--color-forest);
  background: var(--color-forest);
  color: var(--color-on-accent);
}

.preflight-form footer button:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

.preflight-dialog button:focus-visible,
.preflight-dialog input:focus-visible,
.preflight-dialog select:focus-visible,
.preflight-dialog textarea:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

.spinning {
  animation: preflight-spin 900ms linear infinite;
}

@keyframes preflight-spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 620px) {
  .preflight-dialog {
    width: 100vw;
    max-height: 94dvh;
    top: auto;
    bottom: 0;
    left: 0;
    transform: none;
    border-radius: 12px 12px 0 0;
  }

  .preflight-heading,
  .preflight-form {
    padding-right: 18px;
    padding-left: 18px;
  }

  .paired-fields,
  .preflight-facts {
    grid-template-columns: 1fr;
  }

  .backend-location {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .backend-location small {
    grid-column: 1 / -1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .spinning { animation: none; }
}
</style>
