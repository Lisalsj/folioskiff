<script setup lang="ts">
import { currentLocale, messageRef, settingsText as t } from '@/stores/locale'
import { computed, onMounted, ref } from 'vue'
import {
  PhArrowSquareOut,
  PhArrowsClockwise,
  PhCheckCircle,
  PhClockCounterClockwise,
  PhGithubLogo,
  PhShieldWarning,
  PhWarning,
} from '@phosphor-icons/vue'
import {
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogOverlay,
  AlertDialogPortal,
  AlertDialogRoot,
  AlertDialogTitle,
} from 'reka-ui'

import type { components } from '@/api/schema'
import { apiJson } from '@/lib/json-api'

type UpdateSnapshot = components['schemas']['UpdateSnapshot']
type ReleaseMetadata = components['schemas']['ReleaseMetadata']

const snapshot = ref<UpdateSnapshot>()
const loading = ref(true)
const busy = ref('')
const error = messageRef(t)
const notice = messageRef(t)
const reviewOpen = ref(false)

const release = computed(() => snapshot.value?.release ?? undefined)
const validatedReleaseUrl = computed(() => safeReleaseUrl(release.value))

const statusCopy = computed(() => {
  const status = snapshot.value?.status ?? 'never_checked'
  const copy: Record<string, { title: string; detail: string; tone: string }> = {
    disabled: {
      title: t('自动检查已关闭'),
      detail: t('需要时仍可手动检查；应用不会在后台联系 GitHub。'),
      tone: 'quiet',
    },
    never_checked: {
      title: t('尚未检查更新'),
      detail: t('只会读取 FolioSkiff 的公开 GitHub Release 元数据。'),
      tone: 'quiet',
    },
    up_to_date: {
      title: t('当前已是最新版本'),
      detail: t('没有发现版本号更高的正式发布。'),
      tone: 'good',
    },
    update_available: {
      title: t('发现新的正式版本'),
      detail: t('先阅读变更与迁移说明，再决定是否离开应用查看发布页。'),
      tone: 'available',
    },
    server_older: {
      title: t('当前版本比公开发布更新'),
      detail: t('可能正在使用预览构建；不会建议降级。'),
      tone: 'quiet',
    },
    prerelease: {
      title: t('仅发现预发布版本'),
      detail: t('预发布版本不会作为常规更新提供。'),
      tone: 'quiet',
    },
    draft: {
      title: t('发布尚未完成'),
      detail: t('草稿发布不会作为可安装更新提供。'),
      tone: 'quiet',
    },
    rate_limited: {
      title: t('GitHub 暂时限制了检查频率'),
      detail: t('不会反复重试；稍后可以手动检查。'),
      tone: 'warning',
    },
    offline: {
      title: t('当前无法连接 GitHub'),
      detail: t('现有功能不受影响，稍后可以手动重试。'),
      tone: 'warning',
    },
    invalid_response: {
      title: t('发布信息未通过安全校验'),
      detail: t('不会打开链接或读取发布资产，请等待维护者修正。'),
      tone: 'warning',
    },
    service_unavailable: {
      title: t('GitHub 发布服务暂时不可用'),
      detail: t('现有功能不受影响，稍后可以手动重试。'),
      tone: 'warning',
    },
  }
  return copy[status] ?? copy.never_checked
})

function safeReleaseUrl(value: ReleaseMetadata | undefined): string | undefined {
  if (!value) return undefined
  try {
    const parsed = new URL(value.html_url)
    const expectedPath = `/Lisalsj/folioskiff/releases/tag/${encodeURIComponent(value.tag_name)}`
    if (
      parsed.protocol !== 'https:'
      || parsed.hostname !== 'github.com'
      || parsed.username
      || parsed.password
      || parsed.port
      || parsed.search
      || parsed.hash
      || parsed.pathname !== expectedPath
    ) return undefined
    return parsed.toString()
  } catch {
    return undefined
  }
}

