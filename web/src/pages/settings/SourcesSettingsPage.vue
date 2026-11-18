<script setup lang="ts">
import { messageRef, messageTemplate, settingsText as t, serverMessage } from '@/stores/locale'
import { onMounted, reactive, ref } from 'vue'
import {
  PhArrowSquareOut,
  PhCheckCircle,
  PhKey,
  PhTrash,
} from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'

import { useSettingsDirtyState } from './settings-dirty-state'

type SourceCapability = components['schemas']['SourceCapabilityResponse']
type EnricherCapability = components['schemas']['EnricherCapabilityResponse']
type EnricherCredential = components['schemas']['EnricherCredentialResponse']

const sources = ref<SourceCapability[]>([])
const enrichers = ref<EnricherCapability[]>([])
const credentialInputs = reactive<Record<string, string>>({})
const busySource = ref<string>()
const busyCredential = ref<string>()
const loading = ref(true)
const error = messageRef(t)
const notice = messageRef(t)
const dirtyState = useSettingsDirtyState()

function syncDirty(): void {
  dirtyState.setDirty(Object.values(credentialInputs).some((value) => Boolean(value?.trim())))
}

function replaceSource(updated: SourceCapability): void {
  sources.value = sources.value.map((source) =>
    source.connector_id === updated.connector_id ? updated : source,
  )
}

function replaceEnricher(updated: EnricherCapability): void {
  enrichers.value = enrichers.value.map((enricher) =>
    enricher.enricher_id === updated.enricher_id ? updated : enricher,
  )
}

function credentialKey(enricher: EnricherCapability, credential: EnricherCredential): string {
  return `${enricher.enricher_id}:${credential.credential_id}`
}

function enricherPurpose(enricherId: string): string {
  if (enricherId === 'openalex') {
    return t('主要质量证据：归一化引用、FWCI、撤稿、作者机构与研究主题。')
  }
  if (enricherId === 'semantic_scholar') {
    return t('交叉验证引用与高影响引用；未配置 key 时仍可使用共享公开额度。')
  }
  return t('核验 DOI 书目、出版场所、许可证、基金与版本更新关系。')
}

function throughput(enricher: EnricherCapability): string {
  return enricher.default_requests_per_second == null
    ? t('额度由服务端动态返回')
    : t('{0} 次/秒 · 最多并发 {1}', enricher.default_requests_per_second, enricher.max_concurrency)
}

function dateSemantics(source: SourceCapability): string {
  return source.supports_exact_updated_date
    ? t('支持按最近更新日期精确筛选')
    : t('来源接口不提供完整精确更新日；本地会再次按月份严格过滤')
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [sourceItems, enricherItems] = await Promise.all([
      apiJson<SourceCapability[]>('/api/v1/settings/sources'),
      apiJson<EnricherCapability[]>('/api/v1/settings/enrichers'),
    ])
    sources.value = sourceItems
    enrichers.value = enricherItems
  } catch {
    error.value = '暂时无法读取论文来源与质量证据服务，请稍后重试。'
  } finally {
    loading.value = false
  }
}

async function saveSourceCredential(source: SourceCapability): Promise<void> {
  const value = credentialInputs[source.connector_id]?.trim()
  if (!value || busySource.value) return
  busySource.value = source.connector_id
  error.value = ''
  notice.value = ''
  try {
    replaceSource(await apiJson<SourceCapability>(
      `/api/v1/settings/sources/${encodeURIComponent(source.connector_id)}/credential`,
      { method: 'PUT', body: JSON.stringify({ value }) },
    ))
    notice.value = messageTemplate('{0} 的密钥已保存到 Windows 凭据库。', source.display_name)
  } catch {
    error.value = messageTemplate('无法保存 {0} 密钥，请检查后重试。', source.display_name)
  } finally {
    credentialInputs[source.connector_id] = ''
    syncDirty()
    busySource.value = undefined
  }
}

async function deleteSourceCredential(source: SourceCapability): Promise<void> {
  if (busySource.value) return
  busySource.value = source.connector_id
  error.value = ''
  notice.value = ''
  try {
    replaceSource(await apiJson<SourceCapability>(
      `/api/v1/settings/sources/${encodeURIComponent(source.connector_id)}/credential`,
      { method: 'DELETE' },
    ))
    notice.value = messageTemplate('{0} 的本地密钥已移除。', source.display_name)
  } catch {
    error.value = messageTemplate('无法移除 {0} 密钥，请稍后重试。', source.display_name)
  } finally {
    credentialInputs[source.connector_id] = ''
    syncDirty()
    busySource.value = undefined
  }
}

