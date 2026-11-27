import { expect, test, type Page } from '@playwright/test'

const recent = {
  start_time: '2026-10-10T10:00:01.123Z',
  end_time: '2026-10-10T10:04:58.456Z',
  event_count: 18,
}
type Run = Record<string, unknown>
async function fixture(page: Page) {
  const state = { coverage: true, runs: [] as Run[], submits: [] as Run[] }
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const name = url.pathname.replace('/api/v1/', '')
    let data: unknown
    if (name === 'events')
      return route.fulfill({ contentType: 'text/event-stream', body: 'retry: 60000\n\n' })
    if (name === 'auth/me') data = { username: 'isolated', role: 'ADMIN' }
    else if (name === 'strategy-versions') data = [
      { strategy_id: 'S01', version: '1.0.0', role: 'RETIRED' },
      { strategy_id: 'S01', version: '1.0.1', role: 'CHALLENGER' },
    ]
    else if (name === 'backtests/window') {
      const start = url.searchParams.get('start_time')
      const end = url.searchParams.get('end_time')
      if (!start) data = state.coverage ? recent : null
      else data = {
        start_time: start,
        end_time: end,
        event_count: state.coverage && start < recent.end_time && end && end > recent.start_time
          ? (url.searchParams.get('assets') === 'BTC' ? 4 : 9) : 0,
      }
    } else if (name === 'backtests' && route.request().method() === 'POST') {
      state.submits.push(route.request().postDataJSON())
      if (!state.coverage)
        return route.fulfill({ status: 409, json: { error: { code: 'NO_REPLAY_EVENTS', message: '所选窗口的历史事件已不可用。' } } })
      const run = { id: 'fixture-replay', status: 'QUEUED', progress: 0, result: {}, created_at: recent.end_time }
      state.runs = [run]
      data = run
    } else if (name === 'backtests') data = state.runs
    else data = []
    await route.fulfill({ json: { data } })
  })
  await page.goto('/backtests')
  return state
}
test('a selected market window supplies UTC defaults and tracks a submitted replay', async ({ page }) => {
  const state = await fixture(page)
  await expect(page.getByText('共 0 个回测任务')).toBeVisible()
  await page.getByRole('button', { name: '新建回测' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByLabel('起始时间（UTC）')).toHaveValue(recent.start_time.slice(0, -1))
  await expect(dialog.getByLabel('截止时间（UTC）')).toHaveValue(recent.end_time.slice(0, -1))
  await expect(dialog.getByLabel('策略版本')).toHaveValue('1.0.1')
  await expect(dialog.getByRole('status')).toContainText('18 条所选市场事件')
  await dialog.getByRole('button', { name: '核对配置' }).click()
  await dialog.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByRole('cell', { name: '等待引擎统计' })).toBeVisible()
  expect(state.submits).toHaveLength(1)
  expect(state.submits[0]).toMatchObject({
    strategy_id: 'S01', strategy_version: '1.0.1',
    start_time: recent.start_time, end_time: recent.end_time,
  })
  Object.assign(state.runs[0], { status: 'RUNNING', progress: 40, result: { event_count: 30, event_counts: { BOOK: 10, REFERENCE: 2 } } })
  await page.getByRole('button', { name: '刷新记录' }).click()
  await expect(page.getByRole('cell', { name: '12 / 30' })).toBeVisible()
  await expect(page.getByRole('cell', { name: '40%' })).toBeVisible()
  Object.assign(state.runs[0], { status: 'COMPLETED', progress: 100, result: { event_count: 30, event_counts: { BOOK: 18, REFERENCE: 12 }, fills: 0, validation: 'INSUFFICIENT_DATA' } })
  const row = page.getByRole('row', { name: /COMPLETED/ })
  await expect(row.getByRole('cell', { name: '30 / 30' })).toBeVisible({ timeout: 10000 })
  await expect(row.getByRole('cell', { name: '0', exact: true })).toBeVisible()
  await expect(row.getByRole('cell', { name: '研究样本不足' })).toBeVisible()
})

