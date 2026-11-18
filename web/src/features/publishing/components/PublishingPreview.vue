<script setup lang="ts">
import { publishingText as t } from '@/stores/locale'
import { ref } from 'vue'
import {
  PhBroadcast,
  PhCheckCircle,
  PhGitBranch,
  PhLockKey,
  PhWarning,
} from '@phosphor-icons/vue'

import type { components } from '@/api/schema'

type PreviewEnvelope = components['schemas']['PublishingPreviewEnvelope']

defineProps<{
  envelope: PreviewEnvelope
  publishing: boolean
}>()

defineEmits<{
  discard: []
  'open-confirmation': []
}>()

const heading = ref<HTMLElement>()

function fieldLabel(value: string): string {
  const labels: Record<string, string> = {
    'curated title and public introduction': t('精选标题与公开导语'),
    'canonical paper title, authors, abstract, DOI and publication date': t('论文标题、作者、摘要、DOI 与发表日期'),
    'latest objective quality score summary when available': t('可用时附上最新客观质量摘要'),
    'explicitly selected formal analysis report when available': t('仅附上明确选中的正式分析报告'),
    'PDF and extracted full text': t('PDF 与提取的全文'),
    'annotations, private notes and reading progress': t('批注、私有笔记与阅读进度'),
    'paper conversations and preference events': t('论文聊天与偏好事件'),
    credentials: t('凭据'),
    'complete prompts, failure drafts and model diagnostics': t('完整提示词、失败草稿与模型诊断'),
    'private score evidence and source raw payloads': t('私有评分证据与来源原始响应'),
    'local filesystem paths': t('本地文件系统路径'),
  }
  return labels[value] ?? value
}

function focusHeading(): void {
  heading.value?.focus()
}

defineExpose({ focusHeading })
</script>

