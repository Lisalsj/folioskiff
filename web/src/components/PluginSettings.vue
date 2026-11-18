<script setup lang="ts">
import { messageRef, messageTemplate, settingsText as t } from '@/stores/locale'
import { computed, onMounted, ref } from 'vue'
import {
  PhArrowClockwise,
  PhCheck,
  PhCircuitry,
  PhLockKey,
  PhPause,
  PhPlug,
  PhShieldCheck,
  PhWarning,
  PhX,
} from '@phosphor-icons/vue'
import {
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogOverlay,
  AlertDialogPortal,
  AlertDialogRoot,
  AlertDialogTitle,
} from 'reka-ui'

import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'

type Plugin = components['schemas']['PluginSummary']
type Permission = components['schemas']['PluginPermissionSummary']
type PermissionDecision = components['schemas']['PluginPermissionDecisionRequest']['decision']
type PluginRefresh = components['schemas']['PluginRefreshSummary']

interface PendingPermission {
  plugin: Plugin
  permission: Permission
  decision: PermissionDecision
}

const plugins = ref<Plugin[]>([])
const loading = ref(true)
const refreshing = ref(false)
const busyKey = ref('')
const error = messageRef(t)
const notice = messageRef(t)
const pendingPermission = ref<PendingPermission>()

const readyCount = computed(() => plugins.value.filter((plugin) => plugin.status === 'ready').length)

onMounted(() => void load())

