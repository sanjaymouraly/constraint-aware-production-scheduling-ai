/* Real end-to-end browser drive of the running application.
 *
 * Usage:  cd frontend && node scripts/capture_screenshots.mjs [baseUrl]
 *
 * The script logs in with the bootstrap administrator, exercises the real
 * workflow (baseline run -> optimised run -> Gantt -> disruption -> AI) and
 * writes PNG screenshots into docs/images/.  Nothing is mocked: every screen
 * is rendered by the application itself and every number comes from the API.
 */
import { chromium } from 'playwright'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'

const BASE = process.argv[2] || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.cwd(), '..', 'docs/images')
const USERNAME = process.env.DEMO_USER || 'admin'
const PASSWORD = process.env.DEMO_PASSWORD || 'admin'

async function main() {
  await mkdir(OUT, { recursive: true })
  const browser = await chromium.launch()
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 1,
  })
  const page = await context.newPage()
  const errors = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(String(error)))

  const shot = async (name) => {
    await page.waitForTimeout(600)
    await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: false })
    console.log(`captured ${name}.png`)
  }

  const runEngine = async (buttonName) => {
    const [response] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes('/api/scheduling/run') && r.request().method() === 'POST',
        { timeout: 300000 },
      ),
      page.getByRole('button', { name: buttonName }).click(),
    ])
    await response.json()
    await page.waitForTimeout(1500)
  }

  // ---------------------------------------------------------------- login
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await shot('01-login')

  await page.fill('#username', USERNAME)
  await page.fill('#password', PASSWORD)
  await page.click('button[type=submit]')
  await page.waitForURL(`${BASE}/`, { timeout: 20000 })
  await page.waitForLoadState('networkidle')

  // ------------------------------------------------------------ optimizer
  await page.click('a[href="/optimizer"]')
  await page.waitForURL('**/optimizer')
  await page.waitForLoadState('networkidle')
  await shot('03-optimizer-configuration')

  await page.getByRole('button', { name: 'FCFS baseline' }).click()
  await runEngine(/Run FCFS/)
  await shot('04-optimizer-fcfs-baseline')

  await page.getByRole('button', { name: 'CP-SAT optimizer' }).click()
  await runEngine(/Run CP-SAT/)
  await shot('05-optimizer-cpsat-comparison')

  // ----------------------------------------------------------- dashboard
  await page.click('a[href="/"]')
  await page.waitForURL(`${BASE}/`)
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(800)
  await shot('02-dashboard-overview')

  // ---------------------------------------------------------------- gantt
  await page.click('a[href="/schedule"]')
  await page.waitForURL('**/schedule')
  await page.waitForLoadState('networkidle')
  await shot('06-schedule-versions')

  await page.getByRole('button', { name: 'Open' }).first().click()
  await page.waitForSelector('text=Gantt chart', { timeout: 20000 })
  await page.waitForTimeout(1200)
  await shot('07-gantt-chart')

  // ------------------------------------------------------------ analytics
  await page.click('a[href="/analytics"]')
  await page.waitForURL('**/analytics')
  await page.waitForLoadState('networkidle')
  const compareButton = page.getByRole('button', { name: 'Compare', exact: true })
  if (await compareButton.count()) {
    await compareButton.first().click()
    await page.waitForTimeout(1500)
  }
  await shot('08-analytics-comparison')

  // ------------------------------------------------------------ scenarios
  await page.click('a[href="/scenarios"]')
  await page.waitForURL('**/scenarios')
  await page.waitForLoadState('networkidle')

  // create a scenario from the newest plan version
  await page.getByRole('button', { name: 'New scenario' }).click()
  await page.waitForSelector('div[role=dialog]', { timeout: 10000 })
  const planSelect = page.locator('div[role=dialog] select').first()
  await planSelect.waitFor({ state: 'visible' })
  const planOptions = await planSelect.locator('option').all()
  if (planOptions.length < 2) throw new Error('No plan versions available for the scenario')
  await planSelect.selectOption(await planOptions[planOptions.length - 1].getAttribute('value'))
  await page.fill('div[role=dialog] input.input', 'CNC-3 breakdown on Monday')
  await page.fill(
    'div[role=dialog] textarea',
    'The main CNC machine fails for eight hours on the first day of the horizon.',
  )
  await page.getByRole('button', { name: 'Create scenario' }).click()
  await page.waitForTimeout(1500)

  // add a machine breakdown disruption
  await page.getByRole('button', { name: 'Add disruption' }).click()
  await page.waitForSelector('div[role=dialog]', { timeout: 10000 })
  const machineSelect = page.getByLabel(/^Machine$/)
  const firstMachineOption = machineSelect.locator('option').nth(1)
  await firstMachineOption.waitFor({ state: 'attached', timeout: 20000 })
  const machineId = await firstMachineOption.getAttribute('value')
  if (!machineId) throw new Error('No machine options are available for the breakdown scenario')
  await machineSelect.selectOption(machineId)
  await page.getByRole('button', { name: 'Apply disruption' }).click()
  await page.waitForTimeout(1500)

  // re-plan the scenario
  const [simulateResponse] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/simulate') && r.request().method() === 'POST',
      { timeout: 300000 },
    ),
    page.getByRole('button', { name: 'Re-plan scenario' }).click(),
  ])
  await simulateResponse.json()
  await page.waitForTimeout(1500)
  await shot('09-scenarios-disruption-simulation')

  // ------------------------------------------------------------ workorders
  await page.click('a[href="/work-orders"]')
  await page.waitForURL('**/work-orders')
  await page.waitForLoadState('networkidle')
  await shot('10-work-orders')

  // ------------------------------------------------------------- resources
  await page.click('a[href="/resources"]')
  await page.waitForURL('**/resources')
  await page.waitForLoadState('networkidle')
  await shot('11-resources-machines')

  // ------------------------------------------------------------- assistant
  await page.click('a[href="/assistant"]')
  await page.waitForURL('**/assistant')
  await page.waitForLoadState('networkidle')
  await page.fill('textarea', 'Which orders are late, what causes the delay and what should the planner do?')
  const [explainResponse] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/api/ai/explain') && r.request().method() === 'POST',
      { timeout: 120000 },
    ),
    page.getByRole('button', { name: 'Ask the assistant' }).click(),
  ])
  await explainResponse.json()
  await page.waitForTimeout(1500)
  await shot('12-ai-assistant')

  // ------------------------------------------------------------ ai settings
  await page.click('a[href="/settings/ai"]')
  await page.waitForURL('**/settings/ai')
  await page.waitForLoadState('networkidle')
  await shot('13-ai-settings')

  await browser.close()
  if (errors.length) {
    console.log('BROWSER CONSOLE ERRORS:')
    for (const error of errors) console.log(' -', error)
    process.exitCode = 2
  } else {
    console.log('No browser console errors.')
  }
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
