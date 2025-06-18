import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const API_PORT = process.env.E2E_API_PORT ?? '8000'
const WEB_PORT = process.env.E2E_WEB_PORT ?? '5173'
const BASE_URL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${WEB_PORT}`

/**
 * End-to-end tests drive the real application: a real API server with the
 * seeded demo factory and the real Vite dev server.  Nothing is mocked, so a
 * green run is evidence that the whole stack works.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    actionTimeout: 20_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1600, height: 1000 } },
    },
  ],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : [
        {
          command: 'node ./e2e/start-api.mjs',
          url: `http://127.0.0.1:${API_PORT}/api/health`,
          reuseExistingServer: false,
          timeout: 180_000,
          cwd: __dirname,
          env: {
            E2E_API_PORT: API_PORT,
            E2E_WEB_PORT: WEB_PORT,
            ...(process.env.E2E_PYTHON ? { E2E_PYTHON: process.env.E2E_PYTHON } : {}),
          },
        },
        {
          command: 'npm run dev -- --port ' + WEB_PORT,
          url: BASE_URL,
          reuseExistingServer: false,
          timeout: 180_000,
          cwd: __dirname,
          env: {
            VITE_API_TARGET: `http://127.0.0.1:${API_PORT}`,
          },
        },
      ],
})
