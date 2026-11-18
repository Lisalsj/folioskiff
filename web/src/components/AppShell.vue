<script setup lang="ts">
import { shellText as t } from '@/stores/locale'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type Component } from 'vue'
import { useRoute } from 'vue-router'
import {
  PhBinoculars,
  PhBookOpenText,
  PhBooks,
  PhBroadcast,
  PhDotsThree,
  PhGear,
  PhList,
  PhPulse,
  PhMagnifyingGlass,
  PhMoon,
  PhDesktop,
  PhSun,
  PhSailboat,
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
  DialogTrigger,
} from 'reka-ui'

import JobDrawer from '@/components/JobDrawer.vue'
import { useJobStore } from '@/stores/jobs'
import { useLocale } from '@/stores/locale'
import { usePreferencesStore } from '@/stores/preferences'

interface NavigationItem {
  label: string
  href: string
  icon: Component
  description?: string
}

const navigation: NavigationItem[] = [
  { label: '阅读简报', href: '/', icon: PhBookOpenText },
  { label: '搜索论文', href: '/search', icon: PhMagnifyingGlass },
  { label: '关注计划', href: '/plans', icon: PhBinoculars },
  { label: '我的论文库', href: '/library', icon: PhBooks },
  { label: '多论文比较', href: '/comparisons', icon: PhList },
  { label: '精选发布', href: '/publishing', icon: PhBroadcast },
  { label: '设置', href: '/settings', icon: PhGear },
]
const mobilePrimaryNavigation: NavigationItem[] = [
  { label: '简报', href: '/', icon: PhBookOpenText },
  { label: '计划', href: '/plans', icon: PhBinoculars },
  { label: '论文库', href: '/library', icon: PhBooks },
]
const mobileMoreNavigation: NavigationItem[] = [
  { label: '搜索论文', href: '/search', icon: PhMagnifyingGlass, description: '按关键词和来源查找论文' },
  {
    label: '多论文比较',
    href: '/comparisons',
    icon: PhList,
    description: '并排梳理论文证据与差异',
  },
  {
    label: '精选发布',
    href: '/publishing',
    icon: PhBroadcast,
    description: '预览并发布隐私白名单内容',
  },
  {
    label: '设置',
    href: '/settings/overview',
    icon: PhGear,
    description: '管理来源、模型与本地数据',
  },
  {
    label: '诊断与支持',
    href: '/settings/about',
    icon: PhPulse,
    description: '查看版本并导出脱敏诊断',
  },
]
const route = useRoute()
const jobs = useJobStore()
const locale = useLocale()
const preferences = usePreferencesStore()
const isReaderWorkspace = computed(() => route.meta.workspace === 'reader')
const moreIsActive = computed(() => ['/search', '/comparisons', '/publishing', '/settings'].some(
  (prefix) => route.path.startsWith(prefix),
))
const mainContent = ref<HTMLElement>()
const moreOpen = ref(false)
const themeIcon = computed(() => preferences.theme === 'light'
  ? PhSun
  : preferences.theme === 'dark'
    ? PhMoon
    : PhDesktop)
const themeLabel = computed(() => preferences.theme === 'light'
  ? t('切换到深色主题')
  : preferences.theme === 'dark'
    ? t('切换到系统主题')
    : t('切换到浅色主题'))

function isActive(item: NavigationItem): boolean {
  return item.href === '/' ? route.path === '/' : route.path.startsWith(item.href)
}

async function focusMain(): Promise<void> {
  await nextTick()
  mainContent.value?.focus()
}

watch(
  () => route.path,
  () => void focusMain(),
)

onMounted(() => {
  void jobs.start()
  void preferences.load()
})
onBeforeUnmount(() => jobs.stop())
</script>

