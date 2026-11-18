<script setup lang="ts">
import { libraryText as t, messageRef } from '@/stores/locale'
import { computed, ref, watch } from 'vue'
import {
  PhArrowRight,
  PhCheckCircle,
  PhFilePdf,
  PhLink,
  PhUploadSimple,
  PhX,
} from '@phosphor-icons/vue'
import {
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from 'reka-ui'

import type { components } from '@/api/schema'
import { ApiResponseError, apiJson } from '@/lib/json-api'

type PdfImportResponse = components['schemas']['PdfImportResponse']
type ReferenceImportResponse = components['schemas']['ReferenceImportResponse']
type ReferenceKind = components['schemas']['ReferenceKind']

export interface ImportTargetWork {
  id: string
  title: string
}

export interface LibraryImportResult {
  workId: string
  documentVersionId: string | null
  kind: 'pdf' | 'reference'
}

const MAX_PDF_BYTES = 100 * 1024 * 1024

const props = withDefaults(defineProps<{
  open: boolean
  targetWork?: ImportTargetWork | null
}>(), {
  targetWork: null,
})

const emit = defineEmits<{
  'update:open': [value: boolean]
  imported: [result: LibraryImportResult]
}>()

const mode = ref<'pdf' | 'reference'>('pdf')
const referenceKind = ref<ReferenceKind>('doi')
const selectedFile = ref<File | null>(null)
const pdfTitle = ref('')
const pdfAuthors = ref('')
const pdfDoi = ref('')
const rightsConfirmed = ref(false)
const referenceValue = ref('')
const referenceTitle = ref('')
const referenceAuthors = ref('')
const saving = ref(false)
const errorMessage = messageRef(t)

const canSubmitPdf = computed(() => Boolean(
  selectedFile.value
  && rightsConfirmed.value
  && !saving.value,
))
const canSubmitReference = computed(() => (
  referenceValue.value.trim().length > 0 && !saving.value
))
const referenceLabel = computed(() => {
  if (referenceKind.value === 'doi') return 'DOI'
  if (referenceKind.value === 'bibtex') return t('BibTeX 条目')
  return t('论文或出版页面链接')
})
const referencePlaceholder = computed(() => {
  if (referenceKind.value === 'doi') return t('10.1000/example 或 https://doi.org/…')
  if (referenceKind.value === 'bibtex') return '@article{…}'
  return 'https://example.org/papers/…'
})

watch(() => props.open, (open) => {
  if (!open) return
  mode.value = 'pdf'
  referenceKind.value = 'doi'
  selectedFile.value = null
  pdfTitle.value = ''
  pdfAuthors.value = ''
  pdfDoi.value = ''
  rightsConfirmed.value = false
  referenceValue.value = ''
  referenceTitle.value = ''
  referenceAuthors.value = ''
  saving.value = false
  errorMessage.value = ''
}, { immediate: true })

function setMode(value: 'pdf' | 'reference'): void {
  if (props.targetWork && value === 'reference') return
  mode.value = value
  errorMessage.value = ''
}

function setReferenceKind(value: ReferenceKind): void {
  referenceKind.value = value
  errorMessage.value = ''
}

function choosePdf(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  errorMessage.value = ''
  if (!file) {
    selectedFile.value = null
    return
  }
  if (file.size > MAX_PDF_BYTES) {
    selectedFile.value = null
    input.value = ''
    errorMessage.value = 'PDF 超过 100 MiB，未读取或上传。'
    return
  }
  if (file.type && file.type.toLocaleLowerCase() !== 'application/pdf') {
    selectedFile.value = null
    input.value = ''
    errorMessage.value = '请选择 PDF 文件；其他文件不会上传。'
    return
  }
  selectedFile.value = file
}

function parseAuthors(value: string): string[] {
  const seen = new Set<string>()
  return value
    .split(/[\n,;；]+/)
    .map((author) => author.trim().replace(/\s+/g, ' '))
    .filter((author) => {
      if (!author) return false
      const key = author.toLocaleLowerCase()
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
}

async function submitPdf(): Promise<void> {
  const file = selectedFile.value
  if (!file || saving.value) return
  if (!rightsConfirmed.value) {
    errorMessage.value = '请先确认你有权在本机保存和处理这份 PDF。'
    return
  }
  saving.value = true
  errorMessage.value = ''
  const form = new FormData()
  form.append('file', file, file.name)
  form.append('authors', JSON.stringify(parseAuthors(pdfAuthors.value)))
  form.append('rights_confirmed', 'true')
  if (props.targetWork) form.append('work_id', props.targetWork.id)
  if (pdfTitle.value.trim()) form.append('title', pdfTitle.value.trim())
  if (pdfDoi.value.trim()) form.append('doi', pdfDoi.value.trim())
  try {
    const result = await apiJson<PdfImportResponse>('/api/v1/library/imports/pdf', {
      method: 'POST',
      body: form,
    })
    emit('imported', {
      workId: result.work_id,
      documentVersionId: result.document_version_id,
      kind: 'pdf',
    })
    emit('update:open', false)
  } catch (error) {
    errorMessage.value = pdfErrorMessage(error)
  } finally {
    saving.value = false
  }
}

async function submitReference(): Promise<void> {
  const value = referenceValue.value.trim()
  if (!value || saving.value || props.targetWork) return
  saving.value = true
  errorMessage.value = ''
  try {
    const result = await apiJson<ReferenceImportResponse>(
      '/api/v1/library/imports/reference',
      {
        method: 'POST',
        body: JSON.stringify({
          kind: referenceKind.value,
          value,
          title: referenceTitle.value.trim() || null,
          authors: parseAuthors(referenceAuthors.value),
          rights_confirmed: false,
        }),
      },
    )
    emit('imported', {
      workId: result.work_id,
      documentVersionId: null,
      kind: 'reference',
    })
    emit('update:open', false)
  } catch {
    errorMessage.value = '无法保存这条引用。请检查格式、长度和链接是否为公开地址。'
  } finally {
    saving.value = false
  }
}

function pdfErrorMessage(error: unknown): string {
  if (error instanceof ApiResponseError && error.status === 413) {
    return t('PDF 超过 100 MiB，未保存任何内容。')
  }
  if (error instanceof ApiResponseError && error.status === 422) {
    return t('无法导入这份 PDF。请确认文件有效，并已勾选合法取得声明。')
  }
  return t('PDF 暂时无法导入。文件没有被保存，请稍后重试。')
}
</script>

<template>
  <DialogRoot :open="open" @update:open="emit('update:open', $event)">
    <DialogPortal>
      <DialogOverlay class="import-overlay" />
      <DialogContent class="import-panel" data-test="library-import-panel">
        <header class="import-heading">
          <div>
            <span>{{ t('本地入库 · 只在这台设备处理') }}</span>
            <DialogTitle as="h2">
              {{ targetWork ? t('添加论文版本') : t('把论文加入本地库') }}
            </DialogTitle>
            <DialogDescription>
              {{ targetWork
                ? t('作为新版本加入《{0}》，旧版本和批注都会保留。', targetWork.title)
                : t('导入你合法取得的 PDF，或只保存 DOI、BibTeX 与来源链接。') }}
            </DialogDescription>
          </div>
          <DialogClose as-child>
            <button type="button" class="close-import" :aria-label="t('关闭论文导入')">
              <PhX :size="19" aria-hidden="true" />
            </button>
          </DialogClose>
        </header>

        <div v-if="!targetWork" class="import-modes" role="tablist" :aria-label="t('导入方式')">
          <button
            type="button"
            role="tab"
            data-test="import-mode-pdf"
            :aria-selected="mode === 'pdf'"
            @click="setMode('pdf')"
          >
            <PhFilePdf :size="17" aria-hidden="true" />
            {{ t('PDF 文件') }}
          </button>
          <button
            type="button"
            role="tab"
            data-test="import-mode-reference"
            :aria-selected="mode === 'reference'"
            @click="setMode('reference')"
          >
            <PhLink :size="17" aria-hidden="true" />
            {{ t('DOI / BibTeX / 链接') }}
          </button>
        </div>

        <p v-if="errorMessage" class="import-error" role="alert">
          {{ errorMessage }}
        </p>

        <form
          v-if="mode === 'pdf'"
          class="import-form"
          data-test="pdf-import-form"
          @submit.prevent="submitPdf"
        >
          <label class="file-picker">
            <PhUploadSimple :size="22" aria-hidden="true" />
            <span>
              <strong>{{ selectedFile?.name || t('选择本机 PDF') }}</strong>
              <small>
                {{ selectedFile
                  ? t('{0} KB · 仅在确认后上传', Math.max(1, Math.ceil(selectedFile.size / 1024)))
                  : t('最大 100 MiB；选择文件不会立即保存') }}
              </small>
            </span>
            <input
              data-test="pdf-import-file"
              type="file"
              accept="application/pdf,.pdf"
              @change="choosePdf"
            >
          </label>

          <div v-if="!targetWork" class="field-grid">
            <label>
              <span>{{ t('论文标题') }} <small>{{ t('可选，留空时使用文件名') }}</small></span>
              <input
                v-model="pdfTitle"
                data-test="pdf-import-title"
                type="text"
                maxlength="2000"
                autocomplete="off"
              >
            </label>
            <label>
              <span>{{ t('作者') }} <small>{{ t('用逗号、分号或换行分隔') }}</small></span>
              <input
                v-model="pdfAuthors"
                data-test="pdf-import-authors"
                type="text"
                maxlength="100000"
                autocomplete="off"
              >
            </label>
            <label>
              <span>DOI <small>{{ t('可选') }}</small></span>
              <input
                v-model="pdfDoi"
                data-test="pdf-import-doi"
                type="text"
                maxlength="255"
                autocomplete="off"
              >
            </label>
          </div>

          <label class="rights-confirmation">
            <input v-model="rightsConfirmed" data-test="pdf-import-rights" type="checkbox">
            <span>
              <strong>{{ t('我确认这份 PDF 是我合法取得并有权在本机处理的副本') }}</strong>
              <small>{{ t('paper-reader 不绕过付费墙，也不会替你判断内容许可。') }}</small>
            </span>
          </label>

          <footer class="form-footer">
            <p>{{ t('文件通过 PDF 签名和大小校验后，才会进入内容寻址论文库。') }}</p>
            <button
              type="submit"
              data-test="submit-pdf-import"
              :disabled="!canSubmitPdf"
            >
              <PhCheckCircle v-if="!saving" :size="17" aria-hidden="true" />
              {{ saving ? t('正在校验并保存…') : targetWork ? t('保存为新版本') : t('导入本地论文库') }}
              <PhArrowRight v-if="!saving" :size="15" aria-hidden="true" />
            </button>
          </footer>
        </form>

        <form
          v-else
          class="import-form reference-form"
          data-test="reference-import-form"
          @submit.prevent="submitReference"
        >
          <div class="reference-kinds" role="group" :aria-label="t('引用类型')">
            <button
              v-for="item in ([
                ['doi', 'DOI'],
                ['bibtex', 'BibTeX'],
                ['url', '链接'],
              ] as const)"
              :key="item[0]"
              type="button"
              :data-test="`reference-kind-${item[0]}`"
              :aria-pressed="referenceKind === item[0]"
              @click="setReferenceKind(item[0])"
            >
              {{ item[1] }}
            </button>
          </div>

          <label>
            <span>{{ referenceLabel }}</span>
            <textarea
              v-model="referenceValue"
              data-test="reference-import-value"
              :placeholder="referencePlaceholder"
              :rows="referenceKind === 'bibtex' ? 7 : 3"
              maxlength="100000"
            />
          </label>
          <div v-if="referenceKind !== 'bibtex'" class="field-grid reference-meta">
            <label>
              <span>{{ t('论文标题') }} <small>{{ t('可选') }}</small></span>
              <input
                v-model="referenceTitle"
                data-test="reference-import-title"
                type="text"
                maxlength="2000"
                autocomplete="off"
              >
            </label>
            <label>
              <span>{{ t('作者') }} <small>{{ t('可选，多人可分隔') }}</small></span>
              <input
                v-model="referenceAuthors"
                data-test="reference-import-authors"
                type="text"
                maxlength="100000"
                autocomplete="off"
              >
            </label>
          </div>

          <aside class="reference-boundary">
            <PhLink :size="17" aria-hidden="true" />
            <p>
              <strong>{{ t('这里只保存引用与来源信息，不会下载正文') }}</strong>
              <span>{{ t('以后打开、收藏或分析时，仍需明确选择合法开放版本或导入本机 PDF。') }}</span>
            </p>
          </aside>

          <footer class="form-footer">
            <p>{{ t('链接只接受公开 HTTP/HTTPS 地址；本机和内网地址会被拒绝。') }}</p>
            <button
              type="submit"
              data-test="submit-reference-import"
              :disabled="!canSubmitReference"
            >
              {{ saving ? t('正在保存引用…') : t('保存到论文库') }}
              <PhArrowRight v-if="!saving" :size="15" aria-hidden="true" />
            </button>
          </footer>
        </form>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style scoped>
.import-overlay {
  position: fixed;
  z-index: 30;
  inset: 0;
  background: rgb(18 25 19 / 52%);
  backdrop-filter: blur(2px);
}

.import-panel {
  position: fixed;
  z-index: 31;
  top: 50%;
  left: 50%;
  width: min(680px, calc(100vw - 32px));
  max-height: min(820px, calc(100dvh - 32px));
  overflow: auto;
  transform: translate(-50%, -50%);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
  background: var(--color-paper-deep);
  color: var(--color-ink);
  box-shadow: 0 28px 80px rgb(18 25 19 / 30%);
}

.import-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding: 24px 26px 20px;
  border-top: 3px solid var(--color-forest);
  border-bottom: 1px solid var(--color-line);
}

