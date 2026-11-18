import { computed, shallowRef } from 'vue'

import type { components } from '@/api/schema'

export type LibraryWork = components['schemas']['LibraryWorkResponse']

export function useOrderedSelection(maximum: number) {
  const selectedWorks = shallowRef<LibraryWork[]>([])
  const selectedIds = computed(() => selectedWorks.value.map((work) => work.id))

  function toggle(work: LibraryWork, checked: boolean): void {
    if (!checked) {
      selectedWorks.value = selectedWorks.value.filter((item) => item.id !== work.id)
    } else if (!selectedIds.value.includes(work.id) && selectedWorks.value.length < maximum) {
      selectedWorks.value = [...selectedWorks.value, work]
    }
  }

  function move(id: string, direction: -1 | 1): void {
    const index = selectedIds.value.indexOf(id)
    const target = index + direction
    if (index < 0 || target < 0 || target >= selectedWorks.value.length) return
    const reordered = [...selectedWorks.value]
    const [work] = reordered.splice(index, 1)
    reordered.splice(target, 0, work!)
    selectedWorks.value = reordered
  }

  return { selectedWorks, selectedIds, toggle, move }
}
