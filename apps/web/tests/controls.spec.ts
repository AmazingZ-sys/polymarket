import { expect, test, type Locator, type Page } from '@playwright/test'

async function replaceByKeyboard(control: Locator, text: string) {
  await control.click()
  await control.press('ControlOrMeta+a')
  await control.press('Backspace')
  await control.pressSequentially(text)
}
async function fixture(page: Page, signed = true) {
  const urls: URL[] = []
  const credentials: object[] = []
  await page.route('**/api/v1/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    urls.push(url)
    const name = url.pathname.slice('/api/v1/'.length)
    const identity = { username: 'audit', role: 'ADMIN' }
    if (name === 'auth/me')
      return route.fulfill(signed ? { json: { data: identity } } : { status: 401, json: { error: { message: '登录后访问' } } })
    if (name === 'auth/login') {
      const body = req.postDataJSON()
      credentials.push(body)
      signed = body.username === 'audit' && body.password === 'test-only-123'
      return route.fulfill(signed ? { json: { data: identity } } : { status: 401, json: { error: { message: '测试登录失败' } } })
    }
    if (name === 'events')
      return route.fulfill({ contentType: 'text/event-stream', body: 'retry: 60000\n\n' })
    const total = name === 'markets' ? 61 : 0
    const offset = Number(url.searchParams.get('offset') || 0)
    const limit = Number(url.searchParams.get('limit') || 20)
    const data = Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, i) => ({
      market_id: String(i + offset), title: '隔离市场 ' + (i + offset),
    }))
    await route.fulfill({ json: { data, meta: { total, limit, offset } } })
  })
  return { credentials, urls }
}
function latest(urls: URL[], resource: string) {
  return urls.filter((url) => url.pathname.endsWith('/' + resource)).at(-1)?.searchParams
}
test('keyboard login, password visibility, retry, and settings persistence', async ({ page }) => {
  const { credentials } = await fixture(page, false)
  await page.goto('/settings')
  const username = page.getByLabel('用户名')
  const password = page.getByLabel('密码', { exact: true })
  await replaceByKeyboard(username, 'audit')
  await username.press('Tab')
  await expect(password).toBeFocused()
  await password.pressSequentially('incorrect')
  await page.getByRole('button', { name: '显示密码' }).click()
  await expect(password).toHaveAttribute('type', 'text')
  await expect(password).toHaveValue('incorrect')
  await page.getByRole('button', { name: '隐藏密码' }).click()
  await password.press('Enter')
  await expect(page.getByRole('alert')).toHaveText('测试登录失败')
  await replaceByKeyboard(password, 'test-only-123')
  await page.getByRole('button', { name: '登录终端' }).click()
  await expect(page.getByRole('heading', { name: '显示偏好' })).toBeVisible()
  expect(credentials).toEqual([
    { username: 'audit', password: 'incorrect' },
    { username: 'audit', password: 'test-only-123' },
  ])
  const theme = page.getByLabel('显示主题')
  await theme.selectOption('light')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await page.getByLabel('时间显示').selectOption('America/New_York')
  await page.reload()
  await expect(page.getByLabel('时间显示')).toHaveValue('America/New_York')
  await expect(page.getByLabel('显示主题')).toHaveValue('light')
})
test('market and account filters, search, pages and export honor selected controls', async ({ page }) => {
  const { urls } = await fixture(page)
  await page.goto('/markets')
  await page.getByLabel('筛选资产').selectOption('ETH')
  await page.getByLabel('筛选周期').selectOption('15')
  await expect.poll(() => latest(urls, 'markets')?.get('timeframe')).toBe('15')
  expect(latest(urls, 'markets')?.get('asset')).toBe('ETH')
  await page.getByRole('button', { name: '下一页' }).click()
  await expect.poll(() => latest(urls, 'markets')?.get('offset')).toBe('20')
  await page.getByRole('button', { name: '上一页' }).click()
  await expect.poll(() => latest(urls, 'markets')?.get('offset')).toBe('0')
  await page.getByLabel('每页数量').selectOption('50')
  await page.getByRole('button', { name: '下一页' }).click()
  await expect.poll(() => latest(urls, 'markets')?.get('offset')).toBe('50')
  const search = page.getByLabel('搜索记录')
  await replaceByKeyboard(search, '  window+15  ')
  await search.press('Enter')
  await expect.poll(() => latest(urls, 'markets')?.get('q')).toBe('window+15')
  expect(latest(urls, 'markets')?.get('offset')).toBe('0')
  await page.getByRole('button', { name: '刷新记录' }).click()
  await page.getByLabel('导出格式').selectOption('csv')
  const saved = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出', exact: true }).click()
  expect((await saved).suggestedFilename()).toBe('markets.csv')
  expect(latest(urls, 'markets')?.get('q')).toBe('window+15')
  expect(latest(urls, 'markets')?.get('direction')).toBe('asc')
  await page.goto('/paper-accounts')
  await page.getByLabel('筛选状态').selectOption('PAUSED')
  await expect.poll(() => latest(urls, 'paper-accounts')?.get('status')).toBe('PAUSED')
})
test('attribution filters preserve exact decimals, UTC and independent optional bounds', async ({ page }) => {
  const { urls } = await fixture(page)
  await page.goto('/attributions')
  const min = page.getByLabel('最小亏损（USD）')
  const max = page.getByLabel('最大亏损（USD）')
  const from = page.getByLabel('归因起始（UTC）')
  const to = page.getByLabel('归因截止（UTC）')
  const apply = page.getByRole('button', { name: '应用筛选' })
  for (const invalid of ['-1', '1e-8', '0.000000001']) {
    await replaceByKeyboard(min, invalid)
    expect(await min.evaluate(el => (el as HTMLInputElement).validity.patternMismatch)).toBe(true)
    await apply.click()
  }
  expect(latest(urls, 'attributions')?.has('min_loss')).toBe(false)
  await replaceByKeyboard(min, '0.00000001')
  await replaceByKeyboard(max, '9999999999999.00000001')
  await from.fill('2026-01-01T02:03:04.321')
  await to.fill('2025-01-01T02:03:04.321')
  await apply.click()
  await expect(page.getByRole('alert')).toHaveText('归因截止时间须晚于起始时间。')
  await to.fill('2026-02-01T02:03:04.987')
  await page.getByLabel('发出信号的策略').selectOption('S03')
  await page.getByLabel('成交时行情质量').selectOption('FLAGGED')
  await apply.click()
  await expect.poll(() => latest(urls, 'attributions')?.get('min_loss')).toBe('0.00000001')
  const params = latest(urls, 'attributions')!
  expect(Object.fromEntries(params)).toMatchObject({
    max_loss: '9999999999999.00000001', start_time: '2026-01-01T02:03:04.321Z',
    end_time: '2026-02-01T02:03:04.987Z', strategy_id: 'S03', data_quality: 'FLAGGED',
  })
  await replaceByKeyboard(min, '9999999999999.00000002')
  await apply.click()
  await expect(page.getByRole('alert')).toHaveText('最大亏损须大于或等于最小亏损。')
  await replaceByKeyboard(min, '0')
  await replaceByKeyboard(max, '')
  await from.fill('')
  await apply.click()
  await expect.poll(() => latest(urls, 'attributions')?.get('min_loss')).toBe('0')
  expect(latest(urls, 'attributions')?.has('max_loss')).toBe(false)
  expect(latest(urls, 'attributions')?.has('start_time')).toBe(false)
  for (const [label, key, value] of [
    ['筛选账户 ID', 'account_id', '10000000-0000-4000-a000-000000000001'],
    ['筛选市场 ID', 'market_id', 'fixture-market'],
    ['筛选原因', 'reason', 'TEST REASON'],
  ]) {
    const input = page.getByLabel(label)
    await replaceByKeyboard(input, '  ' + value + '  ')
    await input.press('Enter')
    await expect.poll(() => latest(urls, 'attributions')?.get(key)).toBe(value)
    await replaceByKeyboard(input, '')
    await input.press('Enter')
    await expect.poll(() => latest(urls, 'attributions')?.has(key)).toBe(false)
  }
})
