<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import { PhBooks, PhFileText, PhX } from '@phosphor-icons/vue'

import type { components } from '@/api/schema'

type LibraryDocument = components['schemas']['LibraryDocumentResponse']
type LibraryWork = components['schemas']['LibraryWorkResponse']

defineProps<{
  work: LibraryWork
  documents: LibraryDocument[]
  selectedDocumentId: string
  open: boolean
  compact: boolean
}>()

const emit = defineEmits<{
  close: []
  'select-document': [documentId: string]
}>()

function versionLabel(document: LibraryDocument): string {
  if (document.original_filename) return document.original_filename
  return t('版本 {0}', document.content_sha256.slice(0, 8))
}

function accessLabel(document: LibraryDocument): string {
  if (!document.available) return t('仅元数据')
  if (document.access_status === 'user_provided') return t('用户导入')
  return t('开放获取')
}

function fileSize(value: number): string {
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}
</script>

<template>
  <aside
    id="reader-library-column"
    class="library-column"
    :class="{ open }"
    :aria-hidden="compact && !open"
    :inert="compact && !open ? true : undefined"
  >
    <header>
      <div>
        <span>{{ t('论文与版本') }}</span>
        <strong>{{ documents.length }} {{ t('个记录') }}</strong>
      </div>
      <button type="button" :aria-label="t('关闭论文与版本列表')" @click="emit('close')">
        <PhX :size="17" aria-hidden="true" />
      </button>
    </header>

    <section class="work-summary" :aria-label="t('论文摘要')">
      <PhBooks :size="19" aria-hidden="true" />
      <p>{{ work.abstract || t('这篇论文暂时没有可用摘要。') }}</p>
      <span v-if="work.doi">DOI {{ work.doi }}</span>
    </section>

    <nav class="version-list" :aria-label="t('论文版本')">
      <a
        v-for="document in documents"
        :key="document.id"
        :href="`/library/${work.id}/read/${document.id}`"
        :class="{ active: document.id === selectedDocumentId }"
        :aria-current="document.id === selectedDocumentId ? 'page' : undefined"
        @click.prevent="$emit('select-document', document.id)"
      >
        <PhFileText :size="17" aria-hidden="true" />
        <span>
          <strong>{{ versionLabel(document) }}</strong>
          <small>{{ accessLabel(document) }} · {{ fileSize(document.byte_size) }}</small>
        </span>
      </a>
    </nav>
  </aside>

  <button
    v-if="open && compact"
    type="button"
    class="drawer-scrim"
    :aria-label="t('关闭论文与版本列表')"
    @click="emit('close')"
  />
</template>

<style scoped>
.library-column {
  min-width: 0;
  min-height: 0;
  overflow: auto;
  border-right: 1px solid var(--color-line);
  background: var(--color-paper-deep);
}

.library-column > header {
  height: 54px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 14px;
  border-bottom: 1px solid var(--color-line);
}

.library-column > header span,
.library-column > header strong {
  display: block;
}

.library-column > header span {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.library-column > header strong {
  margin-top: 2px;
  font-family: var(--font-editorial);
  font-size: 14px;
  font-weight: 500;
}

.library-column > header button {
  display: none;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted-strong);
  cursor: pointer;
}

.library-column > header button:hover {
  background: var(--color-forest-hover-soft);
  color: var(--color-forest);
}

.library-column > header button:focus-visible,
.version-list a:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.work-summary {
  padding: 18px 14px;
  border-bottom: 1px solid var(--color-line);
}

.work-summary > svg {
  color: var(--color-forest);
}

.work-summary p {
  display: -webkit-box;
  margin: 12px 0 9px;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 5;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  line-height: 1.65;
}

.work-summary span {
  color: var(--color-muted);
  font-size: var(--type-meta);
  word-break: break-all;
}

.version-list {
  display: grid;
}

.version-list a {
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 13px 14px;
  border-bottom: 1px solid var(--color-line);
  color: var(--color-muted-strong);
  text-decoration: none;
}

.version-list a:hover {
  background: var(--color-forest-hover-soft);
}

.version-list a.active {
  box-shadow: inset 3px 0 0 var(--color-forest);
  background: var(--color-forest-soft);
  color: var(--color-forest);
}

.version-list a > svg {
  flex: none;
  margin-top: 1px;
}

.version-list a > span {
  min-width: 0;
}

.version-list strong,
.version-list small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.version-list strong {
  font-size: var(--type-meta);
}

.version-list small {
  margin-top: 4px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.version-list a.active small {
  color: var(--color-muted-strong);
}

.drawer-scrim {
  position: absolute;
  z-index: 5;
  inset: 0;
  display: block;
  border: 0;
  background: rgb(18 25 19 / 34%);
}

@media (max-width: 1280px) {
  .library-column {
    position: absolute;
    z-index: 6;
    inset: 0 auto 0 0;
    width: min(320px, calc(100% - 48px));
    transform: translateX(-102%);
    box-shadow: 12px 0 30px rgb(30 41 34 / 18%);
    transition: transform 160ms ease;
  }

  .library-column.open {
    transform: translateX(0);
  }

  .library-column > header button {
    display: grid;
    place-items: center;
  }
}

@media (max-width: 760px) {
  .library-column {
    width: min(340px, calc(100% - 28px));
  }
}

@media (prefers-reduced-motion: reduce) {
  .library-column {
    transition: none;
  }
}
</style>