async function load(scanInstalled = false): Promise<void> {
  loading.value = true
  refreshing.value = scanInstalled
  error.value = ''
  if (scanInstalled) notice.value = ''
  try {
    let refresh: PluginRefresh | undefined
    if (scanInstalled) {
      refresh = await apiJson<PluginRefresh>('/api/v1/plugins/refresh', { method: 'POST' })
    }
    plugins.value = await apiJson<Plugin[]>('/api/v1/plugins')
    if (refresh) {
      notice.value = refresh.issues.length
        ? messageTemplate('检查完成：发现 {0} 个，已登记 {1} 个；{2} 个未通过隔离验证。', refresh.discovered, refresh.registered, refresh.issues.length)
        : messageTemplate('检查完成：发现 {0} 个，已登记 {1} 个。', refresh.discovered, refresh.registered)
    }
  } catch {
    error.value = scanInstalled
      ? '无法完成扩展检查；未通过验证的扩展不会运行。'
      : '无法读取本机扩展，请稍后重试。'
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function replacePlugin(updated: Plugin): void {
  plugins.value = plugins.value.map((plugin) =>
    plugin.plugin_id === updated.plugin_id ? updated : plugin,
  )
}

function capabilityLabel(kind: string): string {
  const labels: Record<string, string> = {
    source_connector: t('论文来源'),
    metadata_enricher: t('质量证据'),
    score_dimension: t('评分维度'),
    agent_backend: t('Agent 后端'),
    publisher: t('精选发布'),
  }
  return labels[kind] ?? t('扩展能力')
}

function methodLabel(method: string): string {
  const labels: Record<string, string> = {
    probe: t('健康检查'),
    estimate: t('数量预估'),
    fetch_page: t('分页获取'),
    enrich: t('补充证据'),
    evaluate: t('计算证据'),
    models: t('模型列表'),
    generate: t('生成内容'),
    publish: t('发布快照'),
  }
  return labels[method] ?? method
}

function permissionKindLabel(kind: string): string {
  if (kind === 'network') return t('网络域名')
  if (kind === 'file') return t('受控文件')
  return t('凭据别名')
}

function statusLabel(plugin: Plugin): string {
  if (plugin.compatibility_reason === 'plugin_not_installed') return t('未找到安装')
  if (plugin.compatibility_reason === 'plugin_not_verified') return t('验证未通过')
  const labels: Record<Plugin['status'], string> = {
    ready: t('可以使用'),
    disabled: t('等待启用'),
    incompatible: t('版本不兼容'),
    permissions_required: t('等待授权'),
    circuit_open: t('已暂停保护'),
    sandbox_unavailable: t('隔离不可用'),
  }
  return labels[plugin.status]
}

function healthExplanation(plugin: Plugin): string {
  if (plugin.status === 'ready') return t('所有已声明权限均已批准，能力已加入本机工作流。')
  if (plugin.status === 'disabled') return t('扩展保持关闭，不会执行任何插件代码。')
  if (plugin.compatibility_reason === 'plugin_not_installed') {
    return t('本次检查未在当前运行时找到这个扩展；旧授权已撤销，能力已移除。')
  }
  if (plugin.compatibility_reason === 'plugin_not_verified') {
    return t('本次隔离检查未能验证扩展清单；旧授权已撤销，能力已移除。')
  }
  if (plugin.status === 'incompatible') return t('当前插件协议与本机版本不兼容，无法启用。')
  if (plugin.status === 'permissions_required') return t('至少一项能力仍在等待精确授权。')
  if (plugin.status === 'circuit_open') return t('连续失败达到保护阈值；检查后可显式恢复。')
  return t('Windows 受限运行环境不可用，扩展不会回退到普通进程。')
}

function askPermission(
  plugin: Plugin,
  permission: Permission,
  decision: PermissionDecision,
): void {
  pendingPermission.value = { plugin, permission, decision }
}

async function confirmPermission(): Promise<void> {
  const pending = pendingPermission.value
  if (!pending) return
  const key = `permission:${pending.plugin.plugin_id}:${pending.permission.kind}:${pending.permission.resource}`
  busyKey.value = key
  error.value = ''
  notice.value = ''
  try {
    const updated = await apiJson<Plugin>(
      `/api/v1/plugins/${encodeURIComponent(pending.plugin.plugin_id)}/permissions`,
      {
        method: 'POST',
        body: JSON.stringify({
          kind: pending.permission.kind,
          resource: pending.permission.resource,
          decision: pending.decision,
        }),
      },
    )
    replacePlugin(updated)
    notice.value = pending.decision === 'grant'
      ? messageTemplate('已批准 {0}。', pending.permission.resource)
      : messageTemplate('已撤销 {0}；相关能力已立即移除。', pending.permission.resource)
    pendingPermission.value = undefined
  } catch {
    error.value = '无法更新扩展授权，请稍后重试。'
  } finally {
    busyKey.value = ''
  }
}

async function setEnabled(plugin: Plugin, enabled: boolean): Promise<void> {
  busyKey.value = `enabled:${plugin.plugin_id}`
  error.value = ''
  notice.value = ''
  try {
    const updated = await apiJson<Plugin>(
      `/api/v1/plugins/${encodeURIComponent(plugin.plugin_id)}/enabled`,
      { method: 'PUT', body: JSON.stringify({ enabled }) },
    )
    replacePlugin(updated)
    notice.value = enabled
      ? messageTemplate('已启用 {0}；仅获批能力会加入工作流。', plugin.display_name)
      : messageTemplate('已停用 {0}。', plugin.display_name)
  } catch {
    error.value = '无法更新扩展状态，请稍后重试。'
  } finally {
    busyKey.value = ''
  }
}

async function resetCircuit(plugin: Plugin): Promise<void> {
  busyKey.value = `reset:${plugin.plugin_id}`
  error.value = ''
  notice.value = ''
  try {
    const updated = await apiJson<Plugin>(
      `/api/v1/plugins/${encodeURIComponent(plugin.plugin_id)}/reset`,
      { method: 'POST' },
    )
    replacePlugin(updated)
    notice.value = messageTemplate('已恢复调用 {0}；后续失败仍会重新触发保护。', plugin.display_name)
  } catch {
    error.value = '无法恢复扩展调用，请稍后重试。'
  } finally {
    busyKey.value = ''
  }
}
</script>

<template>
  <section class="plugin-settings" aria-labelledby="plugin-settings-title">
    <header class="plugin-heading">
      <div>
        <p>{{ t('受限扩展') }}</p>
        <h2 id="plugin-settings-title">{{ t('扩展能力') }}</h2>
        <span>{{ t('插件只在 Windows 隔离环境中运行。每个网络域、文件资源和凭据别名都需要单独批准。') }}</span>
      </div>
      <div class="plugin-heading-actions">
        <span v-if="!loading">{{ plugins.length }} {{ t('个扩展 ·') }} {{ readyCount }} {{ t('个可用') }}</span>
        <button
          type="button"
          data-test="reload-plugins"
          :disabled="loading"
          @click="load(true)"
        >
          <PhArrowClockwise :size="15" :class="{ spinning: loading }" aria-hidden="true" />
          {{ t('重新检查') }}
        </button>
      </div>
    </header>

    <p v-if="error" class="plugin-message error" role="alert">{{ error }}</p>
    <p v-if="notice" class="plugin-message success" role="status">{{ notice }}</p>

    <div v-if="loading" class="plugin-empty" role="status">
      <PhArrowClockwise :size="22" class="spinning" aria-hidden="true" />
      <strong>{{ refreshing ? t('正在隔离验证扩展清单') : t('正在读取本机扩展记录') }}</strong>
      <span>{{ refreshing
        ? t('插件代码只在 Windows 受限进程中读取清单，不会获得业务权限。')
        : t('读取记录不会加载任何插件代码。') }}</span>
    </div>

    <div v-else-if="plugins.length === 0" class="plugin-empty" data-test="plugin-empty">
      <PhPlug :size="25" aria-hidden="true" />
      <strong>{{ t('尚未发现已安装的扩展') }}</strong>
      <span>{{ t('安装扩展后可点“重新检查”。示例插件不会自动安装或冒充真实论文来源。') }}</span>
    </div>

    <div v-else class="plugin-list">
      <article
        v-for="plugin in plugins"
        :key="plugin.plugin_id"
        class="plugin-card"
        :class="`status-${plugin.status}`"
      >
        <header class="plugin-card-heading">
          <div class="plugin-identity">
            <span class="plugin-index" aria-hidden="true">{{ String(plugins.indexOf(plugin) + 1).padStart(2, '0') }}</span>
            <div>
              <h3>{{ plugin.display_name }}</h3>
              <p>{{ plugin.distribution_name }} · {{ plugin.distribution_version }}</p>
            </div>
          </div>
          <span class="status-badge">
            <PhCheck v-if="plugin.status === 'ready'" :size="13" aria-hidden="true" />
            <PhPause v-else-if="plugin.status === 'disabled'" :size="13" aria-hidden="true" />
            <PhWarning v-else :size="13" aria-hidden="true" />
            {{ statusLabel(plugin) }}
          </span>
        </header>

        <div class="plugin-contract">
          <div class="health-note">
            <PhShieldCheck :size="19" aria-hidden="true" />
            <div>
              <strong>{{ plugin.sandbox_available ? t('Windows 隔离可用') : t('Windows 隔离不可用') }}</strong>
              <span>{{ healthExplanation(plugin) }}</span>
            </div>
          </div>
          <dl>
            <div>
              <dt>{{ t('协议') }}</dt>
              <dd>{{ plugin.protocol_minimum }}–{{ plugin.protocol_maximum }}</dd>
            </div>
            <div>
              <dt>{{ t('许可证') }}</dt>
              <dd>{{ plugin.license }}</dd>
            </div>
            <div>
              <dt>{{ t('调用保护') }}</dt>
              <dd>{{ t('连续失败') }} {{ plugin.consecutive_failures }} {{ t('次') }}</dd>
            </div>
          </dl>
        </div>

        <div class="capability-list" :aria-label="t('扩展能力与授权')">
          <section
            v-for="capability in plugin.capabilities"
            :key="capability.capability_id"
            class="capability-row"
          >
            <div class="capability-copy">
              <span class="capability-kind">{{ capabilityLabel(capability.kind) }}</span>
              <strong>{{ capability.capability_id }}</strong>
              <small>{{ capability.ready ? t('已加入工作流') : t('当前不执行') }} {{ t('· 最长') }} {{ capability.timeout_seconds }} {{ t('秒') }}</small>
              <details>
                <summary>{{ t('查看允许的操作') }}</summary>
                <span>{{ capability.methods.map(methodLabel).join(' · ') }}</span>
              </details>
            </div>

            <div v-if="capability.permissions.length" class="permission-list">
              <article
                v-for="permission in capability.permissions"
                :key="`${permission.kind}:${permission.resource}`"
                class="permission-row"
              >
                <PhLockKey :size="16" aria-hidden="true" />
                <div>
                  <span>{{ permissionKindLabel(permission.kind) }}</span>
                  <strong>{{ permission.resource }}</strong>
                  <small>{{ permission.purpose }}</small>
                </div>
                <button
                  v-if="permission.granted"
                  type="button"
                  class="permission-button revoke"
                  :data-test="`revoke-${plugin.plugin_id}-${permission.kind}-${permission.resource}`"
                  :disabled="Boolean(busyKey)"
                  @click="askPermission(plugin, permission, 'revoke')"
                >
                  {{ t('撤销') }}
                </button>
                <button
                  v-else
                  type="button"
                  class="permission-button"
                  :data-test="`grant-${plugin.plugin_id}-${permission.kind}-${permission.resource}`"
                  :disabled="Boolean(busyKey)"
                  @click="askPermission(plugin, permission, 'grant')"
                >
                  {{ t('批准') }}
                </button>
              </article>
            </div>
            <div v-else class="permission-free">
              <PhCheck :size="15" aria-hidden="true" />
              {{ t('不请求网络、文件或凭据') }}
            </div>
          </section>
        </div>

        <footer class="plugin-actions">
          <button
            v-if="plugin.circuit_open_reason"
            type="button"
            class="secondary-action"
            :data-test="`reset-${plugin.plugin_id}`"
            :disabled="Boolean(busyKey)"
            @click="resetCircuit(plugin)"
          >
            <PhCircuitry :size="16" aria-hidden="true" />
            {{ t('重置失败保护') }}
          </button>
          <button
            v-if="plugin.enabled"
            type="button"
            class="secondary-action danger"
            :data-test="`disable-${plugin.plugin_id}`"
            :disabled="Boolean(busyKey)"
            @click="setEnabled(plugin, false)"
          >
            {{ t('停用扩展') }}
          </button>
          <button
            v-else
            type="button"
            class="primary-action"
            :data-test="`enable-${plugin.plugin_id}`"
            :disabled="Boolean(busyKey) || !plugin.compatible || !plugin.sandbox_available"
            @click="setEnabled(plugin, true)"
          >
            {{ t('启用扩展') }}
          </button>
        </footer>
      </article>
    </div>

    <AlertDialogRoot
      :open="Boolean(pendingPermission)"
      @update:open="(value) => { if (!value && !busyKey) pendingPermission = undefined }"
    >
      <AlertDialogPortal>
        <AlertDialogOverlay class="permission-overlay" />
        <AlertDialogContent class="permission-dialog" data-test="plugin-permission-dialog">
          <div class="dialog-mark" aria-hidden="true">
            <PhLockKey :size="20" />
          </div>
          <AlertDialogTitle>
            {{ pendingPermission?.decision === 'grant' ? t('批准这一项权限？') : t('撤销这一项权限？') }}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {{ pendingPermission?.decision === 'grant'
              ? t('只批准这一项。插件仍不能访问未列出的网络、文件或凭据。')
              : t('撤销后，依赖这项权限的能力会立即从工作流移除。') }}
          </AlertDialogDescription>
          <dl v-if="pendingPermission" class="permission-review">
            <div>
              <dt>{{ t('对象') }}</dt>
              <dd>{{ pendingPermission.permission.resource }}</dd>
            </div>
            <div>
              <dt>{{ t('用途') }}</dt>
              <dd>{{ pendingPermission.permission.purpose }}</dd>
            </div>
            <div>
              <dt>{{ t('扩展') }}</dt>
              <dd>{{ pendingPermission.plugin.display_name }}</dd>
            </div>
          </dl>
          <div class="dialog-actions">
            <AlertDialogCancel as-child>
              <button type="button" :disabled="Boolean(busyKey)">
                <PhX :size="15" aria-hidden="true" />
                {{ t('取消') }}
              </button>
            </AlertDialogCancel>
            <button
              type="button"
              class="confirm"
              data-test="confirm-plugin-permission"
              :disabled="Boolean(busyKey)"
              @click="confirmPermission"
            >
              {{ pendingPermission?.decision === 'grant' ? t('确认批准') : t('确认撤销') }}
            </button>
          </div>
        </AlertDialogContent>
      </AlertDialogPortal>
    </AlertDialogRoot>
  </section>
</template>

<style scoped>
.plugin-settings {
  margin-top: clamp(48px, 7vw, 84px);
}

.plugin-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 28px;
  padding-bottom: 18px;
  border-bottom: 2px solid var(--color-forest);
}

.plugin-heading p {
  margin: 0 0 10px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.13em;
}

.plugin-heading h2 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: 28px;
  font-weight: 500;
}

