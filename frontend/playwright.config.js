import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for TaskLane E2E testing.
 * Uses local Microsoft Edge on Windows if no browser is downloaded,
 * falling back to default Chromium in CI.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  expect: {
    timeout: 5000,
  },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_TEST_BASE_URL || 'http://localhost:8000',
    trace: 'on-first-retry',
    channel: process.env.PLAYWRIGHT_CHANNEL || (process.env.CI ? undefined : 'msedge'),
    headless: true,
  },
  projects: [
    {
      name: 'desktop',
      use: {
        ...devices['Desktop Chrome'],
        channel: process.env.PLAYWRIGHT_CHANNEL || (process.env.CI ? undefined : 'msedge'),
      },
    },
  ],
});
