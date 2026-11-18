<script setup lang="ts">
import { computed, useSlots } from 'vue'

type NoticeKind = 'info' | 'success' | 'warning' | 'error' | 'progress'

const props = withDefaults(defineProps<{
  kind?: NoticeKind
  title?: string
  message?: string
}>(), {
  kind: 'info',
  title: '',
  message: '',
})

const slots = useSlots()
const role = computed(() => {
  if (props.kind === 'error') return 'alert'
  if (props.kind === 'progress' || props.kind === 'success') return 'status'
  return undefined
})
const live = computed(() => {
  if (props.kind === 'error') return 'assertive'
  if (props.kind === 'progress' || props.kind === 'success') return 'polite'
  return undefined
})
</script>

<template>
  <section
    class="status-notice"
    :class="`status-notice--${kind}`"
    :role="role"
    :aria-live="live"
  >
    <div class="status-notice__copy">
      <h2 v-if="title">{{ title }}</h2>
      <p v-if="message">{{ message }}</p>
      <slot />
    </div>
    <div v-if="slots.action" class="status-notice__action">
      <slot name="action" />
    </div>
  </section>
</template>

<style scoped>
.status-notice {
  max-width: 680px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 20px;
  border: 1px solid var(--color-line);
  border-left: 3px solid var(--color-forest);
  border-radius: var(--radius-sm);
  background: var(--color-paper-raised);
}

.status-notice--error {
  border-left-color: var(--color-danger);
}

.status-notice--warning {
  border-left-color: var(--color-wine);
}

.status-notice--success {
  background: var(--color-forest-soft);
}

.status-notice__copy {
  min-width: 0;
}

h2,
p {
  margin: 0;
}

h2 {
  font-family: var(--font-editorial);
  font-size: 20px;
  font-weight: 500;
}

h2 + p {
  margin-top: 6px;
}

p,
:slotted(p) {
  color: var(--color-muted-strong);
  font-size: 13px;
  line-height: 1.65;
}

.status-notice__action {
  flex: none;
}

@media (max-width: 640px) {
  .status-notice {
    flex-direction: column;
  }
}
</style>
