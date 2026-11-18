<script setup lang="ts">
import { readerText as t } from '@/stores/locale'
import { computed, nextTick, ref, watch } from 'vue'
import {
  PhArrowClockwise,
  PhArrowRight,
  PhBookmarkSimple,
  PhCaretLeft,
  PhCaretRight,
  PhCheck,
  PhEye,
  PhHighlighter,
  PhNotePencil,
  PhPencilSimple,
  PhTrash,
  PhWarningCircle,
  PhX,
} from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import type { PdfTextSelection } from '@/lib/annotation-geometry'

type Annotation = components['schemas']['AnnotationResponse']
type AnnotationKind = components['schemas']['AnnotationKind']
type AnnotationColor = components['schemas']['AnnotationColor']
type MigrationPreview = components['schemas']['MigrationPreviewResponse']
type MigrationCandidate = components['schemas']['MigrationCandidateResponse']
type MigrationDecision = 'accept' | 'reject'

export interface MigrationVersionOption {
  id: string
  label: string
  available: boolean
}

export interface AnnotationCreateIntent {
  kind: AnnotationKind
  color: AnnotationColor
  note: string | null
}

export interface AnnotationUpdateIntent {
  color: AnnotationColor
  note: string | null
}

const props = withDefaults(defineProps<{
  annotations: Annotation[]
  selection: PdfTextSelection | null
  currentPage: number
  loading: boolean
  saving: boolean
  errorMessage?: string
  statusMessage?: string
  versionOptions?: MigrationVersionOption[]
  currentDocumentId?: string
  migrationPreview?: MigrationPreview | null
  migrationLoading?: boolean
  migrationSaving?: boolean
  migrationError?: string
  migrationStatus?: string
}>(), {
  errorMessage: '',
  statusMessage: '',
  versionOptions: () => [],
  currentDocumentId: '',
  migrationPreview: null,
  migrationLoading: false,
  migrationSaving: false,
  migrationError: '',
  migrationStatus: '',
})

const emit = defineEmits<{
  create: [intent: AnnotationCreateIntent]
  update: [annotation: Annotation, intent: AnnotationUpdateIntent]
  delete: [annotation: Annotation]
  navigate: [pageNumber: number]
  retry: []
  'clear-selection': []
  'preview-migration': [sourceDocumentId: string]
  'preview-candidate': [candidate: MigrationCandidate]
  'decide-migration': [candidate: MigrationCandidate, decision: MigrationDecision]
}>()

const filter = ref<'all' | AnnotationKind>('all')
const composerColor = ref<AnnotationColor>('amber')
const composerNote = ref('')
const editingId = ref('')
const editingColor = ref<AnnotationColor>('amber')
const editingNote = ref('')
const confirmingDeleteId = ref('')
const localAnnouncement = ref('')
const heading = ref<HTMLElement>()
const selectedMigrationSource = ref('')
const migrationCandidateIndex = ref(0)

const filteredAnnotations = computed(() => filter.value === 'all'
  ? props.annotations
  : props.annotations.filter((annotation) => annotation.kind === filter.value))
const announcement = computed(() => props.statusMessage || localAnnouncement.value)
const canCreateSelection = computed(() => Boolean(props.selection) && !props.saving)
const canCreateNote = computed(() => (
  canCreateSelection.value && composerNote.value.trim().length > 0
))
const migrationSources = computed(() => props.versionOptions.filter((version) => (
  version.available && version.id !== props.currentDocumentId
)))
const currentMigrationCandidate = computed(() => (
  props.migrationPreview?.candidates[migrationCandidateIndex.value] ?? null
))
const migratableCount = computed(() => (
  (props.migrationPreview?.coverage.matched ?? 0)
  + (props.migrationPreview?.coverage.ambiguous ?? 0)
))
const canAcceptMigration = computed(() => Boolean(
  currentMigrationCandidate.value
  && currentMigrationCandidate.value.match_state !== 'orphaned'
  && currentMigrationCandidate.value.decision === null
  && !props.migrationSaving,
))
const canRejectMigration = computed(() => Boolean(
  currentMigrationCandidate.value
  && currentMigrationCandidate.value.decision === null
  && !props.migrationSaving,
))

