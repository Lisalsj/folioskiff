<script setup lang="ts">
import { messageRef, messageTemplate, settingsText as t } from '@/stores/locale'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { PhArrowSquareOut, PhFileCsv, PhShieldCheck, PhUploadSimple } from '@phosphor-icons/vue'

import type { components } from '@/api/schema'
import ScoreRuleEditor from '@/components/ScoreRuleEditor.vue'
import { apiJson, apiVoid } from '@/lib/json-api'

import { useSettingsDirtyState } from './settings-dirty-state'

type RatingCatalog = components['schemas']['RatingCatalogResponse']
type RatingCatalogKind = components['schemas']['RatingCatalogKind']
type CCFCatalog = components['schemas']['CCFCatalogResponse']

const ratingCatalogs = ref<RatingCatalog[]>([])
const ccfCatalog = ref<CCFCatalog>()
const selectedRatingFile = ref<File>()
const ratingFileInput = ref<HTMLInputElement>()
const busy = ref(false)
const loading = ref(true)
const error = messageRef(t)
const notice = messageRef(t)
const dirtyState = useSettingsDirtyState()
const ratingForm = reactive<{
  kind: RatingCatalogKind
  catalogName: string
  catalogVersion: string
  sourceNote: string
  rightsConfirmed: boolean
}>({
  kind: 'jcr',
  catalogName: '',
  catalogVersion: '',
  sourceNote: '',
  rightsConfirmed: false,
})

const hasDraft = computed(() => Boolean(
  selectedRatingFile.value
  || ratingForm.catalogName.trim()
  || ratingForm.catalogVersion.trim()
  || ratingForm.sourceNote.trim()
  || ratingForm.rightsConfirmed,
))
const canImport = computed(() => Boolean(
  selectedRatingFile.value
  && ratingForm.catalogName.trim()
  && ratingForm.catalogVersion.trim()
  && ratingForm.sourceNote.trim()
  && ratingForm.rightsConfirmed,
))

function ratingKindLabel(kind: RatingCatalogKind): string {
  if (kind === 'jcr') return 'JCR'
  if (kind === 'cas') return t('CAS 分区')
  return t('新锐榜单')
}

function selectRatingFile(event: Event): void {
  selectedRatingFile.value = (event.currentTarget as HTMLInputElement).files?.[0]
}

function resetDraft(): void {
  selectedRatingFile.value = undefined
  ratingForm.kind = 'jcr'
  ratingForm.catalogName = ''
  ratingForm.catalogVersion = ''
  ratingForm.sourceNote = ''
  ratingForm.rightsConfirmed = false
  if (ratingFileInput.value) ratingFileInput.value.value = ''
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [catalogItems, builtinCcf] = await Promise.all([
      apiJson<RatingCatalog[]>('/api/v1/scores/rating-catalogs'),
      apiJson<CCFCatalog>('/api/v1/scores/rating-catalogs/ccf'),
    ])
    ratingCatalogs.value = catalogItems
    ccfCatalog.value = builtinCcf
  } catch {
    error.value = '暂时无法读取评分规则与本地评级目录，请稍后重试。'
  } finally {
    loading.value = false
  }
}

