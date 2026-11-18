<script setup lang="ts">
import { publishingText as t } from '@/stores/locale'
import { PhArrowDown, PhArrowUp, PhFileText, PhX } from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import LibraryWorkPicker from '@/components/LibraryWorkPicker.vue'
import type { LibraryWork } from '@/features/library/ordered-selection'

type FormalReport = components['schemas']['AnalysisReportSummaryResponse']

defineProps<{
  maximum: number
  selectedIds: readonly string[]
  selectedWorks: readonly LibraryWork[]
  reportIds: Readonly<Record<string, string>>
  reportsByWork: Readonly<Record<string, readonly FormalReport[]>>
  reportLoading: Readonly<Record<string, boolean>>
  reportErrors: Readonly<Record<string, boolean>>
}>()

const emit = defineEmits<{
  toggle: [work: LibraryWork, checked: boolean]
  move: [workId: string, direction: -1 | 1]
  'select-report': [workId: string, reportId: string]
}>()

function reportLabel(item: FormalReport): string {
  const evidence = item.evidence_level === 'full_text' ? t('全文证据') : t('摘要证据')
  const language = item.language === 'bilingual'
    ? t('双语')
    : item.language === 'en' ? t('英文') : t('中文')
  return t('正式报告 v{0} · {1} · {2}', item.version, evidence, language)
}

function selectReport(workId: string, event: Event): void {
  emit('select-report', workId, (event.currentTarget as HTMLSelectElement).value)
}
</script>

<template>
  <div class="selection-grid">
    <section aria-labelledby="publication-library-title">
      <div class="section-heading compact">
        <div>
          <span aria-hidden="true">02</span>
          <div>
            <p>{{ t('论文库') }}</p>
            <h2 id="publication-library-title">{{ t('选择公开论文') }}</h2>
          </div>
        </div>
        <small>{{ selectedIds.length }} / {{ maximum }}</small>
      </div>
      <LibraryWorkPicker
        :selected-ids="selectedIds"
        :maximum="maximum"
        :option-test-id="(_, index) => `select-publishing-work-${index + 1}`"
        @toggle="(work, checked) => emit('toggle', work, checked)"
      />
    </section>

    <section aria-labelledby="publication-order-title">
      <div class="section-heading compact">
        <div>
          <span aria-hidden="true">03</span>
          <div>
            <p>{{ t('顺序与报告') }}</p>
            <h2 id="publication-order-title">{{ t('只附上正式版本') }}</h2>
          </div>
        </div>
      </div>
      <p class="boundary-note">
        {{ t('批注、聊天、失败草稿和完整提示词没有发布入口。报告留空时只公开论文元数据。') }}
      </p>
      <div v-if="selectedWorks.length === 0" class="empty-selection">
        <PhFileText :size="27" weight="thin" aria-hidden="true" />
        <p>{{ t('从左侧选择论文后，在这里安排公开顺序。') }}</p>
      </div>
      <ol v-else class="selected-list">
        <li
          v-for="(work, index) in selectedWorks"
          :key="work.id"
          :data-test="`selected-publishing-work-${work.id}`"
        >
          <span class="position">{{ String(index + 1).padStart(2, '0') }}</span>
          <div class="selected-copy">
            <strong>{{ work.title }}</strong>
            <label>
              <span>{{ t('随附报告') }}</span>
              <select
                :data-test="`publishing-report-${work.id}`"
                :value="reportIds[work.id] ?? ''"
                :disabled="reportLoading[work.id]"
                @change="selectReport(work.id, $event)"
              >
                <option value="">
                  {{ reportLoading[work.id] ? t('正在读取正式报告…') : t('不公开分析报告') }}
                </option>
                <option
                  v-for="item in reportsByWork[work.id] ?? []"
                  :key="item.id"
                  :value="item.id"
                >{{ reportLabel(item) }}</option>
              </select>
            </label>
            <small v-if="reportErrors[work.id]" class="inline-error">
              {{ t('正式报告暂时无法读取；仍可只发布论文元数据。') }}
            </small>
          </div>
          <div class="order-actions">
            <button
              :data-test="`move-publishing-${work.id}-up`"
              type="button"
              :disabled="index === 0"
              :aria-label="t('上移《{0}》', work.title)"
              @click="emit('move', work.id, -1)"
            ><PhArrowUp :size="16" aria-hidden="true" /></button>
            <button
              :data-test="`move-publishing-${work.id}-down`"
              type="button"
              :disabled="index === selectedWorks.length - 1"
              :aria-label="t('下移《{0}》', work.title)"
              @click="emit('move', work.id, 1)"
            ><PhArrowDown :size="16" aria-hidden="true" /></button>
            <button
              type="button"
              :aria-label="t('移除《{0}》', work.title)"
              @click="emit('toggle', work, false)"
            ><PhX :size="16" aria-hidden="true" /></button>
          </div>
        </li>
      </ol>
    </section>
  </div>
