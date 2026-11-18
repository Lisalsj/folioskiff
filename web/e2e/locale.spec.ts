import { expect, test } from '@playwright/test'
import { fulfillCompletedOnboarding } from './support'

test('an English deep link keeps paper text and an unsubmitted search when switching languages', async ({ page }) => {
  const title = '最近运行'
  const draft = '未提交的研究问题'
  await page.addInitScript(() => {
    if (localStorage.getItem('paper-reader.locale') === null) localStorage.setItem('paper-reader.locale', 'en')
  })
  await page.route('**/api/v1/onboarding', async (route) => { await fulfillCompletedOnboarding(route) })
  await page.route('**/api/v1/library/works?**', (route) => route.fulfill({
    json: {
      items: [{
        id: 'work-original', title, abstract: '保留论文原始摘要', authors: ['我的论文库'],
        representative_document: null, document_count: 0, published_at: null,
      }], total: 1, limit: 20, offset: 0,
    },
  }))
  await page.goto('/search')
  await expect(page.getByRole('heading', { name: 'Search papers', exact: true })).toBeVisible()
  await expect(page).toHaveTitle(/Search papers/)
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
  const field = page.getByRole('searchbox')
  await field.fill(draft)
  const mobile = (page.viewportSize()?.width ?? 1440) <= 640
  if (mobile) await page.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('button', { name: 'Interface language', exact: true }).click()
  if (mobile) await page.getByRole('button', { name: '关闭更多功能' }).click()
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN')
  await expect(field).toHaveValue(draft)
  await expect(page.getByRole('heading', { name: '搜索论文', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
  await page.reload()
  await expect(page).toHaveTitle(/搜索论文/)
})
