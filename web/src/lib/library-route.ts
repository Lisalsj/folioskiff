import type { LocationQueryValue } from 'vue-router'

export const LIBRARY_PAGE_SIZE = 20

export function routeText(value: LocationQueryValue | LocationQueryValue[] | undefined): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function pageOffset(value: LocationQueryValue | LocationQueryValue[] | undefined): number {
  const page = Number(routeText(value))
  const offset = (page - 1) * LIBRARY_PAGE_SIZE
  return Number.isSafeInteger(page) && Number.isSafeInteger(offset) && offset > 0 ? offset : 0
}