async function saveEnricherCredential(
  enricher: EnricherCapability,
  credential: EnricherCredential,
): Promise<void> {
  const key = credentialKey(enricher, credential)
  const value = credentialInputs[key]?.trim()
  if (!value || busyCredential.value) return
  busyCredential.value = key
  error.value = ''
  notice.value = ''
  try {
    replaceEnricher(await apiJson<EnricherCapability>(
      `/api/v1/settings/enrichers/${encodeURIComponent(enricher.enricher_id)}/credentials/${encodeURIComponent(credential.credential_id)}`,
      { method: 'PUT', body: JSON.stringify({ value }) },
    ))
    notice.value = messageTemplate('{0} 已保存到 Windows 凭据库。', credential.label)
  } catch {
    error.value = messageTemplate('无法保存 {0}，请检查后重试。', credential.label)
  } finally {
    credentialInputs[key] = ''
    syncDirty()
    busyCredential.value = undefined
  }
}

async function deleteEnricherCredential(
  enricher: EnricherCapability,
  credential: EnricherCredential,
): Promise<void> {
  const key = credentialKey(enricher, credential)
  if (busyCredential.value) return
  busyCredential.value = key
  error.value = ''
  notice.value = ''
  try {
    replaceEnricher(await apiJson<EnricherCapability>(
      `/api/v1/settings/enrichers/${encodeURIComponent(enricher.enricher_id)}/credentials/${encodeURIComponent(credential.credential_id)}`,
      { method: 'DELETE' },
    ))
    notice.value = messageTemplate('{0} 已从本机移除。', credential.label)
  } catch {
    error.value = messageTemplate('无法移除 {0}，请稍后重试。', credential.label)
  } finally {
    credentialInputs[key] = ''
    syncDirty()
    busyCredential.value = undefined
  }
}

onMounted(() => void load())
</script>

