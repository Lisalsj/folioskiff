<script setup lang="ts">
import { currentLocale, messageRef, settingsText as t, serverMessage } from '@/stores/locale'
import { computed, onMounted, ref } from 'vue'

import type { components } from '@/api/schema'
import { useOnboardingStore } from '@/features/onboarding/store'
import { apiJson } from '@/lib/json-api'

type BackupArchive = components['schemas']['BackupArchiveResponse']
type OnboardingOverview = components['schemas']['OnboardingOverview']
type UpdateSnapshot = components['schemas']['UpdateSnapshot']

interface CapabilityIssue {
  key: string
  dataIssue: string
  title: string
  detail: string
  href: string
}

const props = defineProps<{
  overview?: OnboardingOverview
  backupArchives?: BackupArchive[]
  updateSnapshot?: UpdateSnapshot
}>()
const onboarding = props.overview ? undefined : useOnboardingStore()
const archives = ref<BackupArchive[]>(props.backupArchives ?? [])
const update = ref<UpdateSnapshot | undefined>(props.updateSnapshot)
const loading = ref(!props.overview)
const loadWarning = messageRef(t)

const currentOverview = computed(() => props.overview ?? onboarding?.overview)
const readyAgents = computed(() => currentOverview.value?.agents.filter((agent) => agent.available).length ?? 0)
const readySources = computed(() => currentOverview.value?.sources.filter((source) => source.enabled).length ?? 0)
const freeSources = computed(() => currentOverview.value?.sources.filter(
  (source) => source.credential_requirement === 'none',
).length ?? 0)
const keyedSources = computed(() => currentOverview.value?.sources.filter(
  (source) => source.credential_requirement !== 'none',
).length ?? 0)

const issues = computed<CapabilityIssue[]>(() => {
  const value = currentOverview.value
  if (!value) return []
  const found: CapabilityIssue[] = []
  if (!value.storage.writable) {
    found.push({
      key: 'storage-unwritable',
      dataIssue: 'storage_unwritable',
      title: t('本地目录需要处理'),
      detail: t('至少一个状态、缓存或论文库目录不可写。'),
      href: '/settings/storage',
    })
  }
  for (const agent of value.agents.filter((item) => !item.available)) {
    found.push({
      key: `agent-${agent.backend_id}-${agent.probe_code}`,
      dataIssue: agent.probe_code,
      title: `${agent.display_name}：${agent.probe_code}`,
      detail: serverMessage(agent.recommended_action) || t('打开 AI 与模型设置查看诊断。'),
      href: `/settings/agents#${encodeURIComponent(agent.backend_id)}`,
    })
  }
  for (const source of value.sources.filter((item) => !item.enabled)) {
    found.push({
      key: `source-${source.connector_id}`,
      dataIssue: source.disabled_reason ?? 'source_unavailable',
      title: t('{0} 尚未启用', source.display_name),
      detail: source.credential_requirement === 'none' ? t('来源暂时不可用。') : t('可在论文来源中配置可选凭据。'),
      href: `/settings/sources#${encodeURIComponent(source.connector_id)}`,
    })
  }
  if (!value.retrieval.semantic_enabled) {
    found.push({
      key: 'semantic-retrieval',
      dataIssue: 'semantic_retrieval_unavailable',
      title: t('语义检索尚未启用'),
      detail: t('当前继续使用 FTS5 词法检索，推荐置信度会反映降级。'),
      href: '/settings/storage#model-assets',
    })
  }
  return found
})

const updateLabel = computed(() => {
  const status = update.value?.status
  if (!status) return t('尚未读取更新状态')
  if (status === 'update_available') return t('发现可选更新')
  if (status === 'up_to_date') return t('当前已是最新版本')
  if (status === 'disabled') return t('自动检查已关闭')
  return t('更新状态需要查看')
})

async function load(): Promise<void> {
  if (!onboarding) return
  loading.value = true
  loadWarning.value = ''
  const [capabilityResult, backupResult, updateResult] = await Promise.allSettled([
    onboarding.load(),
    apiJson<BackupArchive[]>('/api/v1/backups'),
    apiJson<UpdateSnapshot>('/api/v1/settings/updates'),
  ])
  if (backupResult.status === 'fulfilled') archives.value = backupResult.value
  if (updateResult.status === 'fulfilled') update.value = updateResult.value
  if (
    capabilityResult.status === 'rejected'
    || capabilityResult.value === false
    || backupResult.status === 'rejected'
    || updateResult.status === 'rejected'
  ) loadWarning.value = '部分状态暂时无法读取；已显示的能力信息仍可使用。'
  loading.value = false
}

onMounted(() => void load())
</script>

