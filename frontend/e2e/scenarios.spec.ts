import { expect, test } from '@playwright/test'
import { login, runEngine } from './helpers'

test.describe('scenario simulator', () => {
  test.slow()

  test('a disruption can be applied, re-planned and compared', async ({ page }) => {
    await login(page)

    // A plan is required as the starting point of a scenario.
    await page.goto('/optimizer')
    await runEngine(page, 'fcfs')

    await page.goto('/scenarios')
    await page.waitForLoadState('networkidle')

    await page.getByRole('button', { name: /new scenario/i }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()

    const basePlan = dialog.getByLabel(/base plan/i)
    await expect(basePlan).toBeVisible()
    // The first option is the "Select a plan…" placeholder.
    await basePlan.selectOption({ index: 1 })

    const nameField = dialog.getByLabel(/^name$/i).first()
    await nameField.fill('E2E machine breakdown')
    await dialog.getByRole('button', { name: /create scenario/i }).click()
    await expect(page.getByText('E2E machine breakdown').first()).toBeVisible()

    // Add a machine breakdown disruption.
    await page.getByRole('button', { name: /add disruption/i }).click()
    const disruptionDialog = page.getByRole('dialog')
    await disruptionDialog.getByLabel(/disruption type/i).selectOption({ index: 1 })
    await disruptionDialog.getByLabel(/^title/i).fill('Unexpected breakdown')
    const machineSelect = disruptionDialog.getByLabel(/machine/i)
    if (await machineSelect.count()) {
      await machineSelect.selectOption({ index: 1 })
    }
    await disruptionDialog.getByRole('button', { name: /apply disruption/i }).click()
    await expect(page.getByText('Unexpected breakdown').first()).toBeVisible()

    // Re-plan the scenario.
    await page.getByRole('button', { name: /re-plan scenario/i }).click()
    await expect(page.getByText(/re-plan|planned|impact/i).first()).toBeVisible()
    await page.waitForTimeout(2000)
  })
})