</template>

<style scoped>
.selection-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(360px, 1.1fr);
  gap: clamp(30px, 5vw, 58px);
  margin-top: 48px;
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 14px;
}

.section-heading > div { display: flex; align-items: flex-start; gap: 12px; }
.section-heading > div > span { padding-top: 3px; color: var(--color-wine); font-family: var(--font-editorial); font-size: 13px; }
.section-heading p { margin: 0 0 8px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: 0.14em; text-transform: uppercase; }
.section-heading h2 { margin: 0; font-family: var(--font-editorial); font-size: 22px; font-weight: 500; }
.section-heading small { color: var(--color-muted); font-size: var(--type-meta); }

.selected-list { padding: 0; margin: 0; border-block: 1px solid var(--color-line); list-style: none; }
.selected-list li + li { border-top: 1px solid var(--color-line); }
.selected-copy > strong { overflow-wrap: anywhere; font-family: var(--font-editorial); font-size: 16px; font-weight: 600; line-height: 1.35; }
.boundary-note { margin: -2px 0 15px; padding: 11px 0 11px 13px; border-left: 2px solid var(--color-forest); color: var(--color-muted); font-size: var(--type-meta); line-height: 1.7; }
.empty-selection { color: var(--color-muted); font-size: 12px; line-height: 1.7; }
.empty-selection { padding: 42px 20px; border-block: 1px solid var(--color-line); text-align: center; }
.empty-selection p { margin: 12px 0 0; }
.selected-list li { display: grid; grid-template-columns: 30px minmax(0, 1fr) auto; gap: 11px; padding: 14px 4px; }
.position { color: var(--color-wine); font-family: var(--font-editorial); font-size: 12px; }
.selected-copy { min-width: 0; }
.selected-copy label { display: grid; gap: 7px; margin-top: 11px; color: var(--color-muted-strong); font-size: var(--type-meta); font-weight: 650; }
.selected-copy select { width: 100%; padding: 8px 11px; border: 1px solid var(--color-line); border-radius: var(--radius-sm); background: var(--color-paper-raised); color: var(--color-ink); font-size: 12px; }
.inline-error { display: block; margin-top: 7px; color: var(--color-danger); font-size: var(--type-meta); }
.order-actions { display: flex; gap: 3px; }
.order-actions button { width: 30px; height: 30px; display: grid; place-items: center; border: 0; background: transparent; color: var(--color-muted); cursor: pointer; }
.order-actions button:hover:not(:disabled) { color: var(--color-forest); }
.order-actions button:disabled { opacity: 0.3; cursor: default; }
button:focus-visible,
select:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }

@media (max-width: 980px) { .selection-grid { grid-template-columns: 1fr; } }
@media (max-width: 680px) {
  .selected-list li { grid-template-columns: 26px minmax(0, 1fr); }
  .order-actions { grid-column: 2; justify-content: flex-end; }
  .order-actions button { width: 44px; height: 44px; }
}
</style>
