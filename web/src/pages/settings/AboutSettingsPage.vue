<script setup lang="ts">
import { messageRef, settingsText as t } from '@/stores/locale'
import { ref } from 'vue'

import type { components } from '@/api/schema'
import UpdateSettings from '@/components/UpdateSettings.vue'
import { apiJson } from '@/lib/json-api'

import { buildRedactedDiagnostic, type HealthSnapshot } from './redacted-diagnostics'

type OnboardingOverview = components['schemas']['OnboardingOverview']

const exporting = ref(false)
const exportError = messageRef(t)
const exportNotice = messageRef(t)

async function exportRedactedDiagnostics(): Promise<void> {
  if (exporting.value) return
  exporting.value = true
  exportError.value = ''
  exportNotice.value = ''
  try {
    const [health, overview] = await Promise.all([
      apiJson<HealthSnapshot>('/api/v1/health'),
      apiJson<OnboardingOverview>('/api/v1/onboarding'),
    ])
    const diagnostic = buildRedactedDiagnostic(health, overview, {
      platform: navigator.platform || 'unknown',
    })
    const blob = new Blob([`${JSON.stringify(diagnostic, null, 2)}\n`], {
      type: 'application/json;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    try {
      const link = document.createElement('a')
      link.href = url
      link.download = `paper-reader-diagnostics-${new Date().toISOString().slice(0, 10)}.json`
      link.click()
    } finally {
      URL.revokeObjectURL(url)
    }
    exportNotice.value = '脱敏诊断摘要已生成；它不会自动上传。'
  } catch {
    exportError.value = '暂时无法生成诊断摘要，请稍后重试。'
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <section class="settings-section" aria-labelledby="about-settings-title">
    <header>
      <h2 id="about-settings-title" data-settings-heading tabindex="-1">{{ t('关于与更新') }}</h2>
      <span>{{ t('页舟 FolioSkiff 使用 Apache-2.0 许可证。论文与笔记默认保存在本机。') }}</span>
    </header>

    <section class="project-facts" aria-labelledby="project-facts-title">
      <h3 id="project-facts-title">{{ t('项目与诊断') }}</h3>
      <dl>
        <div><dt>{{ t('代码仓库') }}</dt><dd><a href="https://github.com/Lisalsj/folioskiff" target="_blank" rel="noreferrer">Lisalsj/folioskiff</a></dd></div>
        <div><dt>{{ t('开源许可') }}</dt><dd><a href="https://www.apache.org/licenses/LICENSE-2.0" target="_blank" rel="noreferrer">Apache License 2.0</a></dd></div>
        <div>
          <dt>{{ t('诊断边界') }}</dt>
          <dd>
            <span>{{ t('诊断摘要只能手动导出并默认脱敏；当前页面不会自动上传日志。') }}</span>
            <button
              type="button"
              data-test="export-redacted-diagnostics"
              :disabled="exporting"
              @click="exportRedactedDiagnostics"
            >
              {{ exporting ? t('正在生成') : t('导出脱敏诊断摘要') }}
            </button>
            <small>{{ t('仅包含版本、契约与能力状态；不包含目录、凭据、论文、笔记、聊天、偏好或日志。') }}</small>
            <span v-if="exportNotice" class="export-notice" role="status">{{ exportNotice }}</span>
            <span v-if="exportError" class="export-error" role="alert">{{ exportError }}</span>
          </dd>
        </div>
      </dl>
    </section>

    <div id="updates" class="section-anchor"><UpdateSettings /></div>
  </section>
</template>

<style scoped>
.settings-section { display: grid; gap: 28px; }
header { padding-bottom: 16px; border-bottom: 2px solid var(--color-forest); }
header > p { margin: 0 0 7px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: .12em; }
h2 { margin: 0; font-family: var(--font-editorial); font-size: 30px; font-weight: 500; }
h2:focus { outline: none; }
header span { display: block; margin-top: 9px; color: var(--color-muted-strong); line-height: 1.65; }
.project-facts { padding-bottom: 18px; border-bottom: 1px solid var(--color-line); }
.project-facts h3 { margin: 0; font-family: var(--font-editorial); font-size: 21px; font-weight: 500; }
.project-facts dl { margin: 14px 0 0; display: grid; }
.project-facts dl div { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 18px; padding: 11px 0; border-top: 1px solid var(--color-line); }
.project-facts dt { color: var(--color-muted); font-size: var(--type-meta); font-weight: 700; }
.project-facts dd { margin: 0; color: var(--color-muted-strong); line-height: 1.55; }
.project-facts a { color: var(--color-forest); font-weight: 700; }
.project-facts dd > span, .project-facts dd > small { display: block; }
.project-facts dd > small { margin-top: 7px; color: var(--color-muted); }
.project-facts button { min-height: 40px; margin-top: 12px; padding: 0 13px; border: 1px solid var(--color-line-strong); border-radius: var(--radius-sm); background: var(--color-paper-raised); color: var(--color-ink); font: inherit; font-weight: 700; cursor: pointer; }
.project-facts button:disabled { opacity: .55; cursor: wait; }
.project-facts a:focus-visible, .project-facts button:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
.export-notice, .export-error { margin-top: 8px; font-size: 12px; }
.export-notice { color: var(--color-forest); }
.export-error { color: var(--color-wine); }
.section-anchor { scroll-margin-top: 30px; }
@media (max-width: 520px) { .project-facts dl div { grid-template-columns: 1fr; gap: 4px; } }
</style>
