<script setup lang="ts">
import { messageRef, messageTemplate, settingsText as t } from '@/stores/locale'
import { reactive, ref, watch } from 'vue'

import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'

type SourceCapability = components['schemas']['SourceSetupCapability']
type SourceCapabilityResponse = components['schemas']['SourceCapabilityResponse']

const props = defineProps<{ sources: SourceCapability[] }>()
const emit = defineEmits<{ complete: [] }>()
const localSources = ref<SourceCapability[]>([...props.sources])
const credentialInputs = reactive<Record<string, string>>({})
const busySource = ref<string>()
const error = messageRef(t)
const notice = messageRef(t)

watch(() => props.sources, (sources) => { localSources.value = [...sources] })

function requiresCredential(source: SourceCapability): boolean {
  return source.credential_requirement !== 'none'
}

function statusLabel(source: SourceCapability): string {
  if (source.enabled) return source.credential_configured ? t('已配置') : t('可用')
  if (requiresCredential(source) && !source.credential_configured) return t('可稍后配置')
  return t('暂时不可用')
}

function replaceSource(updated: SourceCapabilityResponse): void {
  localSources.value = localSources.value.map((source) => source.connector_id === updated.connector_id
    ? {
        connector_id: updated.connector_id,
        display_name: updated.display_name,
        enabled: updated.enabled,
        credential_requirement: updated.credential_requirement,
        credential_configured: updated.credential_configured,
        disabled_reason: updated.disabled_reason,
        setup_url: updated.setup_url,
      }
    : source)
}

async function saveCredential(source: SourceCapability): Promise<void> {
  const value = credentialInputs[source.connector_id]?.trim()
  if (!value || busySource.value) return
  busySource.value = source.connector_id
  error.value = ''
  notice.value = ''
  try {
    const updated = await apiJson<SourceCapabilityResponse>(
      `/api/v1/settings/sources/${encodeURIComponent(source.connector_id)}/credential`,
      { method: 'PUT', body: JSON.stringify({ value }) },
    )
    replaceSource(updated)
    notice.value = messageTemplate('{0} 凭据已安全保存。', source.display_name)
  } catch {
    error.value = messageTemplate('{0} 凭据保存失败，请检查后重试。', source.display_name)
  } finally {
    credentialInputs[source.connector_id] = ''
    busySource.value = undefined
  }
}
</script>

<template>
  <section class="setup-step">
    <header>
      <h2 tabindex="-1">{{ t('检查论文来源') }}</h2>
      <p>{{ t('arXiv、OpenReview 与 Europe PMC 可直接使用。IEEE Xplore 与 Springer Nature 的密钥是可选配置。') }}</p>
    </header>

    <p v-if="error" class="setup-alert" role="alert">{{ error }}</p>
    <p v-if="notice" class="setup-notice" role="status">{{ notice }}</p>

    <ul class="setup-list">
      <li
        v-for="source in localSources"
        :key="source.connector_id"
        class="setup-card source-row"
        :data-source="source.connector_id"
      >
        <div class="setup-card-header">
          <div>
            <h3>{{ source.display_name }}</h3>
            <p>{{ requiresCredential(source) ? t('可选凭据来源') : t('免费来源') }}</p>
          </div>
          <span class="setup-status" :class="{ ready: source.enabled, warning: !source.enabled }">
            {{ statusLabel(source) }}
          </span>
        </div>

        <div v-if="requiresCredential(source)" class="credential-row">
          <label class="setup-field">
            <span>{{ source.display_name }} {{ t('凭据') }}</span>
            <input
              v-model="credentialInputs[source.connector_id]"
              type="password"
              :data-credential="source.connector_id"
              autocomplete="new-password"
            />
          </label>
          <button
            type="button"
            class="setup-button"
            :data-save-credential="source.connector_id"
            :disabled="busySource === source.connector_id || !credentialInputs[source.connector_id]?.trim()"
            @click="saveCredential(source)"
          >
            {{ busySource === source.connector_id ? t('保存中') : t('保存') }}
          </button>
        </div>

        <a
          v-if="source.setup_url"
          class="setup-link"
          :href="source.setup_url"
          target="_blank"
          rel="noreferrer"
        >
          {{ t('查看官方配置说明') }}
        </a>
      </li>
    </ul>

    <div class="setup-button-row">
      <button type="button" class="setup-button primary" @click="emit('complete')">{{ t('继续') }}</button>
    </div>
  </section>
</template>

<style scoped>
.source-row {
  display: grid;
  gap: 14px;
}

.credential-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: end;
}

@media (max-width: 560px) {
  .credential-row {
    grid-template-columns: 1fr;
  }
}
</style>
