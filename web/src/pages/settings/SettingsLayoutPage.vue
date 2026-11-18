<script setup lang="ts">
import { settingsText as t } from '@/stores/locale'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import { provideSettingsDirtyState } from './settings-dirty-state'

interface SettingsNavigationItem {
  label: string
  path: string
  description: string
}

const navigation: readonly SettingsNavigationItem[] = [
  { label: '配置总览', path: '/settings/overview', description: '能力与待处理问题' },
  { label: 'AI 与模型', path: '/settings/agents', description: 'Codex 与 Ollama' },
  { label: '论文来源', path: '/settings/sources', description: '来源与质量证据' },
  { label: '阅读与语言', path: '/settings/reading', description: '报告、翻译与时区' },
  { label: '推荐与评分', path: '/settings/recommendation', description: '质量规则与评级目录' },
  { label: '存储与备份', path: '/settings/storage', description: '论文、模型与恢复' },
  { label: '扩展与发布', path: '/settings/extensions', description: '插件与精选边界' },
  { label: '关于与更新', path: '/settings/about', description: '版本、更新与诊断' },
]

const route = useRoute()
const router = useRouter()
const panel = ref<HTMLElement>()
const dirtyState = provideSettingsDirtyState()
const selectedPath = computed(() => route.path)

function confirmLeave(): boolean {
  if (!dirtyState.dirty.value) return true
  const accepted = window.confirm(t('当前设置尚未保存。确定离开并放弃这些修改吗？'))
  if (accepted) dirtyState.clearDirty()
  return accepted
}

function selectSection(event: Event): void {
  const path = (event.target as HTMLSelectElement).value
  if (path && path !== route.path) void router.push(path)
}

async function focusSectionHeading(): Promise<void> {
  await nextTick()
  panel.value?.querySelector<HTMLElement>('[data-settings-heading]')?.focus()
}

onBeforeRouteLeave(() => confirmLeave())
onBeforeRouteUpdate((to, from) => to.path === from.path || confirmLeave())
watch(() => route.path, () => void focusSectionHeading())
onMounted(() => void focusSectionHeading())
</script>

<template>
  <section class="settings-layout" aria-labelledby="settings-title">
    <header class="settings-header">
      <h1 id="settings-title">{{ t('设置') }}</h1>
      <span>{{ t('每个分区独立读取和保存。未打开的配置不会在后台请求数据。') }}</span>
    </header>

    <label class="settings-mobile-select">
      <span>{{ t('设置分类') }}</span>
      <select :value="selectedPath" :aria-label="t('设置分类')" @change="selectSection">
        <option v-for="item in navigation" :key="item.path" :value="item.path">
          {{ t(item.label) }}
        </option>
      </select>
    </label>

    <div class="settings-body">
      <nav :aria-label="t('设置分类')" class="settings-navigation">
        <RouterLink
          v-for="item in navigation"
          :key="item.path"
          :to="item.path"
          :title="t(item.description)"
        >
          <strong>{{ t(item.label) }}</strong>
        </RouterLink>
      </nav>

      <div ref="panel" class="settings-panel">
        <RouterView />
      </div>
    </div>
  </section>
</template>

<style scoped>
.settings-layout {
  width: min(1160px, 100%);
  margin: 0 auto;
}

.settings-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 52ch);
  gap: 8px 40px;
  align-items: end;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--color-line);
}

.settings-header p {
  grid-column: 1 / -1;
  margin: 0;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 750;
  letter-spacing: 0.13em;
}

.settings-header h1 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: clamp(34px, 5vw, 52px);
  font-weight: 500;
  letter-spacing: -0.035em;
}

.settings-header span {
  color: var(--color-muted-strong);
  line-height: 1.65;
}

.settings-body {
  display: grid;
  grid-template-columns: 210px minmax(0, 1fr);
  gap: clamp(32px, 5vw, 68px);
  align-items: start;
  padding-top: 34px;
}

.settings-navigation {
  position: sticky;
  top: 28px;
  display: grid;
  border-top: 1px solid var(--color-line);
}

.settings-navigation a {
  display: grid;
  gap: 3px;
  min-height: 54px;
  padding: 11px 10px;
  border-bottom: 1px solid var(--color-line);
  color: var(--color-muted-strong);
  text-decoration: none;
}

.settings-navigation a:hover {
  background: var(--color-forest-hover-soft);
  color: var(--color-forest);
}

.settings-navigation a:focus-visible,
.settings-mobile-select select:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

.settings-navigation a[aria-current='page'] {
  border-left: 3px solid var(--color-forest);
  background: var(--color-forest-soft);
  color: var(--color-forest);
}

.settings-navigation strong {
  font-size: 13px;
}

.settings-panel {
  min-width: 0;
}

.settings-mobile-select {
  display: none;
}

@media (max-width: 760px) {
  .settings-header {
    grid-template-columns: 1fr;
  }

  .settings-header span {
    max-width: 60ch;
  }

  .settings-body {
    display: block;
    padding-top: 24px;
  }

  .settings-navigation {
    display: none;
  }

  .settings-mobile-select {
    margin-top: 22px;
    display: grid;
    gap: 7px;
    color: var(--color-muted-strong);
    font-size: 12px;
    font-weight: 700;
  }

  .settings-mobile-select select {
    min-height: 44px;
    padding: 0 11px;
    border: 1px solid var(--color-line-strong);
    border-radius: var(--radius-sm);
    background: var(--color-paper-raised);
    color: var(--color-ink);
    font: inherit;
  }
}
</style>