watch(migrationSources, (sources) => {
  if (!sources.some((source) => source.id === selectedMigrationSource.value)) {
    selectedMigrationSource.value = sources[0]?.id ?? ''
  }
}, { immediate: true })

watch(() => props.migrationPreview?.id, () => {
  migrationCandidateIndex.value = 0
})

watch(() => props.migrationPreview?.candidates.length, (length) => {
  migrationCandidateIndex.value = Math.min(
    migrationCandidateIndex.value,
    Math.max(0, (length ?? 0) - 1),
  )
})

watch(
  () => props.selection?.quoteText,
  () => {
    composerNote.value = ''
    localAnnouncement.value = props.selection
      ? t('已选择第 {0} 页文字', props.selection.pageNumber)
      : ''
  },
)

function selectColor(value: AnnotationColor): void {
  composerColor.value = value
}

function create(kind: AnnotationKind): void {
  if (props.saving) return
  if (kind !== 'bookmark' && !props.selection) return
  const note = composerNote.value.trim() || null
  if (kind === 'note' && note === null) return
  emit('create', { kind, color: composerColor.value, note })
  localAnnouncement.value = kind === 'bookmark' ? t('正在保存本页书签') : t('正在保存批注')
}

function setFilter(value: 'all' | AnnotationKind): void {
  filter.value = value
}

function startEdit(annotation: Annotation): void {
  confirmingDeleteId.value = ''
  editingId.value = annotation.id
  editingColor.value = annotation.color
  editingNote.value = annotation.note ?? ''
}

function cancelEdit(): void {
  editingId.value = ''
}

function saveEdit(annotation: Annotation): void {
  if (props.saving) return
  const note = editingNote.value.trim() || null
  if (annotation.kind === 'note' && note === null) {
    localAnnouncement.value = t('笔记内容不能为空')
    return
  }
  emit('update', annotation, {
    color: editingColor.value,
    note,
  })
  editingId.value = ''
  localAnnouncement.value = t('正在更新批注')
}

function requestDelete(annotation: Annotation): void {
  editingId.value = ''
  confirmingDeleteId.value = annotation.id
}

function cancelDelete(): void {
  confirmingDeleteId.value = ''
}

async function confirmDelete(annotation: Annotation): Promise<void> {
  if (props.saving) return
  emit('delete', annotation)
  confirmingDeleteId.value = ''
  localAnnouncement.value = t('正在删除批注')
  await nextTick()
  heading.value?.focus()
}

function kindLabel(kind: AnnotationKind): string {
  if (kind === 'highlight') return t('高亮')
  if (kind === 'note') return t('笔记')
  return t('书签')
}

function colorLabel(color: AnnotationColor): string {
  if (color === 'amber') return t('琥珀')
  if (color === 'green') return t('森林绿')
  if (color === 'rose') return t('酒红')
  return t('墨蓝')
}

function startMigrationPreview(): void {
  if (!selectedMigrationSource.value || props.migrationLoading) return
  emit('preview-migration', selectedMigrationSource.value)
}

function moveMigrationCandidate(offset: number): void {
  const count = props.migrationPreview?.candidates.length ?? 0
  migrationCandidateIndex.value = Math.max(
    0,
    Math.min(count - 1, migrationCandidateIndex.value + offset),
  )
}

function confidenceLabel(candidate: MigrationCandidate): string {
  if (candidate.confidence === 'high') return t('高置信度')
  if (candidate.confidence === 'medium') return t('中等置信度')
  if (candidate.confidence === 'low') return t('低置信度')
  return t('未找到位置')
}

