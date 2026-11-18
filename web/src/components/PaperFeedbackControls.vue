<script setup lang="ts">
import { libraryText as t, messageRef } from '@/stores/locale'
import { computed, ref, watch } from 'vue'
import { PhBookmarkSimple, PhHeart, PhThumbsDown, PhThumbsUp } from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'

type LibraryStateResponse = components['schemas']['LibraryStateResponse']
type Feedback = 'neutral' | 'like' | 'dislike'

interface LibraryStateLike {
  work_id: string
  favorite: boolean
  feedback: string
  feedback_reasons?: string[]
  pinned: boolean
  reading_progress: number
  analysis_state: string
  persisted?: boolean
  created_at?: string | null
  updated_at?: string | null
}

const props = defineProps<{
  workId: string
  state: LibraryStateLike
  compact?: boolean
}>()

const emit = defineEmits<{
  updated: [state: LibraryStateResponse]
}>()

const current = ref<LibraryStateLike>(copyState(props.state))
const hydrated = ref(Array.isArray(props.state.feedback_reasons))
const draftFeedback = ref<Feedback>(asFeedback(props.state.feedback))
const draftReasons = ref<string[]>([...(props.state.feedback_reasons ?? [])])
const busy = ref(false)
const statusMessage = messageRef(t)
const errorMessage = messageRef(t)

const reasonOptions = computed(() => draftFeedback.value === 'like'
  ? [
      ['high_relevance', t('高度相关')],
      ['strong_evidence', t('证据扎实')],
      ['useful_method', t('方法有用')],
      ['novel', t('有新意')],
      ['clear_writing', t('表达清晰')],
      ['reproducible', t('可复现')],
      ['practical', t('有实践价值')],
      ['follow_up', t('值得继续追踪')],
    ] as const
  : [
      ['off_topic', t('偏离主题')],
      ['weak_evidence', t('证据不足')],
      ['unclear_method', t('方法不清')],
      ['low_novelty', t('新意有限')],
      ['inaccessible', t('难以获取全文')],
      ['duplicate', t('内容重复')],
      ['poor_fit', t('不符合当前研究')],
      ['other', t('其他')],
    ] as const)

watch(
  () => props.state,
  (state) => {
    if (busy.value) return
    current.value = copyState(state)
    hydrated.value = Array.isArray(state.feedback_reasons)
    draftFeedback.value = asFeedback(state.feedback)
    draftReasons.value = [...(state.feedback_reasons ?? [])]
  },
  { deep: true },
)

function copyState(state: LibraryStateLike): LibraryStateLike {
  return { ...state, feedback_reasons: state.feedback_reasons ? [...state.feedback_reasons] : undefined }
}

function asFeedback(value: string): Feedback {
  return value === 'like' || value === 'dislike' ? value : 'neutral'
}

async function completeState(): Promise<LibraryStateLike> {
  if (hydrated.value) return current.value
  const loaded = await apiJson<LibraryStateResponse>(
    `/api/v1/library/works/${encodeURIComponent(props.workId)}`,
  )
  current.value = copyState(loaded)
  hydrated.value = true
  return current.value
}

async function persist(
  changes: Partial<Pick<LibraryStateLike, 'favorite' | 'feedback' | 'feedback_reasons'>>,
  success: string,
): Promise<void> {
  if (busy.value) return
  busy.value = true
  statusMessage.value = ''
  errorMessage.value = ''
  try {
    const base = await completeState()
    const payload = {
      favorite: changes.favorite ?? base.favorite,
      feedback: changes.feedback ?? asFeedback(base.feedback),
      feedback_reasons: changes.feedback_reasons ?? base.feedback_reasons ?? [],
      pinned: base.pinned,
      reading_progress: base.reading_progress,
      analysis_state: base.analysis_state,
    }
    const saved = await apiJson<LibraryStateResponse>(
      `/api/v1/library/works/${encodeURIComponent(props.workId)}`,
      { method: 'PUT', body: JSON.stringify(payload) },
    )
    current.value = copyState(saved)
    hydrated.value = true
    draftFeedback.value = asFeedback(saved.feedback)
    draftReasons.value = [...saved.feedback_reasons]
    statusMessage.value = success
    emit('updated', saved)
  } catch {
    errorMessage.value = '保存失败，没有修改原有记录。可以稍后重试。'
  } finally {
    busy.value = false
  }
}

function chooseFeedback(feedback: Exclude<Feedback, 'neutral'>): void {
  draftFeedback.value = asFeedback(current.value.feedback) === feedback ? 'neutral' : feedback
  draftReasons.value = draftFeedback.value === asFeedback(current.value.feedback)
    ? [...(current.value.feedback_reasons ?? [])]
    : []
  statusMessage.value = ''
  errorMessage.value = ''
}

