<script setup lang="ts">
import { currentLocale, researchText as t } from '@/stores/locale'
import { PhArrowSquareOut, PhGlobe, PhSealCheck, PhWarning } from '@phosphor-icons/vue'

import type { KnowledgeResource } from '../api'

defineProps<{
  resources: KnowledgeResource[]
}>()

const typeLabels: Record<string, string> = {
  get official_docs() { return t('官方文档') },
  get review() { return t('综述与导读') },
  get course() { return t('课程') },
  get textbook() { return t('教材') },
  get database() { return t('数据库') },
}

function dateLabel(value?: string | null): string {
  return value ? new Date(value).toLocaleDateString(currentLocale()) : t('访问时间未知')
}

function safeExternalUrl(resource: KnowledgeResource): string | undefined {
  if (resource.verification_status !== 'verified') return undefined
  try {
    const url = new URL(resource.final_url)
    return url.protocol === 'https:' ? url.href : undefined
  } catch {
    return undefined
  }
}
</script>

<template>
  <section class="knowledge-list" aria-live="polite">
    <p class="boundary"><PhGlobe :size="14" aria-hidden="true" /> {{ t('知识网站，不是论文；打开后将离开本地应用。') }}</p>
    <article v-for="resource in resources" :key="resource.id" class="resource-row">
      <div>
        <span>{{ typeLabels[resource.resource_type] ?? resource.resource_type }}</span>
        <span :class="resource.verification_status">
          <PhSealCheck v-if="resource.verification_status === 'verified'" :size="12" aria-hidden="true" />
          <PhWarning v-else :size="12" aria-hidden="true" />
          {{ resource.verification_status === 'verified' ? t('链接已核验') : t('链接待核验') }}
        </span>
      </div>
      <h4>
        <a
          v-if="safeExternalUrl(resource)"
          :href="safeExternalUrl(resource)"
          target="_blank"
          rel="noopener noreferrer"
        >
          {{ resource.title }} <PhArrowSquareOut :size="13" aria-hidden="true" />
        </a>
        <span v-else>{{ resource.title }}</span>
      </h4>
      <p>{{ resource.reason }}</p>
      <footer>
        <span>{{ resource.domain }}</span>
        <span v-if="resource.publisher">{{ resource.publisher }}</span>
        <span>{{ dateLabel(resource.accessed_at) }}</span>
      </footer>
    </article>
    <p v-if="!resources.length" class="empty">{{ t('本次没有启用外部研究，或尚未找到已核验的知识资源。') }}</p>
  </section>
</template>

<style scoped>
.knowledge-list { display: grid; }
.boundary { display: flex; align-items: center; gap: 6px; margin: 0 0 8px; padding: 8px 0; color: var(--color-wine); font-size: var(--type-meta); }
.resource-row { padding: 14px 0; border-top: 1px solid var(--color-line); }
.resource-row > div, .resource-row > div span, .resource-row h4 a, .resource-row footer { display: flex; align-items: center; }
.resource-row > div { justify-content: space-between; gap: 10px; color: var(--color-muted); font-size: var(--type-meta); }
.resource-row > div span { gap: 4px; }
.resource-row > div .verified { color: var(--color-forest); }
.resource-row h4 { margin: 7px 0 0; font-size: 14px; }
.resource-row h4 a { gap: 5px; color: var(--color-ink); text-decoration: underline; text-decoration-color: var(--color-line-strong); text-underline-offset: 3px; }
.resource-row h4 > span { color: var(--color-muted-strong); }
.resource-row > p { margin: 6px 0 0; color: var(--color-muted-strong); font-size: var(--type-meta); line-height: 1.5; }
.resource-row footer { flex-wrap: wrap; gap: 8px 14px; margin-top: 8px; color: var(--color-muted); font-size: var(--type-meta); }
.empty { margin: 0; padding: 24px 10px; color: var(--color-muted); font-size: var(--type-meta); text-align: center; }
a:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
</style>