function decisionLabel(candidate: MigrationCandidate): string {
  if (candidate.decision === 'accept') return t('已接受迁移')
  if (candidate.decision === 'reject') return t('已保留原位置')
  return t('等待确认')
}

function decideMigration(decision: MigrationDecision): void {
  if (!currentMigrationCandidate.value) return
  if (decision === 'accept' && !canAcceptMigration.value) return
  if (decision === 'reject' && !canRejectMigration.value) return
  emit('decide-migration', currentMigrationCandidate.value, decision)
}
</script>

<template>
  <section class="annotation-panel" aria-labelledby="annotation-panel-heading">
    <header class="panel-heading">
      <div>
        <span>{{ t('阅读证据') }}</span>
        <h2
          id="annotation-panel-heading"
          ref="heading"
          data-test="annotation-heading"
          tabindex="-1"
        >
          {{ t('批注与书签') }}
        </h2>
      </div>
      <strong>{{ annotations.length }}</strong>
    </header>

    <p class="sr-only" aria-live="polite">{{ announcement }}</p>

    <section
      v-if="migrationSources.length > 0 || migrationPreview"
      class="migration-review"
      aria-labelledby="migration-review-heading"
    >
      <header class="migration-heading">
        <div>
          <span>{{ t('版本证据') }}</span>
          <h3 id="migration-review-heading">{{ t('迁移旧版批注') }}</h3>
        </div>
        <span class="source-preserved">{{ t('原批注始终保留') }}</span>
      </header>

      <div class="migration-source-row">
        <label>
          <span>{{ t('来源版本') }}</span>
          <select
            v-model="selectedMigrationSource"
            data-test="migration-source"
            :disabled="migrationLoading || migrationSources.length === 0"
          >
            <option
              v-for="version in migrationSources"
              :key="version.id"
              :value="version.id"
            >
              {{ version.label }}
            </option>
          </select>
        </label>
        <button
          type="button"
          data-test="start-migration-preview"
          :disabled="!selectedMigrationSource || migrationLoading"
          @click="startMigrationPreview"
        >
          <PhArrowClockwise :size="15" aria-hidden="true" />
          {{ migrationLoading ? t('正在比对…') : migrationPreview ? t('重新预览') : t('生成预览') }}
        </button>
      </div>

      <p v-if="migrationError" class="migration-message error" role="alert">
        <PhWarningCircle :size="15" aria-hidden="true" />
        {{ migrationError }}
      </p>
      <p v-else-if="migrationStatus" class="migration-message" role="status">
        <PhCheck :size="15" aria-hidden="true" />
        {{ migrationStatus }}
      </p>

      <template v-if="migrationPreview">
        <p class="migration-coverage" data-test="migration-coverage">
          <strong>{{ migratableCount }} {{ t('可迁移') }}</strong>
          <span>{{ migrationPreview.coverage.ambiguous }} {{ t('待辨认') }}</span>
          <span>{{ migrationPreview.coverage.orphaned }} {{ t('未定位') }}</span>
          <span>{{ migrationPreview.coverage.pending }} {{ t('待处理') }}</span>
        </p>

        <article v-if="currentMigrationCandidate" class="migration-candidate">
          <header>
            <div>
              <span
                class="migration-confidence"
                :data-confidence="currentMigrationCandidate.confidence"
              >
                {{ confidenceLabel(currentMigrationCandidate) }}
              </span>
              <span>{{ decisionLabel(currentMigrationCandidate) }}</span>
            </div>
            <strong data-test="migration-candidate-position">
              {{ migrationCandidateIndex + 1 }} / {{ migrationPreview.candidates.length }}
            </strong>
          </header>

          <div class="migration-source-evidence">
            <span>{{ t('旧版 · 第') }} {{ currentMigrationCandidate.source_page_number }} {{ t('页') }}</span>
            <blockquote
              v-if="currentMigrationCandidate.source_quote_text"
              data-test="migration-source-quote"
            >
              {{ currentMigrationCandidate.source_quote_text }}
            </blockquote>
            <p v-else data-test="migration-source-quote">{{ t('页码书签') }}</p>
            <p v-if="currentMigrationCandidate.source_note" class="saved-note">
              {{ currentMigrationCandidate.source_note }}
            </p>
          </div>

          <div
            v-if="currentMigrationCandidate.match_state === 'orphaned'"
            class="migration-warning"
          >
            <PhWarningCircle :size="17" aria-hidden="true" />
            <p>
              <strong>{{ t('目标版本没有找到可迁移位置') }}</strong>
              <span>{{ t('可拒绝本次迁移，旧版批注不会删除或移动。') }}</span>
            </p>
          </div>
          <div
            v-else-if="currentMigrationCandidate.match_state === 'ambiguous'"
            class="migration-warning"
          >
            <PhWarningCircle :size="17" aria-hidden="true" />
            <p>
              <strong>{{ t('存在多个相似位置') }}</strong>
              <span>{{ t('请先在 PDF 中核对系统建议的位置，再决定是否接受。') }}</span>
            </p>
          </div>
          <div v-else class="migration-target-evidence">
            <span>
              {{ t('新版 · 第') }} {{ currentMigrationCandidate.target_page_number }} {{ t('页') }}
              <small v-if="currentMigrationCandidate.text_source === 'ocr'">{{ t('OCR 识别') }}</small>
            </span>
            <blockquote v-if="currentMigrationCandidate.target_quote_text">
              {{ currentMigrationCandidate.target_quote_text }}
            </blockquote>
          </div>

          <div class="migration-actions">
            <button
              type="button"
              data-test="preview-migration-candidate"
              :disabled="currentMigrationCandidate.target_page_number === null"
              @click="emit('preview-candidate', currentMigrationCandidate)"
            >
              <PhEye :size="15" aria-hidden="true" />
              {{ t('在 PDF 中核对') }}
            </button>
            <button
              type="button"
              data-test="accept-migration-candidate"
              :disabled="!canAcceptMigration"
              @click="decideMigration('accept')"
            >
              <PhCheck :size="15" aria-hidden="true" />
              {{ t('接受') }}
            </button>
            <button
              type="button"
              data-test="reject-migration-candidate"
              :disabled="!canRejectMigration"
              @click="decideMigration('reject')"
            >
              <PhX :size="15" aria-hidden="true" />
              {{ t('拒绝') }}
            </button>
          </div>

          <footer class="migration-pagination">
            <button
              type="button"
              data-test="previous-migration-candidate"
              :aria-label="t('上一条迁移候选')"
              :disabled="migrationCandidateIndex === 0"
              @click="moveMigrationCandidate(-1)"
            >
              <PhCaretLeft :size="15" aria-hidden="true" />
            </button>
            <span>{{ t('逐条确认，系统不会批量改写批注') }}</span>
            <button
              type="button"
              data-test="next-migration-candidate"
              :aria-label="t('下一条迁移候选')"
              :disabled="migrationCandidateIndex >= migrationPreview.candidates.length - 1"
              @click="moveMigrationCandidate(1)"
            >
              <PhCaretRight :size="15" aria-hidden="true" />
            </button>
          </footer>
        </article>
      </template>
    </section>

    <section class="composer" :aria-label="t('新建批注')">
      <div v-if="selection" class="selection-card">
        <header>
          <span>{{ t('第') }} {{ selection.pageNumber }} {{ t('页选区') }}</span>
          <button
            type="button"
            data-test="clear-selection"
            :aria-label="t('清除当前文字选区')"
            @click="emit('clear-selection')"
          >
            <PhX :size="15" aria-hidden="true" />
          </button>
        </header>
        <blockquote data-test="selection-quote">{{ selection.quoteText }}</blockquote>
      </div>
      <p v-else class="selection-help">
        {{ t('在 PDF 中选择文字即可建立证据高亮或笔记；书签不需要文字选区。') }}
      </p>

      <fieldset class="color-picker">
        <legend>{{ t('标记颜色') }}</legend>
        <button
          v-for="color in (['amber', 'green', 'rose', 'blue'] as const)"
          :key="color"
          type="button"
          :data-test="`color-${color}`"
          :data-color="color"
          :aria-label="colorLabel(color)"
          :aria-pressed="composerColor === color"
          @click="selectColor(color)"
        />
      </fieldset>

      <label class="note-field">
        <span>{{ t('随手记') }} <small>{{ t('可选；保存为笔记时必填') }}</small></span>
        <textarea
          v-model="composerNote"
          data-test="composer-note"
          maxlength="20000"
          rows="3"
          :placeholder="t('写下判断、疑问或下一步…')"
        />
      </label>

      <div class="composer-actions">
        <button
          type="button"
          data-test="create-highlight"
          aria-keyshortcuts="Shift+H"
          :disabled="!canCreateSelection"
          @click="create('highlight')"
        >
          <PhHighlighter :size="16" aria-hidden="true" />
          {{ t('保存高亮') }}
        </button>
        <button
          type="button"
          data-test="create-note"
          :disabled="!canCreateNote"
          @click="create('note')"
        >
          <PhNotePencil :size="16" aria-hidden="true" />
          {{ t('保存为笔记') }}
        </button>
        <button
          type="button"
          data-test="create-bookmark"
          :disabled="saving || currentPage < 1"
          @click="create('bookmark')"
        >
          <PhBookmarkSimple :size="16" aria-hidden="true" />
          {{ t('本页书签') }}
        </button>
      </div>
    </section>

    <div class="annotation-filters" role="group" :aria-label="t('筛选批注')">
      <button
        v-for="item in ([
          ['all', '全部'],
          ['highlight', '高亮'],
          ['note', '笔记'],
          ['bookmark', '书签'],
        ] as const)"
        :key="item[0]"
        type="button"
        :data-test="`filter-${item[0]}`"
        :aria-pressed="filter === item[0]"
        @click="setFilter(item[0])"
      >
        {{ item[1] }}
      </button>
    </div>

    <div v-if="loading" class="panel-state" role="status">
      {{ t('正在读取批注…') }}
    </div>
    <div v-else-if="errorMessage" class="panel-error" role="alert">
      <p>{{ errorMessage }}</p>
      <button type="button" data-test="retry-annotations" @click="emit('retry')">
        {{ t('重新读取') }}
      </button>
    </div>

    <div v-if="!loading && filteredAnnotations.length === 0" class="panel-state">
      <strong>{{ t('还没有批注或书签') }}</strong>
      <span>{{ t('文字证据、自己的判断和页面书签会按页码排在这里。') }}</span>
    </div>

    <ol v-else-if="!loading" class="annotation-list">
      <li
        v-for="annotation in filteredAnnotations"
        :key="annotation.id"
        :data-test="`annotation-${annotation.id}`"
        :data-color="annotation.color"
      >
        <header>
          <button
            type="button"
            :data-test="`navigate-${annotation.id}`"
            :aria-label="t('前往第 {0} 页', annotation.page_number)"
            @click="emit('navigate', annotation.page_number)"
          >
            {{ t('第') }} {{ annotation.page_number }} {{ t('页') }}
            <PhArrowRight :size="13" aria-hidden="true" />
          </button>
          <span>{{ kindLabel(annotation.kind) }}</span>
        </header>

        <template v-if="editingId !== annotation.id">
          <blockquote v-if="annotation.quote_text">{{ annotation.quote_text }}</blockquote>
          <p v-if="annotation.note" class="saved-note">{{ annotation.note }}</p>
          <div class="item-actions">
            <button
              type="button"
              :data-test="`edit-${annotation.id}`"
              @click="startEdit(annotation)"
            >
              <PhPencilSimple :size="14" aria-hidden="true" />
              {{ t('编辑') }}
            </button>
            <button
              type="button"
              :data-test="`delete-${annotation.id}`"
              @click="requestDelete(annotation)"
            >
              <PhTrash :size="14" aria-hidden="true" />
              {{ t('删除') }}
            </button>
          </div>
        </template>

        <div v-else class="edit-form">
          <label>
            <span>{{ t('笔记') }}</span>
            <textarea
              v-model="editingNote"
              :data-test="`edit-note-${annotation.id}`"
              maxlength="20000"
              rows="3"
            />
          </label>
          <fieldset class="color-picker compact">
            <legend>{{ t('颜色') }}</legend>
            <button
              v-for="color in (['amber', 'green', 'rose', 'blue'] as const)"
              :key="color"
              type="button"
              :data-test="`edit-color-${annotation.id}-${color}`"
              :data-color="color"
              :aria-label="colorLabel(color)"
              :aria-pressed="editingColor === color"
              @click="editingColor = color"
            />
          </fieldset>
          <div>
            <button type="button" :data-test="`save-${annotation.id}`" @click="saveEdit(annotation)">
              {{ t('保存修改') }}
            </button>
            <button type="button" @click="cancelEdit">{{ t('取消') }}</button>
          </div>
        </div>

        <div v-if="confirmingDeleteId === annotation.id" class="delete-confirm" role="alert">
          <span>{{ t('确定删除这条') }}{{ kindLabel(annotation.kind) }}{{ t('吗？') }}</span>
          <button
            type="button"
            :data-test="`confirm-delete-${annotation.id}`"
            @click="confirmDelete(annotation)"
          >
            {{ t('确定删除') }}
          </button>
          <button type="button" @click="cancelDelete">{{ t('取消') }}</button>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.annotation-panel {
  min-height: 100%;
  color: var(--color-ink);
}