.import-heading span {
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 700;
  letter-spacing: 0.12em;
}

.import-heading h2 {
  margin: 7px 0 8px;
  font-family: var(--font-editorial);
  font-size: 27px;
  font-weight: 500;
  letter-spacing: -0.025em;
}

.import-heading p {
  max-width: 540px;
  margin: 0;
  color: var(--color-muted-strong);
  font-size: 12px;
  line-height: 1.65;
}

.close-import {
  width: 38px;
  height: 38px;
  flex: none;
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
}

.import-modes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  padding: 0 26px;
  border-bottom: 1px solid var(--color-line);
}

.import-modes button {
  position: relative;
  min-height: 52px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border: 0;
  background: transparent;
  color: var(--color-muted);
  font-size: var(--type-meta);
  cursor: pointer;
}

.import-modes button[aria-selected='true'] {
  color: var(--color-forest);
  font-weight: 700;
}

.import-modes button[aria-selected='true']::after {
  position: absolute;
  right: 24px;
  bottom: -1px;
  left: 24px;
  height: 2px;
  background: var(--color-forest);
  content: '';
}

.import-error {
  margin: 16px 26px 0;
  padding: 11px 12px;
  border-left: 3px solid var(--color-wine);
  background: var(--color-wine-soft);
  color: var(--color-wine);
  font-size: var(--type-meta);
  line-height: 1.5;
}

