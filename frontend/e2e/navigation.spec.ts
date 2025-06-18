import { expect, test } from '@playwright/test'
import { login, watchConsoleErrors } from './helpers'

const PAGES: { path: string; heading: RegExp }[] = [
  { path: '/', heading: /plant overview/i },
  { path: '/schedule', heading: /production schedule/i },
  { path: '/optimizer', heading: /schedule optimizer/i },
  { path: '/work-orders', heading: /work orders/i },
  { path: '/resources', heading: /resources/i },
  { path: '/scenarios', heading: /scenario simulator/i },
  // The analytics screen shows an empty state until two plan versions exist.
  { path: '/analytics', heading: /analytics|not enough plan versions/i },
  { path: '/assistant', heading: /ai assistant/i },
  { path: '/settings/ai', heading: /ai settings/i },
]

test.describe('navigation', () => {
  test('every screen renders without browser errors', async ({ page }) => {
    const errors = watchConsoleErrors(page)
    await login(page)
    for (const target of PAGES) {
      await page.goto(target.path)
      await page.waitForLoadState('networkidle')
      await expect(page.getByRole('heading', { name: target.heading }).first()).toBeVisible()
    }
    expect(errors).toEqual([])
  })

  test('the navigation menu links to every section', async ({ page }) => {
    await login(page)
    for (const label of [
      'Overview',
      'Schedule',
      'Optimizer',
      'Work orders',
      'Resources',
      'Scenarios',
      'Analytics',
      'AI assistant',
    ]) {
      await expect(page.getByRole('link', { name: label, exact: true })).toBeVisible()
    }
  })
})
