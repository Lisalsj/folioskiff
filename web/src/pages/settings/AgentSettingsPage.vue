<script setup lang="ts">
import { messageRef, settingsText as t, serverMessage } from '@/stores/locale'
import { computed, onMounted, reactive, ref, watch } from 'vue'

import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'
import { usePreferencesStore } from '@/stores/preferences'

import { useSettingsDirtyState } from './settings-dirty-state'

type AgentCapability = components['schemas']['AgentCapabilityResponse']
type AgentCredentialStatus = components['schemas']['AgentCredentialStatusResponse']
type PreferencesUpdate = components['schemas']['UserPreferencesUpdate']
type DefaultAgentBackend = PreferencesUpdate['default_agent_backend']
type DefaultReasoningEffort = PreferencesUpdate['default_reasoning_effort']
type ReportLanguage = PreferencesUpdate['report_language']
type TranslationLanguage = PreferencesUpdate['translation_source_language']

const preferences = usePreferencesStore()
const dirtyState = useSettingsDirtyState()
const agents = ref<AgentCapability[]>([])
const loading = ref(true)
const refreshing = ref(false)
const savingDefaults = ref(false)
const credentialBusy = ref(false)
const credentialInput = ref('')
const error = messageRef(t)
const notice = messageRef(t)
const draft = reactive({
  default_agent_backend: 'codex_cli' as DefaultAgentBackend,
  default_agent_model_id: '',
  default_reasoning_effort: 'medium' as DefaultReasoningEffort,
  report_language: 'zh-CN' as ReportLanguage,
  translation_source_language: 'en' as TranslationLanguage,
  translation_target_language: 'zh-CN' as TranslationLanguage,
})

const selectedAgent = computed(
  () => agents.value.find((agent) => agent.backend_id === draft.default_agent_backend),
)
const modelOptions = computed(() => selectedAgent.value?.models ?? [])
const preferencesDirty = computed(() => {
  const snapshot = preferences.snapshot
  if (!snapshot) return false
  return draft.default_agent_backend !== snapshot.default_agent_backend
    || (draft.default_agent_model_id.trim() || null) !== snapshot.default_agent_model_id
    || draft.default_reasoning_effort !== snapshot.default_reasoning_effort
    || draft.report_language !== snapshot.report_language
    || draft.translation_source_language !== snapshot.translation_source_language
    || draft.translation_target_language !== snapshot.translation_target_language
})

function statusLabel(code: string): string {
  const labels: Readonly<Record<string, string>> = {
    ready: t('可用'),
    not_installed: t('尚未安装'),
    not_authenticated: t('尚未登录'),
    configuration_invalid: t('配置无效'),
    version_unsupported: t('版本不受支持'),
    service_not_running: t('服务未启动'),
    probe_timeout: t('检测超时'),
    unavailable: t('暂时不可用'),
  }
  return labels[code] ?? t('状态未知')
}

function syncDraft(): void {
  const snapshot = preferences.snapshot
  if (!snapshot) return
  draft.default_agent_backend = snapshot.default_agent_backend
  draft.default_agent_model_id = snapshot.default_agent_model_id ?? ''
  draft.default_reasoning_effort = snapshot.default_reasoning_effort
  draft.report_language = snapshot.report_language
  draft.translation_source_language = snapshot.translation_source_language
  draft.translation_target_language = snapshot.translation_target_language
}

function onDefaultBackendChange(): void {
  // A model ID belongs to a backend. Require an intentional re-selection after
  // switching providers instead of carrying an Ollama ID into Codex (or vice
  // versa).
  draft.default_agent_model_id = ''
}

async function loadAgents(): Promise<void> {
  const response = await apiJson<unknown>('/api/v1/settings/agents')
  if (!Array.isArray(response)) throw new Error('invalid_agent_capability_response')
  agents.value = response as AgentCapability[]
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  const [agentResult, preferenceResult] = await Promise.allSettled([
    loadAgents(),
    preferences.load(),
  ])
  if (preferenceResult.status === 'fulfilled' && preferenceResult.value) syncDraft()
  if (
    agentResult.status === 'rejected'
    || preferenceResult.status === 'rejected'
    || preferenceResult.value === false
  ) error.value = '部分 Agent 设置暂时无法读取，请稍后重试。'
  loading.value = false
}

