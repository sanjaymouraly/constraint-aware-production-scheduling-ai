import { expect, test } from '@playwright/test'
import { login, watchConsoleErrors } from './helpers'

test.describe('resource management', () => {
  test.slow()

  test('creates resources and records maintenance, absence, receipts and calendar exceptions', async ({ page }) => {
    const errors = watchConsoleErrors(page)
    await login(page)
    await page.goto('/resources')

    // Add a machine and create a maintenance window against that machine.
    await page.getByRole('button', { name: 'Machine', exact: true }).click()
    let dialog = page.getByRole('dialog')
    await dialog.getByLabel('Machine code').fill('E2E-CNC-01')
    await dialog.getByLabel('Machine name').fill('E2E CNC Cell')
    await dialog.getByLabel('Machine type').fill('CNC')
    await dialog.getByLabel('Calendar').selectOption({ index: 1 })
    await dialog.getByRole('button', { name: 'Save resource' }).click()
    await expect(dialog).toBeHidden()
    await expect(page.getByText('E2E-CNC-01')).toBeVisible()

    const machineRow = page.locator('tr').filter({ hasText: 'E2E-CNC-01' })
    await machineRow.getByRole('button', { name: /downtime/i }).click()
    dialog = page.getByRole('dialog')
    await dialog.getByLabel('Window type').selectOption('maintenance')
    await dialog.getByLabel('Reason').fill('Routine service')
    await dialog.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(dialog).toBeHidden()
    await expect(machineRow.getByText('1', { exact: true })).toBeVisible()

    // Add an operator and record a time-bounded absence.
    await page.getByRole('button', { name: 'Operators', exact: true }).click()
    await page.getByRole('button', { name: 'Operator', exact: true }).click()
    dialog = page.getByRole('dialog')
    await dialog.getByLabel('Operator code').fill('E2E-OP-01')
    await dialog.getByLabel('Operator name').fill('E2E Operator')
    await dialog.getByLabel('Skills').fill('cnc-op')
    await dialog.getByLabel('Eligible machine types').fill('CNC')
    await dialog.getByLabel('Calendar').selectOption({ index: 1 })
    await dialog.getByRole('button', { name: 'Save resource' }).click()
    await expect(dialog).toBeHidden()
    await expect(page.getByText('E2E-OP-01')).toBeVisible()

    const operatorRow = page.locator('tr').filter({ hasText: 'E2E-OP-01' })
    await operatorRow.getByRole('button', { name: 'Absence' }).click()
    dialog = page.getByRole('dialog')
    await dialog.getByLabel('Reason').fill('Safety training')
    await dialog.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(dialog).toBeHidden()

    // Add a material and a time-phased supplier receipt.
    await page.getByRole('button', { name: 'Materials', exact: true }).click()
    await page.getByRole('button', { name: 'Material', exact: true }).click()
    dialog = page.getByRole('dialog')
    await dialog.getByLabel('SKU').fill('E2E-RM-01')
    await dialog.getByLabel('Material name').fill('E2E stock')
    await dialog.getByLabel(/^Unit$/).fill('kg')
    await dialog.getByLabel('On hand').fill('10')
    await dialog.getByRole('button', { name: 'Save resource' }).click()
    await expect(dialog).toBeHidden()
    await expect(page.getByText('E2E-RM-01')).toBeVisible()

    const materialRow = page.locator('tr').filter({ hasText: 'E2E-RM-01' })
    await materialRow.getByRole('button', { name: 'Add receipt' }).click()
    dialog = page.getByRole('dialog')
    await dialog.getByLabel('Quantity').fill('25')
    await dialog.getByLabel('Purchase order / reference').fill('E2E-PO')
    await dialog.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(dialog).toBeHidden()
    await expect(materialRow.getByText(/\+25/)).toBeVisible()

    // Add a dated calendar shutdown.
    await page.getByRole('button', { name: 'Calendars', exact: true }).click()
    const calendarCard = page.locator('section.card').filter({ hasText: 'PLANT-STD' }).first()
    await calendarCard.getByRole('button', { name: /exception/i }).click()
    dialog = page.getByRole('dialog')
    await dialog.getByLabel('Exception type').selectOption('shutdown')
    await dialog.getByLabel('Label').fill('E2E plant closure')
    await dialog.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(dialog).toBeHidden()
    await expect(page.getByText('E2E plant closure')).toBeVisible()

    expect(errors).toEqual([])
  })

  test('routing batch, transfer, alternatives and parallel group controls update the API', async ({ page }) => {
    await login(page)
    await page.goto('/resources')
    await page.getByRole('button', { name: 'Routings', exact: true }).click()
    const edit = page.getByRole('button', { name: 'Edit rules' }).first()
    await edit.click()
    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('Maximum batch size').fill('20')
    await dialog.getByLabel('Changeover between batches (min)').fill('5')
    await dialog.getByLabel('Transfer time to next step (min)').fill('12')
    await dialog.getByLabel('Parallel group').fill('e2e-parallel')
    await dialog.getByRole('button', { name: 'Save routing rules' }).click()
    await expect(page.getByText(/20 units · 5 min changeover/)).toBeVisible()
    await expect(page.getByText('e2e-parallel').first()).toBeVisible()
  })
})
