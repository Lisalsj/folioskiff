import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

import {
  installRelatedResourcesFixture,
  showReaderAssistant,
} from './support'

test('related research is staged, partially recoverable and saves only verified papers', async ({
  page,
}, testInfo) => {
  const fixture = await installRelatedResourcesFixture(page)

  await page.goto(`/library/${fixture.workId}/read/${fixture.documentVersionId}`)
  await expect(page.locator('[data-test="reader-workspace"]')).toBeVisible()
  expect(fixture.relatedApiRequests).toEqual([])
  expect(fixture.agentRefineCalls).toBe(0)
  expect(fixture.academicSourceCalls).toEqual([])

  await showReaderAssistant(page, testInfo.project.name)
  const assistant = page.locator('[data-test="research-assistant"]')
  await assistant.locator('[data-test="assistant-mode-related"]').click()
  await expect(assistant.locator('[data-test="related-task"]')).toBeVisible()
  expect(fixture.relatedApiRequests).toEqual([])

  await assistant.locator('[data-test="start-related"]').click()
  await expect(assistant.locator('[data-test="intent-editor"]')).toBeVisible()
  expect(fixture.draftCalls).toBe(1)
  expect(fixture.agentRefineCalls).toBe(0)
  expect(fixture.academicSourceCalls).toEqual([])

  await assistant.getByPlaceholder('例如：更关注可复现评测，并排除纯综述').fill(
    '加入可复现基准，并保留中英文同义词。',
  )
  await assistant.locator('[data-test="refine-related"]').click()
  await expect(assistant.locator('[data-test="intent-diff"]')).toContainText('reproducibility')
  expect(fixture.agentRefineCalls).toBe(1)
  expect(fixture.academicSourceCalls).toEqual([])

  const confirmButton = assistant.locator('[data-test="confirm-related"]')
  await confirmButton.scrollIntoViewIfNeeded()
  if (testInfo.project.name === 'mobile-chromium') {
    await expect(page.locator('[data-test="mobile-primary-nav"]')).toBeHidden()
    const buttonBox = await confirmButton.boundingBox()
    expect(buttonBox).not.toBeNull()
    expect(buttonBox!.y).toBeGreaterThanOrEqual(0)
    expect(buttonBox!.y + buttonBox!.height).toBeLessThanOrEqual(page.viewportSize()!.height)
  }
  await page.screenshot({
    path: testInfo.outputPath('related-intent-workbench.png'),
    fullPage: true,
  })

  await confirmButton.click()
  await expect(assistant.locator('[data-test="run-related"]')).toBeEnabled()
  expect(fixture.runCalls).toBe(0)
  expect(fixture.academicSourceCalls).toEqual([])

  await assistant.locator('[data-test="run-related"]').click()
  await expect(assistant.getByRole('tab', { name: /相关论文/ })).toBeVisible()
  await expect(assistant.getByText('Verified Diffusion Agent', { exact: true })).toBeVisible()
  await expect(assistant.getByText('身份相似但未静默合并', { exact: false })).toBeVisible()
  await expect(assistant.getByText('覆盖 67%', { exact: true })).toBeVisible()

  expect(fixture.runCalls).toBe(1)
  expect(fixture.academicSourceCalls).toEqual(['arxiv', 'openreview', 'ieee'])
  expect(fixture.rateLimitedSources).toEqual(['ieee'])
  expect(fixture.duplicateCandidatesCollapsed).toBe(1)

  const verifiedSave = assistant.locator('[data-test="save-related-related-paper-verified"]')
  await expect(verifiedSave).toBeEnabled()
  await verifiedSave.click()
  await expect(assistant.getByText('已在论文库', { exact: true })).toBeVisible()
  expect(fixture.saveCalls).toBe(1)

  const mergeReviewSave = assistant.locator('[data-test="save-related-related-paper-review"]')
  await expect(mergeReviewSave).toBeDisabled()

  const accessibility = await new AxeBuilder({ page })
    .include('[data-test="research-assistant"]')
    .analyze()
  expect(accessibility.violations).toEqual([])
  await page.screenshot({
    path: testInfo.outputPath('related-paper-results.png'),
    fullPage: true,
  })
  expect(fixture.externalBrowserRequests).toEqual([])
})

test('external knowledge resources require visible per-run consent and verified HTTPS', async ({
  page,
}, testInfo) => {
  const fixture = await installRelatedResourcesFixture(page)

  await page.goto(`/library/${fixture.workId}/read/${fixture.documentVersionId}`)
  await showReaderAssistant(page, testInfo.project.name)
  const assistant = page.locator('[data-test="research-assistant"]')
  await assistant.locator('[data-test="assistant-mode-related"]').click()
  await assistant.locator('[data-test="start-related"]').click()
  await assistant.locator('[data-test="confirm-related"]').click()

  await assistant.getByLabel('同时查找外部知识资源', { exact: false }).check()
  await expect(assistant.getByText('Codex 将联网访问公共网站', { exact: true })).toBeVisible()
  await expect(assistant.locator('[data-test="run-related"]')).toBeDisabled()
  expect(fixture.externalResearchCalls).toBe(0)

  await assistant.getByLabel('我同意本次外部研究', { exact: true }).check()
  await expect(assistant.locator('[data-test="run-related"]')).toBeEnabled()
  await assistant.locator('[data-test="run-related"]').click()
  await expect(assistant.getByRole('tab', { name: /知识资源/ })).toBeVisible()
  await assistant.getByRole('tab', { name: /知识资源/ }).click()

  const officialLink = assistant.getByRole('link', { name: /Official diffusion guide/ })
  await expect(officialLink).toHaveAttribute('href', 'https://docs.example.org/diffusion')
  await expect(officialLink).toHaveAttribute('rel', /noopener/)
  await expect(assistant.getByText('Rejected loopback mirror')).toHaveCount(0)
  await expect(assistant.getByText('知识网站，不是论文', { exact: false })).toBeVisible()

  expect(fixture.externalResearchCalls).toBe(1)
  expect(fixture.rejectedKnowledgeUrls).toEqual(['https://127.0.0.1/private'])
  expect(fixture.runRequests[0]).toMatchObject({
    include_academic_sources: true,
    include_external_web: true,
    external_web_consent: true,
    agent: { backend_id: 'codex_cli' },
  })

  const accessibility = await new AxeBuilder({ page })
    .include('[data-test="research-assistant"]')
    .analyze()
  expect(accessibility.violations).toEqual([])
  await page.screenshot({
    path: testInfo.outputPath('related-knowledge-results.png'),
    fullPage: true,
  })
  expect(fixture.externalBrowserRequests).toEqual([])
})
