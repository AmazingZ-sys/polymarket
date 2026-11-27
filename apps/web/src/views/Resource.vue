<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Plus, ShieldAlert } from 'lucide-vue-next'
import { useMessage } from 'naive-ui'
import { byKey } from '../nav'
import type { Row } from '../api'
import { useConsole, zones } from '../store'
import Heading from '../components/Heading.vue'
import Slice from '../components/Slice.vue'
import DataStats from '../components/DataStats.vue'
import Comparison from '../components/Comparison.vue'
import ReferenceQuotes from '../components/ReferenceQuotes.vue'
import CommandForm from '../components/CommandForm.vue'
const route = useRoute(),
  router = useRouter(),
  store = useConsole(),
  message = useMessage()
const key = computed(() => String(route.name)),
  page = computed(() => byKey[key.value])
const create: Record<string, string> = {
  'paper-accounts': '创建模拟盘',
  backtests: '新建回测',
  experiments: '新建实验',
  'strategy-versions': '创建版本',
  reports: '生成报告',
  evaluations: '登记评估',
  improvements: '提交建议',
}
const context: Record<string, string> = {
  'paper-accounts': '各模拟账户独立记账，共享行情下的结果属于独立对照实验。',
  backtests: '按可观察事件回放历史，保存策略、数据与撮合版本。',
  experiments: '比较预先登记研究假设的独立模拟账户。',
  opportunities: '每条研究信号关联其可观察数据与策略版本。',
  attributions: '根据模拟成交与市场事件核对净收益和亏损原因。',
  reports: '按 UTC 时间范围汇总真实研究记录。',
}
const asset = ref(''),
  period = ref(''),
  state = ref(''),
  account = ref(String(route.query.account_id || '')),
  market = ref(String(route.query.market_id || '')),
  reason = ref('')
const typedAccount = ref(account.value),
  typedMarket = ref(market.value),
  typedReason = ref('')
const slice = ref<InstanceType<typeof Slice>>(),
  command = ref(''),
  target = ref<Row>(),
  section = ref(key.value)
