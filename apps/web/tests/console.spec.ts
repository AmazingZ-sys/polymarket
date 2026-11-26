import { expect, test, type Page } from '@playwright/test'

const identity = { username: 'test-researcher', role: 'ADMIN' }
async function fixture(page: Page, role = 'ADMIN') {
  let signed = false
  const account = {
    id: '20000000-0000-4000-a000-000000000001',
    name: '隔离测试账户',
    strategy_id: 'S00',
    strategy_version: '1.0.0',
    status: 'CREATED',
    equity: '1000.00000000',
    initial_balance: '1000',
    available_balance: '1000',
    frozen_balance: '0',
    realized_pnl: '0',
    mode: 'PAPER',
    config: { assets: ['BTC', 'ETH'], timeframes: [5, 15] },
    config_hash: 'test-only',
  }
  const commands: string[] = []
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.slice(
      '/api/v1/'.length,
    )
    const send = (data: unknown, status = 200) =>
      route.fulfill({ status, json: data })
    if (path === 'auth/me')
      return signed
        ? send({ data: { ...identity, role } })
        : send(
            { error: { code: 'UNAUTHORIZED', message: '测试要求登录' } },
            401,
          )
    if (path === 'auth/login') {
      signed = true
      return send({ data: { ...identity, role } })
    }
    if (path === 'auth/logout') {
      signed = false
      return send({ data: { ok: true } })
    }
    if (path === 'events')
      return route.fulfill({
        contentType: 'text/event-stream',
        body: 'retry: 60000\n\n',
      })
    if (path === 'dashboard')
      return send({
        data: {
          accounts: 0,
          running_accounts: 0,
          fills: 0,
          markets: 0,
          series: [],
          sources: [],
          events: [],
        },
      })
    if (route.request().method() === 'POST') {
      expect(route.request().headers()['idempotency-key']).toBeTruthy()
      commands.push(path)
      if (path === 'paper-accounts') {
        const body = route.request().postDataJSON()
        expect(body.initial_balance).toBe('1000')
        expect(body.config.assets).toEqual(['BTC', 'ETH'])
        account.name = body.name
        return send({ data: account })
      }
      const states: Record<string, string> = {
        start: 'RUNNING',
        pause: 'PAUSED',
        resume: 'RUNNING',
        stop: 'STOPPED',
        archive: 'ARCHIVED',
      }
      const action = path.split('/').at(-1) || ''
      if (states[action]) account.status = states[action]
      return send({ data: account })
    }
    if (path === 'paper-accounts/' + account.id) return send({ data: account })
    const rows =
      path === 'strategy-versions'
        ? [{ strategy_id: 'S00', version: '1.0.0' }]
        : path === 'paper-accounts' && commands.length > 0
          ? [account]
          : []
    return send({
      data: rows,
      meta: { limit: 20, offset: 0, total: rows.length },
    })
  })
  return commands
}
async function signIn(page: Page) {
  await page.goto('/')
  await page.getByRole('textbox', { name: '用户名' }).fill(identity.username)
  await page.locator('input[name=password]').fill('TEST-FIXTURE-ONLY')
  await page.getByRole('button', { name: '登录终端' }).click()
  await expect(page.getByRole('heading', { name: '研究总览' })).toBeVisible()
}
test('isolated fixture: sign in, create and control an independent account', async ({
  page,
}) => {
  const commands = await fixture(page)
  await signIn(page)
  await page.getByRole('link', { name: '管理模拟盘' }).click()
  await page.getByRole('button', { name: '创建模拟盘' }).click()
  const dialog = page.getByRole('dialog', { name: '创建模拟盘' })
  await dialog
    .getByRole('textbox', { name: '名称', exact: true })
    .fill('隔离测试账户')
  await dialog.getByRole('button', { name: '核对配置' }).click()
  expect(commands).toHaveLength(0)
  await dialog.getByRole('button', { name: '确认提交' }).click()
  await expect(
    page.getByRole('heading', { name: '隔离测试账户' }),
  ).toBeVisible()
  for (const [label, status] of [
    ['启动', 'RUNNING'],
    ['暂停', 'PAUSED'],
    ['恢复', 'RUNNING'],
    ['停止', 'STOPPED'],
    ['归档', 'ARCHIVED'],
  ]) {
    await page.getByRole('button', { name: label, exact: true }).click()
    await page
      .getByRole('button', { name: '确认' + label, exact: true })
      .click()
    await expect(page.locator('.account-bar .tag')).toHaveText(status)
  }
  expect(commands).toHaveLength(6)
  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page.getByRole('heading', { name: '登录工作区' })).toBeVisible()
})
test('isolated fixture: narrow viewport, read-only role and empty sources', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await fixture(page, 'VIEWER')
  await signIn(page)
  await expect(page.getByText('等待模拟权益快照')).toBeVisible()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(391)
  await page.getByRole('button', { name: '打开导航' }).click()
  await page.getByRole('link', { name: '模拟盘', exact: true }).click()
  await expect(page.getByRole('button', { name: '创建模拟盘' })).toHaveCount(0)
  await page.getByRole('button', { name: '切换主题' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
})
