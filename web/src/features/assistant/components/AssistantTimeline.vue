<script setup lang="ts">
import { currentLocale, researchText as t } from '@/stores/locale'
import { computed } from 'vue'
import {
  PhArrowClockwise,
  PhBookOpenText,
  PhChatCircleText,
  PhWarningCircle,
} from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import type {
  AssistantFragment,
  AssistantMessage,
  AssistantTaskSummary,
} from '../api'

type Checkpoint = components['schemas']['AssistantCheckpointResponse']

const props = defineProps<{
  loading: boolean
  messages: AssistantMessage[]
  tasks: AssistantTaskSummary[]
  fragments: AssistantFragment[]
  activeTaskId: string
  currentDocumentContentSha256: string
  latestCheckpoint: Checkpoint | null
}>()

const emit = defineEmits<{
  navigatePage: [pageNumber: number]
  retry: [taskId: string]
}>()

const fragmentText = computed(() => [...props.fragments]
  .sort((left, right) => left.sequence - right.sequence)
  .map((fragment) => fragment.text)
  .join(''))
const failedTasks = computed(() => props.tasks.filter((task) => (
  task.status === 'failed' || task.status === 'cancelled'
)))
const activeTask = computed(() => props.tasks.find((task) => task.id === props.activeTaskId))

function roleLabel(role: string): string {
  return role === 'user' ? t('你') : 'Assistant'
}

function citationLabel(citation: AssistantMessage['citations'][number]): string {
  if (citation.page_number) return t('第 {0} 页证据', citation.page_number)
  return citation.locator ?? citation.evidence_id
}

function versionLabel(message: AssistantMessage): string {
  return message.evidence_manifest.document_content_sha256 === props.currentDocumentContentSha256
    ? t('当前版本')
    : t('已冻结版本')
}

function taskModeLabel(task: AssistantTaskSummary): string {
  return task.mode === 'explain' ? t('解释') : t('提问')
}