<template>
  <div class="app-shell" :class="{ 'is-reader-workspace': isReaderWorkspace }">
    <a
      href="#main-content"
      class="skip-link"
      data-test="skip-to-content"
      @click.prevent="focusMain"
    >
      {{ t('跳到主要内容') }}
    </a>
    <aside class="sidebar" :aria-label="t('主侧栏')">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true"><PhSailboat :size="25" weight="regular" /></span>
        <div>
          <strong>FolioSkiff</strong>
          <span>{{ t('本地研究阅读空间') }}</span>
        </div>
      </div>

      <nav class="desktop-navigation" data-test="desktop-primary-nav" :aria-label="t('主导航')">
        <RouterLink
          v-for="item in navigation"
          :key="item.href"
          :to="item.href"
          class="primary-link"
          :class="{ active: isActive(item) }"
          :aria-current="isActive(item) ? 'page' : undefined"
        >
          <component :is="item.icon" :size="19" weight="regular" aria-hidden="true" />
          <span>{{ t(item.label) }}</span>
        </RouterLink>
      </nav>

      <DialogRoot v-model:open="moreOpen">
        <nav
          class="mobile-navigation"
          data-test="mobile-primary-nav"
          :aria-label="t('移动主导航')"
        >
          <RouterLink
            v-for="item in mobilePrimaryNavigation"
            :key="item.href"
            :to="item.href"
            class="primary-link"
            :class="{ active: isActive(item) }"
            :aria-current="isActive(item) ? 'page' : undefined"
          >
            <component :is="item.icon" :size="20" weight="regular" aria-hidden="true" />
            <span>{{ t(item.label) }}</span>
          </RouterLink>
          <DialogTrigger as-child>
            <button
              type="button"
              class="mobile-more"
              data-test="mobile-more"
              :class="{ active: moreIsActive }"
              :aria-current="moreIsActive ? 'page' : undefined"
            >
              <PhDotsThree :size="20" weight="bold" aria-hidden="true" />
              <span>{{ t('更多') }}</span>
            </button>
          </DialogTrigger>
        </nav>

        <DialogPortal>
          <DialogOverlay class="more-overlay" />
          <DialogContent class="more-content">
            <header class="more-header">
              <div>
                <p>MORE</p>
                <DialogTitle>{{ t('更多功能') }}</DialogTitle>
              </div>
              <DialogClose as-child>
                <button type="button" class="more-close" :aria-label="t('关闭更多功能')">
                  <PhX :size="19" aria-hidden="true" />
                </button>
              </DialogClose>
            </header>
            <DialogDescription class="more-description">
              {{ t('搜索论文、比较证据或管理研究设置。') }}
            </DialogDescription>
            <nav class="more-menu-links" :aria-label="t('更多功能')">
              <DialogClose
                v-for="item in mobileMoreNavigation"
                :key="item.href"
                as-child
              >
                <RouterLink
                  :to="item.href"
                  :aria-current="route.path === item.href ? 'page' : undefined"
                  @click="moreOpen = false"
                >
                  <component :is="item.icon" :size="21" weight="regular" aria-hidden="true" />
                  <span>
                    <strong>{{ t(item.label) }}</strong>
                    <small v-if="item.description">{{ t(item.description) }}</small>
                  </span>
                </RouterLink>
              </DialogClose>
            </nav>
            <button type="button" class="more-language quiet-button" :aria-label="locale.language.value" @click="locale.toggleLocale">
              {{ locale.switchTo }}
            </button>
          </DialogContent>
        </DialogPortal>
      </DialogRoot>

      <p class="local-note">
        <span class="status-dot" aria-hidden="true" />
        {{ locale.localOnly }}
      </p>
      <button class="locale-toggle" type="button" :aria-label="locale.language.value" @click="locale.toggleLocale">
        {{ locale.switchTo }}
      </button>
    </aside>

    <main
      id="main-content"
      ref="mainContent"
      tabindex="-1"
      :class="{ 'reader-main': isReaderWorkspace }"
    >
      <slot />
    </main>
    <nav class="utility-actions" :aria-label="t('应用操作')">
      <RouterLink
        v-if="!isReaderWorkspace"
        class="mobile-search-action"
        to="/search"
        :aria-label="t('搜索论文')"
      >
        <PhMagnifyingGlass :size="19" aria-hidden="true" />
      </RouterLink>
      <button
        type="button"
        class="theme-toggle"
        data-test="theme-toggle"
        :aria-label="themeLabel"
        :title="themeLabel"
        :disabled="preferences.saving"
        @click="preferences.cycleTheme"
      >
        <component :is="themeIcon" :size="19" weight="regular" aria-hidden="true" />
        <span class="sr-only">{{ themeLabel }}</span>
      </button>
      <JobDrawer />
    </nav>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100dvh;
  display: grid;
  grid-template-columns: 252px minmax(0, 1fr);
  background: var(--color-paper);
  color: var(--color-ink);
}

