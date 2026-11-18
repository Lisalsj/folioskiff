<script setup lang="ts">
import { settingsText as t, serverMessage } from '@/stores/locale'
import { computed } from 'vue'

import type { components } from '@/api/schema'

type AgentCapability = components['schemas']['AgentSetupCapability']
type AgentProbeCode = components['schemas']['AgentProbeCode']

const props = withDefaults(defineProps<{
  agents: AgentCapability[]
  probing?: boolean
}>(), {
  probing: false,
})
const emit = defineEmits<{
  complete: []
  refresh: []
}>()

const priority: Readonly<Record<string, number>> = {
  codex_cli: 0,
  ollama_local: 1,
  ollama_cloud: 2,
}
const orderedAgents = computed(() => [...props.agents].sort(
  (left, right) => (priority[left.backend_id] ?? 99) - (priority[right.backend_id] ?? 99),
))

function diagnosticLabel(agent: AgentCapability): string {
  const labels: Readonly<Record<AgentProbeCode, string>> = {
    ready: t('可用'),
    not_installed: t('尚未安装'),
    not_authenticated: t('尚未登录'),
    configuration_invalid: agent.backend_id === 'codex_cli' ? t('Codex 配置无效') : t('配置无效'),
    version_unsupported: t('版本不受支持'),
    service_not_running: t('本地服务未启动'),
    probe_timeout: t('检测超时'),
    unavailable: t('暂时不可用'),
  }
  return labels[agent.probe_code]
}

function isCloud(agent: AgentCapability): boolean {
  return agent.location === 'cloud' || agent.backend_id === 'codex_cli'
}
</script>

<template>
  <section class="setup-step">
    <header>
      <h2 tabindex="-1">{{ t('检查 AI 能力') }}</h2>
      <p>{{ t('Agent 只会在你点击分析、翻译或对话时运行。没有可用模型也能继续使用论文发现与阅读功能。') }}</p>
    </header>

    <div class="setup-button-row probe-row">
      <button
        type="button"
        class="setup-button"
        data-test="probe-agents"
        :disabled="probing"
        @click="emit('refresh')"
      >
        {{ probing ? t('正在检测') : t('重新检测') }}
      </button>
    </div>

    <ul v-if="orderedAgents.length" class="setup-list">
      <li
        v-for="agent in orderedAgents"
        :id="agent.backend_id"
        :key="agent.backend_id"
        class="setup-card"
        :data-agent="agent.backend_id"
      >
        <div class="setup-card-header">
          <div>
            <h3>{{ agent.display_name }}</h3>
            <code>{{ agent.probe_code }}</code>
            <span v-if="agent.version" class="agent-version">{{ t('版本') }} {{ agent.version }}</span>
          </div>
          <span
            class="setup-status"
            :class="{ ready: agent.available, warning: !agent.available }"
          >
            {{ diagnosticLabel(agent) }}
          </span>
        </div>
        <p v-if="agent.backend_id === 'codex_cli'">{{ t('本地 CLI，模型可能在云端运行。') }}</p>
        <p v-else-if="isCloud(agent)">{{ t('云端后端。所选论文证据会发送到对应服务。') }}</p>
        <p v-else>{{ t('本机服务。论文证据不会离开此设备。') }}</p>
        <p v-if="agent.backend_id === 'codex_cli' && agent.desktop_app_running === true" class="desktop-status">
          {{ t('ChatGPT 桌面应用正在运行；生成能力以已验证的 Codex CLI 登录状态为准。') }}
        </p>
        <p v-else-if="agent.backend_id === 'codex_cli' && agent.desktop_app_running === false" class="desktop-status muted">
          {{ t('未检测到 ChatGPT 桌面进程；这不会影响已安装且已登录的 Codex CLI。') }}
        </p>
        <p v-if="agent.recommended_action" class="agent-action">
          {{ serverMessage(agent.recommended_action) }}
        </p>
        <div v-if="agent.models.length" class="model-list" :aria-label="t('可用模型')">
          <span v-for="model in agent.models" :key="model.model_id">{{ model.display_name }}</span>
        </div>
        <p v-else class="setup-help">{{ t('尚未发现模型。支持时仍可在后续设置中手动填写模型 ID。') }}</p>
      </li>
    </ul>
    <p v-else class="setup-help">{{ t('未发现 Agent 后端。你可以稍后在设置中重新检测。') }}</p>

    <div class="setup-button-row">
      <button type="button" class="setup-button primary" @click="emit('complete')">{{ t('继续') }}</button>
    </div>
  </section>
</template>

<style scoped>
.probe-row {
  margin-top: -8px;
  justify-content: flex-start;
}

.setup-card code {
  display: inline-block;
  margin-top: 4px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.agent-version {
  display: inline-block;
  margin-left: 8px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.agent-action {
  margin-top: 10px !important;
  color: var(--color-wine) !important;
}

.desktop-status { color: var(--color-forest) !important; }
.desktop-status.muted { color: var(--color-muted) !important; }

.model-list {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.model-list span {
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  background: var(--color-forest-soft);
  color: var(--color-forest);
  font-size: 12px;
}

@media (max-width: 620px) {
  .probe-row { margin-top: 0; }
}
</style>