.panel-heading {
  min-height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-line);
}

.panel-heading span,
.panel-heading h2 {
  display: block;
  margin: 0;
}

.panel-heading span {
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.11em;
}

.panel-heading h2 {
  margin-top: 3px;
  font-family: var(--font-editorial);
  font-size: 17px;
  font-weight: 500;
}

.panel-heading h2:focus-visible,
.annotation-panel button:focus-visible,
.annotation-panel textarea:focus-visible,
.annotation-panel select:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 1px;
}

.panel-heading strong {
  min-width: 28px;
  color: var(--color-muted);
  font-size: 12px;
  text-align: right;
}

.migration-review {
  padding: 14px 16px 16px;
  border-bottom: 1px solid var(--color-line);
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--color-forest-soft) 64%, transparent), transparent 68%),
    var(--color-paper-deep);
}

.migration-heading,
.migration-heading > div,
.migration-heading h3,
.migration-heading span {
  margin: 0;
}

.migration-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.migration-heading > div > span {
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.1em;
}

.migration-heading h3 {
  margin-top: 3px;
  font-family: var(--font-editorial);
  font-size: 15px;
  font-weight: 500;
}

.source-preserved {
  padding: 4px 7px;
  border: 1px solid var(--color-line);
  border-radius: 999px;
  color: var(--color-muted);
  font-size: var(--type-meta);
  white-space: nowrap;
}