.skip-link {
  position: fixed;
  z-index: 100;
  top: 10px;
  left: 10px;
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  padding: 0 14px;
  border: 2px solid var(--color-focus);
  border-radius: var(--radius-sm);
  background: var(--color-paper-raised);
  color: var(--color-ink);
  font-weight: 700;
  text-decoration: none;
  transform: translateY(calc(-100% - 14px));
}

.skip-link:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
  transform: translateY(0);
}

.sidebar {
  --color-focus: var(--rail-ink);
  position: sticky;
  top: 0;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  padding: 28px 20px 22px;
  border-right: 1px solid var(--color-line);
  background: var(--rail-paper);
  color: var(--rail-ink);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 8px 30px;
}

.brand-mark {
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border-radius: 2px 6px 6px 2px;
  border-inline-start: 4px double var(--rail-paper);
  background: var(--rail-ink);
  color: var(--rail-paper);
  font-family: var(--font-editorial);
  font-size: 20px;
  letter-spacing: -0.04em;
}

.brand strong,
.brand span {
  display: block;
}

.brand strong {
  font: 600 23px/1.1 var(--font-editorial);
  letter-spacing: -.035em;
}

.brand div > span {
  margin-top: 2px;
  color: var(--rail-muted);
  font-size: var(--type-meta);
}

.desktop-navigation {
  display: grid;
  gap: 4px;
  padding-top: 16px;
  border-top: 1px solid #5f776b;
}

.primary-link {
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 42px;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  color: var(--rail-muted);
  font-size: 14px;
  text-decoration: none;
  transition: background-color 140ms ease, color 140ms ease;
}

.primary-link:hover,
.mobile-more:hover {
  background: var(--rail-current);
  color: var(--rail-ink);
}

.primary-link:focus-visible,
.mobile-more:focus-visible,
.more-close:focus-visible,
.more-menu-links a:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

.primary-link.active,
.mobile-more.active {
  background: var(--rail-current);
  color: var(--rail-ink);
  font-weight: 650;
}

.mobile-navigation {
  display: none;
}

.mobile-more {
  border: 0;
  background: transparent;
  color: var(--rail-muted);
  cursor: pointer;
}

.more-overlay {
  position: fixed;
  z-index: 30;
  inset: 0;
  background: rgb(20 31 24 / 42%);
}

.more-content {
  position: fixed;
  z-index: 31;
  right: 12px;
  bottom: calc(var(--mobile-navigation-height) + env(safe-area-inset-bottom, 0px) + 10px);
  left: 12px;
  max-height: calc(100dvh - var(--mobile-navigation-height) - env(safe-area-inset-bottom, 0px) - 32px);
  overflow: auto;
  padding: 20px;
  border: 1px solid var(--color-line-strong, var(--color-line));
  border-radius: var(--radius-md);
  background: var(--color-paper-raised);
  box-shadow: 0 18px 50px rgb(21 31 24 / 18%);
}

.more-header {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 20px;
  padding-bottom: 13px;
  border-bottom: 1px solid var(--color-line);
}

.more-header p {
  margin: 0 0 5px;
  color: var(--color-forest);
  font-size: var(--type-meta);
  font-weight: 750;
  letter-spacing: .12em;
}

.more-header h2 {
  margin: 0;
  font-family: var(--font-editorial);
  font-size: 25px;
  font-weight: 500;
}

.more-close {
  width: 40px;
  height: 40px;
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-ink);
  cursor: pointer;
}

.more-description {
  margin: 13px 0 0;
  color: var(--color-muted-strong);
  font-size: 13px;
  line-height: 1.6;
}

.more-menu-links {
  display: grid;
  margin-top: 12px;
  border-top: 1px solid var(--color-line);
}

.more-menu-links a {
  min-height: 66px;
  display: grid;
  grid-template-columns: 27px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
  padding: 10px 6px;
  border-bottom: 1px solid var(--color-line);
  color: var(--color-ink);
  text-decoration: none;
}

.more-menu-links a:hover {
  background: var(--color-forest-hover-soft);
}

.more-menu-links strong,
.more-menu-links small {
  display: block;
}

.more-menu-links strong {
  font-size: 14px;
}

.more-menu-links small {
  margin-top: 3px;
  color: var(--color-muted);
  font-size: var(--type-meta);
  line-height: 1.45;
}