<template>
  <section data-test="publishing-preview" class="publication-preview" aria-labelledby="publication-preview-title">
    <header class="preview-header">
      <div>
        <p class="eyebrow">{{ t('尚未创建快照 · 尚未运行 Git') }}</p>
        <h2 id="publication-preview-title" ref="heading" data-test="publishing-preview-heading" tabindex="-1">{{ t('公开预览') }}</h2>
        <p>{{ t('这是即将被冻结的完整公开边界。返回修改会直接丢弃本次预览。') }}</p>
      </div>
      <button data-test="discard-publishing-preview" class="secondary-action" type="button" :disabled="publishing" @click="$emit('discard')">{{ t('返回修改') }}</button>
    </header>

    <div class="public-copy-preview">
      <span class="technical-value">/{{ envelope.preview.payload.slug }}</span>
      <h3>{{ envelope.preview.payload.title }}</h3>
      <p>{{ envelope.preview.payload.public_intro || t('未填写公开导语。') }}</p>
    </div>

    <div class="preview-columns">
      <section aria-labelledby="preview-works-title">
        <div class="section-heading compact">
          <div><span aria-hidden="true">01</span><div><p>{{ t('顺序固定') }}</p><h3 id="preview-works-title">{{ t('公开论文') }}</h3></div></div>
          <small>{{ envelope.preview.payload.works.length }} {{ t('篇') }}</small>
        </div>
        <ol class="preview-work-list">
          <li v-for="(work, index) in envelope.preview.payload.works" :key="work.canonical_work_id">
            <span>{{ String(index + 1).padStart(2, '0') }}</span>
            <div>
              <strong>{{ work.title }}</strong>
              <small>{{ work.authors.join('、') || t('作者未知') }}</small>
              <em>{{ work.report ? t('正式报告 v{0} · {1}', work.report.version, work.report.evidence_level === 'full_text' ? t('全文证据') : t('摘要证据')) : t('不附分析报告') }}</em>
            </div>
          </li>
        </ol>
      </section>

      <section class="field-boundary" aria-labelledby="field-boundary-title">
        <div class="section-heading compact">
          <div><span aria-hidden="true">02</span><div><p>{{ t('白名单') }}</p><h3 id="field-boundary-title">{{ t('字段边界') }}</h3></div></div>
        </div>
        <h4>{{ t('会公开') }}</h4>
        <ul class="boundary-list included">
          <li v-for="field in envelope.included_fields" :key="field">
            <PhCheckCircle :size="16" aria-hidden="true" />
            <span><strong>{{ fieldLabel(field) }}</strong><small class="technical-value">{{ field }}</small></span>
          </li>
        </ul>
        <h4>{{ t('明确排除') }}</h4>
        <ul class="boundary-list excluded">
          <li v-for="field in envelope.excluded_fields" :key="field">
            <PhLockKey :size="16" aria-hidden="true" />
            <span><strong>{{ fieldLabel(field) }}</strong><small class="technical-value">{{ field }}</small></span>
          </li>
        </ul>
      </section>
    </div>

    <div class="target-review">
      <section aria-labelledby="file-manifest-title">
        <h3 id="file-manifest-title">{{ t('确切文件') }}</h3>
        <ul><li v-for="file in envelope.files" :key="file" class="technical-value">{{ file }}</li></ul>
      </section>
      <section aria-labelledby="git-target-title">
        <h3 id="git-target-title">{{ t('Git 与 Pages 目标') }}</h3>
        <dl>
          <div><dt>{{ t('本地仓库') }}</dt><dd class="technical-value">{{ envelope.git.repository_root || t('未检测到') }}</dd></div>
          <div><dt>{{ t('远端') }}</dt><dd class="technical-value">{{ envelope.git.remote_name }} · {{ envelope.git.remote_url || t('未配置') }}</dd></div>
          <div><dt>{{ t('唯一分支') }}</dt><dd class="technical-value">{{ envelope.git.branch }}</dd></div>
          <div><dt>GitHub Pages</dt><dd class="technical-value">{{ envelope.git.workflow_path }} → {{ envelope.git.site_root }}/</dd></div>
        </dl>
      </section>
    </div>

    <p v-if="!envelope.git.enabled" class="message error" role="alert">
      <PhWarning :size="17" aria-hidden="true" />
      {{ t('Git 发布尚不可用。可以继续核对预览，但不能创建快照或运行命令。') }}
    </p>

    <div class="publish-action">
      <div>
        <PhGitBranch :size="22" aria-hidden="true" />
        <p><strong>{{ t('最后一步才会冻结快照。') }}</strong>{{ t('主工作树不会被修改；只允许向') }} <span class="technical-value">published</span> {{ t('分支快进推送。') }}</p>
      </div>
      <button
        data-test="open-publish-confirmation"
        class="primary-action wine"
        type="button"
        :disabled="publishing || !envelope.git.enabled || !envelope.git.target_fingerprint"
        @click="$emit('open-confirmation')"
      ><PhBroadcast :size="18" aria-hidden="true" />{{ t('核对并发布') }}</button>
    </div>
  </section>
</template>

