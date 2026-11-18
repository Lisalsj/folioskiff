<script setup lang="ts">
import { researchText as t } from '@/stores/locale'
import { ref } from 'vue'
import { PhArrowBendDownRight, PhPlus, PhX } from '@phosphor-icons/vue'

import type { RelatedSearchIntent } from '../api'

type TermArea = 'required' | 'optional' | 'excluded'

const props = defineProps<{
  modelValue: RelatedSearchIntent
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: RelatedSearchIntent]
}>()

const newRequired = ref('')
const newOptional = ref('')
const newExcluded = ref('')

const sourceOptions = [
  ['arxiv', 'arXiv'],
  ['openreview', 'OpenReview'],
  ['europe_pmc', 'Europe PMC'],
  ['ieee', 'IEEE Xplore'],
  ['springer_nature', 'Springer Nature'],
] as const

function update(patch: Partial<RelatedSearchIntent>): void {
  emit('update:modelValue', {
    ...props.modelValue,
    ...patch,
    confirmed: false,
    confirmed_at: null,
  })
}

function editSummary(event: Event): void {
  update({ summary: (event.target as HTMLTextAreaElement).value })
}

function editRequired(groupIndex: number, termIndex: number, event: Event): void {
  const groups = props.modelValue.required_groups.map((group) => [...group])
  groups[groupIndex]![termIndex] = (event.target as HTMLInputElement).value
  update({ required_groups: groups })
}

function editFlat(area: Exclude<TermArea, 'required'>, index: number, event: Event): void {
  const terms = [...props.modelValue[`${area}_terms`]]
  terms[index] = (event.target as HTMLInputElement).value
  update({ [`${area}_terms`]: terms })
}

function removeRequired(groupIndex: number, termIndex: number): void {
  const groups = props.modelValue.required_groups.map((group) => [...group])
  groups[groupIndex]?.splice(termIndex, 1)
  const nonEmpty = groups.filter((group) => group.length)
  if (nonEmpty.length) update({ required_groups: nonEmpty })
}

function removeFlat(area: Exclude<TermArea, 'required'>, index: number): void {
  const terms = [...props.modelValue[`${area}_terms`]]
  terms.splice(index, 1)
  update({ [`${area}_terms`]: terms })
}

function moveRequired(groupIndex: number, termIndex: number, event: Event): void {
  const target = (event.target as HTMLSelectElement).value
  if (!target) return
  const groups = props.modelValue.required_groups.map((group) => [...group])
  const [term] = groups[groupIndex]?.splice(termIndex, 1) ?? []
  if (!term) return
  const nonEmpty = groups.filter((group) => group.length)
  if (target === 'optional') {
    update({ required_groups: nonEmpty, optional_terms: [...props.modelValue.optional_terms, term] })
  } else if (target === 'excluded') {
    update({ required_groups: nonEmpty, excluded_terms: [...props.modelValue.excluded_terms, term] })
  } else if (target.startsWith('required-')) {
    const targetIndex = Number(target.slice('required-'.length))
    const targetGroups = nonEmpty.map((group) => [...group])
    const destination = Math.min(targetIndex, targetGroups.length - 1)
    targetGroups[destination]?.push(term)
    update({ required_groups: targetGroups })
  }
  ;(event.target as HTMLSelectElement).value = ''
}

function moveFlat(area: Exclude<TermArea, 'required'>, index: number, event: Event): void {
  const target = (event.target as HTMLSelectElement).value
  if (!target) return
  const key = `${area}_terms` as const
  const terms = [...props.modelValue[key]]
  const [term] = terms.splice(index, 1)
  if (!term) return
  if (target === 'required') {
    const groups = props.modelValue.required_groups.map((group) => [...group])
    groups[0]?.push(term)
    update({ required_groups: groups, [key]: terms })
  } else if (area === 'optional') {
    update({ optional_terms: terms, excluded_terms: [...props.modelValue.excluded_terms, term] })
  } else {
    update({ excluded_terms: terms, optional_terms: [...props.modelValue.optional_terms, term] })
  }
  ;(event.target as HTMLSelectElement).value = ''
}

