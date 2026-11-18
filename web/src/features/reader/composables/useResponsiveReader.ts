import {
  nextTick,
  onMounted,
  onScopeDispose,
  reactive,
  ref,
  type Ref,
} from 'vue'

export type AgentTab = 'annotations' | 'report' | 'assistant'
export type MobilePanelView = 'pdf' | AgentTab

export interface ResponsiveReaderOptions {
  matchMedia?: (query: string) => MediaQueryList
}

export interface ResponsiveReaderState {
  libraryDrawerOpen: Ref<boolean>
  isCompactLayout: Ref<boolean>
  mobilePanelView: Ref<MobilePanelView>
  agentTab: Ref<AgentTab>
  reportVisited: Ref<boolean>
  assistantVisited: Ref<boolean>
  agentColumn: Ref<HTMLElement | undefined>
  scrollPositions: Record<AgentTab, number>
  selectAgentTab(value: unknown): void
  selectMobilePanel(value: unknown): void
  showAgentTab(tab: AgentTab): Promise<void>
  showPdf(): void
  resetForDocument(): void
}

export function useResponsiveReader(
  options: ResponsiveReaderOptions = {},
): ResponsiveReaderState {
  const libraryDrawerOpen = ref(false)
  const isCompactLayout = ref(false)
  const mobilePanelView = ref<MobilePanelView>('pdf')
  const agentTab = ref<AgentTab>('annotations')
  const reportVisited = ref(false)
  const assistantVisited = ref(false)
  const agentColumn = ref<HTMLElement>()
  const scrollPositions = reactive<Record<AgentTab, number>>({
    annotations: 0,
    report: 0,
    assistant: 0,
  })
  let compactQuery: MediaQueryList | undefined

  function updateCompactLayout(event: MediaQueryListEvent | MediaQueryList): void {
    isCompactLayout.value = event.matches
    if (!event.matches) libraryDrawerOpen.value = false
  }

  function isAgentTab(value: unknown): value is AgentTab {
    return value === 'annotations' || value === 'report' || value === 'assistant'
  }

  function selectAgentTab(value: unknown): void {
    if (isAgentTab(value)) void showAgentTab(value)
  }

  function selectMobilePanel(value: unknown): void {
    if (value === 'pdf') {
      showPdf()
      return
    }
    selectAgentTab(value)
  }

  async function showAgentTab(tab: AgentTab): Promise<void> {
    const previousTab = agentTab.value
    if (previousTab !== tab && agentColumn.value) {
      scrollPositions[previousTab] = agentColumn.value.scrollTop
    }
    if (tab === 'report') reportVisited.value = true
    if (tab === 'assistant') assistantVisited.value = true
    agentTab.value = tab
    mobilePanelView.value = tab
    await nextTick()
    if (previousTab !== tab && agentColumn.value) {
      agentColumn.value.scrollTop = scrollPositions[tab]
    }
  }

  function showPdf(): void {
    mobilePanelView.value = 'pdf'
  }

  function resetForDocument(): void {
    libraryDrawerOpen.value = false
    scrollPositions.annotations = 0
    scrollPositions.report = 0
    scrollPositions.assistant = 0
    if (agentColumn.value) agentColumn.value.scrollTop = 0
  }

  onMounted(() => {
    const matchMedia = options.matchMedia
      ?? (typeof window.matchMedia === 'function' ? window.matchMedia.bind(window) : undefined)
    if (!matchMedia) return
    compactQuery = matchMedia('(max-width: 1280px)')
    updateCompactLayout(compactQuery)
    compactQuery.addEventListener('change', updateCompactLayout)
  })
  onScopeDispose(() => {
    compactQuery?.removeEventListener('change', updateCompactLayout)
  })

  return {
    libraryDrawerOpen,
    isCompactLayout,
    mobilePanelView,
    agentTab,
    reportVisited,
    assistantVisited,
    agentColumn,
    scrollPositions,
    selectAgentTab,
    selectMobilePanel,
    showAgentTab,
    showPdf,
    resetForDocument,
  }
}