async function importRatingCatalog(): Promise<void> {
  const file = selectedRatingFile.value
  if (!file || !canImport.value || busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  const payload = new FormData()
  payload.append('kind', ratingForm.kind)
  payload.append('catalog_name', ratingForm.catalogName.trim())
  payload.append('catalog_version', ratingForm.catalogVersion.trim())
  payload.append('source_note', ratingForm.sourceNote.trim())
  payload.append('rights_confirmed', String(ratingForm.rightsConfirmed))
  payload.append('file', file)
  try {
    const imported = await apiJson<RatingCatalog>('/api/v1/scores/rating-catalogs/import', {
      method: 'POST',
      body: payload,
    })
    ratingCatalogs.value = [
      imported,
      ...ratingCatalogs.value
        .filter((catalog) => catalog.id !== imported.id)
        .map((catalog) => catalog.kind === imported.kind ? { ...catalog, active: false } : catalog),
    ]
    notice.value = messageTemplate('{0} {1} 已导入并启用。', ratingKindLabel(imported.kind), imported.catalog_version)
    resetDraft()
  } catch {
    error.value = '无法导入本地评级目录；请检查文件格式、授权确认和必填信息。'
  } finally {
    busy.value = false
  }
}

async function deactivateRatingCatalog(kind: RatingCatalogKind): Promise<void> {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await apiVoid(`/api/v1/scores/rating-catalogs/${encodeURIComponent(kind)}/active`, {
      method: 'DELETE',
    })
    ratingCatalogs.value = ratingCatalogs.value.map((catalog) =>
      catalog.kind === kind ? { ...catalog, active: false } : catalog,
    )
    notice.value = messageTemplate('{0} 本地目录已停用；历史评分快照仍保留原证据。', ratingKindLabel(kind))
  } catch {
    error.value = '无法停用本地评级目录，请稍后重试。'
  } finally {
    busy.value = false
  }
}

watch(hasDraft, (value) => dirtyState.setDirty(value))
onMounted(() => void load())
</script>