function addTerm(area: TermArea): void {
  const draft = area === 'required' ? newRequired : area === 'optional' ? newOptional : newExcluded
  const term = draft.value.trim()
  if (!term) return
  if (area === 'required') {
    update({ required_groups: [...props.modelValue.required_groups, [term]] })
  } else {
    update({ [`${area}_terms`]: [...props.modelValue[`${area}_terms`], term] })
  }
  draft.value = ''
}

function updateNewFlatTerm(area: Exclude<TermArea, 'required'>, event: Event): void {
  const value = (event.target as HTMLInputElement).value
  if (area === 'optional') newOptional.value = value
  else newExcluded.value = value
}

function toggleSource(sourceId: string, event: Event): void {
  const checked = (event.target as HTMLInputElement).checked
  const current = props.modelValue.source_ids
  const next = checked
    ? [...new Set([...current, sourceId])]
    : current.filter((item) => item !== sourceId)
  if (next.length) update({ source_ids: next })
}

function updateMonths(event: Event): void {
  const value = Math.max(1, Math.min(120, Number((event.target as HTMLInputElement).value)))
  if (Number.isFinite(value)) update({ recent_months: value })
}
</script>

<template>
  <section class="intent-editor" data-test="intent-editor" aria-labelledby="related-intent-title">
    <div class="editor-heading">
      <div>
        <p>{{ t('本地草稿 · 尚未检索') }}</p>
        <h3 id="related-intent-title">{{ t('检索意图') }}</h3>
      </div>
      <span>{{ t('版本') }} {{ modelValue.revision }}</span>
    </div>

    <label class="summary-field">
      <span>{{ t('研究焦点') }}</span>
      <textarea
        :value="modelValue.summary"
        :disabled="disabled"
        rows="3"
        maxlength="2000"
        @change="editSummary"
      />
    </label>

    <fieldset class="term-section">
      <legend>{{ t('必需概念组') }} <small>{{ t('组间 AND，组内中英文同义词 OR') }}</small></legend>
      <div v-for="(group, groupIndex) in modelValue.required_groups" :key="groupIndex" class="term-group">
        <strong>{{ t('概念') }} {{ groupIndex + 1 }}</strong>
        <div v-for="(term, termIndex) in group" :key="`${groupIndex}:${termIndex}`" class="term-row">
          <input
            :data-test="`term-required-${groupIndex}-${termIndex}`"
            :aria-label="t('必需概念 {0} 词语 {1}', groupIndex + 1, termIndex + 1)"
            :value="term"
            :disabled="disabled"
            maxlength="120"
            @change="editRequired(groupIndex, termIndex, $event)"
          >
          <select
            :data-test="`move-required-${groupIndex}-${termIndex}`"
            :aria-label="t('移动 {0}', term)"
            :disabled="disabled || (group.length === 1 && modelValue.required_groups.length === 1)"
            @change="moveRequired(groupIndex, termIndex, $event)"
          >
            <option value="">{{ t('移动至…') }}</option>
            <option value="optional">{{ t('增强词') }}</option>
            <option value="excluded">{{ t('排除词') }}</option>
            <option
              v-for="(_, targetIndex) in modelValue.required_groups"
              :key="targetIndex"
              :value="`required-${targetIndex}`"
              :disabled="targetIndex === groupIndex"
            >{{ t('概念') }} {{ targetIndex + 1 }}</option>
          </select>
          <button
            type="button"
            :aria-label="t('删除 {0}', term)"
            :disabled="disabled || (group.length === 1 && modelValue.required_groups.length === 1)"
            @click="removeRequired(groupIndex, termIndex)"
          ><PhX :size="13" aria-hidden="true" /></button>
        </div>
      </div>
      <div class="term-add">
        <input
          v-model="newRequired"
          data-test="add-required-input"
          :disabled="disabled"
          maxlength="120"
          :placeholder="t('新增一个概念组，如 latent diffusion')"
          @keydown.enter.prevent="addTerm('required')"
        >
        <button type="button" data-test="add-required" :disabled="disabled || !newRequired.trim()" @click="addTerm('required')">
          <PhPlus :size="13" aria-hidden="true" /> {{ t('新增概念组') }}
        </button>
      </div>
    </fieldset>

    <fieldset v-for="area in (['optional', 'excluded'] as const)" :key="area" class="term-section compact">
      <legend>{{ area === 'optional' ? t('增强关键词') : t('排除关键词') }}</legend>
      <div v-for="(term, index) in modelValue[`${area}_terms`]" :key="`${area}:${index}`" class="term-row">
        <input
          :value="term"
          :aria-label="t('{0}关键词 {1}', area === 'optional' ? t('增强') : t('排除'), index + 1)"
          :disabled="disabled"
          maxlength="120"
          @change="editFlat(area, index, $event)"
        >
        <select :aria-label="t('移动 {0}', term)" :disabled="disabled" @change="moveFlat(area, index, $event)">
          <option value="">{{ t('移动至…') }}</option>
          <option value="required">{{ t('必需概念 1') }}</option>
          <option :value="area === 'optional' ? 'excluded' : 'optional'">
            {{ area === 'optional' ? t('排除词') : t('增强词') }}
          </option>
        </select>
        <button type="button" :aria-label="t('删除 {0}', term)" :disabled="disabled" @click="removeFlat(area, index)">
          <PhX :size="13" aria-hidden="true" />
        </button>
      </div>
      <div class="term-add">
        <input
          :value="area === 'optional' ? newOptional : newExcluded"
          :data-test="`add-${area}-input`"
          :disabled="disabled"
          maxlength="120"
          :placeholder="area === 'optional' ? t('新增增强词') : t('新增排除词')"
          @input="updateNewFlatTerm(area, $event)"
          @keydown.enter.prevent="addTerm(area)"
        >
        <button
          type="button"
          :data-test="`add-${area}`"
          :disabled="disabled || !(area === 'optional' ? newOptional : newExcluded).trim()"
          @click="addTerm(area)"
        ><PhPlus :size="13" aria-hidden="true" /> {{ t('添加') }}</button>
      </div>
    </fieldset>

    <div class="range-grid">
      <label>
        <span>{{ t('回看月份') }}</span>
        <input
          data-test="recent-months"
          type="number"
          min="1"
          max="120"
          :value="modelValue.recent_months"
          :disabled="disabled"
          @change="updateMonths"
        >
      </label>
      <fieldset>
        <legend>{{ t('学术来源') }}</legend>
        <label v-for="[sourceId, label] in sourceOptions" :key="sourceId">
          <input
            type="checkbox"
            :data-test="`source-${sourceId}`"
            :checked="modelValue.source_ids.includes(sourceId)"
            :disabled="disabled || (modelValue.source_ids.length === 1 && modelValue.source_ids[0] === sourceId)"
            @change="toggleSource(sourceId, $event)"
          >
          {{ label }}
        </label>
      </fieldset>
    </div>

    <section
      v-if="modelValue.diff && (
        modelValue.diff.added_terms.length || modelValue.diff.removed_terms.length
          || modelValue.diff.moved_terms.length || modelValue.diff.added_source_ids.length
          || modelValue.diff.removed_source_ids.length || modelValue.diff.summary_changed
          || modelValue.diff.recent_months_changed
      )"
      class="intent-diff"
      data-test="intent-diff"
      :aria-label="t('上一版本到当前版本的差异')"
    >
      <PhArrowBendDownRight :size="15" aria-hidden="true" />
      <div>
        <strong>{{ t('本轮修改') }}</strong>
        <p v-if="modelValue.diff.added_terms.length">{{ t('新增：') }}{{ modelValue.diff.added_terms.join('、') }}</p>
        <p v-if="modelValue.diff.removed_terms.length">{{ t('删除：') }}{{ modelValue.diff.removed_terms.join('、') }}</p>
        <p v-if="modelValue.diff.moved_terms.length">{{ t('移动：') }}{{ modelValue.diff.moved_terms.join('、') }}</p>
        <p v-if="modelValue.diff.summary_changed">{{ t('研究焦点已修改') }}</p>
        <p v-if="modelValue.diff.recent_months_changed">{{ t('月份范围已修改') }}</p>
      </div>
    </section>
  </section>
