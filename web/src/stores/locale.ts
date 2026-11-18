import { computed, readonly, ref, shallowReactive, shallowRef } from 'vue'

export type Locale = 'zh-CN' | 'en'
export const LOCALE_CACHE_KEY = 'paper-reader.locale'

type Dictionary = Record<string, string>
type Section = 'shell' | 'discovery' | 'library' | 'reader' | 'research' | 'settings' | 'publishing' | 'server'
type Translate = (source: string, ...args: unknown[]) => string

const loaders = import.meta.glob<Dictionary>('../i18n/en/*.json', { import: 'default' })
const sections = shallowReactive<Partial<Record<Section, Dictionary>>>({})
const pending = new Map<Section, Promise<void>>()
const visited = new Set<Section>(['shell'])
const language = ref<Locale>('zh-CN')
export const uiLocale = readonly(language)
export function currentLocale(): Locale { return language.value }

function load(section: Section): Promise<void> {
  if (sections[section]) return Promise.resolve()
  const existing = pending.get(section)
  if (existing) return existing
  const loadFile = loaders[`../i18n/en/${section}.json`]
  if (!loadFile) return Promise.resolve()
  const request = loadFile().then((dictionary) => { sections[section] = dictionary })
    .finally(() => { pending.delete(section) })
  pending.set(section, request)
  return request
}

function format(pattern: string, args: unknown[]): string {
  return args.length ? pattern.replace(/\{(\d+)\}/g, (token, index: string) =>
    Number(index) < args.length ? String(args[Number(index)] ?? '') : token) : pattern
}

function translator(section: Section): Translate {
  return (source, ...args) => {
    visited.add(section)
    if (language.value !== 'en') return format(source, args)
    const dictionary = sections[section]
    if (!dictionary) void load(section).catch(() => {})
    const key = source.trim().replace(/\s+/g, ' ')
    const english = dictionary?.[key]
    if (typeof english !== 'string') return format(source, args)
    const text = source === key ? english : source.replace(/^(\s*)[\s\S]*?(\s*)$/, (_, leading, trailing) =>
      leading + english + trailing)
    return format(text, args)
  }
}

export const shellText = translator('shell')
export const discoveryText = translator('discovery')
export const libraryText = translator('library')
export const readerText = translator('reader')
export const researchText = translator('research')
export const settingsText = translator('settings')
export const publishingText = translator('publishing')
const serverText = translator('server')

const chineseBriefing: Dictionary = {
  'Create your first research watch': '创建第一个关注计划',
  'Describe a research intent, confirm its keyword groups, then choose months and official sources.': '描述研究问题，确认关键词，再选择月份和论文来源。',
  'Your reading briefing is quiet': '暂无新的阅读简报',
  'Run a watch plan to collect and rank new papers.': '运行关注计划，获取新论文并查看推荐排序。',
}
const serverPatterns = computed(() => Object.entries(sections.server ?? {}).flatMap(([key, value]) => {
  if (!key.includes('{0}')) return []
  const escaped = key.split(/(\{\d+\})/).map((part) => /^\{\d+\}$/.test(part)
    ? '([\\s\\S]+?)' : part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  return [{ pattern: new RegExp('^' + escaped.join('') + '$'), value }]
}))

// Only pass application-owned diagnostics and capability labels here. Paper content and
// user input are rendered as received, even when they happen to match a catalog entry.
export function serverMessage(source: string | null | undefined): string {
  if (!source) return ''
  const direct = serverText(source)
  if (language.value === 'zh-CN') return Object.hasOwn(chineseBriefing, source) ? chineseBriefing[source]! : source
  if (direct !== source) return direct
  for (const { pattern, value } of serverPatterns.value) {
    const match = pattern.exec(source)
    if (match) return format(value, match.slice(1))
  }
  return source
}

export interface MessageTemplate {
  source: string
  args: unknown[]
}

export function messageTemplate(source: string, ...args: unknown[]): MessageTemplate {
  return { source, args }
}

// Keep action results translatable after the request has finished, including its original
// parameters. Editing a draft or switching languages never replays the action.
export function messageRef(translate: Translate) {
  const value = shallowRef<string | MessageTemplate>('')
  return computed<string, string | MessageTemplate>({
    get() {
      const message = value.value
      if (typeof message !== 'string') return translate(message.source, ...message.args)
      const translated = translate(message)
      return translated === message && message ? serverMessage(message) : translated
    },
    set(message) { value.value = message },
  })
}

export function readCachedLocale(storage?: Storage): Locale {
  try {
    return (storage ?? window.localStorage).getItem(LOCALE_CACHE_KEY) === 'en' ? 'en' : 'zh-CN'
  } catch { return 'zh-CN' }
}

export async function setLocale(value: Locale): Promise<void> {
  language.value = value
  document.documentElement.lang = value
  try { window.localStorage.setItem(LOCALE_CACHE_KEY, value) } catch { /* optional cache */ }
  if (value === 'en') await Promise.all(Array.from(visited, load))
}

export async function prepareRouteLocale(path: string): Promise<void> {
  if (language.value !== 'en') return
  let section: Section = 'discovery'
  if (path.startsWith('/settings') || path === '/onboarding') section = 'settings'
  else if (path === '/search' || path === '/library') section = 'library'
  else if (path.startsWith('/library/')) section = 'reader'
  else if (path === '/comparisons') section = 'research'
  else if (path === '/publishing') section = 'publishing'
  await load(section)
}

export function useLocale() {
  const message = computed(() => language.value === 'en'
    ? { switchTo: '中文', language: 'Interface language', localOnly: 'Data stays on this device' }
    : { switchTo: 'English', language: '界面语言', localOnly: '数据仅保存在此设备' })
  return {
    locale: uiLocale,
    message,
    switchTo: computed(() => message.value.switchTo),
    language: computed(() => message.value.language),
    localOnly: computed(() => message.value.localOnly),
    toggleLocale: () => setLocale(language.value === 'zh-CN' ? 'en' : 'zh-CN'),
    setLocale,
  }
}
