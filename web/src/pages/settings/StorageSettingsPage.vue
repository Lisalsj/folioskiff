<script setup lang="ts">
import { settingsText as t } from '@/stores/locale'
import BackupSettings from '@/components/BackupSettings.vue'
import ModelAssetSettings from '@/components/ModelAssetSettings.vue'
import ReaderStorageSettings from '@/components/ReaderStorageSettings.vue'
</script>

<template>
  <section class="settings-section" aria-labelledby="storage-settings-title">
    <header>
      <h2 id="storage-settings-title" data-settings-heading tabindex="-1">{{ t('存储与备份') }}</h2>
      <span>{{ t('模型、OCR、论文缓存与备份均在本机管理。系统凭据不会进入备份。') }}</span>
    </header>
    <div id="model-assets" class="section-anchor"><ModelAssetSettings /></div>
    <div id="reader-storage" class="section-anchor"><ReaderStorageSettings /></div>
    <div id="backups" class="section-anchor"><BackupSettings /></div>
  </section>
</template>

<style scoped>
.settings-section { display: grid; gap: 4px; }
header { padding-bottom: 16px; border-bottom: 2px solid var(--color-forest); }
header p { margin: 0 0 7px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: .12em; }
h2 { margin: 0; font-family: var(--font-editorial); font-size: 30px; font-weight: 500; }
h2:focus { outline: none; }
header span { display: block; margin-top: 9px; color: var(--color-muted-strong); line-height: 1.65; }
.section-anchor { min-width: 0; scroll-margin-top: 30px; }
</style>
