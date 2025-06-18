import { expect, type Page } from '@playwright/test'

export const ADMIN = { username: 'admin', password: 'admin' }

export async function login(page: Page, username = ADMIN.username, password = ADMIN.password) {
  await page.goto('/login')
  await page.fill('#username', username)
  await page.fill('#password', password)
  await page.click('button[type=submit]')
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30_000 })
  await page.waitForLoadState('networkidle')
}

/** Run the optimiser from the UI and wait for the API response. */
export async function runEngine(page: Page, engine: 'fcfs' | 'cpsat') {
  const label = engine === 'fcfs' ? 'FCFS baseline' : 'CP-SAT optimizer'
  await page.getByRole('button', { name: label }).click()
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/api/scheduling/run') && r.request().method() === 'POST',
      { timeout: 300_000 },
    ),
    page.getByRole('button', { name: new RegExp(`Run ${engine === 'fcfs' ? 'FCFS' : 'CP-SAT'}`, 'i') }).click(),
  ])
  expect(response.ok()).toBeTruthy()
  const body = await response.json()
  await page.waitForTimeout(800)
  return body
}

/** Collect browser console errors; the suite fails if any appear. */
export function watchConsoleErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(String(error)))
  return errors
}
