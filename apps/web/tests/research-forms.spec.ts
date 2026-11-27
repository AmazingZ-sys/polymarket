import { expect, test, type Locator, type Page } from '@playwright/test'

const ids = Array.from({ length: 10 }, (_, index) => '30000000-0000-4000-a000-' + String(index + 1).padStart(12, '0'))
const [left, right, experiment, baseRun, stressRun, evaluation, proposal, pending] = ids
const versions = [
  { strategy_id: 'S00', version: '1.0.0', role: 'CHAMPION' },
  { strategy_id: 'S01', version: '1.0.0', role: 'CHAMPION' },
  { strategy_id: 'S01', version: '1.1.0', role: 'CHALLENGER' },
]
const records: Record<string, Record<string, unknown>[]> = {
  'paper-accounts': [
    { id: left, name: 'Left test', mode: 'PAPER' },
    { id: right, name: 'Right test', mode: 'PAPER' },
    { id: ids[8], name: 'Shadow test', mode: 'SHADOW' },
  ],
  experiments: [{ id: experiment, name: 'Test experiment', account_ids: [left, right] }],
  'strategy-versions': versions,
  backtests: [{ id: pending, status: 'QUEUED' }, ...[baseRun, stressRun].map(id => ({ id, status: 'COMPLETED' }))],
  evaluations: [{ id: evaluation, strategy_id: 'S01', strategy_version: '1.1.0', status: 'VALIDATION_PASSED' }],
  improvements: [{ id: proposal, status: 'PENDING', problem: 'Test proposal' }],
  jobs: [{ name: 'ledger_check' }],
}
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const key = url.pathname.slice('/api/v1/'.length)
    if (key === 'auth/me') return route.fulfill({ json: { data: { username: 'test', role: 'ADMIN' } } })
    if (key === 'events') return route.fulfill({ contentType: 'text/event-stream', body: 'retry: 60000\n\n' })
    if (request.method() === 'POST') return route.fulfill({ json: { data: {} } })
    let rows = records[key] ?? []
    for (const filter of ['mode', 'strategy_id', 'status']) {
      const value = url.searchParams.get(filter)
      if (value) rows = rows.filter(row => row[filter] === value)
    }
    return route.fulfill({ json: { data: rows, meta: { limit: 20, offset: 0, total: rows.length } } })
  })
})

