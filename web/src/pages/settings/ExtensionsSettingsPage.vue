<script setup lang="ts">
import { settingsText as t } from '@/stores/locale'
import PluginSettings from '@/components/PluginSettings.vue'
</script>

<template>
  <section class="settings-section" aria-labelledby="extensions-settings-title">
    <header>
      <h2 id="extensions-settings-title" data-settings-heading tabindex="-1">{{ t('扩展与发布') }}</h2>
      <span>{{ t('插件默认没有网络、文件或凭据权限；精选发布只包含你明确勾选的白名单内容。') }}</span>
    </header>

    <section class="publishing-boundary" aria-labelledby="publishing-boundary-title">
      <div>
        <h3 id="publishing-boundary-title">{{ t('精选发布边界') }}</h3>
        <p>{{ t('PDF、私有笔记、聊天、行为数据、凭据和完整提示词不会进入 published 分支。') }}</p>
      </div>
      <RouterLink to="/publishing">{{ t('打开精选发布') }}</RouterLink>
    </section>

    <PluginSettings />
  </section>
</template>

<style scoped>
.settings-section { display: grid; gap: 28px; }
header { padding-bottom: 16px; border-bottom: 2px solid var(--color-forest); }
header > p { margin: 0 0 7px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: .12em; }
h2 { margin: 0; font-family: var(--font-editorial); font-size: 30px; font-weight: 500; }
h2:focus { outline: none; }
header span, .publishing-boundary p { display: block; margin: 9px 0 0; color: var(--color-muted-strong); line-height: 1.65; }
.publishing-boundary { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 18px 0; border-bottom: 1px solid var(--color-line); }
.publishing-boundary h3 { margin: 0; font-family: var(--font-editorial); font-size: 21px; font-weight: 500; }
.publishing-boundary a { flex: 0 0 auto; color: var(--color-forest); font-weight: 700; }
.publishing-boundary a:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
@media (max-width: 620px) { .publishing-boundary { align-items: flex-start; flex-direction: column; } }
</style>