.plugin-heading > div > span {
  display: block;
  max-width: 680px;
  margin-top: 9px;
  color: var(--color-muted-strong);
  font-size: 12px;
  line-height: 1.65;
}

.plugin-heading-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 13px;
}

.plugin-heading-actions > span {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.plugin-heading-actions button,
.plugin-actions button,
.permission-button,
.dialog-actions button {
  display: inline-flex;
  min-height: 38px;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 0 13px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-forest);
  font: inherit;
  font-size: var(--type-meta);
  font-weight: 700;
  cursor: pointer;
}

.plugin-heading-actions button:disabled,
.plugin-actions button:disabled,
.permission-button:disabled,
.dialog-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.plugin-message {
  margin: 18px 0 0;
  padding-left: 13px;
  border-left: 3px solid currentColor;
  font-size: 12px;
}

.plugin-message.error { color: var(--color-danger); }
.plugin-message.success { color: var(--color-forest); }

.plugin-empty {
  display: grid;
  min-height: 190px;
  place-items: center;
  align-content: center;
  gap: 8px;
  border-bottom: 1px solid var(--color-line);
  color: var(--color-muted);
  text-align: center;
}

.plugin-empty strong {
  color: var(--color-ink);
  font-family: var(--font-editorial);
  font-size: 19px;
  font-weight: 500;
}