async function refreshAgents(): Promise<void> {
  if (refreshing.value) return
  refreshing.value = true
  error.value = ''
  notice.value = ''
  try {
    await loadAgents()
    notice.value = 'Agent 能力已重新检测；没有生成任何内容。'
  } catch {
    error.value = 'Agent 能力检测失败，请检查本机服务或网络后重试。'
  } finally {
    refreshing.value = false
  }
}

async function saveDefaults(): Promise<void> {
  const snapshot = preferences.snapshot
  if (!snapshot || savingDefaults.value) return
  error.value = ''
  notice.value = ''
  if (draft.translation_source_language === draft.translation_target_language) {
    error.value = '翻译源语言和目标语言不能相同。'
    return
  }
  savingDefaults.value = true
  const saved = await preferences.save({
    timezone_mode: snapshot.timezone_mode,
    timezone_name: snapshot.timezone_name,
    theme: snapshot.theme,
    default_agent_backend: draft.default_agent_backend,
    default_agent_model_id: draft.default_agent_model_id.trim() || null,
    default_reasoning_effort: draft.default_reasoning_effort,
    report_language: draft.report_language,
    translation_source_language: draft.translation_source_language,
    translation_target_language: draft.translation_target_language,
  })
  if (saved) {
    syncDraft()
    notice.value = 'Agent、报告与翻译默认值已保存。'
  } else {
    error.value = preferences.error || '默认值保存失败，请重试。'
  }
  savingDefaults.value = false
}

function acceptCredentialStatus(status: AgentCredentialStatus): void {
  agents.value = agents.value.map((agent) => agent.backend_id === status.backend_id
    ? { ...agent, credential_configured: status.configured }
    : agent)
}

async function saveCloudCredential(): Promise<void> {
  const value = credentialInput.value.trim()
  if (!value || credentialBusy.value) return
  credentialBusy.value = true
  error.value = ''
  notice.value = ''
  try {
    acceptCredentialStatus(await apiJson<AgentCredentialStatus>(
      '/api/v1/settings/agents/ollama-cloud/credential',
      { method: 'PUT', body: JSON.stringify({ value }) },
    ))
    notice.value = 'Ollama Cloud 凭据已保存到 Windows 凭据库；请重新检测能力。'
  } catch {
    error.value = 'Ollama Cloud 凭据保存失败，请检查后重试。'
  } finally {
    credentialInput.value = ''
    credentialBusy.value = false
  }
}

async function deleteCloudCredential(): Promise<void> {
  if (credentialBusy.value) return
  credentialBusy.value = true
  error.value = ''
  notice.value = ''
  try {
    acceptCredentialStatus(await apiJson<AgentCredentialStatus>(
      '/api/v1/settings/agents/ollama-cloud/credential',
      { method: 'DELETE' },
    ))
    notice.value = 'Ollama Cloud 凭据已从本机移除。'
  } catch {
    error.value = 'Ollama Cloud 凭据移除失败，请稍后重试。'
  } finally {
    credentialInput.value = ''
    credentialBusy.value = false
  }
}

watch(
  () => [preferencesDirty.value, credentialInput.value] as const,
  ([formDirty, credential]) => dirtyState.setDirty(formDirty || Boolean(credential.trim())),
)
onMounted(() => void load())
</script>