<style scoped>
.publication-preview { margin-top: clamp(38px, 6vw, 68px); }
.preview-header,
.section-heading,
.publish-action { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; }
.preview-header { align-items: flex-end; padding-bottom: 24px; border-bottom: 1px solid var(--color-line); }
.eyebrow,
.section-heading p { margin: 0 0 8px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: 0.14em; text-transform: uppercase; }
.preview-header h2,
.public-copy-preview h3,
.section-heading h3,
.target-review h3 { font-family: var(--font-editorial); font-weight: 500; }
.preview-header h2 { margin: 0 0 8px; font-size: clamp(32px, 4vw, 44px); }
.preview-header p:last-child { margin: 0; color: var(--color-muted); font-size: 12px; }
.secondary-action,
.primary-action { min-height: 42px; display: inline-flex; align-items: center; justify-content: center; gap: 8px; padding: 0 17px; border-radius: var(--radius-sm); font-weight: 650; cursor: pointer; }
.secondary-action { border: 1px solid var(--color-forest); background: transparent; color: var(--color-forest); }
.primary-action { border: 1px solid var(--color-wine); background: var(--color-wine); color: #fffdf7; }
.secondary-action:disabled,
.primary-action:disabled { opacity: 0.45; cursor: default; }
.public-copy-preview { max-width: 780px; padding: 32px 0 8px; }
.public-copy-preview > span { color: var(--color-wine); font-size: var(--type-meta); }
.public-copy-preview h3 { margin: 10px 0; font-size: 30px; }
.public-copy-preview p { margin: 0; color: var(--color-muted); line-height: 1.75; }
.preview-columns,
.target-review { display: grid; grid-template-columns: minmax(0, 0.9fr) minmax(360px, 1.1fr); gap: clamp(30px, 5vw, 58px); margin-top: 48px; }
.section-heading { align-items: center; margin-bottom: 14px; }
.section-heading > div { display: flex; align-items: flex-start; gap: 12px; }
.section-heading > div > span { padding-top: 3px; color: var(--color-wine); font-family: var(--font-editorial); font-size: 13px; }
.section-heading h3 { margin: 0; font-size: 22px; }
.section-heading small { color: var(--color-muted); font-size: var(--type-meta); }
.preview-work-list,
.boundary-list,
.target-review ul { padding: 0; margin: 0; list-style: none; }
.preview-work-list { border-block: 1px solid var(--color-line); }
.preview-work-list li { display: grid; grid-template-columns: 30px minmax(0, 1fr); gap: 10px; padding: 14px 4px; }
.preview-work-list li + li { border-top: 1px solid var(--color-line); }
.preview-work-list > li > span { color: var(--color-wine); font-family: var(--font-editorial); font-size: 12px; }
.preview-work-list strong,
.preview-work-list small,
.preview-work-list em { display: block; }
.preview-work-list strong { font-family: var(--font-editorial); font-size: 16px; font-weight: 600; line-height: 1.35; }
.preview-work-list small { margin-top: 5px; color: var(--color-muted); }
.preview-work-list em { margin-top: 8px; color: var(--color-forest); font-size: var(--type-meta); font-style: normal; font-weight: 650; }
.field-boundary h4 { margin: 20px 0 9px; color: var(--color-muted-strong); font-size: var(--type-meta); letter-spacing: 0.04em; }
.boundary-list { border-top: 1px solid var(--color-line); }
.boundary-list li { display: grid; grid-template-columns: 18px minmax(0, 1fr); gap: 8px; padding: 8px 3px; border-bottom: 1px solid var(--color-line); font-size: var(--type-meta); line-height: 1.55; }
.boundary-list.included svg { color: var(--color-forest); }
.boundary-list.excluded svg { color: var(--color-wine); }
.boundary-list strong,
.boundary-list small { display: block; }
.boundary-list strong { font-size: var(--type-meta); font-weight: 600; }
.boundary-list small { margin-top: 3px; color: var(--color-muted); font-size: var(--type-meta); font-weight: 400; }
.target-review { grid-template-columns: minmax(250px, 0.7fr) minmax(0, 1.3fr); padding: 28px 0; border-block: 1px solid var(--color-line); }
.target-review h3 { margin-bottom: 14px; font-size: 18px; }
.target-review li { padding: 7px 0; border-bottom: 1px solid var(--color-line); font-size: var(--type-meta); }
.target-review dl { margin: 0; }
.target-review dl > div { display: grid; grid-template-columns: 108px minmax(0, 1fr); gap: 12px; padding: 7px 0; border-bottom: 1px solid var(--color-line); }
.target-review dt { color: var(--color-muted); font-size: var(--type-meta); }
.target-review dd { min-width: 0; margin: 0; font-size: var(--type-meta); }
.message { display: flex; align-items: center; gap: 8px; margin: 20px 0 0; padding: 12px 14px; border-left: 3px solid var(--color-danger); background: var(--color-wine-soft); font-size: 13px; line-height: 1.6; }
.publish-action { align-items: center; margin-top: 30px; padding-top: 22px; border-top: 1px solid var(--color-line); }
.publish-action > div { display: flex; align-items: flex-start; gap: 12px; }
.publish-action > div > svg { flex: 0 0 auto; color: var(--color-forest); }
.publish-action p { max-width: 660px; margin: 0; color: var(--color-muted); font-size: var(--type-meta); line-height: 1.7; }
.publish-action strong { display: block; color: var(--color-ink); font-size: 12px; }
button:focus-visible,
h2:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
@media (max-width: 980px) { .preview-columns { grid-template-columns: 1fr; } .target-review { grid-template-columns: 1fr 1.3fr; } }
@media (max-width: 680px) {
  .preview-header,
  .publish-action { align-items: flex-start; flex-direction: column; }
  .target-review { grid-template-columns: 1fr; }
  .primary-action,
  .secondary-action { width: 100%; }
  .target-review dl > div { grid-template-columns: 82px minmax(0, 1fr); }
}
</style>