async function saveFeedback(): Promise<void> {
  await persist(
    {
      feedback: draftFeedback.value,
      feedback_reasons: draftFeedback.value === 'neutral' ? [] : draftReasons.value.slice(0, 8),
    },
    draftFeedback.value === 'neutral' ? t('已清除评价') : t('评价已保存'),
  )
}
</script>

<template>
  <div class="feedback-controls" :class="{ compact }">
    <div class="primary-controls" :aria-label="t('论文库与评价操作')">
      <button
        v-if="!current.persisted"
        type="button"
        data-test="save-work"
        :disabled="busy"
        @click="persist({}, t('已保存到论文库'))"
      >
        <PhBookmarkSimple :size="15" aria-hidden="true" />
        {{ t('保存') }}
      </button>
      <button
        type="button"
        data-test="favorite-work"
        :aria-pressed="current.favorite"
        :disabled="busy"
        @click="persist({ favorite: !current.favorite }, current.favorite ? t('已取消收藏') : t('已收藏'))"
      >
        <PhHeart :size="15" :weight="current.favorite ? 'fill' : 'regular'" aria-hidden="true" />
        {{ current.favorite ? t('已收藏') : t('收藏') }}
      </button>
      <button
        type="button"
        data-test="feedback-like"
        :aria-pressed="asFeedback(current.feedback) === 'like'"
        :disabled="busy"
        @click="chooseFeedback('like')"
      >
        <PhThumbsUp :size="15" aria-hidden="true" />
        {{ t('喜欢') }}
      </button>
      <button
        type="button"
        data-test="feedback-dislike"
        :aria-pressed="asFeedback(current.feedback) === 'dislike'"
        :disabled="busy"
        @click="chooseFeedback('dislike')"
      >
        <PhThumbsDown :size="15" aria-hidden="true" />
        {{ t('不喜欢') }}
      </button>
    </div>

    <fieldset
      v-if="draftFeedback !== 'neutral'"
      data-test="feedback-reasons"
      class="reason-panel"
      :aria-label="t('{0}原因（可选）', draftFeedback === 'like' ? t('喜欢') : t('不喜欢'))"
    >
      <legend>{{ t('原因可选，最多 8 项') }}</legend>
      <label v-for="option in reasonOptions" :key="option[0]">
        <input
          v-model="draftReasons"
          type="checkbox"
          :value="option[0]"
          :data-test="`feedback-reason-${option[0]}`"
          :disabled="busy"
        />
        <span>{{ option[1] }}</span>
      </label>
      <button type="button" data-test="save-feedback" :disabled="busy" @click="saveFeedback">
        {{ busy ? t('正在保存') : t('保存评价') }}
      </button>
    </fieldset>

    <p data-test="feedback-status" class="feedback-status" role="status" aria-live="polite">
      {{ statusMessage }}
    </p>
    <p v-if="errorMessage" class="feedback-error" role="alert">{{ errorMessage }}</p>
  </div>
</template>

<style scoped>
.feedback-controls { display: grid; gap: 10px; }
.primary-controls { display: flex; flex-wrap: wrap; gap: 7px; }
button { display: inline-flex; min-height: 34px; align-items: center; justify-content: center; gap: 6px; padding: 0 11px; border: 1px solid var(--color-line); border-radius: 7px; background: transparent; color: var(--color-muted-strong); font-size: var(--type-meta); font-weight: 650; cursor: pointer; }
button:hover, button[aria-pressed='true'] { border-color: var(--color-forest); color: var(--color-forest); }
button[aria-pressed='true'] { background: var(--color-forest-soft); }
button:focus-visible, input:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
button:disabled { opacity: .55; cursor: wait; }
.reason-panel { display: flex; flex-wrap: wrap; gap: 7px 13px; margin: 0; padding: 12px; border: 1px solid var(--color-line); border-radius: var(--radius-sm); background: var(--color-paper-deep); }
.reason-panel legend { padding: 0 5px; color: var(--color-muted); font-size: var(--type-meta); }
.reason-panel label { display: inline-flex; align-items: center; gap: 5px; color: var(--color-muted-strong); font-size: var(--type-meta); cursor: pointer; }
.reason-panel input { margin: 0; accent-color: var(--color-forest); }
.reason-panel button { margin-left: auto; border-color: var(--color-forest); color: var(--color-forest); }
.feedback-status, .feedback-error { min-height: 1.2em; margin: 0; font-size: var(--type-meta); line-height: 1.4; }
.feedback-status { color: var(--color-forest); }
.feedback-error { color: var(--color-danger); }
.compact .primary-controls { gap: 5px; }
.compact button { min-height: 31px; padding: 0 9px; }
@media (max-width: 640px) { .reason-panel button { width: 100%; margin-left: 0; } }
</style>
