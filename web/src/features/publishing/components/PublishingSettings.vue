<script setup lang="ts">
import { publishingText as t } from '@/stores/locale'
import { ref } from 'vue'

defineProps<{
  slug: string
  title: string
  intro: string
}>()

const emit = defineEmits<{
  'update:slug': [value: string]
  'update:title': [value: string]
  'update:intro': [value: string]
  change: []
}>()

const heading = ref<HTMLElement>()

function update(field: 'slug' | 'title' | 'intro', event: Event): void {
  const value = (event.currentTarget as HTMLInputElement | HTMLTextAreaElement).value
  if (field === 'slug') emit('update:slug', value)
  else if (field === 'title') emit('update:title', value)
  else emit('update:intro', value)
  emit('change')
}

function focusHeading(): void {
  heading.value?.focus()
}

defineExpose({ focusHeading })
</script>

<template>
  <section class="publishing-settings" aria-labelledby="publication-composer-title">
    <div class="section-heading">
      <div>
        <span aria-hidden="true">01</span>
        <div>
          <p>{{ t('公开说明') }}</p>
          <h2
            id="publication-composer-title"
            ref="heading"
            data-test="publishing-composer-heading"
            tabindex="-1"
          >{{ t('为这份书单命名') }}</h2>
        </div>
      </div>
      <small>{{ t('所有内容会在下一步逐项预览') }}</small>
    </div>

    <div class="copy-fields">
      <label>
        <span>{{ t('公开地址') }}</span>
        <div class="slug-field">
          <span>/</span>
          <input
            :value="slug"
            data-test="publishing-slug"
            type="text"
            maxlength="80"
            autocomplete="off"
            @input="update('slug', $event)"
          />
        </div>
        <small>{{ t('仅使用小写字母、数字和连字符。') }}</small>
      </label>
      <label>
        <span>{{ t('公开标题') }}</span>
        <input
          :value="title"
          data-test="publishing-title"
          type="text"
          maxlength="200"
          @input="update('title', $event)"
        />
      </label>
      <label class="intro-field">
        <span>{{ t('公开导语') }} <small>{{ t('可选') }}</small></span>
        <textarea
          :value="intro"
          data-test="publishing-intro"
          rows="3"
          maxlength="4000"
          @input="update('intro', $event)"
        />
      </label>
    </div>
  </section>
</template>

<style scoped>
.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 20px;
}

.section-heading > div {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.section-heading > div > span {
  padding-top: 3px;
  color: var(--color-wine);
  font-family: var(--font-editorial);
  font-size: 13px;
}

.section-heading p {
  margin: 0 0 8px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 750;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.section-heading h2 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: 26px;
  font-weight: 500;
}

.section-heading small {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.copy-fields {
  display: grid;
  grid-template-columns: minmax(210px, 0.65fr) minmax(260px, 1fr);
  gap: 18px 22px;
  padding: 23px 0 28px;
  border-block: 1px solid var(--color-line);
}

.copy-fields label {
  display: grid;
  gap: 7px;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  font-weight: 650;
}

.copy-fields label > small,
.copy-fields label > span small {
  color: var(--color-muted);
  font-size: var(--type-meta);
  font-weight: 400;
}

.copy-fields input,
.copy-fields textarea {
  width: 100%;
  padding: 10px 11px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-raised);
  color: var(--color-ink);
}

.copy-fields textarea {
  resize: vertical;
  line-height: 1.65;
}

.intro-field {
  grid-column: 1 / -1;
}

.slug-field {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-raised);
}

.slug-field > span {
  padding-left: 11px;
  color: var(--color-muted);
}

.slug-field input {
  border: 0;
  background: transparent;
}

input:focus-visible,
textarea:focus-visible,
h2:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

@media (max-width: 680px) {
  .copy-fields {
    grid-template-columns: 1fr;
  }

  .intro-field {
    grid-column: auto;
  }

  .section-heading {
    align-items: flex-start;
  }
}
</style>