.plugin-empty span { max-width: 480px; font-size: var(--type-meta); line-height: 1.6; }
.plugin-list { display: grid; }

.plugin-card {
  padding: 30px 0 34px;
  border-bottom: 1px solid var(--color-line);
}

.plugin-card-heading,
.plugin-actions,
.plugin-identity,
.status-badge,
.health-note,
.permission-row {
  display: flex;
  align-items: center;
}

.plugin-card-heading { justify-content: space-between; gap: 24px; }
.plugin-identity { gap: 14px; }

.plugin-index {
  color: var(--color-wine);
  font-family: var(--font-editorial);
  font-size: 13px;
}

.plugin-identity h3 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: 23px;
  font-weight: 500;
}

.plugin-identity p {
  margin: 4px 0 0;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.status-badge {
  gap: 5px;
  padding: 4px 9px;
  border: 1px solid var(--color-line);
  border-radius: 999px;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  font-weight: 750;
  letter-spacing: 0.04em;
}

.status-ready .status-badge { border-color: var(--color-forest); color: var(--color-forest); }
.status-circuit_open .status-badge,
.status-incompatible .status-badge,
.status-sandbox_unavailable .status-badge { border-color: var(--color-wine); color: var(--color-wine); }

.plugin-contract {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 36px;
  align-items: start;
  margin-top: 22px;
  padding: 17px 0;
  border-top: 1px solid var(--color-line);
  border-bottom: 1px solid var(--color-line);
}

.health-note { gap: 10px; color: var(--color-forest); }
.health-note > div { display: grid; gap: 3px; }
.health-note strong { font-size: var(--type-meta); }
.health-note span { color: var(--color-muted-strong); font-size: var(--type-meta); line-height: 1.5; }

.plugin-contract dl {
  display: flex;
  gap: 22px;
  margin: 0;
}

.plugin-contract dl > div { display: grid; gap: 3px; }
.plugin-contract dt { color: var(--color-muted); font-size: var(--type-meta); font-weight: 700; letter-spacing: 0.07em; text-transform: uppercase; }
.plugin-contract dd { margin: 0; color: var(--color-ink); font-size: var(--type-meta); }

.capability-row {
  display: grid;
  grid-template-columns: minmax(220px, 0.38fr) minmax(0, 0.62fr);
  gap: clamp(28px, 6vw, 80px);
  padding: 24px 0;
  border-bottom: 1px solid var(--color-line);
}

.capability-copy { display: grid; align-content: start; gap: 5px; }
.capability-kind { color: var(--color-wine); font-size: var(--type-meta); font-weight: 750; letter-spacing: 0.08em; }
.capability-copy > strong { font-family: var(--font-editorial); font-size: 16px; font-weight: 500; overflow-wrap: anywhere; }
.capability-copy > small { color: var(--color-muted); font-size: var(--type-meta); }
.capability-copy details { margin-top: 6px; color: var(--color-muted-strong); font-size: var(--type-meta); }
.capability-copy summary { width: fit-content; cursor: pointer; font-weight: 700; }
.capability-copy details span { display: block; margin-top: 5px; }

.permission-list { display: grid; gap: 1px; background: var(--color-line); }
.permission-row { gap: 10px; min-width: 0; padding: 12px; background: var(--color-paper); }
.permission-row > svg { flex: 0 0 auto; color: var(--color-forest); }
.permission-row > div { display: grid; min-width: 0; flex: 1; gap: 2px; }
.permission-row span { color: var(--color-muted); font-size: var(--type-meta); font-weight: 700; letter-spacing: 0.06em; }
.permission-row strong { font-size: var(--type-meta); overflow-wrap: anywhere; }
.permission-row small { color: var(--color-muted-strong); font-size: var(--type-meta); line-height: 1.45; }
.permission-button { min-height: 31px; padding: 0 10px; border-color: var(--color-forest); }
.permission-button.revoke { border-color: var(--color-line); color: var(--color-danger); }

.permission-free {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--color-forest);
  font-size: var(--type-meta);
}

