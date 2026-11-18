import { describe, expect, it } from 'vitest'

import { formatAuthors, formatBytes, formatShortDate } from './formatters'

describe('formatters', () => {
  it('formats byte sizes consistently across storage and model views', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(2 * 1024 ** 2)).toBe('2.0 MB')
  })

  it('keeps author truncation consistent across library and search', () => {
    expect(formatAuthors([])).toBe('作者信息未知')
    expect(formatAuthors(['甲', '乙', '丙', '丁'])).toBe('甲、乙、丙 等 4 位作者')
  })

  it('uses a stable fallback for missing or invalid dates', () => {
    expect(formatShortDate(null)).toBe('日期未知')
    expect(formatShortDate('invalid')).toBe('日期未知')
  })
})