</template>

<style scoped>
.intent-editor { display: grid; gap: 16px; }
.editor-heading { display: flex; align-items: end; justify-content: space-between; gap: 12px; }
.editor-heading p, .editor-heading h3 { margin: 0; }
.editor-heading p { color: var(--color-forest); font-size: var(--type-meta); letter-spacing: .08em; text-transform: uppercase; }
.editor-heading h3 { margin-top: 3px; font-family: var(--font-editorial); font-size: 20px; font-weight: 500; }
.editor-heading > span { color: var(--color-muted); font-size: var(--type-meta); }
.summary-field { display: grid; gap: 5px; }
.summary-field > span, .range-grid > label > span { color: var(--color-muted-strong); font-size: var(--type-meta); font-weight: 700; }
textarea, input, select { min-width: 0; border: 1px solid var(--color-line); border-radius: var(--radius-sm); background: var(--color-paper-raised); color: var(--color-ink); font: inherit; }
textarea { resize: vertical; padding: 9px 10px; font-size: var(--type-meta); line-height: 1.55; }
.term-section, .range-grid fieldset { min-width: 0; margin: 0; padding: 11px; border: 1px solid var(--color-line); border-radius: var(--radius-sm); }
.term-section legend, .range-grid legend { padding: 0 5px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 700; }
.term-section legend small { margin-left: 5px; color: var(--color-muted); font-weight: 400; }
.term-group + .term-group { margin-top: 11px; padding-top: 10px; border-top: 1px dashed var(--color-line); }
.term-group > strong { display: block; margin-bottom: 6px; color: var(--color-muted); font-size: var(--type-meta); }
.term-row { display: grid; grid-template-columns: minmax(0, 1fr) 82px 28px; gap: 5px; margin-top: 5px; }
.term-row input, .term-row select, .term-add input, .range-grid input { min-height: 30px; padding: 0 7px; font-size: var(--type-meta); }
.term-row button { display: grid; width: 28px; height: 30px; place-items: center; border: 0; background: transparent; color: var(--color-muted); cursor: pointer; }
.term-add { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px; margin-top: 8px; }
.term-add button { min-height: 30px; display: inline-flex; align-items: center; gap: 4px; padding: 0 9px; border: 1px solid var(--color-line); border-radius: var(--radius-sm); background: transparent; color: var(--color-forest); font-size: var(--type-meta); cursor: pointer; }
.range-grid { display: grid; grid-template-columns: 105px minmax(0, 1fr); gap: 10px; }
.range-grid > label { display: grid; align-content: start; gap: 6px; }
.range-grid fieldset { display: flex; flex-wrap: wrap; gap: 7px 12px; }
.range-grid fieldset label { display: inline-flex; align-items: center; gap: 4px; color: var(--color-muted-strong); font-size: var(--type-meta); }
.range-grid fieldset input { width: 14px; min-height: 14px; accent-color: var(--color-forest); }
.intent-diff { display: flex; align-items: flex-start; gap: 8px; padding: 10px; border-left: 2px solid var(--color-wine); background: var(--color-wine-soft); color: var(--color-muted-strong); }
.intent-diff strong { color: var(--color-wine); font-size: var(--type-meta); }
.intent-diff p { margin: 3px 0 0; font-size: var(--type-meta); line-height: 1.45; }
:is(input, select, textarea, button):focus-visible { outline: 3px solid var(--color-focus); outline-offset: 1px; }
:is(input, select, textarea, button):disabled { cursor: not-allowed; opacity: .55; }
@media (max-width: 520px) { .range-grid { grid-template-columns: 1fr; } .term-row { grid-template-columns: minmax(0, 1fr) 76px 28px; } }
</style>
