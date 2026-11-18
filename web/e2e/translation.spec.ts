import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

import {
  installTranslationFixture,
  selectFixturePdfText,
  showReaderAssistant,
} from './support'

test('translates selected English text to Chinese without external research', async ({
  page,
}, testInfo) => {
  const fixture = await installTranslationFixture(page)

  await page.goto(`/library/${fixture.workId}/read/${fixture.documentVersionId}`)
  await expect(page.locator('[data-test="reader-workspace"]')).toBeVisible()
  await selectFixturePdfText(page, 'Diffusion model')
  await showReaderAssistant(page, testInfo.project.name)

  const assistant = page.locator('[data-test="research-assistant"]')
  await assistant.locator('[data-test="assistant-mode-translate"]').click()
  await expect(assistant.locator('[data-test="language-pair"]')).toContainText(
    '英文 → 简体中文',
  )
  await expect(assistant.locator('[data-test="translation-evidence-boundary"]')).toContainText(
    '仅使用当前论文与本地资料',
  )
  expect(fixture.translationCommands).toHaveLength(0)

  await assistant.locator('[data-test="translation-start"]').click()
  await expect(assistant.locator('[data-test="translation-task-card"]')).toContainText('已完成')
  expect(fixture.translationCommands).toHaveLength(1)
  expect(fixture.translationCommands[0]?.body).toMatchObject({
    source_language: 'en',
    target_language: 'zh-CN',
    network_policy: 'local_only',
    context: {
      scope: 'selection',
      page_number: 1,
      selection: {
        page_number: 1,
        quote_text: 'Diffusion model',
      },
    },
  })
  expect(fixture.translationCommands[0]?.idempotencyKey).toMatch(/^translation-ui-/)

  await assistant.locator('[data-test="open-bilingual"]').click()
  await expect(page).toHaveURL(new RegExp(
    `/library/${fixture.workId}/translations/${fixture.selectionArtifactId}`,
  ))
  await expect(page.locator('[data-segment="segment-000001"] [data-side="target"]'))
    .toContainText('扩散模型')
  expect(fixture.externalRequests).toEqual([])

  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
})

test('full translation cancels, reloads, and resumes only unfinished segments', async ({
  page,
}, testInfo) => {
  const fixture = await installTranslationFixture(page)

  await page.goto(`/library/${fixture.workId}/read/${fixture.documentVersionId}`)
  await expect(page.locator('[data-test="reader-workspace"]')).toBeVisible()
  await showReaderAssistant(page, testInfo.project.name)

  let assistant = page.locator('[data-test="research-assistant"]')
  await assistant.locator('[data-test="assistant-mode-translate"]').click()
  await assistant.locator('[data-test="assistant-context-choice"]').selectOption('full_document')
  await assistant.locator('[data-test="translation-start"]').click()
  await expect(assistant.locator('[data-test="translation-task-card"]')).toContainText('1 / 2')
  await assistant.locator('[data-test="translation-cancel"]').click()
  await expect(assistant.locator('[data-test="translation-resume"]')).toBeVisible()

  await page.reload()
  await showReaderAssistant(page, testInfo.project.name)
  assistant = page.locator('[data-test="research-assistant"]')
  await assistant.locator('[data-test="assistant-mode-translate"]').click()
  await expect(assistant.locator('[data-test="translation-task-card"]')).toContainText('1 / 2')
  await assistant.locator('[data-test="translation-resume"]').click()
  await expect(assistant.locator('[data-test="translation-task-card"]')).toContainText('2 / 2')
  await expect(assistant.locator('[data-test="open-bilingual"]')).toBeVisible()

  expect(fixture.resumeCount).toBe(1)
  expect(fixture.resumeIdempotencyKeys).toHaveLength(1)
  expect(fixture.resumeIdempotencyKeys[0]).toMatch(/^translation-ui-/)
  expect(fixture.resumeIdempotencyKeys[0]).not.toBe(
    fixture.translationCommands[0]?.idempotencyKey,
  )
  expect(fixture.resumedSegmentIds).toEqual(['segment-000002'])
  expect(fixture.translationCommands[0]?.body).toMatchObject({
    network_policy: 'local_only',
    context: { scope: 'full_document' },
  })

  await assistant.locator('[data-test="open-bilingual"]').click()
  await expect(page.locator('[data-segment="segment-000001"] [data-side="target"]'))
    .toContainText('扩散模型用于局部证据。')
  await page.getByLabel('当前页').selectOption('2')
  await expect(page.locator('[data-segment="segment-000002"] [data-side="target"]'))
    .toContainText('相关工作来自扫描页。')
  await expect(page.locator('[data-segment="segment-000002"]')).toContainText('OCR 62%')
  expect(fixture.externalRequests).toEqual([])

  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
})
