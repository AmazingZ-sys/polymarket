import { expect, test } from '@playwright/test'

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
  await form.getByRole('textbox', { name: '名称', exact: true }).fill(name)
  await form.getByText('风险与实验参数', { exact: true }).click()
  await form.getByLabel('费用压力倍数').fill('1.5')
  await form.getByLabel('延迟压力倍数').fill('2')
  await form
    .getByRole('combobox', { name: 'S01', exact: true })
    .selectOption({ index: 1 })
  await form.getByRole('button', { name: '核对配置' }).click()
  await form.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByRole('heading', { name })).toBeVisible()
  await expect(page.locator('.account-bar .tag')).toHaveText('CREATED')
  await expect(page.locator('.table-wrap tbody tr').first()).toBeVisible()
  for (const [action, state] of [
    ['启动', 'STARTING|RUNNING'],
    ['暂停', 'PAUSED'],
    ['恢复', 'STARTING|RUNNING'],
    ['停止', 'STOPPED'],
    ['归档', 'ARCHIVED'],
  ]) {
    await page.getByRole('button', { name: action, exact: true }).click()
    await page
      .getByRole('button', { name: '确认' + action, exact: true })
      .click()
    await expect(page.locator('.account-bar .tag')).toHaveText(
      new RegExp(state),
    )
  }
  await page.getByRole('button', { name: '运行历史' }).click()
  await expect(page.locator('.table-wrap tbody tr').first()).toBeVisible()
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
