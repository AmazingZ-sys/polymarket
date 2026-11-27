import { expect, test, type Locator } from '@playwright/test'

async function typeValue(field: Locator, value: string) {
  await field.click()
  await field.press('ControlOrMeta+a')
  await field.pressSequentially(value)
  await expect(field).toHaveValue(value)
}

async function selectByKeyboard(field: Locator, value: string) {
  await field.pressSequentially(value)
  await expect(field).toHaveValue(value)
}

// Must point to a disposable, migrated database and its local management API.
test.skip(
  process.env.PQ_TEST_ISOLATED_STACK !== '1',
  'Requires an isolated PostgreSQL/Redis stack',
)
test('real stack: sign in, create a paper account, freeze configuration, audit and archive', async ({
  page,
}) => {
  const password = process.env.PQ_TEST_ADMIN_PASSWORD
  if (!password)
    throw Error('Set PQ_TEST_ADMIN_PASSWORD for the isolated test account')
  await page.goto('/')
  if (process.env.PQ_TEST_SCREENSHOTS)
    await page.screenshot({
      path: process.env.PQ_TEST_SCREENSHOTS + '/login.png',
    })
  await page
    .getByRole('textbox', { name: '用户名' })
    .fill(process.env.PQ_TEST_ADMIN_USERNAME || 'admin')
  await page.locator('input[name=password]').fill(password)
  const dashboardResponse = page.waitForResponse(
    (r) => new URL(r.url()).pathname === '/api/v1/dashboard',
  )
  await page.getByRole('button', { name: '登录终端' }).click()
  expect((await dashboardResponse).status()).toBe(200)
  await expect(page.getByRole('heading', { name: '研究总览' })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  if (process.env.PQ_TEST_SCREENSHOTS)
    await page.screenshot({
      path: process.env.PQ_TEST_SCREENSHOTS + '/dashboard.png',
    })
  await page.getByRole('link', { name: '管理模拟盘' }).click()
  await page.getByRole('button', { name: '创建模拟盘' }).click()
  const form = page.getByRole('dialog', { name: '创建模拟盘' })
  const name = 'TEST-ONLY-browser-' + Date.now()
  await expect(
    form.getByRole('textbox', { name: '名称', exact: true }),
  ).toBeFocused()
  await page.keyboard.type(name)
  const balance = '1234.50000001'
  const money = form.getByLabel('初始虚拟资金')
  for (const [amount, valid] of [
    ['0', false],
    ['0.000000001', false],
    ['0.00000001', true],
    ['1000000000000.00000000', true],
    ['1000000000000.00000001', false],
  ] as const) {
    await typeValue(money, amount)
    expect(await money.evaluate((el) => (el as HTMLInputElement).validity.valid)).toBe(valid)
  }
  await typeValue(money, balance)
  await selectByKeyboard(form.getByLabel('运行模式'), 'SHADOW')
  await selectByKeyboard(form.getByLabel('研究策略'), 'S02')
  await selectByKeyboard(form.getByLabel('策略版本'), '1.0.0')
  await form.getByLabel('撮合模型').selectOption('stress-v1')
  await form.getByRole('checkbox', { name: 'ETH', exact: true }).press('Space')
  await form.getByRole('checkbox', { name: '5m', exact: true }).press('Space')
  await form.getByText('风险与实验参数', { exact: true }).click()
  const risk = form.getByLabel('单笔风险上限')
  await typeValue(risk, '0.0050000000000000001')
  await form.getByRole('button', { name: '核对配置' }).click()
  await expect(form.getByRole('alert')).toContainText('单笔风险上限')
  await typeValue(risk, '0.004123456789123456789')
  await typeValue(form.getByLabel('单日亏损上限'), '.011')
  await typeValue(form.getByLabel('最大回撤'), '0.0300')
  await typeValue(form.getByLabel('尾盘研究窗口'), '90')
  await typeValue(form.getByLabel('费用压力倍数'), '1.501')
  await typeValue(form.getByLabel('延迟压力倍数'), '2.105')
  await typeValue(form.getByLabel('最小净价差'), '0.0000005123456789')
  await selectByKeyboard(
    form.getByRole('combobox', { name: 'S01', exact: true }),
    '1.0.0',
  )
  await form.getByRole('button', { name: '核对配置' }).click()
  const created = page.waitForResponse(
    (r) =>
      r.request().method() === 'POST' &&
      new URL(r.url()).pathname === '/api/v1/paper-accounts',
  )
  await form.getByRole('button', { name: '确认提交' }).click()
  const response = await created
  expect(response.ok()).toBe(true)
  const original = (await response.json()).data
  expect(original).toMatchObject({
    initial_balance: balance,
    mode: 'SHADOW',
    strategy_id: 'S02',
    strategy_version: '1.0.0',
    config: {
      assets: ['BTC'], timeframes: [15], simulation_model: 'stress-v1',
      risk_per_trade: '0.004123456789123456789', daily_loss: '0.011',
      max_drawdown: '0.03', minimum_edge: '0.0000005123456789', tail_seconds: 90,
      cost_multiplier: 1.501, latency_multiplier: 2.105,
      strategies: [{ strategy_id: 'S01', strategy_version: '1.0.0' }],
    },
  })
  await expect(page.getByRole('heading', { name })).toBeVisible()
  await expect(page.locator('.account-bar .tag')).toHaveText('CREATED')
  await expect(page.locator('.table-wrap tbody tr').first()).toBeVisible()
  await page.getByRole('button', { name: '启动', exact: true }).press('Enter')
  await page.getByRole('button', { name: '取消', exact: true }).press('Enter')
  await expect(page.locator('.account-bar .tag')).toHaveText('CREATED')
  for (const [action, state] of [
    ['启动', 'STARTING|RUNNING'],
    ['暂停', 'PAUSED'],
    ['恢复', 'STARTING|RUNNING'],
    ['停止', 'STOPPED'],
    ['归档', 'ARCHIVED'],
  ]) {
    await page.getByRole('button', { name: action, exact: true }).press('Enter')
    await page
      .getByRole('button', { name: '确认' + action, exact: true })
      .press('Enter')
    await expect(page.locator('.account-bar .tag')).toHaveText(
      new RegExp(state),
    )
  }
  await page.getByRole('button', { name: '运行历史' }).click()
  await expect(page.locator('.table-wrap tbody tr').first()).toBeVisible()
  await page.getByRole('button', { name: '克隆', exact: true }).press('Enter')
  const cloned = page.waitForResponse(
    (r) => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith('/clone'),
  )
  await page.getByRole('button', { name: '确认克隆' }).press('Enter')
  const cloneResponse = await cloned
  expect(cloneResponse.ok()).toBe(true)
  const clone = (await cloneResponse.json()).data
  expect(clone.id).not.toBe(original.id)
  expect(clone).toMatchObject({
    initial_balance: balance, available_balance: balance,
    config_hash: original.config_hash, config: original.config, status: 'CREATED',
  })
  await expect(page).toHaveURL(new RegExp('/paper-accounts/' + clone.id + '$'))
  await page.getByRole('link', { name: '策略版本', exact: true }).click()
  await page.getByRole('button', { name: '查看记录' }).first().click()
  await page.getByRole('button', { name: '人工评审晋级' }).click()
  await expect(
    page.getByText('此策略版本暂无符合晋级条件的评估记录。'),
  ).toBeVisible()
  await page.getByRole('button', { name: '关闭表单' }).click()
  await page.getByRole('link', { name: '策略评估', exact: true }).click()
  await page.getByRole('button', { name: '登记评估' }).click()
  await expect(
    page.getByText('需要预注册实验和两份已完成的回放报告。'),
  ).toBeVisible()
  await page.getByRole('button', { name: '关闭表单' }).click()
  await page.getByRole('link', { name: '亏损归因', exact: true }).click()
  await page.getByLabel('最小亏损（USD）').fill('0.01')
  await page.getByLabel('成交时行情质量').selectOption('UNKNOWN')
  const filtered = page.waitForResponse(
    (r) =>
      r.url().includes('/attributions?') && r.url().includes('min_loss=0.01'),
  )
  await page.getByRole('button', { name: '应用筛选' }).click()
  expect((await filtered).status()).toBe(200)
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page.getByRole('heading', { name: '登录工作区' })).toBeVisible()
})