.local-note {
  margin: auto 8px 0;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--rail-muted);
  font-size: var(--type-meta);
}

.locale-toggle {
  align-self: flex-start;
  margin: 12px 8px 0;
  padding: 5px 8px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--rail-ink);
  cursor: pointer;
  font-size: var(--type-meta);
}

.locale-toggle:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
.more-language { margin-top: 12px; min-height: 44px; }

.utility-actions {
  position: fixed;
  z-index: 15;
  top: 22px;
  right: 24px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.utility-actions :deep(.drawer-trigger) {
  position: relative;
  inset: auto;
}

.mobile-search-action {
  display: none;
}

.theme-toggle {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border: 1px solid var(--color-line-strong, var(--color-line));
  border-radius: 999px;
  background: var(--color-paper-raised);
  color: var(--color-ink);
  box-shadow: 0 5px 20px rgb(21 31 24 / 10%);
  cursor: pointer;
}

.theme-toggle:hover { background: var(--color-forest-hover-soft); color: var(--color-forest); }
.theme-toggle:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
.theme-toggle:disabled { cursor: wait; opacity: .6; }

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #b4d2b6;
}

main {
  min-width: 0;
  padding: 76px clamp(24px, 4.8vw, 76px) 64px;
}

main:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: -3px;
}

main.reader-main {
  height: 100dvh;
  padding: 0;
  overflow: hidden;
}

@media (max-width: 900px) {
  .app-shell {
    grid-template-columns: 76px minmax(0, 1fr);
  }

  .app-shell.is-reader-workspace {
    grid-template-columns: minmax(0, 1fr);
  }

  .app-shell.is-reader-workspace .sidebar {
    display: none;
  }

  .sidebar {
    padding-inline: 12px;
  }

  .brand {
    justify-content: center;
    padding-inline: 0;
  }

  .brand div,
  .desktop-navigation .primary-link span,
  .local-note {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  .desktop-navigation .primary-link {
    justify-content: center;
    padding: 0;
  }

  .locale-toggle {
    align-self: center;
    margin: auto 0 0;
    min-height: 44px;
    padding: 4px;
  }
}

@media (max-width: 640px) {
  .mobile-search-action {
    width: 44px;
    height: 44px;
    display: grid;
    place-items: center;
    border: 1px solid var(--color-line);
    border-radius: 50%;
    background: var(--color-paper-raised);
    color: var(--color-ink);
  }

  .app-shell {
    display: block;
    padding-bottom: calc(var(--mobile-navigation-height) + env(safe-area-inset-bottom, 0px) + 6px);
  }

  .app-shell:not(.is-reader-workspace) {
    position: relative;
  }

  .app-shell.is-reader-workspace {
    padding-bottom: 0;
  }

  .sidebar {
    position: fixed;
    z-index: 10;
    inset: auto 0 0;
    width: 100%;
    height: calc(var(--mobile-navigation-height) + env(safe-area-inset-bottom, 0px));
    display: block;
    padding: 6px 10px max(6px, env(safe-area-inset-bottom, 0px));
    border-top: 1px solid var(--color-line);
    border-right: 0;
  }

  .brand,
  .local-note,
  .locale-toggle {
    display: none;
  }

  .desktop-navigation {
    display: none;
  }

  .mobile-navigation {
    height: 100%;
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 4px;
  }

  .mobile-navigation .primary-link,
  .mobile-more {
    min-width: 0;
    min-height: 54px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 3px;
    padding: 4px 2px;
    border-radius: var(--radius-sm);
    font-size: var(--type-meta);
  }

  main {
    padding: 68px 18px 48px;
  }

  .utility-actions {
    top: 14px;
    right: 14px;
  }

  .app-shell:not(.is-reader-workspace) .utility-actions {
    position: absolute;
  }

  .theme-toggle,
  .utility-actions :deep(.drawer-trigger) {
    width: 44px;
    height: 44px;
  }

  main.reader-main {
    height: calc(100dvh - var(--mobile-navigation-height) - env(safe-area-inset-bottom, 0px));
    padding: 0;
  }

  .app-shell.is-reader-workspace main.reader-main {
    height: 100dvh;
  }
}

@media (prefers-reduced-motion: reduce) {
  .primary-link {
    transition: none;
  }
}
</style>