.migration-source-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: end;
  gap: 7px;
  margin-top: 12px;
}

.migration-source-row label {
  display: grid;
  gap: 5px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.migration-source-row select,
.migration-source-row button,
.migration-actions button,
.migration-pagination button {
  min-height: 34px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper);
  color: var(--color-ink);
  font: inherit;
}

.migration-source-row select {
  min-width: 0;
  padding: 0 30px 0 9px;
  font-size: var(--type-meta);
}

.migration-source-row button,
.migration-actions button,
.migration-pagination button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 0 9px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  cursor: pointer;
}

.migration-source-row button:disabled,
.migration-actions button:disabled,
.migration-pagination button:disabled {
  cursor: default;
  opacity: 0.48;
}

.migration-message {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 10px 0 0;
  color: var(--color-forest);
  font-size: var(--type-meta);
  line-height: 1.45;
}

.migration-message.error { color: var(--color-wine); }

.migration-coverage {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 9px;
  margin: 12px 0 0;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.migration-coverage strong { color: var(--color-forest); }

.migration-candidate {
  margin-top: 10px;
  overflow: hidden;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper);
}

.migration-candidate > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 9px;
  padding: 9px 10px;
  border-bottom: 1px solid var(--color-line);
}

.migration-candidate > header > div {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.migration-candidate > header span,
.migration-candidate > header strong {
  font-size: var(--type-meta);
}

.migration-candidate > header > div > span:last-child { color: var(--color-muted); }

.migration-confidence {
  color: var(--color-forest);
  font-weight: 700;
}

.migration-confidence[data-confidence='low'],
.migration-confidence[data-confidence='none'] { color: var(--color-wine); }

.migration-source-evidence,
.migration-target-evidence {
  padding: 10px;
}

.migration-source-evidence {
  border-bottom: 1px dashed var(--color-line);
}

.migration-source-evidence > span,
.migration-target-evidence > span {
  color: var(--color-muted);
  font-size: var(--type-meta);
  font-weight: 700;
}

.migration-source-evidence blockquote,
.migration-target-evidence blockquote,
.migration-source-evidence > p {
  margin: 6px 0 0;
  color: var(--color-muted-strong);
  font-family: var(--font-editorial);
  font-size: var(--type-meta);
  line-height: 1.55;
  white-space: pre-wrap;
}

.migration-target-evidence small {
  margin-left: 5px;
  color: var(--color-wine);
  font-size: 7px;
}

.migration-warning {
  display: flex;
  gap: 7px;
  padding: 10px;
  color: var(--color-wine);
}

.migration-warning p {
  display: grid;
  gap: 3px;
  margin: 0;
  font-size: var(--type-meta);
  line-height: 1.45;
}

.migration-warning span { color: var(--color-muted); }

.migration-actions {
  display: grid;
  grid-template-columns: 1fr 0.72fr 0.72fr;
  gap: 6px;
  padding: 0 10px 10px;
}

.migration-actions button:first-child {
  background: var(--color-forest-soft);
}

.migration-pagination {
  display: grid;
  grid-template-columns: 34px 1fr 34px;
  align-items: center;
  gap: 7px;
  padding: 8px 10px;
  border-top: 1px solid var(--color-line);
  color: var(--color-muted);
  font-size: var(--type-meta);
  text-align: center;
}

.migration-pagination button {
  width: 34px;
  min-height: 30px;
  padding: 0;
}

.composer {
  padding: 14px 16px 16px;
  border-bottom: 1px solid var(--color-line);
  background: var(--color-paper);
}

.selection-card {
  border-left: 3px solid var(--color-forest);
  background: var(--color-paper-deep);
}

.selection-card header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 9px 0 11px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
}

