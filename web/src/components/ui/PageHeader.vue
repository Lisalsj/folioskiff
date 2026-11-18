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
  <header class="page-header">
    <div class="page-header__copy">
      <p v-if="eyebrow" class="page-header__eyebrow">{{ eyebrow }}</p>
      <h1>{{ title }}</h1>
      <p v-if="description" class="page-header__description">{{ description }}</p>
    </div>

    <div v-if="slots.actions || slots.status" class="page-header__aside">
      <div v-if="slots.actions" data-test="page-header-actions" class="page-header__actions">
        <slot name="actions" />
      </div>
      <div v-if="slots.status" data-test="page-header-status" class="page-header__status">
        <slot name="status" />
      </div>
    </div>
  </header>
</template>

<style scoped>
.page-header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px 32px;
  padding: 19px 0 25px;
  border-block: 3px double var(--color-forest);
}

.page-header__copy {
  flex: 1 1 24rem;
  min-width: 0;
}

.page-header__eyebrow {
  margin: 0 0 10px;
  color: var(--color-forest);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.13em;
}

h1 {
  margin: 0 0 10px;
  font-family: var(--font-editorial);
  font-size: clamp(34px, 4vw, 56px);
  font-weight: 500;
  line-height: 1.12;
  letter-spacing: -0.035em;
}

.page-header__description {
  max-width: 660px;
  margin: 0;
  color: var(--color-muted);
  font-size: 14px;
  line-height: 1.7;
}

.page-header__aside,
.page-header__actions {
  display: flex;
  align-items: stretch;
  flex-wrap: wrap;
  max-width: 100%;
  gap: 12px;
}

.page-header__status {
  min-width: 130px;
}

@media (max-width: 640px) {
  .page-header {
    align-items: stretch;
    flex-direction: column;
  }

  .page-header__aside {
    align-items: stretch;
    flex-direction: column-reverse;
  }

  .page-header__copy { flex: 0 1 auto; }

  .page-header__actions {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
