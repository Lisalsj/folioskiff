<script setup lang="ts">
import { computed, useSlots } from 'vue'

import { shellText as t } from '@/stores/locale'
import EmptyState from './EmptyState.vue'
import StatusNotice from './StatusNotice.vue'

const props = withDefaults(defineProps<{
  loading?: boolean
  error?: string | null
  empty?: boolean
  loadingMessage?: string
  errorTitle?: string
  emptyTitle?: string
  emptyDescription?: string
  retryLabel?: string
}>(), {
  loading: false,
  error: '',
  empty: false,
  emptyDescription: '',
})

const emit = defineEmits<{
  retry: []
}>()
const slots = useSlots()

const state = computed<'loading' | 'error' | 'empty' | 'content'>(() => {
  if (props.loading) return 'loading'
  if (props.error) return 'error'
  if (props.empty) return 'empty'
  return 'content'
})
</script>

<template>
  <div class="async-boundary" :data-state="state" :aria-busy="state === 'loading' || undefined">
    <slot v-if="state === 'loading' && slots.loading" name="loading" />
    <StatusNotice v-else-if="state === 'loading'" kind="progress" :message="loadingMessage ?? t('正在读取…')" />

    <slot v-else-if="state === 'error' && slots.error" name="error" :error="error" :retry="() => emit('retry')" />
    <StatusNotice v-else-if="state === 'error'" kind="error" :title="errorTitle ?? t('暂时无法读取')" :message="error || ''">
      <template #action>
        <button class="quiet-button quiet-button--primary" type="button" @click="emit('retry')">
          {{ retryLabel ?? t('重新读取') }}
        </button>
      </template>
    </StatusNotice>

    <slot v-else-if="state === 'empty' && slots.empty" name="empty" />
    <EmptyState
      v-else-if="state === 'empty'"
      :title="emptyTitle ?? t('暂无内容')"
      :description="emptyDescription"
    />

    <slot v-else />
  </div>
</template>

<style scoped>
.async-boundary:not([data-state='content']) {
  margin-top: clamp(48px, 8vw, 96px);
}

.async-boundary[data-state='content'] {
  display: contents;
}
</style>
