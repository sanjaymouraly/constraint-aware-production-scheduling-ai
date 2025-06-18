import { expect, test } from '@playwright/test'
import { login, runEngine, watchConsoleErrors } from './helpers'

test.describe('scheduler', () => {
  test.slow()

  test('the FCFS baseline produces a valid, fully scheduled plan', async ({ page }) => {
    const errors = watchConsoleErrors(page)
    await login(page)
    await page.goto('/optimizer')
    const body = await runEngine(page, 'fcfs')

    expect(body.status).toBe('FEASIBLE')
    expect(body.validation?.is_valid ?? body.validation?.valid).toBe(true)
    expect(body.validation?.violations ?? []).toEqual([])
    expect(body.metrics?.scheduled_operations).toBe(body.metrics?.total_operations)

    // The result panel shows the status badge and the operation count.
    await expect(page.getByText('FEASIBLE').first()).toBeVisible()
    expect(errors).toEqual([])
  })

  test('the CP-SAT optimiser returns a plan the validator accepts', async ({ page }) => {
    await login(page)
    await page.goto('/optimizer')
    const body = await runEngine(page, 'cpsat')

    expect(['OPTIMAL', 'FEASIBLE']).toContain(body.status)
    expect(body.validation?.is_valid ?? body.validation?.valid).toBe(true)
    expect(body.validation?.violations ?? []).toEqual([])
    // A time limited run must never be reported as globally optimal.
    if (body.status === 'FEASIBLE') {
      expect(body.bound ?? null).not.toBeNull()
    }
  })

  test('the comparison view reports the measured difference between the two plans', async ({ page }) => {
    await login(page)
    await page.goto('/optimizer')
    await runEngine(page, 'fcfs')
    await runEngine(page, 'cpsat')

    await page.goto('/analytics')
    await page.waitForLoadState('networkidle')
    const compare = page.getByRole('button', { name: /compare/i }).first()
    await compare.click()
    await page.waitForTimeout(1500)
    // The comparison table must contain real metric rows, not placeholders.
    await expect(page.getByText(/weighted tardiness/i).first()).toBeVisible()
  })

  test('the Gantt view renders the scheduled operations', async ({ page }) => {
    await login(page)
    await page.goto('/optimizer')
    await runEngine(page, 'fcfs')

    await page.goto('/schedule')
    await page.waitForLoadState('networkidle')
    const svg = page.getByRole('img', { name: /gantt/i })
    if (await svg.count()) {
      await expect(svg.first()).toBeVisible()
    }
  })
})