<template>
  <section class="settings-section" aria-labelledby="source-settings-title">
    <header class="page-heading">
      <h2 id="source-settings-title" data-settings-heading tabindex="-1">{{ t('论文来源') }}</h2>
      <span>{{ t('论文发现连接器与质量证据服务分层配置，凭据只进入 Windows 凭据库。') }}</span>
    </header>

    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice success" role="status">{{ notice }}</p>
    <p v-if="loading" class="quiet-state" role="status">{{ t('正在检查本机来源能力') }}</p>

    <template v-else>
      <section aria-labelledby="connector-settings-title">
        <div class="section-heading">
          <div>
            <h3 id="connector-settings-title">{{ t('官方论文来源') }}</h3>
          </div>
          <span>{{ sources.length }} {{ t('个来源') }}</span>
        </div>

        <div class="source-list">
          <article
            v-for="source in sources"
            :id="source.connector_id"
            :key="source.connector_id"
            class="source-row"
          >
            <div class="source-main">
              <div class="source-title">
                <h4>{{ source.display_name }}</h4>
                <span class="source-status" :class="{ ready: source.enabled }">
                  {{ source.enabled ? t('可用') : t('待配置') }}
                </span>
              </div>
              <p>{{ dateSemantics(source) }}</p>
              <p v-if="source.disabled_reason" class="disabled-reason">
                {{ serverMessage(source.disabled_reason) }}
              </p>
              <a v-if="source.setup_url" :href="source.setup_url" target="_blank" rel="noreferrer">
                {{ t('官方申请与接口说明') }}
                <PhArrowSquareOut :size="15" aria-hidden="true" />
              </a>
            </div>

            <div v-if="source.credential_requirement === 'none'" class="credential-free">
              <PhCheckCircle :size="20" aria-hidden="true" />
              <span><strong>{{ t('无需密钥') }}</strong>{{ t('可直接使用官方公开接口') }}</span>
            </div>

            <form
              v-else
              class="credential-form"
              :data-test="`credential-form-${source.connector_id}`"
              @submit.prevent="saveSourceCredential(source)"
            >
              <div class="credential-state">
                <PhKey :size="18" aria-hidden="true" />
                <span>{{ source.credential_configured ? t('已安全配置') : t('尚未配置') }}</span>
              </div>
              <label :for="`credential-${source.connector_id}`">
                {{ source.display_name }} {{ t('API 密钥') }}
              </label>
              <div class="credential-controls">
                <input
                  :id="`credential-${source.connector_id}`"
                  v-model="credentialInputs[source.connector_id]"
                  :data-test="`credential-${source.connector_id}`"
                  type="password"
                  autocomplete="new-password"
                  spellcheck="false"
                  :placeholder="t('输入新密钥')"
                  @input="syncDirty"
                />
                <button
                  :data-test="`save-${source.connector_id}`"
                  type="submit"
                  :disabled="busySource === source.connector_id || !credentialInputs[source.connector_id]?.trim()"
                >
                  {{ busySource === source.connector_id ? t('保存中') : source.credential_configured ? t('替换') : t('保存') }}
                </button>
                <button
                  v-if="source.credential_configured"
                  :data-test="`delete-${source.connector_id}`"
                  class="delete-button"
                  type="button"
                  :disabled="busySource === source.connector_id"
                  :aria-label="t('移除 {0} 密钥', source.display_name)"
                  @click="deleteSourceCredential(source)"
                >
                  <PhTrash :size="17" aria-hidden="true" />
                </button>
              </div>
              <small>{{ t('提交完成后输入框立即清空；界面只保留是否配置。') }}</small>
            </form>
          </article>
        </div>
      </section>

      <section aria-labelledby="evidence-settings-title">
        <div class="section-heading">
          <div>
            <h3 id="evidence-settings-title">{{ t('质量证据服务') }}</h3>
          </div>
          <span>{{ enrichers.length }} {{ t('个服务') }}</span>
        </div>

        <div class="source-list">
          <article
            v-for="enricher in enrichers"
            :id="enricher.enricher_id"
            :key="enricher.enricher_id"
            class="source-row"
          >
            <div class="source-main">
              <div class="source-title">
                <h4>{{ enricher.display_name }}</h4>
                <span class="source-status" :class="{ ready: enricher.enabled }">
                  {{ enricher.enabled ? t('可用') : t('待配置') }}
                </span>
              </div>
              <p>{{ enricherPurpose(enricher.enricher_id) }}</p>
              <p class="service-policy">{{ throughput(enricher) }} · {{ enricher.version }}</p>
              <p v-if="enricher.disabled_reason" class="disabled-reason">
                {{ serverMessage(enricher.disabled_reason) }}
              </p>
              <a :href="enricher.setup_url" target="_blank" rel="noreferrer">
                {{ t('官方申请与数据说明') }}
                <PhArrowSquareOut :size="15" aria-hidden="true" />
              </a>
            </div>

            <div class="credential-stack">
              <form
                v-for="credential in enricher.credentials"
                :key="credential.credential_id"
                class="credential-form evidence-credential"
                :data-test="`enricher-form-${enricher.enricher_id}-${credential.credential_id}`"
                @submit.prevent="saveEnricherCredential(enricher, credential)"
              >
                <div class="credential-state">
                  <PhKey :size="18" aria-hidden="true" />
                  <span>{{ credential.configured ? t('已安全配置') : credential.required ? t('需要配置') : t('可选') }}</span>
                </div>
                <label :for="`enricher-${enricher.enricher_id}-${credential.credential_id}`">
                  {{ credential.label }}
                </label>
                <div class="credential-controls">
                  <input
                    :id="`enricher-${enricher.enricher_id}-${credential.credential_id}`"
                    v-model="credentialInputs[credentialKey(enricher, credential)]"
                    :data-test="`enricher-${enricher.enricher_id}-${credential.credential_id}`"
                    :type="credential.secret ? 'password' : 'email'"
                    :autocomplete="credential.secret ? 'new-password' : 'email'"
                    spellcheck="false"
                    :placeholder="credential.secret ? t('输入新凭据') : 'name@example.org'"
                    @input="syncDirty"
                  />
                  <button
                    type="submit"
                    :disabled="busyCredential === credentialKey(enricher, credential) || !credentialInputs[credentialKey(enricher, credential)]?.trim()"
                  >
                    {{ busyCredential === credentialKey(enricher, credential) ? t('保存中') : credential.configured ? t('替换') : t('保存') }}
                  </button>
                  <button
                    v-if="credential.configured"
                    class="delete-button"
                    type="button"
                    :disabled="busyCredential === credentialKey(enricher, credential)"
                    :aria-label="t('移除 {0}', credential.label)"
                    @click="deleteEnricherCredential(enricher, credential)"
                  >
                    <PhTrash :size="17" aria-hidden="true" />
                  </button>
                </div>
                <small>{{ t('仅显示配置状态；提交后输入值立即从页面清空。') }}</small>
              </form>
            </div>
          </article>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.settings-section { display: grid; gap: 36px; }