<template>
  <section class="overview" aria-labelledby="settings-overview-title">
    <header class="section-heading">
      <h2 id="settings-overview-title" data-settings-heading tabindex="-1">{{ t('配置总览') }}</h2>
      <span>{{ t('先处理会影响阅读闭环的问题，其余能力可以按需配置。') }}</span>
    </header>

    <p v-if="loading" role="status" class="quiet-state">{{ t('正在读取本地能力') }}</p>
    <p v-if="loadWarning" role="status" class="warning-state">{{ loadWarning }}</p>

    <div v-if="currentOverview" class="capability-grid">
      <article>
        <span>{{ t('本地存储') }}</span>
        <strong>{{ currentOverview.storage.writable ? t('目录可写') : t('需要处理') }}</strong>
        <RouterLink to="/settings/storage">{{ t('查看存储与备份') }}</RouterLink>
      </article>
      <article>
        <span>{{ t('AI 与模型') }}</span>
        <strong>{{ readyAgents }}/{{ currentOverview.agents.length }} {{ t('个后端可用') }}</strong>
        <RouterLink to="/settings/agents">{{ t('查看 Agent') }}</RouterLink>
      </article>
      <article>
        <span>{{ t('论文来源') }}</span>
        <strong>{{ readySources }} {{ t('个来源可用') }}</strong>
        <small>{{ freeSources }} {{ t('个免凭据 ·') }} {{ keyedSources }} {{ t('个凭据来源') }}</small>
        <RouterLink to="/settings/sources">{{ t('查看来源') }}</RouterLink>
      </article>
      <article>
        <span>{{ t('检索模式') }}</span>
        <strong>{{ currentOverview.retrieval.semantic_enabled ? t('词法 + 语义检索') : t('词法检索可用') }}</strong>
        <small v-if="!currentOverview.retrieval.semantic_enabled">{{ t('语义能力按需下载') }}</small>
        <RouterLink to="/settings/storage#model-assets">{{ t('查看模型资源') }}</RouterLink>
      </article>
      <article>
        <span>{{ t('最近备份') }}</span>
        <strong>{{ archives.length ? new Date(archives[0]!.created_at).toLocaleDateString(currentLocale()) : t('尚无备份') }}</strong>
        <RouterLink to="/settings/storage#backups">{{ t('查看备份') }}</RouterLink>
      </article>
      <article>
        <span>{{ t('应用更新') }}</span>
        <strong>{{ updateLabel }}</strong>
        <RouterLink to="/settings/about#updates">{{ t('查看版本') }}</RouterLink>
      </article>
    </div>

    <section v-if="issues.length" class="issues" aria-labelledby="settings-issues-title">
      <div>
        <h3 id="settings-issues-title">{{ t('待处理事项') }}</h3>
      </div>
      <ul>
        <li v-for="issue in issues" :key="issue.key">
          <div>
            <strong>{{ issue.title }}</strong>
            <span>{{ issue.detail }}</span>
          </div>
          <RouterLink :to="issue.href" :data-issue="issue.dataIssue">{{ t('前往处理') }}</RouterLink>
        </li>
      </ul>
    </section>
    <p v-else-if="currentOverview" class="all-clear">{{ t('当前没有阻断阅读闭环的配置问题。') }}</p>
  </section>
</template>

<style scoped>
.overview { display: grid; gap: 28px; }
.section-heading { padding-bottom: 16px; border-bottom: 2px solid var(--color-forest); }
.section-heading p, .issues > div p { margin: 0 0 7px; color: var(--color-forest); font-size: var(--type-meta); font-weight: 750; letter-spacing: .12em; }
.section-heading h2 { margin: 0; font-family: var(--font-editorial); font-size: 30px; font-weight: 500; }
.section-heading h2:focus { outline: none; }
.section-heading span { display: block; margin-top: 9px; color: var(--color-muted-strong); line-height: 1.65; }
.quiet-state, .warning-state, .all-clear { margin: 0; color: var(--color-muted-strong); }
.warning-state { padding-left: 13px; border-left: 3px solid var(--color-wine); color: var(--color-wine); }
.capability-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border-top: 1px solid var(--color-line); }
.capability-grid article { min-width: 0; display: grid; gap: 7px; padding: 18px 16px 18px 0; border-bottom: 1px solid var(--color-line); }
.capability-grid article:nth-child(odd) { border-right: 1px solid var(--color-line); }
.capability-grid article:nth-child(even) { padding-left: 16px; }
.capability-grid span { color: var(--color-muted); font-size: var(--type-meta); font-weight: 700; }
.capability-grid strong { font-family: var(--font-editorial); font-size: 20px; font-weight: 500; }
.capability-grid small { color: var(--color-muted-strong); }
.capability-grid a, .issues a { width: fit-content; color: var(--color-forest); font-weight: 700; }
.capability-grid a:focus-visible, .issues a:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
.issues { display: grid; gap: 12px; padding-top: 4px; }
.issues h3 { margin: 0; font-family: var(--font-editorial); font-size: 24px; font-weight: 500; }
.issues ul { margin: 0; padding: 0; list-style: none; border-top: 1px solid var(--color-line); }
.issues li { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 18px; align-items: center; padding: 14px 0; border-bottom: 1px solid var(--color-line); }
.issues li div { display: grid; gap: 4px; }
.issues li span { color: var(--color-muted-strong); line-height: 1.55; }

@media (max-width: 620px) {
  .capability-grid { grid-template-columns: 1fr; }
  .capability-grid article:nth-child(n) { padding-inline: 0; border-right: 0; }
  .issues li { grid-template-columns: 1fr; }
}
</style>
