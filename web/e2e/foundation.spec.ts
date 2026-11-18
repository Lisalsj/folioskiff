import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { fulfillCompletedOnboarding } from './support'

function usesMobileNavigation(page: Page): boolean {
  return (page.viewportSize()?.width ?? 1440) <= 640
}

function primaryNavigation(page: Page) {
  return page.getByRole('navigation', {
    name: usesMobileNavigation(page) ? '移动主导航' : '主导航',
  })
}

test('reading briefing exposes the stable product navigation', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { level: 1, name: '阅读简报' })).toBeVisible()
  await expect(page.getByRole('link', { name: '新建关注计划' })).toBeVisible()
  const navigation = primaryNavigation(page)
  await expect(navigation.getByRole('link')).toHaveCount(usesMobileNavigation(page) ? 3 : 7)
  if (usesMobileNavigation(page)) {
    await expect(navigation.getByRole('button', { name: '更多' })).toBeInViewport({ ratio: 1 })
    await expect(page.getByRole('navigation', { name: '应用操作' }).getByRole('link', { name: '搜索论文' })).toBeInViewport()
    const tops = await navigation.locator('a, button').evaluateAll((items) =>
      items.map((item) => Math.round(item.getBoundingClientRect().top)))
    expect(new Set(tops).size).toBe(1)
  }
  const clippedLabels = await navigation.locator('a, button').evaluateAll((controls) => controls
    .filter((control) => control.scrollWidth > control.clientWidth + 1)
    .map((control) => control.textContent?.trim()))
  expect(clippedLabels, 'primary navigation labels must remain fully visible').toEqual([])
})

test('foundation shell has no automatically detectable accessibility violations', async ({
  page,
}) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: '阅读简报' })).toBeVisible()
  const results = await new AxeBuilder({ page }).analyze()

  expect(results.violations).toEqual([])
})

test('search restores its filters and page after opening a matching paper record', async ({ page }) => {
  const papers = Array.from({ length: 23 }, (_, index) => ({
    id: `work-${index + 1}`, title: 'Evidence review', authors: [`Author ${index + 1}`],
    published_at: null, added_via: 'doi', added_at: '2026-11-01T12:00:00Z',
    abstract: null, doi: null, favorite: true, feedback: 'neutral', pinned: false,
    reading_progress: 0, analysis_state: 'not_analyzed', document_count: 0,
    representative_document: null,
  }))
  await page.route('**/api/v1/onboarding', async (route) => { await fulfillCompletedOnboarding(route) })
  await page.route('**/api/v1/library/works?**', async (route) => {
    const params = new URL(route.request().url()).searchParams
    const offset = Number(params.get('offset'))
    expect(params.get('favorite')).toBe('true')
    expect(params.get('query')).toBe('Evidence')
    await route.fulfill({ json: { items: papers.slice(offset, offset + 20), total: 23, offset, limit: 20 } })
  })
  await page.route('**/api/v1/works/work-23', (route) => route.fulfill({ json: papers[22] }))
  await page.goto('/search?query=Evidence&favorite=true')
  await page.getByRole('button', { name: '下一页' }).click()
  const lastResult = page.locator('article').filter({ hasText: 'Author 23' })
  await lastResult.getByRole('link', { name: '查看论文记录' }).click()
  await expect(page).toHaveURL(/\/library\?work=work-23$/)
  await expect(page.getByRole('button', { name: '为《Evidence review》添加 PDF 版本' })).toBeVisible()
  await expect(page.getByText('Author 23', { exact: true })).toBeVisible()
  await page.goBack()
  await expect(page.getByLabel('搜索意图')).toHaveValue('Evidence')
  await expect(page.getByLabel('仅收藏')).toBeChecked()
  await expect(lastResult).toBeVisible()
  await expect(page.getByRole('button', { name: '下一页' })).toBeDisabled()
})

test('keyboard users bypass repeated navigation and keep context after route changes', async ({
  page,
}) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: '阅读简报' })).toBeVisible()

  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  await page.keyboard.press('Tab')
  const bypass = page.getByRole('link', { name: '跳到主要内容' })
  await expect(bypass).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('main')).toBeFocused()

  const navigation = primaryNavigation(page)
  const plansLabel = usesMobileNavigation(page) ? '计划' : '关注计划'
  await navigation.getByRole('link', { name: plansLabel }).click()
  await expect(page).toHaveURL(/\/plans$/)
  await expect(page).toHaveTitle('关注计划 | FolioSkiff')
  await expect(page.locator('main')).toBeFocused()
  await expect(
    navigation.getByRole('link', { name: plansLabel }),
  ).toHaveAttribute('aria-current', 'page')

  const targetSizes = await navigation
    .locator('a, button')
    .evaluateAll((links) =>
      links.map((link) => {
        const bounds = link.getBoundingClientRect()
        return { label: link.textContent?.trim(), width: bounds.width, height: bounds.height }
      }),
    )
  expect(
    targetSizes.filter((target) => target.width < 24 || target.height < 24),
    'primary navigation targets smaller than 24 by 24 CSS pixels',
  ).toEqual([])

  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  const horizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(horizontalOverflow, '200% text reflow').toBeLessThanOrEqual(1)

  await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' })
  const adapted = await new AxeBuilder({ page }).analyze()
  expect(adapted.violations).toEqual([])
})

test('task drawer remains usable on narrow screens', async ({ page }) => {
  await page.route('**/api/v1/jobs', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '[]',
    })
  })
  await page.route('**/api/v1/events', async (route) => route.abort())

  await page.goto('/')
  await page.getByRole('button', { name: '查看任务进度' }).click()

  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('heading', { name: '任务进度' })).toBeVisible()
  await expect(page.getByText('当前没有任务')).toBeVisible()
})
