import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useOnboardingStore } from '@/features/onboarding/store'
import { prepareRouteLocale, shellText } from '@/stores/locale'

const ComparisonsPage = () => import('@/pages/ComparisonsPage.vue')
const LibraryPage = () => import('@/pages/LibraryPage.vue')
const OnboardingPage = () => import('@/pages/OnboardingPage.vue')
const PlansPage = () => import('@/pages/PlansPage.vue')
const PlanWizardPage = () => import('@/pages/PlanWizardPage.vue')
const PublishingPage = () => import('@/pages/PublishingPage.vue')
const BilingualReaderPage = () => import('@/pages/BilingualReaderPage.vue')
const ReaderWorkspacePage = () => import('@/pages/ReaderWorkspacePage.vue')
const ReadingBriefingPage = () => import('@/pages/ReadingBriefingPage.vue')
const SearchPage = () => import('@/pages/SearchPage.vue')
const RunResultsPage = () => import('@/pages/RunResultsPage.vue')
const AboutSettingsPage = () => import('@/pages/settings/AboutSettingsPage.vue')
const AgentSettingsPage = () => import('@/pages/settings/AgentSettingsPage.vue')
const ExtensionsSettingsPage = () => import('@/pages/settings/ExtensionsSettingsPage.vue')
const ReadingSettingsPage = () => import('@/pages/settings/ReadingSettingsPage.vue')
const RecommendationSettingsPage = () => import('@/pages/settings/RecommendationSettingsPage.vue')
const SettingsLayoutPage = () => import('@/pages/settings/SettingsLayoutPage.vue')
const SettingsOverviewPage = () => import('@/pages/settings/SettingsOverviewPage.vue')
const SourcesSettingsPage = () => import('@/pages/settings/SourcesSettingsPage.vue')
const StorageSettingsPage = () => import('@/pages/settings/StorageSettingsPage.vue')

export const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'briefing',
    component: ReadingBriefingPage,
    meta: { title: '阅读简报' },
  },
  {
    path: '/search',
    name: 'search',
    component: SearchPage,
    meta: { title: '搜索论文' },
  },
  {
    path: '/onboarding',
    name: 'onboarding',
    component: OnboardingPage,
    meta: { title: '首次配置', publicSetup: true },
  },
  {
    path: '/plans',
    name: 'plans',
    component: PlansPage,
    meta: { title: '关注计划' },
  },
  {
    path: '/plans/new',
    name: 'new-plan',
    component: PlanWizardPage,
    meta: { title: '新建关注计划' },
  },
  {
    path: '/runs/:runId/results',
    name: 'run-results',
    component: RunResultsPage,
    meta: { title: '推荐结果' },
  },
  {
    path: '/library',
    name: 'library',
    component: LibraryPage,
    meta: { title: '我的论文库' },
  },
  {
    path: '/library/:workId/read/:documentVersionId',
    name: 'reader-workspace',
    component: ReaderWorkspacePage,
    meta: { title: '论文阅读', workspace: 'reader' },
  },
  {
    path: '/library/:workId/translations/:artifactId',
    name: 'bilingual-reader',
    component: BilingualReaderPage,
    meta: { title: '双语阅读', workspace: 'reader' },
  },
  {
    path: '/comparisons',
    name: 'comparisons',
    component: ComparisonsPage,
    meta: { title: '多论文比较' },
  },
  {
    path: '/publishing',
    name: 'publishing',
    component: PublishingPage,
    meta: { title: '精选发布' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: SettingsLayoutPage,
    redirect: { name: 'settings-overview' },
    meta: { title: '设置' },
    children: [
      {
        path: 'overview',
        name: 'settings-overview',
        component: SettingsOverviewPage,
        meta: { title: '配置总览' },
      },
      {
        path: 'agents',
        name: 'settings-agents',
        component: AgentSettingsPage,
        meta: { title: 'AI 与模型' },
      },
      {
        path: 'sources',
        name: 'settings-sources',
        component: SourcesSettingsPage,
        meta: { title: '论文来源' },
      },
      {
        path: 'reading',
        name: 'settings-reading',
        component: ReadingSettingsPage,
        meta: { title: '阅读与语言' },
      },
      {
        path: 'recommendation',
        name: 'settings-recommendation',
        component: RecommendationSettingsPage,
        meta: { title: '推荐与评分' },
      },
      {
        path: 'storage',
        name: 'settings-storage',
        component: StorageSettingsPage,
        meta: { title: '存储与备份' },
      },
      {
        path: 'extensions',
        name: 'settings-extensions',
        component: ExtensionsSettingsPage,
        meta: { title: '扩展与发布' },
      },
      {
        path: 'about',
        name: 'settings-about',
        component: AboutSettingsPage,
        meta: { title: '关于与更新' },
      },
    ],
  },
]

export interface AppRouterOptions {
  onboardingStatus?: () => Promise<boolean>
}

export function updatePageTitle(value: unknown): void {
  const title = typeof value === 'string' ? value.trim() : ''
  document.title = title ? `${shellText(title)} | FolioSkiff` : 'FolioSkiff'
}

async function storedOnboardingStatus(): Promise<boolean> {
  const store = useOnboardingStore()
  if (!await store.load()) throw new Error('onboarding overview unavailable')
  return store.completed
}

export function createAppRouter(options: AppRouterOptions = {}) {
  const onboardingStatus = options.onboardingStatus ?? storedOnboardingStatus
  const router = createRouter({
    history: createWebHistory(),
    routes,
    scrollBehavior: () => ({ top: 0 }),
  })

  router.beforeEach(async (route) => {
    const isSetup = route.meta.publicSetup === true
    if (isSetup && route.query.status === 'unavailable') return true
    try {
      const complete = await onboardingStatus()
      if (isSetup) return complete ? { name: 'briefing', replace: true } : true
      return complete ? true : { name: 'onboarding', replace: true }
    } catch {
      if (isSetup) return true
      return {
        name: 'onboarding',
        query: { status: 'unavailable' },
        replace: true,
      }
    }
  })

  router.beforeResolve((route) => prepareRouteLocale(route.path).catch(() => {}))
  router.afterEach((route) => updatePageTitle(route.meta.title))
  return router
}
