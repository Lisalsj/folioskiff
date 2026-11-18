<script setup lang="ts">
import { shellText as t } from '@/stores/locale'
import { PhCheckCircle, PhClock, PhSpinnerGap, PhTray, PhX } from '@phosphor-icons/vue'
import { storeToRefs } from 'pinia'
import {
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
  DialogTrigger,
} from 'reka-ui'

import { useJobStore } from '@/stores/jobs'

const store = useJobStore()
const { jobs, activeCount, connection } = storeToRefs(store)

function jobIcon(status: string) {
  if (['completed', 'succeeded'].includes(status)) return PhCheckCircle
  if (status === 'running') return PhSpinnerGap
  return PhClock
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: t('等待中'),
    running: t('进行中'),
    completed: t('已完成'),
    succeeded: t('已完成'),
    failed: t('失败'),
    paused: t('已暂停'),
    cancelled: t('已取消'),
    pending_restart: t('等待重启'),
  }
  return labels[status] ?? status
}

function kindLabel(kind: string): string {
  const labels: Record<string, string> = {
    'reader.acquire_document': t('获取论文全文'),
    'reader.extract_text': t('提取论文文字'),
    'reader.ocr_models': t('下载 OCR 模型'),
    'reader.ocr_document': t('识别扫描页面'),
    'models.install': t('下载模型资源'),
    'discovery.preview': t('预览来源结果'),
    'discovery.run': t('运行关注计划'),
    'metadata.enrich': t('补充质量证据'),
    'analysis.report': t('生成论文报告'),
    'translation.generate': t('翻译论文'),
    'conversation.reply': t('回答论文问题'),
    'comparison.generate': t('生成多论文比较'),
    'backup.create': t('创建便携备份'),
    'backup.restore.validate': t('验证恢复包'),
    'backup.restore.confirm': t('等待重启恢复'),
    'publishing.publish': t('发布精选内容'),
  }
  return labels[kind] ?? kind
}

function connectionLabel(status: string): string {
  const labels: Record<string, string> = {
    connected: t('实时更新'),
    polling: t('定时更新'),
    error: t('会话未连接'),
    idle: t('本地状态'),
  }
  return labels[status] ?? t('本地状态')
}
</script>

<template>
  <DialogRoot>
    <DialogTrigger as-child>
      <button type="button" class="drawer-trigger" :aria-label="t('查看任务进度')">
        <PhTray :size="20" weight="regular" aria-hidden="true" />
        <span v-if="activeCount" aria-hidden="true">{{ activeCount }}</span>
      </button>
    </DialogTrigger>

    <DialogPortal>
      <DialogOverlay class="drawer-overlay" />
      <DialogContent class="drawer-content">
        <header>
          <div>
            <p>{{ t('本地任务') }}</p>
            <DialogTitle>{{ t('任务进度') }}</DialogTitle>
            <DialogDescription class="drawer-description">
              {{ t('抓取、下载、OCR、评分、分析、翻译、备份、恢复与发布都会在这里保留进度与恢复点。') }}
            </DialogDescription>
          </div>
          <DialogClose as-child>
            <button type="button" class="close-button" :aria-label="t('关闭任务进度')">
              <PhX :size="19" aria-hidden="true" />
            </button>
          </DialogClose>
        </header>

        <div class="connection-state" aria-live="polite">
          <span :class="connection" aria-hidden="true" />
          {{ connectionLabel(connection) }}
        </div>

        <div v-if="jobs.length === 0" class="empty-jobs">
          <PhTray :size="25" weight="thin" aria-hidden="true" />
          <h2>{{ t('当前没有任务') }}</h2>
          <p>{{ t('运行关注计划或点击论文分析后，进度会出现在这里。') }}</p>
        </div>

        <ol v-else class="job-list">
          <li v-for="job in jobs" :key="job.id">
            <component
              :is="jobIcon(job.status)"
              :size="19"
              :class="{ spinning: job.status === 'running' }"
              aria-hidden="true"
            />
            <div>
              <div class="job-heading">
                <strong>{{ kindLabel(job.kind) }}</strong>
                <span>{{ statusLabel(job.status) }}</span>
              </div>
              <progress :value="job.progress" max="100">
                {{ job.progress }}%
              </progress>
              <div class="job-meta">
                <span>{{ job.progress }}%</span>
                <div class="job-actions">
                  <button
                    v-if="job.kind === 'models.install' && ['queued', 'running'].includes(job.status) && !job.pause_requested && !job.cancel_requested"
                    :data-test="`pause-${job.id}`"
                    class="model-control"
                    type="button"
                    @click="store.requestModelPause(job.id)"
                  >
                    {{ t('暂停') }}
                  </button>
                  <button
                    v-if="job.kind === 'models.install' && job.status === 'paused' && !job.cancel_requested"
                    :data-test="`resume-${job.id}`"
                    class="model-control"
                    type="button"
                    @click="store.requestModelResume(job.id)"
                  >
                    {{ t('继续') }}
                  </button>
                  <button
                    v-if="['queued', 'running', 'paused'].includes(job.status) && !job.cancel_requested"
                    type="button"
                    class="cancel-control"
                    @click="store.requestCancel(job.id)"
                  >
                    {{ t('取消') }}
                  </button>
                  <span
                    v-else-if="['queued', 'running', 'paused'].includes(job.status) && job.cancel_requested"
                  >{{ t('正在取消') }}</span>
                </div>
              </div>
            </div>
          </li>
        </ol>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style scoped>