test('rejects empty scope and preserves a manual period across market changes', async ({ page }) => {
  const state = await fixture(page)
  state.coverage = false
  await page.getByRole('button', { name: '新建回测' }).click()
  const dialog = page.getByRole('dialog')
  const review = dialog.getByRole('button', { name: '核对配置' })
  const start = dialog.getByLabel('起始时间（UTC）')
  const end = dialog.getByLabel('截止时间（UTC）')
  await expect(dialog.getByRole('status')).toContainText('暂无历史市场事件')
  await expect(review).toBeDisabled()
  state.coverage = true
  await dialog.getByRole('button', { name: '使用最近有数据的区间' }).click()
  await expect(review).toBeEnabled()
  await start.fill('2026-10-09T09:00')
  await end.fill('2026-10-09T10:00')
  await expect(dialog.getByRole('status')).toContainText('0 条市场事件')
  await expect(review).toBeDisabled()
  await end.fill('2026-10-10T10:02')
  await expect(dialog.getByRole('status')).toContainText('9 条所选市场事件')
  await dialog.getByLabel('ETH', { exact: true }).uncheck()
  await expect(dialog.getByRole('status')).toContainText('4 条所选市场事件')
  await expect(start).toHaveValue('2026-10-09T09:00')
  await expect(end).toHaveValue('2026-10-10T10:02')
  await review.click()
  await dialog.getByRole('button', { name: '返回修改' }).click()
  await expect(start).toHaveValue('2026-10-09T09:00')
  await dialog.getByLabel('BTC', { exact: true }).uncheck()
  await expect(review).toBeDisabled()
  expect(state.submits).toHaveLength(0)
})

test('explains historical zero-event runs without inventing a fill count', async ({ page }) => {
  const state = await fixture(page)
  state.runs.push({ id: 'legacy-empty', status: 'COMPLETED', progress: 100, result: { event_count: 0, validation: 'INSUFFICIENT_DATA' } })
  await page.getByRole('button', { name: '刷新记录' }).click()
  const row = page.getByRole('row', { name: /legacy-empty/ })
  await expect(row.getByRole('cell', { name: '区间内无回放事件' })).toBeVisible()
  await expect(row.getByRole('cell', { name: '0 / 0' })).toBeVisible()
  await expect(row.getByRole('cell', { name: '0', exact: true })).toHaveCount(0)
})

test('keyboard dates and a changed window prevent stale replay submission', async ({ page }) => {
  const state = await fixture(page)
  await page.getByRole('button', { name: '新建回测' }).click()
  const dialog = page.getByRole('dialog')
  const start = dialog.getByLabel('起始时间（UTC）')
  const end = dialog.getByLabel('截止时间（UTC）')
  const review = dialog.getByRole('button', { name: '核对配置' })
  await expect(review).toBeEnabled()
  // The focused date segment varies by locale. Both arrows edit the native control.
  await start.focus()
  await start.press('ArrowDown')
  await expect(start).not.toHaveValue(recent.start_time.slice(0, -1))
  await start.press('ArrowUp')
  await expect(start).toHaveValue(recent.start_time.slice(0, -1))
  await end.fill(recent.start_time.slice(0, -1))
  await expect(review).toBeDisabled()
  await expect(dialog.getByRole('status')).toContainText('截止时间须晚于起始时间')
  await end.fill(recent.end_time.slice(0, -1))
  await review.click()
  state.coverage = false
  await dialog.getByRole('button', { name: '确认提交' }).click()
  await expect(dialog.getByRole('alert')).toContainText('历史事件已不可用')
  await expect(start).toHaveValue(recent.start_time.slice(0, -1))
  await expect(dialog.getByRole('status')).toContainText('0 条市场事件')
  await expect(review).toBeDisabled()
  expect(state.runs).toHaveLength(0)
})
