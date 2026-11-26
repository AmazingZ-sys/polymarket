import { defineConfig } from '@playwright/test'

const baseURL = process.env.PQ_UI_URL || 'http://127.0.0.1:4178'
export default defineConfig({
  testDir: './tests',
  use: {
    baseURL,
    headless: true,
    launchOptions: { executablePath: process.env.PQ_CHROMIUM || undefined },
    viewport: { width: 1350, height: 860 },
  },
  webServer: process.env.PQ_UI_URL
    ? undefined
    : {
        command: 'pnpm exec vite --host 127.0.0.1 --port 4178 --strictPort',
        url: baseURL,
        reuseExistingServer: !process.env.CI,
      },
})
