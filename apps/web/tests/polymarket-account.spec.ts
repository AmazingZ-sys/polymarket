import { expect, test, type Locator, type Page } from '@playwright/test'

const id = '10000000-0000-4000-a000-000000000001'
const wallet = '0x' + '1'.repeat(40)
const paper = { id, name: '测试模拟盘', mode: 'PAPER', strategy_id: 'S00', strategy_version: '1.0.0',
  config: { strategies: [{ strategy_id: 'S01', strategy_version: '1.0.0' }, { strategy_id: 'S01', strategy_version: '1.1.0' }] } }
const config = { wallet_address: wallet, paper_account_id: id, strategy_id: 'S01', strategy_version: '1.1.0',
  max_order_usd: '1.00000001', max_total_usd: '9000000000000.00000001' }
async function fixture(page: Page, role = 'ADMIN') {
  const state = { config: null as typeof config | null, revision: '11', enabled: false, configured_enabled: false, enabled_at: null, executor_ready: false, executor_detail_code: 'FUNDS_UNAVAILABLE', executor_checked_at: '2026-11-27T01:15:04Z',
    accounting: { confirmed_principal_usd: '12.00000000', reserved_principal_usd: '3.50000000', fees: [{ currency: 'USDC', amount: '0.12000000' }], pending_fee_legs: 1 },
    recent_attempts: [{ signal_id: 'sig-1', state: 'UNKNOWN', created_at: '2026-11-27T01:00:00Z', updated_at: '2026-11-27T01:01:00Z', up_amount: '2.5', down_amount: '1.5', up_principal: null, down_principal: '1.50000000', up_fee: null, down_fee: '0.01000000', up_fee_currency: null, down_fee_currency: 'USDC', up_order_id: null, down_order_id: 'ord-1' }],
    geoblock: { status: 'BLOCKED', country: 'US', region: 'IA', checked_at: '2026-11-27T01:15:00.000Z' },
    blockers: ['当前出口地域受限。'] }
  const controls = { down: false, refuseEnable: false }
  const reads: string[] = []
  const writes: { path: string; body: unknown }[] = []
  await page.route('**/api/v1/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname.replace('/api/v1/', '')
    const respond = (data: unknown) => route.fulfill({ json: { data } })
    if (path === 'auth/me') return respond({ username: 'test-user', role })
    if (path === 'events') return route.fulfill({ contentType: 'text/event-stream', body: 'retry: 60000\n\n' })
    if (req.method() === 'GET') {
      reads.push(path)
      if (controls.down && path === 'polymarket-account')
        return route.fulfill({ status: 503, json: { error: { message: '配置服务暂时不可用' } } })
      if (path === 'paper-accounts') {
        expect(url.searchParams.get('mode')).toBe('PAPER')
        return respond([paper])
      }
      return respond(path === 'polymarket-account' ? state : [])
    }
    expect(req.headers()['idempotency-key']).toBeTruthy()
    const body = req.postDataJSON()
    writes.push({ path, body })
    if (path === 'polymarket-account/config') {
      state.config = body
      state.revision = String(BigInt(state.revision) + 1n)
      state.enabled = false
      state.configured_enabled = false
    }
    if (path === 'polymarket-account/disable') {
      state.enabled = false
      state.configured_enabled = false
    }
    if (path === 'polymarket-account/enable' && controls.refuseEnable) {
      state.geoblock.status = 'BLOCKED'
      state.blockers = ['官方服务报告出口地域受限。']
      return route.fulfill({ status: 409, json: { error: { message: '地域复查未通过' } } })
    }
    return respond(state)
  })
  return { state, controls, reads, writes }
}
async function typeValue(control: Locator, text: string) {
  await control.click()
  await control.press('ControlOrMeta+a')
  await control.press('Backspace')
  await control.pressSequentially(text)
}
test('admin saves exact limits and linked strategy while blocked', async ({ page }) => {
  const { writes } = await fixture(page)
  await page.goto('/polymarket-account')
  await expect(page.getByRole('link', { name: 'Polymarket 账户' })).toBeVisible()
  const address = page.getByLabel('钱包公开地址')
  const single = page.getByLabel('单笔本金上限（USD）')
  const total = page.getByLabel('累计本金预算（USD）')
  const save = page.getByRole('button', { name: '保存配置', exact: true })
  await typeValue(address, '0x' + '0'.repeat(40))
  expect(await address.evaluate((el) => (el as HTMLInputElement).validity.patternMismatch)).toBe(true)
  await typeValue(address, wallet)
  await address.press('Tab')
  await expect(page.getByLabel('关联 PAPER 账户')).toBeFocused()
  await page.getByLabel('关联 PAPER 账户').selectOption(id)
  await expect(page.getByText('当前执行器仅支持 S01 双腿策略的实时信号。')).toBeVisible()
  await page.getByLabel('策略版本').selectOption('S01@1.0.0')
  await page.getByLabel('策略版本').selectOption('S01@1.1.0')
  await typeValue(single, '9000000000000.00000002')
  await typeValue(total, config.max_total_usd)
  await save.click()
  await expect(page.getByRole('alert')).toContainText('单笔上限须小于或等于累计预算')
  await typeValue(single, '0')
  await save.click()
  expect(writes).toHaveLength(0)
  await typeValue(single, config.max_order_usd)
  await save.click()
  await expect(page.getByRole('status')).toContainText('配置已保存')
  expect(writes).toEqual([{ path: 'polymarket-account/config', body: config }])
  await page.reload()
  await expect(single).toHaveValue(config.max_order_usd)
  await expect(total).toHaveValue(config.max_total_usd)
  await expect(page.getByLabel('策略版本')).toHaveValue('S01@1.1.0')
  await expect(page.getByText('US / IA', { exact: false })).toBeVisible()
  await expect(page.getByText('当前出口地域受限。')).toBeVisible()
  await expect(page.getByText('FUNDS_UNAVAILABLE')).toBeVisible()
  await expect(page.getByText('12.00000000')).toBeVisible()
  const attempt = page.getByRole('article').filter({ hasText: 'sig-1' })
  await expect(attempt).toContainText('UP 本金 待确认')
  await expect(attempt).toContainText('手续费 待确认')
  await expect(attempt).toContainText('手续费 0.01000000 USDC')
  await page.getByRole('button', { name: '检查地域' }).click()
  await expect(page.getByRole('status')).toContainText('地域检查已更新')
  await expect(page.getByRole('button', { name: '启用自动执行' })).toBeDisabled()
})
test('viewer direct navigation does not read admin settings or show its navigation link', async ({ page }) => {
  const { reads, writes } = await fixture(page, 'VIEWER')
  await page.goto('/polymarket-account')
  await expect(page.getByRole('alert')).toContainText('仅限管理员')
  await expect(page.getByRole('link', { name: 'Polymarket 账户' })).toHaveCount(0)
  await expect(page.getByLabel('钱包公开地址')).toHaveCount(0)
  expect(reads).not.toContain('polymarket-account')
  expect(reads).not.toContain('paper-accounts')
  expect(writes).toEqual([])
})
test('unavailable service and unknown region fail closed; enable refreshes the official check', async ({ page }) => {
  const { state, controls, writes } = await fixture(page)
  controls.down = true
  await page.goto('/polymarket-account')
  await expect(page.getByRole('alert')).toContainText('配置服务暂时不可用')
  await expect(page.getByRole('button', { name: '启用自动执行' })).toHaveCount(0)
  state.config = config
  state.geoblock.status = 'UNKNOWN'
  state.executor_ready = true
  state.blockers = []
  controls.down = false
  await page.getByRole('button', { name: '重新读取' }).click()
  const enable = page.getByRole('button', { name: '启用自动执行' })
  await expect(enable).toBeDisabled()
  state.geoblock.status = 'ALLOWED'
  state.executor_ready = false
  await page.getByRole('button', { name: '检查地域' }).click()
  await expect(page.getByText('未就绪', { exact: true })).toBeVisible()
  await expect(enable).toBeDisabled()
  state.executor_ready = true
  await page.getByRole('button', { name: '检查地域' }).click()
  controls.refuseEnable = true
  await expect(enable).toBeDisabled()
  await page.getByRole('checkbox', { name: /本人符合/ }).check()
  await enable.click()
  await expect(page.getByRole('alert')).toContainText('地域复查未通过')
  await expect(enable).toBeDisabled()
  await expect(page.getByText('官方服务报告出口地域受限。')).toBeVisible()
  expect(writes.at(-1)?.body).toEqual({ revision: '11', eligibility_confirmed: true })
})
test('can stop an enabled configuration even with unsaved budget edits', async ({ page }) => {
  const { state, writes } = await fixture(page)
  state.config = config
  state.configured_enabled = true
  await page.goto('/polymarket-account')
  await typeValue(page.getByLabel('单笔本金上限（USD）'), '0.00000001')
  await page.getByRole('button', { name: '停用自动执行' }).click()
  await expect(page.getByRole('status')).toContainText('自动执行已停用')
  await expect(page.getByLabel('单笔本金上限（USD）')).toHaveValue('0.00000001')
  expect(writes).toEqual([{ path: 'polymarket-account/disable', body: {} }])
})
