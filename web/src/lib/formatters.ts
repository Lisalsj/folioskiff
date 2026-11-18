import { currentLocale, libraryText as t } from '@/stores/locale'
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}

export function formatAuthors(authors: string[], maximum = 3): string {
  if (authors.length === 0) return t('作者信息未知')
  const visible = authors.slice(0, maximum).join('、')
  return authors.length > maximum ? t('{0} 等 {1} 位作者', visible, authors.length) : visible
}

export function formatShortDate(value: string | null): string {
  if (!value) return t('日期未知')
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return t('日期未知')
  return new Intl.DateTimeFormat(currentLocale(), {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(date)
}