.page-heading { padding-bottom: 16px; border-bottom: 2px solid var(--color-forest); }
.page-heading > p, .section-heading p { margin: 0 0 7px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: .12em; }
.page-heading h2, .section-heading h3, .source-title h4 { margin: 0; font-family: var(--font-editorial); font-weight: 500; }
.page-heading h2 { font-size: 30px; }
.page-heading h2:focus { outline: none; }
.page-heading > span { display: block; margin-top: 9px; color: var(--color-muted-strong); line-height: 1.65; }
.notice { margin: -14px 0 0; padding-left: 14px; border-left: 3px solid currentColor; }
.notice.error, .disabled-reason { color: var(--color-wine); }
.notice.success { color: var(--color-forest); }
.quiet-state { margin: 0; padding: 28px 0; color: var(--color-muted); }
.section-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; padding-bottom: 14px; }
.section-heading h3 { font-size: 23px; }
.section-heading > span { color: var(--color-muted); font-size: var(--type-meta); }
.source-list { border-top: 1px solid var(--color-line); }
.source-row { display: grid; grid-template-columns: minmax(230px, 1fr) minmax(280px, .9fr); gap: clamp(28px, 5vw, 64px); padding: 24px 0; border-bottom: 1px solid var(--color-line); scroll-margin-top: 30px; }
.source-title { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; }
.source-title h4 { font-size: 20px; }
.source-status { color: var(--color-wine); font-size: var(--type-meta); font-weight: 750; letter-spacing: .04em; }
.source-status.ready { color: var(--color-forest); }
.source-main > p { margin: 9px 0 0; color: var(--color-muted-strong); font-size: 12px; line-height: 1.6; }
.source-main .service-policy { color: var(--color-forest); font-weight: 650; }
.source-main a { width: fit-content; display: inline-flex; align-items: center; gap: 5px; margin-top: 13px; color: var(--color-forest); font-size: 12px; font-weight: 700; }
.credential-free, .credential-state { display: flex; align-items: center; gap: 9px; color: var(--color-forest); font-size: 12px; }
.credential-free { align-self: center; }
.credential-free span { display: grid; gap: 3px; }
.credential-form label { display: block; margin: 12px 0 7px; color: var(--color-muted-strong); font-size: var(--type-meta); font-weight: 700; }
.credential-controls { display: flex; gap: 8px; }
.credential-controls input { min-width: 0; flex: 1; min-height: 40px; padding: 0 11px; border: 1px solid var(--color-line-strong); border-radius: var(--radius-sm); background: var(--color-paper-raised); color: var(--color-ink); }
.credential-controls button { min-height: 40px; padding: 0 14px; border: 1px solid var(--color-forest); border-radius: var(--radius-sm); background: var(--color-forest); color: var(--color-on-accent); font: inherit; font-size: 12px; font-weight: 700; cursor: pointer; }
.credential-controls .delete-button { width: 40px; padding: 0; border-color: var(--color-line-strong); background: transparent; color: var(--color-wine); }
.credential-controls button:disabled { opacity: .5; cursor: not-allowed; }
.credential-controls input:focus-visible, .credential-controls button:focus-visible, .source-main a:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
.credential-form small { display: block; margin-top: 7px; color: var(--color-muted); font-size: var(--type-meta); line-height: 1.5; }
.credential-stack { display: grid; gap: 18px; }
.evidence-credential + .evidence-credential { padding-top: 18px; border-top: 1px solid var(--color-line); }

@media (max-width: 900px) {
  .source-row { grid-template-columns: 1fr; gap: 20px; }
  .credential-free { align-self: start; }
}

@media (max-width: 520px) {
  .credential-controls { flex-wrap: wrap; }
  .credential-controls input { flex-basis: 100%; }
}
</style>