.plugin-actions { justify-content: flex-end; gap: 8px; margin-top: 18px; }
.plugin-actions .primary-action { border-color: var(--color-forest); background: var(--color-forest); color: var(--color-on-accent); }
.plugin-actions .danger { color: var(--color-danger); }

.plugin-heading-actions button:focus-visible,
.plugin-actions button:focus-visible,
.permission-button:focus-visible,
.capability-copy summary:focus-visible,
.dialog-actions button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

.spinning { animation: plugin-spin 1s linear infinite; }

@keyframes plugin-spin { to { transform: rotate(360deg); } }

@media (max-width: 820px) {
  .plugin-heading,
  .plugin-card-heading { align-items: flex-start; }
  .plugin-heading { flex-direction: column; }
  .plugin-contract,
  .capability-row { grid-template-columns: 1fr; gap: 20px; }
  .plugin-contract dl { flex-wrap: wrap; }
}

@media (max-width: 520px) {
  .plugin-heading-actions { width: 100%; justify-content: space-between; }
  .plugin-card-heading { flex-direction: column; }
  .status-badge { align-self: flex-start; }
  .permission-row { align-items: flex-start; flex-wrap: wrap; }
  .permission-row > button { margin-left: 26px; }
  .plugin-actions { align-items: stretch; flex-direction: column-reverse; }
  .plugin-actions button { width: 100%; }
}