function formatDate(value: string | null | undefined): string {
  if (!value) return t('尚未检查')
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return t('日期未知')
  return new Intl.DateTimeFormat(currentLocale(), {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function capabilityLabel(key: string): string {
  const labels: Record<string, string> = {
    windows_x64: 'Windows 11 x64',
    installer: t('安装版'),
    portable: t('便携版'),
    arxiv: 'arXiv',
    openreview: 'OpenReview',
    europe_pmc: 'Europe PMC',
    ieee_xplore: 'IEEE Xplore',
    springer_nature: 'Springer Nature',
  }
  return labels[key] ?? key.replaceAll('_', ' ')
}

function capabilityStatus(value: string): string {
  const labels: Record<string, string> = {
    supported: t('已支持'),
    available: t('可用'),
    fixture: t('契约验证'),
    disabled: t('未启用'),
    not_included: t('本版不含'),
  }
  return labels[value] ?? value
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    snapshot.value = await apiJson<UpdateSnapshot>('/api/v1/settings/updates')
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法读取更新设置'
  } finally {
    loading.value = false
  }
}

async function checkNow(): Promise<void> {
  busy.value = 'check'
  error.value = ''
  notice.value = ''
  try {
    snapshot.value = await apiJson<UpdateSnapshot>('/api/v1/settings/updates/check', {
      method: 'POST',
    })
    notice.value = '检查已完成；应用没有下载任何发布文件。'
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '暂时无法检查更新'
  } finally {
    busy.value = ''
  }
}

async function clearCache(): Promise<void> {
  busy.value = 'clear'
  error.value = ''
  notice.value = ''
  try {
    snapshot.value = await apiJson<UpdateSnapshot>('/api/v1/settings/updates/cache', {
      method: 'DELETE',
    })
    notice.value = '已清除公开发布元数据和上次检查时间。'
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法清除更新缓存'
  } finally {
    busy.value = ''
  }
}

function openReleasePage(): void {
  const url = validatedReleaseUrl.value
  if (!url) return
  window.open(url, '_blank', 'noopener,noreferrer')
  reviewOpen.value = false
}

onMounted(() => void load())
</script>

<template>
  <section class="update-settings" aria-labelledby="update-settings-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">RELEASES</p>
        <h2 id="update-settings-title">{{ t('应用更新') }}</h2>
        <p>{{ t('只有点击“立即检查”时才会向 GitHub 查询新版本。你可以在发布页手动下载安装包。') }}</p>
      </div>
      <PhGithubLogo :size="28" aria-hidden="true" />
    </div>

    <p v-if="loading" class="notice" role="status">{{ t('正在读取本地更新记录…') }}</p>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice success" role="status">{{ notice }}</p>

    <template v-if="snapshot">
      <div class="update-card">
        <div class="version-strip">
          <div>
            <span>{{ t('当前版本') }}</span>
            <strong>{{ snapshot.current_version }}</strong>
          </div>
          <div v-if="release">
            <span>{{ t('可用版本') }}</span>
            <strong>{{ release.version }}</strong>
          </div>
          <div>
            <span>{{ t('上次检查') }}</span>
            <strong>{{ formatDate(snapshot.last_checked_at) }}</strong>
          </div>
        </div>

        <div class="status-row" :data-tone="statusCopy.tone">
          <component
            :is="statusCopy.tone === 'good' ? PhCheckCircle : statusCopy.tone === 'warning' ? PhWarning : PhArrowsClockwise"
            :size="22"
            aria-hidden="true"
          />
          <div>
            <strong>{{ statusCopy.title }}</strong>
            <p>{{ statusCopy.detail }}</p>
          </div>
        </div>

        <div v-if="release" class="release-details">
          <div class="release-meta">
            <span>{{ t('发布于') }} {{ formatDate(release.published_at) }}</span>
            <span>{{ release.tag_name }}</span>
          </div>

          <div v-if="release.unsigned" class="unsigned-warning">
            <PhShieldWarning :size="20" aria-hidden="true" />
            <div>
              <strong>{{ t('此版本尚未进行代码签名') }}</strong>
              <p>{{ t('Windows 可能显示 SmartScreen 提示。请在 GitHub 发布页核对 SHA-256 与 SBOM。') }}</p>
            </div>
          </div>

          <div class="release-copy-grid">
            <article>
              <span>{{ t('版本变化') }}</span>
              <p>{{ release.changelog }}</p>
            </article>
            <article>
              <span>{{ t('迁移说明') }}</span>
              <p>{{ release.migration_notes }}</p>
            </article>
          </div>

          <div class="capabilities">
            <span>{{ t('能力矩阵') }}</span>
            <ul>
              <li v-for="(value, key) in release.capability_matrix" :key="key">
                <span>{{ capabilityLabel(String(key)) }}</span>
                <strong>{{ capabilityStatus(value) }}</strong>
              </li>
            </ul>
          </div>

          <p v-if="!validatedReleaseUrl" class="unsafe-url" role="alert">
            {{ t('发布页地址未通过本地安全校验，链接已被停用。') }}
          </p>
        </div>

        <div class="update-actions">
          <button
            data-test="clear-update-cache"
            type="button"
            class="quiet-button"
            :disabled="Boolean(busy) || (!snapshot.last_checked_at && !release)"
            @click="clearCache"
          >
            <PhClockCounterClockwise :size="17" aria-hidden="true" />
            {{ t('清除记录') }}
          </button>
          <button
            data-test="check-updates"
            type="button"
            class="quiet-button"
            :disabled="Boolean(busy)"
            @click="checkNow"
          >
            <PhArrowsClockwise :size="17" aria-hidden="true" />
            {{ busy === 'check' ? t('正在检查…') : t('立即检查') }}
          </button>
          <button
            v-if="snapshot.status === 'update_available' && validatedReleaseUrl"
            data-test="review-update"
            type="button"
            class="primary-button"
            :disabled="Boolean(busy)"
            @click="reviewOpen = true"
          >
            {{ t('查看并更新') }}
            <PhArrowSquareOut :size="17" aria-hidden="true" />
          </button>
        </div>
      </div>
    </template>

    <AlertDialogRoot v-model:open="reviewOpen">
      <AlertDialogPortal>
        <AlertDialogOverlay class="dialog-overlay" />
        <AlertDialogContent class="update-dialog" data-test="update-confirm-dialog">
          <div class="dialog-mark" aria-hidden="true"><PhArrowSquareOut :size="20" /></div>
          <AlertDialogTitle>{{ t('前往 GitHub 查看这个版本？') }}</AlertDialogTitle>
          <AlertDialogDescription>
            {{ t('你将离开 FolioSkiff，在浏览器新标签页中打开固定仓库的正式 Release 页面。应用不会下载、执行安装器，也不会自动重启。') }}
          </AlertDialogDescription>
          <dl v-if="release" class="dialog-review">
            <div><dt>{{ t('版本') }}</dt><dd>{{ snapshot?.current_version }} → {{ release.version }}</dd></div>
            <div><dt>{{ t('签名') }}</dt><dd>{{ release.unsigned ? t('未签名，请核对 SHA-256') : t('发布清单标记为已签名') }}</dd></div>
            <div><dt>{{ t('目标') }}</dt><dd>github.com/Lisalsj/folioskiff</dd></div>
          </dl>
          <div class="dialog-actions">
            <AlertDialogCancel as-child>
              <button type="button">{{ t('继续留在应用') }}</button>
            </AlertDialogCancel>
            <button
              data-test="confirm-open-release"
              type="button"
              class="confirm"
              @click="openReleasePage"
            >
              {{ t('确认打开发布页') }}
            </button>
          </div>
        </AlertDialogContent>
      </AlertDialogPortal>
    </AlertDialogRoot>
  </section>
</template>

<style scoped>
.update-settings {
  margin-top: clamp(42px, 6vw, 76px);
}

.section-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding-bottom: 17px;
  border-bottom: 1px solid var(--color-line);
}

.section-heading h2 {
  margin: 0 0 7px;
  font-family: var(--font-display);
  font-size: clamp(22px, 3vw, 31px);
  font-weight: 560;
}

.section-heading > div > p:last-child,
.eyebrow {
  margin: 0;
  color: var(--color-muted);
  font-size: 12px;
  line-height: 1.65;
}

.eyebrow {
  margin-bottom: 7px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 720;
  letter-spacing: 0.12em;
}

.update-card {
  margin-top: 18px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-lg);
  background: var(--color-paper-raised);
  overflow: hidden;
}

