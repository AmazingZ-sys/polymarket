<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api, errorText, query } from '../api'
import { useConsole } from '../store'
import Heading from '../components/Heading.vue'

interface Strategy { strategy_id: string; strategy_version: string }
interface PaperAccount extends Strategy {
  id: string
  name: string
  config: { strategies?: Strategy[] }
}
interface AccountConfig extends Strategy {
  wallet_address: string
  paper_account_id: string
  max_order_usd: string
  max_total_usd: string
}
interface AccountState {
  config: AccountConfig | null
  revision: string
  configured_enabled: boolean
  enabled: boolean
  enabled_at: string | null
  executor_ready: boolean
  executor_detail_code: string | null
  executor_checked_at: string | null
  geoblock: {
    status: 'UNCHECKED' | 'ALLOWED' | 'BLOCKED' | 'UNKNOWN'
    country: string | null
    region: string | null
    checked_at: string | null
  }
  blockers: string[]
  accounting: {
    confirmed_principal_usd: string
    reserved_principal_usd: string
    fees: { currency: string; amount: string }[]
    pending_fee_legs: number
  }
  recent_attempts: {
    signal_id: string
    state: 'RESERVED' | 'CONFIRMED' | 'REJECTED' | 'UNKNOWN' | 'EXPOSED'
    created_at: string
    updated_at?: string
    up_amount: string
    down_amount: string
    up_principal: string | null
    down_principal: string | null
    up_fee: string | null
    down_fee: string | null
    up_fee_currency: string | null
    down_fee_currency: string | null
    up_order_id: string | null
    down_order_id: string | null
  }[]
}

const store = useConsole()
const state = ref<AccountState>()
const accounts = ref<PaperAccount[]>([])
const emptyConfig: AccountConfig = {
  wallet_address: '', paper_account_id: '', strategy_id: '', strategy_version: '',
  max_order_usd: '', max_total_usd: '',
}
const draft = reactive<AccountConfig>({ ...emptyConfig })
const pending = ref(false)
const error = ref('')
const paperError = ref('')
const notice = ref('')
const confirmed = ref(false)
const life = new AbortController()
const amountPattern = '(?=.*[1-9])(?:0|[1-9][0-9]{0,12})(?:\\.[0-9]{1,8})?'
const regionLabels = { UNCHECKED: '尚未检查', ALLOWED: '允许', BLOCKED: '受限', UNKNOWN: '无法确定' }
const attemptLabels = { RESERVED: '已预留', CONFIRMED: '已确认', REJECTED: '已拒绝', UNKNOWN: '待核对', EXPOSED: '已暴露' }
const decimal = (value: string | null) => value === null ? '待确认' : value
const account = computed(() => accounts.value.find((item) => item.id === draft.paper_account_id))
const strategies = computed(() => account.value ? [account.value, ...(account.value.config?.strategies ?? [])] : [])
function versionKey(strategy: Strategy) {
  return strategy.strategy_id + '@' + strategy.strategy_version
}
const selectedVersion = computed(() => strategies.value.find((item) => versionKey(item) === versionKey(draft)))
const dirty = computed(() => {
  const saved = state.value?.config ?? emptyConfig
  return (Object.keys(emptyConfig) as (keyof AccountConfig)[]).some((key) => draft[key] !== saved[key])
})
watch([() => state.value?.revision, dirty], () => { confirmed.value = false })
const canEnable = computed(() => {
  const server = state.value
  return server?.config?.strategy_id === 'S01' && !server.configured_enabled && !dirty.value && server.executor_ready &&
    server.geoblock.status === 'ALLOWED' && !server.blockers.length
})
function chooseStrategy(next?: Strategy) {
  draft.strategy_id = next?.strategy_id ?? ''
  draft.strategy_version = next?.strategy_version ?? ''
}
async function readState() {
  return (await api<AccountState>('polymarket-account', { signal: life.signal })).data
}
async function load() {
  if (!store.canWrite || pending.value) return
  pending.value = true
  error.value = paperError.value = notice.value = ''
  const preserveDraft = dirty.value
  await Promise.all([
    (async () => {
      try {
        state.value = await readState()
        if (!preserveDraft) Object.assign(draft, state.value.config ?? emptyConfig)
      } catch (cause) {
        state.value = undefined
        error.value = errorText(cause)
      }
    })(),
    (async () => {
      try {
        const all: PaperAccount[] = []
        let total = 0
        do {
          const page = await api<PaperAccount[]>('paper-accounts' + query({ mode: 'PAPER', limit: 200, offset: all.length }), { signal: life.signal })
          all.push(...page.data)
          total = page.meta?.total ?? all.length
          if (!page.data.length) break
        } while (all.length < total)
        accounts.value = all
      } catch (cause) {
        accounts.value = []
        paperError.value = errorText(cause)
      }
    })(),
  ])
  pending.value = false
}
onMounted(load)
onBeforeUnmount(() => life.abort())

