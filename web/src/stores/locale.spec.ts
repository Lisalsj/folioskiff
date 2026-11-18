import { afterEach, describe, expect, it } from 'vitest'

import {
  libraryText, LOCALE_CACHE_KEY, messageRef, messageTemplate, readCachedLocale,
  serverMessage, setLocale, shellText, useLocale,
} from './locale'

describe('interface language', () => {
  afterEach(async () => {
    await setLocale('zh-CN')
    window.localStorage.removeItem(LOCALE_CACHE_KEY)
  })

  it('shares the language between controls, updates the page language, and persists the choice', async () => {
    expect(readCachedLocale()).toBe('zh-CN')
    const first = useLocale()
    const second = useLocale()
    await first.toggleLocale()
    expect(second.locale.value).toBe('en')
    expect(readCachedLocale()).toBe('en')
    expect(document.documentElement.lang).toBe('en')
    expect(shellText('我的论文库')).toBe('My library')
  })

  it('switches an existing status while keeping user-supplied interpolation values literal', async () => {
    const status = messageRef(libraryText)
    status.value = messageTemplate('为《{0}》添加 PDF 版本', '我的论文库 {0}')
    expect(status.value).toContain('为《我的论文库 {0}》')
    await setLocale('en')
    expect(status.value).toContain('我的论文库 {0}')
    expect(status.value).not.toContain('添加 PDF 版本')
    await setLocale('zh-CN')
    expect(status.value).toBe('为《我的论文库 {0}》添加 PDF 版本')
  })

  it('translates known server diagnostics with verbatim arguments and retains unknown diagnostics', async () => {
    serverMessage('未发现指定的 Ollama 模型：名称{0}')
    await setLocale('en')
    expect(serverMessage('未发现指定的 Ollama 模型：名称{0}')).toContain('名称{0}')
    expect(serverMessage('未发现指定的 Ollama 模型：名称{0}')).not.toContain('未发现')
    expect(serverMessage('Custom diagnostic')).toBe('Custom diagnostic')
    expect(serverMessage('constructor')).toBe('constructor')
  })
})