.version-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  border-bottom: 1px solid var(--color-line);
}

.version-strip > div {
  display: grid;
  gap: 5px;
  min-width: 0;
  padding: 17px 20px;
}

.version-strip > div + div { border-left: 1px solid var(--color-line); }
.version-strip span,
.release-copy-grid article > span,
.capabilities > span {
  color: var(--color-muted);
  font-size: var(--type-meta);
  font-weight: 720;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.version-strip strong { font-size: 13px; overflow-wrap: anywhere; }

.status-row {
  display: flex;
  gap: 13px;
  padding: 18px 20px;
  border-bottom: 1px solid var(--color-line);
  color: var(--color-forest);
}

.status-row[data-tone='warning'] { color: var(--color-danger); }
.status-row[data-tone='available'] { color: var(--color-wine); }
.status-row strong { display: block; margin-bottom: 3px; font-size: 13px; }
.status-row p { margin: 0; color: var(--color-muted); font-size: 12px; line-height: 1.6; }

.release-details { padding: 21px 20px 3px; }
.release-meta { display: flex; justify-content: space-between; gap: 12px; color: var(--color-muted); font-size: var(--type-meta); }

.unsigned-warning {
  display: flex;
  gap: 12px;
  margin-top: 17px;
  padding: 14px;
  border: 1px solid color-mix(in srgb, var(--color-wine) 35%, var(--color-line));
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--color-wine) 5%, transparent);
  color: var(--color-wine);
}