async function open(page: Page, path: string, button: string, row?: string) {
  await page.goto('/' + path)
  if (path === 'system') await page.getByRole('button', { name: '定时任务' }).click()
  if (row) await page.getByRole('row').filter({ hasText: row }).getByRole('button', { name: '查看记录' }).click()
  await page.getByRole('button', { name: button, exact: true }).click()
  const form = page.getByRole('dialog').filter({ has: page.locator('form') })
  await expect(form.getByRole('button', { name: '核对配置' })).toBeEnabled()
  return form
}
async function edit(form: Locator, label: string, text: string) {
  const control = form.getByLabel(label, { exact: true })
  await control.click()
  await expect(control).toBeFocused()
  await control.press('ControlOrMeta+a')
  await control.pressSequentially(text)
  await expect(control).toHaveValue(text)
}
async function choose(form: Locator, label: string, value: string) {
  const control = form.getByRole('combobox', { name: new RegExp('^' + label) })
  await control.click()
  await expect(control).toBeFocused()
  await control.selectOption(value)
  await control.press('Tab')
  await expect(control).toHaveValue(value)
}
async function submit(page: Page, form: Locator, endpoint: string, expected: Record<string, unknown>) {
  await form.getByRole('button', { name: '核对配置' }).click()
  const confirmation = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: '确认操作' }) })
  if (Object.keys(expected).length) {
    expect(JSON.parse(await confirmation.locator('pre').innerText())).toEqual(expected)
  }
  const sent = page.waitForRequest(request => request.method() === 'POST'
    && new URL(request.url()).pathname === '/api/v1/' + endpoint)
  await confirmation.getByRole('button', { name: '确认提交' }).press('Enter')
  const request = await sent
  expect(request.postDataJSON()).toEqual(expected)
  expect(request.headers()['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/)
  await expect(confirmation).toHaveCount(0)
}

test('experiment and strategy version fields reach the matching command', async ({ page }) => {
  let form = await open(page, 'experiments', '新建实验')
  await edit(form, '名称', 'Paired test')
  await expect(form.getByText('Shadow test')).toHaveCount(0)
  await form.getByLabel('Left test').click()
  await form.getByLabel('Right test').press('Space')
  await edit(form, '研究假设', 'Compare two accounts')
  await edit(form, '验收标准', 'Compare the same interval')
  await submit(page, form, 'experiments', { name: 'Paired test', hypothesis: 'Compare two accounts',
    criteria: 'Compare the same interval', account_ids: [left, right] })

  form = await open(page, 'strategy-versions', '创建版本')
  await choose(form, '研究策略', 'S01')
  await choose(form, '基础版本', '1.1.0')
  await edit(form, '新版本号', '1.2.1')
  await edit(form, '策略参数（JSON）', '{"tail_seconds":35}')
  await edit(form, '变更说明', 'Retest a shorter tail')
  await submit(page, form, 'strategy-versions', { strategy_id: 'S01', version: '1.2.1',
    base_version: '1.1.0', config: { tail_seconds: 35 }, description: 'Retest a shorter tail' })
})

test('report UTC dates and paper account selection reach the command', async ({ page }) => {
  const form = await open(page, 'reports', '生成报告')
  for (const [label, value] of [['起始时间（UTC）', '2024-04-10T08:15'], ['截止时间（UTC）', '2024-04-10T08:29']]) {
    const input = form.getByLabel(label)
    await input.click()
    await input.fill(value)
    await input.press('ArrowUp')
    await input.press('ArrowDown')
    await expect(input).toHaveValue(value)
  }
  await choose(form, '模拟账户', right)
  await submit(page, form, 'reports', { start_time: '2024-04-10T08:15:00.000Z',
    end_time: '2024-04-10T08:29:00.000Z', account_id: right })
})

test('improvement creation and review submit the selected evidence and decision', async ({ page }) => {
  let form = await open(page, 'improvements', '提交建议')
  await choose(form, '研究策略', 'S01')
  await edit(form, '研究问题', 'Coverage')
  await edit(form, '统计证据', 'Observation log')
  await edit(form, '研究假设', 'Repeat the window')
  await edit(form, '验收标准', 'Two observed cases')
  await submit(page, form, 'improvements', { strategy_id: 'S01', problem: 'Coverage',
    evidence: 'Observation log', hypothesis: 'Repeat the window', acceptance: 'Two observed cases' })
  form = await open(page, 'improvements', '审核建议', 'Test proposal')
  await choose(form, '审核结论', 'REJECTED')
  await edit(form, '依据', 'Insufficient coverage')
  await submit(page, form, 'improvements/' + proposal + '/review', { status: 'REJECTED', reason: 'Insufficient coverage' })
})

test('evaluation cost remains an exact decimal and promotion selects an eligible evaluation', async ({ page }) => {
  let form = await open(page, 'evaluations', '登记评估')
  await choose(form, '预注册实验', experiment)
  await choose(form, '受评模拟账户', right)
  await choose(form, '样本外基准回放', baseRun)
  await choose(form, '同期压力回放', stressRun)
  const cost = '12500.00000010'
  await edit(form, '研究运行成本（USD）', cost)
  for (const check of ['数据质量', '延迟压力', '极端行情', '多重检验', '样本独立性', '持续运行覆盖'])
    await form.getByLabel(check, { exact: true }).press('Space')
  await form.getByLabel('极端行情', { exact: true }).click()
  await edit(form, '证据与核验依据', 'Isolated research evidence')
  await submit(page, form, 'evaluations', { experiment_id: experiment, account_id: right, backtest_id: baseRun,
    stress_backtest_id: stressRun, cost_usd: cost, checks: { quality: true, delay: true, extreme: false,
      multiple_testing: true, independent_sample: true, continuous_coverage: true }, evidence: 'Isolated research evidence' })
  form = await open(page, 'strategy-versions', '人工评审晋级', '1.1.0')
  await choose(form, '已通过的评估', evaluation)
  await edit(form, '人工晋级理由', 'Reviewed evaluation')
  await submit(page, form, 'strategy-versions/S01/1.1.0/promote', { evaluation_id: evaluation, reason: 'Reviewed evaluation' })
})

test('global halt, manual release, backtest cancellation and job trigger use their scoped commands', async ({ page }) => {
  let form = await open(page, 'risk', '全局暂停')
  await submit(page, form, 'risk/halt', {})
  form = await open(page, 'risk', '解除全局暂停')
  await edit(form, '人工恢复依据', 'Sources recovered')
  await submit(page, form, 'risk/release', { reason: 'Sources recovered' })
  form = await open(page, 'backtests', '取消回测', 'QUEUED')
  await submit(page, form, 'backtests/' + pending + '/cancel', {})
  form = await open(page, 'system', '手动触发', 'ledger_check')
  await submit(page, form, 'jobs/ledger_check/trigger', {})
})
