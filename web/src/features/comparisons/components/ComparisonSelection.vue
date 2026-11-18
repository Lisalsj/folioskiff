<script setup lang="ts">
import { researchText as t } from '@/stores/locale'
import { computed } from 'vue'
import { PhArrowDown, PhArrowUp, PhBooks, PhScales, PhX } from '@phosphor-icons/vue'

import LibraryWorkPicker from '@/components/LibraryWorkPicker.vue'
import type { LibraryWork } from '@/features/library/ordered-selection'

const props = defineProps<{
  selectedWorks: readonly LibraryWork[]
  selectedIds: readonly string[]
  maximum: number
}>()

const emit = defineEmits<{
  toggle: [work: LibraryWork, checked: boolean]
  move: [workId: string, direction: -1 | 1]
}>()

const plannedMode = computed<'empty' | 'insufficient' | 'direct' | 'staged'>(() => {
  if (props.selectedIds.length === 0) return 'empty'
  if (props.selectedIds.length < 2) return 'insufficient'
  return props.selectedIds.length <= 10 ? 'direct' : 'staged'
})
</script>

<template>
  <section class="comparison-layout" :aria-label="t('比较论文选择与顺序')">
    <div class="library-pane">
      <div class="section-heading">
        <div><span>01</span><h2>{{ t('从论文库选择') }}</h2></div>
        <small>{{ t('最多') }} {{ maximum }} {{ t('篇') }}</small>
      </div>
      <LibraryWorkPicker
        :selected-ids="selectedIds"
        :maximum="maximum"
        :option-test-id="(work) => `select-${work.id}`"
        @toggle="(work, checked) => emit('toggle', work, checked)"
      />
    </div>

    <div class="order-pane">
      <div class="section-heading">
        <div><span>02</span><h2>{{ t('确认顺序与路径') }}</h2></div>
      </div>
      <div v-if="selectedWorks.length === 0" class="empty-selection">
        <PhBooks :size="28" weight="thin" aria-hidden="true" />
        <p>{{ t('尚未选择论文') }}</p>
        <small>{{ t('选择顺序会写入报告版本，并在每条证据旁保留。') }}</small>
      </div>
      <ol v-else class="selected-list">
        <li v-for="(work, index) in selectedWorks" :key="work.id" :data-test="`selected-${work.id}`">
          <span>{{ String(index + 1).padStart(2, '0') }}</span>
          <strong>{{ work.title }}</strong>
          <div>
            <button
              type="button"
              :data-test="`move-${work.id}-up`"
              :disabled="index === 0"
              :aria-label="t('将《{0}》上移', work.title)"
              @click="emit('move', work.id, -1)"
            ><PhArrowUp :size="15" aria-hidden="true" /></button>
            <button
              type="button"
              :data-test="`move-${work.id}-down`"
              :disabled="index === selectedWorks.length - 1"
              :aria-label="t('将《{0}》下移', work.title)"
              @click="emit('move', work.id, 1)"
            ><PhArrowDown :size="15" aria-hidden="true" /></button>
            <button
              type="button"
              :aria-label="t('移除《{0}》', work.title)"
              @click="emit('toggle', work, false)"
            ><PhX :size="15" aria-hidden="true" /></button>
          </div>
        </li>
      </ol>

      <div data-test="comparison-mode-note" class="mode-note" :class="plannedMode">
        <PhScales :size="20" aria-hidden="true" />
        <p v-if="plannedMode === 'empty'">{{ t('选择至少两篇论文后，系统会说明实际综合路径。') }}</p>
        <p v-else-if="plannedMode === 'insufficient'">{{ t('还需要一篇不同的规范论文。') }}</p>
        <p v-else-if="plannedMode === 'direct'">{{ t('2–10 篇采用') }}<strong>{{ t('一次综合') }}</strong>{{ t('；每篇仍独立显示证据覆盖率。') }}</p>
        <p v-else>{{ t('11 篇以上先') }}<strong>{{ t('逐篇提取证据') }}</strong>{{ t('，再按') }}<strong>{{ t('每批最多 10 篇') }}</strong>{{ t('综合，最后汇总并披露遗漏项。') }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.comparison-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(340px, 0.95fr);
  gap: 42px;
  margin-top: 42px;
}

.library-pane,
.order-pane { min-width: 0; }

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 18px;
}

.section-heading > div { display: flex; align-items: baseline; gap: 11px; }
.section-heading span { color: var(--color-wine); font-family: var(--font-editorial); font-size: 13px; }
.section-heading h2 { margin: 0; font-family: var(--font-editorial); font-size: 25px; font-weight: 500; }
.section-heading small { color: var(--color-muted); font-size: var(--type-meta); }

.selected-list { padding: 0; margin: 0; list-style: none; }
.selected-list li + li { border-top: 1px solid var(--color-line); }

.empty-selection { padding: 40px 24px; border-block: 1px solid var(--color-line); color: var(--color-muted); text-align: center; }
.empty-selection p { margin: 12px 0 5px; color: var(--color-ink); font-family: var(--font-editorial); font-size: 18px; }
.selected-list { border-block: 1px solid var(--color-line); }
.selected-list li { min-height: 58px; display: grid; grid-template-columns: 30px minmax(0, 1fr) auto; align-items: center; gap: 11px; padding: 9px 5px; }
.selected-list > li > span { color: var(--color-wine); font-family: var(--font-editorial); font-size: 12px; }
.selected-list strong { overflow-wrap: anywhere; font-family: var(--font-editorial); font-size: 15px; }
.selected-list li > div { display: flex; gap: 4px; }
.selected-list button { width: 30px; height: 30px; display: grid; place-items: center; border: 0; background: transparent; color: var(--color-muted); cursor: pointer; }
.selected-list button:hover:not(:disabled) { color: var(--color-forest); }
.selected-list button:disabled { opacity: 0.25; cursor: default; }
.selected-list button:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }

.mode-note { display: flex; align-items: flex-start; gap: 11px; margin-top: 18px; padding: 14px 0; border-bottom: 1px solid var(--color-line); color: var(--color-muted-strong); }
.mode-note svg { flex: none; color: var(--color-forest); }
.mode-note p { margin: 0; font-size: 12px; line-height: 1.7; }

@media (max-width: 980px) { .comparison-layout { grid-template-columns: 1fr; } }
@media (max-width: 640px) {
  .selected-list li { grid-template-columns: 26px minmax(0, 1fr); }
  .selected-list li > div { grid-column: 2; justify-content: flex-end; }
  .selected-list button { width: 44px; height: 44px; }
}
</style>