.drawer-trigger {
  position: fixed;
  z-index: 8;
  top: 22px;
  right: 24px;
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper);
  color: var(--color-muted-strong);
  cursor: pointer;
}

.drawer-trigger:hover {
  color: var(--color-forest);
  border-color: var(--color-forest);
}

.drawer-trigger > span {
  position: absolute;
  top: -5px;
  right: -5px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  display: grid;
  place-items: center;
  border: 2px solid var(--color-paper);
  border-radius: 9px;
  background: var(--color-forest);
  color: var(--color-on-accent);
  font-size: var(--type-meta);
  font-weight: 700;
}

.drawer-trigger:focus-visible,
.close-button:focus-visible,
.job-meta button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

.drawer-overlay {
  position: fixed;
  z-index: 20;
  inset: 0;
  background: rgb(10 22 15 / 32%);
  animation: fade-in 160ms ease-out;
}

.drawer-content {
  position: fixed;
  z-index: 21;
  inset: 0 0 0 auto;
  width: min(430px, 92vw);
  padding: 30px;
  overflow-y: auto;
  border-left: 1px solid var(--color-line);
  background: var(--color-paper);
  color: var(--color-ink);
  box-shadow: -18px 0 48px rgb(16 28 20 / 14%);
  animation: slide-in 180ms ease-out;
}

.drawer-content > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--color-line);
}

.drawer-content header p {
  margin: 0 0 8px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.13em;
}

.drawer-content header h2 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: 30px;
  font-weight: 500;
}

.drawer-description {
  display: block;
  max-width: 320px;
  margin-top: 10px;
  color: var(--color-muted);
  font-size: 12px;
  line-height: 1.65;
}

.close-button {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
}

.close-button:hover {
  background: var(--color-forest-hover-soft);
  color: var(--color-forest);
}

.connection-state {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 18px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.connection-state span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-muted);
}

.connection-state span.connected,
.connection-state span.polling {
  background: var(--color-forest);
}

.connection-state span.error {
  background: var(--color-danger);
}

.empty-jobs {
  margin-top: 80px;
  padding-top: 22px;
  border-top: 2px solid var(--color-forest);
}

.empty-jobs h2 {
  margin: 24px 0 9px;
  font-family: var(--font-editorial);
  font-size: 24px;
  font-weight: 500;
}

.empty-jobs p {
  max-width: 310px;
  margin: 0;
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.7;
}

.job-list {
  margin: 28px 0 0;
  padding: 0;
  list-style: none;
}

.job-list li {
  display: grid;
  grid-template-columns: 22px 1fr;
  gap: 12px;
  padding: 18px 0;
  border-bottom: 1px solid var(--color-line);
}

.job-heading,
.job-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.job-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.job-heading strong {
  font-size: 13px;
  font-weight: 650;
}

.job-heading span,
.job-meta {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

progress {
  width: 100%;
  height: 3px;
  margin: 14px 0 9px;
  border: 0;
  background: var(--color-line);
}

progress::-webkit-progress-bar {
  background: var(--color-line);
}

progress::-webkit-progress-value,
progress::-moz-progress-bar {
  background: var(--color-forest);
}

.job-meta button {
  padding: 2px 0;
  border: 0;
  background: transparent;
  color: var(--color-forest);
  font-size: var(--type-meta);
  cursor: pointer;
}

.job-meta .cancel-control {
  color: var(--color-danger);
}

.spinning {
  animation: spin 900ms linear infinite;
}

@keyframes fade-in {
  from { opacity: 0; }
}

@keyframes slide-in {
  from { transform: translateX(24px); opacity: 0; }
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 640px) {
  .drawer-trigger {
    top: 14px;
    right: 14px;
  }

  .drawer-content {
    width: 100%;
    padding: 24px 20px 96px;
  }
}
</style>
