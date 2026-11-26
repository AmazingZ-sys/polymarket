<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  ArrowRight,
  ArrowUpRight,
  ChartCandlestick,
  CircleDollarSign,
  Layers,
  Radio,
  RefreshCw,
  Wallet,
} from 'lucide-vue-next'
import Heading from '../components/Heading.vue'
import Chart from '../components/Chart.vue'
import Table from '../components/Table.vue'
import { api, asRows, type Row } from '../api'
import { money } from '../fields'
import { useLoad } from '../load'
const summary = ref<Row>({}),
  markets = ref<Row[]>([]),
  range = ref('全部'),
  mode = ref('权益快照')
const { busy, error, refresh } = useLoad(async (signal) => {
  const [dash, market] = await Promise.all([
    api<Row>('dashboard', { signal }),
    api<Row[]>('markets?limit=5&offset=0', { signal }),
  ])
  summary.value = dash.data
  markets.value = market.data
})
const stats = computed(() => [
  {
    name: '模拟账户总权益',
    value: money(summary.value.total_equity),
    icon: Wallet,
    detail: 'USD · 虚拟资金',
  },
  {
    name: '累计模拟净收益',
    value: money(summary.value.net_pnl),
    icon: CircleDollarSign,
    detail: 'USD · 扣除计入成本',
  },
  {
    name: '运行模拟盘',
    value: summary.value.running_accounts ?? '—',
    icon: Layers,
    detail: '共 ' + (summary.value.accounts ?? '—') + ' 个独立账户',
  },
  {
    name: '研究市场',
    value: summary.value.markets ?? '—',
    icon: ChartCandlestick,
    detail: 'BTC / ETH · 5m / 15m',
  },
])
const chartRows = computed(() => {
  const rows = asRows(
    mode.value === '权益快照' ? summary.value.series : summary.value.daily_pnl,
  )
  const floor =
    range.value === '全部'
      ? -Infinity
      : Date.now() - (range.value === '24H' ? 1 : 7) * 86400000
  return rows.filter((r) => Date.parse(String(r.time)) >= floor)
})
</script>
<template>
  <Heading
    title="研究总览"
    en="Workspace overview"
    detail="共享同一行情的账户属于独立反事实实验，汇总资金按账户相加。"
    ><button class="btn" @click="refresh">
      <RefreshCw :size="14" :class="{ spin: busy }" />刷新</button
    ><router-link class="primary" to="/paper-accounts"
      >管理模拟盘<ArrowUpRight :size="15" /></router-link
  ></Heading>
  <div v-if="error" class="error" role="alert">{{ error }}</div>
  <div class="stats">
    <article v-for="s in stats" :key="s.name" class="stat">
      <div>
        <span>{{ s.name }}</span
        ><component :is="s.icon" :size="15" />
      </div>
      <strong>{{ s.value }}</strong
      ><small>{{ s.detail }}</small>
    </article>
  </div>
  <div class="dash-grid">
    <section class="panel">
      <div class="panel-head">
        <div>
          <small class="kicker">PORTFOLIO</small>
          <h2>模拟资金表现</h2>
          <p class="muted">权益曲线包含新增账户投入的虚拟本金。</p>
        </div>
        <div class="seg">
          <button
            v-for="r in ['24H', '7D', '全部']"
            :key="r"
            :class="{ selected: range === r }"
            @click="range = r"
          >
            {{ r }}
          </button>
        </div>
      </div>
      <div class="chart-tabs">
        <button
          v-for="m in ['权益快照', '每日已结算净收益']"
          :key="m"
          :class="{ chosen: mode === m }"
          @click="mode = m"
        >
          {{ m }}</button
        ><small>USD</small>
      </div>
      <Chart
        :rows="chartRows"
        :type="mode === '权益快照' ? 'line' : 'bar'"
        :value-key="mode === '权益快照' ? 'equity' : 'net_pnl'"
        :empty="
          mode === '权益快照' ? '等待模拟权益快照' : '暂无每日已结算净收益样本'
        "
      />
    </section>
    <section class="panel feed">
      <div class="panel-head">
        <div>
          <small class="kicker">DATA FEED</small>
          <h2>数据源状态</h2>
        </div>
        <Radio :size="20" />
      </div>
      <div v-if="!asRows(summary.sources).length" class="empty">
        <Radio :size="26" /><strong>等待数据源状态</strong>
      </div>
      <div
        v-for="(s, i) in asRows(summary.sources).slice(0, 5)"
        :key="i"
        class="feed-row"
      >
        <div>
          <i /><strong>{{ s.source || s.name }}</strong>
        </div>
        <span>{{ s.status || '未知' }}</span>
      </div>
      <router-link class="panel-link" to="/data-sources"
        >查看数据中心<ArrowRight :size="14"
      /></router-link>
    </section>
  </div>
  <section class="panel">
    <div class="panel-head">
      <div>
        <small class="kicker">MARKET WATCH</small>
        <h2>市场观察</h2>
      </div>
      <router-link class="text-link" to="/markets"
        >全部市场<ArrowRight :size="14"
      /></router-link>
    </div>
    <Table
      resource="markets"
      :rows="markets"
      :loading="busy"
      empty="等待真实市场记录"
      :open-row="
        (r) =>
          $router.push(
            '/markets/' + encodeURIComponent(String(r.market_id ?? r.id)),
          )
      "
    />
  </section>
  <div class="dash-grid lower">
    <section class="panel">
      <div class="panel-head"><h2>模拟统计</h2></div>
      <dl class="metrics">
        <div>
          <dt>累计模拟成交</dt>
          <dd>{{ summary.fills ?? '—' }}</dd>
        </div>
        <div>
          <dt>当日已结算净收益</dt>
          <dd>{{ money(summary.today_pnl) }}</dd>
        </div>
        <div>
          <dt>结算胜率</dt>
          <dd>
            {{
              summary.win_rate == null
                ? '—'
                : (Number(summary.win_rate) * 100).toFixed(2) + '%'
            }}
          </dd>
        </div>
        <div>
          <dt>单账户最大回撤峰值</dt>
          <dd>
            {{
              summary.max_drawdown == null
                ? '—'
                : (Number(summary.max_drawdown) * 100).toFixed(2) + '%'
            }}
          </dd>
        </div>
        <div>
          <dt>运行策略版本</dt>
          <dd>{{ summary.running_strategies ?? '—' }}</dd>
        </div>
        <div>
          <dt>已结算样本</dt>
          <dd>{{ summary.sample_count ?? '—' }}</dd>
        </div>
      </dl>
    </section>
    <section class="panel">
      <div class="panel-head">
        <h2>最近异常</h2>
        <router-link class="text-link" to="/risk"
          >风险控制<ArrowRight :size="14"
        /></router-link>
      </div>
      <div v-if="!asRows(summary.events).length" class="empty small">
        <strong>暂无异常记录</strong>
      </div>
      <div
        v-for="(e, i) in asRows(summary.events).slice(0, 4)"
        :key="i"
        class="event-line"
      >
        {{ e.message || e.reason || e.kind || e.action || e.status }}
      </div>
    </section>
  </div>
  <div class="book-grid">
    <section
      v-for="view in [
        { key: 'by_strategy', title: '策略组合已结算收益' },
        { key: 'by_asset', title: 'BTC / ETH 已结算收益' },
      ]"
      :key="view.key"
      class="panel"
    >
      <div class="panel-head">
        <h2>{{ view.title }}</h2>
      </div>
      <Table
        :rows="asRows(summary[view.key])"
        :resource="view.key"
        empty="暂无已结算样本"
      />
    </section>
  </div>
</template>