<template>
  <section class="settings-section" aria-labelledby="recommendation-settings-title">
    <header class="page-heading">
      <h2 id="recommendation-settings-title" data-settings-heading tabindex="-1">{{ t('推荐与评分') }}</h2>
      <span>{{ t('评分规则、场所证据和推荐排序保持可解释、可版本化，并与 Agent 输出分离。') }}</span>
    </header>

    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice success" role="status">{{ notice }}</p>

    <ScoreRuleEditor />

    <section id="rating-catalogs" aria-labelledby="rating-settings-title">
      <div class="section-heading">
        <div>
          <h3 id="rating-settings-title">{{ t('本地评级目录') }}</h3>
        </div>
        <span>{{ ratingCatalogs.filter((catalog) => catalog.active).length }} {{ t('个已启用') }}</span>
      </div>

      <p v-if="loading" class="quiet-state" role="status">{{ t('正在读取评级目录') }}</p>
      <template v-else>
        <article v-if="ccfCatalog" class="ccf-reference">
          <div class="reference-mark" aria-hidden="true"><PhShieldCheck :size="22" /></div>
          <div>
            <h4>{{ t('CCF 2022 第六版') }}</h4>
            <p>
              {{ ccfCatalog.entry_count }} {{ t('个场所，采用 2024-06-28 官方更名修订；等级仍属于 2022 第六版。') }}
            </p>
            <a :href="ccfCatalog.source_url" target="_blank" rel="noreferrer">
              {{ t('查看 CCF 官方目录') }}
              <PhArrowSquareOut :size="15" aria-hidden="true" />
            </a>
          </div>
          <code :title="ccfCatalog.source_pdf_sha256">
            {{ t('PDF 校验') }} {{ ccfCatalog.source_pdf_sha256.slice(0, 12) }}…
          </code>
        </article>

        <p class="rating-explainer">
          <strong>{{ t('JCR、CAS 与新锐榜单始终独立。') }}</strong>
          {{ t('缺失或未命中只降低覆盖度，不会记为低质量；本地文件不会上传或发布。') }}
        </p>

        <div class="rating-layout">
          <form class="rating-import-form" data-test="rating-import-form" @submit.prevent="importRatingCatalog">
            <div class="import-heading">
              <PhUploadSimple :size="21" aria-hidden="true" />
              <div>
                <h4>{{ t('导入合法取得的目录') }}</h4>
                <p>{{ t('支持 UTF-8 / GB18030 CSV 与不含公式的 XLSX，最大 10 MiB。') }}</p>
              </div>
            </div>

            <div class="form-grid">
              <label>
                <span>{{ t('目录类型') }}</span>
                <select v-model="ratingForm.kind" data-test="rating-kind">
                  <option value="jcr">JCR</option>
                  <option value="cas">{{ t('CAS 分区') }}</option>
                  <option value="emerging">{{ t('新锐榜单') }}</option>
                </select>
              </label>
              <label>
                <span>{{ t('目录名称') }}</span>
                <input v-model="ratingForm.catalogName" data-test="rating-name" maxlength="160" />
              </label>
              <label>
                <span>{{ t('版本') }}</span>
                <input v-model="ratingForm.catalogVersion" data-test="rating-version" maxlength="80" />
              </label>
              <label class="wide-field">
                <span>{{ t('来源说明') }}</span>
                <input v-model="ratingForm.sourceNote" data-test="rating-source-note" maxlength="2000" />
              </label>
              <label class="wide-field">
                <span>{{ t('CSV 或 XLSX 文件') }}</span>
                <input
                  ref="ratingFileInput"
                  data-test="rating-file"
                  type="file"
                  accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  @change="selectRatingFile"
                />
              </label>
            </div>

            <label class="rights-check">
              <input v-model="ratingForm.rightsConfirmed" data-test="rating-rights" type="checkbox" />
              <span>{{ t('我确认有权在本机使用此文件。') }}</span>
            </label>
            <button class="import-button" type="submit" :disabled="busy || !canImport">
              <PhUploadSimple :size="17" aria-hidden="true" />
              {{ busy ? t('处理中') : t('验证并导入') }}
            </button>
          </form>

          <div class="catalog-history" :aria-label="t('已导入评级目录')">
            <div class="history-heading">
              <h4>{{ t('本机目录') }}</h4>
              <span>{{ ratingCatalogs.length }} {{ t('个版本') }}</span>
            </div>
            <p v-if="ratingCatalogs.length === 0" class="quiet-state">
              {{ t('尚未导入。本地榜单缺失只会降低场所证据覆盖度。') }}
            </p>
            <article v-for="catalog in ratingCatalogs" :key="catalog.id" class="catalog-row">
              <PhFileCsv :size="19" aria-hidden="true" />
              <div>
                <strong>{{ catalog.catalog_name }}</strong>
                <span>{{ catalog.active ? t('已启用') : t('已停用') }}</span>
                <p>{{ ratingKindLabel(catalog.kind) }} · {{ catalog.catalog_version }} · {{ catalog.row_count }} {{ t('条') }}</p>
                <small>{{ catalog.filename }} · {{ catalog.file_sha256.slice(0, 10) }}…</small>
              </div>
              <button
                v-if="catalog.active"
                :data-test="`rating-deactivate-${catalog.kind}`"
                type="button"
                :disabled="busy"
                @click="deactivateRatingCatalog(catalog.kind)"
              >
                {{ t('停用') }}
              </button>
            </article>
          </div>
        </div>
      </template>
    </section>
  </section>
</template>

