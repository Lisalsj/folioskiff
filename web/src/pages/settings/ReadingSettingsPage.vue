<script setup lang="ts">
import { settingsText as t, useLocale } from '@/stores/locale'
import GeneralSettings from '@/components/GeneralSettings.vue'
const { switchTo, language, toggleLocale } = useLocale()
</script>

<template>
  <section class="settings-section" aria-labelledby="reading-settings-title">
    <header>
      <h2 id="reading-settings-title" data-settings-heading tabindex="-1">{{ t('阅读与语言') }}</h2>
      <button class="quiet-button" type="button" :aria-label="language" @click="toggleLocale">{{ switchTo }}</button>
    </header>
    <GeneralSettings />
  </section>
</template>

<style scoped>
.settings-section { display: grid; gap: 4px; }
header { padding-bottom: 16px; border-bottom: 2px solid var(--color-forest); }
header p { margin: 0 0 7px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: .12em; }
h2 { margin: 0; font-family: var(--font-editorial); font-size: 30px; font-weight: 500; }
h2:focus { outline: none; }
header span { display: block; margin-top: 9px; color: var(--color-muted-strong); line-height: 1.65; }
</style>