.selection-card header button {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-muted);
}

.selection-card blockquote,
.annotation-list blockquote {
  white-space: pre-wrap;
}

.selection-card blockquote {
  max-height: 84px;
  margin: 0;
  overflow: auto;
  padding: 6px 11px 11px;
  color: var(--color-muted-strong);
  font-family: var(--font-editorial);
  font-size: 12px;
  line-height: 1.55;
}

.selection-help {
  margin: 0;
  color: var(--color-muted);
  font-size: var(--type-meta);
  line-height: 1.6;
}

.color-picker {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 12px 0 0;
  padding: 0;
  border: 0;
}

.color-picker legend {
  float: left;
  margin-right: 5px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.color-picker button {
  width: 20px;
  height: 20px;
  padding: 0;
  border: 2px solid var(--color-paper);
  border-radius: 50%;
  box-shadow: 0 0 0 1px var(--color-line);
}

.color-picker button[aria-pressed='true'] {
  box-shadow: 0 0 0 2px var(--color-ink);
}

.color-picker button[data-color='amber'] { background: #d6a84b; }
.color-picker button[data-color='green'] { background: #4f7660; }
.color-picker button[data-color='rose'] { background: #8d4a50; }
.color-picker button[data-color='blue'] { background: #4f687d; }

.note-field {
  display: grid;
  gap: 6px;
  margin-top: 12px;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
}

.note-field small {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.note-field textarea,
.edit-form textarea {
  width: 100%;
  resize: vertical;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-deep);
  color: var(--color-ink);
  font: inherit;
  line-height: 1.55;
}

.note-field textarea {
  padding: 9px;
  font-size: var(--type-meta);
}

.composer-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 7px;
  margin-top: 10px;
}

.composer-actions button,
.panel-error button,
.item-actions button,
.edit-form button,
.delete-confirm button {
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 0 9px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper-deep);
  color: var(--color-forest);
  font-size: var(--type-meta);
  cursor: pointer;
}

.composer-actions button:last-child {
  grid-column: 1 / -1;
}

.composer-actions button:disabled {
  color: var(--color-muted);
  cursor: default;
  opacity: 0.52;
}

.annotation-filters {
  display: flex;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-line);
}

.annotation-filters button {
  flex: 1;
  min-height: 30px;
  padding: 0 5px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.annotation-filters button[aria-pressed='true'] {
  background: var(--color-forest-soft);
  color: var(--color-forest);
  font-weight: 700;
}

.panel-state,
.panel-error {
  display: grid;
  gap: 7px;
  margin: 18px 16px;
  color: var(--color-muted);
  font-size: var(--type-meta);
  line-height: 1.55;
}

.panel-state strong {
  color: var(--color-ink);
  font-family: var(--font-editorial);
  font-size: 15px;
  font-weight: 500;
}

.panel-error {
  padding: 12px;
  border-left: 3px solid var(--color-wine);
  background: var(--color-wine-soft);
}

.panel-error p { margin: 0; }
.panel-error button { justify-self: start; }

.annotation-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.annotation-list > li {
  position: relative;
  padding: 13px 16px 15px 19px;
  border-bottom: 1px solid var(--color-line);
}

.annotation-list > li::before {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 3px;
  background: inherit;
  content: '';
}

.annotation-list > li[data-color='amber']::before { background: #d6a84b; }
.annotation-list > li[data-color='green']::before { background: #4f7660; }
.annotation-list > li[data-color='rose']::before { background: #8d4a50; }
.annotation-list > li[data-color='blue']::before { background: #4f687d; }

.annotation-list > li > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.annotation-list > li > header button {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
}

.annotation-list > li > header span {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.annotation-list blockquote,
.saved-note {
  margin: 10px 0 0;
  font-size: var(--type-meta);
  line-height: 1.6;
}

.annotation-list blockquote {
  color: var(--color-muted-strong);
  font-family: var(--font-editorial);
}

.saved-note {
  padding: 8px 9px;
  background: var(--color-paper);
  color: var(--color-ink);
}

.item-actions {
  display: flex;
  gap: 6px;
  margin-top: 10px;
}

.item-actions button {
  min-height: 29px;
  border: 0;
  background: transparent;
  color: var(--color-muted);
}

.edit-form {
  display: grid;
  gap: 9px;
  margin-top: 10px;
}

.edit-form label {
  display: grid;
  gap: 5px;
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.edit-form textarea { padding: 8px; font-size: var(--type-meta); }
.color-picker.compact { margin: 0; }
.edit-form > div { display: flex; gap: 6px; }

.delete-confirm {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
  margin-top: 10px;
  padding: 9px;
  background: var(--color-wine-soft);
  color: var(--color-wine);
  font-size: var(--type-meta);
}

.delete-confirm span { flex-basis: 100%; }

@media (forced-colors: active) {
  .color-picker button,
  .annotation-list > li::before {
    border: 2px solid CanvasText;
  }
}
</style>