.import-form {
  display: grid;
  gap: 18px;
  padding: 22px 26px 26px;
}

.file-picker {
  position: relative;
  min-height: 84px;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 15px;
  border: 1px dashed var(--color-forest);
  border-radius: var(--radius-sm);
  background: var(--color-forest-soft);
  color: var(--color-forest);
  cursor: pointer;
}

.file-picker > svg { flex: none; }
.file-picker > span { min-width: 0; }

.file-picker strong,
.file-picker small {
  display: block;
}

.file-picker strong {
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-picker small {
  margin-top: 5px;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
}

.file-picker input {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
}

.file-picker:focus-within {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

.field-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 13px;
}

.field-grid > label:first-child { grid-column: 1 / -1; }

.import-form > label:not(.file-picker, .rights-confirmation),
.field-grid label {
  display: grid;
  gap: 6px;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
}

.import-form label small {
  color: var(--color-muted);
  font-size: var(--type-meta);
}

.import-form input[type='text'],
.import-form textarea {
  width: 100%;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper);
  color: var(--color-ink);
  font: inherit;
}

.import-form input[type='text'] {
  height: 40px;
  padding: 0 10px;
}

.import-form textarea {
  resize: vertical;
  padding: 10px;
  line-height: 1.55;
}

.rights-confirmation {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: start;
  gap: 10px;
  padding: 13px;
  border: 1px solid var(--color-line);
  background: var(--color-paper);
  cursor: pointer;
}

