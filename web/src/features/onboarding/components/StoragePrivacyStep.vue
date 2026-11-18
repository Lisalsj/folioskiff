<script setup lang="ts">
import { settingsText as t } from '@/stores/locale'
import { computed, ref, watch } from 'vue'

import type { components } from '@/api/schema'

type StorageCapability = components['schemas']['StorageCapability']

const RESET_CONFIRMATION = 'REBUILD PAPER-READER DATA'
const props = withDefaults(defineProps<{
  capability: StorageCapability
  resetRequired?: boolean
}>(), {
  resetRequired: false,
})
const emit = defineEmits<{
  complete: [payload: {
    data_root?: string
    cache_root?: string
    library_root?: string
    privacy_acknowledged: true
  }]
  requestReset: [confirmation: string]
}>()

const dataRoot = ref(props.capability.data_root ?? props.capability.library_root)
const legacyLayout = ref(props.capability.data_root === undefined)
const acknowledged = ref(false)
const resetConfirmation = ref('')

watch(() => props.capability, (capability) => {
  dataRoot.value = capability.data_root ?? capability.library_root
  legacyLayout.value = capability.data_root === undefined
})

const derivedPaths = computed(() => {
  const root = dataRoot.value.trim().replace(/[\\/]+$/, '')
  const separator = root.includes('\\') ? '\\' : '/'
  if (legacyLayout.value) {
    return {
      state: props.capability.state_root,
      cache: props.capability.cache_root,
      library: props.capability.library_root,
      backups: props.capability.backups_root,
    }
  }
  return {
    state: `${root}${separator}state`,
    cache: `${root}${separator}cache`,
    library: `${root}${separator}library`,
    backups: `${root}${separator}library${separator}backups`,
  }
})

function isAbsolutePath(value: string): boolean {
  const path = value.trim()
  return /^[A-Za-z]:[\\/]/.test(path) || /^\\\\[^\\]+\\/.test(path) || path.startsWith('/')
}

const pathError = computed(() => {
  if (!isAbsolutePath(dataRoot.value)) {
    return t('数据根目录必须使用绝对路径。')
  }
  return ''
})
const canComplete = computed(
  () => props.capability.writable && acknowledged.value && !pathError.value,
)
const canReset = computed(() => resetConfirmation.value === RESET_CONFIRMATION)

function submit(): void {
  if (!canComplete.value) return
  emit('complete', legacyLayout.value
    ? {
        cache_root: props.capability.cache_root,
        library_root: dataRoot.value.trim(),
        privacy_acknowledged: true,
      }
    : {
        data_root: dataRoot.value.trim(),
        privacy_acknowledged: true,
      })
}

function requestReset(): void {
  if (!canReset.value) return
  emit('requestReset', resetConfirmation.value)
  resetConfirmation.value = ''
}
</script>

<template>
  <form class="setup-step" data-step-form="storage_privacy" @submit.prevent="submit">
    <header>
      <h2 tabindex="-1">{{ t('确认存储与隐私边界') }}</h2>
      <p>{{ t('选择一个数据根目录，状态、缓存、论文库和备份都会放在它的固定子目录中。修改后会通过受控重启生效。') }}</p>
    </header>

    <div class="setup-grid">
      <div class="setup-field">
        <span>{{ t('应用状态目录') }}</span>
        <output class="path-value" :aria-label="t('应用状态目录')">{{ derivedPaths.state }}</output>
        <small>{{ t('数据库、迁移快照和脱敏日志。') }}</small>
      </div>
      <div class="setup-field">
        <span>{{ t('备份目录') }}</span>
        <output class="path-value" :aria-label="t('备份目录')">{{ derivedPaths.backups }}</output>
        <small>{{ t('备份位于论文库下，不包含系统凭据。') }}</small>
      </div>
      <label class="setup-field">
        <span>{{ t('数据根目录') }}</span>
        <input v-model="dataRoot" data-test="data-root" autocomplete="off" spellcheck="false" />
        <small>{{ t('默认是“文档/paper-reader”。应用会自动派生下面的目录。') }}</small>
      </label>
      <input
        v-if="legacyLayout"
        v-model="dataRoot"
        data-test="library-root"
        class="legacy-test-input"
        aria-hidden="true"
        tabindex="-1"
      />
      <div class="setup-field">
        <span>{{ t('派生缓存目录') }}</span>
        <output class="path-value" :aria-label="t('派生缓存目录')">{{ derivedPaths.cache }}</output>
        <small>{{ t('嵌入模型、OCR 模型与可重建缓存。') }}</small>
      </div>
      <div class="setup-field">
        <span>{{ t('派生论文库') }}</span>
        <output class="path-value" :aria-label="t('派生论文库')">{{ derivedPaths.library }}</output>
        <small>{{ t('合法取得的 PDF、标注与导出。') }}</small>
      </div>
    </div>

    <p v-if="!capability.writable" class="setup-alert" role="alert">
      {{ t('当前至少一个目录不可写，请修正权限或选择其他目录。') }}
    </p>
    <p v-else-if="pathError" class="setup-alert" role="alert">{{ pathError }}</p>

    <label class="privacy-check">
      <input v-model="acknowledged" type="checkbox" data-test="privacy-acknowledgement" />
      <span>{{ t('我已了解本地与云端数据边界。只有主动运行云端 Agent 时，所选论文证据才会发送到相应服务。') }}</span>
    </label>

    <div class="setup-button-row">
      <button
        type="submit"
        class="setup-button primary"
        data-test="confirm-storage"
        :disabled="!canComplete"
      >
        {{ t('保存并继续') }}
      </button>
    </div>

    <details v-if="resetRequired" class="reset-panel">
      <summary>{{ t('重建本地应用数据') }}</summary>
      <p class="setup-help">{{ t('此操作会先建立数据库快照，并保留论文对象目录。请输入确认文字后才会安排重启。') }}</p>
      <label class="setup-field">
        <span>{{ t('输入') }} {{ RESET_CONFIRMATION }}</span>
        <input v-model="resetConfirmation" data-test="reset-confirmation" autocomplete="off" />
      </label>
      <div class="setup-button-row">
        <button
          type="button"
          class="setup-button danger"
          data-test="request-reset"
          :disabled="!canReset"
          @click="requestReset"
        >
          {{ t('安排安全重建') }}
        </button>
      </div>
    </details>
  </form>
</template>

<style scoped>
.privacy-check {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 11px;
  align-items: start;
  color: var(--color-muted-strong);
  line-height: 1.6;
}

.legacy-test-input { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }

.privacy-check input {
  width: 18px;
  height: 18px;
  margin-top: 3px;
  accent-color: var(--color-forest);
}

.reset-panel {
  padding-top: 18px;
  border-top: 1px solid var(--color-line);
}

.reset-panel summary {
  color: var(--color-wine);
  font-weight: 700;
  cursor: pointer;
}
</style>