<style scoped>
.settings-section { display: grid; gap: 38px; }
.page-heading { padding-bottom: 16px; border-bottom: 2px solid var(--color-forest); }
.page-heading > p, .section-heading p { margin: 0 0 7px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: .12em; }
.page-heading h2, .section-heading h3, h4 { margin: 0; font-family: var(--font-editorial); font-weight: 500; }
.page-heading h2 { font-size: 30px; }
.page-heading h2:focus { outline: none; }
.page-heading > span { display: block; margin-top: 9px; color: var(--color-muted-strong); line-height: 1.65; }
.notice { margin: -16px 0 0; padding-left: 14px; border-left: 3px solid currentColor; }
.notice.error { color: var(--color-wine); }
.notice.success { color: var(--color-forest); }
.section-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; padding-bottom: 14px; }
.section-heading h3 { font-size: 23px; }
.section-heading > span { color: var(--color-muted); font-size: var(--type-meta); }
.quiet-state { margin: 0; padding: 24px 0; color: var(--color-muted); }
.ccf-reference { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 16px; align-items: center; padding: 22px 0; border-top: 1px solid var(--color-line); border-bottom: 1px solid var(--color-line); }
.reference-mark { width: 40px; height: 40px; display: grid; place-items: center; border: 1px solid var(--color-forest); border-radius: 50%; color: var(--color-forest); }
.ccf-reference h4, .import-heading h4, .history-heading h4 { font-size: 19px; }
.ccf-reference p, .import-heading p, .catalog-row p { margin: 6px 0 0; color: var(--color-muted-strong); font-size: var(--type-meta); line-height: 1.55; }
.ccf-reference a { display: inline-flex; align-items: center; gap: 5px; margin-top: 9px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 700; }
.ccf-reference code { color: var(--color-muted); font-size: var(--type-meta); white-space: nowrap; }
.rating-explainer { margin: 0; padding: 16px 0; color: var(--color-muted-strong); line-height: 1.65; }
.rating-explainer strong { color: var(--color-forest); }
.rating-layout { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(280px, .95fr); gap: clamp(32px, 6vw, 72px); padding-top: 26px; }
.import-heading { display: flex; gap: 11px; padding-bottom: 16px; border-bottom: 1px solid var(--color-line); }
.import-heading > svg { flex: 0 0 auto; color: var(--color-forest); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 15px; margin-top: 18px; }
.form-grid label > span { display: block; margin-bottom: 6px; color: var(--color-muted-strong); font-size: var(--type-meta); font-weight: 700; }
.form-grid input, .form-grid select { width: 100%; min-height: 40px; box-sizing: border-box; padding: 0 10px; border: 1px solid var(--color-line-strong); border-radius: var(--radius-sm); background: var(--color-paper-raised); color: var(--color-ink); }
.form-grid input[type='file'] { padding: 8px 10px; font-size: var(--type-meta); }
.wide-field { grid-column: 1 / -1; }
.rights-check { display: flex; gap: 9px; align-items: flex-start; margin-top: 16px; color: var(--color-muted-strong); font-size: var(--type-meta); }
.rights-check input { accent-color: var(--color-forest); }
.import-button { min-height: 42px; margin-top: 16px; display: inline-flex; align-items: center; gap: 7px; padding: 0 16px; border: 1px solid var(--color-forest); border-radius: var(--radius-sm); background: var(--color-forest); color: var(--color-on-accent); font: inherit; font-weight: 700; cursor: pointer; }
.import-button:disabled { opacity: .5; cursor: not-allowed; }
.history-heading { display: flex; justify-content: space-between; gap: 14px; padding-bottom: 16px; border-bottom: 1px solid var(--color-line); }
.history-heading span { color: var(--color-muted); font-size: var(--type-meta); }
.catalog-row { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 11px; padding: 16px 0; border-bottom: 1px solid var(--color-line); }
.catalog-row > svg { color: var(--color-forest); }
.catalog-row strong, .catalog-row span { display: block; }
.catalog-row span { margin-top: 3px; color: var(--color-forest); font-size: var(--type-meta); }
.catalog-row small { color: var(--color-muted); font-size: var(--type-meta); overflow-wrap: anywhere; }
.catalog-row button { align-self: start; padding: 5px 8px; border: 1px solid var(--color-line-strong); border-radius: var(--radius-sm); background: transparent; color: var(--color-wine); cursor: pointer; }
a:focus-visible, input:focus-visible, select:focus-visible, button:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }

@media (max-width: 900px) {
  .rating-layout { grid-template-columns: 1fr; }
  .ccf-reference { grid-template-columns: auto 1fr; }
  .ccf-reference code { grid-column: 2; }
}

@media (max-width: 520px) {
  .form-grid { grid-template-columns: 1fr; }
  .wide-field { grid-column: auto; }
  .ccf-reference { grid-template-columns: 1fr; }
  .ccf-reference code { grid-column: auto; }
  .reference-mark { display: none; }
}
</style>