.rights-confirmation input {
  width: 17px;
  height: 17px;
  margin: 1px 0 0;
  accent-color: var(--color-forest);
}

.rights-confirmation strong,
.rights-confirmation small {
  display: block;
}

.rights-confirmation strong {
  font-size: var(--type-meta);
  line-height: 1.45;
}

.rights-confirmation small {
  margin-top: 4px;
  line-height: 1.45;
}

.form-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--color-line);
}

.form-footer p {
  max-width: 350px;
  margin: 0;
  color: var(--color-muted);
  font-size: var(--type-meta);
  line-height: 1.55;
}

.form-footer button {
  min-height: 40px;
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 15px;
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--color-forest);
  color: var(--color-on-accent);
  font-size: var(--type-meta);
  font-weight: 700;
  cursor: pointer;
}

.form-footer button:disabled {
  cursor: default;
  opacity: 0.48;
}

.reference-kinds {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  padding: 3px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: var(--color-paper);
}

.reference-kinds button {
  min-height: 34px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-muted);
  font-size: var(--type-meta);
  cursor: pointer;
}

.reference-kinds button[aria-pressed='true'] {
  background: var(--color-forest-soft);
  color: var(--color-forest);
  font-weight: 700;
}

.reference-boundary {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 12px;
  border-left: 3px solid var(--color-forest);
  background: var(--color-paper);
  color: var(--color-forest);
}