@media (prefers-reduced-motion: reduce) {
  .spinning { animation: none; }
}
</style>

<style>
.permission-overlay {
  position: fixed;
  z-index: 80;
  inset: 0;
  background: rgb(18 28 24 / 48%);
  backdrop-filter: blur(2px);
}

.permission-dialog {
  position: fixed;
  z-index: 81;
  top: 50%;
  left: 50%;
  width: min(92vw, 500px);
  max-height: 88vh;
  padding: 28px;
  overflow: auto;
  transform: translate(-50%, -50%);
  border: 1px solid var(--color-line);
  border-top: 3px solid var(--color-forest);
  background: var(--color-paper);
  box-shadow: 0 24px 90px rgb(20 31 27 / 22%);
  color: var(--color-ink);
}

.dialog-mark {
  display: grid;
  width: 38px;
  height: 38px;
  margin-bottom: 17px;
  place-items: center;
  border: 1px solid var(--color-forest);
  border-radius: 50%;
  color: var(--color-forest);
}

.permission-dialog h2 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: 25px;
  font-weight: 500;
}

.permission-dialog > p {
  margin: 9px 0 0;
  color: var(--color-muted-strong);
  font-size: 12px;
  line-height: 1.65;
}

.permission-review {
  display: grid;
  margin: 22px 0;
  border-top: 1px solid var(--color-line);
}

.permission-review > div {
  display: grid;
  grid-template-columns: 70px minmax(0, 1fr);
  gap: 16px;
  padding: 11px 0;
  border-bottom: 1px solid var(--color-line);
}

.permission-review dt { color: var(--color-muted); font-size: var(--type-meta); font-weight: 700; }
.permission-review dd { margin: 0; font-size: var(--type-meta); overflow-wrap: anywhere; }

.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; }
.dialog-actions .confirm { border-color: var(--color-forest); background: var(--color-forest); color: var(--color-on-accent); }

@media (prefers-reduced-motion: reduce) {
  .permission-overlay,
  .permission-dialog { animation: none !important; transition: none !important; }
}
</style>
