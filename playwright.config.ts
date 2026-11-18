import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'

const e2eRoot = resolve('.tmp/playwright-e2e')
const browserState = resolve(e2eRoot, 'browser-state.json')

export default defineConfig({
  testDir: './web/e2e',
  globalSetup: './web/e2e/global-setup.ts',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:4178',
    storageState: browserState,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run build && uv run python -m paper_reader.cli serve --no-browser --port 4178',
    env: {
      PAPER_READER_CACHE_ROOT: resolve(e2eRoot, 'cache'),
      PAPER_READER_DISABLE_UPDATE_CHECKS: '1',
      PAPER_READER_LIBRARY_ROOT: resolve(e2eRoot, 'library'),
      PAPER_READER_STATE_ROOT: resolve(e2eRoot, 'state'),
    },
    url: 'http://127.0.0.1:4178/api/v1/health',
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    {
      name: 'desktop-chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'medium-chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 900, height: 900 } },
    },
    {
      name: 'mobile-chromium',
      use: { ...devices['Pixel 7'] },
    },
  ],
})