<template>
  <section class="settings-section" aria-labelledby="agent-settings-title">
    <header class="section-heading">
      <div>
        <h2 id="agent-settings-title" data-settings-heading tabindex="-1">{{ t('AI 与模型') }}</h2>
      </div>
      <button type="button" :disabled="refreshing" @click="refreshAgents">
        {{ refreshing ? t('正在检测') : t('重新检测') }}
      </button>
    </header>
    <p class="section-intro">{{ t('分析、翻译和论文对话只在你主动操作时调用模型。能力检测不会生成内容。') }}</p>

    <p v-if="error" role="alert" class="section-error">{{ error }}</p>
    <p v-if="notice" role="status" class="section-notice">{{ notice }}</p>
    <p v-if="loading" role="status" class="section-state">{{ t('正在读取 Agent 与阅读默认值') }}</p>

    <template v-else>
      <section class="defaults" aria-labelledby="agent-defaults-title">
        <div class="subheading">
          <div>
            <h3 id="agent-defaults-title">{{ t('分析、报告与翻译') }}</h3>
          </div>
          <span>{{ t('英文 → 中文') }}</span>
        </div>
        <form data-test="agent-defaults-form" @submit.prevent="saveDefaults">
          <div class="defaults-grid">
            <label>
              <span>{{ t('默认 Agent') }}</span>
              <select v-model="draft.default_agent_backend" data-test="default-agent-backend" @change="onDefaultBackendChange">
                <option v-for="agent in agents" :key="agent.backend_id" :value="agent.backend_id">
                  {{ agent.display_name }}{{ agent.location === 'cloud' ? t('（云端）') : t('（本机）') }}
                </option>
              </select>
            </label>
            <label class="model-default-field">
              <span>{{ t('默认模型（可选）') }}</span>
              <select
                v-model="draft.default_agent_model_id"
                data-test="default-agent-model-select"
                @change="draft.default_agent_model_id = ($event.target as HTMLSelectElement).value"
              >
                <option value="">{{ t('后端默认 / 自动选择') }}</option>
                <option v-for="model in modelOptions" :key="model.model_id" :value="model.model_id">
                  {{ model.display_name }}{{ model.context_window ? t(' · 上下文 {0}', model.context_window.toLocaleString()) : '' }}
                </option>
              </select>
              <input
                v-model="draft.default_agent_model_id"
                data-test="default-agent-model"
                autocomplete="off"
                spellcheck="false"
                :placeholder="t('也可输入自定义模型 ID')"
              />
              <small>{{ t('选择列表中的模型，或输入后端支持的自定义 ID。') }}</small>
            </label>
            <label>
              <span>{{ t('默认推理强度') }}</span>
              <select v-model="draft.default_reasoning_effort" data-test="default-reasoning-effort">
                <option value="minimal">{{ t('最小') }}</option>
                <option value="low">{{ t('低') }}</option>
                <option value="medium">{{ t('中') }}</option>
                <option value="high">{{ t('高') }}</option>
                <option value="xhigh">{{ t('极高') }}</option>
              </select>
            </label>
            <label>
              <span>{{ t('报告语言') }}</span>
              <select v-model="draft.report_language" data-test="default-report-language">
                <option value="zh-CN">{{ t('中文') }}</option>
                <option value="en">{{ t('英文') }}</option>
                <option value="bilingual">{{ t('中英双语') }}</option>
              </select>
            </label>
            <label>
              <span>{{ t('翻译源语言') }}</span>
              <select v-model="draft.translation_source_language" data-test="translation-source-language">
                <option value="en">{{ t('英文') }}</option>
                <option value="zh-CN">{{ t('中文') }}</option>
              </select>
            </label>
            <label>
              <span>{{ t('翻译目标语言') }}</span>
              <select v-model="draft.translation_target_language" data-test="translation-target-language">
                <option value="zh-CN">{{ t('中文') }}</option>
                <option value="en">{{ t('英文') }}</option>
              </select>
            </label>
          </div>
          <div class="defaults-footer">
            <p>{{ t('单篇分析前仍可覆盖这些值；保存默认值不会调用模型。') }}</p>
            <button type="submit" data-test="save-agent-defaults" :disabled="savingDefaults || !preferencesDirty">
              {{ savingDefaults ? t('正在保存') : t('保存默认值') }}
            </button>
          </div>
        </form>
      </section>

      <section aria-labelledby="agent-capabilities-title">
        <div class="subheading">
          <div>
            <h3 id="agent-capabilities-title">{{ t('已发现的后端') }}</h3>
          </div>
          <span>{{ agents.length }} {{ t('个') }}</span>
        </div>
        <div v-if="agents.length" class="agent-list">
          <article v-for="agent in agents" :id="agent.backend_id" :key="agent.backend_id">
            <div class="agent-title">
              <div>
                <h4>{{ agent.display_name }}</h4>
                <code>{{ agent.probe_code }}</code>
                <span v-if="agent.version">{{ t('版本') }} {{ agent.version }}</span>
              </div>
              <span class="location-badge" :class="{ cloud: agent.location === 'cloud' }">
                {{ agent.location === 'cloud' ? t('云端') : t('本机') }}
              </span>
              <strong :class="{ warning: !agent.available }">{{ statusLabel(agent.probe_code) }}</strong>
            </div>
            <p>{{ agent.location === 'cloud' ? t('所选论文证据可能发送到外部模型服务。') : t('模型与论文证据在本机处理。') }}</p>
            <p v-if="agent.backend_id === 'codex_cli' && agent.desktop_app_running === true" class="desktop-status">
              {{ t('ChatGPT 桌面应用正在运行；可生成状态取决于 Codex CLI 的版本与登录验证。') }}
            </p>
            <p v-else-if="agent.backend_id === 'codex_cli' && agent.desktop_app_running === false" class="desktop-status muted">
              {{ t('未检测到 ChatGPT 桌面进程；请确认桌面应用或直接安装的 Codex CLI 状态。') }}
            </p>
            <p v-if="agent.recommended_action" class="agent-action">{{ serverMessage(agent.recommended_action) }}</p>

            <form
              v-if="agent.backend_id === 'ollama_cloud'"
              class="credential-form"
              @submit.prevent="saveCloudCredential"
            >
              <div>
                <strong>{{ agent.credential_configured ? t('凭据已配置') : t('尚未配置凭据') }}</strong>
                <small>{{ t('密钥只进入 Windows 凭据库，页面不会回显。') }}</small>
              </div>
              <label>
                <span>Ollama Cloud API key</span>
                <input
                  v-model="credentialInput"
                  data-test="ollama-cloud-key"
                  type="password"
                  autocomplete="new-password"
                  spellcheck="false"
                  :placeholder="t('输入新密钥')"
                />
              </label>
              <div class="credential-actions">
                <button
                  type="submit"
                  data-test="save-ollama-cloud"
                  :disabled="credentialBusy || !credentialInput.trim()"
                  @click.prevent="saveCloudCredential"
                >
                  {{ credentialBusy ? t('处理中') : agent.credential_configured ? t('替换') : t('保存') }}
                </button>
                <button
                  v-if="agent.credential_configured"
                  type="button"
                  class="quiet-button"
                  data-test="delete-ollama-cloud"
                  :disabled="credentialBusy"
                  @click="deleteCloudCredential"
                >
                  {{ t('移除') }}
                </button>
              </div>
            </form>
          </article>
        </div>
        <p v-else class="section-state">{{ t('没有发现可配置的 Agent，论文发现与阅读仍可使用。') }}</p>
      </section>
    </template>
  </section>
