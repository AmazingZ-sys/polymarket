<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NModal, useMessage } from 'naive-ui'
import { ArrowLeft, Copy, Download, X } from 'lucide-vue-next'
import Heading from '../components/Heading.vue'
import Slice from '../components/Slice.vue'
import Chart from '../components/Chart.vue'
import { api, errorText, query, rowsToFile, type Row } from '../api'
import { field, label, money } from '../fields'
import { useLoad } from '../load'
import { useConsole } from '../store'
const id = String(useRoute().params.id),
  router = useRouter(),
  store = useConsole(),
  message = useMessage()
const account = ref<Row>({}),
  metrics = ref<Row>({}),
  snapshots = ref<Row[]>([]),
  active = ref('ledger'),
  cmd = ref(''),
  commandKey = ref(''),
  busyCommand = ref(false),
  errorCommand = ref('')
const { busy, error, refresh } = useLoad(async (signal) => {
  const [a, s, stats] = await Promise.all([
    api<Row>('paper-accounts/' + id, { signal }),
    api<Row[]>('equity' + query({ account_id: id, limit: 200 }), { signal }),
    api<Row[]>('analytics' + query({ account_id: id, limit: 1 }), { signal }),
  ])
  account.value = a.data
  snapshots.value = s.data
  metrics.value = stats.data[0] || {}
})
const tabs: Record<string, string> = {
  ledger: '资金流水',
  'paper-positions': '虚拟持仓',
  'paper-orders': '模拟订单',
  'paper-fills': '模拟成交',
  settlements: '结算记录',
  opportunities: '策略信号',
  attributions: '亏损归因',
  risk: '风险事件',
  'paper-sessions': '运行历史',
  config: '实验配置',
}
const actions: {
  action: string
  label: string
  states: string[]
  detail: string
}[] = [
  {
    action: 'start',
    label: '启动',
    states: ['CREATED'],
    detail: '将启动此研究实例并检查运行条件。',
  },
  {
    action: 'pause',
    label: '暂停',
    states: ['RUNNING', 'STARTING'],
    detail: '将暂停新订单并解冻在途订单，已有持仓继续等待结算。',
  },
  {
    action: 'resume',
    label: '恢复',
    states: ['PAUSED', 'RISK_HALTED', 'ERROR'],
    detail: '将重新校验账务与市场状态，再进入运行流程。',
  },
  {
    action: 'stop',
    label: '停止',
    states: [
      'CREATED',
      'STARTING',
      'RUNNING',
      'PAUSED',
      'RISK_HALTED',
      'ERROR',
    ],
    detail: '将撤销待处理订单，停止策略运行，持仓保留至结算。',
  },
  {
    action: 'archive',
    label: '归档',
    states: ['STOPPED'],
    detail: '归档需先结清持仓，历史记录可以继续查阅。',
  },
  {
    action: 'clone',
    label: '克隆',
    states: [],
    detail: '使用同一初始虚拟资金和配置创建独立账户。',
  },
]
const additionalStrategies = computed(() => {
  const cfg = account.value.config as Row | undefined
  return Array.isArray(cfg?.strategies)
    ? (cfg.strategies as Row[])
        .map((s) => s.strategy_id + ' v' + s.strategy_version)
        .join(' + ')
    : ''
})
const selected = computed(() => actions.find((a) => a.action === cmd.value))
function request(action: string) {
  cmd.value = action
  commandKey.value = crypto.randomUUID()
  errorCommand.value = ''
}
async function apply() {
  if (busyCommand.value) return
  busyCommand.value = true
  errorCommand.value = ''
  try {
    const row = (
      await api<Row>('paper-accounts/' + id + '/' + cmd.value, {
        body: {},
        key: commandKey.value,
      })
    ).data
    const cloned = cmd.value === 'clone'
    cmd.value = ''
    message.success('操作已记录')
    if (cloned && row.id) void router.push('/paper-accounts/' + row.id)
    else await refresh()
  } catch (e) {
    errorCommand.value = errorText(e)
  } finally {
    busyCommand.value = false
  }
}
</script>
<template>
  <router-link class="back" to="/paper-accounts"
    ><ArrowLeft :size="14" />模拟盘</router-link
  ><Heading :title="String(account.name || '模拟盘详情')" en="Paper account"
    ><button
      v-if="account.id"
      class="btn"
      @click="rowsToFile([account], 'json', 'paper-account-' + id)"
    >
      <Download :size="14" />导出配置
    </button></Heading
  >
  <p v-if="error" class="error" role="alert">
    {{ error }} <button class="btn" @click="refresh">重试</button>
  </p>
  <template v-if="account.id">
    <div class="account-bar">
      <div>
        <span class="tag">{{ account.status }}</span
        ><span class="mono"
          >{{ account.mode }} / {{ account.strategy_id }} / v{{
            account.strategy_version
          }}<template v-if="additionalStrategies">
            + {{ additionalStrategies }}</template
          ></span
        >
      </div>
      <div v-if="store.canWrite" class="actions">
        <button
          v-for="a in actions.filter(
            (x) =>
              !x.states.length || x.states.includes(String(account.status)),
          )"
          :key="a.action"
          :class="
            a.action === 'start' || a.action === 'resume' ? 'primary' : 'btn'
          "
          @click="request(a.action)"
        >
          <Copy v-if="a.action === 'clone'" :size="13" />{{ a.label }}
        </button>
      </div>
    </div>
    <div class="stats">
      <article
        v-for="k in [
          'equity',
          'available_balance',
          'frozen_balance',
          'realized_pnl',
        ]"
        :key="k"
        class="stat"
      >
        <div>{{ label(k) }}</div>
        <strong>{{ money(account[k]) }}</strong
        ><small>USD · 虚拟资金</small>
      </article>
    </div>
    <div class="dash-grid">
      <section class="panel">
        <div class="panel-head">
          <h2>模拟净值曲线</h2>
          <small class="kicker">USD</small>
        </div>
        <Chart :rows="snapshots" time-key="created_at" />
      </section>
      <section class="panel">
        <div class="panel-head"><h2>账户指标</h2></div>
        <dl class="brief">
          <dt>模拟成交数 / 结算胜率</dt>
          <dd>
            {{ metrics.fills ?? '—' }} /
            {{
              metrics.win_rate == null
                ? '—'
                : (Number(metrics.win_rate) * 100).toFixed(2) + '%'
            }}
          </dd>
          <dt>累计模拟净收益</dt>
          <dd>{{ money(metrics.net_pnl) }}</dd>
          <template
            v-for="k in [
              'initial_balance',
              'fees',
              'peak_equity',
              'max_drawdown',
              'created_at',
              'started_at',
              'stopped_at',
            ]"
            :key="k"
            ><dt>{{ label(k) }}</dt>
            <dd>
              {{
                k === 'max_drawdown' && account[k] != null
                  ? (Number(account[k]) * 100).toFixed(2) + '%'
                  : field(k, account[k], store.date)
              }}
            </dd></template
          >
        </dl>
      </section>
    </div>
    <div class="tabs">
      <button
        v-for="(name, k) in tabs"
        :key="k"
        :class="{ chosen: active === k }"
        @click="active = String(k)"
      >
        {{ name }}
      </button>
    </div>
    <Slice
      v-if="active !== 'config'"
      :key="active"
      :resource="active"
      :filters="{ account_id: id }"
    />
    <section v-else class="panel">
      <div class="panel-head"><h2>固定实验配置</h2></div>
      <pre class="json">{{ JSON.stringify(account.config, null, 2) }}</pre>
      <p class="hash">SHA-256 · {{ account.config_hash }}</p>
    </section>
  </template>
  <div v-else-if="busy" class="empty">正在读取账户…</div>
  <NModal
    :show="!!cmd"
    :mask-closable="!busyCommand"
    @update:show="
      (v) => {
        if (!v && !busyCommand) cmd = ''
      }
    "
    ><div class="dialog" role="dialog" aria-modal="true" aria-label="确认账户操作">
      <div class="panel-head">
        <h2>确认{{ selected?.label }}模拟盘</h2>
        <button
          class="icon"
          aria-label="关闭"
          :disabled="busyCommand"
          @click="cmd = ''"
        >
          <X :size="17" />
        </button>
      </div>
      <div class="edit-form">
        <strong>{{ account.name }}</strong>
        <p>{{ selected?.detail }}</p>
        <p v-if="errorCommand" class="error" role="alert">{{ errorCommand }}</p>
        <div class="form-actions">
          <button class="btn" :disabled="busyCommand" @click="cmd = ''">
            取消</button
          ><button class="primary" :disabled="busyCommand" @click="apply">
            确认{{ selected?.label }}
          </button>
        </div>
      </div>
    </div></NModal
  >
</template>