const comparison = ref<Row | null>(null)
const lossForm = reactive({
  min_loss: '',
  max_loss: '',
  start_time: '',
  end_time: '',
  strategy_id: '',
  data_quality: '',
})
const lossFilters = ref<Record<string, string>>({})
const lossError = ref('')
const lossAmountPattern = '(?:0|[1-9][0-9]{0,12})(?:\\.[0-9]{1,8})?'
function lossUnits(amount: string) {
  const [whole, fraction = ''] = amount.split('.')
  return BigInt(whole + fraction.padEnd(8, '0'))
}
function applyLossFilters() {
  lossError.value = ''
  const values = { ...lossForm }
  if (values.min_loss && values.max_loss && lossUnits(values.min_loss) > lossUnits(values.max_loss)) {
    lossError.value = '最大亏损须大于或等于最小亏损。'
    return
  }
  for (const key of ['start_time', 'end_time'] as const) {
    if (values[key]) values[key] = new Date(values[key] + 'Z').toISOString()
  }
  if (values.start_time && values.end_time && values.start_time >= values.end_time) {
    lossError.value = '归因截止时间须晚于起始时间。'
    return
  }
  lossFilters.value = values
}
const filters = computed(() => {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries({
    asset: asset.value,
    timeframe: period.value,
    status: state.value,
    account_id: account.value,
    market_id: market.value,
    reason: reason.value,
    event_id:
      key.value === 'market-events' ? String(route.query.event_id || '') : '',
    ...lossFilters.value,
  }))
    if (v) out[k] = v
  return out
})
const sections: Record<string, Record<string, string>> = {
  'data-sources': {
    'data-sources': '数据源',
    'data-quality': '质量事件',
    datasets: '归档数据集',
    'reference-prices': '参考行情',
  },
  system: { system: '系统事件', jobs: '定时任务', notifications: '通知历史' },
}
const acctFilter = [
  'paper-orders',
  'paper-positions',
  'paper-fills',
  'settlements',
  'opportunities',
  'attributions',
  'ledger',
  'equity',
  'risk',
]
const marketFilter = [
  'paper-orders',
  'paper-positions',
  'paper-fills',
  'settlements',
  'opportunities',
  'attributions',
  'risk',
  'market-events',
]
function edit(kind: string, row?: Row) {
  command.value = kind
  target.value = row
}
function editRecord(close: (action: () => void) => void, kind: string, row: Row) {
  close(() => edit(kind, row))
}
function openRecord(row: Row) {
  if (key.value === 'paper-accounts')
    void router.push('/paper-accounts/' + row.id)
  if (key.value === 'experiments') comparison.value = row
  if (key.value === 'markets')
    void router.push('/markets/' + encodeURIComponent(String(row.market_id)))
}
function saved(row: Row) {
  const createdBacktest = command.value === 'backtests'
  command.value = ''
  message.success(createdBacktest ? '回测任务已提交，可在列表查看回放进度。' : '操作已记录')
  if (key.value === 'paper-accounts' && row.id)
    void router.push('/paper-accounts/' + row.id)
  else if (createdBacktest) void slice.value?.showNewest()
  else void slice.value?.refresh()
}
</script>
<template>
  <Heading :title="page.label" :en="page.en" :detail="context[key]"
    ><button
      v-if="store.canWrite && create[key]"
      class="primary"
      @click="edit(key)"
    >
      <Plus :size="16" />{{ create[key] }}</button
    ><button
      v-if="store.canWrite && key === 'risk'"
      class="btn"
      @click="edit('release')"
    >
      解除全局暂停</button
    ><button
      v-if="store.canWrite && key === 'risk'"
      class="danger"
      @click="edit('halt')"
    >
      <ShieldAlert :size="15" />全局暂停
    </button></Heading
  >
  <DataStats v-if="key === 'data-sources'" />
  <ReferenceQuotes v-if="key === 'markets'" />
  <section v-if="key === 'settings'" class="panel">
    <div class="panel-head"><h2>显示偏好</h2></div>
    <div class="settings-grid">
      <label
        >显示主题<select
          :value="store.theme"
          @change="store.setTheme(($event.target as HTMLSelectElement).value)"
        >
          <option value="dark">深色</option>
          <option value="light">浅色</option>
        </select></label
      ><label
        >时间显示<select
          :value="store.zone"
          @change="store.setZone(($event.target as HTMLSelectElement).value)"
        >
          <option v-for="z in zones" :key="z">{{ z }}</option>
        </select></label
      >
    </div>
  </section>
  <div v-if="sections[key]" class="tabs">
    <button
      v-for="(name, k) in sections[key]"
      :key="k"
      :class="{ chosen: section === k }"
      @click="section = String(k)"
    >
      {{ name }}
    </button>
  </div>
  <form
    v-if="key === 'attributions'"
    class="panel loss-filters"
    @submit.prevent="applyLossFilters"
  >
    <label
      >最小亏损（USD）<input
        v-model="lossForm.min_loss"
        inputmode="decimal"
        :pattern="lossAmountPattern"
        title="请输入非负十进制金额，最多 13 位整数和 8 位小数"
    /></label>
    <label
      >最大亏损（USD）<input
        v-model="lossForm.max_loss"
        inputmode="decimal"
        :pattern="lossAmountPattern"
        title="请输入非负十进制金额，最多 13 位整数和 8 位小数"
    /></label>
    <label
      >归因起始（UTC）<input
        v-model="lossForm.start_time"
        type="datetime-local"
        step="any"
    /></label>
    <label
      >归因截止（UTC）<input v-model="lossForm.end_time" type="datetime-local" step="any"
    /></label>
    <label
      >发出信号的策略<select v-model="lossForm.strategy_id">
        <option value="">全部策略</option>
        <option v-for="id in ['S00', 'S01', 'S02', 'S03', 'S04']" :key="id">
          {{ id }}
        </option>
      </select></label
    >
    <label
      >成交时行情质量<select v-model="lossForm.data_quality">
        <option value="">全部质量</option>
        <option value="CLEAN">无质量标记</option>
        <option value="FLAGGED">有质量标记</option>
        <option value="UNKNOWN">缺少行情证据</option>
      </select></label
    >
    <button class="btn">应用筛选</button>
    <p v-if="lossError" role="alert" class="error">{{ lossError }}</p>
  </form>
  <p v-if="key === 'market-events' && filters.event_id" class="muted">
    事件 ID · {{ filters.event_id }}
  </p>
  <Slice
    ref="slice"
    :resource="section"
    :filters="filters"
    :open-row="
      ['paper-accounts', 'markets', 'experiments'].includes(key)
        ? openRecord
        : undefined
    "
    ><template #filters>
      <template v-if="key === 'markets'"
        ><select v-model="asset" aria-label="筛选资产">
          <option value="">全部资产</option>
          <option>BTC</option>
          <option>ETH</option></select
        ><select v-model="period" aria-label="筛选周期">
          <option value="">全部周期</option>
          <option value="5">5m</option>
          <option value="15">15m</option>
        </select></template
      >
      <select
        v-if="key === 'paper-accounts'"
        v-model="state"
        aria-label="筛选状态"
      >
        <option value="">全部状态</option>
        <option
          v-for="s in [
            'CREATED',
            'STARTING',
            'RUNNING',
            'PAUSED',
            'RISK_HALTED',
            'STOPPING',
            'STOPPED',
            'ERROR',
            'ARCHIVED',
          ]"
          :key="s"
        >
          {{ s }}
        </option>
      </select>
      <input
        v-if="acctFilter.includes(key)"
        v-model="typedAccount"
        class="filter-field"
        placeholder="账户 ID · 回车筛选"
        aria-label="筛选账户 ID"
        @keyup.enter="account = typedAccount.trim()"
      />
      <input
        v-if="marketFilter.includes(key)"
        v-model="typedMarket"
        class="filter-field"
        placeholder="市场 ID · 回车筛选"
        aria-label="筛选市场 ID"
        @keyup.enter="market = typedMarket.trim()"
      />
      <input
        v-if="key === 'attributions'"
        v-model="typedReason"
        class="filter-field"
        placeholder="归因原因 · 回车筛选"
        aria-label="筛选原因"
        @keyup.enter="reason = typedReason.trim()"
      /> </template
    ><template #record-action="{ row, close }"
      ><div v-if="row" class="form-actions">
        <button
          v-if="
            key === 'strategy-versions' &&
            store.canWrite &&
            row.role !== 'CHAMPION'
          "
          class="btn"
          @click="editRecord(close, 'promote', row)"
        >
          人工评审晋级
        </button>
        <button
          v-if="section === 'jobs' && store.canWrite"
          class="primary"
          @click="editRecord(close, 'trigger', row)"
        >
          手动触发
        </button>
        <button
          v-if="
            key === 'backtests' &&
            store.canWrite &&
            ['QUEUED', 'RUNNING'].includes(String(row.status))
          "
          class="danger"
          @click="editRecord(close, 'cancel', row)"
        >
          取消回测
        </button>
        <button
          v-if="
            key === 'improvements' && store.canWrite && row.status === 'PENDING'
          "
          class="primary"
          @click="editRecord(close, 'review', row)"
        >
          审核建议
        </button>
        <router-link
          v-for="event in [
            'signal_event',
            'book_event',
            'resolution_event',
          ].filter((k) => row[k])"
          :key="event"
          class="btn"
          :to="{
            path: '/market-events',
            query: {
              market_id: String(row.market_id),
              event_id: String(row[event]),
            },
          }"
          >{{
            {
              signal_event: '信号行情',
              book_event: '成交盘口',
              resolution_event: '结算证据',
            }[event]
          }}</router-link
        >
        <router-link
          v-if="row.account_id"
          class="btn"
          :to="'/paper-accounts/' + row.account_id"
          >查看关联模拟盘</router-link
        >
        <router-link
          v-if="row.market_id && key !== 'market-events'"
          class="btn"
          :to="{
            path: '/market-events',
            query: { market_id: String(row.market_id) },
          }"
          >查看市场时间线</router-link
        >
      </div></template
    ></Slice
  >
  <Comparison
    v-if="comparison"
    :experiment="comparison"
    @close="comparison = null"
  /><CommandForm
    v-if="command"
    :kind="command"
    :record="target"
    @close="command = ''"
    @saved="saved"
  />
</template>