function formatDate(value: string): string {
  try {
    return new Intl.DateTimeFormat(currentLocale(), {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(value))
  } catch {
    return value
  }
}
</script>

<template>
  <section class="assistant-timeline" :aria-label="t('Assistant 历史')">
    <div v-if="loading" class="timeline-state" role="status" aria-live="polite">
      <span class="skeleton-line wide" />
      <span class="skeleton-line" />
      <span class="skeleton-line short" />
      <span class="sr-only">{{ t('正在读取 Assistant 历史') }}</span>
    </div>

    <template v-else>
      <details v-if="latestCheckpoint" class="checkpoint">
        <summary>{{ t('上下文检查点，保留至消息') }} {{ latestCheckpoint.through_sequence }}</summary>
        <p>{{ latestCheckpoint.summary }}</p>
        <small>{{ t('原始消息仍完整保留') }}</small>
      </details>

      <div
        v-if="messages.length === 0 && !activeTaskId"
        class="timeline-empty"
        data-test="assistant-empty"
      >
        <PhBookOpenText :size="20" aria-hidden="true" />
        <h2>{{ t('从正在阅读的证据开始') }}</h2>
        <p>{{ t('选择范围并发送后才会调用 Agent。回答与当前 PDF 内容哈希绑定。') }}</p>
      </div>

      <article
        v-for="message in messages"
        :key="message.id"
        class="timeline-message"
        :class="message.role"
      >
        <header>
          <strong>{{ roleLabel(message.role) }}</strong>
          <span>{{ versionLabel(message) }}</span>
          <time :datetime="message.created_at">{{ formatDate(message.created_at) }}</time>
        </header>
        <p>{{ message.content }}</p>
        <div v-if="message.citations.length" class="message-citations" :aria-label="t('本地证据引用')">
          <template v-for="citation in message.citations" :key="citation.evidence_id">
            <button
              v-if="citation.page_number"
              type="button"
              data-test="assistant-citation"
              @click="emit('navigatePage', citation.page_number)"
            >
              {{ citationLabel(citation) }}
            </button>
            <span v-else>{{ citationLabel(citation) }}</span>
          </template>
        </div>
      </article>

      <section v-if="activeTaskId" class="active-reply" role="status" aria-live="polite">
        <header>
          <PhChatCircleText :size="16" aria-hidden="true" />
          <strong>{{ activeTask?.status === 'queued' ? t('等待 Agent') : t('正在形成回答') }}</strong>
        </header>
        <p v-if="fragmentText">{{ fragmentText }}</p>
        <div v-else class="fragment-skeleton" aria-hidden="true">
          <span />
          <span />
        </div>
      </section>

      <article
        v-for="task in failedTasks"
        :key="task.id"
        class="task-diagnostic"
        role="status"
      >
        <PhWarningCircle :size="17" aria-hidden="true" />
        <div>
          <strong>{{ taskModeLabel(task) }}{{ t('任务') }}{{ task.status === 'cancelled' ? t('已取消') : t('未完成') }}</strong>
          <p>{{ task.diagnostic || t('本次任务没有生成正式回答，既有历史保持不变。') }}</p>
          <small v-if="task.context.section_fallback">{{ t('章节范围已回退到当前页') }}</small>
          <button
            v-if="task.retryable"
            type="button"
            :data-test="`assistant-retry-${task.id}`"
            @click="emit('retry', task.id)"
          >
            <PhArrowClockwise :size="14" aria-hidden="true" />
            {{ t('按原证据重试') }}
          </button>
        </div>
      </article>
    </template>
  </section>
</template>

<style scoped>
.assistant-timeline {
  min-height: 180px;
  display: grid;
  align-content: start;
  gap: 10px;
  padding: 12px 13px 18px;
}

.timeline-state,
.fragment-skeleton {
  display: grid;
  gap: 7px;
}

.skeleton-line,
.fragment-skeleton span {
  width: 74%;
  height: 10px;
  border-radius: var(--radius-sm);
  background: var(--color-line);
  opacity: 0.58;
}

.skeleton-line.wide,
.fragment-skeleton span:first-child {
  width: 94%;
}

.skeleton-line.short {
  width: 46%;
}

.timeline-empty {
  padding: 22px 4px 10px;
  color: var(--color-muted-strong);
}

.timeline-empty > svg {
  color: var(--color-forest);
}

.timeline-empty h2 {
  margin: 13px 0 7px;
  font-family: var(--font-editorial);
  font-size: 18px;
  font-weight: 500;
}

.timeline-empty p,
.checkpoint p,
.timeline-message p,
.active-reply p,
.task-diagnostic p {
  margin: 0;
  font-size: var(--type-meta);
  line-height: 1.65;
}

.checkpoint {
  padding: 8px 10px;
  border-left: 2px solid var(--color-forest);
  background: var(--color-forest-hover-soft);
}

.checkpoint summary {
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  cursor: pointer;
}

.checkpoint p {
  margin-top: 8px;
}

.checkpoint small {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.timeline-message {
  max-width: 94%;
  padding: 10px 11px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-raised);
}

.timeline-message.user {
  justify-self: end;
  background: var(--color-forest-soft);
}

.timeline-message.user > header {
  color: var(--color-muted-strong);
}

.timeline-message > header {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 7px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.timeline-message > header strong {
  color: var(--color-forest);
  font-size: var(--type-meta);
}

.timeline-message > header time {
  margin-left: auto;
}

.timeline-message > p {
  white-space: pre-wrap;
}

.message-citations {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 9px;
}

.message-citations button,
.message-citations span,
.task-diagnostic button {
  min-height: 28px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 8px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper);
  color: var(--color-forest);
  font-size: var(--type-meta);
}

.message-citations button,
.task-diagnostic button {
  cursor: pointer;
}

.message-citations button:focus-visible,
.task-diagnostic button:focus-visible,
.checkpoint summary:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.active-reply,
.task-diagnostic {
  padding: 10px 11px;
  border-left: 2px solid var(--color-forest);
  background: var(--color-forest-hover-soft);
}

.active-reply header {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-forest);
  font-size: var(--type-meta);
}

.active-reply p {
  margin-top: 8px;
  white-space: pre-wrap;
}

.fragment-skeleton {
  margin-top: 9px;
}

.task-diagnostic {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px;
  border-left-color: var(--color-wine);
  background: var(--color-wine-soft);
}

.task-diagnostic > svg,
.task-diagnostic strong,
.task-diagnostic small {
  color: var(--color-wine);
}

.task-diagnostic strong {
  font-size: var(--type-meta);
}

.task-diagnostic p {
  margin-top: 4px;
  color: var(--color-muted-strong);
}

.task-diagnostic small {
  display: block;
  margin-top: 5px;
  font-size: var(--type-meta);
}

.task-diagnostic button {
  margin-top: 8px;
  color: var(--color-wine);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
