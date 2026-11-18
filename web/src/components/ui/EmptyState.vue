<script setup lang="ts">
import { useSlots } from 'vue'

defineProps<{
  title: string
  eyebrow?: string
  description?: string
}>()

const slots = useSlots()
</script>

<template>
  <section class="empty-state">
    <div v-if="slots.icon" class="empty-state__icon">
      <slot name="icon" />
    </div>
    <p v-if="eyebrow" class="empty-state__eyebrow">{{ eyebrow }}</p>
    <h2>{{ title }}</h2>
    <p v-if="description" class="empty-state__description">{{ description }}</p>
    <div v-if="slots.primary || slots.secondary" class="empty-state__actions">
      <div v-if="slots.primary" data-test="empty-primary">
        <slot name="primary" />
      </div>
      <div v-if="slots.secondary" data-test="empty-secondary">
        <slot name="secondary" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.empty-state {
  max-width: 640px;
  padding-top: 20px;
  border-top: 2px solid var(--color-forest);
}

.empty-state__icon {
  color: var(--color-muted);
}

.empty-state__eyebrow {
  margin: 0 0 10px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.12em;
}

h2 {
  margin: 22px 0 10px;
  font-family: var(--font-editorial);
  font-size: 28px;
  font-weight: 500;
}

.empty-state__description {
  margin: 0;
  color: var(--color-muted-strong);
  font-size: 14px;
  line-height: 1.7;
}

.empty-state__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 20px;
}
</style>