.reference-boundary p {
  display: grid;
  gap: 4px;
  margin: 0;
  font-size: var(--type-meta);
  line-height: 1.5;
}

.reference-boundary span { color: var(--color-muted-strong); }

.close-import:focus-visible,
.import-modes button:focus-visible,
.import-form input:focus-visible,
.import-form textarea:focus-visible,
.import-form button:focus-visible,
.file-picker:focus-within {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

@media (max-width: 620px) {
  .import-panel {
    top: auto;
    right: 0;
    bottom: 0;
    left: 0;
    width: 100%;
    max-height: calc(100dvh - 24px);
    transform: none;
    border-radius: var(--radius-md) var(--radius-md) 0 0;
  }

  .import-heading,
  .import-form {
    padding-right: 18px;
    padding-left: 18px;
  }

  .import-modes {
    padding: 0 18px;
  }

  .field-grid,
  .form-footer {
    grid-template-columns: 1fr;
  }

  .field-grid { display: grid; }
  .field-grid > label:first-child { grid-column: auto; }

  .form-footer {
    display: grid;
  }

  .form-footer button { width: 100%; }
}

@media (forced-colors: active) {
  .file-picker,
  .rights-confirmation,
  .reference-boundary {
    border: 2px solid CanvasText;
  }
}
</style>
