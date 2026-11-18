import {
  inject,
  onBeforeUnmount,
  provide,
  ref,
  type InjectionKey,
  type Ref,
} from 'vue'

export interface SettingsDirtyState {
  dirty: Ref<boolean>
  clearDirty: () => void
  setDirty: (value?: boolean) => void
}

interface SettingsDirtyRegistry {
  dirty: Ref<boolean>
  clear: () => void
  remove: (source: symbol) => void
  set: (source: symbol, value: boolean) => void
}

const SETTINGS_DIRTY_REGISTRY: InjectionKey<SettingsDirtyRegistry> = Symbol('settings-dirty-registry')

function createRegistry(): SettingsDirtyRegistry {
  const dirty = ref(false)
  const sources = new Map<symbol, boolean>()
  const refresh = () => { dirty.value = [...sources.values()].some(Boolean) }
  return {
    dirty,
    clear: () => {
      sources.clear()
      refresh()
    },
    remove: (source) => {
      sources.delete(source)
      refresh()
    },
    set: (source, value) => {
      if (value) sources.set(source, true)
      else sources.delete(source)
      refresh()
    },
  }
}

function scopedState(registry: SettingsDirtyRegistry, source: symbol): SettingsDirtyState {
  return {
    dirty: registry.dirty,
    clearDirty: () => registry.set(source, false),
    setDirty: (value = true) => registry.set(source, value),
  }
}

export function provideSettingsDirtyState(): SettingsDirtyState {
  const registry = createRegistry()
  const source = Symbol('settings-layout')
  provide(SETTINGS_DIRTY_REGISTRY, registry)
  return {
    ...scopedState(registry, source),
    clearDirty: registry.clear,
  }
}

export function useSettingsDirtyState(): SettingsDirtyState {
  const registry = inject(SETTINGS_DIRTY_REGISTRY, null) ?? createRegistry()
  const source = Symbol('settings-section')
  onBeforeUnmount(() => registry.remove(source))
  return scopedState(registry, source)
}