</template>

<style scoped>
.settings-section { display: grid; gap: 28px; }
.section-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; padding-bottom: 15px; border-bottom: 2px solid var(--color-forest); }
.section-heading p, .subheading p { margin: 0 0 7px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: .12em; }
.section-heading h2 { margin: 0; font-family: var(--font-editorial); font-size: 30px; font-weight: 500; }
.section-heading h2:focus { outline: none; }
.section-heading button, .defaults-footer button, .credential-actions button { min-height: 40px; padding: 0 13px; border: 1px solid var(--color-line-strong); border-radius: var(--radius-sm); background: var(--color-paper-raised); color: var(--color-ink); font: inherit; font-size: 12px; font-weight: 700; cursor: pointer; }
.section-heading button:disabled, .defaults-footer button:disabled, .credential-actions button:disabled { opacity: .55; cursor: not-allowed; }
.section-intro, .section-state { margin: 0; color: var(--color-muted-strong); line-height: 1.7; }
.section-error, .section-notice { margin: -8px 0; padding-left: 14px; border-left: 3px solid currentColor; }
.section-error, .agent-action { color: var(--color-wine); }
.desktop-status { color: var(--color-forest) !important; }
.desktop-status.muted { color: var(--color-muted) !important; }
.section-notice { color: var(--color-forest); }
.defaults { border-bottom: 1px solid var(--color-line); }
.subheading { display: flex; align-items: end; justify-content: space-between; gap: 18px; padding-bottom: 13px; border-bottom: 1px solid var(--color-line); }
.subheading h3 { margin: 0; font-family: var(--font-editorial); font-size: 23px; font-weight: 500; }
.subheading > span { color: var(--color-muted); font-size: var(--type-meta); }
.defaults form { padding: 19px 0 24px; }
.defaults-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 22px; }
.defaults-grid label, .credential-form label { display: grid; gap: 7px; color: var(--color-muted-strong); font-size: var(--type-meta); font-weight: 700; }
.defaults-grid input, .defaults-grid select, .credential-form input { width: 100%; min-height: 41px; padding: 0 11px; border: 1px solid var(--color-line-strong); border-radius: var(--radius-sm); background: var(--color-paper-raised); color: var(--color-ink); font: inherit; }
.model-default-field { gap: 7px; }
.model-default-field small { color: var(--color-muted); font-size: var(--type-meta); font-weight: 400; line-height: 1.5; }
.defaults-footer { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--color-line); }
.defaults-footer p { margin: 0; color: var(--color-muted); font-size: var(--type-meta); line-height: 1.55; }
.defaults-footer button, .credential-actions button:first-child { border-color: var(--color-forest); background: var(--color-forest); color: var(--color-on-accent); }
.agent-list { display: grid; }
.agent-list article { padding: 20px 0; border-bottom: 1px solid var(--color-line); }
.agent-title { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 12px; align-items: start; }
.agent-list h4 { margin: 0; font-family: var(--font-editorial); font-size: 20px; font-weight: 500; }
.agent-list code, .agent-list .agent-title div > span { display: inline-block; margin: 5px 8px 0 0; color: var(--color-muted); font-size: var(--type-meta); }
.agent-title > strong { color: var(--color-forest); font-size: 12px; }
.agent-title > strong.warning { color: var(--color-wine); }
.location-badge { padding: 3px 7px; border: 1px solid var(--color-line); border-radius: var(--radius-sm); color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; }
.location-badge.cloud { border-color: color-mix(in srgb, var(--color-wine) 34%, var(--color-line)); color: var(--color-wine); }
.agent-list article > p { margin: 9px 0 0; color: var(--color-muted-strong); line-height: 1.55; }
.credential-form { display: grid; grid-template-columns: minmax(160px, .7fr) minmax(220px, 1fr) auto; gap: 18px; align-items: end; margin-top: 18px; padding: 15px; border: 1px solid var(--color-line); background: var(--color-paper-deep); }
.credential-form > div:first-child { align-self: center; }
.credential-form > div > strong, .credential-form > div > small { display: block; }
.credential-form > div > small { margin-top: 4px; color: var(--color-muted); line-height: 1.5; }
.credential-actions { display: flex; gap: 7px; }
.credential-actions .quiet-button { background: transparent; }
button:focus-visible, input:focus-visible, select:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }

@media (max-width: 720px) {
  .defaults-grid { grid-template-columns: 1fr; }
  .defaults-footer { align-items: stretch; flex-direction: column; }
  .defaults-footer button { width: 100%; }
  .agent-title { grid-template-columns: minmax(0, 1fr) auto; }
  .agent-title > strong { grid-column: 1 / -1; }
  .credential-form { grid-template-columns: 1fr; align-items: stretch; }
}
</style>