function budgetUnits(amount: string) {
  const [whole, fraction = ''] = amount.split('.')
  return BigInt(whole + fraction.padEnd(8, '0'))
}
async function act(action: 'config' | 'check' | 'enable' | 'disable') {
  if (!store.canWrite || pending.value || !state.value) return
  if (action === 'enable' && (!confirmed.value || !canEnable.value)) return
  error.value = notice.value = ''
  if (action === 'config' && budgetUnits(draft.max_order_usd) > budgetUnits(draft.max_total_usd)) {
    error.value = '单笔上限须小于或等于累计预算。'
    return
  }
  pending.value = true
  try {
    state.value = (await api<AccountState>('polymarket-account/' + action, {
      body: action === 'config' ? { ...draft } : action === 'enable'
        ? { revision: state.value.revision, eligibility_confirmed: true } : {},
      signal: life.signal,
    })).data
    if (action === 'config') Object.assign(draft, state.value.config)
    confirmed.value = false
    notice.value = {
      config: '配置已保存，自动执行已停用。',
      check: '地域检查已更新。',
      enable: '自动执行已启用。',
      disable: '自动执行已停用。',
    }[action]
  } catch (cause) {
    error.value = errorText(cause)
    try {
      state.value = await readState()
    } catch {
      state.value = undefined
    }
  } finally {
    pending.value = false
  }
}
</script>
<template>
  <Heading title="Polymarket 账户" en="Polymarket account" detail="管理国际站的钱包公开地址、策略来源与执行预算。">
    <button v-if="store.canWrite && state" class="btn" :disabled="pending" @click="load">刷新账户状态</button>
  </Heading>
  <p v-if="!store.canWrite" class="error inset" role="alert">此页面仅限管理员访问。</p>
  <template v-else>
    <p v-if="error" class="error inset" role="alert">{{ error }}</p>
    <p v-if="notice" class="muted" role="status">{{ notice }}</p>
    <p v-if="!state" class="muted">
      {{ pending ? '正在读取账户配置…' : '当前账户状态不可用。' }}
      <button v-if="!pending" class="btn" @click="load">重新读取</button>
    </p>
    <div v-else class="account-layout">
      <section class="panel">
        <header class="panel-head"><h2>账户与限额</h2></header>
        <form class="account-form" @submit.prevent="act('config')">
          <fieldset :disabled="pending">
            <label>钱包公开地址<input v-model.trim="draft.wallet_address" required pattern="0x(?!0{40}$)[0-9a-fA-F]{40}"
              autocomplete="off" spellcheck="false" class="mono" placeholder="0x…" title="0x 开头的 40 位十六进制钱包地址" /></label>
            <label>关联 PAPER 账户<select v-model="draft.paper_account_id" required
              @change="chooseStrategy(account)">
              <option value="" disabled>选择模拟账户</option>
              <option v-if="draft.paper_account_id && !account" :value="draft.paper_account_id" disabled>{{ draft.paper_account_id }}</option>
              <option v-for="paper in accounts" :key="paper.id" :value="paper.id">{{ paper.name }} · {{ paper.id }}</option>
            </select></label>
            <p v-if="paperError" role="alert" class="error">账户列表读取失败。{{ paperError }}
              <button class="btn" type="button" @click="load">重试</button>
            </p>
            <p v-else-if="!pending && !accounts.length" class="muted">尚无 PAPER 账户。<router-link to="/paper-accounts">前往模拟盘</router-link></p>
            <label>策略版本<select :value="draft.strategy_id ? versionKey(draft) : ''" required :disabled="!account"
              @change="chooseStrategy(strategies.find((item) => versionKey(item) === ($event.target as HTMLSelectElement).value))">
              <option value="" disabled>先选择模拟账户</option>
              <option v-if="draft.strategy_id && !selectedVersion" :value="versionKey(draft)" disabled>
                {{ draft.strategy_id }} · {{ draft.strategy_version }}
              </option>
              <option v-for="strategy in strategies" :key="versionKey(strategy)" :value="versionKey(strategy)">
                {{ strategy.strategy_id }} · {{ strategy.strategy_version }}
              </option>
            </select></label>
            <p v-if="draft.strategy_id && draft.strategy_id !== 'S01'" class="muted">当前执行器仅支持 S01 双腿策略的实时信号。</p>
            <div class="form-grid">
              <label>单笔本金上限（USD）<input v-model="draft.max_order_usd" inputmode="decimal" :pattern="amountPattern" required title="正数，最多 13 位整数和 8 位小数" /></label>
              <label>累计本金预算（USD）<input v-model="draft.max_total_usd" inputmode="decimal" :pattern="amountPattern" required title="正数，最多 13 位整数和 8 位小数" /></label>
            </div>
            <p class="muted">预算按成交本金封顶。手续费按实际回报币种单列，未确认的手续费显示“待确认”。</p>
            <div class="form-actions"><button class="primary" :disabled="!dirty || !selectedVersion">
              {{ state.configured_enabled ? '保存配置并停用' : '保存配置' }}
            </button></div>
          </fieldset>
        </form>
      </section>
      <section class="panel execution">
        <header class="panel-head"><h2>执行状态</h2><span class="tag">{{ state.enabled ? '已启用' : state.configured_enabled ? '已授权 · 执行暂停' : '已停用' }}</span></header>
        <dl>
          <dt>执行器</dt><dd>{{ state.executor_ready ? '就绪' : '未就绪' }}</dd>
          <template v-if="state.executor_detail_code"><dt>执行器诊断</dt><dd>{{ state.executor_detail_code }}</dd></template>
          <template v-if="state.executor_checked_at"><dt>心跳时间（UTC）</dt><dd>{{ state.executor_checked_at }}</dd></template>
          <dt>服务器出口地域</dt><dd>{{ regionLabels[state.geoblock.status] }}
            <span v-if="state.geoblock.country || state.geoblock.region" class="mono"> · {{ [state.geoblock.country, state.geoblock.region].filter(Boolean).join(' / ') }}</span>
          </dd>
          <dt>检查时间（UTC）</dt><dd>{{ state.geoblock.checked_at ?? '尚无记录' }}</dd>
          <template v-if="state.configured_enabled && state.enabled_at"><dt>启用时间（UTC）</dt><dd>{{ state.enabled_at }}</dd></template>
        </dl>
        <ul v-if="state.blockers.length" class="blockers"><li v-for="item in state.blockers" :key="item">{{ item }}</li></ul>
        <p v-if="dirty" class="muted">当前配置有未保存的更改。</p>
        <label v-if="canEnable" class="authorization"><input v-model="confirmed" type="checkbox" :disabled="pending" />
          本人符合 Polymarket 国际站使用资格，并确认按当前策略版本、钱包地址、单笔上限和累计预算启用自动执行。
        </label>
        <div class="form-actions">
          <button class="btn" :disabled="pending" @click="act('check')">检查地域</button>
          <button v-if="state.configured_enabled" class="danger" :disabled="pending" @click="act('disable')">停用自动执行</button>
          <button v-else class="primary" :disabled="pending || !canEnable || !confirmed" @click="act('enable')">启用自动执行</button>
        </div>
      </section>
      <section v-if="state.accounting" class="panel accounting">
        <header class="panel-head"><h2>本金与手续费</h2></header>
        <dl class="accounting-summary">
          <dt>已确认成交本金（USD）</dt><dd>{{ state.accounting.confirmed_principal_usd }}</dd>
          <dt>未确认预留本金（USD）</dt><dd>{{ state.accounting.reserved_principal_usd }}</dd>
          <dt>待确认手续费腿数</dt><dd>{{ state.accounting.pending_fee_legs }}</dd>
          <template v-for="fee in state.accounting.fees" :key="fee.currency"><dt>已确认手续费（{{ fee.currency }}）</dt><dd>{{ fee.amount }}</dd></template>
        </dl>
        <div class="attempts">
          <h3>近期执行（当前钱包，最近 20 条）</h3>
          <p v-if="!state.recent_attempts.length" class="muted">暂无执行记录。</p>
          <article v-for="attempt in state.recent_attempts" :key="attempt.signal_id" class="attempt">
            <div class="attempt-head"><span class="mono">{{ attempt.signal_id }}</span><span class="tag">{{ attemptLabels[attempt.state] }}</span></div>
            <time>{{ attempt.created_at }}<template v-if="attempt.updated_at"> · 更新 {{ attempt.updated_at }}</template></time>
            <div class="legs">
              <span>UP 本金 {{ decimal(attempt.up_principal) }} · 预算 {{ attempt.up_amount }}<small> · 订单 {{ attempt.up_order_id ?? '待确认' }} · 手续费 {{ decimal(attempt.up_fee) }} {{ attempt.up_fee_currency ?? '' }}</small></span>
              <span>DOWN 本金 {{ decimal(attempt.down_principal) }} · 预算 {{ attempt.down_amount }}<small> · 订单 {{ attempt.down_order_id ?? '待确认' }} · 手续费 {{ decimal(attempt.down_fee) }} {{ attempt.down_fee_currency ?? '' }}</small></span>
            </div>
          </article>
        </div>
      </section>
    </div>
  </template>
