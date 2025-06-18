import { expect, test } from '@playwright/test'
import { login, watchConsoleErrors } from './helpers'

test.describe('authentication', () => {
  test('the login screen rejects bad credentials', async ({ page }) => {
    const errors = watchConsoleErrors(page)
    await page.goto('/login')
    await page.fill('#username', 'admin')
    await page.fill('#password', 'wrong-password')
    await page.click('button[type=submit]')
    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page).toHaveURL(/\/login/)
    expect(errors.filter((e) => !e.includes('401'))).toEqual([])
  })

  test('the administrator can sign in and reach the dashboard', async ({ page }) => {
    await login(page)
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('heading', { name: /factory overview|overview/i }).first()).toBeVisible()
  })

  test('an unauthenticated visitor is redirected to the login screen', async ({ page }) => {
    await page.goto('/optimizer')
    await expect(page).toHaveURL(/\/login/)
  })

  test('signing out clears the session', async ({ page }) => {
    await login(page)
    await page.getByRole('button', { name: /sign out|log out/i }).first().click()
    await expect(page).toHaveURL(/\/login/)
    await page.goto('/work-orders')
    await expect(page).toHaveURL(/\/login/)
  })
})