.unsigned-warning strong { font-size: 12px; }
.unsigned-warning p { margin: 4px 0 0; color: var(--color-muted); font-size: var(--type-meta); line-height: 1.6; }

.release-copy-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 17px; }
.release-copy-grid article { padding: 15px; border: 1px solid var(--color-line); border-radius: var(--radius-md); background: var(--color-paper-deep); }
.release-copy-grid p { margin: 9px 0 0; font-size: 12px; line-height: 1.7; white-space: pre-line; }

.capabilities { margin-top: 16px; }
.capabilities ul { display: flex; flex-wrap: wrap; gap: 7px; margin: 9px 0 0; padding: 0; list-style: none; }
.capabilities li { display: flex; gap: 7px; padding: 6px 9px; border: 1px solid var(--color-line); border-radius: 999px; font-size: var(--type-meta); }
.capabilities li strong { color: var(--color-forest); }
.unsafe-url { margin: 16px 0 0; color: var(--color-danger); font-size: 12px; }

.update-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 20px;
  padding: 17px 20px;
  border-top: 1px solid var(--color-line);
  background: var(--color-paper-deep);
}

.update-actions button,
.dialog-actions button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 40px;
  padding: 0 13px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-ink);
  font-size: var(--type-meta);
  font-weight: 700;
  cursor: pointer;
}
.update-actions .primary-button,
.dialog-actions .confirm { border-color: var(--color-forest); background: var(--color-forest); color: var(--color-on-accent); }
.update-actions button:disabled { opacity: 0.52; cursor: wait; }

.notice { margin: 15px 0 0; padding: 10px 12px; border-radius: var(--radius-sm); background: var(--color-paper-deep); color: var(--color-muted); font-size: var(--type-meta); }
.notice.error { color: var(--color-danger); }
.notice.success { color: var(--color-forest); }

.dialog-overlay { position: fixed; inset: 0; z-index: 79; background: rgb(15 25 20 / 48%); backdrop-filter: blur(2px); }
.update-dialog {
  position: fixed;
  z-index: 80;
  top: 50%;
  left: 50%;
  width: min(500px, calc(100vw - 32px));
  max-height: calc(100vh - 32px);
  padding: 24px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-lg);
  background: var(--color-paper-raised);
  box-shadow: 0 22px 65px rgb(20 28 23 / 24%);
  transform: translate(-50%, -50%);
  overflow: auto;
}
.dialog-mark { display: grid; place-items: center; width: 38px; height: 38px; margin-bottom: 15px; border-radius: 50%; background: var(--color-forest-soft); color: var(--color-forest); }
.update-dialog h2 { margin: 0; font-family: var(--font-display); font-size: 24px; }
.update-dialog > p { margin: 10px 0 0; color: var(--color-muted); font-size: 12px; line-height: 1.7; }
.dialog-review { margin: 18px 0 0; border-top: 1px solid var(--color-line); }
.dialog-review div { display: grid; grid-template-columns: 72px 1fr; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--color-line); font-size: var(--type-meta); }
.dialog-review dt { color: var(--color-muted); }
.dialog-review dd { margin: 0; overflow-wrap: anywhere; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px; }

@media (max-width: 760px) {
  .version-strip { grid-template-columns: 1fr; }
  .version-strip > div + div { border-top: 1px solid var(--color-line); border-left: 0; }
  .release-copy-grid { grid-template-columns: 1fr; }
  .update-actions button { flex: 1 1 130px; }
}

@media (forced-colors: active) {
  .unsigned-warning,
  .status-row { border: 1px solid CanvasText; }
}
</style>