</template>
<style scoped>
.account-layout { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(17rem, 1fr); align-items: start; gap: 16px; }
.account-layout > .panel { margin: 0; min-width: 0; }
.account-form, .execution > dl, .execution > .form-actions, .execution > p { margin: 0; padding: 16px 18px; }
.account-form fieldset { border: 0; margin: 0; padding: 0; display: grid; gap: 16px; min-width: 0; }
.account-form label { display: grid; gap: 6px; min-width: 0; }
.account-form input, .account-form select { width: 100%; }
.account-form p { margin: 0; }
.execution dl { display: grid; grid-template-columns: auto 1fr; gap: 14px 12px; }
.execution dt { color: var(--muted); }
.execution dd { margin: 0; text-align: right; overflow-wrap: anywhere; }
.blockers { padding: 10px 16px 10px 32px; background: var(--bg); color: var(--dim); margin: 0 18px; }
.authorization { display: flex; align-items: flex-start; gap: 8px; padding: 14px 18px 0; }
.accounting { grid-column: 1 / -1; }
.accounting-summary { display: grid; grid-template-columns: auto 1fr; gap: 10px 18px; margin: 0; padding: 16px 18px; }
.accounting-summary dt { color: var(--muted); }
.accounting-summary dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
.attempts { border-top: 1px solid var(--line); padding: 14px 18px; }
.attempts h3 { margin: 0 0 10px; font-size: 13px; }
.attempt { border-top: 1px solid var(--line); padding: 11px 0; display: grid; gap: 7px; }
.attempt-head, .legs { display: flex; gap: 10px; justify-content: space-between; flex-wrap: wrap; }
.attempt time, .legs small { color: var(--muted); font-size: 11px; }
.legs { display: grid; gap: 5px; font-size: 12px; }
@media (max-width: 850px) { .account-layout { grid-template-columns: 1fr; } }
</style>
