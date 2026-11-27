import type { Row } from './api'
const columns: Record<string, string> = {
  markets: 'title asset timeframe market_status reference_price end_time',
  'market-events': 'kind market_id event_time received_at quality_flags',
  'data-sources': 'source status last_success_at last_attempt_at last_error',
  opportunities: 'strategy_id market_id action reason created_at',
  'paper-accounts':
    'name mode strategy_id status equity realized_pnl created_at',
  'paper-orders':
    'id account_id market_id side status quantity price created_at',
  'paper-fills': 'id account_id market_id side quantity price fee created_at',
  'paper-positions': 'account_id market_id side quantity cost',
  settlements: 'account_id market_id outcome payout realized_pnl created_at',
  ledger:
    'reason available_before available_delta available_after frozen_delta created_at',
  equity: 'account_id equity created_at',
  strategies: 'strategy_id name description research_status',
  'strategy-versions': 'strategy_id version description created_at',
  experiments: 'name hypothesis status created_at',
  backtests: 'id status progress replay_events fills replay_result created_at',
  analytics: 'name account_id equity realized_pnl fees max_drawdown',
  attributions: 'account_id market_id reason net_pnl created_at',
  improvements: 'strategy_id problem status created_at',
  reports: 'name start_time end_time created_at',
  risk: 'account_id reason severity created_at',
  system: 'name status updated_at',
  jobs: 'name status last_run_at next_run_at',
  logs: 'actor action created_at',
  settings: 'name value updated_at',
}
const labels: Record<string, string> = {
  name: '名称',
  strategies: '策略组合',
  title: '市场名称',
  asset: '资产',
  timeframe: '周期',
  market_status: '市场状态',
  status: '状态',
  reference_price: '开盘基准价',
  market_id: '市场编号',
  id: '记录编号',
  condition_id: 'Condition ID',
  strategy_id: '策略',
  strategy_version: '策略版本',
  version: '版本',
  mode: '运行模式',
  initial_balance: '初始虚拟资金',
  available_balance: '可用余额',
  frozen_balance: '冻结余额',
  equity: '模拟权益',
  realized_pnl: '已实现盈亏',
  max_drawdown: '最大回撤',
  fees: '累计费用',
  created_at: '创建时间',
  updated_at: '更新时间',
  started_at: '启动时间',
  stopped_at: '停止时间',
  source: '来源',
  last_event_at: '最近事件',
  event_time: '事件时间',
  received_at: '接收时间',
  quality_flags: '质量标记',
  side: '方向',
  quantity: '数量',
  price: '价格',
  fee: '费用',
  cost: '成本',
  kind: '类型',
  before_balance: '变动前',
  amount: '金额',
  after_balance: '变动后',
  account_id: '模拟账户',
  outcome: '结算结果',
  payout: '兑付',
  description: '说明',
  research_status: '研究阶段',
  hypothesis: '研究假设',
  start_time: '开始时间',
  end_time: '结束时间',
  progress: '进度',
  replay_events: '已回放 / 总事件',
  fills: '模拟成交数',
  replay_result: '回放结果',
  result: '运行结果',
  reason: '原因',
  problem: '研究问题',
  severity: '级别',
  actor: '操作人',
  action: '操作',
  value: '值',
  message: '状态说明',
  conclusion: '研究结论',
  available_before: '可用余额（前）',
  available_delta: '可用余额变动',
  available_after: '可用余额（后）',
  frozen_delta: '冻结变动',
  last_error: '最近异常',
  last_success_at: '最近成功',
  last_attempt_at: '最近尝试',
  available: '可用',
  settlements: '结算数',
  win_rate: '胜率',
  profit_factor: '盈亏因子',
  last_run_at: '最近执行',
  next_run_at: '下次执行',
  config: '实验配置',
  sample_count: '样本数',
  rule_version: '规则版本',
  settlement_rule: '结算规则',
  resolution_source: '结算来源',
  config_hash: '配置摘要',
  data: '事件数据',
  peak_equity: '历史最高权益',
  asset_id: 'Token ID',
  type: '类别',
  evidence: '证据',
  timestamp: '时间',
  net_pnl: '模拟净收益',
}
export const label = (key: string) => labels[key] || key
export function backtestRow(row: Row): Row {
  const result = (row.result ?? {}) as Row
  const total = result.event_count
  const progress = typeof row.progress === 'string' || typeof row.progress === 'number'
    ? Number(row.progress)
    : NaN
  let processed: number | null = null
  if (result.event_counts && typeof result.event_counts === 'object')
    processed = Object.values(result.event_counts).reduce((sum: number, count) => sum + Number(count), 0)
  else if (total === 0) processed = 0

  return {
    id: row.id,
    status: row.status,
    progress: Number.isFinite(progress)
      ? progress.toLocaleString('zh-CN', { maximumFractionDigits: 1 }) + '%'
      : null,
    replay_events: total == null
      ? (row.status === 'QUEUED' ? '等待引擎统计' : null)
      : (processed ?? '—') + ' / ' + total,
    fills: result.fills,
    replay_result: result.error || (total === 0
      ? '区间内无回放事件'
      : result.validation === 'INSUFFICIENT_DATA' ? '研究样本不足' : result.validation),
    created_at: row.created_at,
  }
}
export function keysFor(resource: string, rows: Row[]) {
  const actual = Array.from(new Set(rows.flatMap((row) => Object.keys(row))))
  const preferred = (columns[resource] || '').split(' ').filter(Boolean)
  if (!rows.length) return preferred.slice(0, 6)
  const selected = preferred.filter((key) => actual.includes(key))
  const others = actual.filter(
    (key) =>
      !selected.includes(key) &&
      rows.some((row) => row[key] !== null && typeof row[key] !== 'object'),
  )
  return [...selected, ...others].slice(0, 7)
}
export const money = (value: unknown) =>
  value === null ||
  value === undefined ||
  value === '' ||
  !Number.isFinite(Number(value))
    ? '—'
    : new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(Number(value))
export function field(
  key: string,
  value: unknown,
  date: (v: unknown) => string,
) {
  if (value === null || value === undefined || value === '') return '—'
  if (
    (key.endsWith('_at') || key.endsWith('_time') || key === 'timestamp') &&
    typeof value === 'string' &&
    !Number.isNaN(Date.parse(value))
  )
    return date(value)
  if (key === 'timeframe' && /^\d+$/.test(String(value))) return value + 'm'
  if (
    [
      'equity',
      'initial_balance',
      'available_balance',
      'frozen_balance',
      'realized_pnl',
      'fees',
      'payout',
      'net_pnl',
      'peak_equity',
    ].includes(key)
  )
    return money(value)
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
